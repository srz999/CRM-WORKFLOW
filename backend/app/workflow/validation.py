"""Deterministic validation of extracted issues against the source conversation.

Guards against fabricated evidence, invented customer references and invented deadlines.
"""
import hashlib
import re

from ..agents.schemas import ExtractionOutput

_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


def normalize(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def _norm_quote(q: str) -> str:
    return normalize(q).strip(" .,'\"…").replace("...", "").strip()


def appears_in(fragment: str, text: str) -> bool:
    frag = _norm_quote(fragment)
    return bool(frag) and frag in normalize(text)


def issue_key(conversation_id: int, ordinal: int, summary: str) -> str:
    basis = f"{conversation_id}:{ordinal}:{re.sub(r'[^a-z0-9 ]', '', normalize(summary))}"
    return hashlib.sha256(basis.encode()).hexdigest()[:40]


def conversation_hash(text: str, customer_ref: str | None) -> str:
    return hashlib.sha256(f"{customer_ref or ''}\n{normalize(text)}".encode()).hexdigest()


def deterministic_deadline_hours(deadline_text: str) -> int | None:
    t = normalize(deadline_text)
    if re.search(r"\b(today|tonight|this (morning|afternoon|evening)|end of (the )?day)\b", t):
        return 12
    if re.search(r"\btomorrow\b", t):
        return 24
    m = re.search(r"\bin (\d+|one|two|three|four|five|six|seven) (hours?|days?|weeks?)\b", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _WORD_NUM[m.group(1)]
        unit = m.group(2)
        return n if unit.startswith("hour") else n * 24 if unit.startswith("day") else n * 168
    if re.search(r"\bnext week\b", t):
        return 168
    return None


def validate_extraction(
    output: ExtractionOutput, *, conversation_text: str, conversation_customer_ref: str | None, max_issues: int
) -> tuple[list[dict], list[str]]:
    """Returns (validated issues, notes about dropped/adjusted items)."""
    notes: list[str] = []
    issues: list[dict] = []
    seen: set[str] = set()

    for idx, item in enumerate(output.issues):
        key = re.sub(r"[^a-z0-9 ]", "", normalize(item.summary))
        if key in seen:
            notes.append(f"Dropped duplicate issue #{idx + 1}: {item.summary[:60]}")
            continue
        seen.add(key)
        if len(issues) >= max_issues:
            notes.append(f"Issue limit ({max_issues}) reached; remaining issues dropped")
            break

        review: list[str] = []

        verified = [q for q in item.evidence if appears_in(q, conversation_text)]
        rejected = [q for q in item.evidence if q not in verified]
        if rejected:
            notes.append(f"Issue #{idx + 1}: {len(rejected)} evidence quote(s) not found in conversation were removed")
        if not verified:
            review.append("No supporting evidence could be verified in the original conversation")

        # Customer reference must literally appear in the text, otherwise fall back to the known customer.
        ref = item.customer_ref.strip() if item.customer_ref else None
        if ref and not appears_in(ref, conversation_text) and ref != conversation_customer_ref:
            notes.append(f"Issue #{idx + 1}: customer reference {ref!r} not present in text; ignored")
            ref = None
        ref = ref or conversation_customer_ref
        if not ref:
            review.append("Customer could not be identified from the conversation")

        deadline_hours = None
        deadline_text = item.deadline_text
        if deadline_text:
            if appears_in(deadline_text, conversation_text):
                deadline_hours = deterministic_deadline_hours(deadline_text)
                if deadline_hours is None:
                    deadline_hours = item.deadline_hours
            else:
                notes.append(f"Issue #{idx + 1}: deadline {deadline_text!r} not found in text; ignored")
                deadline_text = None

        if item.missing_information:
            review.append("Clarification needed from customer: " + ", ".join(item.missing_information))

        issues.append(
            {
                "summary": item.summary.strip(),
                "evidence": verified,
                "customer_ref": ref,
                "customer_impact": item.customer_impact,
                "requested_action": item.requested_action,
                "missing_information": item.missing_information,
                "facts": {
                    "affected_users": item.affected_users,
                    "amount": item.amount,
                    "deadline_text": deadline_text,
                    "deadline_hours": deadline_hours,
                },
                "review_reasons": review,
            }
        )
    return issues, notes
