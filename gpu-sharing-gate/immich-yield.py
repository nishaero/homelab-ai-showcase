#!/usr/bin/env python3
"""immich-yield: pause Immich's heavy queues while the doorbell or voice assistant is working, resume after.

Temporary helper for the Immich backlog: stop it (systemctl --user disable --now immich-yield) once the
backlog is done. Listens on the Home Assistant websocket; any trigger pauses Immich via immich-gate and a
hold timer resumes it once things have been quiet."""
import asyncio, json, os, subprocess, time

HA = dict(l.strip().split("=", 1) for l in open(os.path.expanduser(os.environ.get("HA_ENV", "~/.config/ha.env"))) if "=" in l)
WS = HA["HA_URL"].replace("http", "ws", 1).rstrip("/") + "/api/websocket"
GATE = os.path.expanduser("~/bin/immich-gate")

HOLD_VOICE = 45      # seconds of quiet after a voice request
HOLD_DOORBELL = 60   # seconds of quiet after the doorbell goes idle
VOICE_PREFIX = ("conversation.", "stt.", "tts.")
DOORBELL = {"binary_sensor.doorbell_person", "binary_sensor.doorbell_visitor",
            "binary_sensor.doorbell_motion"}
IDENTITY = "sensor.doorbell_person_identity"

hold_until = 0.0
held = False
doorbell_busy = False
busy_since = 0.0


def gate(action):
    try:
        subprocess.run([GATE, action, "watcher"], capture_output=True, timeout=60)
    except Exception as e:
        print("gate", action, "failed:", e, flush=True)


def bump(seconds, why):
    global hold_until, held
    hold_until = max(hold_until, time.monotonic() + seconds)
    if not held:
        held = True
        print(time.strftime("%H:%M:%S"), "pause Immich:", why, flush=True)
        gate("pause")


async def releaser():
    global held, doorbell_busy
    while True:
        await asyncio.sleep(3)
        if doorbell_busy and time.monotonic() - busy_since > 300:
            doorbell_busy = False  # sensor stuck on 'scanning' - never hold Immich forever
        if held and not doorbell_busy and time.monotonic() > hold_until:
            held = False
            print(time.strftime("%H:%M:%S"), "resume Immich", flush=True)
            gate("resume")


async def listen():
    global doorbell_busy, busy_since
    import websockets
    async with websockets.connect(WS, max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": HA["HA_TOKEN"]}))
        if json.loads(await ws.recv()).get("type") != "auth_ok":
            raise RuntimeError("HA auth failed")
        await ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
        print("connected to HA", flush=True)
        async for raw in ws:
            m = json.loads(raw)
            if m.get("type") != "event":
                continue
            d = m["event"]["data"]
            eid = d.get("entity_id", "")
            new = (d.get("new_state") or {}).get("state")
            if eid.startswith(VOICE_PREFIX):
                if new not in (None, "unavailable", "unknown"):
                    bump(HOLD_VOICE, eid)
            elif eid in DOORBELL and new == "on":
                bump(HOLD_DOORBELL, eid)
            elif eid == IDENTITY:
                doorbell_busy = new not in ("idle", "unavailable", "unknown", None)
                if doorbell_busy:
                    busy_since = time.monotonic()
                    bump(HOLD_DOORBELL, f"{eid}={new}")
                else:
                    bump(HOLD_DOORBELL, "doorbell idle")  # keep paused a bit for the description/notification


async def main():
    asyncio.create_task(releaser())
    while True:
        try:
            await listen()
        except Exception as e:
            print("ws error:", e, flush=True)
        await asyncio.sleep(10)


asyncio.run(main())
