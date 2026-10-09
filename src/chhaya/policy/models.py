"""Policy schema: what a site is allowed to do, encoded as strict data.

The policy file is the single source of truth for what Chhaya may ever
propose, who may approve it, the exact phrase that constitutes approval, how
the action is verified after the fact, and how long it may persist. The
interpreter in :mod:`chhaya.policy.engine` never improvises: anything not
written here is refused.

Everything is ``extra="forbid"`` so a typo in the YAML fails to load rather
than silently defaulting. A safety policy that quietly ignores half its own
keys is worse than no policy at all.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chhaya.domain.bands import Band
from chhaya.domain.screening import Acclimatization
from chhaya.domain.workload import Workload


class PolicyError(ValueError):
    """A policy file is malformed, unsafe, or self-contradictory."""


class VerifyMethod(StrEnum):
    """How Chhaya proves an action actually happened.

    ``SUPERVISOR_CONFIRM`` a human types confirmation back.
    ``STORE_PRESENT`` the standing advisory is present and unexpired in the
        store, which is checkable without any human in the loop.
    ``NOTIFIER_ACK`` the notifier accepted and confirmed delivery of the
        message (SNS ``success`` in AWS, a logged write locally).
    ``GEOFENCE`` the crew's reported position moved inside the safe zone.
    """

    SUPERVISOR_CONFIRM = "supervisor_confirm"
    STORE_PRESENT = "store_present"
    NOTIFIER_ACK = "notifier_ack"
    GEOFENCE = "geofence"


class EmergencyContact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    role: str = Field(min_length=1)


class SiteSupervisor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    roles: list[str] = Field(min_length=1)


class Site(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    name: str = Field(min_length=1)
    city: str = Field(min_length=1)
    state: str = Field(min_length=1)
    country: str = Field(min_length=1)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    timezone: str = Field(min_length=1)
    elevation_m: float = Field(ge=-500.0, le=9000.0)
    supervisor: SiteSupervisor


class CrewDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_workload: Workload
    default_acclimatization: Acclimatization


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caution_margin_c: float | None = Field(default=None, ge=0.0, le=10.0)
    stop_when_band: Band = Band.STOP


class ApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_minutes: int = Field(default=10, ge=1, le=120)
    casefold: bool = False


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool = False
    max_actions_per_hour: int = Field(default=6, ge=1, le=60)
    max_advisory_ttl_minutes: int = Field(default=480, ge=15, le=1440)
    approval: ApprovalPolicy = Field(default_factory=ApprovalPolicy)


class ActionSpec(BaseModel):
    """One action the system may ever propose, and everything about consent.

    ``phrase`` is the exact string a human must type for the action to be
    approved. It is matched against the raw message text with no
    summarisation, no fuzzy matching, and no model in the middle: consent is
    a string comparison, so the same message always yields the same decision.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    phrase: str = Field(min_length=8)
    approver_roles: list[str] = Field(min_length=1)
    notify: list[str] = Field(min_length=1)
    verify: VerifyMethod
    reversible: bool
    ttl_minutes: int | None = Field(default=None, ge=1, le=1440)
    standing_allowed: bool = False

    @field_validator("phrase")
    @classmethod
    def _phrase_is_unambiguous(cls, phrase: str) -> str:
        if phrase != phrase.strip():
            raise ValueError("approval phrase must not start or end with whitespace")
        if "  " in phrase:
            raise ValueError("approval phrase must not contain double spaces")
        return phrase


class Escalation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    after_minutes: int = Field(ge=1, le=240)
    notify_role: str = Field(min_length=1)
    emergency_contacts: list[EmergencyContact] = Field(min_length=1)


class Proposals(BaseModel):
    """Which actions the system proposes when each band is reached.

    This is the site's own escalation ladder, written down: at Normal nothing
    is proposed, at Caution the low-friction crew-level actions, at Stop the
    actions that halt work. An empty list is a valid answer — a site may
    decide Caution needs no automatic proposal.
    """

    model_config = ConfigDict(extra="forbid")

    normal: list[str] = Field(default_factory=list)
    caution: list[str] = Field(default_factory=list)
    stop: list[str] = Field(default_factory=list)


class HeatPolicy(BaseModel):
    """A complete, self-consistent site heat policy."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    site: Site
    crew: CrewDefaults
    thresholds: Thresholds
    policy: PolicyConfig
    actions: list[ActionSpec] = Field(min_length=1)
    proposals: Proposals = Field(default_factory=Proposals)
    escalation: Escalation

    @model_validator(mode="after")
    def _ids_and_phrases_are_unique(self) -> HeatPolicy:
        ids = [action.id for action in self.actions]
        if len(ids) != len(set(ids)):
            raise PolicyError("action ids must be unique")
        phrases = [action.phrase for action in self.actions]
        if len(phrases) != len(set(phrases)):
            raise PolicyError(
                "approval phrases must be unique across actions; two actions "
                "sharing a phrase would make consent ambiguous"
            )
        known = set(ids)
        for band_name, proposed in (
            ("normal", self.proposals.normal),
            ("caution", self.proposals.caution),
            ("stop", self.proposals.stop),
        ):
            unknown = [action_id for action_id in proposed if action_id not in known]
            if unknown:
                raise PolicyError(
                    f"proposals.{band_name} references unknown action(s): {', '.join(unknown)}"
                )
        return self

    def action(self, action_id: str) -> ActionSpec:
        """Look up an action by id, or refuse loudly."""
        for spec in self.actions:
            if spec.id == action_id:
                return spec
        allowed = ", ".join(sorted(a.id for a in self.actions))
        raise PolicyError(f"unknown action {action_id!r}; policy allows only: {allowed}")

    def standing_actions(self) -> tuple[ActionSpec, ...]:
        return tuple(a for a in self.actions if a.standing_allowed)

    def proposed_actions_for(self, band: Band) -> tuple[str, ...]:
        """The action ids this policy proposes when the site reaches ``band``."""
        if band is Band.NORMAL:
            return tuple(self.proposals.normal)
        if band is Band.CAUTION:
            return tuple(self.proposals.caution)
        return tuple(self.proposals.stop)
