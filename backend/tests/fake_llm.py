"""Scripted stand-in for the OpenAI client (tests only)."""
import copy
import json
from collections import Counter
from pathlib import Path

from app.agents.llm import RetryableLLMError, parse_json_object

from .fixtures.mock_llm.responses import SAMPLES

SAMPLE_TEXT = {
    s["sample_id"]: s["text"]
    for s in json.loads((Path(__file__).parents[1] / "data" / "samples.json").read_text())["samples"]
}


class FakeLLM:
    """Answers each agent call from the canned responses.

    `overrides` maps an agent name to a callable(payload, call_number) returning either a dict
    (used as the response), a str (parsed like raw model output), or raising an exception.
    Returning None falls through to the canned response.
    """

    def __init__(self, overrides=None, extra=None):
        self.calls = Counter()
        self.log: list[tuple[str, dict]] = []
        self.overrides = overrides or {}
        self.by_text: dict[str, list[dict]] = {}
        for sid, issues in {**SAMPLES, **(extra or {})}.items():
            text = SAMPLE_TEXT.get(sid, sid)  # `extra` keys are raw conversation texts
            self.by_text[text.strip()] = issues
        self.by_summary = {i["extraction"]["summary"]: i for issues in self.by_text.values() for i in issues}

    def complete_json(self, *, agent, system, payload):
        self.calls[agent] += 1
        self.log.append((agent, payload))
        if agent in self.overrides:
            res = self.overrides[agent](payload, self.calls[agent])
            if isinstance(res, str):
                return parse_json_object(res, agent)
            if res is not None:
                return copy.deepcopy(res)
        if agent == "extraction":
            issues = self.by_text.get(payload["conversation"].strip())
            if issues is None:
                raise RetryableLLMError("extraction: no canned response")
            return {"issues": [copy.deepcopy(i["extraction"]) for i in issues]}
        canned = self.by_summary[payload["issue"]["summary"]]
        return copy.deepcopy(canned[agent])
