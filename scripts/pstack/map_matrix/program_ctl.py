#!/usr/bin/env python3
"""Claim next pending map from program.json under an exclusive lock."""

from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
from collections import Counter
from pathlib import Path


MATRIX = Path(__file__).resolve().parent
PROGRAM = MATRIX / "program.json"
LOCK = MATRIX / "program.lock"
ARTIFACTS = MATRIX.parents[2] / "artifacts" / "pstack" / "map-matrix"


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def with_lock(fn):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load() -> dict:
    return json.loads(PROGRAM.read_text(encoding="utf-8"))


def save(program: dict) -> None:
    PROGRAM.write_text(json.dumps(program, indent=2) + "\n", encoding="utf-8")


def _receipt_perfect(map_id: str) -> bool:
    path = ARTIFACTS / map_id / "receipt.json"
    if not path.is_file():
        return False
    receipt = json.loads(path.read_text(encoding="utf-8"))
    score = receipt.get("score") or {}
    if score.get("pass_mode") != "all_evaluable":
        return False
    return bool(score.get("passed")) and int(score.get("points") or 0) == int(score.get("max_points") or -1)


def claim(worker_id: str) -> dict | None:
    def _claim():
        program = load()
        for entry in program["maps"]:
            if entry["status"] == "pending":
                entry["status"] = "running"
                entry["worker_id"] = worker_id
                entry["started_at"] = utc_now()
                program["current"] = {"id": entry["id"], "worker_id": worker_id, "status": "running"}
                if program.get("started_at") is None:
                    program["started_at"] = utc_now()
                program["status"] = "running"
                program.setdefault("history", []).append(
                    {"at": utc_now(), "id": entry["id"], "status": "running", "worker_id": worker_id}
                )
                save(program)
                return {"id": entry["id"]}
        return None

    return with_lock(_claim)


def finish(map_id: str, status: str, **extra) -> None:
    def _finish():
        program = load()
        for entry in program["maps"]:
            if entry["id"] == map_id:
                entry["status"] = status
                entry["finished_at"] = utc_now()
                entry.update(extra)
                break
        program["current"] = {"id": map_id, "status": status}
        program.setdefault("history", []).append(
            {"at": utc_now(), "id": map_id, "status": status, **extra}
        )
        pending = sum(1 for m in program["maps"] if m["status"] in {"pending", "running"})
        if pending == 0:
            program["status"] = "complete"
            program["completed_at"] = utc_now()
        save(program)

    with_lock(_finish)


def status() -> dict:
    program = load()
    counts = Counter(m["status"] for m in program["maps"])
    imperfect = []
    for m in program["maps"]:
        if m.get("status") in {"passed", "pr_opened"} and _receipt_perfect(m["id"]):
            continue
        if m.get("status") in {"pending", "running"}:
            imperfect.append(m["id"])
            continue
        if not _receipt_perfect(m["id"]):
            imperfect.append(m["id"])
    perfect = len(program["maps"]) - len(imperfect)
    return {
        "status": program.get("status"),
        "phase": program.get("phase"),
        "round": program.get("round"),
        "counts": dict(counts),
        "pending": counts.get("pending", 0),
        "running": counts.get("running", 0),
        "perfect": perfect,
        "imperfect": len(imperfect),
        "imperfect_ids": imperfect,
        "clear": len(imperfect) == 0
        and counts.get("pending", 0) == 0
        and counts.get("running", 0) == 0,
        "completed_at": program.get("completed_at"),
    }


def init_program(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Drop prior receipts so perfect/clear only reflects this run's scores.
    if ARTIFACTS.is_dir():
        for child in ARTIFACTS.iterdir():
            if child.is_dir():
                (child / "receipt.json").unlink(missing_ok=True)
    program = {
        "schema_version": "pstack-map-matrix-program/v2",
        "program_id": "map-matrix-until-clear",
        "status": "armed",
        "phase": "maps",
        "round": 1,
        "started_at": None,
        "completed_at": None,
        "current": None,
        "settings": {
            "concurrency": 4,
            "auto_improve": True,
            "auto_pr": True,
            "pass_mode": "all_evaluable",
            "base_branch": "main",
            "branch_prefix": "pstack/map",
        },
        "maps": [{"id": m["id"], "status": "pending", "filename": m["filename"]} for m in manifest["maps"]],
        "history": [],
    }
    save(program)


def requeue_imperfect(round_number: int | None = None) -> dict:
    def _requeue():
        program = load()
        reset: list[str] = []
        kept: list[str] = []
        for entry in program["maps"]:
            if _receipt_perfect(entry["id"]) and entry.get("status") in {"passed", "pr_opened"}:
                entry["status"] = "passed"
                kept.append(entry["id"])
                continue
            (ARTIFACTS / entry["id"] / "receipt.json").unlink(missing_ok=True)
            entry["status"] = "pending"
            entry.pop("worker_id", None)
            entry.pop("started_at", None)
            entry.pop("finished_at", None)
            entry.pop("grade", None)
            entry.pop("points", None)
            reset.append(entry["id"])
        if round_number is not None:
            program["round"] = round_number
        else:
            program["round"] = int(program.get("round") or 1) + 1
        program["status"] = "armed"
        program["completed_at"] = None
        program["phase"] = "maps"
        program.setdefault("history", []).append(
            {
                "at": utc_now(),
                "event": "requeue_imperfect",
                "round": program["round"],
                "reset": reset,
                "kept_perfect": kept,
            }
        )
        save(program)
        return {"round": program["round"], "requeued": reset, "perfect": kept}

    return with_lock(_requeue)


def list_imperfect() -> list[str]:
    program = load()
    out = []
    for m in program["maps"]:
        if m.get("status") in {"passed", "pr_opened"} and _receipt_perfect(m["id"]):
            continue
        out.append(m["id"])
    return out


def is_clear() -> bool:
    return bool(status().get("clear"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    init_p = sub.add_parser("init")
    init_p.add_argument("--manifest", default=str(MATRIX / "manifest.json"))
    claim_p = sub.add_parser("claim")
    claim_p.add_argument("--worker-id", required=True)
    finish_p = sub.add_parser("finish")
    finish_p.add_argument("--map-id", required=True)
    finish_p.add_argument("--status", required=True)
    finish_p.add_argument("--receipt", default="")
    finish_p.add_argument("--grade", default="")
    finish_p.add_argument("--points", default="")
    finish_p.add_argument("--pr-url", default="")
    requeue_p = sub.add_parser("requeue-imperfect")
    requeue_p.add_argument("--round", type=int, default=None)
    sub.add_parser("list-imperfect")
    sub.add_parser("is-clear")
    args = parser.parse_args(argv)

    if args.cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if args.cmd == "init":
        init_program(Path(args.manifest))
        print(json.dumps({"initialized": True, "maps": len(load()["maps"]), "round": 1}))
        return 0
    if args.cmd == "claim":
        item = claim(args.worker_id)
        if item is None:
            print(json.dumps({"done": True}))
            return 0
        print(json.dumps(item))
        return 0
    if args.cmd == "finish":
        extra = {}
        if args.receipt:
            extra["receipt"] = args.receipt
        if args.grade:
            extra["grade"] = args.grade
        if args.points:
            extra["points"] = args.points
        if args.pr_url:
            extra["pr_url"] = args.pr_url
        finish(args.map_id, args.status, **extra)
        print(json.dumps({"finished": args.map_id, "status": args.status}))
        return 0
    if args.cmd == "requeue-imperfect":
        print(json.dumps(requeue_imperfect(args.round), indent=2))
        return 0
    if args.cmd == "list-imperfect":
        ids = list_imperfect()
        print(json.dumps({"imperfect": ids, "count": len(ids)}))
        return 0
    if args.cmd == "is-clear":
        clear = is_clear()
        print(json.dumps({"clear": clear, **status()}))
        return 0 if clear else 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
