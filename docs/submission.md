# Chhaya — submission playbook

For the **WeMakeDevs Environmental Hacks** judges (Track 02: Heat & Water).
This is the map from their criteria to where in this repo the proof lives.

## Criterion → evidence

| Criterion | Chhaya's answer | Where it is proven |
|---|---|---|
| **Idea & Impact** | India's HAPs don't reach contract/daily-wage workers (AP/Telangana court findings). A site-level plan that *stops* work with a receipt, minus invented units. | `docs/safety.md`; `docs/LEARNINGS.md` |
| **Built on AWS** | Bedrock (agent narration), Lambda+API Gateway (the live console), DynamoDB (single-table store), SNS (escalations), EventBridge Scheduler (15/5-min loops). SAM CLI (an AWS open-source tool) deploys it. | `template.yaml`; `src/chhaya/handlers/*`; `docs/architecture.md` |
| **Design & Usability** | One `web/index.html`, fetch-based, zero build; a supervisor's phone UI. Every action shows its exact approval phrase. A typo is refused and *shown* refused. | `web/index.html`; `/api/*` |
| **Execution** | 352 tests, 94% coverage, ruff+mypy strict, `cfn-lint` clean. `make demo` runs offline; the console is a live app, not a mockup; Dynamo store tested under moto. | `README.md`, `tests/` |
| **Demo video** | 3-minute script below; shows failure (typo refused), decision (two-engine verdict), and closure (advisory + ledger + escalation). | `docs/submission.md` §Video |

## The demo script (console, 3 minutes)

1. **The problem (0:00–0:30).** An afternoon in Hyderabad in May. The phone
   opens the console: `band=stop`, WBGT 41 °C, and the line "No approval
   received … within 10 minutes" is already on the escalation clock.
2. **The two-engine verdict (0:30–1:00).** Click a stop hour. The verdict card
   shows ISO 7243 and NIOSH *both* saying Stop plus the margin. (Say:
   "these are the standards, computed here, with the sources in the code.")
3. **Consent as code (1:00–1:40).** Type `APPROVE STOP WORK!` — refused,
   boundary check. Type `APPROVE STOP WORK` — executed, dry-run receipt shown
   with the policy fingerprint.
4. **Closure (1:40–2:30).** The standing advisory card, the ledger ticking
   `workers × minutes`, and the audit trail.
5. **AWS fit (2:30–3:00).** Architecture slide: the same console served by
   Lambda+API Gateway, DynamoDB single-table, SNS escalation, two IAM roles
   (read-only agent, write-only notifier). One line of the scripted agent:
   "the model explains the decision; the policy makes it."

Rule from the winners: never show a live Bedrock call as the proof of safety.
Show the receipts; the AWS fit is a titled slide.

## Personas

- **Ravi Kumar**, site supervisor at Miyapur Metro Construction — approves
  with the phrase, gets the escalation if he doesn't.
- **Padma Constructions**, site owner — first call when the clock fires.
- **Telangana labour helpline** — the emergency contact the policy carries,
  because the court orders made the point.

## Honesty that scores

- The ledger labels its own counterfactual (no invented units).
- The demo says plainly: local mode = synthetic weather + JSON + scripted
  agent; AWS mode is opted into; Bedrock grant timing is the one external
  dependency.
- `LEARNINGS.md` lists the three corrections from primary sources.

## Video logistics

- 3 minutes, recorded, screen capture + one architecture slide; no live demo
  dependence on Bedrock. Submit the `ConsoleUrl` if the stack is up, else the
  recorded-mode replay (`demo/fixtures/`).
- The blog is in `docs/blog.md` (top-5 blogs also win).