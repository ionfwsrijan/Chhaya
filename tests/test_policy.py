"""Tests for the policy schema, loader, and consent interpreter.

The property this file exists to prove: consent is a string comparison. The
same message always produces the same decision, a near-miss is a refusal, a
role-less approver is refused even with the right phrase, and nothing the
policy does not list can ever be approved.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from chhaya.policy import (
    ActionSpec,
    ApprovalDecision,
    DeniedReason,
    HeatPolicy,
    PolicyError,
    VerifyMethod,
    action_by_id,
    check_approval,
    load_policy,
    load_policy_text,
    policy_fingerprint,
    required_phrase_display,
    unknown_action_error,
)

POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / "heat_policy_v1.yaml"


@pytest.fixture
def policy() -> HeatPolicy:
    return load_policy(POLICY_PATH)


@pytest.fixture
def shade_spec(policy: HeatPolicy) -> ActionSpec:
    return action_by_id(policy, "move_to_shade")


class TestSchema:
    def test_site_policy_loads(self, policy: HeatPolicy) -> None:
        assert policy.version == 1
        assert policy.site.id == "hyderabad-miyapur-site-b"
        assert policy.crew.default_workload.value == "heavy"
        assert policy.policy.dry_run is True

    def test_every_action_has_a_phrase_of_at_least_eight_characters(
        self, policy: HeatPolicy
    ) -> None:
        for action in policy.actions:
            assert len(action.phrase) >= 8
            assert action.phrase == action.phrase.strip()
            assert "  " not in action.phrase

    def test_every_action_notifies_someone(self, policy: HeatPolicy) -> None:
        for action in policy.actions:
            assert action.notify

    def test_stop_work_is_not_standing(self, policy: HeatPolicy) -> None:
        assert policy.action("stop_work").standing_allowed is False

    def test_standing_actions_are_the_reversible_ones(self, policy: HeatPolicy) -> None:
        for action in policy.standing_actions():
            assert action.reversible is True
            assert action.ttl_minutes is not None
            assert action.ttl_minutes <= policy.policy.max_advisory_ttl_minutes

    def test_unknown_action_raises_with_the_allowlist(self, policy: HeatPolicy) -> None:
        with pytest.raises(PolicyError, match="unknown action"):
            policy.action("deploy_drone")

    def test_unknown_action_error_helper(self, policy: HeatPolicy) -> None:
        error = unknown_action_error(policy, "deploy_drone")
        assert "move_to_shade" in str(error)
        assert "stop_work" in str(error)

    def test_action_ttls_respect_the_configured_maximum(self, policy: HeatPolicy) -> None:
        for action in policy.actions:
            if action.ttl_minutes is not None:
                assert action.ttl_minutes <= policy.policy.max_advisory_ttl_minutes


class TestLoader:
    def test_missing_file_is_a_policy_error(self, tmp_path: Path) -> None:
        with pytest.raises(PolicyError, match="not found"):
            load_policy(tmp_path / "nope.yaml")

    def test_invalid_yaml_is_a_policy_error(self) -> None:
        with pytest.raises(PolicyError, match="invalid YAML"):
            load_policy_text("version: [unclosed")

    def test_non_mapping_is_a_policy_error(self) -> None:
        with pytest.raises(PolicyError, match="must be a YAML mapping"):
            load_policy_text("- just\n- a\n- list\n")

    def test_missing_required_key_names_the_key(self) -> None:
        with pytest.raises(PolicyError, match="site"):
            load_policy_text("version: 1\n")

    def test_unknown_key_is_refused_not_ignored(self) -> None:
        text = """
version: 1
site:
  id: test-site
  name: Test
  city: Hyderabad
  state: Telangana
  country: India
  latitude: 17.0
  longitude: 78.0
  timezone: Asia/Kolkata
  elevation_m: 500
  supervisor: {name: A, phone: "1", roles: [site_supervisor]}
crew: {default_workload: heavy, default_acclimatization: unacclimatized}
thresholds: {}
policy: {}
extra_key: should_not_be_here
actions:
  - {id: stop_work, label: Stop, phrase: "APPROVE STOP WORK", approver_roles: [site_supervisor], notify: [supervisor], verify: notifier_ack, reversible: true}
escalation: {after_minutes: 10, notify_role: owner, emergency_contacts: [{name: N, phone: "1", role: owner}]}
"""
        with pytest.raises(PolicyError, match="extra_key"):
            load_policy_text(text)

    def test_duplicate_action_ids_are_refused(self) -> None:
        action = (
            '  - {id: stop_work, label: Stop, phrase: "APPROVE STOP WORK", '
            "approver_roles: [site_supervisor], notify: [supervisor], "
            "verify: notifier_ack, reversible: true}\n"
        )
        with pytest.raises(PolicyError, match="ids must be unique"):
            load_policy_text(_policy_with_actions(action + action))

    def test_duplicate_phrases_are_refused_because_consent_would_be_ambiguous(
        self,
    ) -> None:
        text = _policy_with_actions(
            '  - {id: stop_work, label: Stop, phrase: "APPROVE STOP WORK", '
            "approver_roles: [site_supervisor], notify: [supervisor], "
            "verify: notifier_ack, reversible: true}\n"
            '  - {id: hydrate, label: Water, phrase: "APPROVE STOP WORK", '
            "approver_roles: [site_supervisor], notify: [supervisor], "
            "verify: supervisor_confirm, reversible: true}\n"
        )
        with pytest.raises(PolicyError, match="phrases must be unique"):
            load_policy_text(text)

    def test_out_of_range_latitude_is_refused(self) -> None:
        bad = _policy_with_actions(
            '  - {id: stop_work, label: Stop, phrase: "APPROVE STOP WORK", '
            "approver_roles: [site_supervisor], notify: [supervisor], "
            "verify: notifier_ack, reversible: true}\n"
        ).replace("latitude: 17.0", "latitude: 91.0")
        with pytest.raises(PolicyError, match="latitude"):
            load_policy_text(bad)

    def test_verify_method_must_be_a_known_one(self) -> None:
        bad = _policy_with_actions(
            '  - {id: stop_work, label: Stop, phrase: "APPROVE STOP WORK", '
            "approver_roles: [site_supervisor], notify: [supervisor], "
            "verify: notifier_ack, reversible: true}\n"
        ).replace("verify: notifier_ack", "verify: vibes")
        with pytest.raises(PolicyError, match="verify"):
            load_policy_text(bad)


class TestFingerprint:
    def test_same_policy_same_fingerprint(self, policy: HeatPolicy) -> None:
        assert policy_fingerprint(policy) == policy_fingerprint(policy)

    def test_fingerprint_is_a_sha256_hex_digest(self, policy: HeatPolicy) -> None:
        fingerprint = policy_fingerprint(policy)
        assert len(fingerprint) == 64
        assert all(c in "0123456789abcdef" for c in fingerprint)

    def test_different_policy_different_fingerprint(self, policy: HeatPolicy) -> None:
        changed = policy.model_copy(deep=True)
        changed.policy.dry_run = not changed.policy.dry_run
        assert policy_fingerprint(changed) != policy_fingerprint(policy)

    def test_fingerprint_ignores_key_order(self, policy: HeatPolicy) -> None:
        reordered = HeatPolicy.model_validate(
            {key: policy.model_dump()[key] for key in reversed(list(policy.model_dump()))}
        )
        assert policy_fingerprint(reordered) == policy_fingerprint(policy)


class TestConsent:
    """The core property: consent is a string comparison, not a judgement."""

    def test_exact_phrase_with_right_role_is_approved(
        self, policy: HeatPolicy, shade_spec: ActionSpec
    ) -> None:
        decision = check_approval(
            policy,
            shade_spec,
            "Crew is melting. APPROVE MOVE TO SHADE",
            ("site_supervisor",),
        )
        assert decision.allowed is True
        assert decision.reason == "approved"
        assert decision.phrase_matched is True

    def test_paraphrase_is_refused(self, policy: HeatPolicy, shade_spec: ActionSpec) -> None:
        for message in (
            "please move the crew to the shade now",
            "move to shade approved",
            "APPROVE MOVE TO SHADEE",
            "APPROVE MOVE TO SHAD",
            "APPROVE  MOVE TO SHADE",
        ):
            decision = check_approval(policy, shade_spec, message, ("site_supervisor",))
            assert decision.allowed is False, message
            assert decision.reason == DeniedReason.PHRASE_MISMATCH

    def test_near_miss_is_a_refusal_not_a_guess(
        self, policy: HeatPolicy, shade_spec: ActionSpec
    ) -> None:
        decision = check_approval(policy, shade_spec, "APPROVE MOVE TO SHAD", ("site_supervisor",))
        assert decision.allowed is False
        assert decision.phrase_matched is False

    def test_case_is_significant_by_default(
        self, policy: HeatPolicy, shade_spec: ActionSpec
    ) -> None:
        decision = check_approval(policy, shade_spec, "approve move to shade", ("site_supervisor",))
        assert decision.allowed is False
        assert decision.reason == DeniedReason.PHRASE_MISMATCH

    def test_empty_message_is_refused(self, policy: HeatPolicy, shade_spec: ActionSpec) -> None:
        decision = check_approval(policy, shade_spec, "", ("site_supervisor",))
        assert decision.allowed is False

    def test_right_phrase_wrong_role_is_refused(
        self, policy: HeatPolicy, shade_spec: ActionSpec
    ) -> None:
        decision = check_approval(policy, shade_spec, "APPROVE MOVE TO SHADE", ("crew_lead",))
        assert decision.allowed is False
        assert decision.reason == DeniedReason.MISSING_ROLE
        assert "site_supervisor" in decision.explain

    def test_no_roles_at_all_is_refused(self, policy: HeatPolicy, shade_spec: ActionSpec) -> None:
        decision = check_approval(policy, shade_spec, "APPROVE MOVE TO SHADE", ())
        assert decision.allowed is False
        assert decision.reason == DeniedReason.MISSING_ROLE

    def test_any_one_of_several_roles_is_enough(self, policy: HeatPolicy) -> None:
        spec = action_by_id(policy, "reschedule_shift")
        assert check_approval(
            policy, spec, "APPROVE RESCHEDULE SHIFT", ("site_supervisor",)
        ).allowed
        # safety_officer is not in reschedule_shift's approver_roles.
        assert not check_approval(
            policy, spec, "APPROVE RESCHEDULE SHIFT", ("safety_officer",)
        ).allowed

    def test_issue_advisory_requires_the_safety_officer(self, policy: HeatPolicy) -> None:
        spec = action_by_id(policy, "issue_advisory")
        assert check_approval(
            policy, spec, "APPROVE STANDING ADVISORY", ("safety_officer",)
        ).allowed
        assert not check_approval(
            policy, spec, "APPROVE STANDING ADVISORY", ("site_supervisor",)
        ).allowed

    def test_decision_is_reproducible(self, policy: HeatPolicy, shade_spec: ActionSpec) -> None:
        args = (policy, shade_spec, "APPROVE MOVE TO SHADE", ("site_supervisor",))
        first = check_approval(*args)
        second = check_approval(*args)
        assert first == second

    def test_as_dict_is_json_safe(self, policy: HeatPolicy, shade_spec: ActionSpec) -> None:
        decision = check_approval(policy, shade_spec, "nope", ("site_supervisor",))
        data = decision.as_dict()
        assert data["allowed"] is False
        assert data["reason"] == DeniedReason.PHRASE_MISMATCH
        assert isinstance(data["approver_roles"], list)

    def test_approved_property_aliases_allowed(
        self, policy: HeatPolicy, shade_spec: ActionSpec
    ) -> None:
        decision = check_approval(policy, shade_spec, "APPROVE MOVE TO SHADE", ("site_supervisor",))
        assert decision.approved is decision.allowed is True


class TestCasefoldMode:
    @pytest.fixture
    def lenient_policy(self) -> HeatPolicy:
        text = POLICY_PATH.read_text(encoding="utf-8").replace("casefold: false", "casefold: true")
        return load_policy_text(text)

    def test_lowercase_phrase_is_approved_when_casefold_is_on(
        self, lenient_policy: HeatPolicy
    ) -> None:
        spec = action_by_id(lenient_policy, "move_to_shade")
        decision = check_approval(
            lenient_policy, spec, "approve move to shade", ("site_supervisor",)
        )
        assert decision.allowed is True

    def test_a_near_miss_is_still_refused_when_casefold_is_on(
        self, lenient_policy: HeatPolicy
    ) -> None:
        spec = action_by_id(lenient_policy, "move_to_shade")
        decision = check_approval(
            lenient_policy, spec, "approve move to shad", ("site_supervisor",)
        )
        assert decision.allowed is False
        assert decision.reason == DeniedReason.PHRASE_MISMATCH


class TestStandingAdvisories:
    def test_actions_marked_standing_may_be_issued_as_standing(self, policy: HeatPolicy) -> None:
        spec = action_by_id(policy, "start_rest_cycle")
        decision = check_approval(
            policy, spec, "APPROVE REST CYCLE", ("site_supervisor",), standing=True
        )
        assert decision.allowed is True

    def test_stop_work_may_not_become_a_standing_advisory(self, policy: HeatPolicy) -> None:
        spec = action_by_id(policy, "stop_work")
        decision = check_approval(
            policy, spec, "APPROVE STOP WORK", ("site_supervisor",), standing=True
        )
        assert decision.allowed is False
        assert decision.reason == DeniedReason.NOT_STANDING

    def test_standing_flag_does_not_block_a_one_shot_action(self, policy: HeatPolicy) -> None:
        spec = action_by_id(policy, "stop_work")
        decision = check_approval(
            policy, spec, "APPROVE STOP WORK", ("site_supervisor",), standing=False
        )
        assert decision.allowed is True


class TestHelpers:
    def test_required_phrase_display_shows_the_phrase_verbatim(
        self, shade_spec: ActionSpec
    ) -> None:
        assert required_phrase_display(shade_spec) == "APPROVE MOVE TO SHADE"

    def test_verify_methods_are_the_documented_four(self) -> None:
        assert {method.value for method in VerifyMethod} == {
            "supervisor_confirm",
            "store_present",
            "notifier_ack",
            "geofence",
        }


class TestApprovalDecision:
    def test_is_frozen(self, policy: HeatPolicy, shade_spec: ActionSpec) -> None:
        decision: ApprovalDecision = check_approval(
            policy, shade_spec, "APPROVE MOVE TO SHADE", ("site_supervisor",)
        )
        with pytest.raises(FrozenInstanceError):
            decision.allowed = False  # type: ignore[misc]


def _minimal_policy() -> str:
    """A valid policy skeleton through ``policy:``, for mutation-based tests."""
    return """
version: 1
site:
  id: test-site
  name: Test
  city: Hyderabad
  state: Telangana
  country: India
  latitude: 17.0
  longitude: 78.0
  timezone: Asia/Kolkata
  elevation_m: 500
  supervisor: {name: A, phone: "1", roles: [site_supervisor]}
crew: {default_workload: heavy, default_acclimatization: unacclimatized}
thresholds: {}
policy: {}
"""


def _policy_with_actions(actions_yaml: str) -> str:
    return _minimal_policy() + (
        f"actions:\n{actions_yaml}"
        "escalation: {after_minutes: 10, notify_role: owner, "
        'emergency_contacts: [{name: N, phone: "1", role: owner}]}\n'
    )
