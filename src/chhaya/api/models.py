"""Request bodies for the Chhaya HTTP API.

Responses are returned as the domain's own ``as_dict()`` payloads so the API
can never invent a field the audit trail does not contain. Requests are typed
here because a malformed request is the one place a 422 is better than a
guess.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

__all__ = [
    "ApproveRequest",
    "EscalationCheckRequest",
    "ExposureRequest",
    "NaiveDatetime",
    "RevokeRequest",
    "TriageRequest",
]


def _to_naive_utc(value: datetime) -> datetime:
    """Fold any client offset down to the naive-UTC convention the engines use.

    The domain (and every stored row) works in naive time, the same way the
    recording format does. A client that sends ``2026-05-18T15:00:00Z`` would
    otherwise introduce an aware datetime that no longer compares against the
    clock the scheduled handlers read from EventBridge.
    """
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


NaiveDatetime = Annotated[datetime, AfterValidator(_to_naive_utc)]


class TriageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    when: NaiveDatetime | None = None
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

    when: NaiveDatetime
    minutes_observed: float = Field(default=60.0, gt=0.0, le=1440.0)
    workload: str | None = None
    acclimatization: str | None = None
    workers: int = Field(default=20, ge=1, le=10000)


class EscalationCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    now: NaiveDatetime | None = None
