# Chhaya (छाया) — heat action plans for outdoor workers

> **Track 02 — Heat & Water.** A site-level heat action plan that measures,
> decides, asks for permission, executes once, and proves it afterwards.
> Built for WeMakeDevs *Environmental Hacks*, and built to run — the whole
> loop is exercised by 352 tests before anything is "demo'd".

Chhaya means *shade*. This project gives outdoor workers something the paper
heat action plans in India already promise: a rule that actually stops work
when the heat is dangerous — not a recommendation, a decision with a receipt.

A supervisor opens the console on a phone, sees the current hour's verdict
from two published heat-stress engines, and when the band is **Stop**, the
console proposes the actions the *policy file* names for that band. A human
approves with the **exact phrase** the policy printed. The action executes
once (a dry run by default), its post-condition is verified against the store,
and a **standing advisory** goes into force with a bounded TTL. If nobody
answers inside the window, the **escalation clock** calls the site owner.

```
heat hour ──► two engines, stricter wins ──► band
  band ──► actions the policy names (never invented)
  action ──► human approves with exact phrase
  approved ──► execute once ──► verify post-condition ──► record
  past window ──► escalate to owner + labour helpline
```

Nothing here depends on a model making the call. The agent writes the
supervisor-facing explanation; the policy decides what gets proposed. The
safety property is ordinary code that is tested 352 times.

---

## Quick start

```bash
make setup        # create .venv, install package + dev deps
make demo         # walk the whole loop against synthetic Hyderabad weather
make serve        # open the console at http://127.0.0.1:8000
make test         # 352 tests, 94% coverage
make lint         # ruff + ruff-format + mypy --strict
```

Or without Make:

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev,aws]"
python -m chhaya.cli demo
python -m chhaya.cli serve
```

That is the whole developer story: **no secrets, no network, no API key, no
frontend build**. `make demo` works with the wifi off (the weather is a
deterministic synthetic day, the store is local JSON, the notifier is a log
file, the agent is scripted). The same code path — literally the same
functions — runs against Open-Meteo + Bedrock + DynamoDB + SNS in AWS mode.

### The console

The console is a single `web/index.html`, zero build step. It shows the live
configuration and policy fingerprint, a *screen an hour* form, the current
verdict with per-engine findings, one card per proposed action with the exact
approval phrase, standing advisories with revoke, the exposure ledger, a full
audit trail, and the escalation clock.

---

## What here is made up, and what is not

The engineering is honest about both halves, the way the winning write-ups
were:

| Already real | Not yet real |
|---|---|
| Both heat engines are **published, cited standards** — not vibes | The weather is **synthetic in local mode**; live mode reads Open-Meteo |
| WBGT, workload/meteorological screening, NIOSH work-rest schedule validated against primary sources | The crew data (25 workers, heavy work) is an **example site** in the shipped policy |
| Consent is an **exact phrase** with a word-boundary, case-sensitive check | There is **no SMS-to-console approval loop** yet — approvals come from the console |
| Every step is **recorded with the policy fingerprint** that was in force | Escalations are **logged/SNS messages**, not a real call tree (the SNS topic and numbers are example values) |
| 352 tests, 94% coverage, ruff + mypy strict, `template.yaml` passes `cfn-lint` | No real AWS stack has been deployed from this repo yet — see `docs/runbook.md` |

That table is in `LEARNINGS.md` too, with the specific numerical corrections
we made when a primary source disagreed with a secondary one.

---

## Layout

```
src/chhaya/
  domain/      heat.py, niosh.py, workload.py, screening.py, bands.py
  policy/      strict YAML schema, fingerprint, word-boundary consent check
  safety/      the loop: triage, propose, approve, execute, verify, escalate
  sources/     Open-Meteo (live), recorded fixture, deterministic fake
  agent/       LocalAgent (scripted) | BedrockAgent (converse via boto3)
  notify/      console, JSON log, SNS
  store/       LocalStore (JSON) | DynamoStore (single table) — same protocol
  api/         FastAPI app + proposal-token registry + console
  handlers/    Lambda entry points (triage, escalation, api via Mangum)
  cli.py       serve / triage / escalate / demo
policies/      heat_policy_v1.yaml — the shipped demo site policy
web/           index.html — the zero-build console
template.yaml  SAM: single-table DynamoDB, SNS, 3 lambdas, read/agent + write/notifier roles
```

## Building on AWS

The submission uses **AWS** in three places and no other platform:

1. **Amazon Bedrock** (Claude 3 Haiku) drafts every supervisor-facing
   explanation. `BedrockAgent` fails safe: it raises `AgentError` and the
   caller can fall back to the scripted agent.
2. **AWS Lambda + API Gateway** serve the exact same FastAPI console, via
   Mangum, so the live URL and `make serve` are byte-for-byte the same app.
3. **Amazon DynamoDB** is the single-table store (advisories, action records,
   exposure, coverage, and the pending proposals that ARE the escalation
   clock). **Amazon SNS** publishes escalation alerts. **EventBridge
   Scheduler** runs triage every 15 minutes and the escalation check every 5
   minutes.

Deploy with the SAM CLI — an AWS open-source tool:

```bash
sam build
sam deploy --guided --parameter-overrides SiteOwnerEmail=you@example.com
```

The template gives the three functions least-privilege policies: a
read-only **agent** policy (Bedrock invoke + DynamoDB read) and a write-only
**notifier** policy (SNS publish), exactly the split the first-place
write-ups emphasized. `cfn-lint template.yaml` is part of CI.

## Why these engines cite

There are more speculations about heat stress than facts; the code picks facts.

| Purpose | Source |
|---|---|
| Wet-bulb temperature | **Stull (2011), "Wet-Bulb Temperature", *J. Appl. Meteor. Clim.*** — our implementation reproduces his 20 °C / 50 % → 13.69 °C example |
| WBGT | **ISO 7243:2017** eq. (1)/(2), with the Annex D solar-radiation adjustment |
| Heat index | **Rothfusz (1990)**, plus NWS's no-HI-below-80 °F rule |
| Work-rest schedules | **NIOSH, *Criteria for a Recommended Standard: Occupational Exposure to Heat and Hot Environments* (rev. 2016)**, Table 5-1 |
| Workload classes | ISO 8996 energy-expenditure bands |
| Acclimatization screening | NIOSH 2016 §8 |
| Saturation vapour pressure | Bolton (1980) / Alduchov & Eskridge |

Every numeric anchor in the tests points back to one of these. `LEARNINGS.md`
lists the three places a secondary source disagreed with the primary one and
which answer we shipped.

## The ledger: worker-hours of hazardous exposure avoided

Chhaya numbers its impact like a payroll system: each recorded hour becomes
*N workers × 60 minutes in band X*, and the advisory coverage it produces is
subtracted. The summary explicitly labels the counterfactual: how many
*hazardous worker-minutes* were made avoidable. There is no "tons of CO₂
saved"-style invented unit here — the failure of an exposure recording to be
attached to an advisory appears in the ledger as a zero, not as a story.

## Limits, said plainly

- This ships **policy and process**, not a civil-engineering guarantee. A
  "stop" vote tells a supervisor to make people stop; it cannot physically
  make them.
- The shipped site and telephone numbers are **example values**.
- Bedrock access on a brand-new AWS account can take time to grant; the local
  agent is the tested fallback, and the write-up says so.

See `docs/` for the long story:

- `docs/architecture.md` — the loop, the modules, the single-table design
- `docs/safety.md` — the two engines, the citations, the validation
- `docs/runbook.md` — every way to run it, including the AWS deployment
- `docs/submission.md` — the judges' criterion map and demo script
- `docs/LEARNINGS.md` — honest approximations and corrections
- `docs/blog.md` — the WeMakeDevs community post

## License

Apache-2.0. This is a hackathon build; the physics citations are in the code.