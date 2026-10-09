"""AWS Lambda entry points.

The handlers are thin: they build a runtime from the environment, read the
clock out of the event, and call the same orchestrator the API and the tests
call. No safety logic lives here, because everything that decides whether a
crew keeps working must be provable without an AWS account.

* :mod:`chhaya.handlers.triage` — scheduled triage: record the hour, start the
  escalation clock.
* :mod:`chhaya.handlers.escalation` — scheduled clock check: notify when a
  stop proposal goes unanswered.
* :mod:`chhaya.handlers.api` — the FastAPI console behind API Gateway.
"""

from __future__ import annotations

__all__: list[str] = []
