"""Smoke tests for the benchmark overview page."""

from __future__ import annotations

from app import _load_benchmark_publication, render_benchmarks_page


def test_benchmark_overview_renders_averaged_results_with_error_bars() -> None:
    publication = _load_benchmark_publication()
    html = render_benchmarks_page(publication=publication)
    assert "Averaged results with error bars" in html
    assert "benchmark-error-bar" in html


def test_benchmark_overview_renders_visualisations() -> None:
    publication = _load_benchmark_publication()
    html = render_benchmarks_page(publication=publication)
    assert "Visualisations" in html
    assert "<svg" in html
    assert "Recall vs context length" in html
    assert "Latency vs recall trade-off" in html


def test_benchmark_publication_is_loadable() -> None:
    publication = _load_benchmark_publication()
    assert publication["status"] != "error"
    assert isinstance(publication.get("runs"), list)
