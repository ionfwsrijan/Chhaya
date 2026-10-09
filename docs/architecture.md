# Chhaya — architecture

## The loop

One system property runs through everything: **an action is only ever the
policy file's idea, approved by a human's exact phrase, executed once, then
verified against the store.** Reading the sequence top to bottom is the whole
README:

```
when ──► source.current(site, when)          # weather: live or synthetic
  ──► triage(policy, inputs)                 # two engines, stricter wins
  ──► propose_actions(policy, result)        # actions the YAML names, only
  ──► approve_and_execute(policy, proposal)  # exact phrase, dry-run flag
  ──► verify_action(...)                     # post-condition, store-backed
  ──► record_*                               # ledger + advisory + audit trail
  ──► [same function as Lambda]              # handlers reuse all of this
```

Every function in the loop is stateless; the policy, the store, and the clock
are passed in. That is what makes the identical code run under pytest, under
`make demo` (local JSON + synthetic weather), and under Lambda (DynamoDB +
Bedrock + SNS). There is no "test build" and no "server build".

## Modules

| Module | Responsibility | Never does |
|---|---|---|
| `domain/heat.py` | WBGT from air T, humidity, wind, solar; Annex D adjustment | I/O |
| `domain/niosh.py` | Work-rest schedule + acclimatization screening | guess |
| `domain/screening.py` | Meteorological/workload prescreens | I/O |
| `domain/bands.py` | Band/Verdict, `merge()` stricter-wins tie-break | — |
| `policy/` | Strict YAML schema, fingerprint, approval check | invent |
| `safety/` | The orchestrator and its parts | hold state |
| `sources/` | Weather backends (Open-Meteo / recorded / fake) | forecast trust |
| `agent/` | Reasoning for the supervisor (local / Bedrock) | decide actions |
| `notify/` | Escalation delivery (console / log / SNS) | claim delivery |
| `store/` | HeatStore protocol + Local/Dynamo implementations | hide the clock |
| `api/` | FastAPI shell + propose-token registry + console | business logic |
| `handlers/` | Lambda entry points over the orchestrator | safety logic |

## The single-table store

`store/dynamo.py` owns the one schema both stores speak (`store/rows.py` is
the shared codec):

```
pk         = SITE#<site_id>
sk         = <KIND>#<Y-m-dTH:M:S>#<entity_id>      KIND ∈ ADV|REC|EXP|COV|PEND
gsi1pk     = ID#<entity_id>   (GSI gsi1)           lookup any record by id
gsi1sk     = <site_id>
```

Every record carries the **policy fingerprint** that was in force when it was
written, so a review can reconstruct what the system believed at 2 p.m.

## Where state lives

- **Consent tokens** are in-memory in the API process (one-shot, server-issued).
- **Everything that must survive a restart** — advisories, action records,
  exposure, coverage, and the pending proposals that *are* the escalation
  clock — lives in the store behind `HeatStore`.
- Local mode persists to JSON files under `.chhaya/`; AWS mode to DynamoDB.
  The JSON rows and the Dynamo items are the exact same shape.

## Why the agent is a narrator

A model deciding when people work is not demonstrable in a grading room:
nondeterminism, context size, and eval luck all show up on stage. Instead the
policy says *what* may be proposed per band, the engines say *which* band it
is, and `agent/` writes why. `BedrockAgent` calls Claude (converse) with the
policy summary and the triage result, receives a JSON tool call we construct,
and if anything fails it raises `AgentError` — the orchestrator never gets a
guessed action. `LocalAgent`, the tests, and `make demo` use the same
interface with a scripted narrator.

## Deployment shape (SAM)

`template.yaml` deploys:

- one single-table DynamoDB (PAY_PER_REQUEST, PITR, SSE),
- one SNS topic (SMS/email subscriptions are parameter-gated),
- `ApiFunction` (FastAPI via Mangum, HTTP API `/{proxy+}`),
- `TriageFunction` (EventBridge Scheduler, 15 min),
- `EscalationFunction` (EventBridge Scheduler, 5 min),
- three IAM managed policies: read-only **agent**, write-only **notifier**,
  and store-writes; functions compose them.

Environment variables mirror `Settings` (`.env.example`), so `make local` and
the Lambda cold start differ only in mode.