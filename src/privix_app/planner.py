"""Privix Smart Intent Planner: Compiles natural language tasks into deterministic browser automation plans."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass


@dataclass
class IntentPlan:
    intent_type: str         # "youtube_playback" | "wikipedia_search" | "web_search" | "generic"
    query: str
    start_url: str | None
    optimized_prompt: str
    keep_browser_open: bool
    recommended_steps: int
    description: str


class SmartIntentPlanner:
    """
    Transforms fuzzy, high-level natural language user prompts into high-compliance,
    loop-resistant execution instructions tailored for local language models (qwen2.5:3b).
    """

    @classmethod
    def compile(cls, raw_prompt: str, user_keep_open: bool | None = None, user_steps: int | None = None) -> IntentPlan:
        text = raw_prompt.strip()

        # 1. YouTube Media Playback Intent
        # Examples: "play closer by ed sheeran on yt", "play starboy", "watch inception trailer on youtube"
        yt_match = re.search(
            r'(?:play|watch|listen to|stream)\s+(.+?)(?:\s+(?:on\s+)?(?:yt|youtube)|\s+video|\s+song)?$',
            text,
            re.IGNORECASE
        )
        is_yt_keyword = any(kw in text.lower() for kw in ("yt", "youtube", "music", "song", "track"))
        
        if yt_match and (is_yt_keyword or text.lower().startswith("play ") or text.lower().startswith("watch ")):
            query = yt_match.group(1).strip()
            # Clean trailing words like "on youtube", "on yt" if captured
            query = re.sub(r'\s+on\s+(?:youtube|yt)$', '', query, flags=re.IGNORECASE).strip()
            encoded_query = urllib.parse.quote_plus(query)
            start_url = f"https://www.youtube.com/results?search_query={encoded_query}"

            optimized_prompt = (
                f"The browser is open at {start_url}.\n"
                f"Goal: Start playing the video for '{query}'.\n"
                f"1. Scan the search results on the page to identify the first matching video title/thumbnail element.\n"
                f"2. Use the 'click' action with that element's index to open and start playback.\n"
                f"3. CRITICAL: Never use the 'navigate' action to guess watch URLs (e.g., watch?v=...). Only click the link on the page.\n"
                f"4. Once the video player page opens, call the 'done' action so playback continues."
            )

            keep_open = True if user_keep_open is None else user_keep_open
            steps = user_steps or 8

            return IntentPlan(
                intent_type="youtube_playback",
                query=query,
                start_url=start_url,
                optimized_prompt=optimized_prompt,
                keep_browser_open=keep_open,
                recommended_steps=steps,
                description=f"Smart Media Intent: Search YouTube for '{query}' and initiate playback."
            )

        # 2. Wikipedia Search & Summary Intent
        # Examples: "search quantum computing on wikipedia", "summarize artificial intelligence on wiki"
        wiki_match = re.search(
            r'(?:search|look up|read about|summarize|explain)\s+(.+?)(?:\s+on\s+(?:wikipedia|wiki))?$',
            text,
            re.IGNORECASE
        )
        if wiki_match and any(w in text.lower() for w in ("wikipedia", "wiki")):
            query = wiki_match.group(1).strip()
            query = re.sub(r'\s+on\s+(?:wikipedia|wiki)$', '', query, flags=re.IGNORECASE).strip()
            encoded_query = urllib.parse.quote_plus(query)
            start_url = f"https://en.wikipedia.org/wiki/Special:Search?search={encoded_query}"

            optimized_prompt = (
                f"The browser is open at {start_url}.\n"
                f"1. Read the article title and lead introductory paragraph for '{query}'.\n"
                f"2. Call the 'done' action returning a concise 2-3 sentence factual summary."
            )

            keep_open = False if user_keep_open is None else user_keep_open
            steps = user_steps or 6

            return IntentPlan(
                intent_type="wikipedia_search",
                query=query,
                start_url=start_url,
                optimized_prompt=optimized_prompt,
                keep_browser_open=keep_open,
                recommended_steps=steps,
                description=f"Smart Wikipedia Intent: Direct search and extraction for '{query}'."
            )

        # 3. Web Search Intent (DuckDuckGo / Google)
        # Examples: "search latest space telescope news on ddg", "search tech news on google"
        search_match = re.search(
            r'(?:search|google|find)\s+(.+?)(?:\s+on\s+(?:google|duckduckgo|ddg))?$',
            text,
            re.IGNORECASE
        )
        if search_match and any(w in text.lower() for w in ("google", "duckduckgo", "ddg")):
            query = search_match.group(1).strip()
            query = re.sub(r'\s+on\s+(?:google|duckduckgo|ddg)$', '', query, flags=re.IGNORECASE).strip()
            encoded_query = urllib.parse.quote_plus(query)
            engine = "duckduckgo" if "duck" in text.lower() or "ddg" in text.lower() else "google"
            start_url = f"https://duckduckgo.com/?q={encoded_query}" if engine == "duckduckgo" else f"https://www.google.com/search?q={encoded_query}"

            optimized_prompt = (
                f"The browser is open at {start_url}.\n"
                f"Review the search results for '{query}' and call the 'done' action summarizing the top 3 relevant findings."
            )

            keep_open = False if user_keep_open is None else user_keep_open
            steps = user_steps or 6

            return IntentPlan(
                intent_type="web_search",
                query=query,
                start_url=start_url,
                optimized_prompt=optimized_prompt,
                keep_browser_open=keep_open,
                recommended_steps=steps,
                description=f"Smart Search Intent: Direct query for '{query}' via {engine}."
            )

        # 4. Fallback / Generic Custom Prompt
        keep_open = False if user_keep_open is None else user_keep_open
        steps = user_steps or 10

        return IntentPlan(
            intent_type="generic",
            query=text,
            start_url=None,
            optimized_prompt=text,
            keep_browser_open=keep_open,
            recommended_steps=steps,
            description="Direct custom prompt execution."
        )
