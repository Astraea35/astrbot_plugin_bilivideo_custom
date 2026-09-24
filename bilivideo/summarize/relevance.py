"""Conservative checks for summaries assembled from unrelated transcripts."""

from __future__ import annotations

import re

_CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]+")


def _grams(text: str, size: int) -> set[str]:
    return {
        run[i : i + size]
        for match in _CHINESE_RUN.finditer(text)
        for run in (match.group(),)
        for i in range(len(run) - size + 1)
    }


def _body(markdown: str) -> str:
    # The prompt requires the video title in the first line, so it is not evidence.
    return markdown.partition("\n")[2]


def title_conflicts_with_text(title: str, text: str) -> bool:
    """Flag only long, topic-specific titles with no shared phrase in long text."""

    runs = [match.group() for match in _CHINESE_RUN.finditer(title)]
    specific_runs = [run for run in runs if len(run) >= 4]
    if len(specific_runs) < 2 or len(text) < 200:
        return False
    title_phrases = set().union(*(_grams(run, 3) for run in specific_runs))
    return len(title_phrases) >= 6 and not (title_phrases & _grams(text, 3))


def summary_conflicts_with_transcript(transcript: str, markdown: str) -> bool:
    """Catch gross topic swaps, not ordinary paraphrasing or brief summaries."""

    body = _body(markdown)
    if len(transcript) < 200 or len(body) < 120:
        return False
    summary_phrases = _grams(body, 3)
    if len(summary_phrases) < 20:
        return False
    return len(summary_phrases & _grams(transcript, 3)) < 2
