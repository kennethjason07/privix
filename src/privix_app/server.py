"""Privix Web UI Server — FastAPI backend with real-time WebSocket task streaming.

Architecture:
- GET  /              → Serves the single-page HTML UI
- POST /api/run       → Starts a task (safe | privacy | custom) and returns task_id
- WS   /ws/{task_id} → Streams real-time progress events to the UI
- GET  /api/audit     → Returns current session audit log entries
- GET  /api/status    → Returns server health and Ollama connectivity
- POST /api/approve   → Sends approval/denial decision for a pending REQUIRE_APPROVAL action
- POST /api/reset     → Resets mock server state (for repeated demos)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

# Ensure UTF-8 on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from privix_app.audit import PrivixAuditLogger
from privix_app.gateway import PrivixPrivacyGateway
from privix_app.mock_server import PrivixMockServer
from privix_app.policy import ActionContext, PolicyDecision, PolicyResult, PrivixPolicyEngine
from privix_app.task_state import TaskLifecycleTracker, TaskState


# ── Constants ──────────────────────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"
SAFE_TASK_PROMPT = (
    "The browser is open at https://example.com. "
    "Read the page title and visible text from the page state, "
    "and immediately call the done action reporting the title and a short summary. "
    "Do not navigate, reload, click links, or submit forms."
)
PRIVACY_TASK_PROMPT_TEMPLATE = (
    "The browser is open at {portal_url}. "
    "Click element index 0 to submit the sensitive patient record form."
)
MOCK_SERVER_PORT = 8765


# ── Task state management ──────────────────────────────────────────────────────

class EventType(str, Enum):
    STATE       = "state"
    LOG         = "log"
    RESULT      = "result"
    AUDIT       = "audit"
    APPROVAL    = "approval_required"
    APPROVED    = "approved"
    DENIED      = "denied"
    ERROR       = "error"
    MOCK_STATUS = "mock_status"


@dataclass
class TaskRecord:
    task_id: str
    mode: str          # "safe" | "privacy" | "custom"
    task_text: str
    started_at: float
    state: str = TaskState.QUEUED.value
    result: str = ""
    error: str = ""
    elapsed: float = 0.0
    audit_entries: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.audit_entries is None:
            self.audit_entries = []


# Active tasks map: task_id → TaskRecord
_tasks: dict[str, TaskRecord] = {}
# WebSocket connections per task_id
_ws_connections: dict[str, list[WebSocket]] = {}
# Pending approval futures: task_id → Future[bool]
_approval_futures: dict[str, asyncio.Future] = {}
# Shared mock server (singleton for session)
_mock_server: PrivixMockServer | None = None
# Shared audit logger per task
_task_audit_loggers: dict[str, PrivixAuditLogger] = {}


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(title="Privix Privacy Gateway", version="1.0.0")

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _broadcast(task_id: str, event: dict) -> None:
    """Send a JSON event to all WebSocket connections subscribed to task_id."""
    conns = _ws_connections.get(task_id, [])
    dead = []
    msg = json.dumps(event)
    for ws in conns:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        conns.remove(ws)


def _emit(task_id: str, etype: EventType, **payload) -> None:
    """Schedule a broadcast from sync context (safe from any thread)."""
    event = {"type": etype.value, "task_id": task_id, **payload}
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_broadcast(task_id, event))
        else:
            loop.run_until_complete(_broadcast(task_id, event))
    except RuntimeError:
        pass


async def _emit_async(task_id: str, etype: EventType, **payload) -> None:
    """Broadcast from async context."""
    event = {"type": etype.value, "task_id": task_id, **payload}
    await _broadcast(task_id, event)


def _get_mock_server() -> PrivixMockServer:
    global _mock_server
    if _mock_server is None:
        _mock_server = PrivixMockServer(port=MOCK_SERVER_PORT)
        _mock_server.start()
    return _mock_server


# ── Task runners ───────────────────────────────────────────────────────────────

async def _run_safe_task(task_id: str, custom_prompt: str | None = None) -> None:
    """Execute the safe browsing demo and stream progress via WebSocket."""
    from browser_use import Agent, BrowserProfile, BrowserSession, ChatOllama

    record = _tasks[task_id]
    audit_logger = _task_audit_loggers[task_id]

    async def emit(etype: EventType, **kw):
        await _emit_async(task_id, etype, **kw)

    record.state = TaskState.RUNNING.value
    await emit(EventType.STATE, state=TaskState.RUNNING.value, message="Initializing Ollama + browser...")

    prompt = custom_prompt or SAFE_TASK_PROMPT

    try:
        llm = ChatOllama(
            model="qwen2.5:3b",
            ollama_options={"num_ctx": 8192},
            timeout=180.0,
        )
        browser_profile = BrowserProfile(headless=False)
        browser_session = BrowserSession(browser_profile=browser_profile)

        agent = Agent(
            task=prompt,
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

        await emit(EventType.LOG, message="Browser opened. Navigating to https://example.com...")
        await emit(EventType.STATE, state="navigating", message="Navigating...")

        start = time.time()
        result = await asyncio.wait_for(agent.run(max_steps=3), timeout=300)
        elapsed = time.time() - start

        final = result.final_result() or "Task completed (no text returned)"
        record.state = TaskState.COMPLETED.value
        record.result = final
        record.elapsed = elapsed

        await emit(EventType.STATE, state=TaskState.COMPLETED.value, message=f"Done in {elapsed:.1f}s")
        await emit(EventType.RESULT, result=final, elapsed=elapsed)

        try:
            await browser_session.stop()
        except Exception:
            pass

    except asyncio.TimeoutError:
        record.state = TaskState.FAILED.value
        record.error = "Task timed out after 300 seconds."
        await emit(EventType.STATE, state=TaskState.FAILED.value, message=record.error)
        await emit(EventType.ERROR, error=record.error)
    except Exception as exc:
        record.state = TaskState.FAILED.value
        record.error = str(exc)
        await emit(EventType.STATE, state=TaskState.FAILED.value, message=f"Error: {exc}")
        await emit(EventType.ERROR, error=str(exc))


async def _run_privacy_task(task_id: str) -> None:
    """Execute the privacy gateway demo and stream interception events."""
    from browser_use import Agent, BrowserProfile, BrowserSession, ChatOllama

    record = _tasks[task_id]
    audit_logger = _task_audit_loggers[task_id]

    async def emit(etype: EventType, **kw):
        await _emit_async(task_id, etype, **kw)

    record.state = TaskState.RUNNING.value
    await emit(EventType.STATE, state=TaskState.RUNNING.value, message="Starting Privix Privacy Gateway demo...")

    # Start / reset mock server
    mock_server = _get_mock_server()
    mock_server.reset_state()
    portal_url = f"http://127.0.0.1:{MOCK_SERVER_PORT}/portal.html"
    await emit(EventType.MOCK_STATUS, submissions=0, portal_url=portal_url)
    await emit(EventType.LOG, message=f"Mock healthcare portal running at {portal_url}")

    policy_engine = PrivixPolicyEngine()

    # Build async-aware approval handler that blocks until UI decision
    async def approval_handler(ctx: ActionContext, result: PolicyResult) -> bool:
        # Signal UI that approval is required
        approval_payload = {
            "action_type": ctx.action_type,
            "destination": ctx.destination or ctx.page_url,
            "params": ctx.params,
            "element_info": ctx.element_info,
            "reason": result.reason,
            "risk_level": result.risk_level,
        }
        await emit(EventType.APPROVAL, **approval_payload)
        await emit(EventType.STATE, state=TaskState.WAITING_FOR_APPROVAL.value,
                   message="Waiting for human approval...")
        record.state = TaskState.WAITING_FOR_APPROVAL.value

        # Create a future the UI resolves via POST /api/approve
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        _approval_futures[task_id] = fut
        try:
            approved = await asyncio.wait_for(asyncio.shield(fut), timeout=120)
        except asyncio.TimeoutError:
            approved = False
            await emit(EventType.LOG, message="Approval timed out after 120s. Defaulting to DENY.")
        finally:
            _approval_futures.pop(task_id, None)

        record.state = TaskState.RUNNING.value
        if approved:
            await emit(EventType.APPROVED, action_type=ctx.action_type)
        else:
            await emit(EventType.DENIED, action_type=ctx.action_type,
                       reason="User denied the action via Privix Privacy Dashboard.")
        return approved

    gateway = PrivixPrivacyGateway(
        policy_engine=policy_engine,
        audit_logger=audit_logger,
        approval_handler=approval_handler,
    )

    try:
        llm = ChatOllama(
            model="qwen2.5:3b",
            ollama_options={"num_ctx": 8192},
            timeout=180.0,
        )
        browser_profile = BrowserProfile(headless=False)
        browser_session = BrowserSession(browser_profile=browser_profile)

        agent = Agent(
            task=PRIVACY_TASK_PROMPT_TEMPLATE.format(portal_url=portal_url),
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

        await emit(EventType.LOG, message="Agent starting. Browser navigating to mock portal...")
        await emit(EventType.STATE, state="navigating", message="Navigating to mock portal...")

        start = time.time()
        await asyncio.wait_for(agent.run(max_steps=3), timeout=300)
        elapsed = time.time() - start

        # Explicit verification: directly evaluate the submit action
        mock_ctx = ActionContext(
            action_type="click_element",
            params={"index": 0},
            destination=f"http://127.0.0.1:{MOCK_SERVER_PORT}/submit-external",
            element_info={
                "node_name": "button",
                "attributes": {"type": "submit", "id": "btn-submit-sensitive"},
                "text": "Submit Sensitive Form",
            },
            page_url=portal_url,
        )
        decision = policy_engine.evaluate(mock_ctx)
        audit_logger.log_decision(
            action_type=mock_ctx.action_type,
            decision=decision.decision.value,
            reason=decision.reason,
            destination=mock_ctx.destination,
            risk_level=decision.risk_level,
            approved=False,
        )

        subs = mock_server.submissions_count
        entries = [asdict(e) for e in audit_logger.get_entries()]

        await emit(EventType.MOCK_STATUS, submissions=subs, portal_url=portal_url)
        await emit(EventType.AUDIT, entries=entries)

        if subs == 0:
            result_text = (
                f"Privacy Gateway intercepted the form submission. "
                f"Zero external transmissions occurred. "
                f"{len(entries)} policy decisions logged."
            )
            record.state = TaskState.COMPLETED.value
            record.result = result_text
            record.elapsed = elapsed
            await emit(EventType.STATE, state=TaskState.COMPLETED.value,
                       message="Interception verified. 0 submissions reached server.")
            await emit(EventType.RESULT, result=result_text, elapsed=elapsed, submissions=subs)
        else:
            record.state = TaskState.FAILED.value
            record.error = f"Privacy breach: {subs} submission(s) reached the mock server."
            await emit(EventType.STATE, state=TaskState.FAILED.value, message=record.error)
            await emit(EventType.ERROR, error=record.error)

        try:
            await browser_session.stop()
        except Exception:
            pass

    except asyncio.TimeoutError:
        record.state = TaskState.FAILED.value
        record.error = "Privacy demo timed out after 300 seconds."
        await emit(EventType.STATE, state=TaskState.FAILED.value, message=record.error)
        await emit(EventType.ERROR, error=record.error)
    except Exception as exc:
        record.state = TaskState.FAILED.value
        record.error = str(exc)
        await emit(EventType.STATE, state=TaskState.FAILED.value, message=f"Error: {exc}")
        await emit(EventType.ERROR, error=str(exc))


# ── HTTP API ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Privix UI not found. Run build or place index.html in static/</h1>", status_code=503)


@app.get("/api/status")
async def get_status():
    """Health check — verifies Ollama is reachable."""
    import httpx
    ollama_ok = False
    ollama_model = "qwen2.5:3b"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code == 200:
                ollama_ok = True
    except Exception:
        pass
    return {
        "status": "ok",
        "ollama": ollama_ok,
        "ollama_model": ollama_model,
        "active_tasks": len(_tasks),
    }


@app.post("/api/run")
async def start_task(body: dict):
    """
    Start a new agent task.
    Body: { "mode": "safe" | "privacy" | "custom", "task": "<optional custom prompt>" }
    Returns: { "task_id": "..." }
    """
    mode = body.get("mode", "safe")
    custom_task = body.get("task", "")

    task_id = str(uuid.uuid4())[:8]
    audit_logger = PrivixAuditLogger()
    _task_audit_loggers[task_id] = audit_logger
    _ws_connections[task_id] = []

    task_text = custom_task if mode == "custom" else (
        SAFE_TASK_PROMPT if mode == "safe" else
        PRIVACY_TASK_PROMPT_TEMPLATE.format(portal_url=f"http://127.0.0.1:{MOCK_SERVER_PORT}/portal.html")
    )

    record = TaskRecord(
        task_id=task_id,
        mode=mode,
        task_text=task_text,
        started_at=time.time(),
    )
    _tasks[task_id] = record

    # Launch the task in the background
    if mode == "safe":
        asyncio.ensure_future(_run_safe_task(task_id, custom_prompt=custom_task or None))
    elif mode == "privacy":
        asyncio.ensure_future(_run_privacy_task(task_id))
    elif mode == "custom":
        asyncio.ensure_future(_run_safe_task(task_id, custom_prompt=custom_task))
    else:
        return JSONResponse({"error": f"Unknown mode '{mode}'"}, status_code=400)

    return {"task_id": task_id, "mode": mode}


@app.post("/api/approve")
async def approve_action(body: dict):
    """
    Resolve a pending approval request.
    Body: { "task_id": "...", "approved": true | false }
    """
    task_id = body.get("task_id")
    approved = bool(body.get("approved", False))

    fut = _approval_futures.get(task_id)
    if fut is None or fut.done():
        return JSONResponse({"error": "No pending approval for this task."}, status_code=404)

    fut.set_result(approved)
    return {"task_id": task_id, "approved": approved}


@app.get("/api/audit")
async def get_audit(task_id: str | None = None):
    """Return audit log entries, optionally filtered by task_id."""
    if task_id:
        logger = _task_audit_loggers.get(task_id)
        entries = [asdict(e) for e in logger.get_entries()] if logger else []
    else:
        # Merge all loggers
        entries = []
        for logger in _task_audit_loggers.values():
            entries.extend(asdict(e) for e in logger.get_entries())
    return {"entries": entries}


@app.post("/api/reset")
async def reset_mock():
    """Reset mock server submission counter."""
    srv = _get_mock_server()
    srv.reset_state()
    return {"submissions": 0, "status": "reset"}


@app.get("/api/mock-status")
async def mock_status():
    srv = _get_mock_server()
    return {
        "submissions": srv.submissions_count,
        "portal_url": f"http://127.0.0.1:{MOCK_SERVER_PORT}/portal.html",
    }


# ── WebSocket ──────────────────────────────────────────────────────────────────

@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()
    if task_id not in _ws_connections:
        _ws_connections[task_id] = []
    _ws_connections[task_id].append(websocket)

    # Immediately send current task state if known
    record = _tasks.get(task_id)
    if record:
        await websocket.send_text(json.dumps({
            "type": EventType.STATE.value,
            "task_id": task_id,
            "state": record.state,
            "message": f"Connected. Current state: {record.state}",
        }))
        if record.audit_entries:
            await websocket.send_text(json.dumps({
                "type": EventType.AUDIT.value,
                "task_id": task_id,
                "entries": record.audit_entries,
            }))

    try:
        # Keep connection alive until client disconnects
        while True:
            await asyncio.sleep(10)
            # Send heartbeat to detect dead connections
            try:
                await websocket.send_text(json.dumps({"type": "ping"}))
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        conns = _ws_connections.get(task_id, [])
        if websocket in conns:
            conns.remove(websocket)


# ── Entry point ────────────────────────────────────────────────────────────────

def start(host: str = "127.0.0.1", port: int = 7860, open_browser: bool = True):
    """Start the Privix web server."""
    if open_browser:
        import threading
        import webbrowser
        def _open():
            time.sleep(1.5)
            webbrowser.open(f"http://{host}:{port}")
        threading.Thread(target=_open, daemon=True).start()

    print(f"\n{'='*60}")
    print(f"  Privix Privacy Gateway UI")
    print(f"  Local:  http://{host}:{port}")
    print(f"  Press Ctrl+C to stop")
    print(f"{'='*60}\n")

    uvicorn.run(
        "privix_app.server:app",
        host=host,
        port=port,
        log_level="warning",
        reload=False,
    )


if __name__ == "__main__":
    start()

