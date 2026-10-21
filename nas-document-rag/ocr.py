#!/usr/bin/env python3
"""OCR scanned PDFs (needs_ocr=true) with a local vision model. Reads only the local cache.

Renders the first MAX_PAGES pages and asks qwen3-vl to transcribe them. Resumable: a record is
rewritten in place only after OCR succeeded. Usage: ocr.py [--limit N] [--probe]
"""
import base64, json, subprocess, sys, tempfile, time, glob, requests
from pathlib import Path

ROOT = Path.home() / "nas-rag"
MODEL, MAX_PAGES, URL = "qwen3-vl:8b-instruct", 3, "http://localhost:11434/api/chat"
PROMPT = ("Transcribe all text visible on this scanned document page exactly as written, in its original "
          "language (often German or English). Keep line breaks. Output only the transcription, no comments. "
          "If the page has no text, output nothing.")


def render(pdf, page, tmp):
    base = Path(tmp) / f"p{page}"
    subprocess.run(["pdftoppm", "-r", "130", "-f", str(page), "-l", str(page), "-jpeg", "-singlefile",
                    str(pdf), str(base)], capture_output=True, timeout=120)
    f = Path(str(base) + ".jpg")
    return f.read_bytes() if f.exists() else None


def ocr_page(img):
    r = requests.post(URL, json={"model": MODEL, "stream": False, "keep_alive": "30m",
                                 "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 1200, "repeat_penalty": 1.15},
                                 "messages": [{"role": "user", "content": PROMPT,
                                               "images": [base64.b64encode(img).decode()]}]}, timeout=600)
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


def squash(t):
    """Collapse model repetition loops (same line, or same short token run, repeated)."""
    import re
    t = re.sub(r"((?:[^\n/]{1,12}[/\n ]+?))\1{5,}", r"\1", t)
    out, prev, n = [], None, 0
    for line in t.split("\n"):
        n = n + 1 if line == prev else 0
        if n < 2:
            out.append(line)
        prev = line
    return "\n".join(out)


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 10**9
    todo = []
    for f in sorted(glob.glob(str(ROOT / "text" / "*.json"))):
        d = json.load(open(f))
        if d.get("needs_ocr") and not d.get("ocr_done"):
            todo.append((f, d))
    print(f"{len(todo)} scans to OCR", flush=True)
    for n, (f, d) in enumerate(todo[:limit], 1):
        t0 = time.time()
        pdf = ROOT / "docs" / d["path"]
        parts = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                for pg in range(1, MAX_PAGES + 1):
                    img = render(pdf, pg, tmp)
                    if img is None:
                        break
                    parts.append(ocr_page(img))
            d["text"], d["needs_ocr"], d["ocr_done"] = squash("\n\n".join(p for p in parts if p)), False, True
            d["ocr_pages"] = len(parts)
            Path(f).write_text(json.dumps(d, ensure_ascii=False))
        except Exception as e:  # leave the record untouched so it is retried next run
            print("ocr failed", d["path"][:60], type(e).__name__, str(e)[:80], flush=True)
            continue
        print(f"{n}/{len(todo)} {time.time()-t0:.0f}s {len(d['text'])} chars  {d['path'][:60]}", flush=True)


main()
