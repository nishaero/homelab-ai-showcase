# NAS document search

Makes a large folder of family documents searchable by a local model, without
changing a single file.

## Pipeline

1. **extract.py** reads a copy of the documents and writes one JSON record per
   file (PDF, Word, Excel, PowerPoint, OpenDocument, RTF, text). A PDF with no text
   layer is flagged `needs_ocr`. A corrupt file is recorded and skipped.
2. **ocr.py** renders the first pages of flagged scans and asks a local vision
   model (`qwen3-vl`) to transcribe them. It collapses repetition loops in the
   output and rewrites a record only after OCR succeeded.
3. **ingest.py** uploads each text to an Open WebUI knowledge base. The first
   lines carry the original path, so answers can name the source file. Very large
   files (textbooks) are only ingested when asked for.

Every step is resumable and never deletes anything. Ingestion keeps a hash per
file, so re-running after OCR only uploads what changed.

## Using it

Open WebUI answers questions over the knowledge base. The
[web-search-mcp](https://github.com/nishaero/web-search-mcp) `search_documents`
tool queries the same Qdrant collection so a voice assistant can ask it too.

## Config

The Open WebUI API key is read from `~/.config/openwebui-rag.env`
(`OPENWEBUI_API_KEY=`). The document copy lives in `~/nas-rag/docs` with a list
of relative paths in `docs.lst`.
