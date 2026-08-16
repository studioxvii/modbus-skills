#!/usr/bin/env python3
"""Generate a small static and agent-readable skill catalog."""

from __future__ import annotations

import csv
import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "skills.json"
ACTIVATION = ROOT / "catalog" / "activation-cases.json"
WORKFLOWS = ROOT / "catalog" / "workflows.json"
RESEARCH = ROOT / "research" / "issues.json"
EXAMPLE = ROOT / "docs" / "examples" / "compile-user-map"
SITE = ROOT / "site"
BASE_URL = os.environ.get("MODBUS_SKILLS_SITE_URL", "https://studioxvii.github.io/modbus-skills").rstrip("/")

PROBLEM_TITLES = {
    "protocol-offset-vs-reference-number": "Address offset vs. reference number",
    "register-area-identity": "The register area is part of the address",
    "word-and-byte-order": "Word and byte order",
    "bit-order-confusion": "Bit order is not byte order",
    "serial-queue-contention": "Serial requests need one queue",
    "poll-rate-control": "Configured and observed poll rates can differ",
    "unit-zero-ambiguity": "Unit identifier 0 is ambiguous",
    "illegal-address-block-boundary": "A read block can cross a valid address range",
    "malformed-or-short-response": "A response can be shorter than it claims",
    "reusable-modpoll-config": "Reusable Modpoll configuration",
    "modscan-repeatable-test-script": "Repeatable ModScan tests",
    "modbus-poll-machine-readable-project": "Versioned Modbus Poll projects",
}

SOURCE_TYPE_LABELS = {
    "official-specification": "Modbus specification",
    "official-tool-documentation": "Official tool documentation",
    "official-tool-example": "Official tool example",
    "project-discussion": "Project discussion",
    "project-documentation": "Project documentation",
    "project-issue": "Project issue",
}

SCHEMA_LABELS = {
    "address-convention-request/v1": "the known source and target address conventions",
    "after-modbus-map/v1": "the reviewed map after the change",
    "after-source-bundle/v1": "the newer source map",
    "before-modbus-map/v1": "the reviewed map before the change",
    "before-source-bundle/v1": "the older source map",
    "candidate-map/v1": "traceable candidate register rows",
    "capture/v1": "a bounded raw-word sample",
    "custom-export/v1": "the requested text or CSV export",
    "export-example/v1": "a documented example of the required format",
    "final-tool-pack-request/v1": "the reviewed endpoint and selected final tools",
    "modbus-address-remap/v1": "a preview of every address conversion",
    "modbus-byte-order-evidence/v1": "all supported byte-order candidates and evidence",
    "modbus-capture-analysis/v1": "a bounded communication and signal report",
    "modbus-compile-request/v1": "a vendor map, measurement list, and output choices",
    "modbus-compile-result/v1": "an organized user map and the requested offline files",
    "modbus-map-diff/v1": "added, removed, moved, and changed points",
    "modbus-map-evidence-review/v1": "grouped evidence exceptions",
    "modbus-map-lint/v1": "deterministic map validation findings",
    "modbus-map/v1": "a reviewed Modbus map",
    "modbus-read-plan/v1": "a bounded Modbus read plan",
    "modbus-review-decisions/v1": "your confirmed engineering decisions",
    "modbus-tool-pack/v1": "the selected read-only tool files",
    "pdf-source/v1": "a vendor PDF or selected page range",
    "probe-request/v1": "one identified point and endpoint for a bounded read",
    "probe-tool-pack-request/v1": "one reviewed request for a bounded probe",
    "review-disposition/v1": "your recorded review decision",
    "source-bundle/v1": "a PDF, spreadsheet, or structured register source",
    "tool-pack-request/v1": "a reviewed map and selected target tools",
}

STYLE = """
:root {
  --bg: #f5f6f7;
  --surface: #ffffff;
  --text: #161a1d;
  --muted: #5d666d;
  --line: #dde2e4;
  --accent: #174ea6;
  --ok: #1b7f4e;
  --hold: #a15c00;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  color: var(--text);
  background: var(--bg);
  font: 16px/1.5 system-ui, sans-serif;
}
main, header.top, footer.foot {
  max-width: 960px;
  margin: 0 auto;
  padding: 0 1.25rem;
}
header.top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  padding-top: 1rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--line);
}
.brand { font-weight: 650; color: var(--text); text-decoration: none; }
nav { display: flex; gap: 0.9rem; flex-wrap: wrap; }
nav a { color: var(--muted); text-decoration: none; font-size: 0.92rem; }
nav a:hover, nav a.is-current, a { color: var(--accent); }
nav a.is-current { text-decoration: underline; text-underline-offset: 0.3rem; }
h1 { font-size: 1.75rem; line-height: 1.25; margin: 1.4rem 0 0.6rem; }
h2 { font-size: 1.15rem; margin: 1.8rem 0 0.6rem; }
h3 { font-size: 1rem; margin: 1.2rem 0 0.4rem; }
p, li { color: var(--text); }
.lede, .muted { color: var(--muted); }
.kicker { margin: 1.4rem 0 0; color: var(--muted); font-size: 0.85rem; letter-spacing: 0.02em; }
.actions { display: flex; gap: 0.6rem; flex-wrap: wrap; margin: 1rem 0 1.4rem; }
.btn {
  display: inline-block;
  padding: 0.45rem 0.75rem;
  border: 1px solid var(--accent);
  border-radius: 6px;
  text-decoration: none;
  font-size: 0.92rem;
}
.btn.primary { background: var(--accent); color: #fff; }
.btn.secondary { background: var(--surface); color: var(--accent); }
.panel {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0.9rem 1rem;
  margin: 0.8rem 0;
}
.table-scroll {
  max-width: 100%;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}
.table-scroll:focus { outline: 2px solid var(--accent); outline-offset: 2px; }
.jobs { display: grid; gap: 0.8rem; }
@media (min-width: 760px) { .jobs { grid-template-columns: repeat(3, 1fr); } }
.jobs h3 { margin-top: 0; }
table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
th, td { text-align: left; vertical-align: top; padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; }
code, pre {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.86rem;
}
pre, .code {
  display: block;
  white-space: pre-wrap;
  background: #1e2328;
  color: #e8edf2;
  padding: 0.75rem;
  border-radius: 6px;
  overflow-x: auto;
}
code.inline { background: #eef1f3; color: var(--text); padding: 0.05rem 0.3rem; border-radius: 4px; }
.badge {
  display: inline-block;
  font-size: 0.75rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  border: 1px solid var(--line);
}
.badge.ok { color: var(--ok); border-color: #b7ddc6; background: #eef8f2; }
.badge.hold { color: var(--hold); border-color: #ead2a8; background: #fff6e8; }
article.skill {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0.9rem 1rem;
  margin: 0.8rem 0;
  background: var(--surface);
}
.related-grid { display: grid; gap: 0.8rem; }
@media (min-width: 640px) { .related-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.related-grid .panel { margin: 0; }
.related-grid h3 { margin-top: 0; }
.workflow-steps { padding-left: 1.4rem; }
.workflow-steps > li { padding-left: 0.25rem; }
.workflow-steps h3 { margin-top: 0; }
.machine { margin-top: 1.5rem; }
.machine summary { color: var(--accent); cursor: pointer; }
.machine li { margin: 0.55rem 0; }
summary, a, code { overflow-wrap: anywhere; }
footer.foot { color: var(--muted); font-size: 0.85rem; padding: 2rem 1.25rem; }
@media (max-width: 560px) {
  header.top { align-items: flex-start; flex-direction: column; }
  nav { gap: 0.55rem 0.85rem; }
  h1 { font-size: 1.55rem; }
}
""".strip()


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _inline_markdown(value: str) -> str:
    parts = re.split(r"(`[^`]+`)", value)
    return "".join(
        f'<code class="inline">{_esc(part[1:-1])}</code>'
        if part.startswith("`") and part.endswith("`")
        else _esc(part)
        for part in parts
    )


def _sentence(value: str) -> str:
    text = value.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else f"{text}."


def _skill_copy(skill: dict[str, object]) -> tuple[str, str]:
    description = str(skill["description"])
    if " Use when " not in description:
        return description, description
    purpose, use_when = description.split(" Use when ", 1)
    return _sentence(purpose), _sentence(use_when)


def _section_bullets(path: Path, heading: str) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(f"## {heading}") + 1
    except ValueError:
        return []
    bullets: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            bullets.append(line[2:].strip())
        elif bullets and line.startswith("  "):
            bullets[-1] = f"{bullets[-1]} {line.strip()}"
    return bullets


def _schema_label(schema_id: str) -> str:
    if schema_id in SCHEMA_LABELS:
        return SCHEMA_LABELS[schema_id]
    name = schema_id.split("/", 1)[0].replace("-", " ")
    return f"a {name}"


def _source_label(source: dict[str, str]) -> str:
    parsed = urlparse(source["url"])
    host = parsed.netloc.removeprefix("www.")
    parts = [part for part in parsed.path.split("/") if part]
    if host == "github.com" and len(parts) >= 2:
        project_names = {
            "FluentModbus": "FluentModbus",
            "core": "Home Assistant",
            "modpoll": "gavinying/modpoll",
            "node-red-contrib-modbus": "node-red-contrib-modbus",
            "pymodbus": "Pymodbus",
        }
        project = project_names.get(parts[1], parts[1])
        suffixes = {
            "project-discussion": "discussion",
            "project-documentation": "documentation",
            "project-issue": "issue",
        }
        if source["type"] in suffixes:
            return f'{project} {suffixes[source["type"]]}'
    if host.endswith("modbus.org"):
        return "Modbus application protocol"
    if host == "modbustools.com":
        return "Witte Modbus Poll XML example" if "pollxml" in parsed.path else "Witte Modbus Poll manual"
    if host == "win-tech.com":
        return "WinTECH ModScan documentation"
    return SOURCE_TYPE_LABELS.get(source["type"], source["type"])


def _page(
    *,
    title: str,
    description: str,
    canonical: str,
    body: str,
    depth: int = 0,
    schema: str | None = None,
    current: str | None = None,
    section: str | None = None,
    noindex: bool = False,
    absolute_navigation: bool = False,
) -> str:
    prefix = f"{BASE_URL}/" if absolute_navigation else "../" * depth
    favicon = f'{prefix}favicon.svg'
    schema_tag = f'<script type="application/ld+json">{schema}</script>' if schema else ""
    robots_tag = '<meta name="robots" content="noindex">' if noindex else ""
    section_links = {
        "skills": ("Skills", "index.html#skills"),
        "workflows": ("Workflows", "index.html#workflows"),
        "problems": ("Problems", "index.html#problems"),
    }
    last_label, last_href = section_links.get(section, ("Install", "index.html#install"))

    def nav_link(label: str, href: str, key: str) -> str:
        active = current == key
        class_name = ' class="is-current"' if active else ""
        aria = ' aria-current="page"' if active else ""
        return f'<a{class_name}{aria} href="{prefix}{href}">{label}</a>'

    navigation = "".join(
        (
            nav_link("Home", "index.html", "home"),
            nav_link("When to use", "when-to-use.html", "when-to-use"),
            nav_link("Example", "examples/compile-user-map.html", "example"),
            nav_link(last_label, last_href, section or "install"),
        )
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width\">"
        f"<title>{_esc(title)}</title>"
        f"<meta name=\"description\" content=\"{_esc(description)}\">"
        f"{robots_tag}"
        f"<link rel=\"canonical\" href=\"{_esc(canonical)}\">"
        f"<link rel=\"icon\" href=\"{_esc(favicon)}\" type=\"image/svg+xml\">"
        f"{schema_tag}<style>{STYLE}</style></head><body>"
        f"<header class=\"top\"><a class=\"brand\" href=\"{prefix}index.html\">Modbus Skills</a>"
        f"<nav aria-label=\"Primary\">{navigation}</nav></header>"
        f"<main>{body}</main>"
        f"<footer class=\"foot\">Read-only by design. Apache-2.0. "
        f"<a href=\"https://github.com/studioxvii/modbus-skills\">GitHub</a></footer>"
        "</body></html>\n"
    )


def _csv_table(
    path: Path,
    columns: list[str] | None = None,
    labels: dict[str, str] | None = None,
    aria_label: str = "Data table",
) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return "<p class=\"muted\">No rows.</p>"
    fields = columns or list(rows[0])
    head = "".join(f"<th>{_esc((labels or {}).get(field, field))}</th>" for field in fields)
    body = []
    for row in rows:
        cells = "".join(f"<td>{_esc(row.get(field, ''))}</td>" for field in fields)
        body.append(f"<tr>{cells}</tr>")
    table = f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    return (
        f'<div class="table-scroll" tabindex="0" role="region" '
        f'aria-label="{_esc(aria_label)}">{table}</div>'
    )


def _load_example_preview() -> dict[str, str]:
    source = EXAMPLE / "source.csv"
    unresolved_source = EXAMPLE / "source-unresolved.csv"
    user_csv = EXAMPLE / "output" / "user-map.csv"
    user_md = (EXAMPLE / "output" / "user-map.md").read_text(encoding="utf-8")
    return {
        "before": _csv_table(
            source,
            ["Address", "Name", "Data Type", "Byte Order", "Access"],
            aria_label="Resolved source table",
        ),
        "unresolved": _csv_table(
            unresolved_source,
            ["Address", "Name", "Data Type", "Byte Order", "Access"],
            aria_label="Source table with unresolved byte order",
        ),
        "after": _csv_table(
            user_csv,
            ["oem_point_id", "name", "source_register", "area", "protocol_offset", "datatype"],
            {
                "oem_point_id": "Point",
                "name": "Name",
                "source_register": "Manual address",
                "area": "Area",
                "protocol_offset": "Offset",
                "datatype": "Type",
            },
            aria_label="Finished user map",
        ),
        "summary": user_md,
    }


def render() -> dict[Path, str]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    skills = data["skills"]
    activation = json.loads(ACTIVATION.read_text(encoding="utf-8"))
    workflow_data = json.loads(WORKFLOWS.read_text(encoding="utf-8"))
    workflows = workflow_data["workflows"]
    research_data = json.loads(RESEARCH.read_text(encoding="utf-8"))
    research = research_data["records"]
    skills_by_id = {skill["id"]: skill for skill in skills}
    workflows_by_id = {workflow["id"]: workflow for workflow in workflows}
    prompts = {case["skill_id"]: case["positive"] for case in activation["cases"]}
    example = _load_example_preview()

    workflow_links = "".join(
        f'<li><a href="workflows/{_esc(workflow["id"])}.html">{_esc(workflow["name"])}</a></li>'
        for workflow in workflows
    )
    research_links = "".join(
        f'<li><a href="problems/{_esc(record["id"])}.html">'
        f'{_esc(PROBLEM_TITLES[record["id"]])}</a></li>'
        for record in research
    )
    skill_rows = "".join(
        f'<tr><td><a href="skills/{_esc(skill["id"])}.html">{_esc(skill["display_name"])}</a></td>'
        f'<td>{_esc(skill.get("short_description") or skill["description"])}</td></tr>'
        for skill in skills
    )
    index_schema = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": "Modbus Skills",
            "applicationCategory": "DeveloperApplication",
            "url": f"{BASE_URL}/",
            "description": "Turn a vendor Modbus manual into a readable map, JSON, and CSV.",
            "license": "https://www.apache.org/licenses/LICENSE-2.0",
        },
        separators=(",", ":"),
    )
    index_body = f"""
<p class="kicker">Read-only map tools for commissioning work</p>
<h1>Turn a vendor Modbus manual into a usable map</h1>
<p class="lede">For controls, commissioning, and integration engineers. Give it a PDF or spreadsheet. Get a readable map plus JSON and CSV. If the manual is unclear, you get a short list of questions.</p>
<p class="actions">
  <a class="btn primary" href="#install">Install</a>
  <a class="btn secondary" href="examples/compile-user-map.html">See the worked example</a>
  <a class="btn secondary" href="when-to-use.html">When to use this</a>
</p>
<h2>First 10 minutes</h2>
<ol>
  <li>Add the project once. The install steps are below.</li>
  <li>Ask for a user map from your local manual or spreadsheet, and name the measurements you actually need.</li>
  <li>Open the three files it writes: the readable map, the JSON, and the CSV.</li>
</ol>
<p>You can say this in plain language:</p>
<pre>Use this manual to make a user map for temperatures, operating status, alarms, and power. Give me a readable map plus JSON and CSV.</pre>
<h2>What it does with a messy table</h2>
<p class="muted">This is a fictional commissioning table. 40001 becomes holding-register offset 0. 30001 becomes input-register offset 0. The read map keeps the readable points. Level Setpoint is recorded as write-only.</p>
<div class="panel">
  <h3>Source table</h3>
  {example["before"]}
</div>
<div class="panel">
  <h3>Finished user map</h3>
  {example["after"]}
  <p><span class="badge ok">Readable</span> three points in the map &nbsp; <span class="badge hold">Write-only</span> Level Setpoint recorded as an exclusion</p>
</div>
<p><a href="examples/compile-user-map.html">See the full example, including a separate table that pauses for a byte-order choice</a></p>
<h2>Three jobs</h2>
<div class="jobs">
  <section class="panel">
    <h3>Vendor file to user map</h3>
    <p>Turn a PDF or spreadsheet into a readable map, JSON, and CSV.</p>
    <p><a href="skills/compile-user-map.html">Compile a user map</a></p>
  </section>
  <section class="panel">
    <h3>A 32-bit value that looks wrong</h3>
    <p>See every supported byte and word layout from one sample. You choose the layout that matches the device.</p>
    <p><a href="skills/check-byte-order.html">Check byte order</a></p>
  </section>
  <section class="panel">
    <h3>A polling setup from a checked map</h3>
    <p>Build a read-only Node-RED, Modpoll (BETA), or ModScan (BETA) setup. You enable it when you are ready to poll.</p>
    <p><a href="skills/build-tool-pack.html">Build a tool pack</a></p>
  </section>
</div>
<h2 id="install">Install in Codex</h2>
<p>These commands install the plugin in Codex:</p>
<pre>git clone https://github.com/studioxvii/modbus-skills.git
cd modbus-skills
codex plugin marketplace add "$PWD"
codex plugin add modbus-skills@modbus-skills</pre>
<p>For Cursor, Claude Code, and Agent Plugins 1.0, see <a href="https://github.com/studioxvii/modbus-skills#build-for-another-client">Build for another client in the README</a>.</p>
<h2>Safety</h2>
<p>These tools produce read-only maps, bounded poll plans, and a short question list when a manual is unclear.</p>
<h2 id="workflows">Longer jobs</h2>
<ul>{workflow_links}</ul>
<h2 id="problems">Problems these cover</h2>
<ul>{research_links}</ul>
<h2 id="skills">Everything it can do</h2>
<div class="table-scroll" tabindex="0" role="region" aria-label="Skill catalog"><table><thead><tr><th>Task</th><th>What you get</th></tr></thead><tbody>{skill_rows}</tbody></table></div>
"""
    files: dict[Path, str] = {
        SITE / "index.html": _page(
            title="Modbus Skills",
            description="Turn a vendor Modbus manual into a readable map, JSON, and CSV.",
            canonical=f"{BASE_URL}/",
            body=index_body,
            schema=index_schema,
            current="home",
        ),
        SITE / "favicon.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect width="64" height="64" rx="12" fill="#174ea6"/>'
            '<path d="M14 46V18h8l10 16 10-16h8v28h-8V31L32 46 22 31v15z" fill="white"/>'
            "</svg>\n"
        ),
    }

    when_body = """
<p class="kicker">A short guide</p>
<h1>When to use Modbus Skills</h1>
<p class="lede">Use this when you need a usable map, a layout check, or a read-only polling setup from vendor documentation.</p>
<h2>This work</h2>
<ul>
  <li>You have a vendor PDF, spreadsheet, CSV, JSON, XML, or text register map.</li>
  <li>You need a readable map plus JSON and CSV.</li>
  <li>A 32-bit or 64-bit value looks wrong and you want every possible layout from one sample so you can choose.</li>
  <li>A firmware or device map changed and you need to see what was added, removed, moved, or changed.</li>
  <li>You want a read-only Node-RED, Modpoll (BETA), or ModScan (BETA) setup from a map you have already checked.</li>
</ul>
<p>Start by asking for a <a href="skills/compile-user-map.html">user map from the vendor file</a>. If you want help choosing the next step, use <a href="skills/modbus-help.html">the help path</a>.</p>
<h2>Out of scope</h2>
<ul>
  <li>Register writes, coil forces, and broadcasts.</li>
  <li>Network discovery and unbounded polling.</li>
  <li>Choosing a byte order, unit ID, or address style while the manual is still silent.</li>
  <li>Opening a live device while the manual is still being read. Live reads come later, as a limited step.</li>
</ul>
<h2>Why it pauses</h2>
<p>Unclear documentation becomes a short list of questions. Byte order, 40001 conversions, and write-only points wait for a stated value or an explicit exclusion.</p>
<p><a href="examples/compile-user-map.html">See the worked example</a></p>
"""
    files[SITE / "when-to-use.html"] = _page(
        title="When to use · Modbus Skills",
        description="When Modbus Skills is the right tool for a map, layout check, or read-only poll setup.",
        canonical=f"{BASE_URL}/when-to-use.html",
        body=when_body,
        current="when-to-use",
    )
    files[SITE / "when-to-use.md"] = (
        "# When to use Modbus Skills\n\n"
        "Use this when you need a usable map, a layout check, or a read-only "
        "polling setup from vendor documentation.\n\n"
        "## This work\n\n"
        "- You have a vendor PDF, spreadsheet, CSV, JSON, XML, or text register map.\n"
        "- You need a readable map plus JSON and CSV.\n"
        "- A 32-bit or 64-bit value looks wrong and you want every possible layout from one sample so you can choose.\n"
        "- A firmware or device map changed and you need to see what was added, removed, moved, or changed.\n"
        "- You want a read-only Node-RED, Modpoll (BETA), or ModScan (BETA) setup from a map you have already checked.\n\n"
        "Start by asking for a user map from the vendor file. If you want help choosing the next step, ask for that.\n\n"
        "## Out of scope\n\n"
        "- Register writes, coil forces, and broadcasts.\n"
        "- Network discovery and unbounded polling.\n"
        "- Choosing a byte order, unit ID, or address style while the manual is still silent.\n"
        "- Opening a live device while the manual is still being read. Live reads come later, as a limited step.\n\n"
        "Unclear documentation becomes a short list of questions. Byte order, 40001 conversions, and write-only points wait for a stated value or an explicit exclusion.\n\n"
        "See [the worked example](examples/compile-user-map.html).\n"
    )

    example_body = f"""
<p class="kicker">Worked example · fictional commissioning table</p>
<h1>A messy table becomes a user map</h1>
<p class="lede">The first table has the information needed to finish. The result keeps the readable points, preserves the manual addresses, and records the write-only point as an exclusion.</p>
<h2>Resolved source</h2>
<div class="panel">{example["before"]}</div>
<h2>Finished user map</h2>
<div class="panel">{example["after"]}</div>
<h2>Readable summary</h2>
<pre>{_esc(example["summary"])}</pre>
<h2>What stayed explicit</h2>
<ul>
  <li>Level Setpoint is write-only, so it is recorded as an exclusion on the read plan.</li>
  <li>40001 becomes holding-register offset 0. 40003 becomes holding-register offset 2. 30001 becomes input-register offset 0.</li>
  <li>Flow Rate uses CDAB. Energy Total uses ABCD. No blocking exception remains in this completed result.</li>
</ul>
<h2>What a pause looks like</h2>
<p>This separate source leaves byte order blank for the two multi-register values. The skill does not choose a layout. It holds those rows and asks you to confirm the documented or tested order.</p>
<div class="panel">
  <p><span class="badge hold">Needs a decision</span> Flow Rate and Energy Total have no byte order.</p>
  {example["unresolved"]}
</div>
<p>The source files are in the repository under <code class="inline">docs/examples/compile-user-map</code>.</p>
<p><a href="../when-to-use.html">When to use this</a> · <a href="../skills/compile-user-map.html">Compile a user map</a></p>
"""
    files[SITE / "examples" / "compile-user-map.html"] = _page(
        title="Compile example · Modbus Skills",
        description="Synthetic before-and-after compile of a mixed 40001/30001 Modbus table.",
        canonical=f"{BASE_URL}/examples/compile-user-map.html",
        body=example_body,
        depth=1,
        current="example",
    )
    files[SITE / "examples" / "compile-user-map.md"] = (
        "# A messy table becomes a user map\n\n"
        "This is a fictional commissioning table.\n\n"
        "The completed table maps 40001 to holding-register offset 0, 40003 to "
        "holding-register offset 2, and 30001 to input-register offset 0. It "
        "records the write-only setpoint as an exclusion. A separate unresolved "
        "table leaves the two multi-register byte orders blank and pauses for a decision.\n\n"
        "Source files live in `docs/examples/compile-user-map`.\n"
    )

    for skill in skills:
        title = skill["display_name"]
        description = skill["description"]
        purpose, use_when = _skill_copy(skill)
        canonical = f'{BASE_URL}/skills/{skill["id"]}.html'
        example_request = prompts[skill["id"]][0]
        skill_file = ROOT / skill["path"] / "SKILL.md"
        outputs = _section_bullets(skill_file, "Output files")[:3]
        source_url = f'https://github.com/studioxvii/modbus-skills/blob/main/{skill["path"]}/SKILL.md'
        schema = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "TechArticle",
                "headline": title,
                "description": description,
                "url": canonical,
                "keywords": [example_request],
                "isPartOf": {"@type": "CollectionPage", "name": "Modbus Skills", "url": f"{BASE_URL}/"},
            },
            separators=(",", ":"),
        )
        outputs_html = "".join(f"<li>{_inline_markdown(output)}</li>" for output in outputs)
        page_body = (
            '<p class="kicker">Read-only skill</p>'
            f"<h1>{_esc(title)}</h1><p class=\"lede\">{_esc(purpose)}</p>"
            f"<h2>Use this when</h2><p>{_esc(use_when)}</p>"
            f"<h2>What you get back</h2><ul>{outputs_html}</ul>"
            f'<h2>Example request</h2><div class="panel"><p>{_esc(example_request)}</p></div>'
            "<h2>Safety boundary</h2><p>This skill does not write registers, force coils, "
            "broadcast, scan a network, or start unbounded polling. Unresolved engineering "
            "fields stay visible.</p>"
            f'<p><a href="{_esc(source_url)}">View the skill source on GitHub</a></p>'
        )
        files[SITE / "skills" / f'{skill["id"]}.html'] = _page(
            title=f"{title} · Modbus Skills",
            description=description,
            canonical=canonical,
            body=page_body,
            depth=1,
            schema=schema,
            current="skills",
            section="skills",
        )
        outputs_md = "\n".join(f"- {output}" for output in outputs)
        files[SITE / "skills" / f'{skill["id"]}.md'] = (
            f"# {skill['display_name']}\n\n{purpose}\n\n"
            f"## Use this when\n\n{use_when}\n\n"
            f"## What you get back\n\n{outputs_md}\n\n"
            f"## Example request\n\n{example_request}\n\n"
            "## Safety boundary\n\nThis skill does not write registers, force coils, "
            "broadcast, scan a network, or start unbounded polling. Unresolved engineering "
            "fields stay visible.\n\n"
            f"[View the skill source on GitHub]({source_url})\n"
        )

    llms = (
        "# Modbus Skills\n\n"
        "Read-only Modbus engineering workflows for Codex, Claude Code, Cursor, "
        "and other Agent Plugins 1.0 clients.\n\n"
        "Turn a vendor manual, spreadsheet, or register map into a usable engineering "
        "file. The generated work is read-only.\n\n"
        "## When to use\n\n"
        "Use these skills when an engineer needs a user map, a byte-order comparison, "
        "a firmware-map diff, or a bounded read-only tool pack from vendor documentation. "
        "Register writes, network discovery, unbounded polling, and invented byte orders "
        "are out of scope.\n\n"
        "Details: [when-to-use.md](when-to-use.md)\n\n"
        "## Start here\n\n"
        "1. [Compile User Map](skills/compile-user-map.html): OEM PDF or structured map to Markdown, JSON, and CSV.\n"
        "2. [Check Byte Order](skills/check-byte-order.html): every supported layout from one raw sample.\n"
        "3. [Build Tool Pack](skills/build-tool-pack.html): disabled read-only Node-RED, Modpoll (BETA), or ModScan (BETA) files.\n\n"
        "If the next step is unclear, use [Modbus Help](skills/modbus-help.html).\n\n"
        "## Worked example\n\n"
        "[Compile a synthetic OEM table](examples/compile-user-map.html)\n\n"
        "## Skills\n\n"
        + "\n".join(
            f'- [{s["display_name"]}](skills/{s["id"]}.html): {s["description"]}'
            for s in skills
        )
        + "\n"
    )
    files[SITE / "llms.txt"] = llms
    workflow_llms = "\n\n".join(
        f'## Workflow: {w["name"]}\n\n{w["goal"]}\n\n'
        + "\n".join(
            f'{index}. {step.get("skill", step.get("workflow", step["kind"]))}: '
            f'{", ".join(step["inputs"])} -> {step["output"]}'
            for index, step in enumerate(w["steps"], start=1)
        )
        for w in workflows
    )
    research_llms = "\n".join(
        f'## Problem: {r["problem"]}\n\n{r["evidence"]}\n\nSources: '
        + ", ".join(source["url"] for source in r["sources"])
        for r in research
    )
    files[SITE / "llms-full.txt"] = (
        llms
        + "\n"
        + "\n".join(
            f'## {s["display_name"]}\n\n{s["description"]}\n\nExample: {s["default_prompt"]}'
            for s in skills
        )
        + "\n\n"
        + workflow_llms
        + "\n\n"
        + research_llms
        + "\n"
    )
    files[SITE / "skills.json"] = json.dumps(data, indent=2) + "\n"
    files[SITE / "workflows.json"] = json.dumps(workflow_data, indent=2) + "\n"
    files[SITE / "research.json"] = json.dumps(research_data, indent=2) + "\n"

    for workflow in workflows:
        canonical = f'{BASE_URL}/workflows/{workflow["id"]}.html'
        step_count = len(workflow["steps"])
        step_word = "step" if step_count == 1 else "steps"
        workflow_goal = workflow["goal"].replace("capture-sample", "Capture Sample")
        rendered_steps: list[str] = []
        machine_contracts: list[str] = []
        for step in workflow["steps"]:
            if step["kind"] == "skill":
                skill = skills_by_id[step["skill"]]
                step_title = (
                    f'<a href="../skills/{_esc(skill["id"])}.html">'
                    f'{_esc(skill["display_name"])}</a>'
                )
                step_copy = _esc(skill.get("short_description") or _skill_copy(skill)[0])
            elif step["kind"] == "workflow":
                child = workflows_by_id[step["workflow"]]
                step_title = (
                    f'<a href="{_esc(child["id"])}.html">{_esc(child["name"])}</a>'
                )
                step_copy = _esc(child["goal"].replace("capture-sample", "Capture Sample"))
            elif step["kind"] == "human-gate":
                step_title = "Your decision"
                step_copy = _esc(step["instruction"])
            elif step["kind"] == "external-gate":
                step_title = "One bounded device read"
                step_copy = _esc(step["instruction"])
            else:
                step_title = _esc(step["kind"].replace("-", " ").title())
                step_copy = _esc(step.get("instruction", "Complete this bounded step."))
            input_labels = ", ".join(_schema_label(value) for value in step["inputs"])
            output_label = (
                "a disabled one-read probe"
                if step.get("skill") == "capture-sample" and step["output"] == "modbus-tool-pack/v1"
                else _schema_label(step["output"])
            )
            rendered_steps.append(
                f'<li class="panel"><h3>{step_title}</h3><p>{step_copy}</p>'
                f'<p class="muted">Starts with {_esc(input_labels)}. Produces '
                f'{_esc(output_label)}.</p></li>'
            )
            machine_inputs = ", ".join(
                f'<code class="inline">{_esc(value)}</code>' for value in step["inputs"]
            )
            machine_contracts.append(
                f'<li>{machine_inputs} → <code class="inline">{_esc(step["output"])}</code></li>'
            )
        steps_html = "".join(rendered_steps)
        contracts_html = "".join(machine_contracts)
        stops_html = "".join(
            f"<li>{_esc(_sentence(stop))}</li>" for stop in workflow["stop_conditions"]
        )
        start_html = "".join(
            f"<li>{_esc(_schema_label(value))}</li>" for value in workflow["steps"][0]["inputs"]
        )
        result = _schema_label(workflow["steps"][-1]["output"])
        files[SITE / "workflows" / f'{workflow["id"]}.html'] = _page(
            title=f'{workflow["name"]} · Modbus Skills',
            description=workflow_goal,
            canonical=canonical,
            body=(
                f'<p class="kicker">Workflow · {step_count} {step_word}</p>'
                f'<h1>{_esc(workflow["name"])}</h1><p class="lede">{_esc(workflow_goal)}</p>'
                f'<div class="related-grid"><section class="panel"><h2>You start with</h2>'
                f'<ul>{start_html}</ul></section><section class="panel"><h2>You receive</h2>'
                f'<p>{_esc(result)}.</p></section></div>'
                f'<h2>How it works</h2><ol class="workflow-steps">{steps_html}</ol>'
                f'<h2>When it stops</h2><ul>{stops_html}</ul>'
                f'<details class="machine"><summary>Show machine-readable contracts</summary>'
                f'<ol>{contracts_html}</ol><p><a href="../workflows.json">View all workflow data</a></p></details>'
            ),
            depth=1,
            current="workflows",
            section="workflows",
        )

    for record in research:
        canonical = f'{BASE_URL}/problems/{record["id"]}.html'
        sources_html = "".join(
            f'<li><a href="{_esc(source["url"])}">'
            f'{_esc(_source_label(source))}</a> '
            f'<span class="muted">{_esc(urlparse(source["url"]).netloc.removeprefix("www."))}</span></li>'
            for source in record["sources"]
        )
        skills_html = "".join(
            f'<article class="panel"><h3><a href="../skills/{_esc(skill_id)}.html">'
            f'{_esc(skills_by_id[skill_id]["display_name"])}</a></h3>'
            f'<p>{_esc(skills_by_id[skill_id].get("short_description") or _skill_copy(skills_by_id[skill_id])[0])}</p></article>'
            for skill_id in record["skills"]
        )
        problem_title = PROBLEM_TITLES[record["id"]]
        files[SITE / "problems" / f'{record["id"]}.html'] = _page(
            title=f'{problem_title} · Modbus Skills',
            description=record["evidence"],
            canonical=canonical,
            body=(
                f'<p class="kicker">Problem</p><h1>{_esc(problem_title)}</h1>'
                f'<p class="lede">{_esc(record["problem"])}</p>'
                f'<h2>Why it matters</h2><p>{_esc(record["evidence"])}</p>'
                f'<h2>What to do</h2><div class="related-grid">{skills_html}</div>'
                f'<h2>Sources</h2><ul>{sources_html}</ul>'
            ),
            depth=1,
            current="problems",
            section="problems",
        )

    files[SITE / "404.html"] = _page(
        title="Page not found · Modbus Skills",
        description="The requested Modbus Skills page does not exist.",
        canonical=f"{BASE_URL}/404.html",
        body=(
            '<p class="kicker">404</p><h1>Page not found</h1>'
            '<p class="lede">That address does not match a page in this site.</p>'
            f'<p class="actions"><a class="btn primary" href="{BASE_URL}/">Go home</a>'
            f'<a class="btn secondary" href="{BASE_URL}/when-to-use.html">When to use</a>'
            f'<a class="btn secondary" href="{BASE_URL}/#skills">Browse skills</a></p>'
        ),
        noindex=True,
        absolute_navigation=True,
    )

    urls = (
        [f"{BASE_URL}/", f"{BASE_URL}/when-to-use.html", f"{BASE_URL}/when-to-use.md", f"{BASE_URL}/examples/compile-user-map.html", f"{BASE_URL}/examples/compile-user-map.md"]
        + [f'{BASE_URL}/skills/{s["id"]}.html' for s in skills]
        + [f'{BASE_URL}/skills/{s["id"]}.md' for s in skills]
        + [f'{BASE_URL}/workflows/{w["id"]}.html' for w in workflows]
        + [f'{BASE_URL}/problems/{r["id"]}.html' for r in research]
    )
    files[SITE / "sitemap.xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(f"  <url><loc>{_esc(url)}</loc></url>" for url in urls)
        + "\n</urlset>\n"
    )
    files[SITE / "robots.txt"] = f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n"
    files[SITE / ".nojekyll"] = ""
    return files


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv or sys.argv[1:])
    files = render()
    stale: list[str] = []
    generated_dirs = (SITE / "skills", SITE / "workflows", SITE / "problems", SITE / "examples")
    expected = set(files)
    obsolete = sorted(
        path
        for directory in generated_dirs
        if directory.exists()
        for path in directory.iterdir()
        if path.is_file() and path not in expected
    )
    if check:
        stale.extend(str(path.relative_to(ROOT)) for path in obsolete)
    else:
        for path in obsolete:
            path.unlink()
    for path, content in files.items():
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    if stale:
        for path in stale:
            print(f"ERROR: stale generated site file: {path}")
        return 1
    print("Site is current" if check else f"Generated {len(files)} site files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
