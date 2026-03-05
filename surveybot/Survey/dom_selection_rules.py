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

_EXCLUSIVE_OPTION_PREFIXES = [
    "aucun",
    "aucune",
    "aucune de ces",
    "none",
    "none of the above",
    "pas applicable",
    "n/a",
    "autre",
    "other",
    "je ne sais pas",
    "prefer not",
]

_SECTOR_ACTIVITY_PATTERNS = [
    # FR
    r"\bsecteurs?\s+d['’]?activite\b",
    r"\bdomaines?\s+d['’]?activite\b",
    r"\bsecteurs?\s+professionnels?\b",
    r"\btravaillez[-\s]?vous\s+pour\s+une\s+entreprise\b",
    r"\btravaille\s+pour\s+une\s+entreprise\b",
    r"\btravaille[-\s]?t[-\s]?elle\s+pour\s+une\s+entreprise\b",
    r"\bfoyer\s+travaille\b",
    # EN
    r"\bindustr(?:y|ies)\b",
    r"\bsectors?\s+of\s+activity\b",
    r"\bwork\s+for\s+a\s+company\b",
    r"\bwork\s+in\s+the\s+following\b",
    r"\bhousehold\s+work\b",
]


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


def is_sector_activity_question(question_text: str | None) -> bool:
    text = _norm_folded_lc(question_text)
    if not text:
        return False
    return any(re.search(pat, text) for pat in _SECTOR_ACTIVITY_PATTERNS)


def _is_exclusive_option_text(option_text: str | None) -> bool:
    folded = _norm_folded_lc(option_text)
    if not folded:
        return False
    return any(folded.startswith(prefix) for prefix in _EXCLUSIVE_OPTION_PREFIXES)


def compute_min_select(question_text: str | None, options: list[str], max_select: int) -> int:
    bounded_max = max(1, int(max_select or 1))

    if is_sector_activity_question(question_text):
        return bounded_max

    exact_count = explicit_exact_count_from_question(question_text)
    if exact_count is not None:
        return max(1, min(int(exact_count), bounded_max))

    if has_explicit_multi_indicator(question_text):
        return max(1, min(3, bounded_max))

    return 1


def compute_checkbox_max_select(options: list[str], question_text: str | None = None) -> int:
    if not options:
        return 1

    explicit_exact_count = explicit_exact_count_from_question(question_text)
    exclusive_count = sum(1 for opt in options if _is_exclusive_option_text(opt))
    natural_max = max(1, len(options) - exclusive_count)

    if explicit_exact_count is not None:
        return max(1, min(int(explicit_exact_count), natural_max))

    return natural_max
