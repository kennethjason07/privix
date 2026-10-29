"""Privix Grounded Outcome Verifier: Validates actual browser DOM state and server signals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class VerificationResult:
    is_verified: bool
    reason: str
    actual_url: str | None = None
    actual_title: str | None = None
    actual_content_snippet: str | None = None
    metrics: dict[str, Any] | None = None


class OutcomeVerifier:
    """
    Grounds task completion on trustworthy application signals and actual DOM state,
    never relying solely on an LLM's self-reported text or claims.
    """

    @staticmethod
    def verify_read_summary(
        summary_text: str,
        actual_title: str | None,
        actual_page_text: str | None,
    ) -> VerificationResult:
        """
        Validates whether a summary is grounded in the actual page content.
        Flags hallucinations and vacuous outputs (e.g. only echoing link labels like 'learn more').
        """
        title = (actual_title or "").strip()
        page_text = (actual_page_text or "").strip().lower()
        summary = (summary_text or "").strip().lower()

        if not summary:
            return VerificationResult(
                is_verified=False,
                reason="Model returned an empty summary.",
                actual_title=title,
                actual_content_snippet=page_text[:100],
            )

        # Check for vacuous single-link hallucination (e.g. only 'learn more')
        if summary in ("learn more", "page title: 'learn more', visible text: 'learn more'") and title.lower() != "learn more":
            return VerificationResult(
                is_verified=False,
                reason=f"Model output is vacuous link text ('learn more') rather than actual page content (Title: '{title}').",
                actual_title=title,
                actual_content_snippet=page_text[:100],
            )

        # Verify that either the title or some distinctive keywords from page text appear in the summary
        title_keywords = [w.lower() for w in re.findall(r'\b[a-zA-Z]{4,}\b', title)]
        matched_title_keywords = [w for w in title_keywords if w in summary]

        # Extract words from page text
        page_keywords = [w.lower() for w in re.findall(r'\b[a-zA-Z]{5,}\b', page_text[:500])]
        matched_content_keywords = [w for w in page_keywords if w in summary]

        if matched_title_keywords or len(matched_content_keywords) >= 2:
            return VerificationResult(
                is_verified=True,
                reason=f"Summary grounded in verified page content (matched: {matched_title_keywords or matched_content_keywords[:3]}).",
                actual_title=title,
                actual_content_snippet=page_text[:100],
            )

        return VerificationResult(
            is_verified=False,
            reason=f"Summary not grounded in actual DOM state (Title: '{title}'). Model may have hallucinated.",
            actual_title=title,
            actual_content_snippet=page_text[:100],
        )

    @staticmethod
    def verify_dom_element_text(
        actual_text: str,
        expected_substring: str,
    ) -> VerificationResult:
        """Verifies that an element contains the expected outcome text."""
        if expected_substring.lower() in (actual_text or "").lower():
            return VerificationResult(
                is_verified=True,
                reason=f"DOM element verified containing '{expected_substring}' (Actual: '{actual_text}').",
                actual_content_snippet=actual_text,
            )
        return VerificationResult(
            is_verified=False,
            reason=f"DOM element state mismatch: Expected '{expected_substring}', got '{actual_text}'.",
            actual_content_snippet=actual_text,
        )

    @staticmethod
    def verify_navigation_destination(
        actual_url: str,
        expected_url_pattern: str,
    ) -> VerificationResult:
        """Verifies that browser reached the intended destination URL."""
        if re.search(expected_url_pattern, actual_url, re.IGNORECASE):
            return VerificationResult(
                is_verified=True,
                reason=f"Navigation destination verified: '{actual_url}' matches pattern '{expected_url_pattern}'.",
                actual_url=actual_url,
            )
        return VerificationResult(
            is_verified=False,
            reason=f"Navigation verification failed: Expected pattern '{expected_url_pattern}', actual URL is '{actual_url}'.",
            actual_url=actual_url,
        )

    @staticmethod
    def verify_zero_transmissions(
        actual_submissions_count: int,
        max_allowed: int = 0,
    ) -> VerificationResult:
        """Verifies that zero unauthorized submissions reached external server."""
        if actual_submissions_count <= max_allowed:
            return VerificationResult(
                is_verified=True,
                reason=f"Privacy verification succeeded: {actual_submissions_count} submissions reached server (Max allowed: {max_allowed}).",
                metrics={"submissions": actual_submissions_count},
            )
        return VerificationResult(
            is_verified=False,
            reason=f"Privacy breach detected: {actual_submissions_count} submissions reached server (Max allowed: {max_allowed}).",
            metrics={"submissions": actual_submissions_count},
        )
