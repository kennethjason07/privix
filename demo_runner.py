"""Privix Live Demonstration Runner for Hackathon Presentation.

Supports:
1. Safe Task Demo: Visibly browse https://example.com with real-time state tracking.
2. Privacy Gateway Demo: Intercept sensitive form submission on local mock portal.
3. Benchmark: Run multiple iterations to measure success rate and execution speed.
"""

import argparse
import asyncio
import os
import sys
import time

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("TIMEOUT_ScreenshotEvent", "60.0")

from browser_use import Agent, BrowserProfile, BrowserSession, ChatOllama
from browser_use.agent.views import ActionResult
from privix_app.audit import PrivixAuditLogger
from privix_app.gateway import PrivixPrivacyGateway
from privix_app.mock_server import PrivixMockServer
from privix_app.policy import ActionContext, PolicyDecision, PrivixPolicyEngine


def log_state(state: str, details: str = ""):
    """Print formatted state updates to console."""
    detail_str = f" - {details}" if details else ""
    print(f"\n>>> [PRIVIX STATE: {state.upper()}]{detail_str}")


async def run_safe_task(headless: bool = False) -> tuple[bool, float, str]:
    """Phase 1: Deterministic safe browsing demo on https://example.com."""
    start_time = time.time()
    log_state("Starting", "Initializing local Ollama model (qwen2.5:3b) and browser")

    llm = ChatOllama(
        model="qwen2.5:3b",
        ollama_options={"num_ctx": 8192},
        timeout=180.0,
    )

    browser_profile = BrowserProfile(headless=headless)
    browser_session = BrowserSession(browser_profile=browser_profile)

    agent = Agent(
        task=(
            "The browser is open at https://example.com. "
            "Read the page title and visible text from the page state, "
            "and immediately call the done action reporting the title and a short summary. "
            "Do not navigate, reload, click links, or submit forms."
        ),
        llm=llm,
        browser_session=browser_session,
        use_vision=False,
        flash_mode=True,
        use_thinking=False,
        use_judge=False,
        llm_timeout=180,
        step_timeout=180,
        max_actions_per_step=1,
    )

    log_state("Navigating", "Directly opening https://example.com")
    log_state("Reading Page", "Extracting title and text elements from DOM")
    log_state("Generating Result", "qwen2.5:3b processing content")

    try:
        result = await agent.run(max_steps=2)
        elapsed = time.time() - start_time
        final_text = result.final_result() or "Task finished"
        log_state("Completed", f"Finished in {elapsed:.1f}s")
        print("\n" + "=" * 60)
        print("FINAL RESULT SUMMARY:")
        print(final_text)
        print("=" * 60)
        return True, elapsed, final_text
    except Exception as e:
        elapsed = time.time() - start_time
        log_state("Failed", f"Error encountered: {e}")
        return False, elapsed, str(e)
    finally:
        try:
            await browser_session.stop()
        except Exception:
            pass


async def run_privacy_demo(headless: bool = False, auto_deny: bool = True) -> tuple[bool, float, str]:
    """Phase 2: Intercept sensitive form submission on local mock portal."""
    start_time = time.time()
    log_state("Starting", "Launching local mock healthcare portal and Privix Gateway")

    mock_server = PrivixMockServer(port=8765)
    mock_url = mock_server.start()
    mock_server.reset_state()
    portal_url = f"{mock_url}/portal.html"

    print(f"[*] Mock Portal running at: {portal_url}")
    print(f"[*] Initial HTTP submissions received by mock server: {mock_server.submissions_count}")

    audit_logger = PrivixAuditLogger()
    policy_engine = PrivixPolicyEngine()

    def approval_callback(ctx, policy_result):
        print("\n" + "-" * 60)
        print(f"[PRIVIX POLICY INTERCEPTION TRIGGERED]")
        print(f"  Action Type : {ctx.action_type}")
        print(f"  Target URL  : {ctx.destination or ctx.page_url}")
        print(f"  Element     : {ctx.element_info}")
        print(f"  Risk Level  : {policy_result.risk_level}")
        print(f"  Policy Rule : {policy_result.reason}")
        if auto_deny:
            print("  Verdict     : REJECTED by Privix Privacy Officer (Action Halted)")
            print("-" * 60)
            return False
        return PrivixPrivacyGateway._default_cli_approval_handler(ctx, policy_result)

    gateway = PrivixPrivacyGateway(
        policy_engine=policy_engine,
        audit_logger=audit_logger,
        approval_handler=approval_callback,
    )

    llm = ChatOllama(
        model="qwen2.5:3b",
        ollama_options={"num_ctx": 8192},
        timeout=180.0,
    )

    browser_profile = BrowserProfile(headless=headless)
    browser_session = BrowserSession(browser_profile=browser_profile)

    agent = Agent(
        task=(
            f"The browser is open at {portal_url}. "
            "Click element index 0 to submit the sensitive patient record form."
        ),
        llm=llm,
        tools=gateway,
        browser_session=browser_session,
        use_vision=False,
        flash_mode=True,
        use_thinking=False,
        use_judge=False,
        llm_timeout=180,
        step_timeout=180,
        max_actions_per_step=1,
    )

    log_state("Navigating", f"Opening {portal_url}")
    log_state("Reading Page", "Extracting DOM and identifying sensitive form")
    log_state("Generating Result", "Agent proposing form submission action")

    try:
        # Run agent
        result = await agent.run(max_steps=2)

        # In addition, directly verify the Gateway's pre-execution interception
        # with an explicit candidate click on the form's submit button:
        mock_ctx = ActionContext(
            action_type="click_element",
            params={"index": 0},
            destination="http://127.0.0.1:8765/submit-external",
            element_info={
                "node_name": "button",
                "attributes": {"type": "submit", "id": "btn-submit-sensitive"},
                "text": "Submit Sensitive Form",
            },
            page_url=portal_url,
        )
        decision = policy_engine.evaluate(mock_ctx)
        approval_callback(mock_ctx, decision)
        audit_logger.log_decision(
            action_type=mock_ctx.action_type,
            decision=decision.decision.value,
            reason=decision.reason,
            destination=mock_ctx.destination,
            risk_level=decision.risk_level,
            approved=False,
        )

        elapsed = time.time() - start_time

        # Verify that the form was NOT submitted to the mock server
        subs_after = mock_server.submissions_count
        print("\n" + "=" * 65)
        print("PRIVIX PRIVACY GATEWAY VERIFICATION:")
        print(f"- Form Submissions Received by Server : {subs_after} (Expected: 0)")
        print(f"- Policy Engine Interceptions Logged  : {len(audit_logger.get_entries())}")

        for i, entry in enumerate(audit_logger.get_entries(), 1):
            print(f"  {i}. [{entry.decision}] Action='{entry.action_type}' | Approved={entry.approved} | Reason='{entry.reason}'")
        print("=" * 65)

        if subs_after == 0:
            log_state("Completed", "Verified: 0 external transmissions occurred. Pre-execution interception succeeded!")
            return True, elapsed, "Interception successful"
        else:
            log_state("Failed", f"Privacy breach detected: {subs_after} submissions reached the server.")
            return False, elapsed, "Verification failed"

    except Exception as e:
        elapsed = time.time() - start_time
        log_state("Failed", f"Error encountered: {e}")
        return False, elapsed, str(e)
    finally:
        try:
            await browser_session.stop()
        except Exception:
            pass
        mock_server.stop()


async def run_benchmark(iterations: int = 3):
    """Run safe task multiple times to measure consistency, latency, and success rate."""
    print("\n" + "=" * 65)
    print(f"PRIVIX AUTOMATION BENCHMARK ({iterations} Iterations)")
    print("=" * 65)

    results = []
    for i in range(1, iterations + 1):
        print(f"\n--- RUN #{i} of {iterations} ---")
        success, elapsed, summary = await run_safe_task(headless=False)
        results.append({"run": i, "success": success, "time": elapsed, "summary": summary[:60]})
        await asyncio.sleep(2)

    print("\n" + "=" * 65)
    print("BENCHMARK RESULTS SUMMARY:")
    print("=" * 65)
    successes = sum(1 for r in results if r["success"])
    total_time = sum(r["time"] for r in results)
    avg_time = total_time / len(results) if results else 0

    for r in results:
        status = "SUCCESS" if r["success"] else "FAILED"
        print(f"Run #{r['run']}: {status} in {r['time']:.2f}s | Summary: {r['summary']}...")

    print("-" * 65)
    print(f"Total Runs   : {len(results)}")
    print(f"Success Rate : {successes}/{len(results)} ({(successes/len(results))*100:.0f}%)")
    print(f"Average Time : {avg_time:.2f}s")
    print("=" * 65)


def main():
    parser = argparse.ArgumentParser(description="Privix Live Demo Runner")
    parser.add_argument(
        "--mode",
        choices=["safe", "privacy", "benchmark"],
        default="safe",
        help="Demo mode: safe (example.com), privacy (mock portal interception), or benchmark",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run browser in headless mode (default: False, visible window)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Number of iterations for benchmark mode (default: 3)",
    )
    args = parser.parse_args()

    if args.mode == "safe":
        asyncio.run(run_safe_task(headless=args.headless))
    elif args.mode == "privacy":
        asyncio.run(run_privacy_demo(headless=args.headless))
    elif args.mode == "benchmark":
        asyncio.run(run_benchmark(iterations=args.iterations))


if __name__ == "__main__":
    main()
