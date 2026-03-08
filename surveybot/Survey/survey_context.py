from __future__ import annotations

import logging
import threading
from pprint import pprint
from typing import Any

from openai import OpenAI

logger = logging.getLogger("survey_context")


class SurveyContext:
    """In-memory rolling context for a single survey session."""

    def __init__(
        self,
        session_id: str,
        openai_api_key: str,
        summary_every_n_pages: int = 1,
    ) -> None:
        self.session_id = session_id
        self.history: list[dict[str, Any]] = []
        self.summary = ""
        self.page_count = 0
        self._api_key = openai_api_key
        self.summary_every_n_pages = max(1, int(summary_every_n_pages))
        self._lock = threading.RLock()

    def record(self, question: str, options: list[str], answer: str) -> None:
        """Append one answered question block to history."""
        entry = {
            "question": question or "",
            "options": [str(opt) for opt in (options or [])],
            "answer": answer or "",
        }
        with self._lock:
            self.history.append(entry)

        logger.debug(
            "SurveyContext.record session_id=%s history_size=%d question=%r answer=%r",
            self.session_id,
            len(self.history),
            entry["question"][:120],
            entry["answer"][:120],
        )

    def maybe_update_summary(self) -> None:
        """Increment page counter and trigger async summary refresh on schedule."""
        should_update = False
        with self._lock:
            self.page_count += 1
            should_update = (
                self.page_count % self.summary_every_n_pages == 0
                and len(self.history) > 0
            )

        if should_update:
            logger.debug(
                "SurveyContext summary update start session_id=%s page_count=%d history_size=%d",
                self.session_id,
                self.page_count,
                len(self.history),
            )
            threading.Thread(target=self._generate_summary, daemon=True).start()

    def get_context_snippet(self) -> str:
        """Return compact prompt-ready context containing summary + last 5 Q&A."""
        with self._lock:
            summary = self.summary.strip()
            recent = self.history[-5:]

        if not summary and not recent:
            return ""

        lines = [
            "[Survey context]",
            f"Summary: {summary if summary else 'Not yet available'}",
            "Recent Q&A (last 5):",
        ]

        for entry in recent:
            options = ", ".join(entry.get("options", []))
            lines.append(
                f"- Q: {entry.get('question', '')} | Options: {options} | A: {entry.get('answer', '')}"
            )

        return "\n".join(lines)

    def dump(self) -> dict[str, Any]:
        """Return plain dict copy of full internal state for debugging/logging."""
        with self._lock:
            return {
                "session_id": self.session_id,
                "history": [dict(item) for item in self.history],
                "summary": self.summary,
                "page_count": self.page_count,
                "summary_every_n_pages": self.summary_every_n_pages,
            }

    def print_debug(self) -> None:
        """Pretty-print current context with clear section headers."""
        state = self.dump()
        print("=" * 60)
        print("SURVEY CONTEXT DEBUG")
        print("=" * 60)
        print(f"Session ID: {state['session_id']}")
        print(f"Page count: {state['page_count']}")
        print(f"Summary every N pages: {state['summary_every_n_pages']}")
        print("\n[Summary]")
        print(state["summary"] if state["summary"].strip() else "(empty)")
        print("\n[History]")
        if state["history"]:
            pprint(state["history"], width=120)
        else:
            print("(empty)")
        print("=" * 60)

    def _generate_summary(self) -> None:
        """Generate and store a rolling survey summary from full Q&A history."""
        try:
            with self._lock:
                history_snapshot = [dict(item) for item in self.history]

            if not history_snapshot:
                return

            history_lines: list[str] = []
            for i, entry in enumerate(history_snapshot, start=1):
                options = ", ".join(entry.get("options", []))
                history_lines.append(
                    f"{i}. Question: {entry.get('question', '')}\n"
                    f"   Options: {options}\n"
                    f"   Answer: {entry.get('answer', '')}"
                )

            prompt = (
                "Summarize the following survey progress based on all answered questions so far.\n\n"
                f"Session ID: {self.session_id}\n"
                f"Total answered blocks: {len(history_snapshot)}\n\n"
                "Answered Q&A:\n"
                + "\n".join(history_lines)
            )

            client = OpenAI(api_key=self._api_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=200,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are summarizing a survey session context for an automation system. "
                            "Write a neutral 3-5 sentence summary describing what the survey appears "
                            "to be about and what profile the respondent has established so far "
                            "(e.g., demographics answered, topics covered, industries mentioned)."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )

            new_summary = (response.choices[0].message.content or "").strip()
            if not new_summary:
                return

            with self._lock:
                self.summary = new_summary

            logger.info(
                "SurveyContext summary generated session_id=%s summary=%r",
                self.session_id,
                new_summary[:80],
            )
        except Exception:
            # On any error, skip silently by design.
            return


# Integration hint:
# SurveyContext is instantiated once per survey run in survey_solver.py.
# record() is called inside survey_executor.py after each successful action dispatch.
# maybe_update_summary() is called at the end of each page loop iteration.
# get_context_snippet() is injected into prompt_builder.py as a prefix block.
