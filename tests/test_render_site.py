"""Layer 1: static-site renderer tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lean_alpha import config, cycle as cy
from lean_alpha import render_site


@pytest.fixture(autouse=True)
def isolated_runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(cy, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(render_site, "RUNS_DIR", tmp_path)
    return tmp_path


def _seed_cycle(tmp_path: Path) -> str:
    """Build a minimal cycle dir on disk for the renderer to consume."""
    state = cy.CycleState(asset="ACHR", horizon="12 months")
    state.record_gate("A2.3", "approved", "[auto-approve]")
    state.record_gate("A5", "approved", "[auto-approve]")
    state.record_gate("A7", "approved", "[auto-approve]")
    state.mark_complete()

    artifacts = state.artifact_dir()
    (artifacts / "A2_1.md").write_text(
        "# A2.1 — Historical Drivers\n\n"
        "Per [MacroTrends](https://www.macrotrends.net/x), TSR was 730%.\n"
    )
    (artifacts / "A2_1.json").write_text(json.dumps({
        "stage": "A2.1", "summary": "TSR 730%",
        "rows": [{"label": "TSR", "value": "730%", "source_url": "https://x", "confidence": "high", "assumed": False}],
    }))
    (artifacts / "A5_1.md").write_text(
        "# A5.1 — Valuation\n\n"
        "Per [A2.1 historical](A2_1.md), the multiple expanded.\n"
    )
    (artifacts / "A5_1.json").write_text(json.dumps({
        "stage": "A5.1", "summary": "valuation",
        "rows": [{"label": "EPS", "value": "$7.20", "source_url": None, "confidence": "medium", "assumed": True}],
    }))

    final_md = (
        "# ACHR — Lean Agentic Alpha Report\n\n"
        "## Executive Summary\n\n"
        "Expected return [+12%](./artifacts/A5_1.md), driven by "
        "[FAA progress](./artifacts/A2_1.md).\n"
    )
    state.final_report_path_md().write_text(final_md)
    state.final_report_path_json().write_text(json.dumps({"asset": "ACHR"}))
    return state.cycle_id


def test_render_creates_index_and_artifact_pages(isolated_runs_dir):
    cycle_id = _seed_cycle(isolated_runs_dir)
    site = render_site.render_cycle_to_site(cycle_id)
    assert (site / "index.html").exists()
    assert (site / "style.css").exists()
    assert (site / "artifacts" / "A2_1.html").exists()
    assert (site / "artifacts" / "A5_1.html").exists()
    # JSON sidecars are copied so they're reachable from the static site
    assert (site / "artifacts" / "A2_1.json").exists()


def test_md_links_become_html_links_in_index(isolated_runs_dir):
    cycle_id = _seed_cycle(isolated_runs_dir)
    site = render_site.render_cycle_to_site(cycle_id)
    index = (site / "index.html").read_text()
    # final_report.md had ./artifacts/A5_1.md → should now be .html
    assert 'href="./artifacts/A5_1.html"' in index
    assert 'href="./artifacts/A2_1.html"' in index
    # No raw .md links left for relative paths
    assert ".md" not in index.split("</head>")[1].split("</body>")[0].replace(".md\"", "").replace(".markdown", "")


def test_external_urls_preserved(isolated_runs_dir):
    cycle_id = _seed_cycle(isolated_runs_dir)
    site = render_site.render_cycle_to_site(cycle_id)
    a2_1 = (site / "artifacts" / "A2_1.html").read_text()
    # https links must NOT be rewritten — they have no .md suffix
    assert "https://www.macrotrends.net/x" in a2_1


def test_inter_artifact_links_rewritten(isolated_runs_dir):
    cycle_id = _seed_cycle(isolated_runs_dir)
    site = render_site.render_cycle_to_site(cycle_id)
    a5_1 = (site / "artifacts" / "A5_1.html").read_text()
    # A5_1.md had [A2.1 historical](A2_1.md) — same dir → A2_1.html
    assert 'href="A2_1.html"' in a5_1


def test_sidebar_lists_all_stages(isolated_runs_dir):
    cycle_id = _seed_cycle(isolated_runs_dir)
    site = render_site.render_cycle_to_site(cycle_id)
    index = (site / "index.html").read_text()
    assert "A2.1" in index and "A5.1" in index
    assert "Final Report" in index


def test_badges_show_gate_status(isolated_runs_dir):
    cycle_id = _seed_cycle(isolated_runs_dir)
    site = render_site.render_cycle_to_site(cycle_id)
    index = (site / "index.html").read_text()
    assert "gate A2.3: approved" in index
    assert "gate A5: approved" in index
    assert "gate A7: approved" in index


def test_artifact_badges_show_citation_count(isolated_runs_dir):
    cycle_id = _seed_cycle(isolated_runs_dir)
    site = render_site.render_cycle_to_site(cycle_id)
    a2_1 = (site / "artifacts" / "A2_1.html").read_text()
    assert "1/1 cited" in a2_1
    a5_1 = (site / "artifacts" / "A5_1.html").read_text()
    assert "0/1 cited" in a5_1
    assert "1 assumed" in a5_1


def test_stage_label_handles_versions(isolated_runs_dir):
    label, key = render_site._stage_label_and_sort("A3_2_v2")
    assert label == "A3.2 v2"
    assert key == (3, 2, 2)
    label, key = render_site._stage_label_and_sort("A7")
    assert label == "A7"
    assert key == (7, 0, 0)
    label, key = render_site._stage_label_and_sort("A4_1")
    assert label == "A4.1"
    assert key == (4, 1, 0)


def test_cycle_not_found_raises(isolated_runs_dir):
    with pytest.raises(FileNotFoundError):
        render_site.render_cycle_to_site("cycle_nonexistent")
