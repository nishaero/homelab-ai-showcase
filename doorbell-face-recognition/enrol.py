import sys, json, os, httpx
name, path = sys.argv[1], sys.argv[2]
ent = json.dumps({"facial-recognition": {"detection": {"modelName": "buffalo_l", "options": {"minScore": 0.6}}, "recognition": {"modelName": "buffalo_l"}}})
r = httpx.post(os.environ.get("ML", "http://immich_machine_learning:3003/predict"), data={"entries": ent}, files={"image": ("a.jpg", open(path, "rb").read(), "image/jpeg")}, timeout=60).json()["facial-recognition"]
assert len(r) == 1, f"expected exactly 1 face, found {len(r)}"
f = "/data/doorbell_refs.json"
d = json.load(open(f)) if os.path.exists(f) else {}
d.setdefault(name, []).append(json.loads(r[0]["embedding"]) if isinstance(r[0]["embedding"], str) else r[0]["embedding"])
json.dump(d, open(f, "w")); os.chmod(f, 0o600); print(name, len(d[name]), "refs")
