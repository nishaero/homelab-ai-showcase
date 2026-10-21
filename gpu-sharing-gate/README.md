# GPU sharing gate

One GPU serves a voice assistant, doorbell descriptions and an ops agent through
Ollama, and also runs Immich's photo machine learning. Immich's backlog jobs are
heavy and made everything else slow. These two scripts let the background work
yield.

## immich-gate

`immich-gate pause|resume|status [owner]` pauses and resumes Immich's job queues.

- **Reference counted.** Several owners can hold a pause at once. A queue is only
  resumed when the last owner lets go.
- **Does not touch what it did not pause.** A queue that was already paused on
  purpose is left alone, on pause and on resume.
- **Safe under concurrency.** State lives in a JSON file guarded by a file lock.
- **Least privilege.** It uses an Immich API key that can only manage queues.

The ops agent calls it before its heavy cycle and resumes in a `finally` block, so
an error cannot leave Immich paused.

## immich-yield

A small service that listens to Home Assistant state changes and holds the pause
while the doorbell sees a person or the voice assistant is in use, then resumes
after a quiet period (45 seconds for voice, 60 for the doorbell). If the doorbell
sensor gets stuck in a busy state, it gives up after 5 minutes instead of holding
Immich forever. It is meant to be temporary while a backlog clears.

## Config

`~/.config/immich-gate.env` holds `IMMICH_URL` and `IMMICH_GATE_KEY`.
`immich-yield` reads `HA_URL` and `HA_TOKEN` from the file named by `HA_ENV`
(default `~/.config/ha.env`).
