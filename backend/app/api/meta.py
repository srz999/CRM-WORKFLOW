import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import TICKET_STATUSES
from ..rules.config import taxonomy, teams
from ..schemas import MetaOut, SampleOut, SeedResult

router = APIRouter(prefix="/api", tags=["meta"])

SAMPLES_PATH = Path(__file__).resolve().parents[2] / "data" / "samples.json"


def load_samples() -> list[dict]:
    with open(SAMPLES_PATH, encoding="utf-8") as fh:
        return json.load(fh)["samples"]


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, "Database unavailable") from e
    return {"status": "ok", "database": "ok", "llm_configured": bool(get_settings().openai_api_key)}


@router.get("/meta", response_model=MetaOut)
def meta():
    s = get_settings()
    cats = [
        {"key": k, "label": v["label"], "subcategories": list(v["subcategories"].keys())}
        for k, v in taxonomy()["categories"].items()
    ]
    return MetaOut(categories=cats, teams=teams(), statuses=list(TICKET_STATUSES),
                   priorities=["URGENT", "MEDIUM", "LOW"], llm_configured=bool(s.openai_api_key), model=s.openai_model)


@router.get("/samples", response_model=list[SampleOut])
def samples():
    return [
        SampleOut(sample_id=x["sample_id"], title=x["title"], customer_ref=(x.get("customer") or {}).get("reference"),
                  customer_name=(x.get("customer") or {}).get("name"), text=x["text"])
        for x in load_samples()
    ]


@router.post("/samples/seed", response_model=SeedResult)
def seed_samples():
    from ..seed import seed

    return SeedResult(**seed())
