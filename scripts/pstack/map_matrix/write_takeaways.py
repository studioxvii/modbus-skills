#!/usr/bin/env python3
"""Write a short human-readable takeaways report for the map-matrix run."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MATRIX = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts" / "pstack" / "map-matrix"
OUT_MD = ARTIFACTS / "TAKEAWAYS.md"
OUT_JSON = ARTIFACTS / "takeaways.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    program = load(MATRIX / "program.json")
    manifest = load(MATRIX / "manifest.json")
    by_id = {m["id"]: m for m in manifest["maps"]}

    rows = []
    for entry in program["maps"]:
        receipt_path = ARTIFACTS / entry["id"] / "receipt.json"
        receipt = load(receipt_path) if receipt_path.is_file() else {}
        score = receipt.get("score", {})
        steps = {s["step"]: s for s in receipt.get("steps", [])}
        perfect = bool(score.get("passed")) and int(score.get("points") or 0) == int(
            score.get("max_points") or -1
        )
        rows.append(
            {
                "id": entry["id"],
                "filename": entry.get("filename") or by_id.get(entry["id"], {}).get("filename"),
                "format": by_id.get(entry["id"], {}).get("format"),
                "status": entry.get("status"),
                "grade": entry.get("grade") or score.get("grade"),
                "perfect": perfect,
                "points": entry.get("points") or (
                    f"{score.get('points')}/{score.get('max_points')}" if score else None
                ),
                "pass_mode": score.get("pass_mode"),
                "skills": score.get("skills") or {},
                "compile_state": steps.get("compile-user-map", {}).get("state"),
                "user_map_points": steps.get("compile-user-map", {}).get("user_map_points"),
                "intake_skill": steps.get("intake", {}).get("skill"),
                "notes": score.get("notes", []),
                "pr_url": entry.get("pr_url") or "",
                "crashed": receipt.get("crashed", False),
            }
        )

    grades = Counter(r["grade"] for r in rows)
    formats = Counter(r["format"] for r in rows)
    compile_states = Counter(r["compile_state"] for r in rows if r["compile_state"])
    fails = [r for r in rows if not r.get("perfect")]
    passes = [r for r in rows if r.get("perfect")]
    prs = [r for r in rows if r.get("pr_url")]
    skill_fail_counts: Counter[str] = Counter()
    for r in rows:
        for skill, grade in (r.get("skills") or {}).items():
            if grade != "pass":
                skill_fail_counts[skill] += 1

    # Cluster fail notes
    note_hits: dict[str, list[str]] = defaultdict(list)
    for r in fails:
        for note in r.get("notes") or ["(no note)"]:
            note_hits[note].append(r["id"])

    top_lessons = sorted(note_hits.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:8]

    lines = []
    lines.append("# Map-matrix takeaways")
    lines.append("")
    lines.append(f"Run status: **{program.get('status')}**  ")
    lines.append(f"Round: **{program.get('round', 1)}**  ")
    lines.append(f"Pass mode: **all_evaluable (100% of scored skills)**  ")
    lines.append(f"Started: {program.get('started_at')}  ")
    lines.append(f"Finished: {program.get('completed_at')}  ")
    lines.append("")
    lines.append("## Scoreboard")
    lines.append("")
    lines.append(f"- Maps run: **{len(rows)}**")
    lines.append(f"- Perfect (all evaluable skills): **{len(passes)}**")
    lines.append(f"- Imperfect: **{len(fails)}**")
    lines.append(f"- Formats: " + ", ".join(f"{k}={v}" for k, v in sorted(formats.items())))
    lines.append(
        "- Compile states: "
        + (", ".join(f"{k}={v}" for k, v in sorted(compile_states.items())) or "none")
    )
    lines.append(f"- Improve PRs opened: **{len(prs)}**")
    if skill_fail_counts:
        lines.append(
            "- Skill misses: "
            + ", ".join(f"{k}={v}" for k, v in skill_fail_counts.most_common())
        )
    lines.append("")
    lines.append("## What worked")
    lines.append("")
    if passes:
        with_points = [r for r in passes if (r.get("user_map_points") or 0) > 0]
        lines.append(
            f"- {len(passes)} maps scored full credit on every evaluable skill."
        )
        lines.append(
            f"- {len(with_points)} of those produced a user-map with points + plan + tool pack."
        )
        examples = ", ".join(r["id"] for r in with_points[:5]) or "none yet"
        lines.append(f"- Strongest examples: {examples}")
    else:
        lines.append("- No maps are perfect yet. See failures below.")
    lines.append("")
    lines.append("## What broke")
    lines.append("")
    if not fails:
        lines.append("- Nothing material. Corpus is clear.")
    else:
        for note, ids in top_lessons:
            lines.append(f"- **{note}** — maps: {', '.join(ids[:6])}{'…' if len(ids) > 6 else ''}")
    lines.append("")
    lines.append("## Lessons learned")
    lines.append("")
    # Derive blunt PM-readable lessons
    lessons: list[str] = []
    if any("Intake failed" in (n or "") or "zero candidate" in (n or "") for r in fails for n in r.get("notes", [])):
        lessons.append(
            "Structured XLSX maps parse more reliably than dense PDF manuals; PDF intake needs better table recovery or page scoping."
        )
    if any("Compile state not acceptable" in (n or "") for r in fails for n in r.get("notes", [])):
        lessons.append(
            "Compile still dies or returns illegal states on some OEM layouts — those layouts are the next product bugs to fix."
        )
    if any("No user-map points" in (n or "") for r in fails for n in r.get("notes", [])):
        lessons.append(
            "Partial compiles without points are common on messy manuals; full-clear requires recoverable points, not just a legal hold state."
        )
    if any("Plan-reads" in (n or "") or "tool-pack" in (n or "").lower() for r in fails for n in r.get("notes", [])):
        lessons.append(
            "Downstream plan/tool-pack only clears when intake+compile produce a usable map artifact."
        )
    if any(r.get("crashed") for r in rows):
        lessons.append(
            "At least one map crashed the pipeline — treat crashes as P0; users never see a clean hold."
        )
    if prs:
        lessons.append(
            f"Improve opened {len(prs)} PR(s); the until-clear loop merges green ones between rounds."
        )
    if not lessons:
        lessons.append(
            "Corpus cleared every evaluable skill. Remaining work is live-device / capture-class skills outside this matrix."
        )
    for lesson in lessons:
        lines.append(f"- {lesson}")
    lines.append("")
    lines.append("## PRs to review")
    lines.append("")
    if not prs:
        lines.append("- None.")
    else:
        for r in prs:
            lines.append(f"- [{r['id']}]({r['pr_url']})")
    lines.append("")
    lines.append("## Per-map results")
    lines.append("")
    lines.append("| Map | Format | Perfect | Points | Compile | User points | Status |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['format']} | {'yes' if r.get('perfect') else 'no'} | {r['points']} | "
            f"{r['compile_state'] or '—'} | {r['user_map_points'] if r['user_map_points'] is not None else '—'} | {r['status']} |"
        )
    lines.append("")
    lines.append("_Private map files were not committed. Receipts live under `artifacts/pstack/map-matrix/`._")
    lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(
        json.dumps(
            {
                "scoreboard": {
                    "maps": len(rows),
                    "perfect": len(passes),
                    "imperfect": len(fails),
                    "grades": dict(grades),
                    "formats": dict(formats),
                    "compile_states": dict(compile_states),
                    "skill_misses": dict(skill_fail_counts),
                    "prs": len(prs),
                    "round": program.get("round"),
                },
                "lessons": lessons,
                "rows": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"takeaways": str(OUT_MD.relative_to(ROOT)), "maps": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
