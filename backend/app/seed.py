"""Seed sample customers and conversations (metadata only - no outputs are seeded).

    python -m app.seed
"""
from .api.meta import load_samples


def seed() -> dict:
    from .api.conversations import upsert_conversation

    refs, convs = set(), 0
    for x in load_samples():
        c = x.get("customer") or {}
        upsert_conversation(
            x["text"], c.get("reference"), c.get("name"), x["sample_id"], email=c.get("email"), phone=c.get("phone")
        )
        if c.get("reference"):
            refs.add(c["reference"])
        convs += 1
    return {"customers": len(refs), "conversations": convs}


if __name__ == "__main__":
    print(seed())
