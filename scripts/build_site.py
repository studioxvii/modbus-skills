#!/usr/bin/env python3
"""Generate a small static and agent-readable skill catalog."""

from __future__ import annotations

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
SITE = ROOT / "site"
BASE_URL = os.environ.get("MODBUS_SKILLS_SITE_URL", "https://studioxvii.github.io/modbus-skills").rstrip("/")


def render() -> dict[Path, str]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    skills = data["skills"]
    activation = json.loads(ACTIVATION.read_text(encoding="utf-8"))
    workflow_data = json.loads(WORKFLOWS.read_text(encoding="utf-8"))
    workflows = workflow_data["workflows"]
    research_data = json.loads(RESEARCH.read_text(encoding="utf-8"))
    research = research_data["records"]
    prompts = {case["skill_id"]: case["positive"] for case in activation["cases"]}
    cards = "\n".join(
        f'<article><h2><a href="skills/{html.escape(skill["id"])}.html">{html.escape(skill["display_name"])}</a></h2>'
        f'<p>{html.escape(skill["description"])}</p><code>{html.escape(skill["default_prompt"])}</code></article>'
        for skill in skills
    )
    style = "body{font:16px system-ui;max-width:960px;margin:auto;padding:2rem;color:#172033}article{border:1px solid #ccd5e0;border-radius:12px;padding:1rem;margin:1rem 0}code{display:block;white-space:pre-wrap;background:#f5f7fa;padding:.75rem}a{color:#174ea6}"
    favicon_root = '<link rel="icon" href="favicon.svg" type="image/svg+xml">'
    favicon_child = '<link rel="icon" href="../favicon.svg" type="image/svg+xml">'
    index_schema = json.dumps({"@context": "https://schema.org", "@type": "CollectionPage", "name": "Modbus Skills", "url": f"{BASE_URL}/", "hasPart": [{"@type": "TechArticle", "name": skill["display_name"], "url": f'{BASE_URL}/skills/{skill["id"]}.html'} for skill in skills]}, separators=(",", ":"))
    workflow_links = "".join(f'<li><a href="workflows/{html.escape(workflow["id"])}.html">{html.escape(workflow["name"])}</a></li>' for workflow in workflows)
    research_links = "".join(f'<li><a href="problems/{html.escape(record["id"])}.html">{html.escape(record["problem"])}</a></li>' for record in research)
    index = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Modbus Skills</title><meta name="description" content="Read-only Modbus engineering skills for register maps, byte order, tool generation, and capture analysis."><link rel="canonical" href="{BASE_URL}/">{favicon_root}<script type="application/ld+json">{index_schema}</script><style>{style}</style></head><body><main><h1>Modbus Skills</h1><p>Tested, read-only workflows for Modbus engineering.</p><h2>Chained workflows</h2><ul>{workflow_links}</ul><h2>Researched problems</h2><ul>{research_links}</ul><h2>Focused skills</h2>{cards}</main></body></html>\n'
    files: dict[Path, str] = {
        SITE / "index.html": index,
        SITE / "favicon.svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#174ea6"/><path d="M14 46V18h8l10 16 10-16h8v28h-8V31L32 46 22 31v15z" fill="white"/></svg>\n',
    }
    for skill in skills:
        title = html.escape(skill["display_name"])
        description = html.escape(skill["description"])
        canonical = f'{BASE_URL}/skills/{skill["id"]}.html'
        examples = prompts[skill["id"]][:5]
        schema = json.dumps({"@context": "https://schema.org", "@type": "TechArticle", "headline": skill["display_name"], "description": skill["description"], "url": canonical, "keywords": examples, "isPartOf": {"@type": "CollectionPage", "name": "Modbus Skills", "url": f"{BASE_URL}/"}}, separators=(",", ":"))
        examples_html = "".join(f"<li>{html.escape(example)}</li>" for example in examples)
        page = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{title} · Modbus Skills</title><meta name="description" content="{description}"><link rel="canonical" href="{canonical}">{favicon_child}<script type="application/ld+json">{schema}</script><style>{style}</style></head><body><main><p><a href="../index.html">Modbus Skills</a></p><h1>{title}</h1><p>{description}</p><h2>Common requests</h2><ul>{examples_html}</ul><h2>Try it</h2><code>{html.escape(skill["default_prompt"])}</code><p>Source: <code>{html.escape(skill["path"])}/SKILL.md</code></p></main></body></html>\n'
        files[SITE / "skills" / f'{skill["id"]}.html'] = page
        examples_md = "\n".join(f"- {example}" for example in examples)
        files[SITE / "skills" / f'{skill["id"]}.md'] = f'# {skill["display_name"]}\n\n{skill["description"]}\n\n## Common requests\n\n{examples_md}\n\n## Try it\n\n```text\n{skill["default_prompt"]}\n```\n\nSource: `{skill["path"]}/SKILL.md`\n'
    llms = "# Modbus Skills\n\n" + "\n".join(f'- [{s["display_name"]}](skills/{s["id"]}.html): {s["description"]}' for s in skills) + "\n"
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
    research_llms = "\n".join(f'## Problem: {r["problem"]}\n\n{r["evidence"]}\n\nSources: ' + ", ".join(source["url"] for source in r["sources"]) for r in research)
    files[SITE / "llms-full.txt"] = llms + "\n" + "\n".join(f'## {s["display_name"]}\n\n{s["description"]}\n\nExample: {s["default_prompt"]}' for s in skills) + "\n\n" + workflow_llms + "\n\n" + research_llms + "\n"
    files[SITE / "skills.json"] = json.dumps(data, indent=2) + "\n"
    files[SITE / "workflows.json"] = json.dumps(workflow_data, indent=2) + "\n"
    files[SITE / "research.json"] = json.dumps(research_data, indent=2) + "\n"
    for workflow in workflows:
        canonical = f'{BASE_URL}/workflows/{workflow["id"]}.html'
        rendered_steps = []
        for step in workflow["steps"]:
            if step["kind"] == "skill":
                actor = f'<a href="../skills/{html.escape(step["skill"])}.html">{html.escape(step["skill"])}</a>'
            elif step["kind"] == "workflow":
                actor = f'<a href="{html.escape(step["workflow"])}.html">workflow: {html.escape(step["workflow"])}</a>'
            else:
                actor = html.escape(step["kind"])
            inputs = ", ".join(html.escape(value) for value in step["inputs"])
            instruction = f'<br>{html.escape(step["instruction"])}' if step.get("instruction") else ""
            rendered_steps.append(f'<li>{actor}: <code>{inputs}</code> → <code>{html.escape(step["output"])}</code>{instruction}</li>')
        steps_html = "".join(rendered_steps)
        stops_html = "".join(f"<li>{html.escape(stop)}</li>" for stop in workflow["stop_conditions"])
        page = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(workflow["name"])} · Modbus Skills</title><meta name="description" content="{html.escape(workflow["goal"])}"><link rel="canonical" href="{canonical}">{favicon_child}<style>{style}</style></head><body><main><p><a href="../index.html">Modbus Skills</a></p><h1>{html.escape(workflow["name"])}</h1><p>{html.escape(workflow["goal"])}</p><h2>Steps</h2><ol>{steps_html}</ol><h2>Stop conditions</h2><ul>{stops_html}</ul></main></body></html>\n'
        files[SITE / "workflows" / f'{workflow["id"]}.html'] = page
    for record in research:
        canonical = f'{BASE_URL}/problems/{record["id"]}.html'
        sources_html = "".join(f'<li><a href="{html.escape(source["url"])}">{html.escape(source["type"])}</a></li>' for source in record["sources"])
        skills_html = "".join(f'<li><a href="../skills/{html.escape(skill)}.html">{html.escape(skill)}</a></li>' for skill in record["skills"])
        page = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(record["problem"])} · Modbus Skills</title><meta name="description" content="{html.escape(record["evidence"])}"><link rel="canonical" href="{canonical}">{favicon_child}<style>{style}</style></head><body><main><p><a href="../index.html">Modbus Skills</a></p><h1>{html.escape(record["problem"])}</h1><p>{html.escape(record["evidence"])}</p><h2>Primary sources</h2><ul>{sources_html}</ul><h2>Related skills</h2><ul>{skills_html}</ul></main></body></html>\n'
        files[SITE / "problems" / f'{record["id"]}.html'] = page
    urls = [f"{BASE_URL}/"] + [f'{BASE_URL}/skills/{s["id"]}.html' for s in skills] + [f'{BASE_URL}/skills/{s["id"]}.md' for s in skills] + [f'{BASE_URL}/workflows/{w["id"]}.html' for w in workflows] + [f'{BASE_URL}/problems/{r["id"]}.html' for r in research]
    files[SITE / "sitemap.xml"] = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(f"  <url><loc>{html.escape(url)}</loc></url>" for url in urls) + "\n</urlset>\n"
    files[SITE / "robots.txt"] = f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n"
    files[SITE / ".nojekyll"] = ""
    return files


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv or sys.argv[1:])
    files = render()
    stale: list[str] = []
    generated_dirs = (SITE / "skills", SITE / "workflows", SITE / "problems")
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
