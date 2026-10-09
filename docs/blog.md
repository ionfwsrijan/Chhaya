# Chhaya — the WeMakeDevs community post

The blog that wins the "top 5 blogs" prize reads like a build log with a
thesis, not a feature list. Draft for posting after the submissions close.

---

**Title:** *Shade as software: giving an outdoor worker a heat decision that
can be audited*

**Thesis.** India already has heat action plans. The courts in AP and
Telangana ordered a 12–3 p.m. construction ban a decade ago, and yet the
reports keep finding the plans don't reach contract labour. The bottleneck
isn't the science — it's that the decision to stop work lives in a
supervisor's head and disappears at the end of the shift. What if it lived in
a ledger instead?

**The build.** A 24-hour heat-action system for one site: Open-Meteo read
(and a synthetic day for demos), two published heat engines with the *stricter
verdict winning*, a policy file that names the allowed actions per band, an
exact-phrase consent check, single-execution dry-run semantics, a store-backed
verifier, a worker-minutes ledger, and an escalation clock that treats silence
as a failure mode. 352 tests; the whole loop runs offline in one command.

**The two sentences I'd underline in the video:** *The model narrates; the
policy decides.* And: *the ledger labels its own counterfactual.*

**The honest paragraph.** Local mode is synthetic; the crew data is an
example; SMS approval and real call trees are next. What is not honest-to-a-
fault: the physics comes from Stull, ISO 7243, NIOSH, and Rothfusz, and the
three corrections we made when the secondary sources disagreed with the
primary ones are published in `docs/LEARNINGS.md`.

**Why AWS.** Bedrock for the narration, Lambda + API Gateway to serve the
very same console, DynamoDB as the single-table store where even the
escalation clock lives, SNS for the escalation, EventBridge for the two
schedules, and the SAM CLI to deploy it — least-privilege IAM (a read-only
agent role and a write-only notifier role) because safety systems should not
have the keys to write more than they need.

**Close.** Construction labour in a 41 °C Hyderabad afternoon doesn't need an
app with a model; it needs a receipt for the human who says stop. Chhaya is
that receipt.

---

*Run it yourself:* `make setup && make demo && make serve` from the repo root.