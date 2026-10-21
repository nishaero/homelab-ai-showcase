"""Doorbell face identification.

Watches the HA doorbell presence helper, grabs snapshots while someone is at the door,
runs Immich's own face model (immich_machine_learning) and compares the embeddings with
the named people in Immich's database (read-only role). Publishes the result to HA as
sensor.doorbell_person_identity. Everything stays local.
"""
import asyncio, json, os, time, uuid, traceback, glob
from datetime import datetime, timezone
import base64, io, re, httpx, psycopg2, websockets
from PIL import Image

HA = os.environ["HA_URL"].rstrip("/"); TOK = os.environ["HA_TOKEN"]
ML = os.environ.get("ML_URL", "http://immich_machine_learning:3003/predict")
CAM = os.environ.get("CAMERA", "camera.doorbell")
MAXD = float(os.environ.get("MAX_DISTANCE", "0.50"))      # Immich's own default match cutoff
MARGIN = float(os.environ.get("MARGIN", "0.04"))          # gap to the 2nd-best person
MINW = int(os.environ.get("MIN_FACE_PX", "60"))
FRAMES = int(os.environ.get("MAX_FRAMES", "10"))
GAP = float(os.environ.get("FRAME_GAP", "1.0"))
OLLAMA = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434/api/chat")
VLM = os.environ.get("VLM_MODEL", "qwen3-vl:8b-instruct")
LOG = "/data/decisions.jsonl"; LAST = "/data/last"
H = {"Authorization": "Bearer " + TOK}
_run = None
_last_end = 0.0
_last_veh = 0.0


def _set_last_veh():
    global _last_veh
    _last_veh = time.time()

COOLDOWN = float(os.environ.get('SCAN_COOLDOWN', '40'))


def db():
    return psycopg2.connect(connect_timeout=5)


def match(emb):
    sql = ("select btrim(p.name), (fs.embedding <=> %s::vector) d from face_search fs "
           "join asset_face af on af.id = fs.\"faceId\" join person p on p.\"personGroupId\" = af.\"personGroupId\" "
           "where p.name <> '' and af.\"deletedAt\" is null order by fs.embedding <=> %s::vector limit 30")
    with db() as c, c.cursor() as cur:
        cur.execute(sql, (emb, emb)); rows = cur.fetchall()
    best = {}
    for n, d in rows:
        best[n] = min(best.get(n, 9), float(d))
    # doorbell-camera reference faces (verified by the owner); same lens/lighting, so often much closer
    try:
        e = json.loads(emb) if isinstance(emb, str) else list(emb)
        for n, vecs in json.load(open("/data/doorbell_refs.json")).items():
            for v in vecs:
                dot = sum(a * b for a, b in zip(e, v)); na = sum(a * a for a in e) ** .5; nb = sum(b * b for b in v) ** .5
                best[n] = min(best.get(n, 9), 1 - dot / (na * nb))
    except FileNotFoundError:
        pass
    except Exception as ex:
        print("refs failed", ex, flush=True)
    return sorted(best.items(), key=lambda x: x[1])


MEM = os.environ.get("MEMORY_URL", ""); MEMT = os.environ.get("MEMORY_TOKEN", "")
_last = {"faces": [], "t": 0.0, "ident": ""}
REFS = "/data/doorbell_refs.json"
# Comma separated Home Assistant notify services, for example "mobile_app_phone_1,mobile_app_phone_2"
PHONES = [p for p in os.environ.get("NOTIFY_SERVICES", "").split(",") if p]


async def mem_event(client, kind, text, meta=None):
    if not MEM: return
    try:
        await client.post(f"{MEM}/events", headers={"X-Token": MEMT}, json={"kind": kind, "text": text, "meta": meta or {}}, timeout=10)
    except Exception as e:
        print("mem_event failed", e, flush=True)


async def tell(client, title, msg):
    for ph in PHONES:
        try:
            await client.post(f"{HA}/api/services/notify/{ph}", headers=H, json={"title": title, "message": msg}, timeout=10)
        except Exception as e:
            print("notify failed", e, flush=True)


def hpos(bb, iw):
    cx = (bb["x1"] + bb["x2"]) / 2 / iw if iw else 0.5
    return "left" if cx < 0.4 else "right" if cx > 0.6 else "middle"


def immich_names():
    with db() as c, c.cursor() as cur:
        cur.execute("select distinct btrim(name) from person where btrim(name) <> ''"); return [r[0] for r in cur.fetchall()]


async def handle_teach(client, reply):
    parts = (reply or "").strip().split()
    hint = next((w.lower() for w in parts if w.lower() in ("left", "right", "middle")), None)
    name = " ".join(w for w in parts if w.lower() not in ("left", "right", "middle")).strip()
    if not name:
        await tell(client, "Doorbell learning", "I didn't get a name. Reply with a name, e.g. 'Sam'."); return
    known = {n.lower(): n for n in await asyncio.to_thread(immich_names)}
    canon = known.get(name.lower())
    faces = _last["faces"]
    if not faces or time.time() - _last["t"] > 6 * 3600:
        await tell(client, "Doorbell learning", "I don't have a recent face to learn from."); return
    if hint: cands = [f for f in faces if f["h"] == hint]
    else:
        cands = [f for f in faces if not f["name"]] or (faces if len(faces) == 1 else [])
    if len(cands) != 1:
        await tell(client, "Doorbell learning", f"I saw {len(faces)} faces and can't tell which is {name}. Reply '{name} left', '{name} middle' or '{name} right'."); return
    f = cands[0]
    try: d = json.load(open(REFS))
    except Exception: d = {}
    d.setdefault(canon or name.title(), []).append(f["emb"]); json.dump(d, open(REFS, "w")); os.chmod(REFS, 0o600)
    label = canon or name.title()
    await mem_event(client, "correction", f"Taught: the face {f['where']} (previously {f['name'] or 'unrecognised'}) is {label}.", {"name": label, "was": f["name"]})
    extra = "" if canon else " (not a named person in Immich yet, so I saved it as a new name)"
    await tell(client, "Doorbell learning", f"Saved: that face is {label}{extra}. I now have {len(d[label])} doorbell photo(s) of {label}.")


def place(bb, iw, ih):
    if not iw or not ih: return ""
    cx = (bb["x1"] + bb["x2"]) / 2 / iw; cy = (bb["y1"] + bb["y2"]) / 2 / ih
    h = "left" if cx < 0.4 else "right" if cx > 0.6 else "middle"
    v = "low" if cy > 0.5 else "high"
    return f"{'in the middle' if h == 'middle' else 'on the ' + h + ' side'} of the image, face {v} in the frame"


def layout_text(pos, unk_pos):
    parts = [f"{n}: {w}" for n, w in sorted(pos.items()) if w]
    parts += [f"an unrecognised person: {w}" for w in unk_pos if w]
    return "; ".join(parts)


async def ensure_baseline(client):
    """HA forgets REST-posted entities on restart; re-create any that are missing (never touches existing ones)."""
    base = {"sensor.doorbell_person_identity": ("idle", {"friendly_name": "Doorbell person identity", "final": True}),
            "binary_sensor.doorbell_known_person": ("off", {"friendly_name": "Doorbell known person", "device_class": "presence", "who": []}),
            "sensor.doorbell_delivery": ("none", {"friendly_name": "Doorbell delivery"}),
            "sensor.doorbell_vehicle": ("none", {"friendly_name": "Doorbell vehicle"})}
    for ent, (st, at) in base.items():
        try:
            r = await client.get(f"{HA}/api/states/{ent}", headers=H, timeout=10)
            if r.status_code == 404: await post_state(ent, st, at, client)
        except Exception as e:
            print("baseline check failed", e, flush=True)


async def post_state(entity, state, attrs, client):
    try:
        await client.post(f"{HA}/api/states/{entity}", headers=H, timeout=10,
                          json={"state": state, "attributes": attrs, "force_update": True})
    except Exception as e:
        print("post_state failed", e, flush=True)


def predict(img: bytes):
    ent = json.dumps({"facial-recognition": {"detection": {"modelName": "buffalo_l", "options": {"minScore": 0.6}},
                                             "recognition": {"modelName": "buffalo_l"}}})
    r = httpx.post(ML, data={"entries": ent}, files={"image": ("a.jpg", img, "image/jpeg")}, timeout=60)
    r.raise_for_status()
    return r.json().get("facial-recognition", [])


def judge(face):
    """-> (name or None, dist, second_dist)"""
    m = match(face["embedding"])
    if not m: return None, 9.0, 9.0
    best_n, best_d = m[0]; second = m[1][1] if len(m) > 1 else 9.0
    if best_d <= MAXD and second - best_d >= MARGIN:
        return best_n, best_d, second
    return None, best_d, second


CARRIERS = ["none", "DHL", "Deutsche Post", "Hermes", "DPD", "GLS", "UPS", "FedEx", "Amazon", "Other carrier"]
PROMPT = ("You look at a doorbell camera image. Decide whether a parcel/mail delivery person or delivery vehicle from one of these companies is visible: "
          "DHL (yellow and red; yellow van or yellow/red uniform with DHL logo), Deutsche Post (yellow; yellow van, uniform or mail cart with a black post horn logo), "
          "Hermes (blue/light-blue branding and uniform, Hermes logo), DPD (red and grey, DPD logo), GLS (blue and yellow, GLS logo), UPS (brown uniform and brown van, UPS logo), "
          "FedEx, Amazon (blue Amazon-branded van or vest, smile logo), or another carrier. Only name a company if you can see its logo or its distinctive uniform or vehicle colours "
          "together with a parcel, a handheld scanner, a cart or a van. An ordinary person in a yellow, blue or brown jacket is NOT a delivery. If unsure answer none. Answer in JSON with only the carrier.")
SCHEMA = {"type": "object", "properties": {"carrier": {"type": "string", "enum": CARRIERS}}, "required": ["carrier"]}


def classify(img: bytes):
    im = Image.open(io.BytesIO(img)); im = im.resize((1536, int(im.height * 1536 / im.width))); b = io.BytesIO(); im.save(b, "JPEG", quality=85)
    body = {"model": VLM, "stream": False, "format": SCHEMA, "keep_alive": -1, "options": {"temperature": 0, "num_predict": 20},
            "messages": [{"role": "user", "content": PROMPT, "images": [base64.b64encode(b.getvalue()).decode()]}]}
    r = httpx.post(OLLAMA, json=body, timeout=60); r.raise_for_status()
    return json.loads(r.json()["message"]["content"]).get("carrier", "none")


def phrase(c):
    if c == "Deutsche Post": return "Deutsche Post mail delivery"
    if c == "Other carrier": return "parcel delivery"
    return f"{c} package delivery"


async def classify_and_post(client, img, st):
    """Runs beside the face scan; a slow vision model must never delay or fail the face check."""
    try:
        c = await asyncio.wait_for(asyncio.to_thread(classify, img), timeout=50)
    except Exception as e:
        print("classify failed:", type(e).__name__, flush=True); return
    if c != "none" and st["carrier"] is None:
        st["carrier"] = c
        await post_state("sensor.doorbell_delivery", c, {"friendly_name": "Doorbell delivery", "carrier": c, "phrase": phrase(c),
                         "updated": datetime.now(timezone.utc).isoformat()}, client)


VEH_SCHEMA = {"type": "object", "properties": {"vehicles": {"type": "array", "items": {"type": "object", "properties": {
    "kind": {"type": "string"}, "colour": {"type": "string"}, "make_model": {"type": "string"}, "bbox": {"type": "array", "items": {"type": "integer"}}},
    "required": ["kind", "colour", "bbox"]}}}, "required": ["vehicles"]}
PLATE_SCHEMA = {"type": "object", "properties": {"plate": {"type": "string"}, "readable": {"type": "boolean"}}, "required": ["plate", "readable"]}
def _norm_plate(p):
    return re.sub(r"[^A-ZÄÖÜ0-9]", "", (p or "").upper())


def _known_plates():
    try:
        return json.load(open("/data/known_plates.json"))
    except Exception:
        return {}


PLATE_RE = re.compile(r"^[A-ZÄÖÜ]{1,3} [A-Z]{1,2} \d{1,4}[EH]?$")


def vlm_json(img, prompt, schema, n):
    b = io.BytesIO(); img.convert("RGB").save(b, "JPEG", quality=90)
    body = {"model": VLM, "stream": False, "format": schema, "keep_alive": -1, "options": {"temperature": 0, "num_predict": n},
            "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(b.getvalue()).decode()]}]}
    r = httpx.post(OLLAMA, json=body, timeout=90); r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def vehicle_info(img: bytes):
    """-> dict(kind, colour, make_model, plate) for the most prominent vehicle, or None."""
    im = Image.open(io.BytesIO(img)); W, H = im.size
    small = im.resize((1536, int(H * 1536 / W)))
    r = vlm_json(small, "List every vehicle visible. For each give kind (car, van, truck, motorbike, bicycle), colour, make_model if you can tell, "
                        "and bbox as [x1,y1,x2,y2] in 0-1000 relative image coordinates.", VEH_SCHEMA, 200)
    vs = [v for v in r.get("vehicles", []) if len(v.get("bbox", [])) == 4]
    if not vs: return None
    v = max(vs, key=lambda v: (v["bbox"][2] - v["bbox"][0]) * (v["bbox"][3] - v["bbox"][1]))
    x1, y1, x2, y2 = [c / 1000 for c in v["bbox"]]; pad = 0.02
    crop = im.crop((int(max(0, x1 - pad) * W), int(max(0, y1 - pad) * H), int(min(1, x2 + pad) * W), int(min(1, y2 + pad) * H)))
    crop = crop.resize((crop.width * 2, crop.height * 2))
    p = vlm_json(crop, "Read the licence plate of the vehicle exactly as written (German format like 'XX AB 123'). "
                       "If the plate is not clearly readable set readable=false and plate=''.", PLATE_SCHEMA, 60)
    plate = re.sub(r"[^A-ZÄÖÜ0-9 ]", "", (p.get("plate") or "").upper()); plate = re.sub(r"\s+", " ", plate).strip()
    label = _known_plates().get(_norm_plate(p.get("plate")), "")
    if not label and not (p.get("readable") and PLATE_RE.match(plate)): plate = ""
    if label and not plate: plate = _norm_plate(p.get("plate"))
    return {"kind": v.get("kind", "vehicle"), "colour": v.get("colour", ""), "make_model": v.get("make_model", ""), "plate": plate, "label": label}


async def vehicle_and_post(client, img):
    try:
        info = await asyncio.wait_for(asyncio.to_thread(vehicle_info, img), timeout=80)
    except Exception as e:
        print("vehicle failed:", type(e).__name__, flush=True); return
    if not info: return
    desc = info["label"] or " ".join(x for x in [info["colour"], info["make_model"] or info["kind"]] if x).strip()
    await post_state("sensor.doorbell_vehicle", desc or info["kind"], {"friendly_name": "Doorbell vehicle", **info, "description": desc or info["kind"],
                     "updated": datetime.now(timezone.utc).isoformat()}, client)
    print("vehicle:", "known" if info["label"] else desc, "plate read" if info["plate"] else "no plate", flush=True)


def keep_last(img, tag):
    try:
        os.makedirs(LAST, exist_ok=True)
        open(f"{LAST}/{int(time.time())}_{tag}.jpg", "wb").write(img)
        for f in sorted(glob.glob(LAST + "/*.jpg"))[:-30]: os.remove(f)
    except Exception:
        pass


async def scan_inner(client, present_ok):
    state_c = {"carrier": None, "task": None}; t0 = time.time(); known = {}; faces_max = 0; faces_seen = 0; closest = 9.0; frames = 0; pos = {}; pos_d = {}; unk_pos = []; best_faces = []
    await post_state("sensor.doorbell_person_identity", "scanning", {"friendly_name": "Doorbell person identity"}, client)
    await post_state("sensor.doorbell_delivery", "none", {"friendly_name": "Doorbell delivery"}, client)
    if time.time() - _last_veh > 60:
        await post_state("sensor.doorbell_vehicle", "none", {"friendly_name": "Doorbell vehicle"}, client)
    for i in range(FRAMES):
        if not present_ok(): break
        try:
            r = await client.get(f"{HA}/api/camera_proxy/{CAM}", headers=H, timeout=15)
            img = r.content
            faces = await asyncio.to_thread(predict, img)
            if i in (0, 4) and (state_c.get("vtask") is None or state_c["vtask"].done()) and time.time() - _last_veh > 60:
                try:
                    vs = (await client.get(f"{HA}/api/states/binary_sensor.doorbell_vehicle", headers=H, timeout=5)).json()["state"]
                except Exception:
                    vs = "off"
                if vs == "on":
                    _set_last_veh()
                    state_c["vtask"] = asyncio.create_task(vehicle_and_post(client, img))
            if state_c["carrier"] is None and i in (0, 3, 6) and (state_c["task"] is None or state_c["task"].done()):
                state_c["task"] = asyncio.create_task(classify_and_post(client, img, state_c))
        except Exception as e:
            print("frame failed", e, flush=True); await asyncio.sleep(GAP); continue
        frames += 1
        big = [f for f in faces if (f["boundingBox"]["x2"] - f["boundingBox"]["x1"]) >= MINW]
        faces_seen += len(big)
        if big: keep_last(img, "face")
        unk = 0; frame_unk = []; frame_faces = []
        try:
            iw, ih = Image.open(io.BytesIO(img)).size
        except Exception:
            iw, ih = 0, 0
        for f in big:
            name, d, sec = await asyncio.to_thread(judge, f)
            closest = min(closest, d)
            where = place(f["boundingBox"], iw, ih)
            emb = json.loads(f["embedding"]) if isinstance(f["embedding"], str) else list(f["embedding"])
            frame_faces.append({"emb": emb, "name": name, "where": where, "h": hpos(f["boundingBox"], iw)})
            if name:
                known[name] = min(known.get(name, 9), d)
                if d <= pos_d.get(name, 9): pos_d[name] = d; pos[name] = where
            else:
                unk += 1; frame_unk.append(where)
        if frame_unk: unk_pos = frame_unk
        if len(frame_faces) >= len(best_faces): best_faces = frame_faces
        faces_max = max(faces_max, len(big))
        if known:
            await post_state("sensor.doorbell_person_identity", " & ".join(sorted(known)),
                             {"friendly_name": "Doorbell person identity", "distance": round(min(known.values()), 3),
                              "frames": frames, "final": False, "faces": faces_max, "unknown": max(0, faces_max - len(known)),
                              "layout": layout_text(pos, unk_pos)}, client)
            if not unk:  # confident and nobody left unidentified -> stop early
                break
        await asyncio.sleep(GAP)
    if known: state = " & ".join(sorted(known))
    elif faces_seen: state = "unknown"
    else: state = "no_face"
    attrs = {"friendly_name": "Doorbell person identity", "known": sorted(known), "faces_seen": faces_seen,
             "closest_distance": round(closest, 3) if closest < 9 else None, "frames": frames,
             "seconds": round(time.time() - t0, 1), "final": True, "carrier": state_c["carrier"], "faces": faces_max, "unknown": max(0, faces_max - len(known)),
             "layout": layout_text(pos, unk_pos), "updated": datetime.now(timezone.utc).isoformat()}
    await post_state("sensor.doorbell_person_identity", state, attrs, client)
    await post_state("binary_sensor.doorbell_known_person", "on" if known else "off",
                     {"friendly_name": "Doorbell known person", "device_class": "presence", "who": sorted(known)}, client)
    open(LOG, "a").write(json.dumps({"t": attrs["updated"], "result": state, **{k: attrs[k] for k in ("faces_seen", "closest_distance", "frames", "seconds")},
                                     "known": {k: round(v, 3) for k, v in known.items()}}) + "\n")
    print("scan done:", state, attrs, flush=True)
    if best_faces:
        _last.update(faces=best_faces, t=time.time(), ident=state)
        lay = layout_text(pos, unk_pos)
        who = " and ".join(sorted(known)) if known else ""
        more = attrs["unknown"]
        who_txt = (who + (f" and {more} unrecognised {'person' if more == 1 else 'people'}" if more else "")) if who else ("an unrecognised person" if more <= 1 else f"{more} unrecognised people")
        await mem_event(client, "doorbell", f"{who_txt} at the front door on {datetime.now().strftime('%A %d %B at %H:%M')}",
                        {"known": sorted(known), "unknown": attrs["unknown"], "closest": attrs["closest_distance"], "layout": lay})


async def scan(client, present_ok):
    global _last_end
    try:
        await scan_inner(client, present_ok)
    except Exception as e:
        traceback.print_exc()
        await post_state("sensor.doorbell_person_identity", "error",
                         {"friendly_name": "Doorbell person identity", "error": str(e)[:200], "final": True}, client)
    finally:
        _last_end = time.time()


async def main():
    global _run
    async with httpx.AsyncClient() as client:
        await post_state("sensor.doorbell_person_identity", "idle", {"friendly_name": "Doorbell person identity", "final": True}, client)
        while True:
            try:
                async with websockets.connect(HA.replace("http", "ws", 1) + "/api/websocket", max_size=None, ping_interval=20) as ws:
                    await ws.recv(); await ws.send(json.dumps({"type": "auth", "access_token": TOK})); await ws.recv()
                    present = {"v": False, "until": 0.0}
                    for n, ent in enumerate(["input_boolean.doorbell_person_present", "binary_sensor.doorbell_visitor", "binary_sensor.doorbell_vehicle"], 1):
                        await ws.send(json.dumps({"id": n, "type": "subscribe_trigger", "trigger": {"platform": "state", "entity_id": ent}}))
                    await ws.send(json.dumps({"id": 20, "type": "subscribe_events", "event_type": "mobile_app_notification_action"}))
                    await ws.send(json.dumps({"id": 21, "type": "subscribe_trigger", "trigger": {"platform": "state", "entity_id": "input_text.doorbell_ai_description"}}))
                    print("connected to HA", flush=True)
                    await ensure_baseline(client)
                    while True:
                        msg = json.loads(await ws.recv())
                        if msg.get("type") != "event": continue
                        if msg.get("id") == 20:
                            ed = msg["event"].get("data", {})
                            if ed.get("action") == "DOORBELL_TEACH": asyncio.create_task(handle_teach(client, ed.get("reply_text")))
                            continue
                        if msg.get("id") == 21:
                            ts = msg["event"]["variables"]["trigger"]["to_state"]["state"]
                            if ts and ts not in ("unknown", "unavailable") and time.time() - _last["t"] < 300:
                                asyncio.create_task(mem_event(client, "description", f"Doorbell description: {ts}", {"ident": _last["ident"]}))
                            continue
                        t = msg["event"]["variables"]["trigger"]; to = t["to_state"]["state"]; ent = t["entity_id"]
                        if ent.endswith("person_present"):
                            present["v"] = (to == "on")
                        if to == "on":
                            if ent.endswith("visitor") or ent.endswith("vehicle"): present["until"] = time.time() + 14
                            if (_run is None or _run.done()) and time.time() - _last_end > COOLDOWN:
                                _run = asyncio.create_task(scan(client, lambda: present["v"] or time.time() < present["until"]))
            except Exception as e:
                print("ws error:", e, flush=True); traceback.print_exc(); await asyncio.sleep(5)


asyncio.run(main())
