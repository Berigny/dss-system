"""Tests for shared_types.openrouter_client."""

from __future__ import annotations

from shared_types.openrouter_client import normalise_openrouter_response


def test_normalise_openrouter_response_full() -> None:
    raw = {
        "choices": [
            {
                "message": {
                    "content": "  Hello, world!  ",
                }
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "total_cost": 0.0001,
        },
    }
    out = normalise_openrouter_response(raw, "openai/gpt-4o")
    assert out["text"] == "Hello, world!"
    assert out["model"] == "openai/gpt-4o"
    assert out["tokens"] == {"prompt": 10, "completion": 5, "total": 15}
    assert out["cost"] == 0.0001
    assert "raw" in out


def test_normalise_openrouter_response_multimodal_list() -> None:
    raw = {
        "choices": [{"message": {"content": [{"type": "text", "text": "hi"}, {"type": "image_url"}]}}],
        "usage": {},
    }
    out = normalise_openrouter_response(raw, "openai/gpt-4o")
    assert out["text"] == "hi"
    assert out["cost"] == 0.0


def test_normalise_openrouter_response_error() -> None:
    out = normalise_openrouter_response({}, "openai/gpt-4o")
    assert out["text"] == ""
    assert out["model"] == "openai/gpt-4o"
    assert "error" in out
