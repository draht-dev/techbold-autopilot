"""LLM JSON extraction helpers."""
from app.agent.llm import _extract_first_json_object


def test_extract_first_json_with_trailing_text():
    text = """{
  "hypotheses": [
    {"title": "nginx down", "reasoning": "failed unit", "evidence": "x", "proposed_check": "systemctl status nginx"}
  ]
}
Here is some extra commentary the model added after the JSON."""
    data = _extract_first_json_object(text)
    assert len(data["hypotheses"]) == 1
    assert data["hypotheses"][0]["title"] == "nginx down"


def test_extract_from_markdown_fence():
    text = """```json
{"confirmed": true, "reasoning": "ok"}
```
"""
    data = _extract_first_json_object(text)
    assert data["confirmed"] is True
