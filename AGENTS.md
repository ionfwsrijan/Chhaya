# AGENTS.md

Working notes for AI agents and humans onboarding into Chhaya.

## Repo commands

| Command | What it does |
|---|---|
| `python -m chhaya.cli demo` | Walk the whole safety loop (offline) |
| `python -m chhaya.cli serve` | Serve the console at http://127.0.0.1:8000 |
| `python -m chhaya.cli triage` / `escalate` | Run one scheduled Lambda step by hand |
| `.venv/Scripts/python -m pytest tests/` | Full suite (352 tests) |
| `.venv/Scripts/ruff check src/ tests/` + `format` | Lint / format (line-length 100) |
| `.venv/Scripts/mypy src/chhaya` | Strict typing gate |
| `cfn-lint template.yaml` | Validate the SAM template |
| `Makefile` targets | `setup test lint serve demo clean aws-validate deploy` |

Run everything from the repo root. On Windows the interpreter is
`.venv\Scripts\python.exe`; in CI it is `python`.

## Hard rules (do not break)

1. **Never invent a numeric anchor.** Every constant or schedule in
   `src/chhaya/domain/` traces to a published source listed in the code's
   docstrings. If a new number appears in code, it needs a citation and a
   test named after the source. See `docs/LEARNINGS.md` for the three
   corrections already made.
2. **The policy decides; the agent narrates.** `propose_actions()`
   (policy engine) always produces the action list. Agent backends
   (`agent/local.py`, `agent/bedrock.py`) only write reasoning and must raise
   `AgentError` on failure — never guess an action.
3. **Consent is exact.** `check_approval` matches the phrase with a
   word-boundary, case-sensitive regex. A typo must be refused, not
   substring-matched.
4. **Stricter engine wins.** `bands.merge()` keeps the stricter HEAT band
   when ISO/NIOSH disagree, and the finding records both engines' outputs.
5. **The store is the source of truth, including for the clock.**
   Pending proposals (the escalation clock) are persisted via
   `put_pending`/`list_pending` on the same `HeatStore` protocol used by
   LocalStore and DynamoStore. Do not add in-memory-only safety state.
6. **One route = one orchestrator call.** `api/app.py` endpoints must call
   one `chhaya.safety` function; business logic stays out of the HTTP layer.
7. **Console is zero-build.** `web/index.html` is intentionally a single
   file. Do not introduce npm, a bundler, or a `dist/` step.
8. **Everything stays honest on stage.** Local mode = synthetic weather +
   JSON store + scripted agent. AWS mode is opt-in via `CHHAYA_MODE=aws`.
   Never hide a degraded path; say which one is live (`runtime.describe()`).

## Structure worth knowing

- `domain/` — pure physics/risk engines (no I/O). `bands.py` owns Band/Verdict/
  merge. This is where judges' "prove it" questions land.
- `policy/` — strict `extra="forbid"` YAML schema, `policy_fingerprint`.
- `safety/orchestrator.py` — `triage`, `propose_actions`, `approve_and_execute`,
  `record_exposure`, `record_coverage`, `record_pending`/`resolve_pending`.
- `store/rows.py` — the one shared (de)serializer for every record across
  LocalStore and DynamoStore. Change records only here.
- `store/dynamo.py` — single-table design: `pk=SITE#<id>`,
  `sk=<KIND>#<ts>#<entity>`, GSI `gsi1` on `gsi1pk=ID#<entity>`.
- `handlers/` — Lambda entry points, each a thin shell over an orchestrator
  call. `api.py` adapts FastAPI via Mangum.
- `cli.py` — serve / triage / escalate / demo; demo mirrors the API's
  record→resolve pending lifecycle.

## Testing

- 352 tests: domain, policy, safety loop, sources, agents, notifiers, stores
  (Local + DynamoDB moto), runtime, API, handlers.
- `tests/conftest.py` pins the shared fixtures (policy, fake source, etc.).
- New store features need a LocalStore test **and** a DynamoDB (moto) test.
- Every end-to-end story has a `TestLiveLoop`/`TestEndToEnd*` spelling.
- Keep the full gate green before finishing any task: `ruff check`,
  `ruff format --check`, `mypy src/chhaya`, `pytest tests/ --cov=chhaya`.

## Deployment mindset

`template.yaml` deploys three Lambda functions, one single-table DynamoDB,
one SNS topic, EventBridge schedules, and least-privilege roles (read-only
agent / write-only notifier). Validate changes with `cfn-lint template.yaml`.
The deploy path is `sam build && sam deploy --guided` (documented; not run
# here).