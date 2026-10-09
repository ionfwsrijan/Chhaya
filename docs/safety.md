# Chhaya — the safety story

The engineering claim judges asked the first-place teams to *prove* was:
**when the computers say stop, can you show who decided, why, and that it
happened?** The first-place winners (like Beacon and Suraksha) won by making
those proofs mechanical. Chhaya does the same.

## Consents as code

Approval is the literal text the policy printed — `APPROVE STOP WORK` — matched
with a **word-boundary, case-sensitive** regex (`policy/engine.py`).

| Message | Result |
|---|---|
| `APPROVE STOP WORK` | Approved |
| `approve stop work` | Refused (case is real) |
| `APPROVE STOP WORK!` | Refused (boundary is real) |
| `APPROVE STOP WORKING` | Refused (not a substring match) |

The phrase lives in `policies/heat_policy_v1.yaml`; the model may not invent
it. One test per mis-typed variant keeps the check honest.

## Two engines, stricter wins

`domain/bands.py` runs **ISO 7243** and **NIOSH** over the same hour. When they
disagree, `merge()` keeps the *stricter* band, and the verdict records **both**
engines' outputs plus the margin. This is a tested safety property, not a
policy preference: a disagreement is treated as "one of us is right, assume
the worse one is".

- **NIOSH work-rest (Table 5-1)** decides by WBGT, workload class, and
  acclimatization. A heavy-load unacclimatized crew works far fewer minutes
  than a light-load acclimatized one — that is the actual table.
- **ISO 7243** decides by WBGT against an allowed limit by workload; we
  include its Annex D solar-radiation correction instead of pretending a
  ninth-floor facade and a shaded yard have the same heat load.
- The **Stull (2011)** wet-bulb formula is validated against the paper's own
  example (20 °C / 50% → 13.69 °C). The heat index follows Rothfusz and, per
  NWS, reports air temperature below 80 °F.

`HeatInputs.measured_wbgt_c`/`measured_globe_c` let a real WBGT meter override
a computed estimate when a competent person has one — the rule tests for the
instrument it deserves.

## Verification is store-backed

`verify_action` never trusts the agent's word. `verify: store_present` means
the action is a success **only if the store can show the effect** (e.g., a
live advisory). `supervisor_confirm` returns `PENDING` — recorded as pending,
never guessed complete. `notifier_ack` requires the notifier's own
delivery result. This is why "the crew moved to shade" cannot be recorded as
done by the person who was supposed to do it; Chhaya records the advisory,
and the coverage ledger only counts verified coverage.

## The escalation clock

Every Stop-band proposal is persisted as `PendingProposal` in the store with a
10-minute window. The API resolves it the moment a human answers. A separate
process (`EscalationFunction`, 5-minute schedule) lists unresolved stop
proposals and fires the policy's escalation role once each. `resolved` and
`escalated` flags make the clock **idempotent across restarts** — a missed
approval is announced exactly once, and an answered one is never announced.
The clock survives a Lambda cold start because it is in DynamoDB, not memory.

## The ledger labels its own counterfactual

Each recorded hour is `workers × minutes_observed` in a band. Verified coverage
is subtracted. The summary names the assumption outright:

> "hazardous worker-minutes made avoidable by verified advisories"
> (a counterfactual: it counts the minutes the advisory existed, not
> labor actually halted).

No invented "equivalent" unit. The ship rule from the winners: an exposure
hour with no advisory shows up as **zero**, and that zero is honest.

## Citations at a glance

| Constant / schedule | Source |
|---|---|
| wet-bulb temperature | Stull (2011), *J. Appl. Meteor. Clim.* 50, 2261–2271 |
| WBGT limits & screening | ISO 7243:2017 (eq. 1/2; Annex D solar-adjustment) |
| heat index | NWS Rothfusz (1990) + no-report-below-80 °F rule |
| saturation vapour pressure | Bolton (1980); Alduchov & Eskridge (1996) |
| work-rest / acclimatization | NIOSH Publ. 2016-106, rev. 2016, Table 5-1 |
| workload classes | ISO 8996 metabolic-rate bands |

Whatever you read in a demo, the test suite re-derives it from these.

## What is explicitly out of scope

- Legally *enforcing* that a worker stops (a human does that; Chhaya makes
  the decision legible and the silence loud).
- Medical triage of a specific person (that is a doctor's job).
- Predicting tomorrow — a two-day Open-Meteo forecast is read, but the
  decision is about the *observed* hour.