#!/usr/bin/env python3
"""Upload extracted document text to an Open WebUI knowledge base (read-only w.r.t. the NAS).

Each file is uploaded as a .txt whose first lines carry the original NAS path, so answers can cite
where the file lives. Resumable via state.json (path -> file id + text hash); re-run after OCR to pick
up new text. Never deletes anything in Open WebUI. Usage: ingest.py [--kb NAME] [--limit N] [--books]
"""
import glob, hashlib, json, os, sys, time, requests
from pathlib import Path

ROOT = Path.home() / "nas-rag"
BASE = "http://localhost:8090/api/v1"
MAX_CHARS = 300_000            # larger files are textbooks: ingested only with --books
SKIP = (".@__thumb", "@Recently-Snapshot", "@Recycle")


def env():
    for line in open(Path.home() / ".config" / "openwebui-rag.env"):
        if line.startswith("OPENWEBUI_API_KEY="):
            return line.split("=", 1)[1].strip()


H = {"Authorization": f"Bearer {env()}", "Accept": "application/json"}


def get_kb(name):
    r = requests.get(f"{BASE}/knowledge/", headers=H, timeout=60); r.raise_for_status()
    items = r.json().get("items", r.json()) if isinstance(r.json(), dict) else r.json()
    for k in items:
        if k["name"] == name:
            return k["id"]
    r = requests.post(f"{BASE}/knowledge/create", headers={**H, "Content-Type": "application/json"},
                      json={"name": name, "description": "Documents from the NAS OneDrive share (read-only index)."},
                      timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def upload(fname, content):
    r = requests.post(f"{BASE}/files/?process=true", headers=H, timeout=600,
                      files={"file": (fname, content.encode("utf-8"), "text/plain")})
    r.raise_for_status()
    fid = r.json()["id"]
    for _ in range(120):                        # wait until chunked + embedded
        s = requests.get(f"{BASE}/files/{fid}/process/status", headers=H, timeout=60).json().get("status")
        if s == "completed":
            return fid
        if s == "failed":
            raise RuntimeError("processing failed")
        time.sleep(1)
    raise RuntimeError("processing timeout")


def main():
    a = sys.argv
    kb_name = a[a.index("--kb") + 1] if "--kb" in a else "NAS Documents"
    limit = int(a[a.index("--limit") + 1]) if "--limit" in a else 10**9
    books = "--books" in a
    state_f = ROOT / f"state-{kb_name.replace(' ', '_')}.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {}
    kb = get_kb(kb_name)
    recs = [json.load(open(f)) for f in sorted(glob.glob(str(ROOT / "text" / "*.json")))]
    todo = []
    for d in recs:
        t = d["text"]
        if any(s in d["path"] for s in SKIP) or len(t) < 40 or d.get("needs_ocr"):
            continue
        if (len(t) > MAX_CHARS) != books:
            continue
        h = hashlib.sha1(t.encode()).hexdigest()
        if state.get(d["path"], {}).get("hash") != h:
            todo.append((d, h))
    print(f"{len(todo)} to ingest into '{kb_name}' ({kb})", flush=True)
    ok = bad = 0
    for d, h in todo[:limit]:
        path = d["path"]
        top = path.split("/")[0] if "/" in path else "(loose file)"
        content = f"Source file: OneDrive/{path}\nFolder: {top}\n\n{d['text']}"
        try:
            fid = upload("OneDrive - " + path.replace("/", " - ") + ".txt", content)
            r = requests.post(f"{BASE}/knowledge/{kb}/file/add", headers={**H, "Content-Type": "application/json"},
                              json={"file_id": fid}, timeout=600)
            r.raise_for_status()
            state[path] = {"hash": h, "file_id": fid}
            ok += 1
        except Exception as e:
            bad += 1
            print("failed", path[:70], type(e).__name__, str(e)[:100], flush=True)
        if (ok + bad) % 25 == 0:
            state_f.write_text(json.dumps(state)); print(f"{ok} ok, {bad} failed", flush=True)
    state_f.write_text(json.dumps(state))
    print(f"done: {ok} ok, {bad} failed", flush=True)


main()
