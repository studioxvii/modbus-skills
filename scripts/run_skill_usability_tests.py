#!/usr/bin/env python3
"""Run the representative human-like skill usability campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from skill_usability.contracts import (  # noqa: E402
    CAMPAIGN_PATH,
    ContractError,
    load_campaign,
)
from skill_usability.reporting import build_report, write_report  # noqa: E402
from skill_usability.scenarios import make_adapter, run_trial  # noqa: E402
from skill_usability.sessions import SessionError, copy_plugin, hash_tree  # noqa: E402


_LOCAL_ONLY_OUTPUT_ROOTS = (ROOT / "artifacts", ROOT / "private")


class RunnerError(RuntimeError):
    """Campaign runner input or output is invalid."""


def _is_ignored_repo_path(path: Path) -> bool:
    try:
        relative = path.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return False
    result = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "check-ignore",
            "--quiet",
            "--no-index",
            "--",
            relative,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def validate_output_path(output: Path) -> Path:
    resolved = output.resolve()
    if resolved.exists() and any(resolved.iterdir()):
        raise RunnerError("output directory must be empty")
    repository = ROOT.resolve()
    if resolved != repository and repository not in resolved.parents:
        return resolved
    for local_root in _LOCAL_ONLY_OUTPUT_ROOTS:
        local_root = local_root.resolve()
        if (resolved == local_root or local_root in resolved.parents) and _is_ignored_repo_path(resolved):
            return resolved
    raise RunnerError(
        "--output inside the repository must be below an ignored artifacts/ or private/ directory"
    )


def freeze_campaign_inputs(
    campaign: dict[str, Any], *, campaign_dir: Path, destination: Path,
    plugin_source: Path | None = None,
) -> tuple[Path, Path]:
    """Take one verified input copy before starting any per-trial budget."""
    destination.mkdir()
    plugin = copy_plugin(destination, source=plugin_source)
    frozen_campaign = destination / "campaign"
    frozen_campaign.mkdir()
    relative_paths = sorted({
        fixture["path"]
        for scenario in campaign["loaded_scenarios"]
        for fixture in scenario.get("fixtures", ())
    })

    def fixture_hashes(root: Path) -> dict[str, str]:
        result = {}
        for relative in relative_paths:
            path = (root / relative).resolve()
            if not path.is_relative_to(root.resolve()):
                raise RunnerError("fixture escapes campaign directory")
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    before = fixture_hashes(campaign_dir)
    for relative in relative_paths:
        target = frozen_campaign / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(campaign_dir / relative, target)
    if before != fixture_hashes(campaign_dir) or before != fixture_hashes(frozen_campaign):
        raise RunnerError("campaign fixtures changed while freezing inputs")
    config_hash = hashlib.sha256(json.dumps(campaign, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    campaign["input_snapshot"] = {
        "plugin_hash": hash_tree(plugin),
        "fixture_hashes": before,
        "fixture_tree_hash": hash_tree(frozen_campaign),
        "loaded_configuration_hash": config_hash,
        "copy_verified": True,
    }
    return plugin, frozen_campaign


def run_campaign(
    *,
    mode: str,
    output: Path,
    campaign_path: Path | None = None,
    scenario_ids: list[str] | None = None,
    repetitions_override: int | None = None,
    model: str | None = None,
    plugin_source: Path | None = None,
) -> dict[str, Any]:
    campaign = load_campaign(campaign_path)
    if scenario_ids:
        unknown = set(scenario_ids) - set(campaign["scenarios"])
        if unknown:
            raise RunnerError("unknown scenario selection")
        campaign["scenarios"] = [name for name in campaign["scenarios"] if name in scenario_ids]
        campaign["loaded_scenarios"] = [item for item in campaign["loaded_scenarios"] if item["scenario_id"] in scenario_ids]
    if repetitions_override is not None:
        if not 1 <= repetitions_override <= 20:
            raise RunnerError("repetitions must be 1 through 20")
        campaign["real_model_repetitions"] = repetitions_override
    if model:
        campaign["worker_model"] = model
    adapter = make_adapter(mode, model=campaign["worker_model"], budget=campaign["budget"])
    repetitions = 1
    if mode == "real-model":
        repetitions = int(campaign.get("real_model_repetitions") or campaign.get("repetitions") or 1)
    trials: list[dict[str, Any]] = []
    plugin_hash = None
    temporary = tempfile.TemporaryDirectory(prefix="skill-usability-parent-")
    try:
        parent = Path(temporary.name)
        frozen_plugin, frozen_campaign = freeze_campaign_inputs(
            campaign, campaign_dir=(campaign_path or CAMPAIGN_PATH).parent,
            destination=parent / "frozen-inputs", plugin_source=plugin_source,
        )
        plugin_hash = campaign["input_snapshot"]["plugin_hash"]

        def verify_snapshot() -> None:
            snapshot = campaign["input_snapshot"]
            if hash_tree(frozen_plugin) != plugin_hash or hash_tree(frozen_campaign) != snapshot["fixture_tree_hash"]:
                campaign.setdefault("integrity_issue_codes", []).append("campaign-input-snapshot-changed")
                write_report(output, build_report(campaign=campaign, trials=trials,
                    mode=mode, adapter=adapter.name, plugin_hash=plugin_hash))
                raise RunnerError("frozen campaign inputs changed")

        for scenario in campaign["loaded_scenarios"]:
            for repetition in range(1, repetitions + 1):
                verify_snapshot()
                trial = run_trial(
                    scenario,
                    adapter=adapter,
                    campaign_dir=frozen_campaign,
                    plugin_source=frozen_plugin,
                    parent=parent,
                    budget=campaign["budget"],
                    repetition=repetition,
                    evidence_root=output / "raw" if mode == "real-model" else None,
                )
                trials.append(trial)
                verify_snapshot()
                # A later crash must not erase completed trials or look like a
                # complete campaign. Coverage checks keep checkpoints incomplete.
                write_report(output, build_report(campaign=campaign, trials=trials,
                    mode=mode, adapter=adapter.name, plugin_hash=plugin_hash))
    finally:
        try:
            temporary.cleanup()
        except OSError:
            campaign.setdefault("integrity_issue_codes", []).append("campaign-cleanup-failed")
            write_report(output, build_report(campaign=campaign, trials=trials,
                mode=mode, adapter=adapter.name, plugin_hash=plugin_hash))
            raise
    report = build_report(
        campaign=campaign,
        trials=trials,
        mode=mode,
        adapter=adapter.name,
        plugin_hash=plugin_hash,
    )
    write_report(output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("deterministic", "real-model"), default="deterministic")
    parser.add_argument("--output", required=True)
    parser.add_argument("--campaign", default=str(CAMPAIGN_PATH))
    parser.add_argument("--scenario", action="append", help="Run a named subset; report coverage reflects that subset only")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--model", help="Explicit real-session model, recorded in the report")
    args = parser.parse_args(argv)
    try:
        output = validate_output_path(Path(args.output))
        report = run_campaign(mode=args.mode, output=output, campaign_path=Path(args.campaign), scenario_ids=args.scenario, repetitions_override=args.repetitions, model=args.model)
    except (ContractError, RunnerError, SessionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "report": "skill-usability-report.json"}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
