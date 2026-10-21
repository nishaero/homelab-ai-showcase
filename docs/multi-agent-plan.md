# Multi agent plan

Status: built as [homelab-multi-agent](https://github.com/nishaero/homelab-multi-agent), which
runs beside the existing agents. This page is the original plan. The differences are listed
at the end.

## Where things stand

Today there are several single purpose agents. Each one works alone and shares
only the model and a memory service.

| Agent | Job | Tools it has |
|---|---|---|
| Ops agent | Updates, rollbacks, health, host patching | Fixed allowlist, no shell |
| Ops chat | Same tools, with a human confirming changes | Adds automation authoring |
| Doorbell service | Who is at the door, deliveries, vehicles | Face model, vision model |
| Voice assistant | Answers spoken questions | MCP tools: search, home state, memory |
| Home automations | Heating and hot water decisions | Home Assistant rules, forecasts |

What is missing is a way for one agent to ask another for help, and a place to
see a job from start to finish.

## Goal

Let a problem found by one agent be solved by the right specialist, with a
human approving anything risky, and a record of who did what.

Two example jobs:

1. **A container update fails its health check.** The ops agent rolls back. It
   then asks a research agent to read the release notes and the container logs.
   The research agent reports the likely cause. The ops agent notifies a human with
   the cause and a proposed fix, and waits.
2. **A cold night is forecast.** The energy agent proposes switching the heat pump
   to Winter a day ahead and estimates the cost. The home agent applies it after a
   human approves, and checks the next morning that the rooms reached target.

## Roles

| Role | Owns | Can change things? |
|---|---|---|
| Coordinator | Splits a request into steps, routes them, tracks status | No |
| Ops agent | Containers, VMs, updates, backups | Yes, through the existing allowlist |
| Home and energy agent | Heating, hot water, solar, automations | Yes, only safe domains, with approval for anything new |
| Security agent | Doorbell, cameras, alerts, exposure checks | Notify only |
| Research agent | Changelogs, logs, web, documents | No, read only |

The coordinator never touches a system. Only an agent that already has the right
tools and guard rails changes anything.

## How agents talk

Use what is already running: the memory service and its MCP server.

- A **task** is an event with `kind = task` and fields `from`, `to`, `goal`,
  `status` (open, working, done, failed, needs_human), `parent` and `result`.
- Each agent polls for open tasks addressed to it, or gets woken by a
  Home Assistant event or a timer.
- Results are written back to the same task. The coordinator reads the chain and
  decides the next step.
- Every step is searchable later, so an agent can see what was already tried.

This is deliberately boring: a table and a polling loop are easy to debug and
easy to audit. A framework can replace it later if it earns its place.

## One GPU, many agents

The workstation has a single GPU. Several agents thinking at once would slow each
other and the voice assistant.

- A small **model gate** hands out one model call at a time. It is a priority
  queue: voice first, then doorbell, then everything else.
- Heavy agents (ops, research) run in the night window as they do today, or wait
  for the gate.
- The existing Immich gate keeps background photo processing out of the way.
- Each task gets a turn limit and a time budget, so one stuck agent cannot hold the
  gate.

## Guard rails for a team

Each agent keeps its own tool allowlist. Teams add new ways to go wrong, so these
are added on top:

- **No privilege by delegation.** A request from another agent never widens what
  the receiver may do. It is checked against the receiver's own allowlist.
- **Depth limit.** A task chain stops at 3 levels, so agents cannot ping each other
  forever.
- **Approval for anything that changes a system the first time.** A human
  approves on the phone. The approval is stored with the task.
- **Untrusted text stays data.** Changelogs, logs and web pages are passed to the
  model as quoted material, never as instructions. The research agent has no
  tools that change anything, so a poisoned page can at worst give a bad answer.
- **Audit trail.** Each task records who asked, who acted, which tools ran and what
  they returned.

## Build order

1. **Task table and a tiny coordinator** that routes by keyword rules, not by a
   model. Wire the ops agent to hand a failed update to a research agent.
2. **Research agent** with read only tools: changelog fetch, container logs, web
   search, document search.
3. **Model gate** with priorities, then move the voice assistant and doorbell onto
   it.
4. **Home and energy agent** that proposes heating and hot water changes and asks
   for approval on the phone.
5. **Model based coordinator** once the rule based one is too limiting.
6. **Evaluation set**: a few dozen recorded situations with the expected routing
   and the expected refusal, including injected instructions inside a changelog.
   Run it before every change to a prompt or a tool list.

## Risks

- More agents mean more model calls on one GPU. The gate and the night window are
  the mitigation, and the first measurement to take is queue wait time.
- Agents can agree with each other and still be wrong. Approval for risky changes
  and a read only research role keep that cheap.
- Without tests this gets hard to change safely, which is why the evaluation set
  is part of the plan and not an extra.

## What changed when it was built

- **SQLite instead of the memory service for tasks.** A local table is simpler to test and
  keeps the new system independent of the old one. The memory service is optional: a finished
  request leaves a note there.
- **Planner is a model with a rule fallback.** The coordinator asks the model for a plan and
  falls back to keyword rules if the plan is invalid or the model is down.
- **Real loop detection.** Near identical calls and a cap per tool were added, because exact
  matching misses a model that varies its arguments.
- **A claim check.** A live trial showed a summary saying a change had been made when none had.
  Answers that claim a change are now corrected in code.
- **Dry run by default.** Approved actions are recorded and only run when dry run is turned off.
- **Model choice measured.** `qwen3.8:27b` scored best on routing and tool use and was faster
  than the other large model tried. See the evaluation page in the project.
