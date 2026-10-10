# Chhaya — runbook

This file is the "how do I actually run this" answer for the judges, in three
sentences each.

## Modes in one paragraph

`Settings` (`settings.py`) names four knobs: source (fake/recorded/openmeteo),
store (local JSON or DynamoDB), agent (local/bedrock), notifier
(console/log/sns). `CHHAYA_MODE=local` is the default; `CHHAYA_MODE=aws` plus
the SAM environment is production. Everything else — including `make demo` —
needs no environment at all.

## 1) Offline demo (recommended first run)

```bash
make setup
make demo
```

Prints a flat afternoon: three triaged hours, the exact approval phrase, the
dry-run receipt, the escalation clock firing 30 minutes later, and the ledger.
No network, no credentials, no npm. That is the *first* thing to show someone
(curious, not salesy).

## 2) The console

```bash
make serve        # http://127.0.0.1:8000
```

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | mode, alive-ness |
| `GET /api/config` | live configuration + policy fingerprint |
| `GET /api/ledger` | exposure / coverage tally |
| `GET /api/state` | advisories, records, ledger in one call |
| `POST /api/triage` | triage an hour; returns proposals **with tokens** |
| `POST /api/approve` | approve a token with the exact phrase |
| `POST /api/revoke` | revoke a standing advisory |
| `POST /api/exposure` | append an exposure hour |
| `POST /api/escalations/check` | fire due escalations, once |

The triage form at the top lets you pick an hour, workload, and number of
workers. Approving a card echoes the policy's phrase; a paraphrase is
*refused* and the refusal is recorded in the audit trail (try it with a typo —
that is the demo moment).

## 3) The deterministic replay (recorded mode)

```bash
CHHAYA_SOURCE=recorded CHHAYA_RECORDING=demo/fixtures/hyderabad-may-18.json \
  python -m chhaya.cli demo
```

`RecordedSource` replays a JSON fixture in the exact Open-Meteo shape
(`temperature_2m`, `relative_humidity_2m`, …), so the same downtown hour the
video shows can be replayed identically in the room. This is the "history
simulated, today live" tool.

## 4) Live mode (developer machine)

`CHHAYA_SOURCE=openmeteo` reads the Open-Meteo hourly forecast
(no API key; 10k calls/day). No code path changes.

## 5) The one-command Lambda reproduction

```bash
python -m chhaya.cli triage     # same function as TriageFunction
python -m chhaya.cli escalate   # same function as EscalationFunction
```

```

## 6) Full verification

```bash
make test    # 360 tests incl. DynamoDB via moto (no AWS access needed)
make lint    # ruff, format check, mypy --strict
python -m pip install cfn-lint && cfn-lint template.yaml
```

## 7) AWS deployment

```bash
pip install aws-sam-cli
sam build
sam deploy --guided --parameter-overrides SiteOwnerEmail=you@example.com
```

`sam build` installs `requirements.txt` into the bundle; the Lambda execution
role is the least-privilege split (read-only agent / write-only notifier).
Outputs: `ConsoleUrl` (the live console), `HeatTableName`, `AlertsTopicArn`.

Two deployment caveats, both documented honestly in `submission.md`:

- **Bedrock** on a new account may need an access grant. Until then, set
  `CHHAYA_AGENT=local` for the API function; the safety loop is identical.
- **SMS** endpoints must be verified for the region. Parameter-gated
  subscriptions (`SiteOwnerPhone`, `SiteOwnerEmail`) let you skip them.

## 8) Clean machine check

```bash
git clean -fdx          # or fresh clone
make setup
make demo
make test
make lint
```

If that sequence is green, the submission runs on stage.