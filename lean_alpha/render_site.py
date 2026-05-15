"""Static-site renderer for a cycle.

Produces a self-contained HTML bundle from a cycle directory:

    runs/cycle_<id>/
      site/
        index.html              ← final_report.md rendered
        artifacts/
          A2_1.html             ← each .md rendered
          A2_1.json             ← raw JSON copied alongside (clickable)
          ...
        style.css               ← embedded minimal-clean stylesheet

Usage:
    python -m lean_alpha.cli render-site <cycle_id>

Then drag the `site/` folder into Vercel/Netlify, or zip + email.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import markdown

from .config import RUNS_DIR


@dataclass
class _ArtifactEntry:
    stage_label: str   # "A2.1" or "A2.3 v2"
    md_filename: str   # "A2_1.md"
    html_filename: str # "A2_1.html"
    json_filename: str # "A2_1.json"
    sort_key: tuple    # for stable nav ordering


# Regex for stage filename: A2_1, A2_3_v2, A7
_STAGE_RX = re.compile(r"^([A-Z]\d+)(?:_(\d+))?(?:_v(\d+))?$")


def _stage_label_and_sort(name: str) -> tuple[str, tuple]:
    """A2_1 → ('A2.1', (2,1,0)); A2_3_v2 → ('A2.3 v2', (2,3,2)); A7 → ('A7', (7,0,0))."""
    m = _STAGE_RX.match(name)
    if not m:
        return name, (99, 0, 0)
    major, minor, version = m.group(1), m.group(2), m.group(3)
    major_num = int(major[1:])
    minor_num = int(minor) if minor else 0
    version_num = int(version) if version else 0
    label = f"{major}{'.' + minor if minor else ''}{' v' + version if version else ''}"
    return label, (major_num, minor_num, version_num)


def _list_artifacts(cycle_dir: Path) -> list[_ArtifactEntry]:
    out: list[_ArtifactEntry] = []
    artifacts = cycle_dir / "artifacts"
    if not artifacts.exists():
        return out
    for md_path in sorted(artifacts.glob("*.md")):
        stem = md_path.stem
        label, sort_key = _stage_label_and_sort(stem)
        out.append(
            _ArtifactEntry(
                stage_label=label,
                md_filename=md_path.name,
                html_filename=f"{stem}.html",
                json_filename=f"{stem}.json",
                sort_key=sort_key,
            )
        )
    out.sort(key=lambda e: e.sort_key)
    return out


def _rewrite_md_links_to_html(html: str) -> str:
    """Rewrite local relative .md hrefs to .html.

    Matches href="<anything>.md" but NOT https?:// URLs.
    """
    pattern = re.compile(r'href="((?!https?://)[^"]+)\.md(#[^"]*)?"')

    def _sub(m: re.Match) -> str:
        path = m.group(1)
        anchor = m.group(2) or ""
        return f'href="{path}.html{anchor}"'

    return pattern.sub(_sub, html)


def _markdown_to_html(text: str) -> str:
    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "sane_lists",
            "attr_list",
            "footnotes",
        ],
        output_format="html5",
    )
    return md.convert(text)


def _sidebar_html(
    artifacts: Iterable[_ArtifactEntry], current: str, base_prefix: str
) -> str:
    """Sidebar with index + per-artifact nav. `current` is the current page id
    (e.g. 'index' or 'A2_1'); we mark that link as active. `base_prefix` is
    the relative path to the site root (\"\" from index, \"../\" from artifacts/)."""
    items: list[str] = []
    items.append(
        f'<li class="nav-item{" active" if current == "index" else ""}">'
        f'<a href="{base_prefix}index.html">Final Report</a></li>'
    )
    items.append('<li class="nav-section">Specialist artifacts</li>')
    for a in artifacts:
        a_id = a.html_filename.removesuffix(".html")
        cls = " active" if current == a_id else ""
        items.append(
            f'<li class="nav-item{cls}">'
            f'<a href="{base_prefix}artifacts/{a.html_filename}">{a.stage_label}</a></li>'
        )
    return f'<ul class="nav">{"".join(items)}</ul>'


def _page_template(
    *,
    title: str,
    body_html: str,
    sidebar: str,
    cycle_id: str,
    asset: str,
    horizon: str,
    badges_html: str,
    raw_json_path: str | None,
    style_path: str,
) -> str:
    json_link = (
        f'<a class="raw-link" href="{raw_json_path}" target="_blank">view raw JSON</a>'
        if raw_json_path
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — {asset}</title>
<link rel="stylesheet" href="{style_path}">
</head>
<body>
<aside class="sidebar">
  <div class="brand">
    <div class="brand-name">Lean Agentic Alpha</div>
    <div class="brand-asset">{asset}</div>
    <div class="brand-meta">{horizon} · <code>{cycle_id}</code></div>
  </div>
  {sidebar}
</aside>
<main class="content">
  <header class="page-head">
    <h1 class="page-title">{title}</h1>
    <div class="page-meta">{badges_html} {json_link}</div>
  </header>
  <article class="markdown">
    {body_html}
  </article>
  <footer class="page-foot">
    <div>Generated by Lean Agentic Alpha · cycle <code>{cycle_id}</code></div>
  </footer>
</main>
</body>
</html>
"""


def _badge_html(json_path: Path | None) -> str:
    """Citation %, source count, assumed count from the JSON sidecar."""
    if json_path is None or not json_path.exists():
        return ""
    try:
        data = json.loads(json_path.read_text())
    except Exception:  # noqa: BLE001
        return ""
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        return ""
    total = len(rows)
    cited = sum(
        1 for r in rows if isinstance(r, dict) and r.get("source_url") and not r.get("assumed", False)
    )
    assumed = sum(1 for r in rows if isinstance(r, dict) and r.get("assumed", False))
    parts = [
        f'<span class="badge badge-cited">{cited}/{total} cited</span>',
    ]
    if assumed:
        parts.append(f'<span class="badge badge-assumed">{assumed} assumed</span>')
    return "".join(parts)


def _index_badges(cycle_state: dict, citation_pct: float | None) -> str:
    parts: list[str] = []
    gates = cycle_state.get("gates", {})
    for name, gs in gates.items():
        st = gs.get("status", "?")
        cls = "ok" if st == "approved" else ("warn" if st == "iterate" else "muted")
        parts.append(f'<span class="badge badge-{cls}">gate {name}: {st}</span>')
    if citation_pct is not None:
        parts.append(f'<span class="badge badge-cited">verified cells {citation_pct:.0f}%</span>')
    return "".join(parts)


_STYLE = """\
:root {
  --serif: "Charter", "Iowan Old Style", "Palatino", "Georgia", serif;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", "Roboto", sans-serif;
  --mono: ui-monospace, "SF Mono", "Menlo", "Consolas", monospace;
  --bg: #fafaf7;
  --bg-side: #f3f1ea;
  --fg: #1a1a1a;
  --muted: #666;
  --rule: #d8d4c8;
  --accent: #1d4ed8;
  --accent-soft: #e0e7ff;
  --warn: #b45309;
  --warn-soft: #fef3c7;
  --ok: #166534;
  --ok-soft: #dcfce7;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: var(--sans);
  font-size: 16px;
  line-height: 1.6;
  color: var(--fg);
  background: var(--bg);
  display: grid;
  grid-template-columns: 260px 1fr;
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code { font-family: var(--mono); font-size: 0.92em; background: rgba(0,0,0,0.04); padding: 0.05em 0.3em; border-radius: 3px; }
pre code { padding: 0; background: none; }

/* sidebar */
.sidebar {
  background: var(--bg-side);
  border-right: 1px solid var(--rule);
  padding: 24px 18px;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}
.brand { margin-bottom: 22px; padding-bottom: 18px; border-bottom: 1px solid var(--rule); }
.brand-name { font-family: var(--serif); font-size: 1.05rem; font-weight: 600; }
.brand-asset { font-family: var(--serif); font-size: 1.4rem; font-weight: 700; margin-top: 4px; }
.brand-meta { font-size: 0.78rem; color: var(--muted); margin-top: 4px; }
.brand-meta code { font-size: 0.82em; }

ul.nav { list-style: none; padding: 0; margin: 0; }
.nav-item { padding: 4px 0; }
.nav-item a {
  display: block;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 0.92rem;
  color: var(--fg);
}
.nav-item.active a, .nav-item a:hover { background: var(--accent-soft); color: var(--accent); }
.nav-section {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  margin: 18px 8px 6px;
  font-weight: 600;
}

/* main content */
.content {
  padding: 40px 56px 80px;
  max-width: 880px;
}
.page-head { margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--rule); }
.page-title { font-family: var(--serif); font-size: 2rem; font-weight: 700; margin: 0 0 12px; line-height: 1.2; }
.page-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 0.85rem; }

.badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 0.78rem;
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 500;
}
.badge-ok       { background: var(--ok-soft);     color: var(--ok); }
.badge-warn     { background: var(--warn-soft);   color: var(--warn); }
.badge-muted    { background: rgba(0,0,0,0.05);   color: var(--muted); }
.badge-cited    { background: var(--ok-soft);     color: var(--ok); }
.badge-assumed  { background: var(--warn-soft);   color: var(--warn); }

.raw-link {
  font-size: 0.78rem;
  color: var(--muted);
  margin-left: auto;
}

/* article body */
.markdown h1, .markdown h2, .markdown h3, .markdown h4 {
  font-family: var(--serif);
  font-weight: 700;
  line-height: 1.25;
  margin-top: 1.6em;
}
.markdown h2 { font-size: 1.45rem; padding-bottom: 4px; border-bottom: 1px solid var(--rule); }
.markdown h3 { font-size: 1.18rem; }
.markdown h4 { font-size: 1.02rem; }
.markdown p, .markdown li { font-size: 1rem; }

.markdown table {
  border-collapse: collapse;
  margin: 1em 0;
  width: 100%;
  font-size: 0.93rem;
}
.markdown th, .markdown td {
  text-align: left;
  padding: 6px 10px;
  border-bottom: 1px solid var(--rule);
  vertical-align: top;
}
.markdown th { background: var(--bg-side); font-weight: 600; }
.markdown td:has(+ td:first-of-type) { font-family: var(--mono); }

.markdown blockquote {
  border-left: 3px solid var(--rule);
  padding-left: 12px;
  margin-left: 0;
  color: var(--muted);
}

.markdown pre {
  background: #f5f3ec;
  border: 1px solid var(--rule);
  border-radius: 4px;
  padding: 12px 16px;
  overflow-x: auto;
  font-size: 0.86rem;
  line-height: 1.5;
}

.page-foot {
  margin-top: 48px;
  padding-top: 18px;
  border-top: 1px solid var(--rule);
  font-size: 0.78rem;
  color: var(--muted);
}

/* responsive: stack sidebar on top on narrow screens */
@media (max-width: 760px) {
  body { grid-template-columns: 1fr; }
  .sidebar { position: relative; height: auto; }
  .content { padding: 24px 18px 60px; }
}
"""


def render_cycle_to_site(cycle_id: str) -> Path:
    """Render a cycle directory to a static HTML site. Returns the site path."""
    cycle_dir = RUNS_DIR / cycle_id
    if not cycle_dir.exists():
        raise FileNotFoundError(f"No cycle dir at {cycle_dir}")

    site = cycle_dir / "site"
    site.mkdir(exist_ok=True)
    (site / "artifacts").mkdir(exist_ok=True)

    # Stylesheet
    (site / "style.css").write_text(_STYLE)

    cycle_state = json.loads((cycle_dir / "cycle.json").read_text())
    asset = cycle_state.get("asset", "?")
    horizon = cycle_state.get("horizon", "?")

    artifacts = _list_artifacts(cycle_dir)
    sidebar_index = _sidebar_html(artifacts, current="index", base_prefix="")

    # 1. final_report.md → index.html
    final_md_path = cycle_dir / "final_report.md"
    if final_md_path.exists():
        body_md = final_md_path.read_text()
        body_html = _rewrite_md_links_to_html(_markdown_to_html(body_md))
        # Compute citation % from a7.json if present
        citation_pct: float | None = None
        a7_json = cycle_dir / "artifacts" / "A7.json"
        if a7_json.exists():
            try:
                a7 = json.loads(a7_json.read_text())
                # Try the explicit field; fall back to row-level computation
                for r in a7.get("rows", []):
                    extras = r.get("extras", {}) or {}
                    p = (extras.get("payload") or {}).get("verified_cells_pct")
                    if isinstance(p, (int, float)):
                        citation_pct = float(p)
                        break
            except Exception:  # noqa: BLE001
                pass
        index_html = _page_template(
            title="Final Report",
            body_html=body_html,
            sidebar=sidebar_index,
            cycle_id=cycle_id,
            asset=asset,
            horizon=horizon,
            badges_html=_index_badges(cycle_state, citation_pct),
            raw_json_path=None,
            style_path="style.css",
        )
        (site / "index.html").write_text(index_html)

    # 2. each artifact .md → artifacts/X.html
    for entry in artifacts:
        md_text = (cycle_dir / "artifacts" / entry.md_filename).read_text()
        body_html = _rewrite_md_links_to_html(_markdown_to_html(md_text))
        sidebar = _sidebar_html(
            artifacts, current=entry.html_filename.removesuffix(".html"), base_prefix="../"
        )
        json_path = cycle_dir / "artifacts" / entry.json_filename
        # Copy JSON next to the HTML so it's downloadable from the site
        if json_path.exists():
            shutil.copy(json_path, site / "artifacts" / entry.json_filename)
        html = _page_template(
            title=f"{entry.stage_label} — {_stage_description(entry.stage_label)}",
            body_html=body_html,
            sidebar=sidebar,
            cycle_id=cycle_id,
            asset=asset,
            horizon=horizon,
            badges_html=_badge_html(json_path if json_path.exists() else None),
            raw_json_path=entry.json_filename if json_path.exists() else None,
            style_path="../style.css",
        )
        (site / "artifacts" / entry.html_filename).write_text(html)

    return site


_STAGE_DESCRIPTIONS: dict[str, str] = {
    "A2.1": "Historical Drivers",
    "A2.2": "Structural Drivers",
    "A2.3": "Forward Drivers (gated)",
    "A3.1": "Macro Base Rates",
    "A3.2": "Sector Growth",
    "A3.3": "Regulatory / Timing",
    "A4.1": "Peer Metrics",
    "A4.2": "Competitive Edges",
    "A4.3": "Probability Ranking",
    "A5.1": "3-Scenario Valuation (gated)",
    "A5.2": "Stress Test",
    "A6.1": "Portfolio Weight",
    "A6.2": "Portfolio Impact",
    "A7":   "Audit Log (signed)",
}


def _stage_description(label: str) -> str:
    base = label.split(" v")[0]  # strip version suffix
    return _STAGE_DESCRIPTIONS.get(base, "Specialist artifact")
