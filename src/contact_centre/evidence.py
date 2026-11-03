"""Optional, allowlisted evidence projection; never part of the model's message history."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

CAPABILITIES = {
    "identify_customer": "Customer",
    "get_line_status": "Line",
    "get_known_incidents": "Area incidents",
    "check_equipment": "Modem",
    "knowledge_base_retrieve": "Runbooks",
}


@dataclass
class EvidenceRow:
    key: str
    capability: str
    label: str
    status: str
    finding: str
    run_id: str
    signal: str = "unknown"


def _number(value: Any) -> bool:
    return type(value) in (int, float) and 0 <= value <= 10**12 and math.isfinite(value)


def _resource(value: Any, prefix: str) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"{prefix}-[0-9]{{4,12}}", value) is not None


def _guardrail_metadata(value: Any) -> bool:
    if not isinstance(value, dict) or "raiPostToolAnnotations" not in value:
        return False
    annotations = value["raiPostToolAnnotations"]
    if not isinstance(annotations, dict) or annotations.get("blocked") is not False:
        raise ValueError("Blocked or invalid retrieval annotations")
    return set(value) == {"raiPostToolAnnotations"}


def _retrieval_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    remainder = text.lstrip()
    values = []
    while remainder:
        value, end = decoder.raw_decode(remainder)
        if not _guardrail_metadata(value):
            values.append(value)
        remainder = remainder[end:].strip()
    if not values:
        raise ValueError("Missing retrieval documents")
    documents = values[0]
    references: set[int] = set()
    for reference in values[1:]:
        if not isinstance(reference, dict):
            raise ValueError("Invalid retrieval reference")
        if reference != {"structuredResponse": documents}:
            if not isinstance(documents, list) or not all(
                isinstance(document, dict) and type(document.get("ref_id")) is int for document in documents
            ):
                raise ValueError("Missing retrieval reference IDs")
            ref_id = reference.get("ref_id")
            source = reference.get("sourceData")
            if (reference.get("kind") != "reference" or reference.get("mimeType") != "application/json"
                    or type(ref_id) is not int or ref_id in references
                    or ref_id not in {document["ref_id"] for document in documents}
                    or not isinstance(source, dict) or not isinstance(source.get("title"), str)
                    or not isinstance(source.get("content"), str) or reference.get("isError")):
                raise ValueError("Unsupported retrieval reference")
            references.add(ref_id)
    return documents


def _retrieval_count(content: Any) -> int | None:
    for _ in range(12):
        if isinstance(content, str):
            try:
                content = _retrieval_json(content)
            except ValueError:
                return None
        elif isinstance(content, dict):
            if content.get("isError") or _guardrail_metadata(content):
                return None
            structured = content.get("structuredContent")
            if structured is not None and not _guardrail_metadata(structured):
                content = content["structuredContent"]
            elif "response" in content:
                content = content["response"]
            else:
                content = content.get("content")
        elif isinstance(content, list):
            if not content:
                return 0
            if all(isinstance(item, dict) and isinstance(item.get("title"), str)
                   and isinstance(item.get("content"), str) for item in content):
                return len(content)
            if all(isinstance(item, dict) and item.get("type") == "text"
                   and isinstance(item.get("text"), str) for item in content):
                content = "\n".join(item["text"] for item in content)
            elif len(content) == 1 and isinstance(content[0], dict) and isinstance(content[0].get("content"), list):
                content = content[0]["content"]
            else:
                return None
        else:
            return None
    return None


def _findings(name: str, result: dict[str, Any] | None, raw: Any) -> list[tuple[str, str, str, str]]:
    if name == "knowledge_base_retrieve":
        count = _retrieval_count(raw)
        if count is None:
            raise ValueError("Unsupported retrieval result")
        return [("runbooks", "Runbooks", f"Retrieval completed; {count} documents returned", "observed")]
    if result is None or result.get("isError"):
        raise ValueError("Unsupported diagnostic result")
    if name == "identify_customer":
        if type(result.get("found")) is not bool:
            raise ValueError("Missing lookup outcome")
        if result["found"]:
            customer = result.get("customer")
            if not isinstance(customer, dict) or not _resource(customer.get("customer_id"), "CUST"):
                raise ValueError("Missing customer result")
        return [(
            "customer", "Customer", "Customer identified" if result["found"] else "Customer not identified",
            "observed" if result["found"] else "attention",
        )]
    if name == "get_line_status":
        line = result.get("line")
        if result.get("found") is not True or not isinstance(line, dict):
            raise ValueError("No line result")
        identifier, sync = line.get("line_id"), line.get("sync")
        measured, profile = line.get("measured_mbps"), line.get("profile_mbps")
        if (not _resource(identifier, "LINE") or sync not in ("up", "down", "unstable")
                or not isinstance(measured, dict) or not isinstance(profile, dict)
                or not _number(measured.get("down")) or not _number(profile.get("down"))):
            raise ValueError("Invalid line evidence")
        finding = f"Sync {sync}; download {measured['down']}/{profile['down']} Mbps"
        for key, label in (("errors_24h", "errors"), ("resyncs_24h", "resyncs")):
            if key in line:
                if not _number(line[key]):
                    raise ValueError("Invalid line counters")
                finding += f"; {line[key]} {label}/24h"
        return [(f"line:{identifier}", f"Line {identifier}", finding, "observed" if sync == "up" else "attention")]
    if name == "get_known_incidents":
        incidents = result.get("incidents")
        if not isinstance(incidents, list) or not all(
            isinstance(item, dict) and item.get("status") in ("open", "resolved") for item in incidents
        ):
            raise ValueError("Invalid incident evidence")
        count = sum(item["status"] == "open" for item in incidents)
        return [("incidents", "Area incidents", f"{count} open incidents returned", "attention" if count else "observed")]
    if name == "check_equipment":
        equipment = result.get("equipment")
        if result.get("found") is not True or not isinstance(equipment, list) or not equipment:
            raise ValueError("No equipment evidence")
        findings = []
        for item in equipment:
            if not isinstance(item, dict):
                raise ValueError("Invalid equipment")
            identifier, reachable, loss = item.get("equipment_id"), item.get("reachable"), item.get("packet_loss_pct")
            if (not _resource(identifier, "EQ") or type(reachable) is not bool
                    or not _number(loss) or loss > 100):
                raise ValueError("Invalid equipment evidence")
            findings.append((
                f"equipment:{identifier}", f"Modem {identifier}",
                f"{'Responding' if reachable else 'Not responding'}; packet loss {loss}%",
                "observed" if reachable and loss == 0 else "attention",
            ))
        return findings
    raise ValueError("Unsupported capability")


@dataclass
class EvidencePanel:
    rows: dict[str, EvidenceRow] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    phase: str = "idle"
    trace: list[dict[str, str]] = field(default_factory=list)
    trace_seen: set[tuple[str, str, str]] = field(default_factory=set)

    def record(self, row: EvidenceRow, event: str, call_id: str) -> None:
        self.record_event(row.capability, row.run_id, event, call_id)

    def record_event(self, capability: str, run_id: str, event: str, call_id: str) -> bool:
        key = (run_id, call_id, event)
        if key in self.trace_seen:
            return False
        self.trace_seen.add(key)
        self.trace.append({
            "capability": capability, "run_id": run_id, "event": event,
            "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "call_id": call_id,
        })
        return True

    def trace_snapshot(self, run_id: str) -> list[dict[str, str]]:
        return [event.copy() for event in self.trace if event["run_id"] == run_id]

    def begin(self) -> None:
        self.phase = "running"

    def snapshot(self) -> list[dict[str, Any]]:
        return [asdict(row) for row in self.rows.values()]

    def start(self, call_id: str, name: str, run_id: str) -> bool:
        if name not in CAPABILITIES or call_id in self.seen:
            return False
        self.seen.add(call_id)
        for row in self.rows.values():
            if row.capability == name and row.status == "completed" and row.run_id != run_id:
                row.status = "previous"
        self.rows[f"pending:{call_id}"] = EvidenceRow(
            f"pending:{call_id}", name, CAPABILITIES[name], "checking", "", run_id,
        )
        self.record(self.rows[f"pending:{call_id}"], "started", call_id)
        return True

    def result(self, call_id: str, result: dict[str, Any] | None, raw: Any) -> bool:
        key = f"pending:{call_id}"
        row = self.rows.get(key)
        if row is None or row.status != "checking":
            return False
        try:
            findings = _findings(row.capability, result, raw)
        except ValueError:
            row.status, row.finding = "failed", "No usable result returned"
        else:
            del self.rows[key]
            for resource, label, finding, signal in findings:
                self.rows[resource] = EvidenceRow(
                    resource, row.capability, label, "completed", finding, row.run_id, signal,
                )
        self.record(row, "failed" if row.status == "failed" else "completed", call_id)
        return True

    def finish(self, run_id: str, phase: str = "finished") -> bool:
        changed = self.phase != phase
        self.phase = phase
        for row in self.rows.values():
            if row.run_id == run_id and row.status == "checking":
                row.status, row.finding = "incomplete", "Run ended without a usable result"
                self.record(row, "incomplete", row.key.removeprefix("pending:"))
                changed = True
        return changed
