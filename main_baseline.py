import asyncio
import os
import sys

# Ensure UTF-8 output on Windows console to avoid charmap encoding errors
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Configure screenshot watchdog timeout
os.environ.setdefault("TIMEOUT_ScreenshotEvent", "60.0")

from browser_use import Agent, ChatOllama


async def main():
    print("Starting Privix local browser agent...")

    # ChatOllama configured with supported ollama_options mapping and client timeout
    llm = ChatOllama(
        model="qwen2.5:3b",
        ollama_options={
            "num_ctx": 8192,
        },
        timeout=180.0,
    )

    agent = Agent(
        task=(
            "The browser is open at https://example.com. "
            "Read the page title and visible text from the page state, "
            "and immediately call the done action reporting the title and a short summary. "
            "Do not navigate, reload, click links, or submit forms."
        ),
        llm=llm,
        use_vision=False,
        flash_mode=True,
        use_thinking=False,
        use_judge=False,
        llm_timeout=180,
        step_timeout=180,
        max_actions_per_step=1,
    )

    try:
        result = await agent.run(max_steps=2)
        print("\n=== PRIVIX RESULT ===")
        print(result.final_result())
    except Exception as exc:
        print(f"\nPrivix error: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
