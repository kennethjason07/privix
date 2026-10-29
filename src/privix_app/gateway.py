"""Privix Privacy Gateway: Mediates and intercepts browser-use actions before execution."""

import asyncio
from collections.abc import Callable
import sys
from typing import Any

from browser_use.agent.views import ActionResult
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import Tools

from privix_app.audit import PrivixAuditLogger
from privix_app.policy import ActionContext, PolicyDecision, PolicyResult, PrivixPolicyEngine

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class PrivixPrivacyGateway(Tools):
    """
    Subclasses browser-use Tools to enforce deterministic privacy policies
    and cryptographically bound human-in-the-loop approvals before any browser action reaches the DOM or network.
    """

    def __init__(
        self,
        policy_engine: PrivixPolicyEngine | None = None,
        audit_logger: PrivixAuditLogger | None = None,
        approval_handler: Callable[[ActionContext, PolicyResult], bool] | None = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.policy_engine = policy_engine or PrivixPolicyEngine()
        self.audit_logger = audit_logger or PrivixAuditLogger()
        self.approval_handler = approval_handler or self._default_cli_approval_handler

    @staticmethod
    def _default_cli_approval_handler(ctx: ActionContext, result: PolicyResult) -> bool:
        """Default interactive CLI prompt for human approval."""
        print("\n" + "=" * 65)
        print("[PRIVIX PRIVACY GATEWAY] APPROVAL REQUIRED")
        print("=" * 65)
        print(f"Action Type : {ctx.action_type}")
        print(f"Destination : {ctx.destination or ctx.page_url}")
        print(f"Parameters  : {ctx.params}")
        print(f"Reason      : {result.reason}")
        print(f"Risk Level  : {result.risk_level}")
        print("=" * 65)
        try:
            choice = input("Authorize this browser action? [y/N]: ").strip().lower()
            return choice in ("y", "yes")
        except EOFError:
            print("Non-interactive session detected: Defaulting to DENY.")
            return False

    async def _extract_element_info(
        self, browser_session: BrowserSession, index: int | None
    ) -> dict[str, Any] | None:
        """Helper to inspect DOM element node metadata from cached selector map."""
        if index is None:
            return None
        try:
            summary = browser_session._cached_browser_state_summary
            if summary and summary.dom_state and summary.dom_state.selector_map:
                node = summary.dom_state.selector_map.get(index)
                if node:
                    return {
                        "node_name": getattr(node, "node_name", ""),
                        "attributes": getattr(node, "attributes", {}) or {},
                        "text": getattr(node, "node_value", "") or "",
                    }
        except Exception:
            pass
        return None

    async def act(
        self,
        action: Any,
        browser_session: BrowserSession,
        **kwargs: Any,
    ) -> ActionResult:
        """Intercepts candidate actions before they are executed by browser-use."""
        action_dict = action.model_dump(exclude_unset=True)

        current_url = None
        try:
            current_url = await browser_session.get_current_page_url()
        except Exception:
            pass

        for action_name, params in action_dict.items():
            if params is None:
                continue

            elem_info = None
            if isinstance(params, dict) and "index" in params:
                elem_info = await self._extract_element_info(browser_session, params["index"])

            destination = None
            if isinstance(params, dict) and "url" in params:
                destination = params["url"]
            else:
                destination = current_url

            ctx = ActionContext(
                action_type=action_name,
                params=params if isinstance(params, dict) else {"value": params},
                destination=destination,
                element_info=elem_info,
                page_url=current_url,
            )

            # Evaluate policy deterministically
            policy_result = self.policy_engine.evaluate(ctx)

            # 1. Blocked Actions
            if policy_result.decision == PolicyDecision.BLOCK:
                self.audit_logger.log_decision(
                    action_type=action_name,
                    decision=PolicyDecision.BLOCK.value,
                    reason=policy_result.reason,
                    destination=destination,
                    risk_level=policy_result.risk_level,
                    action_hash=policy_result.action_hash,
                    approved=False,
                )
                print(f"[PRIVIX BLOCKED] Action '{action_name}' halted: {policy_result.reason}")
                return ActionResult(
                    error=f"[PRIVIX POLICY BLOCK] Action '{action_name}' blocked: {policy_result.reason}",
                    is_done=False,
                )

            # 2. Actions Requiring Approval (with Secure Action Binding)
            if policy_result.decision == PolicyDecision.REQUIRE_APPROVAL:
                is_async = asyncio.iscoroutinefunction(self.approval_handler)
                approved = (
                    await self.approval_handler(ctx, policy_result)
                    if is_async
                    else self.approval_handler(ctx, policy_result)
                )

                if not approved:
                    self.audit_logger.log_decision(
                        action_type=action_name,
                        decision=PolicyDecision.REQUIRE_APPROVAL.value,
                        reason=policy_result.reason,
                        destination=destination,
                        risk_level=policy_result.risk_level,
                        action_hash=policy_result.action_hash,
                        approved=False,
                    )
                    print(f"[PRIVIX REJECTED] Action '{action_name}' denied by user.")
                    return ActionResult(
                        error=f"[PRIVIX APPROVAL DENIED] User rejected action '{action_name}': {policy_result.reason}",
                        is_done=False,
                    )

                # Action approved -> Issue cryptographically bound token
                token = self.policy_engine.issue_approval_token(ctx)
                self.audit_logger.log_decision(
                    action_type=action_name,
                    decision=PolicyDecision.REQUIRE_APPROVAL.value,
                    reason=policy_result.reason,
                    destination=destination,
                    risk_level=policy_result.risk_level,
                    action_hash=policy_result.action_hash,
                    token_id=token.token_id,
                    approved=True,
                )

                # Verify token matches exact current action state before dispatching
                token_valid, verify_msg = self.policy_engine.verify_approval_token(token.token_id, ctx)
                if not token_valid:
                    print(f"[PRIVIX SECURITY] Action token binding failed: {verify_msg}")
                    return ActionResult(
                        error=f"[PRIVIX BINDING ERROR] Action modified after approval: {verify_msg}",
                        is_done=False,
                    )

                print(f"[PRIVIX APPROVED] Authorized action '{action_name}' with token {token.token_id[:16]}...")

            # 3. Allowed Actions
            if policy_result.decision == PolicyDecision.ALLOW:
                self.audit_logger.log_decision(
                    action_type=action_name,
                    decision=PolicyDecision.ALLOW.value,
                    reason=policy_result.reason,
                    destination=destination,
                    risk_level=policy_result.risk_level,
                    action_hash=policy_result.action_hash,
                    approved=True,
                )

        return await super().act(action=action, browser_session=browser_session, **kwargs)
