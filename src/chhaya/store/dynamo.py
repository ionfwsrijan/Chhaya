"""DynamoDB persistence: the store the AWS deployment actually runs on.

Single-table design, one table for all four collections, so the SAM template
declares exactly one resource and the IAM policy is one ARN:

* partition key ``pk`` = ``SITE#<site_id>``
* sort key ``sk`` = ``<KIND>#<timestamp>#<entity_id>`` where KIND is ADV, REC,
  EXP or COV, so a Query returns each collection in timestamp order.
* GSI ``gsi1`` (``gsi1pk`` = ``ID#<entity_id>``) exists so a record can be
  fetched by its own id when the site is not known — the same call the local
  store answers with a linear scan.

Every write is a PutItem on a deterministic key, so a retried Lambda rewrites
the same row instead of duplicating history. boto3 is imported lazily and the
table is injectable, so the local build never loads the AWS SDK and tests run
against moto.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from chhaya.safety.actions import ActionRecord
from chhaya.safety.advisory import StandingAdvisory
from chhaya.safety.escalate import PendingProposal
from chhaya.safety.exposure import CoverageEvent, ExposureEvent
from chhaya.store.rows import (
    advisory_from,
    coverage_from,
    exposure_from,
    pending_from,
    record_from,
)

__all__ = ["DynamoStore", "ensure_table", "table_definition"]

_ADV = "ADV#"
_REC = "REC#"
_EXP = "EXP#"
_COV = "COV#"
_PEND = "PEND#"
_ID = "ID#"
_SITE = "SITE#"


def table_definition(table_name: str) -> dict[str, Any]:
    """The CreateTable request, also mirrored by the SAM template."""
    return {
        "TableName": table_name,
        "KeySchema": [
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "gsi1pk", "AttributeType": "S"},
            {"AttributeName": "gsi1sk", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "gsi1",
                "KeySchema": [
                    {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        "BillingMode": "PAY_PER_REQUEST",
    }


def ensure_table(client: Any, table_name: str) -> None:
    """Create the table if it does not exist (tests and first-run bootstrap)."""
    existing = client.list_tables()["TableNames"]
    if table_name in existing:
        return
    client.create_table(**table_definition(table_name))
    client.get_waiter("table_exists").wait(TableName=table_name)


def _decimalize(value: Any) -> Any:
    """DynamoDB's low-level client wants Decimal, not float. Recurse."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _decimalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_decimalize(item) for item in value]
    return value


def _key(kind: str, stamp: datetime, entity_id: str) -> str:
    return f"{kind}{stamp.isoformat()}#{entity_id}"


def _index_keys(entity_id: str, site_id: str) -> dict[str, str]:
    return {"gsi1pk": f"{_ID}{entity_id}", "gsi1sk": site_id}


class DynamoStore:
    """A :class:`~chhaya.store.base.HeatStore` backed by one DynamoDB table."""

    def __init__(
        self,
        table_name: str,
        *,
        table: Any | None = None,
        resource: Any | None = None,
        region: str | None = None,
        create: bool = False,
    ) -> None:
        if not table_name:
            raise ValueError("table_name is required")
        self.table_name = table_name
        if table is None:
            import boto3

            resource = resource or boto3.resource("dynamodb", region_name=region)
            if create:
                ensure_table(resource.meta.client, table_name)
            table = resource.Table(table_name)
        self._table = table

    # --- internals ------------------------------------------------------

    def _put(self, item: dict[str, Any]) -> None:
        self._table.put_item(Item=_decimalize(item))

    def _query(
        self,
        pk: str,
        prefix: str,
        *,
        descending: bool,
    ) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        items: list[dict[str, Any]] = []
        start: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": Key("pk").eq(pk) & Key("sk").begins_with(prefix),
                "ScanIndexForward": not descending,
            }
            if start is not None:
                kwargs["ExclusiveStartKey"] = start
            response = self._table.query(**kwargs)
            items.extend(response.get("Items", []))
            start = response.get("LastEvaluatedKey")
            if not start:
                break
        return items

    def _by_id(self, entity_id: str) -> dict[str, Any] | None:
        from boto3.dynamodb.conditions import Key

        response = self._table.query(
            IndexName="gsi1",
            KeyConditionExpression=Key("gsi1pk").eq(f"{_ID}{entity_id}"),
        )
        items = response.get("Items", [])
        return items[0] if items else None

    # --- advisories -----------------------------------------------------

    def put_advisory(self, advisory: StandingAdvisory) -> None:
        item = {
            **advisory.as_dict(),
            "pk": f"{_SITE}{advisory.site_id}",
            "sk": _key(_ADV, advisory.issued_at, advisory.advisory_id),
            **_index_keys(advisory.advisory_id, advisory.site_id),
        }
        self._put(item)

    def get_advisory(self, advisory_id: str) -> StandingAdvisory | None:
        row = self._by_id(advisory_id)
        return None if row is None else advisory_from(row)

    def list_advisories(
        self,
        site_id: str,
        *,
        active_only: bool = False,
        now: datetime | None = None,
    ) -> list[StandingAdvisory]:
        rows = self._query(f"{_SITE}{site_id}", _ADV, descending=True)
        advisories = [advisory_from(row) for row in rows]
        if active_only:
            as_of = now or datetime.now()
            advisories = [a for a in advisories if a.is_active_at(as_of)]
        return advisories

    def revoke_advisory(
        self,
        advisory_id: str,
        *,
        revoked_at: datetime,
        revoked_by: str,
        reason: str,
    ) -> StandingAdvisory:
        advisory = self.get_advisory(advisory_id)
        if advisory is None:
            raise KeyError(f"unknown advisory {advisory_id}")
        if advisory.revoked:
            raise ValueError(f"advisory {advisory_id} is already revoked")
        revoked = StandingAdvisory(
            site_id=advisory.site_id,
            action_id=advisory.action_id,
            band_at_issue=advisory.band_at_issue,
            wbgt_c=advisory.wbgt_c,
            issued_at=advisory.issued_at,
            expires_at=advisory.expires_at,
            issued_by=advisory.issued_by,
            approval_phrase=advisory.approval_phrase,
            reason=advisory.reason,
            policy_fingerprint=advisory.policy_fingerprint,
            advisory_id=advisory.advisory_id,
            revoked_at=revoked_at,
            revoked_by=revoked_by,
            revoke_reason=reason,
        )
        self.put_advisory(revoked)
        return revoked

    # --- action records -------------------------------------------------

    def put_action_record(self, record: ActionRecord) -> None:
        item = {
            **record.as_dict(),
            "pk": f"{_SITE}{record.site_id}",
            "sk": _key(_REC, record.proposed_at, record.record_id),
            **_index_keys(record.record_id, record.site_id),
        }
        self._put(item)

    def list_action_records(
        self, site_id: str, *, since: datetime | None = None
    ) -> list[ActionRecord]:
        rows = self._query(f"{_SITE}{site_id}", _REC, descending=True)
        records = [record_from(row) for row in rows]
        if since is not None:
            records = [r for r in records if r.proposed_at >= since]
        return records

    # --- exposure ledger ------------------------------------------------

    def append_exposure(self, event: ExposureEvent) -> None:
        item = {
            **event.as_dict(),
            "pk": f"{_SITE}{event.site_id}",
            "sk": _key(_EXP, event.observed_at, event.event_id),
        }
        self._put(item)

    def append_coverage(self, event: CoverageEvent) -> None:
        item = {
            **event.as_dict(),
            "pk": f"{_SITE}{event.site_id}",
            "sk": _key(_COV, event.started_at, event.event_id),
        }
        self._put(item)

    def list_exposure(self, site_id: str, *, since: datetime | None = None) -> list[ExposureEvent]:
        rows = self._query(f"{_SITE}{site_id}", _EXP, descending=False)
        events = [exposure_from(row) for row in rows]
        if since is not None:
            events = [e for e in events if e.observed_at >= since]
        return events

    def list_coverage(self, site_id: str, *, since: datetime | None = None) -> list[CoverageEvent]:
        rows = self._query(f"{_SITE}{site_id}", _COV, descending=False)
        events = [coverage_from(row) for row in rows]
        if since is not None:
            events = [e for e in events if e.started_at >= since]
        return events

    # --- pending proposals ----------------------------------------------

    def put_pending(self, pending: PendingProposal) -> None:
        item = {
            **pending.as_dict(),
            "pk": f"{_SITE}{pending.site_id}",
            "sk": _key(_PEND, pending.proposed_at, pending.proposal_id),
            **_index_keys(pending.proposal_id, pending.site_id),
        }
        self._put(item)

    def get_pending(self, proposal_id: str) -> PendingProposal | None:
        row = self._by_id(proposal_id)
        return None if row is None else pending_from(row)

    def list_pending(self, site_id: str, *, unresolved_only: bool = False) -> list[PendingProposal]:
        rows = self._query(f"{_SITE}{site_id}", _PEND, descending=False)
        pending = [pending_from(row) for row in rows]
        if unresolved_only:
            pending = [p for p in pending if not p.resolved]
        return pending
