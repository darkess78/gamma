"""Convert copied legacy rows without granting identity or access authority.

This module is deliberately independent of SQLite and the live application. The
caller owns loading an isolated snapshot and committing a reviewed import. IDs
for canonical entities use the original primary key; source IDs additionally
identify the immutable payload version. Unsupported rows stay in the snapshot
and receive content-free quarantine references here.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Any


_TABLES = (
    "knownperson", "personidentity", "profilefact", "episodicmemory",
    "conversationjournalentry",
)
_PRIVATE = "owner_private"
_UNVERIFIED = "legacy_unverified"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _id(prefix: str, namespace: str, table: str, key: Any, *version: str) -> str:
    return f"{prefix}-{_hash([namespace, table, key, *version])[:24]}"


def _valid_key(value: Any) -> bool:
    if type(value) is int:
        return 0 <= value <= 2**63 - 1
    valid = (
        isinstance(value, str) and 0 < len(value) <= 128
        and bool(value.strip()) and all(ord(char) >= 32 for char in value)
    )
    if valid:
        try:
            value.encode("utf-8")
        except UnicodeError:
            return False
    return valid


def _text(value: Any, *, optional: bool = False) -> bool:
    return (
        isinstance(value, str) and (optional or bool(value.strip()))
        and not any(ord(char) < 32 and char not in "\n\r\t" for char in value)
    )


def _confidence(row: dict[str, Any]) -> float | None:
    value = row.get("confidence", 0.5)
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return min(1.0, max(0.0, number))


def _attribution(value: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return "unknown", {"attribution_invalid": True}
    if value is None:
        return "unknown", {}
    if not isinstance(value, dict):
        return "unknown", {"attribution_invalid": True}
    metadata = {
        key: item for key, item in value.items()
        if key in {"kind", "reporter_name", "reporter_id", "source", "detail"}
        and isinstance(item, str)
    }
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in {
        "manual", "self_report", "reported", "report", "tool", "observed",
    }:
        kind = "unknown"
    return kind, {"attribution": metadata}


def convert_legacy(namespace: str, tables: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return deterministic private candidates, provenance and quarantine refs.

    Names, legacy trust labels and reported confidence never grant authority.
    Only explicit legacy subject types bind a memory to owner/self. Journal
    messages remain experiences rather than becoming inferred durable facts.
    """
    if not _valid_key(namespace) or not isinstance(namespace, str):
        raise ValueError("A nonempty stable source namespace is required")
    try:
        namespace.encode("utf-8")
    except UnicodeError as error:
        raise ValueError("A UTF-8 source namespace is required") from error

    result: dict[str, list[dict[str, Any]]] = {
        key: [] for key in ("sources", "records", "people", "accounts", "quarantine")
    }

    def quarantine(table: str, key: Any, reason: str) -> None:
        result["quarantine"].append({
            "table": table, "id": key if _valid_key(key) else None, "reason": reason,
        })

    if not isinstance(tables, dict):
        quarantine("snapshot", None, "invalid_tables")
        return result

    prepared: dict[str, list[tuple[dict[str, Any], str, bool]]] = {}
    for table in _TABLES:
        rows = tables.get(table, [])
        if not isinstance(rows, (list, tuple)):
            quarantine(table, None, "invalid_table_rows")
            prepared[table] = []
            continue
        versions: dict[str, dict[str, tuple[dict[str, Any], str]]] = defaultdict(dict)
        for row in rows:
            if not isinstance(row, dict) or any(not isinstance(key, str) for key in row):
                quarantine(table, None, "invalid_row")
                continue
            key = row.get("id")
            if not _valid_key(key):
                quarantine(table, key, "invalid_primary_key")
                continue
            try:
                payload_hash = _hash(row)
            except (ValueError, TypeError, UnicodeError, RecursionError):
                quarantine(table, key, "unsupported_payload")
                continue
            versions[_json(key)][payload_hash] = (row, payload_hash)
        prepared[table] = []
        for key in sorted(versions):
            conflicting = len(versions[key]) > 1
            for payload_hash in sorted(versions[key]):
                row, digest = versions[key][payload_hash]
                prepared[table].append((row, digest, conflicting))
                if conflicting:
                    quarantine(table, row["id"], "conflicting_primary_key")

    source_by_id: dict[str, dict[str, Any]] = {}

    def source(table: str, row: dict[str, Any], digest: str, conflict: bool,
               text: Any, kind: str, origin: str = "unknown",
               metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        occurred_at = row.get("occurred_at") if table == "conversationjournalentry" else row.get("created_at")
        item = {
            "id": _id("src", namespace, table, row["id"], digest),
            "legacy_table": table, "legacy_id": row["id"],
            "legacy_payload_sha256": digest,
            "occurred_at": occurred_at if isinstance(occurred_at, str) else None,
            "text": text if isinstance(text, str) else "",
            "source_kind": kind, "origin_kind": origin,
            "certainty": _UNVERIFIED, "scope": _PRIVATE,
            "status": "quarantined" if conflict else "active",
            "legacy_metadata": dict(metadata or {}),
        }
        for key in ("privacy_scope", "subject_type", "subject_name", "relationship_to_user",
                    "session_id", "tags", "updated_at"):
            if isinstance(row.get(key), str):
                item["legacy_metadata"][key] = row[key]
        if occurred_at is not None and not isinstance(occurred_at, str):
            item["status"] = "quarantined"
            quarantine(table, row["id"], "invalid_occurred_at")
        result["sources"].append(item)
        source_by_id[item["id"]] = item
        return item

    def reject(item: dict[str, Any], reason: str) -> None:
        item["status"] = "quarantined"
        quarantine(item["legacy_table"], item["legacy_id"], reason)

    people_by_key: dict[str, str] = {}
    for row, digest, conflict in prepared["knownperson"]:
        metadata = {
            key: row[key] for key in ("relationship_to_user", "notes", "trust")
            if isinstance(row.get(key), str)
        }
        item = source("knownperson", row, digest, conflict, row.get("name"),
                      "legacy_person", metadata=metadata)
        if (not _text(row.get("name"))
                or (row.get("relationship_to_user") is not None
                    and not _text(row["relationship_to_user"], optional=True)) or any(
            not _text(row.get(key, ""), optional=True)
            for key in ("notes", "trust")
        )):
            reject(item, "invalid_person_fields")
        if item["status"] != "active":
            continue
        person_id = _id("person", namespace, "knownperson", row["id"])
        people_by_key[_json(row["id"])] = person_id
        result["people"].append({
            "id": person_id, "name": row["name"],
            "relationship": row.get("relationship_to_user") or "",
            "notes": row.get("notes", ""), "legacy_trust_label": row.get("trust", ""),
            "source_id": item["id"], "scope": _PRIVATE,
            "legacy_table": "knownperson", "legacy_id": row["id"],
        })

    candidate_accounts: list[dict[str, Any]] = []
    for row, digest, conflict in prepared["personidentity"]:
        metadata = {key: row[key] for key in ("platform", "platform_user_id", "display_name")
                    if isinstance(row.get(key), str) or (key in row and row[key] is None)}
        item = source("personidentity", row, digest, conflict, row.get("display_name") or "",
                      "legacy_identity", metadata=metadata)
        key = row.get("person_id")
        person_id = people_by_key.get(_json(key)) if _valid_key(key) else None
        if not person_id:
            reject(item, "missing_person_link")
        if (not _text(row.get("platform")) or not _text(row.get("platform_user_id"))
                or (row.get("display_name") is not None
                    and not _text(row["display_name"], optional=True))):
            reject(item, "invalid_account_fields")
        if item["status"] != "active":
            continue
        candidate_accounts.append({
            "id": _id("account", namespace, "personidentity", row["id"]),
            "person_id": person_id, "platform": row["platform"],
            "platform_user_id": row["platform_user_id"],
            "display_name": row.get("display_name") or "",
            "source_id": item["id"], "scope": _PRIVATE,
            "legacy_table": "personidentity", "legacy_id": row["id"],
        })
    tuple_people: dict[tuple[str, str], set[str]] = defaultdict(set)
    for account in candidate_accounts:
        tuple_people[account["platform"], account["platform_user_id"]].add(account["person_id"])
    for account in candidate_accounts:
        if len(tuple_people[account["platform"], account["platform_user_id"]]) > 1:
            reject(source_by_id[account["source_id"]], "ambiguous_account_link")
        else:
            result["accounts"].append(account)

    for table in ("profilefact", "episodicmemory"):
        for row, digest, conflict in prepared[table]:
            text = row.get("fact_text" if table == "profilefact" else "summary")
            origin, metadata = _attribution(row.get("attribution"))
            source_kind = {
                "manual": "legacy_admin", "self_report": "legacy_reported",
                "reported": "legacy_reported", "report": "legacy_reported",
                "tool": "legacy_tool", "observed": "legacy_observation",
            }.get(origin, "legacy_profile" if table == "profilefact" else "legacy_memory")
            item = source(table, row, digest, conflict, text, source_kind, origin, metadata)
            confidence = _confidence(row)
            if not _text(text):
                reject(item, "invalid_memory_text")
            if confidence is None:
                reject(item, "invalid_confidence")
            if item["status"] != "active":
                continue
            subject = row.get("subject_type")
            subject_id = {"primary_user": "owner", "assistant": "self", "shana": "self"}.get(
                subject if isinstance(subject, str) else "",
                _id("unresolved", namespace, table, row["id"]),
            )
            category = row.get("category")
            kind = category if category in ("fact", "preference", "observation") else "fact"
            if table == "episodicmemory":
                kind = "observation"
            result["records"].append({
                "id": _id("mem", namespace, table, row["id"]),
                "kind": kind, "topic": category if _text(category) else (
                    "profile" if table == "profilefact" else "episodic"),
                "text": text, "subject_id": subject_id, "source_id": item["id"],
                "source_kind": item["source_kind"], "scope": _PRIVATE,
                "source_scope": _PRIVATE, "certainty": _UNVERIFIED,
                "confidence": confidence, "status": "active", "revision": 1,
                "history": [], "legacy_table": table, "legacy_id": row["id"],
            })

    for row, digest, conflict in prepared["conversationjournalentry"]:
        metadata = {key: row[key] for key in (
            "role", "speaker_name", "session_id", "exchange_id", "completion_status",
        ) if isinstance(row.get(key), str)}
        item = source("conversationjournalentry", row, digest, conflict, row.get("text"),
                      "legacy_journal", "conversation", metadata)
        item["actor_person_id"] = "self" if row.get("role") == "assistant" else None
        if row.get("completion_status") != "completed":
            reject(item, "incomplete_journal")
        if not _text(row.get("text")) or row.get("role") not in ("user", "assistant"):
            reject(item, "invalid_journal_fields")

    for key, items in result.items():
        if key == "quarantine":
            result[key] = [json.loads(value) for value in sorted({_json(item) for item in items})]
        else:
            items.sort(key=lambda item: item["id"])
    return result
