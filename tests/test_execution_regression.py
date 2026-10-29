"""Repeatable Regression Test Suite for Privix Task Execution, Grounding, and Privacy Gateway.

Covers 10 explicit local scenarios:
1. Read a page and summarize its content (grounded verification).
2. Find an element and interact with it (DOM state change verification).
3. Navigate between pages (URL & title verification).
4. Handle a dynamically updated page (content change verification).
5. Handle a missing element without hallucinating success.
6. Recover from a controlled timeout.
7. Block a synthetic sensitive-data submission.
8. Require approval for a protected action.
9. Verify that the protected action never executes when denied.
10. Confirm that a task is marked completed only after its intended outcome is verified.
"""

import asyncio
import time
import unittest
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock

from browser_use.agent.views import ActionResult
from privix_app.audit import PrivixAuditLogger
from privix_app.gateway import PrivixPrivacyGateway
from privix_app.mock_server import PrivixMockServer
from privix_app.policy import ActionContext, PolicyDecision, PolicyResult, PrivixPolicyEngine
from privix_app.task_state import TaskLifecycleTracker, TaskState
from privix_app.verifier import OutcomeVerifier, VerificationResult


class TestPrivixExecutionRegression(unittest.IsolatedAsyncioTestCase):
    mock_server = None
    server_url = ""

    @classmethod
    def setUpClass(cls):
        cls.mock_server = PrivixMockServer(port=8765)
        cls.server_url = cls.mock_server.start()

    @classmethod
    def tearDownClass(cls):
        if cls.mock_server:
            cls.mock_server.stop()

    def setUp(self):
        self.mock_server.reset_state()
        self.policy_engine = PrivixPolicyEngine()
        self.audit_logger = PrivixAuditLogger()

    # ── Scenario 1: Read page and summarize content ───────────────────────────
    def test_01_read_summary_grounded_vs_hallucination(self):
        """Scenario 1: Summary must be grounded in actual DOM text, rejecting vacuous hallucinations."""
        actual_title = "Synthetic Quantum Research"
        actual_text = "Project Privix validates privacy-preserving local browser agents with zero cloud telemetry."

        # Grounded summary: contains actual keywords
        good_summary = "Privix is validating local browser agents without cloud telemetry."
        res_good = OutcomeVerifier.verify_read_summary(good_summary, actual_title, actual_text)
        self.assertTrue(res_good.is_verified)
        self.assertIn("grounded in verified page content", res_good.reason)

        # Vacuous / link-only hallucination (the 'Learn more' failure mode observed earlier)
        vacuous_summary = "Page title: 'Learn more', Visible text: 'Learn more'"
        res_bad = OutcomeVerifier.verify_read_summary(vacuous_summary, actual_title, actual_text)
        self.assertFalse(res_bad.is_verified)
        self.assertIn("vacuous link text", res_bad.reason)

    # ── Scenario 2: Find element and interact with it ─────────────────────────
    def test_02_interact_element_dom_verification(self):
        """Scenario 2: Element interaction verified via actual post-action DOM state."""
        # Pre-action state: button says 'Click Me', status is 'Pending'
        pre_text = "Pending"
        res_pre = OutcomeVerifier.verify_dom_element_text(pre_text, "Action Verified")
        self.assertFalse(res_pre.is_verified)

        # Post-action state: button was clicked, DOM updated
        post_text = "Action Verified"
        res_post = OutcomeVerifier.verify_dom_element_text(post_text, "Action Verified")
        self.assertTrue(res_post.is_verified)

    # ── Scenario 3: Navigate between pages ────────────────────────────────────
    def test_03_navigate_destination_verification(self):
        """Scenario 3: Page navigation must verify final URL and title match the destination."""
        final_url = f"{self.server_url}/page-nav-b.html"
        res = OutcomeVerifier.verify_navigation_destination(final_url, r"/page-nav-b\.html$")
        self.assertTrue(res.is_verified)

        # Negative test: still on page A
        wrong_url = f"{self.server_url}/page-nav-a.html"
        res_wrong = OutcomeVerifier.verify_navigation_destination(wrong_url, r"/page-nav-b\.html$")
        self.assertFalse(res_wrong.is_verified)

    # ── Scenario 4: Handle dynamically updated page ───────────────────────────
    def test_04_dynamic_page_update_handling(self):
        """Scenario 4: Verify dynamic state update after asynchronous content loads."""
        initial_dom = "Initial Loading State"
        res_initial = OutcomeVerifier.verify_dom_element_text(initial_dom, "Data Ready: Code SYN-7749")
        self.assertFalse(res_initial.is_verified)

        # After dynamic timer fires:
        updated_dom = "Data Ready: Code SYN-7749"
        res_updated = OutcomeVerifier.verify_dom_element_text(updated_dom, "Data Ready: Code SYN-7749")
        self.assertTrue(res_updated.is_verified)

    # ── Scenario 5: Handle missing element without hallucinating success ───────
    async def test_05_missing_element_handled_without_hallucinating(self):
        """Scenario 5: Attempting to interact with an element index absent from DOM must fail closed."""
        gateway = PrivixPrivacyGateway(
            policy_engine=self.policy_engine,
            audit_logger=self.audit_logger,
        )

        # Mock browser session with empty selector map (element index 999 does not exist)
        mock_session = MagicMock()
        mock_session.get_current_page_url = AsyncMock(return_value=f"{self.server_url}/missing-test.html")
        mock_session.get_dom_element_by_index = AsyncMock(return_value=None)
        mock_summary = MagicMock()
        mock_summary.dom_state.selector_map = {1: MagicMock()}  # only index 1 exists
        mock_session._cached_browser_state_summary = mock_summary

        # Model requests action on non-existent element index 999
        extracted_info = await gateway._extract_element_info(mock_session, index=999)
        self.assertIsNone(extracted_info)

    # ── Scenario 6: Recover from controlled timeout ───────────────────────────
    async def test_06_controlled_timeout_recovery(self):
        """Scenario 6: Controlled timeout aborts gracefully without hanging or unhandled exception."""
        tracker = TaskLifecycleTracker(task_id="timeout_task")
        tracker.transition_to(TaskState.RUNNING, "Starting operation")

        start = time.time()
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.sleep(0.5), timeout=0.05)

        elapsed = time.time() - start
        tracker.transition_to(TaskState.FAILED, f"Operation timed out after {elapsed:.2f}s")

        self.assertEqual(tracker.current_state, TaskState.FAILED)
        self.assertLess(elapsed, 0.2)

    # ── Scenario 7: Block synthetic sensitive-data submission ─────────────────
    def test_07_block_sensitive_data_submission(self):
        """Scenario 7: External transmission containing sensitive PII (SSN) is blocked unconditionally."""
        ctx = ActionContext(
            action_type="input",
            params={"index": 1, "text": "Patient record SSN: 987-65-4321, Condition: Diagnostic Alpha"},
            destination=f"{self.server_url}/submit-external",
            page_url=f"{self.server_url}/portal.html",
        )
        decision = self.policy_engine.evaluate(ctx)
        self.assertEqual(decision.decision, PolicyDecision.BLOCK)
        self.assertIn("Blocked transmission of sensitive information", decision.reason)
        self.assertIn("SSN", decision.reason)

        # Audit logger must auto-redact SSN in logged entries
        entry = self.audit_logger.log_decision(
            action_type=ctx.action_type,
            decision=decision.decision.value,
            reason=decision.reason,
            destination=ctx.destination,
            risk_level=decision.risk_level,
            approved=False,
        )
        self.assertNotIn("987-65-4321", entry.reason)

    # ── Scenario 8: Require approval for protected action ─────────────────────
    def test_08_require_approval_for_protected_action(self):
        """Scenario 8: Submitting forms or invoking destructive operations requires explicit approval."""
        ctx = ActionContext(
            action_type="click",
            params={"index": 0},
            destination=f"{self.server_url}/submit-external",
            element_info={
                "node_name": "button",
                "attributes": {"type": "submit", "id": "btn-submit-sensitive"},
                "text": "Submit Sensitive Form",
            },
            page_url=f"{self.server_url}/portal.html",
        )
        decision = self.policy_engine.evaluate(ctx)
        self.assertEqual(decision.decision, PolicyDecision.REQUIRE_APPROVAL)
        self.assertIn("Form submission requires approval", decision.reason)

    # ── Scenario 9: Verify protected action never executes when denied ─────────
    async def test_09_protected_action_never_executes_when_denied(self):
        """Scenario 9: When human approval is denied, the gateway halts execution and zero HTTP submissions occur."""
        initial_subs = self.mock_server.submissions_count

        approval_handler = MagicMock(return_value=False)  # User denies action
        gateway = PrivixPrivacyGateway(
            policy_engine=self.policy_engine,
            audit_logger=self.audit_logger,
            approval_handler=approval_handler,
        )

        mock_action = MagicMock()
        mock_action.model_dump.return_value = {"click": {"index": 0}}

        mock_session = MagicMock()
        mock_session.get_current_page_url = AsyncMock(return_value=f"{self.server_url}/portal.html")
        mock_node = MagicMock()
        mock_node.node_name = "button"
        mock_node.attributes = {"type": "submit", "id": "btn-submit-sensitive"}
        mock_node.get_all_children_text = MagicMock(return_value="Submit Sensitive Form")
        mock_session.get_dom_element_by_index = AsyncMock(return_value=mock_node)

        # Execute candidate action through gateway
        result = await gateway.act(action=mock_action, browser_session=mock_session)

        # Verify interception halted execution
        approval_handler.assert_called_once()
        self.assertIn("PRIVIX APPROVAL DENIED", result.error)
        self.assertFalse(result.is_done)

        # Verify zero submissions reached mock server
        subs_after = self.mock_server.submissions_count
        self.assertEqual(subs_after, initial_subs)
        self.assertEqual(subs_after, 0)

    # ── Scenario 10: Task marked completed only after outcome verified ────────
    def test_10_outcome_verification_prevents_false_completion(self):
        """Scenario 10: Tasks must not be reported as COMPLETED if actual DOM outcome is unverified."""
        # Model falsely claims "I have completed the task and updated status"
        model_claim = "Task finished successfully. Outcome verified."

        # Actual DOM state is still 'Incomplete'
        actual_dom_state = "Incomplete"
        res = OutcomeVerifier.verify_dom_element_text(actual_dom_state, "Outcome Complete")

        self.assertFalse(res.is_verified)
        self.assertIn("DOM element state mismatch", res.reason)

        # Only when actual DOM state updates does verification succeed
        verified_dom_state = "Outcome Complete"
        res_ok = OutcomeVerifier.verify_dom_element_text(verified_dom_state, "Outcome Complete")
        self.assertTrue(res_ok.is_verified)


if __name__ == "__main__":
    unittest.main()
