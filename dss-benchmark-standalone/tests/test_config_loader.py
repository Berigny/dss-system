"""Smoke tests for the adapter configuration loader."""
from __future__ import annotations

import os
from pathlib import Path

from config.loader import get_adapter_config, load_config


def test_load_default_config() -> None:
    cfg = load_config()
    assert "adapters" in cfg
    assert "faiss" in cfg["adapters"]
    assert "chroma" in cfg["adapters"]


def test_get_adapter_config() -> None:
    cfg = get_adapter_config("qdrant")
    assert cfg["collection_name"] == "dss_eval"
    assert cfg["vector_size"] == 384


def test_env_expansion(tmp_path: Path) -> None:
    os.environ["DSS_TEST_URL"] = "http://test.example:8080"
    path = tmp_path / "adapters.yaml"
    path.write_text("adapters:\n  weaviate:\n    url: ${DSS_TEST_URL}\n")
    cfg = load_config(path)
    assert cfg["adapters"]["weaviate"]["url"] == "http://test.example:8080"
    del os.environ["DSS_TEST_URL"]
