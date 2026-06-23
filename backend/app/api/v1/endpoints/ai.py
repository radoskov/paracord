"""Local AI, summaries, embeddings, and topic modeling endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/summaries")
def create_summary_job() -> dict[str, str]:
    """Queue a local summary job."""
    return {"status": "todo"}


@router.post("/topics")
def create_topic_model_job() -> dict[str, str]:
    """Queue a BERTopic/model-based topic job for a scope."""
    return {"status": "todo"}
