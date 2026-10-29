"""Privix Policy Engine for deterministic evaluation and secure action binding."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import time
from typing import Any

from privix_app.detector import SensitiveDataDetector


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


@dataclass
class ActionContext:
    action_type: str
    params: dict[str, Any] = field(default_factory=dict)
    destination: str | None = None
    element_info: dict[str, Any] | None = None
    page_url: str | None = None

    def compute_payload_hash(self) -> str:
        """Calculate canonical SHA-256 hash for secure action binding."""
        canonical_obj = {
            "action_type": self.action_type.lower(),
            "destination": (self.destination or "").strip().lower(),
            "params": self.params,
            "element_info": self.element_info or {},
        }
        raw_bytes = json.dumps(canonical_obj, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw_bytes).hexdigest()


@dataclass
class PolicyResult:
    decision: PolicyDecision
    reason: str
    risk_level: str = "LOW"  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    action_hash: str = ""


@dataclass
class ApprovalToken:
    token_id: str
    action_type: str
    destination: str
    payload_hash: str
    granted_at: float
    expires_at: float

    def is_valid_for(self, ctx: ActionContext) -> tuple[bool, str]:
        """Validate that the token exactly matches the reviewed action and hasn't expired."""
        now = time.time()
        if now > self.expires_at:
            return False, "Approval token has expired"

        expected_hash = ctx.compute_payload_hash()
        if self.payload_hash != expected_hash:
            return False, "Action target, parameters, or element state changed after approval (tampering detected)"

        if self.action_type != ctx.action_type.lower():
            return False, f"Action type mismatch: approved '{self.action_type}', attempted '{ctx.action_type}'"

        return True, "Valid"


class PrivixPolicyEngine:
    """
    Deterministic privacy and security policy engine.
    Comprehensively evaluates all browser-use actions before execution.
    """

    # Comprehensive set of recognized browser-use tool actions
    SAFE_READ_ACTIONS = {
        "done",
        "extract",
        "search_page",
        "find_elements",
        "scroll",
        "find_text",
        "screenshot",
        "save_as_pdf",
        "dropdown_options",
        "select_dropdown",
        "switch",
        "close",
        "wait",
    }

    def __init__(
        self,
        allowed_domains: list[str] | None = None,
        detector: SensitiveDataDetector | None = None,
    ):
        self.allowed_domains = allowed_domains or ["example.com", "127.0.0.1", "localhost"]
        self.detector = detector or SensitiveDataDetector()
        self._active_tokens: dict[str, ApprovalToken] = {}

    def issue_approval_token(self, ctx: ActionContext, ttl_seconds: float = 60.0) -> ApprovalToken:
        """Issue a cryptographically bound approval token for an exact action state."""
        action_hash = ctx.compute_payload_hash()
        token_id = f"tok_{action_hash[:16]}_{int(time.time())}"
        now = time.time()
        token = ApprovalToken(
            token_id=token_id,
            action_type=ctx.action_type.lower(),
            destination=(ctx.destination or "").strip().lower(),
            payload_hash=action_hash,
            granted_at=now,
            expires_at=now + ttl_seconds,
        )
        self._active_tokens[token_id] = token
        return token

    def verify_approval_token(self, token_id: str, ctx: ActionContext) -> tuple[bool, str]:
        """Verify whether an action is authorized by an unexpired, intact approval token."""
        token = self._active_tokens.get(token_id)
        if not token:
            return False, "No active approval token found"
        valid, msg = token.is_valid_for(ctx)
        if valid:
            # Token is single-use; consume it immediately
            del self._active_tokens[token_id]
        return valid, msg

    def _is_form_submission(self, ctx: ActionContext) -> tuple[bool, str]:
        """Determine if action submits form data."""
        if ctx.action_type.lower() in ("submit_form", "submit"):
            return True, "Explicit form submission action"

        elem = ctx.element_info or {}
        tag = str(elem.get("node_name") or elem.get("tag_name") or "").lower()
        attrs = elem.get("attributes") or {}
        elem_type = str(attrs.get("type", "")).lower()
        elem_id = str(attrs.get("id", "")).lower()
        elem_name = str(attrs.get("name", "")).lower()
        elem_text = str(elem.get("text", "")).lower()

        if tag in ("button", "input") and elem_type == "submit":
            return True, f"Clicking form submit element ({tag}[type=submit])"
        if any(term in elem_id for term in ("submit", "btn-submit")) or any(term in elem_name for term in ("submit",)):
            return True, f"Clicking submission trigger (id='{elem_id}')"
        if "submit" in elem_text:
            return True, f"Clicking element with submission text: '{elem_text}'"

        return False, ""

    def _is_destructive(self, ctx: ActionContext) -> tuple[bool, str]:
        """Check for potentially destructive actions."""
        elem = ctx.element_info or {}
        elem_text = str(elem.get("text", "")).lower()
        attrs = elem.get("attributes") or {}
        elem_id = str(attrs.get("id", "")).lower()

        destructive_terms = ("delete", "destroy", "remove", "drop", "purge", "erase", "terminate")
        for term in destructive_terms:
            if term in elem_text or term in elem_id:
                return True, f"Destructive element interaction matching '{term}'"

        typed = str(ctx.params.get("text", "")).lower()
        for term in ("drop table", "rm -rf", "delete from"):
            if term in typed:
                return True, f"Destructive command in payload: '{term}'"

        return False, ""

    def evaluate(self, ctx: ActionContext) -> PolicyResult:
        """
        Evaluate proposed action against deterministic security and privacy rules.
        Fails closed on any unexpected error or unclassified action.
        """
        try:
            action = ctx.action_type.lower().strip()
            action_hash = ctx.compute_payload_hash()

            # 1. Critical-Risk: Arbitrary In-Browser Script Execution
            if action == "evaluate":
                code = ctx.params.get("code", "")
                return PolicyResult(
                    decision=PolicyDecision.REQUIRE_APPROVAL,
                    reason=f"Script execution requires approval: '{code[:50]}...'",
                    risk_level="CRITICAL",
                    action_hash=action_hash,
                )

            # 2. High-Risk: File System Operations
            if action in ("upload_file", "write_file", "replace_file"):
                path = ctx.params.get("path") or ctx.params.get("file_name") or "unknown"
                return PolicyResult(
                    decision=PolicyDecision.REQUIRE_APPROVAL,
                    reason=f"File system write/upload '{action}' requires approval for path: {path}",
                    risk_level="HIGH",
                    action_hash=action_hash,
                )

            if action == "read_file":
                path = ctx.params.get("file_name", "")
                return PolicyResult(
                    decision=PolicyDecision.REQUIRE_APPROVAL,
                    reason=f"Reading local file requires explicit approval: {path}",
                    risk_level="HIGH",
                    action_hash=action_hash,
                )

            # 3. Navigation Actions
            if action in ("navigate", "go_back", "search"):
                target_url = ctx.params.get("url") or ctx.params.get("query") or ctx.destination or ""
                target_str = str(target_url).strip()

                if target_str.startswith("http://") or target_str.startswith("https://"):
                    return PolicyResult(
                        decision=PolicyDecision.ALLOW,
                        reason=f"Standard web navigation allowed: {target_str}",
                        risk_level="LOW",
                        action_hash=action_hash,
                    )
                if target_str.startswith("about:blank") or target_str == "" or action == "go_back":
                    return PolicyResult(
                        decision=PolicyDecision.ALLOW,
                        reason=f"Internal browser navigation allowed ('{action}')",
                        risk_level="LOW",
                        action_hash=action_hash,
                    )
                if action == "search":
                    return PolicyResult(
                        decision=PolicyDecision.ALLOW,
                        reason=f"Search navigation allowed: '{target_str}'",
                        risk_level="LOW",
                        action_hash=action_hash,
                    )

                return PolicyResult(
                    decision=PolicyDecision.BLOCK,
                    reason=f"Restricted navigation destination or scheme: {target_str}",
                    risk_level="HIGH",
                    action_hash=action_hash,
                )

            # 4. Read-Only / Safe Utility Actions
            if action in self.SAFE_READ_ACTIONS:
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason=f"Safe operational action '{action}' allowed",
                    risk_level="LOW",
                    action_hash=action_hash,
                )

            # 5. Form Submission (Must require explicit authorization)
            is_sub, sub_reason = self._is_form_submission(ctx)
            if is_sub:
                return PolicyResult(
                    decision=PolicyDecision.REQUIRE_APPROVAL,
                    reason=f"Form submission requires approval: {sub_reason}",
                    risk_level="MEDIUM",
                    action_hash=action_hash,
                )

            # 6. Destructive Actions (Must require explicit authorization)
            is_destr, destr_reason = self._is_destructive(ctx)
            if is_destr:
                return PolicyResult(
                    decision=PolicyDecision.REQUIRE_APPROVAL,
                    reason=f"Destructive action requires approval: {destr_reason}",
                    risk_level="HIGH",
                    action_hash=action_hash,
                )

            # 7. Text Input Actions: Inspect for Sensitive Data Leakage
            if action in ("input", "send_keys", "type_text"):
                payload_text = str(ctx.params.get("text", "") or ctx.params.get("keys", ""))
                findings = self.detector.find_all(payload_text)
                if findings:
                    categories = ", ".join(sorted(set(f.category for f in findings)))
                    return PolicyResult(
                        decision=PolicyDecision.BLOCK,
                        reason=f"Blocked transmission of sensitive information ({categories})",
                        risk_level="HIGH",
                        action_hash=action_hash,
                    )
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason="Standard non-sensitive text entry allowed",
                    risk_level="LOW",
                    action_hash=action_hash,
                )

            # 8. Standard Click Interaction (on non-submit, non-destructive elements)
            if action in ("click", "click_element", "click_coordinate"):
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason="Standard element click interaction allowed",
                    risk_level="LOW",
                    action_hash=action_hash,
                )

            # 9. Default to Deny (Fail-Closed) for any unknown or unclassified action
            return PolicyResult(
                decision=PolicyDecision.BLOCK,
                reason=f"Unrecognized action '{action}' blocked by default-deny policy",
                risk_level="HIGH",
                action_hash=action_hash,
            )

        except Exception as exc:
            # Strict Fail-Closed guarantee
            return PolicyResult(
                decision=PolicyDecision.BLOCK,
                reason=f"Policy evaluation failure: {exc}. Defaulting to DENY.",
                risk_level="CRITICAL",
                action_hash="",
            )
