#!/usr/bin/env python3
"""Run the map-matrix pipeline for one map and write a scored receipt."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "plugins" / "modbus-skills" / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(ROOT / "scripts" / "pstack"))

# Prefer the lab venv site-packages when present so pdfplumber grid recovery
# works under the same interpreter the matrix worker already uses.
_VENV_SITE = ROOT / ".venv-lab" / "lib" / "python3.14" / "site-packages"
if _VENV_SITE.is_dir():
    site = str(_VENV_SITE)
    if site not in sys.path:
        sys.path.insert(0, site)
    existing = os.environ.get("PYTHONPATH", "")
    parts = [part for part in existing.split(os.pathsep) if part]
    if site not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([site, *parts])

from modbus_skills.cli import run_cli  # noqa: E402
from modbus_skills.parsers import parse_source  # noqa: E402

MATRIX = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts" / "pstack" / "map-matrix"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def step(name: str, **fields: Any) -> dict[str, Any]:
    return {"step": name, "at": utc_now(), **fields}


def run_cli_capture(command: str, args: list[str]) -> tuple[int, str]:
    import contextlib
    import io

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_cli(command, args)
    text = (out.getvalue() + err.getvalue()).strip()
    return code, text


def score_receipt(receipt: dict[str, Any], evals: dict[str, Any]) -> dict[str, Any]:
    """Score a map receipt.

    ``pass_mode=all_evaluable`` (default for overnight clear loops) requires
    every weighted criterion — i.e. every evaluable skill in the pipeline —
    plus no crash. Soft ``threshold`` mode keeps the older ≥N bar.
    """

    weights = evals["score_weights"]
    points = 0
    max_points = sum(weights.values())
    notes: list[str] = []
    skills: dict[str, str] = {}
    steps = {s["step"]: s for s in receipt.get("steps", [])}

    intake = steps.get("intake", {})
    if intake.get("passed") and int(intake.get("records") or 0) > 0:
        points += weights["intake_ok"]
        skills[intake.get("skill") or "intake"] = "pass"
    elif intake.get("passed"):
        notes.append("Intake CLI succeeded but produced zero candidate records.")
        skills[intake.get("skill") or "intake"] = "fail"
    else:
        notes.append("Intake failed — file did not yield usable candidates.")
        skills[intake.get("skill") or "intake"] = "fail"

    if "normalize_ok" in weights:
        normalize = steps.get("normalize", {})
        if normalize.get("passed"):
            points += weights["normalize_ok"]
            skills["normalize-map"] = "pass"
        else:
            notes.append("Normalize failed or was not reached.")
            skills["normalize-map"] = "fail"

    if "check_map_ok" in weights:
        check = steps.get("check-map", {})
        if check.get("passed"):
            points += weights["check_map_ok"]
            skills["check-map"] = "pass"
        else:
            notes.append("Check-map failed or was not reached.")
            skills["check-map"] = "fail"

    compile_step = steps.get("compile-user-map", {})
    state = compile_step.get("state")
    if compile_step.get("passed") and state in evals["acceptable_compile_states"]:
        points += weights["compile_legal_state"]
        skills["compile-user-map"] = "pass"
    else:
        notes.append(f"Compile state not acceptable: {state!r}.")
        skills["compile-user-map"] = "fail"

    if not receipt.get("crashed"):
        points += weights["no_crash"]
    else:
        notes.append(f"Pipeline crashed: {receipt.get('crash')}")

    user_map = compile_step.get("user_map_points")
    if user_map and user_map > 0:
        points += weights["user_map_when_points"]
        if compile_step.get("lineage"):
            points += weights["lineage"]
        else:
            notes.append("User map points missing source lineage.")
    elif compile_step.get("passed") and state in {"awaiting-source-decision", "partial"}:
        notes.append("No user-map points yet (holds or source decision).")
    elif compile_step.get("passed"):
        notes.append("Compile finished without user-map points.")
    else:
        notes.append("Compile did not produce user-map points.")

    plan = steps.get("plan-reads", {})
    if plan.get("passed"):
        points += weights["downstream_plan"]
        skills["plan-reads"] = "pass"
    elif plan.get("skipped"):
        notes.append("Plan-reads skipped (no map ready).")
        skills["plan-reads"] = "fail"
    else:
        notes.append("Plan-reads failed.")
        skills["plan-reads"] = "fail"

    if "tool_pack_ok" in weights:
        pack = steps.get("build-tool-pack", {})
        if pack.get("passed"):
            points += weights["tool_pack_ok"]
            skills["build-tool-pack"] = "pass"
        else:
            notes.append("Build-tool-pack failed or was not reached.")
            skills["build-tool-pack"] = "fail"

    pass_mode = evals.get("pass_mode", "threshold")
    threshold = int(evals.get("pass_threshold", max_points))
    if pass_mode == "all_evaluable":
        passed = points == max_points and not receipt.get("crashed")
        effective_threshold = max_points
    else:
        passed = points >= threshold and not receipt.get("crashed")
        effective_threshold = threshold

    return {
        "points": points,
        "max_points": max_points,
        "pass_threshold": effective_threshold,
        "pass_mode": pass_mode,
        "passed": passed,
        "notes": notes,
        "skills": skills,
        "grade": "pass" if passed else "fail",
    }


def build_local_ocr_evidence(
    source: Path, *, first_page: int, last_page: int, work: Path
) -> Path | None:
    """Build bounded modbus-ocr-evidence/v1 via local pdftoppm + tesseract.

    Used only by the map-matrix harness when pdftotext finds no text. The skill
    itself still refuses to install or invoke OCR; this supplies the evidence
    artifact the skill already accepts.
    """

    if shutil.which("pdftoppm") is None or shutil.which("tesseract") is None:
        return None
    if last_page < first_page or last_page - first_page + 1 > 40:
        return None
    ocr_dir = work / "ocr-render"
    if ocr_dir.exists():
        shutil.rmtree(ocr_dir)
    ocr_dir.mkdir(parents=True)
    prefix = ocr_dir / "page"
    try:
        render = subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-r",
                "300",
                "-f",
                str(first_page),
                "-l",
                str(last_page),
                str(source),
                str(prefix),
            ],
            capture_output=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if render.returncode != 0:
        return None
    rendered = sorted(ocr_dir.glob("page*.png"))
    if not rendered:
        return None
    # PDFs shorter than the requested window still succeed; use what rendered.
    effective_last = first_page + len(rendered) - 1
    try:
        version = subprocess.run(
            ["tesseract", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        tool_version = (version.stderr or version.stdout or "unknown").splitlines()[0].strip()
    except (OSError, subprocess.TimeoutExpired):
        tool_version = "unknown"
    pages: list[dict[str, Any]] = []
    for page in range(first_page, effective_last + 1):
        image = rendered[page - first_page]
        try:
            ocr = subprocess.run(
                ["tesseract", str(image), "stdout", "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        text = (ocr.stdout or "").replace("\f", "\n").strip()
        if not text:
            text = f"(empty OCR page {page})"
        pages.append({"page_index": page, "text": text[:200_000]})
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    evidence = {
        "schema_version": "modbus-ocr-evidence/v1",
        "artifact_type": "modbus-ocr-evidence",
        "input_hashes": {"source_pdf": digest},
        "assumptions": [],
        "findings": [],
        "holds": [],
        "source_sha256": digest,
        "tool": {"name": "tesseract", "version": tool_version[:100]},
        "pages": pages,
    }
    path = work / "ocr-evidence.json"
    save_json(path, evidence)
    return path


def build_request(
    source: Path,
    fmt: str,
    work: Path,
    *,
    pages: str | None = None,
    ocr_evidence: Path | None = None,
) -> Path:
    source_obj: dict[str, Any] = {"path": str(source.resolve()), "format": fmt}
    if pages:
        source_obj["pages"] = pages
    if ocr_evidence is not None:
        source_obj["ocr_evidence"] = str(ocr_evidence.resolve())
    request = {
        "schema_version": "modbus-compile-request/v1",
        "source": source_obj,
        "selection_template": {
            "schema_version": "modbus-user-selection-template/v1",
            "requested_measurements": [
                "voltage",
                "current",
                "power",
                "energy",
                "status",
                "temperature",
            ],
            "mode": "all-readable",
        },
        "targets": [],
        "target_options": {},
    }
    path = work / "request.json"
    save_json(path, request)
    return path



def run_map(map_entry: dict[str, Any], evals: dict[str, Any]) -> dict[str, Any]:
    map_id = map_entry["id"]
    source = ROOT / map_entry["relative_path"]
    work = ARTIFACTS / map_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    receipt: dict[str, Any] = {
        "schema_version": "pstack-map-matrix-receipt/v1",
        "map_id": map_id,
        "filename": map_entry["filename"],
        "format": map_entry["format"],
        "converted_from": map_entry.get("converted_from"),
        "source_sha256": map_entry["sha256"],
        "started_at": utc_now(),
        "steps": [],
        "crashed": False,
        "not_applicable": evals.get("not_applicable", {}),
    }
    started = time.monotonic()
    budget = int(evals.get("budgets", {}).get("map_wall_seconds", 600))

    try:
        # classify
        fmt = map_entry["format"]
        receipt["steps"].append(
            step(
                "classify",
                passed=fmt in {"pdf", "xlsx"},
                format=fmt,
                bytes=map_entry["bytes"],
                pipeline_class=map_entry.get("pipeline_class"),
            )
        )
        if fmt not in {"pdf", "xlsx"}:
            receipt["finished_at"] = utc_now()
            receipt["score"] = score_receipt(receipt, evals)
            return receipt

        candidate_path: Path | None = None
        compile_pages: str | None = None
        compile_ocr_evidence: Path | None = None
        # intake
        if fmt == "xlsx":
            t0 = time.monotonic()
            try:
                parsed = parse_source(source.read_bytes(), filename=source.name)
                records = parsed.get("records", [])
                candidate_path = work / "candidates.json"
                save_json(candidate_path, parsed)
                receipt["steps"].append(
                    step(
                        "intake",
                        skill="parse-map",
                        passed=len(records) > 0,
                        records=len(records),
                        duration_s=round(time.monotonic() - t0, 2),
                        detail=f"{len(records)} records",
                    )
                )
            except Exception as exc:  # noqa: BLE001 — record as intake fail, still compile
                receipt["steps"].append(
                    step(
                        "intake",
                        skill="parse-map",
                        passed=False,
                        records=0,
                        duration_s=round(time.monotonic() - t0, 2),
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )
        else:
            t0 = time.monotonic()
            out_dir = work / "pdf-extract"
            out_dir.mkdir(parents=True, exist_ok=True)
            page_cap = int(evals.get("budgets", {}).get("pdf_page_cap", 40))
            pages_used = "auto-discovery"
            code, text = run_cli_capture(
                "extract-pdf-map",
                ["--input", str(source), "--output", str(out_dir)],
            )

            def _load_records(directory: Path) -> tuple[Path | None, int]:
                found: Path | None = None
                for name in ("candidates.json", "candidate-map.json", "extraction.json"):
                    hit = directory / name
                    if hit.is_file():
                        found = hit
                        break
                if found is None:
                    for hit in directory.rglob("*.json"):
                        found = hit
                        break
                count = 0
                if found and found.is_file():
                    try:
                        payload = load_json(found)
                        count = len(
                            payload.get("records") or payload.get("candidates") or payload.get("points") or []
                        )
                    except Exception:
                        count = 0
                return found, count

            candidate_path, records = _load_records(out_dir)
            # Let the runtime's bounded discovery inspect the whole source.
            # An early nonempty table must not hide another table after page 40.
            # Do not repeat identical discovery when no rows were found.
            # Scanned manuals: build local OCR evidence and retry once.
            if code == 0 and records == 0:
                ocr_last = min(page_cap, 20)
                ocr_path = build_local_ocr_evidence(
                    source, first_page=1, last_page=ocr_last, work=work
                )
                if ocr_path is not None:
                    evidence = load_json(ocr_path)
                    page_indexes = [
                        int(page["page_index"])
                        for page in evidence.get("pages", [])
                        if isinstance(page, dict) and isinstance(page.get("page_index"), int)
                    ]
                    if page_indexes:
                        ocr_pages = f"{min(page_indexes)}-{max(page_indexes)}"
                        out_dir_ocr = work / "pdf-extract-ocr"
                        out_dir_ocr.mkdir(parents=True, exist_ok=True)
                        code_ocr, text_ocr = run_cli_capture(
                            "extract-pdf-map",
                            [
                                "--input",
                                str(source),
                                "--output",
                                str(out_dir_ocr),
                                "--pages",
                                ocr_pages,
                                "--ocr-evidence",
                                str(ocr_path),
                            ],
                        )
                        candidate_path_ocr, records_ocr = _load_records(out_dir_ocr)
                        if code_ocr == 0 and records_ocr > records:
                            code, text = code_ocr, text_ocr
                            candidate_path, records = candidate_path_ocr, records_ocr
                            pages_used = f"ocr-{ocr_pages}"
                            compile_pages = ocr_pages
                            compile_ocr_evidence = ocr_path
            # PDF intake can legally yield 0 with a hold — pass if CLI succeeded
            receipt["steps"].append(
                step(
                    "intake",
                    skill="extract-pdf-map",
                    passed=code == 0,
                    records=records,
                    duration_s=round(time.monotonic() - t0, 2),
                    detail=(text[-1500:] if text else f"exit={code}")[:1500],
                    pages=pages_used,
                )
            )

        # normalize + check when we have candidates with records
        canonical: Path | None = None
        if candidate_path and candidate_path.is_file():
            t0 = time.monotonic()
            canonical = work / "canonical.json"
            code, text = run_cli_capture(
                "normalize-map",
                ["--input", str(candidate_path), "--output", str(canonical)],
            )
            receipt["steps"].append(
                step(
                    "normalize",
                    skill="normalize-map",
                    passed=code == 0 and canonical.is_file(),
                    duration_s=round(time.monotonic() - t0, 2),
                    detail=text[-1000:] if text else f"exit={code}",
                )
            )
            if code == 0 and canonical.is_file():
                t0 = time.monotonic()
                lint = work / "lint.json"
                code2, text2 = run_cli_capture(
                    "lint-map",
                    ["--input", str(canonical), "--output", str(lint)],
                )
                receipt["steps"].append(
                    step(
                        "check-map",
                        skill="check-map",
                        passed=code2 == 0,
                        duration_s=round(time.monotonic() - t0, 2),
                        detail=text2[-1000:] if text2 else f"exit={code2}",
                    )
                )

        # compile-user-map always
        t0 = time.monotonic()
        request = build_request(
            source,
            fmt,
            work,
            pages=compile_pages,
            ocr_evidence=compile_ocr_evidence,
        )
        compile_out = work / "compile"
        code, text = run_cli_capture(
            "compile-user-map",
            ["--request", str(request), "--output", str(compile_out)],
        )
        compile_result_path = compile_out / "compile-result.json"
        state = "unknown"
        user_points = 0
        lineage = False
        if compile_result_path.is_file():
            result = load_json(compile_result_path)
            state = str(result.get("state", "unknown"))
        user_map_path = compile_out / "output" / "user-map.json"
        if user_map_path.is_file():
            user_map = load_json(user_map_path)
            points = user_map.get("points") or []
            user_points = len(points)
            lineage = bool(points) and all(p.get("source_refs") for p in points)
        receipt["steps"].append(
            step(
                "compile-user-map",
                skill="compile-user-map",
                passed=code == 0 and state in evals["acceptable_compile_states"],
                state=state,
                user_map_points=user_points,
                lineage=lineage,
                duration_s=round(time.monotonic() - t0, 2),
                detail=text[-1200:] if text else f"exit={code}",
            )
        )

        # plan-reads + tool pack when we have a usable map artifact
        map_for_plan = None
        oem_map = compile_out / "artifacts" / "oem-map.json"
        if oem_map.is_file():
            map_for_plan = oem_map
        elif canonical and canonical.is_file():
            map_for_plan = canonical
        elif user_map_path.is_file() and user_points > 0:
            map_for_plan = user_map_path

        if map_for_plan is not None:
            t0 = time.monotonic()
            plan = work / "read-plan.json"
            code, text = run_cli_capture(
                "compile-read-plan",
                ["--input", str(map_for_plan), "--output", str(plan)],
            )
            receipt["steps"].append(
                step(
                    "plan-reads",
                    skill="plan-reads",
                    passed=code == 0 and plan.is_file(),
                    duration_s=round(time.monotonic() - t0, 2),
                    detail=text[-1000:] if text else f"exit={code}",
                )
            )
            if code == 0 and plan.is_file():
                t0 = time.monotonic()
                pack_req = {
                    "schema_version": "modbus-tool-pack-request/v1",
                    "map": str(map_for_plan),
                    "plan": str(plan),
                    "targets": ["node-red", "modpoll", "modscan"],
                    "mode": "probe",
                }
                pack_req_path = work / "tool-pack-request.json"
                save_json(pack_req_path, pack_req)
                pack_out = work / "tool-pack"
                code3, text3 = run_cli_capture(
                    "build-tool-pack",
                    ["--request", str(pack_req_path), "--output", str(pack_out)],
                )
                receipt["steps"].append(
                    step(
                        "build-tool-pack",
                        skill="build-tool-pack",
                        passed=code3 == 0,
                        duration_s=round(time.monotonic() - t0, 2),
                        detail=text3[-1000:] if text3 else f"exit={code3}",
                    )
                )
        else:
            receipt["steps"].append(
                step("plan-reads", skill="plan-reads", passed=False, skipped=True, detail="no map ready")
            )

        # wall budget note
        elapsed = time.monotonic() - started
        receipt["duration_s"] = round(elapsed, 2)
        receipt["over_budget"] = elapsed > budget

    except Exception as exc:  # noqa: BLE001
        receipt["crashed"] = True
        receipt["crash"] = f"{type(exc).__name__}: {exc}"
        receipt["traceback"] = traceback.format_exc()[-3000:]
        receipt["duration_s"] = round(time.monotonic() - started, 2)

    receipt["finished_at"] = utc_now()
    receipt["score"] = score_receipt(receipt, evals)
    save_json(work / "receipt.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map-id", required=True)
    args = parser.parse_args(argv)

    manifest = load_json(MATRIX / "manifest.json")
    evals = load_json(MATRIX / "evals.json")
    entry = next((m for m in manifest["maps"] if m["id"] == args.map_id), None)
    if entry is None:
        print(f"unknown map id: {args.map_id}", file=sys.stderr)
        return 1
    # verify hash still matches
    path = ROOT / entry["relative_path"]
    if not path.is_file():
        print(f"missing source: {path}", file=sys.stderr)
        return 1
    if sha256(path) != entry["sha256"]:
        print(f"sha256 mismatch for {args.map_id}", file=sys.stderr)
        return 1

    receipt = run_map(entry, evals)
    print(
        json.dumps(
            {
                "map_id": args.map_id,
                "grade": receipt["score"]["grade"],
                "points": receipt["score"]["points"],
                "max_points": receipt["score"]["max_points"],
                "receipt": f"artifacts/pstack/map-matrix/{args.map_id}/receipt.json",
            }
        )
    )
    return 0 if receipt["score"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
