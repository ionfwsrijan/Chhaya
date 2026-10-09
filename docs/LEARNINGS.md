# Chhaya — learnings (the honest list)

The judges' most valuable feedback loop is: *say what is real and what is
simulated*. This file is that list, in two halves.

## Numerical corrections that came out of this build

Three places where a secondary source disagreed with a primary source, and the
answer we shipped (each has a regression test named after the source):

1. **Stull (2011), 20 °C / 50 % → 13.78 °C vs 13.6993 °C.** Many blog posts
   copy the rounded value. Recomputing the published formula against the
   paper's own inputs gives 13.6993 °C; the paper itself prints 13.78 in a
   table. We ship the formula's value and note the paper's rounding.
2. **NIOSH work-rest Table 5-1.** Widely-quoted schedules on the web disagree
   at the heavy/very-heavy boundary. We transcribed the numbers from the
   primary PDF (NIOSH 2016-106, rev. 2016) and the tests name the table.
3. **Heat index below 80 °F.** The Rothfusz regression produces an index
   *below* a cold air temperature. NWS never reports HI below 80 °F. We ship
   `air temperature` when `hi_f < 80`, so Chhaya cannot under-state heat.

## ACGIH was paywalled

The ACGIH TLVWBGT screening table is paywalled; crossing it from NIOSH's
Table 5-1 is the documented route (NIOSH reprints the ACGIH curve and adds the
workload columns). We record in the code that the ACGIH-derived screening
column traces to NIOSH's transcription, not the original PDF.

## Product honesty

- **Synthetic vs live.** Local mode is a deterministic fake day; `make demo`
  with the wifi off is a feature, and the console labels it `mode: local`.
  Live forecast mode reads Open-Meteo (no key). Bedrock is the AWS agent;
  the scripted agent is the tested fallback, and Bedrock access on a new
  account can lag — we say so.
- **Example data.** The site, workforce, telephone numbers, and contact names
  are example policy values, marked as such in the YAML comment.
- **No invented metrics.** The ledger is worker-minutes × band, coverage
  minus; the summary says "counterfactual" on the label.
- **The line between a stop and a stop.** Chhaya makes the decision, the
  human makes them stop. The system failing closed (escalation) is the best
  it can do alone, and we state that.

## The policy gotcha

This build taught us to keep **consent as exact text** (`check_approval` —
word boundary, case-sensitive). In a real deployment the phrase itself would
be the legal artifact; making it a testable regex was how a typo stayed a
refusal instead of becoming a loophole.

## What's next (if this were a product)

- SMS-directed approval (one reply phrase = one action) with phone-verified
  endpoints.
- A real AMS/outlook integration: advisories to Scheduler/Ekatra-style
  platforms with the dry-run flag defaulting on.
- WBGT meter ingestion (`measured_wbgt_c` already exists in the model).
- Per-worker exposure tracking with rest minutes, instead of crew-level
  counting.