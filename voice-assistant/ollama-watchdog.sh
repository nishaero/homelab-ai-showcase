#!/usr/bin/env bash
# Detect Ollama's "cannot load new models" hang; restart it (needs the narrow sudo rule) and re-warm the voice/doorbell models.
STATE="$HOME/.cache/ollama-watchdog"; mkdir -p "$STATE"
LOG="$HOME/ollama-watchdog.log"
log(){ echo "$(date '+%F %T') $*" >> "$LOG"; }
warm_voice(){ curl -s -m 120 localhost:11434/api/generate -d '{"model":"qwen3-vl:8b-instruct","prompt":"hi","stream":false,"keep_alive":-1,"options":{"num_ctx":16384,"num_predict":1}}' >/dev/null; }
# keep the voice model resident; this request is also the health probe (a stuck loader never answers)
if curl -s -m 30 -o /dev/null localhost:11434/api/generate -d '{"model":"qwen3-vl:8b-instruct","prompt":"hi","stream":false,"keep_alive":-1,"options":{"num_ctx":16384,"num_predict":1}}'; then
  rm -f "$STATE/fails"; exit 0
fi
n=$(( $(cat "$STATE/fails" 2>/dev/null || echo 0) + 1 )); echo $n > "$STATE/fails"
log "probe failed ($n in a row)"
[ "$n" -lt 2 ] && exit 0
last=$(cat "$STATE/last_restart" 2>/dev/null || echo 0)
if [ $(( $(date +%s) - last )) -lt 900 ]; then log "restart skipped: one happened less than 15 minutes ago"; exit 0; fi
if sudo -n /usr/bin/systemctl restart ollama; then
  date +%s > "$STATE/last_restart"; rm -f "$STATE/fails"; log "ollama restarted by watchdog"
  for i in $(seq 1 30); do curl -s -m 3 -o /dev/null localhost:11434/api/version && break; sleep 2; done
  warm_voice && log "voice model re-warmed"
  systemctl --user start ollama-keepwarm.service && log "second model re-warm requested"
else
  log "cannot restart ollama: sudo rule missing or refused"
fi
