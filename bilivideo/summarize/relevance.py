"""Conservative checks for summaries assembled from unrelated transcripts."""

from __future__ import annotations

import re

_CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_TERM = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*", re.IGNORECASE)
_GENERIC_TERMS = {"pro", "max", "plus", "ultra", "mini", "lite", "flash", "video", "news", "review"}


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
    """Flag specific Chinese or mixed-language titles absent from substantial text."""

    runs = [match.group() for match in _CHINESE_RUN.finditer(title)]
    specific_runs = [run for run in runs if len(run) >= 4]
    if len(text.strip()) < 120:
        return False
    title_phrases = _grams(title, 3)
    latin_terms = {
        term.casefold() for term in _LATIN_TERM.findall(title)
        if len(re.sub(r"[^a-z]", "", term, flags=re.IGNORECASE)) >= 3
        and term.casefold() not in _GENERIC_TERMS
    }
    specific = (
        (len(specific_runs) >= 2 and len(title_phrases) >= 6)
        or bool(latin_terms and (specific_runs or re.search(r"\d", title)))
    )
    if not specific:
        return False
    text_terms = {term.casefold() for term in _LATIN_TERM.findall(text)}
    return not (title_phrases & _grams(text, 3) or latin_terms & text_terms)


def summary_conflicts_with_transcript(transcript: str, markdown: str) -> bool:
    """Catch gross topic swaps, not ordinary paraphrasing or brief summaries."""

    body = _body(markdown)
    if len(transcript) < 200 or len(body) < 120:
        return False
    summary_phrases = _grams(body, 3)
    if len(summary_phrases) < 20:
        return False
    return len(summary_phrases & _grams(transcript, 3)) < 2
