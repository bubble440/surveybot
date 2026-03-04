from __future__ import annotations

import re
import unicodedata

_EXACT_COUNT_WORD_TO_INT = {
    "one": 1,
    "un": 1,
    "une": 1,
    "two": 2,
    "deux": 2,
    "three": 3,
    "trois": 3,
    "four": 4,
    "quatre": 4,
    "five": 5,
    "cinq": 5,
}


def _norm_folded_lc(text: str | None) -> str:
    base = unicodedata.normalize("NFKD", text or "")
    no_marks = "".join(ch for ch in base if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", no_marks).strip().lower()


_EXPLICIT_MULTI_PATTERNS = [
    # FR
    r"\bplusieurs\s+(?:reponses?|choix|options?)\s+(?:possibles?|autorisees?)\b",
    r"\b(?:selectionnez|selectionner|choisissez|cochez)\s+tout(?:es)?\s+ce\s+qui\s+s['’]?applique\b",
    r"\b(?:selectionnez|selectionner|choisissez|cochez)\s+toutes?\s+les\s+(?:reponses?|options?)\s+qui\s+s['’]?appliquent?\b",
    r"\b(?:selectionnez|selectionner|choisissez|cochez)\s+toutes?\s+les\s+reponses?\s+approprie(?:e|es)s?\b",
    r"\bselect(?:ion)?nez\s+toutes?\s+les\s+options\s+valides\b",
    r"\bselectionner\s+plusieurs\b",
    r"\bvous\s+pouvez\s+cocher\s+plusieurs\b",
    r"\bvous\s+pouvez\s+(?:choisir|selectionner|cocher)\s+plusieurs\s+(?:reponses?|choix|options?)\b",
    r"\bvous\s+pouvez\s+choisir\s+plusieurs\s+reponses?\s+parmi\s+celles\s+proposees\b",
    # EN
    r"\b(?:select|choose|check)\s+all\s+that\s+apply\b",
    r"\bmultiple\s+(?:answers?|choices?|options?)\s+(?:allowed|possible)\b",
    r"\byou\s+may\s+select\s+more\s+than\s+one\b",
]


def has_explicit_multi_indicator(question_text: str | None) -> bool:
    text = _norm_folded_lc(question_text)
    if not text:
        return False
    return any(re.search(pat, text) for pat in _EXPLICIT_MULTI_PATTERNS)


def explicit_exact_count_from_question(question_text: str | None) -> int | None:
    text = _norm_folded_lc(question_text)
    if not text:
        return None

    patterns = [
        r"\bexact(?:ement|ly)?\s+(\d+)\b",
        r"\b(?:select|choose|pick|check)\s+(\d+)\b",
        r"\b(?:selectionnez|selectionner|choisissez|cochez)\s+(\d+)\b",
        r"\b(?:select|choose|pick|check)\s+(?:exactly\s+)?(one|two|three|four|five)\b",
        r"\b(?:selectionnez|selectionner|choisissez|cochez)\s+(?:exactement\s+)?(un|une|deux|trois|quatre|cinq)\b",
        r"\bles\s+(un|une|deux|trois|quatre|cinq)\s+r[ée]ponses?\b",
        r"\bles\s+(un|une|deux|trois|quatre|cinq)\b",
        r"\bthe\s+(one|two|three|four|five)\s+(?:answers?|choices?|options?)\b",
    ]

    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if not m:
            continue
        raw = (m.group(1) or "").strip().lower()
        if raw.isdigit():
            n = int(raw)
        else:
            n = _EXACT_COUNT_WORD_TO_INT.get(raw)
        if n and n >= 1:
            return n

    return None
