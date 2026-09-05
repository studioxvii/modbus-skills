"""Bounded user actor and trial driver."""

from __future__ import annotations

import time
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from .contracts import CAMPAIGN_DIR, ContractError, load_campaign
from .oracles import evaluate_trial
from .sessions import (
    CodexSessionAdapter,
    FakeSessionAdapter,
    PreflightUnavailable,
    SessionAdapter,
    SessionError,
    TrialSession,
    interrupt_and_continue,
    seed_workspace,
    tamper_durable_case,
)


class BoundedUserActor:
    """Persona-shaped prompts drawn only from versioned scenario facts."""

    def __init__(self, scenario: Mapping[str, Any]) -> None:
        self.scenario = scenario
        self.facts = dict(scenario.get("permitted_facts") or {})
        self.prompts = dict(scenario.get("prompts") or {})
        self.used: set[str] = set()

    def opening(self) -> str:
        self.used.add("opening")
        return str(self.prompts["opening"])

    def reply(self, events: list[dict[str, Any]]) -> str | None:
        questions = [event for event in events if event.get("kind") == "question"]
        if not questions:
            return None
        for rule in self.scenario.get("response_rules", ()):
            prompt_id = str(rule.get("prompt_id"))
            if prompt_id in self.used:
                continue
            fact = str(rule.get("fact"))
            if fact not in self.facts:
                raise ContractError(f"actor cannot invent fact {fact}")
            self.used.add(prompt_id)
            return str(self.prompts[prompt_id])
        for step in self.scenario.get("transitions", ()):
            if step.get("kind") == "reply-if-asked" and str(step["prompt_id"]) not in self.used:
                self.used.add(str(step["prompt_id"]))
                return str(self.prompts[step["prompt_id"]])
        # Preserve the unanswered question for the outcome oracle; do not
        # manufacture a fact or turn an ordinary worker question into a crash.
        return None

    def prompt(self, prompt_id: str) -> str:
        if prompt_id not in self.prompts:
            raise ContractError(f"unknown prompt {prompt_id}")
        self.used.add(prompt_id)
        return str(self.prompts[prompt_id])


def run_trial(
    scenario: Mapping[str, Any],
    *,
    adapter: SessionAdapter,
    campaign_dir: Path | None = None,
    parent: Path,
    budget: Mapping[str, Any],
    repetition: int = 1,
    evidence_root: Path | None = None,
    plugin_source: Path | None = None,
) -> dict[str, Any]:
    directory = campaign_dir or CAMPAIGN_DIR
    started = time.monotonic()
    session: TrialSession | None = None
    execution_status = "completed"
    missing = None
    worker_started = False
    failure_codes: set[str] = set()
    result: dict[str, Any] = {}
    try:
        try:
            adapter.preflight()
            session = seed_workspace(scenario, campaign_dir=directory, parent=parent,
                                     plugin_source=plugin_source)
            session.state["deadline"] = started + int(budget.get("max_seconds", 120))
            adapter.start(session)
            worker_started = True
            actor = BoundedUserActor(scenario)
            steps = list(scenario.get("transitions", ()))

            def check_deadline() -> None:
                if time.monotonic() >= session.state["deadline"]:
                    raise SessionError("budget-exceeded")

            def turn(text: str) -> list[dict[str, Any]]:
                check_deadline()
                if session.turn_count >= int(budget.get("max_turns", 8)):
                    raise SessionError("turn-budget-exceeded")
                events = adapter.turn(session, text)
                check_deadline()
                if session.tool_calls > int(budget.get("max_tool_calls", 20)):
                    raise SessionError("tool-call-budget-exceeded")
                word_limit = scenario.get("attention_budget", {}).get("max_final_words")
                if adapter.name == "codex" and word_limit is not None:
                    words = len(session.state.get("final_text", "").split())
                    if words > int(word_limit):
                        failure_codes.add("final-output-budget-exceeded")
                        raise SessionError("final-output-budget-exceeded")
                return events

            for index, step in enumerate(steps):
                check_deadline()
                kind = step["kind"]
                if kind == "supply-capture":
                    source = session.fixtures / Path(str(scenario["fixtures"][0]["path"])).name
                    if step.get("fixture_id"):
                        match = next(item for item in scenario["fixtures"] if item["id"] == step["fixture_id"])
                        source = session.fixtures / Path(match["path"]).name
                    (session.work / "capture.json").write_bytes(source.read_bytes())
                    session.events.append({"kind": "external-gate", "fixture": source.name})
                elif kind == "tamper":
                    if step.get("artifact") != "case.json":
                        raise SessionError("unsupported-tamper-artifact")
                    tamper_durable_case(session)
                elif kind == "interrupt":
                    session = interrupt_and_continue(adapter, session)
                elif kind in {"prompt", "reply-if-asked"}:
                    if kind == "prompt":
                        text = actor.prompt(str(step["prompt_id"]))
                    elif session.awaiting_user:
                        text = actor.reply(session.events)
                        if text is None:
                            continue
                        session.events.append({"kind": "actor-response", "prompt_ids": sorted(actor.used)})
                    else:
                        continue
                    events = turn(text)
                    # A terminal turn is not a terminal scenario. In particular,
                    # run explicit fault/restart transitions before any reply.
                    if index == len(steps) - 1 and session.awaiting_user and kind != "reply-if-asked":
                        reply = actor.reply(events)
                        if reply:
                            session.events.append({"kind": "actor-response", "prompt_ids": sorted(actor.used)})
                            turn(reply)
                else:
                    raise SessionError("unsupported-scenario-transition")
        except Exception as exc:
            execution_status = "blocked" if worker_started else "not-run"
            missing = exc.capability if isinstance(exc, PreflightUnavailable) else str(exc)
            if session:
                session.terminal_reason = missing

        snapshot = adapter.snapshot(session) if session else None
        profile = scenario["oracle_profile"]
        if session and snapshot and adapter.name == "codex" and (
            profile.get("handoff_policy") or "expected-refusal" in profile.get("completion_conditions", ())
        ):
            from .handoff_evidence import observe_handoff
            session.events.append(observe_handoff(session.state.get("transcript", []),
                plugin=session.plugin_root, work=session.work, snapshot=snapshot))
        result = evaluate_trial(
            scenario=scenario,
            events=session.events if session else [],
            artifacts=session.artifacts if session else [],
            snapshot=snapshot,
            terminal_reason=session.terminal_reason if session else "not-run",
            execution_status=execution_status,
            missing_capability=missing,
        )
        if session and evidence_root:
            trial_dir = evidence_root / f"{scenario['scenario_id']}-{repetition}"
            trial_dir.mkdir(parents=True, exist_ok=False)
            (trial_dir / "transcript.json").write_text(json.dumps(session.state.get("transcript", session.events), indent=2) + "\n", encoding="utf-8")
            if snapshot:
                shutil.copytree(snapshot, trial_dir / "output")
    except Exception as exc:
        result = {"status": "blocked", "issue_codes": ["evidence-evaluation-error:" + type(exc).__name__],
                  "dimensions": {}, "terminal_reason": "blocked"}
    finally:
        try:
            cleanup = adapter.cleanup(session) if session else {"cleaned": True}
        except Exception:
            cleanup = {"cleaned": False, "issue": "cleanup-failed"}
    if failure_codes:
        result["status"] = "failed"
        result["issue_codes"] = sorted(set(result.get("issue_codes", ())) | failure_codes)
    if cleanup.get("cleaned") is False:
        result["status"] = "failed"
        result["issue_codes"] = sorted(set(result.get("issue_codes", ()) ) | {"cleanup-failed"})
    result.update(
        {
            "scenario_id": scenario["scenario_id"],
            "session_id": session.session_id if session else None,
            "repetition": repetition,
            "event_count": len(session.events) if session else 0,
            "plugin_hash": session.loaded_plugin_hash if session else None,
            "workspace_isolated": True,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "tool_calls": session.tool_calls if session else 0,
            "actual_model": session.state.get("actual_model") if session else None,
            "final_words": len(session.state.get("final_text", "").split()) if session else 0,
        }
    )
    return result


def make_adapter(mode: str, *, model: str | None = None, budget: Mapping[str, Any] | None = None) -> SessionAdapter:
    if mode == "real-model":
        return CodexSessionAdapter(model=model, budget=budget)
    if mode != "deterministic":
        raise ContractError(f"unknown mode: {mode}")
    return FakeSessionAdapter()
