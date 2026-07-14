"""Tests for chat-surface model display-name conversion."""

import app as app_module


def test_dedupe_models_converts_binding_in_name_field():
    """Middleware may put the raw binding id in the name field while id is the OpenRouter path."""
    models = [
        {"id": "anthropic/claude-fable-5", "name": "binding:chat:anthropic-claude-fable-5"},
        {"id": "google/gemini-flash-latest", "name": "binding:chat:google-gemini-flash-latest"},
    ]
    result = app_module._dedupe_models(models)
    assert result == [
        {"id": "anthropic/claude-fable-5", "name": "Anthropic: Claude Fable 5"},
        {"id": "google/gemini-flash-latest", "name": "Google: Gemini Flash Latest"},
    ]


def test_dedupe_models_converts_binding_in_id_field():
    models = [
        {"id": "binding:chat:openai-gpt-5", "name": "binding:chat:openai-gpt-5"},
    ]
    result = app_module._dedupe_models(models)
    assert result == [{"id": "binding:chat:openai-gpt-5", "name": "Openai: Gpt 5"}]


def test_dedupe_models_preserves_existing_human_names():
    models = [
        {"id": "x-ai/grok-4.3", "name": "xAI: Grok 4.3"},
    ]
    result = app_module._dedupe_models(models)
    assert result == [{"id": "x-ai/grok-4.3", "name": "xAI: Grok 4.3"}]
