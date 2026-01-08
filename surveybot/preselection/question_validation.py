# preselection/question_validation.py

from dataclasses import dataclass
from typing import Optional
from Management.guards.sensitive_question_guard import is_sensitive_question
from Management.guards.survey_difficulty_guard import detect_strict_survey

@dataclass
class QuestionDecision:
    action: str
    reason: Optional[str] = None

# actions possibles :
# - "CONTINUE"
# - "SKIP"
# - "DISQUALIFIED"
# - "NOT_RETURNED"
# - "BLOCKED"
# - "RESTART_SURVEY"

def validate_question(question_text: str, page_text: str) -> QuestionDecision:
    if is_sensitive_question(question_text):
        return QuestionDecision("SKIP", "sensitive_question")

    if "tu n'as pas été qualifié" in question_text.lower():
        return QuestionDecision("DISQUALIFIED", "qualification_failed")

    return QuestionDecision("CONTINUE")
