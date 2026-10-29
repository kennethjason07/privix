# 🔒 Privix — Privacy-Preserving AI Browser Agent

Privix is a local, privacy-first AI browser automation platform. An AI agent delegates browser tasks while a deterministic **Privacy Gateway** intercepts and evaluates every browser action before it reaches the DOM or network — blocking, allowing, or requiring human approval based on configurable policies.

Built for hackathon demonstration. 100% local — no cloud LLM calls, no external data transmission.

---

## Stack

| Component | Technology |
|---|---|
| LLM | [Ollama](https://ollama.com) · `qwen2.5:3b` (100% GPU, RTX 3050) |
| Browser Automation | [browser-use](https://github.com/browser-use/browser-use) 0.13.14 |
| Privacy Gateway | Custom deterministic policy engine |
| Web UI | FastAPI + WebSockets + vanilla HTML/CSS/JS |
| Python | 3.12+ · [uv](https://docs.astral.sh/uv/) |

---

## Quick Start

### Prerequisites
- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- [Ollama](https://ollama.com) running locally with `qwen2.5:3b` pulled

```bash
# Pull the model (one-time)
ollama pull qwen2.5:3b
```

### Install

```bash
git clone https://github.com/kennethjason07/privix.git
cd privix
uv sync
```

### Run the Web UI

```bash
uv run python ui.py
# Opens http://127.0.0.1:7860 in your browser automatically
```

### Run the CLI Demo

```bash
# Safe browsing demo (example.com summary)
uv run python demo_runner.py --mode safe

# Privacy gateway demo (intercepts sensitive form submission)
uv run python demo_runner.py --mode privacy

# Benchmark (3 iterations)
uv run python demo_runner.py --mode benchmark --iterations 3
```

### Run Tests

```bash
uv run python -m unittest discover -s tests -v
# Expected: 19 tests, OK
```

---

## Architecture

```
User / Web UI (FastAPI + WS)
        │
        ▼
  demo_runner.py / server.py
        │
        ▼
  browser-use Agent
        │   (every action)
        ▼
  PrivixPrivacyGateway  ←──  PrivixPolicyEngine
        │                          │
        │                    ActionContext
        │                    PolicyResult: ALLOW / BLOCK / REQUIRE_APPROVAL
        │
        ▼ (if ALLOW or approved)
  browser-use Tools.act()  →  Playwright / DOM / Network
```

### Policy Engine Rules

| Action Type | Default Decision |
|---|---|
| Public web navigation (`http://`, `https://`) | `ALLOW` |
| Form submission (submit button click) | `REQUIRE_APPROVAL` |
| Text input with detected sensitive data (SSN, card, etc.) | `BLOCK` |
| Destructive element interaction (delete, drop, purge…) | `REQUIRE_APPROVAL` |
| Script execution (`evaluate`) | `REQUIRE_APPROVAL` |
| File system write / upload | `REQUIRE_APPROVAL` |
| Unknown / unclassified action | `BLOCK` (fail-closed) |

### Sensitive Data Detection

`SensitiveDataDetector` detects and redacts:
- Social Security Numbers (SSN)
- Payment card numbers
- Email addresses + phone numbers
- API / secret keys
- Healthcare keywords (prescription, diagnosis, patient…)
- Credential keywords (password, token…)

---

## Project Structure

```
privix-app/
├── ui.py                        # Web UI launcher
├── demo_runner.py               # CLI demo runner (safe | privacy | benchmark)
├── main.py                      # Simple agent entry point
├── main_baseline.py             # Preserved working baseline
├── pyproject.toml
├── src/
│   └── privix_app/
│       ├── server.py            # FastAPI server + WebSocket
│       ├── policy.py            # Deterministic policy engine
│       ├── gateway.py           # browser-use Tools interception layer
│       ├── audit.py             # Structured audit logging with auto-redaction
│       ├── detector.py          # Sensitive data detector + redactor
│       ├── mock_server.py       # Local synthetic healthcare portal (port 8765)
│       ├── task_state.py        # Task lifecycle state machine
│       └── static/
│           └── index.html       # Single-page web UI
└── tests/
    └── test_policy.py           # 19 unit tests
```

---

## Known Limitations

- **CDP-level bypasses**: Screenshots and browser lifecycle events dispatched via Chrome DevTools Protocol bypass `Tools.act()` — these are documented, not claimed as covered.
- **Single task at a time**: Playwright on Windows does not support multiple concurrent browser sessions trivially.
- **Model variability**: `qwen2.5:3b` occasionally re-navigates instead of calling `done` immediately — bounded by `max_steps`.

---

## License

MIT
