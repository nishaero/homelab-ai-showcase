# Voice assistant pieces

Part of a fully local voice assistant for Home Assistant: speech to text with
Whisper, a language model on Ollama, and text to speech with Kokoro.

## docker-compose.tts.yml

Runs Kokoro (a CPU text to speech server with an OpenAI compatible API) and a
Wyoming bridge so Home Assistant can use it as a voice. Voices and speed are set
on the bridge command line.

## ollama-watchdog.sh

Ollama can hang in a state where loaded models answer but new models never load.
The script sends a tiny request to the voice model with `keep_alive: -1`. That
request keeps the model resident and works as the health probe, because a stuck
loader never answers.

- Two failed probes in a row trigger a restart.
- A restart is skipped if one happened in the last 15 minutes.
- After the restart it waits for the API, then warms the voice model again.
- The restart uses a narrow sudo rule that allows only
  `systemctl restart ollama`. Example rule:
  `youruser ALL=(root) NOPASSWD: /usr/bin/systemctl restart ollama`

Run it from a systemd timer or cron every minute or two.
