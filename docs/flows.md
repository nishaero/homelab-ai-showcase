# Flows

How the main pieces work together at run time.

## Doorbell: who is at the door

```mermaid
sequenceDiagram
    participant D as Doorbell camera
    participant HA as Home Assistant
    participant F as face service
    participant ML as Immich ML (faces)
    participant DB as Immich people (read only)
    participant O as Ollama (vision model)
    participant M as agent memory
    participant P as Phones
    D->>HA: person detected
    HA->>F: state change (websocket)
    F->>HA: sensor = scanning
    loop up to 10 frames
        F->>HA: grab snapshot
        F->>ML: detect faces, get embeddings
        F->>DB: nearest named people
        F->>F: accept only if close enough and clearly ahead of the runner up
        F->>HA: sensor = names, final = false
    end
    F->>HA: sensor = names, final = true
    F->>M: store event
    HA->>HA: wait for final = true
    HA->>O: snapshot + names + positions
    O-->>HA: one sentence per person
    HA->>P: notification with a "Teach who this is" button
    P-->>F: reply "Sam right"
    F->>F: save the face as a reference
```

## Heating: when to switch on

```mermaid
flowchart TD
    A["18:00 every day"] --> B["Read the daily forecast"]
    B --> C{"A night of 4 C or colder in the next 2 days,<br/>or tomorrow's average 7 C or lower?"}
    C -- yes --> W["Set the heat pump to Winter<br/>and notify"]
    C -- no --> N["Do nothing"]
    X["Backstop: outside under 6 C for 6 h,<br/>or hallway or child's room cool for 3 h"] --> W
    W --> T["Wall thermostat dial calls for heat"]
    T --> H["Heat pump runs on its weather curve<br/>(low, steady flow temperature)"]
    H --> R{"Child's room under 17.5 C for 3 h?"}
    R -- yes --> Al["Notify: turn the dial up one notch,<br/>wait a day"]
```

The heat pump has no room sensors. The wall dial is a plain contact, so Home
Assistant decides the season (Summer or Winter) and reports problems, and the
dial decides when heat is called.

## Hot water: when to heat the tank

```mermaid
flowchart LR
    S["Forecast.Solar<br/>today and tomorrow"] --> A
    A["11:00"] --> B{"Today's solar<br/>10 kWh or more?"}
    B -- yes --> H55["Tank target 55 C"]
    B -- no --> H50["Tank target 50 C"]
    H55 --> Showers["Afternoon and evening showers"]
    H50 --> Showers
    Showers --> E["19:30"]
    E --> C{"Tomorrow's solar<br/>10 kWh or more?"}
    C -- yes --> L42["Hold 42 C overnight"]
    C -- no --> L45["Hold 45 C overnight"]
```

The tank is heated in the warmest, sunniest hours, because the heat pump is
more efficient then and the electricity comes from the home's own solar panels
and battery.

## Memory: how agents share what they learn

```mermaid
flowchart LR
    Face["face service"] -->|"events"| API
    Ops["ops agent"] -->|"notes"| API
    HA["Home Assistant"] -->|"descriptions"| API
    API["memory API<br/>(Postgres + pgvector)"] --> E["embedding model on Ollama"]
    API --> MCP["MCP server"]
    MCP --> Voice["voice assistant"]
    MCP --> Chat["chat UI"]
    API -->|"standing facts as a sensor"| HA
```

Events are stored first and embedded afterwards, so a slow model never loses a
record. The MCP side can add facts but cannot delete anything.

## GPU sharing

```mermaid
flowchart LR
    Voice["voice request"] --> Y
    Door["doorbell person"] --> Y
    Y["immich-yield"] -->|"pause"| G["immich-gate<br/>(reference counted)"]
    Ops["ops agent heavy cycle"] -->|"pause, always resume in finally"| G
    G -->|"pause / resume queues"| I["Immich ML jobs"]
    Y -.->|"resume after a quiet period"| G
```

A queue is only resumed when the last owner lets go. A queue that was paused on
purpose is never touched.
