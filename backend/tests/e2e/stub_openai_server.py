"""TEST-ONLY OpenAI-compatible stub for local end-to-end checks without an API key.

Serves the canned mock responses (tests/fixtures/mock_llm) over the real
/v1/chat/completions wire format, so the production OpenAILLM client, the LangGraph
workflow, the API and the dashboard can be exercised end to end. It only knows the
sample conversations. Never use it as a real LLM.

    cd backend && uvicorn tests.e2e.stub_openai_server:app --port 8099
    # then start the API with OPENAI_API_KEY=stub OPENAI_BASE_URL=http://localhost:8099/v1
"""
import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tests.fake_llm import FakeLLM

app = FastAPI()
fake = FakeLLM()
TASK_TO_AGENT = {
    "extract_issues": "extraction",
    "classify_issue": "classification",
    "prioritize_issue": "prioritization",
    "recommend_resolution": "resolution",
}


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    payload = json.loads(body["messages"][-1]["content"])
    agent = TASK_TO_AGENT[payload["task"]]
    try:
        content = json.dumps(fake.complete_json(agent=agent, system="", payload=payload))
    except Exception:  # noqa: BLE001 - unknown conversation: behave like a confused model
        content = "I'm not sure."
    return JSONResponse({
        "id": "stub", "object": "chat.completion", "created": int(time.time()), "model": body.get("model", "stub"),
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })
