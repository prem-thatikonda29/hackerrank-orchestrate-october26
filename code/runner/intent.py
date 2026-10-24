"""Cheap, deterministic intent heuristic. The agent decides intent from the request text; this exists for the
rule-based baseline and for the activity-linking stage, which must ignore non-deletion requests."""

import re

NON_DELETION_HINTS = re.compile(
    r"not (?:asking|requesting) (?:for )?(?:deletion|erasure|to (?:delete|erase))|do(?:n't| not) (?:delete|close|erase|remove)|"
    r"without removing|leave (?:the|my) (?:account|profile)|keep (?:the|my) account|continue using|still (?:need|use|want)|"
    r"(?:send|need|get|want) (?:me )?an? copy of|information request|what personal information|download my|portable format|"
    r"marketing lists only|stop the promotional|correct it|misspelled|out of date|change the email|resetting the login", re.I)


def baseline_intent(text: str) -> str:
    return "non_deletion" if NON_DELETION_HINTS.search(text) else "deletion"
