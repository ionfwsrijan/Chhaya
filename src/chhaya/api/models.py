"""Request bodies for the Chhaya HTTP API.

Responses are returned as the domain's own ``as_dict()`` payloads so the API
can never invent a field the audit trail does not contain. Requests are typed
here because a malformed request is the one place a 422 is better than a
guess.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ApproveRequest",
    "EscalationCheckRequest",
    "ExposureRequest",
    "RevokeRequest",
    "TriageRequest",
]


class TriageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    when: datetime | None = None
    workload: str | None = None
    acclimatization: str | None = None
    workers: int = Field(default=20, ge=1, le=10000)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)
    raw_message: str
    approver_name: str = Field(min_length=1)
    approver_roles: list[str] = Field(min_length=1)
    notifier_acknowledged: bool | None = None


class RevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advisory_id: str = Field(min_length=1)
    revoked_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ExposureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    when: datetime
    minutes_observed: float = Field(default=60.0, gt=0.0, le=1440.0)
    workload: str | None = None
    acclimatization: str | None = None
    workers: int = Field(default=20, ge=1, le=10000)


class EscalationCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    now: datetime | None = None
