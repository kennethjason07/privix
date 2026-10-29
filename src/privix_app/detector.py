"""Modular Sensitive Data Detector, Redactor, and Masking Engine.

Handles synthetic test data:
- Identity Numbers (SSN)
- Payment Cards
- Email Addresses
- Phone Numbers
- Passwords & Credentials
- Healthcare / Diagnostic Records
"""

from dataclasses import dataclass
import re
from typing import Any


@dataclass
class SensitiveFinding:
    category: str
    match_text: str
    redacted_text: str
    start: int
    end: int
    confidence: float


# Regular expressions for synthetic detection
RE_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
RE_CREDIT_CARD = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")
RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
RE_PHONE = re.compile(r"\b(?:\+?1[-. ]?)?\(?[2-9]\d{2}\)?[-. ]?\d{3}[-. ]?\d{4}\b")
RE_API_KEY = re.compile(r"\b(?:sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{20,}|Bearer\s+[a-zA-Z0-9._\-]+)\b")

HEALTHCARE_KEYWORDS = [
    "diagnostic alpha",
    "condition-alpha",
    "patient record",
    "medical history",
    "prescription",
    "medication-x",
]

CREDENTIAL_KEYWORDS = [
    "password",
    "secret",
    "private_key",
    "auth_token",
]


class SensitiveDataDetector:
    """Detects, classifies, and redacts sensitive data in text and action payloads."""

    def __init__(self, custom_keywords: list[str] | None = None):
        self.custom_keywords = [k.lower() for k in (custom_keywords or [])]

    def find_all(self, text: str) -> list[SensitiveFinding]:
        """Detect all sensitive data patterns in text."""
        if not text or not isinstance(text, str):
            return []

        findings: list[SensitiveFinding] = []

        # 1. SSN
        for m in RE_SSN.finditer(text):
            val = m.group()
            masked = f"***-**-{val[-4:]}"
            findings.append(SensitiveFinding("SSN", val, masked, m.start(), m.end(), 0.95))

        # 2. Credit Card
        for m in RE_CREDIT_CARD.finditer(text):
            val = m.group()
            # Clean non-digits
            digits = re.sub(r"\D", "", val)
            if len(digits) == 16:
                masked = f"****-****-****-{digits[-4:]}"
                findings.append(SensitiveFinding("PAYMENT_CARD", val, masked, m.start(), m.end(), 0.95))

        # 3. Email
        for m in RE_EMAIL.finditer(text):
            val = m.group()
            parts = val.split("@")
            masked = f"{parts[0][:2]}***@{parts[1]}"
            findings.append(SensitiveFinding("EMAIL", val, masked, m.start(), m.end(), 0.90))

        # 4. Phone
        for m in RE_PHONE.finditer(text):
            val = m.group()
            masked = "***-***-" + val[-4:]
            findings.append(SensitiveFinding("PHONE", val, masked, m.start(), m.end(), 0.85))

        # 5. API Keys / Bearer Tokens
        for m in RE_API_KEY.finditer(text):
            val = m.group()
            masked = "[REDACTED_API_KEY]"
            findings.append(SensitiveFinding("CREDENTIAL", val, masked, m.start(), m.end(), 0.99))

        # 6. Healthcare & Diagnostics Keywords
        lower_text = text.lower()
        for kw in HEALTHCARE_KEYWORDS + self.custom_keywords:
            start = 0
            while True:
                idx = lower_text.find(kw, start)
                if idx == -1:
                    break
                val = text[idx : idx + len(kw)]
                masked = "[REDACTED_MEDICAL_DATA]"
                findings.append(SensitiveFinding("HEALTHCARE", val, masked, idx, idx + len(kw), 0.85))
                start = idx + len(kw)

        # 7. Credential Keywords followed by values
        for kw in CREDENTIAL_KEYWORDS:
            pattern = re.compile(rf"\b{kw}\s*[:=]\s*(\S+)", re.IGNORECASE)
            for m in pattern.finditer(text):
                val = m.group(1)
                masked = "[REDACTED_SECRET]"
                findings.append(SensitiveFinding("CREDENTIAL", val, masked, m.start(1), m.end(1), 0.90))

        # Sort findings by start position
        findings.sort(key=lambda f: f.start)
        return findings

    def contains_sensitive(self, text: str) -> bool:
        """Returns True if any sensitive finding is detected."""
        return len(self.find_all(text)) > 0

    def redact(self, text: str) -> str:
        """Replace all detected sensitive patterns with masked equivalents."""
        if not text or not isinstance(text, str):
            return ""

        findings = self.find_all(text)
        if not findings:
            return text

        # Sort reverse so index offsets stay valid during replacement
        findings = sorted(findings, key=lambda f: f.start, reverse=True)
        redacted = text
        for f in findings:
            redacted = redacted[: f.start] + f.redacted_text + redacted[f.end :]

        return redacted

    def sanitize_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Recursively redact dictionary values for safe audit logging."""
        sanitized = {}
        for k, v in data.items():
            if isinstance(v, str):
                sanitized[k] = self.redact(v)
            elif isinstance(v, dict):
                sanitized[k] = self.sanitize_dict(v)
            elif isinstance(v, list):
                sanitized[k] = [
                    self.redact(item) if isinstance(item, str) else item
                    for item in v
                ]
            else:
                sanitized[k] = v
        return sanitized

