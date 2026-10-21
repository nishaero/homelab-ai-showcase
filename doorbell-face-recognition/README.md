# Doorbell face recognition

Tells you who is at the door, using a doorbell camera, the face model that Immich
already runs, and a local vision model. Nothing leaves the network.

## Flow

1. Home Assistant reports a person. This service subscribes to the state
   changes over the websocket and starts a scan.
2. It grabs up to 10 frames, 1 second apart, and sends each to Immich's machine
   learning container for face detection and embeddings (`buffalo_l`).
3. Each embedding is compared with the named people in Immich's database through a
   read only database role, plus a small file of reference faces taken by the
   doorbell camera itself. Same lens and lighting means those references are often
   much closer than library photos.
4. A face is accepted only if its distance is at most `MAX_DISTANCE` (0.50) and
   it beats the second best person by at least `MARGIN` (0.04). Otherwise it stays
   unrecognised.
5. The result is posted back to Home Assistant as a sensor. Intermediate results
   have `final: false`. The finished scan has `final: true`, and the automation
   that builds the notification waits for it.
6. In parallel, a vision model (`qwen3-vl`) checks the frames for a delivery
   company and for vehicles, and reads a licence plate from a cropped vehicle. A
   slow model never delays the face result.
7. Every scan is appended to a decision log and stored as an event in a memory
   service.

## Teach loop

The notification has a reply action. Replying "Sam right" tells the service that
the face on the right of the image is Sam. It stores that embedding as a reference
for the next scan. If it cannot tell which face you mean it asks you to say left,
middle or right.

## Lessons kept in the code

- **Wait for the final flag.** The notification once used an intermediate result
  that named one person while two were at the door. Intermediate states are now
  marked and ignored.
- **Positions, not just names.** The result includes where each person stands in
  the image, so a description model can attach a name to the right person.
- **Home Assistant forgets REST posted entities on restart.** `ensure_baseline`
  recreates only the missing ones.

## Configuration

Environment variables: `HA_URL`, `HA_TOKEN`, `ML_URL`, `CAMERA`, `MAX_DISTANCE`,
`MARGIN`, `MIN_FACE_PX`, `MAX_FRAMES`, `FRAME_GAP`, `OLLAMA_URL`, `VLM_MODEL`,
`MEMORY_URL`, `MEMORY_TOKEN`, `NOTIFY_SERVICES` and the standard `PG*` variables for
the Immich database. Reference faces and known plates live in a mounted `/data`
folder and are not part of this repository.

`enrol.py NAME photo.jpg` adds a reference face from a photo with exactly one face.

## Privacy

This repository contains code only. Face embeddings, reference photos, plates and
decision logs stay on the machine that runs it.
