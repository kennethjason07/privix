"""Comprehensive Unit and Adversarial Tests for Privix Privacy Gateway."""

import time
import unittest
from unittest.mock import AsyncMock, MagicMock

from privix_app.audit import PrivixAuditLogger
from privix_app.detector import SensitiveDataDetector
from privix_app.gateway import PrivixPrivacyGateway
from privix_app.policy import ActionContext, PolicyDecision, PrivixPolicyEngine


class TestSensitiveDataDetector(unittest.TestCase):
    def setUp(self):
        self.detector = SensitiveDataDetector()

    def test_detect_and_redact_ssn(self):
        text = "Patient SSN is 987-65-4321 in records."
        findings = self.detector.find_all(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, "SSN")
        redacted = self.detector.redact(text)
        self.assertEqual(redacted, "Patient SSN is ***-**-4321 in records.")

    def test_detect_and_redact_credit_card(self):
        text = "Billing card: 4111 2222 3333 4444 on file."
        findings = self.detector.find_all(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, "PAYMENT_CARD")
        redacted = self.detector.redact(text)
        self.assertIn("****-****-****-4444", redacted)

    def test_detect_and_redact_email_and_phone(self):
        text = "Contact alice@example.com or call 555-123-4567."
        findings = self.detector.find_all(text)
        categories = {f.category for f in findings}
        self.assertIn("EMAIL", categories)
        self.assertIn("PHONE", categories)
        redacted = self.detector.redact(text)
        self.assertNotIn("alice@example.com", redacted)
        self.assertIn("al***@example.com", redacted)

    def test_healthcare_keyword_detection(self):
        text = "Diagnosis: Condition-Alpha active."
        findings = self.detector.find_all(text)
        self.assertTrue(any(f.category == "HEALTHCARE" for f in findings))

    def test_false_positive_standard_text(self):
        text = "Order #123456 completed with 5 items for $120.00."
        findings = self.detector.find_all(text)
        self.assertEqual(len(findings), 0)


class TestPrivixPolicyEngine(unittest.TestCase):
    def setUp(self):
        self.engine = PrivixPolicyEngine()
        self.audit = PrivixAuditLogger()

    def test_public_navigation_allowed(self):
        ctx = ActionContext(
            action_type="navigate",
            params={"url": "https://example.com"},
            destination="https://example.com",
        )
        result = self.engine.evaluate(ctx)
        self.assertEqual(result.decision, PolicyDecision.ALLOW)

    def test_restricted_scheme_blocked(self):
        ctx = ActionContext(
            action_type="navigate",
            params={"url": "javascript:window.location='http://attacker.com'"},
            destination="javascript:...",
        )
        result = self.engine.evaluate(ctx)
        self.assertEqual(result.decision, PolicyDecision.BLOCK)

    def test_form_submission_requires_approval(self):
        # Test browser-use 'click' action on a submit button
        ctx = ActionContext(
            action_type="click",
            params={"index": 3},
            destination="http://127.0.0.1:8765/portal.html",
            element_info={
                "node_name": "button",
                "attributes": {"type": "submit", "id": "btn-submit-sensitive"},
                "text": "Submit Sensitive Form",
            },
        )
        result = self.engine.evaluate(ctx)
        self.assertEqual(result.decision, PolicyDecision.REQUIRE_APPROVAL)
        self.assertIn("Form submission requires approval", result.reason)

    def test_sensitive_info_blocked_from_external_transmission(self):
        # Test browser-use 'input' action
        ctx = ActionContext(
            action_type="input",
            params={"index": 1, "text": "Patient Jane Doe SSN: 987-65-4321"},
            destination="https://untrusted-analytics.example/collector",
        )
        result = self.engine.evaluate(ctx)
        self.assertEqual(result.decision, PolicyDecision.BLOCK)
        self.assertIn("Blocked transmission of sensitive information", result.reason)

    def test_destructive_actions_require_approval(self):
        ctx = ActionContext(
            action_type="click",
            params={"index": 5},
            destination="https://example.com/admin",
            element_info={
                "node_name": "button",
                "attributes": {"id": "btn-delete-patient"},
                "text": "Delete Record",
            },
        )
        result = self.engine.evaluate(ctx)
        self.assertEqual(result.decision, PolicyDecision.REQUIRE_APPROVAL)

    def test_script_execution_requires_approval(self):
        # 'evaluate' action in browser-use
        ctx = ActionContext(
            action_type="evaluate",
            params={"code": "document.cookie"},
            destination="https://example.com",
        )
        result = self.engine.evaluate(ctx)
        self.assertEqual(result.decision, PolicyDecision.REQUIRE_APPROVAL)
        self.assertEqual(result.risk_level, "CRITICAL")

    def test_file_upload_requires_approval(self):
        # 'upload_file' action in browser-use
        ctx = ActionContext(
            action_type="upload_file",
            params={"index": 2, "path": "C:\\secret.pdf"},
            destination="https://example.com",
        )
        result = self.engine.evaluate(ctx)
        self.assertEqual(result.decision, PolicyDecision.REQUIRE_APPROVAL)
        self.assertEqual(result.risk_level, "HIGH")

    def test_unknown_action_default_deny(self):
        ctx = ActionContext(
            action_type="unknown_custom_plugin_action",
            params={"foo": "bar"},
            destination="https://example.com",
        )
        result = self.engine.evaluate(ctx)
        self.assertEqual(result.decision, PolicyDecision.BLOCK)
        self.assertIn("blocked by default-deny policy", result.reason)


class TestSecureActionBinding(unittest.TestCase):
    def setUp(self):
        self.engine = PrivixPolicyEngine()

    def test_action_binding_valid_token(self):
        ctx = ActionContext(
            action_type="click",
            params={"index": 1},
            destination="http://127.0.0.1:8765/portal.html",
        )
        token = self.engine.issue_approval_token(ctx)
        valid, msg = self.engine.verify_approval_token(token.token_id, ctx)
        self.assertTrue(valid)
        self.assertEqual(msg, "Valid")

    def test_action_binding_invalidated_if_parameter_modified(self):
        ctx = ActionContext(
            action_type="click",
            params={"index": 1},
            destination="http://127.0.0.1:8765/portal.html",
        )
        token = self.engine.issue_approval_token(ctx)

        # Adversarial tamper: index changed from 1 to 2 after approval
        tampered_ctx = ActionContext(
            action_type="click",
            params={"index": 2},
            destination="http://127.0.0.1:8765/portal.html",
        )
        valid, msg = self.engine.verify_approval_token(token.token_id, tampered_ctx)
        self.assertFalse(valid)
        self.assertIn("changed after approval", msg)

    def test_action_binding_invalidated_if_destination_modified(self):
        ctx = ActionContext(
            action_type="click",
            params={"index": 1},
            destination="http://127.0.0.1:8765/portal.html",
        )
        token = self.engine.issue_approval_token(ctx)

        # Adversarial tamper: destination changed to external attacker server
        tampered_ctx = ActionContext(
            action_type="click",
            params={"index": 1},
            destination="http://attacker.com/steal",
        )
        valid, msg = self.engine.verify_approval_token(token.token_id, tampered_ctx)
        self.assertFalse(valid)

    def test_action_binding_expired(self):
        ctx = ActionContext(
            action_type="click",
            params={"index": 1},
            destination="http://127.0.0.1:8765/portal.html",
        )
        # Issue token with negative TTL
        token = self.engine.issue_approval_token(ctx, ttl_seconds=-1.0)
        valid, msg = self.engine.verify_approval_token(token.token_id, ctx)
        self.assertFalse(valid)
        self.assertIn("expired", msg)


class TestAuditLoggerRedaction(unittest.TestCase):
    def test_audit_log_redacts_sensitive_values(self):
        audit = PrivixAuditLogger()
        audit.log_decision(
            action_type="input",
            decision="BLOCK",
            reason="Detected SSN 987-65-4321 in payload",
            destination="http://127.0.0.1:8765",
            risk_level="HIGH",
        )
        entries = audit.get_entries()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertNotIn("987-65-4321", entry.reason)
        self.assertIn("***-**-4321", entry.reason)
        self.assertTrue(entry.redacted)


class TestPrivixGatewayInterception(unittest.IsolatedAsyncioTestCase):
    async def test_no_protected_action_executes_before_approval(self):
        engine = PrivixPolicyEngine()
        audit = PrivixAuditLogger()
        approval_mock = MagicMock(return_value=False)

        gateway = PrivixPrivacyGateway(
            policy_engine=engine,
            audit_logger=audit,
            approval_handler=approval_mock,
        )

        mock_action = MagicMock()
        mock_action.model_dump.return_value = {"click": {"index": 1}}

        mock_session = MagicMock()
        mock_session.get_current_page_url = AsyncMock(return_value="http://127.0.0.1:8765/portal.html")
        mock_node = MagicMock()
        mock_node.node_name = "button"
        mock_node.attributes = {"type": "submit"}
        mock_node.node_value = "Submit Sensitive Form"

        mock_summary = MagicMock()
        mock_summary.dom_state.selector_map = {1: mock_node}
        mock_session._cached_browser_state_summary = mock_summary

        result = await gateway.act(action=mock_action, browser_session=mock_session)

        approval_mock.assert_called_once()
        self.assertIn("PRIVIX APPROVAL DENIED", result.error)
        entries = audit.get_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].decision, PolicyDecision.REQUIRE_APPROVAL.value)
        self.assertEqual(entries[0].approved, False)


if __name__ == "__main__":
    unittest.main()
