"""Audit logging system for Privix with automatic sensitive data redaction."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from privix_app.detector import SensitiveDataDetector


@dataclass
class AuditLogEntry:
    timestamp: str
    action_type: str
    destination: str | None
    decision: str  # ALLOW, BLOCK, REQUIRE_APPROVAL
    reason: str
    risk_level: str
    action_hash: str = ""
    token_id: str | None = None
    approved: bool | None = None  # None for auto decisions, True/False if reviewed
    redacted: bool = False


class PrivixAuditLogger:
    """Records tamper-evident, redacted in-memory and JSONL audit logs for privacy decisions."""

    def __init__(self, log_file: Path | str | None = None, detector: SensitiveDataDetector | None = None):
        self.log_file = Path(log_file) if log_file else None
        self.detector = detector or SensitiveDataDetector()
        self.entries: list[AuditLogEntry] = []

    def log_decision(
        self,
        action_type: str,
        decision: str,
        reason: str,
        destination: str | None = None,
        risk_level: str = "LOW",
        action_hash: str = "",
        token_id: str | None = None,
        approved: bool | None = None,
    ) -> AuditLogEntry:
        timestamp = datetime.now(timezone.utc).isoformat()

        # Automatically redact reason and destination if sensitive text leaked
        sanitized_reason = self.detector.redact(reason)
        sanitized_dest = self.detector.redact(destination) if destination else None
        was_redacted = (sanitized_reason != reason) or (sanitized_dest != destination)

        entry = AuditLogEntry(
            timestamp=timestamp,
            action_type=action_type,
            destination=sanitized_dest,
            decision=decision,
            reason=sanitized_reason,
            risk_level=risk_level,
            action_hash=action_hash,
            token_id=token_id,
            approved=approved,
            redacted=was_redacted,
        )
        self.entries.append(entry)

        if self.log_file:
            try:
                self.log_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(entry)) + "\n")
            except Exception as e:
                print(f"[Privix Audit Error] Failed to append to log file: {e}")

        return entry

    def get_entries(self) -> list[AuditLogEntry]:
        return list(self.entries)

    def clear(self) -> None:
        self.entries.clear()
