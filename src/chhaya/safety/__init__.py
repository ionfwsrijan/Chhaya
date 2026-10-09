"""The safety loop: what Chhaya does when the heat is dangerous.

Triage runs two published engines over one hour of weather and keeps the
stricter verdict. The policy file says which actions that verdict proposes.
A human approves with an exact phrase. The action executes inside the
policy's dry-run flag. Its post-condition is verified against the store, not
against the executor's own report. Everything is recorded with the policy
fingerprint that was in force.
"""

from chhaya.safety.actions import ActionOutcome, ActionRecord, VerificationOutcome
from chhaya.safety.advisory import AdvisoryError, AdvisoryManager, StandingAdvisory
from chhaya.safety.escalate import (
    EscalationEvent,
    PendingProposal,
    check_escalation,
)
from chhaya.safety.exposure import CoverageEvent, ExposureEvent, ExposureLedger
from chhaya.safety.orchestrator import (
    ExecutionResult,
    Proposal,
    TriageResult,
    approve_and_execute,
    ledger_from_store,
    propose_actions,
    record_coverage,
    record_exposure,
    record_pending,
    resolve_pending,
    revoke_advisory,
    triage,
)
from chhaya.safety.verify import Verification, verify_action

__all__ = [
    "ActionOutcome",
    "ActionRecord",
    "AdvisoryError",
    "AdvisoryManager",
    "CoverageEvent",
    "EscalationEvent",
    "ExecutionResult",
    "ExposureEvent",
    "ExposureLedger",
    "PendingProposal",
    "Proposal",
    "StandingAdvisory",
    "TriageResult",
    "Verification",
    "VerificationOutcome",
    "approve_and_execute",
    "check_escalation",
    "ledger_from_store",
    "propose_actions",
    "record_coverage",
    "record_exposure",
    "record_pending",
    "resolve_pending",
    "revoke_advisory",
    "triage",
    "verify_action",
]
