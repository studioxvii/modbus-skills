#!/usr/bin/env python3
"""Generate a small static and agent-readable skill catalog."""

from __future__ import annotations

import csv
import html
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "skills.json"
ACTIVATION = ROOT / "catalog" / "activation-cases.json"
WORKFLOWS = ROOT / "catalog" / "workflows.json"
RESEARCH = ROOT / "research" / "issues.json"
EXAMPLE = ROOT / "docs" / "examples" / "compile-user-map"
SITE = ROOT / "site"
BASE_URL = os.environ.get("MODBUS_SKILLS_SITE_URL", "https://studioxvii.github.io/modbus-skills").rstrip("/")

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
nav a:hover, a { color: var(--accent); }
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
footer.foot { color: var(--muted); font-size: 0.85rem; padding: 2rem 1.25rem; }
""".strip()


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _page(
    *,
    title: str,
    description: str,
    canonical: str,
    body: str,
    depth: int = 0,
    schema: str | None = None,
) -> str:
    prefix = "../" * depth
    favicon = f'{prefix}favicon.svg'
    schema_tag = f'<script type="application/ld+json">{schema}</script>' if schema else ""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width\">"
        f"<title>{_esc(title)}</title>"
        f"<meta name=\"description\" content=\"{_esc(description)}\">"
        f"<link rel=\"canonical\" href=\"{_esc(canonical)}\">"
        f"<link rel=\"icon\" href=\"{_esc(favicon)}\" type=\"image/svg+xml\">"
        f"{schema_tag}<style>{STYLE}</style></head><body>"
        f"<header class=\"top\"><a class=\"brand\" href=\"{prefix}index.html\">Modbus Skills</a>"
        f"<nav><a href=\"{prefix}when-to-use.html\">When to use</a>"
        f"<a href=\"{prefix}examples/compile-user-map.html\">Example</a>"
        f"<a href=\"{prefix}index.html#install\">Install</a></nav></header>"
        f"<main>{body}</main>"
        f"<footer class=\"foot\">Read-only by design. Apache-2.0. "
        f"<a href=\"https://github.com/studioxvii/modbus-skills\">GitHub</a></footer>"
        "</body></html>\n"
    )


def _csv_table(
    path: Path,
    columns: list[str] | None = None,
    labels: dict[str, str] | None = None,
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
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _load_example_preview() -> dict[str, str]:
    source = EXAMPLE / "source.csv"
    user_csv = EXAMPLE / "output" / "user-map.csv"
    user_md = (EXAMPLE / "output" / "user-map.md").read_text(encoding="utf-8")
    return {
        "before": _csv_table(
            source,
            ["Address", "Name", "Data Type", "Byte Order", "Access"],
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
    prompts = {case["skill_id"]: case["positive"] for case in activation["cases"]}
    example = _load_example_preview()

    workflow_links = "".join(
        f'<li><a href="workflows/{_esc(workflow["id"])}.html">{_esc(workflow["name"])}</a></li>'
        for workflow in workflows
    )
    research_links = "".join(
        f'<li><a href="problems/{_esc(record["id"])}.html">{_esc(record["problem"])}</a></li>'
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
<p class="muted">This is a fictional commissioning table. 40001 and 30001 become protocol offsets. The read map keeps the readable points. Level Setpoint is recorded as write-only.</p>
<div class="panel">
  <h3>Source table</h3>
  {example["before"]}
</div>
<div class="panel">
  <h3>Finished user map</h3>
  {example["after"]}
  <p><span class="badge ok">Readable</span> three points in the map &nbsp; <span class="badge hold">Write-only</span> Level Setpoint recorded as an exclusion</p>
</div>
<p><a href="examples/compile-user-map.html">See the full example, including the table that pauses for a byte-order choice</a></p>
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
<h2 id="install">Install</h2>
<p>These tools run in Codex, Cursor, or Claude Code. Clone the repository and add it once:</p>
<pre>git clone https://github.com/studioxvii/modbus-skills.git
cd modbus-skills
codex plugin marketplace add "$PWD"
codex plugin add modbus-skills@modbus-skills</pre>
<p>For Cursor and Claude Code, see the <a href="https://github.com/studioxvii/modbus-skills#quick-start">install section in the README</a>.</p>
<h2>Safety</h2>
<p>These tools produce read-only maps, bounded poll plans, and a short question list when a manual is unclear.</p>
<h2>Longer jobs</h2>
<ul>{workflow_links}</ul>
<h2>Problems these cover</h2>
<ul>{research_links}</ul>
<h2>Everything it can do</h2>
<table><thead><tr><th>Task</th><th>What you get</th></tr></thead><tbody>{skill_rows}</tbody></table>
"""
    files: dict[Path, str] = {
        SITE / "index.html": _page(
            title="Modbus Skills",
            description="Turn a vendor Modbus manual into a readable map, JSON, and CSV.",
            canonical=f"{BASE_URL}/",
            body=index_body,
            schema=index_schema,
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
<h2>Other work</h2>
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
        "## Other work\n\n"
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
<p class="lede">This is a fictional commissioning table. 40001 and 30001 become protocol offsets. The read map keeps the readable points. Missing byte order pauses the job so you can choose a layout.</p>
<h2>Source</h2>
<div class="panel">{example["before"]}</div>
<h2>Finished user map</h2>
<div class="panel">{example["after"]}</div>
<h2>Readable summary</h2>
<pre>{_esc(example["summary"])}</pre>
<h2>What stayed explicit</h2>
<ul>
  <li>Level Setpoint is write-only, so it is recorded as an exclusion on the read plan.</li>
  <li>40001 and 30001 stay on the row as the manual address, then convert to offsets 0 and 2, which are the addresses on the wire.</li>
  <li>Blank byte order on Flow Rate or Energy Total pauses the job so you can confirm ABCD, CDAB, or another layout.</li>
</ul>
<p>The source files are in the repository under <code class="inline">docs/examples/compile-user-map</code>.</p>
<p><a href="../when-to-use.html">When to use this</a> · <a href="../skills/compile-user-map.html">Compile a user map</a></p>
"""
    files[SITE / "examples" / "compile-user-map.html"] = _page(
        title="Compile example · Modbus Skills",
        description="Synthetic before-and-after compile of a mixed 40001/30001 Modbus table.",
        canonical=f"{BASE_URL}/examples/compile-user-map.html",
        body=example_body,
        depth=1,
    )
    files[SITE / "examples" / "compile-user-map.md"] = (
        "# A messy table becomes a user map\n\n"
        "This is a fictional commissioning table.\n\n"
        "The finished table remaps 40001/30001 notation to protocol offsets and "
        "records the write-only setpoint as an exclusion. The same table with "
        "byte order left blank pauses so you can choose a layout.\n\n"
        "Source files live in `docs/examples/compile-user-map`.\n"
    )

    for skill in skills:
        title = skill["display_name"]
        description = skill["description"]
        canonical = f'{BASE_URL}/skills/{skill["id"]}.html'
        examples = prompts[skill["id"]][:5]
        schema = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "TechArticle",
                "headline": title,
                "description": description,
                "url": canonical,
                "keywords": examples,
                "isPartOf": {"@type": "CollectionPage", "name": "Modbus Skills", "url": f"{BASE_URL}/"},
            },
            separators=(",", ":"),
        )
        examples_html = "".join(f"<li>{_esc(example_text)}</li>" for example_text in examples)
        page_body = (
            f"<h1>{_esc(title)}</h1><p>{_esc(description)}</p>"
            f"<h2>Common requests</h2><ul>{examples_html}</ul>"
            f"<h2>Try it</h2><pre>{_esc(skill['default_prompt'])}</pre>"
            f"<p>Source: <code class=\"inline\">{_esc(skill['path'])}/SKILL.md</code></p>"
        )
        files[SITE / "skills" / f'{skill["id"]}.html'] = _page(
            title=f"{title} · Modbus Skills",
            description=description,
            canonical=canonical,
            body=page_body,
            depth=1,
            schema=schema,
        )
        examples_md = "\n".join(f"- {example_text}" for example_text in examples)
        files[SITE / "skills" / f'{skill["id"]}.md'] = (
            f"# {skill['display_name']}\n\n{skill['description']}\n\n"
            f"## Common requests\n\n{examples_md}\n\n## Try it\n\n"
            f"```text\n{skill['default_prompt']}\n```\n\n"
            f"Source: `{skill['path']}/SKILL.md`\n"
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
        "are other work.\n\n"
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
        rendered_steps = []
        for step in workflow["steps"]:
            if step["kind"] == "skill":
                actor = f'<a href="../skills/{_esc(step["skill"])}.html">{_esc(step["skill"])}</a>'
            elif step["kind"] == "workflow":
                actor = f'<a href="{_esc(step["workflow"])}.html">workflow: {_esc(step["workflow"])}</a>'
            else:
                actor = _esc(step["kind"])
            inputs = ", ".join(_esc(value) for value in step["inputs"])
            instruction = f'<br>{_esc(step["instruction"])}' if step.get("instruction") else ""
            rendered_steps.append(
                f'<li>{actor}: <code class="inline">{inputs}</code> → '
                f'<code class="inline">{_esc(step["output"])}</code>{instruction}</li>'
            )
        steps_html = "".join(rendered_steps)
        stops_html = "".join(f"<li>{_esc(stop)}</li>" for stop in workflow["stop_conditions"])
        files[SITE / "workflows" / f'{workflow["id"]}.html'] = _page(
            title=f'{workflow["name"]} · Modbus Skills',
            description=workflow["goal"],
            canonical=canonical,
            body=(
                f'<h1>{_esc(workflow["name"])}</h1><p>{_esc(workflow["goal"])}</p>'
                f"<h2>Steps</h2><ol>{steps_html}</ol>"
                f"<h2>Stop conditions</h2><ul>{stops_html}</ul>"
            ),
            depth=1,
        )

    for record in research:
        canonical = f'{BASE_URL}/problems/{record["id"]}.html'
        sources_html = "".join(
            f'<li><a href="{_esc(source["url"])}">{_esc(source["type"])}</a></li>'
            for source in record["sources"]
        )
        skills_html = "".join(
            f'<li><a href="../skills/{_esc(skill)}.html">{_esc(skill)}</a></li>'
            for skill in record["skills"]
        )
        files[SITE / "problems" / f'{record["id"]}.html'] = _page(
            title=f'{record["problem"]} · Modbus Skills',
            description=record["evidence"],
            canonical=canonical,
            body=(
                f'<h1>{_esc(record["problem"])}</h1><p>{_esc(record["evidence"])}</p>'
                f"<h2>Primary sources</h2><ul>{sources_html}</ul>"
                f"<h2>Related skills</h2><ul>{skills_html}</ul>"
            ),
            depth=1,
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
