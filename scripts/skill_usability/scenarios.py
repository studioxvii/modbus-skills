"""Bounded user actor and trial driver."""

from __future__ import annotations

import time
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
            fact = str(rule.get("fact"))
            if fact not in self.facts:
                raise ContractError(f"actor cannot invent fact {fact}")
            self.used.add(prompt_id)
            return str(self.prompts[prompt_id])
        for step in self.scenario.get("transitions", ()):
            if step.get("kind") == "reply-if-asked":
                self.used.add(str(step["prompt_id"]))
                return str(self.prompts[step["prompt_id"]])
        raise ContractError("worker asked a question with no authorized reply")

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
) -> dict[str, Any]:
    directory = campaign_dir or CAMPAIGN_DIR
    started = time.monotonic()
    session: TrialSession | None = None
    execution_status = "completed"
    missing = None
    try:
        adapter.preflight()
        session = seed_workspace(scenario, campaign_dir=directory, parent=parent)
        adapter.start(session)
    except PreflightUnavailable as exc:
        return evaluate_trial(
            scenario=scenario,
            events=[],
            artifacts=[],
            snapshot=None,
            terminal_reason="not-run",
            execution_status="not-run",
            missing_capability=exc.capability,
        )
    except SessionError as exc:
        return evaluate_trial(
            scenario=scenario,
            events=[],
            artifacts=[],
            snapshot=None,
            terminal_reason="not-run",
            execution_status="not-run",
            missing_capability=str(exc),
        )

    actor = BoundedUserActor(scenario)
    pending_text: str | None = None
    try:
        for step in scenario.get("transitions", ()):
            kind = step["kind"]
            if kind == "supply-capture":
                source = session.fixtures / Path(str(scenario["fixtures"][0]["path"])).name
                if step.get("fixture_id"):
                    match = next(
                        item for item in scenario["fixtures"] if item["id"] == step["fixture_id"]
                    )
                    source = session.fixtures / Path(match["path"]).name
                target = session.work / "capture.json"
                target.write_bytes(source.read_bytes())
                session.events.append({"kind": "external-gate", "fixture": source.name})
                continue
            if kind == "tamper":
                # The scripted worker applies the tamper on the next turn.
                pending_text = actor.prompt("resume") if "resume" in actor.prompts else pending_text
                continue
            if kind == "interrupt":
                if session.interrupted:
                    session = interrupt_and_continue(adapter, session)
                continue
            if kind in {"prompt", "reply-if-asked"}:
                if kind == "prompt":
                    pending_text = actor.prompt(str(step["prompt_id"]))
                elif session.awaiting_user:
                    pending_text = actor.reply(session.events)
                else:
                    continue
                events = adapter.turn(session, pending_text)
                pending_text = None
                if session.interrupted:
                    session = interrupt_and_continue(adapter, session)
                    continue
                if session.terminal:
                    break
                if session.awaiting_user and kind != "reply-if-asked":
                    reply = actor.reply(events)
                    if reply:
                        adapter.turn(session, reply)
                if time.monotonic() - started > int(budget.get("max_seconds", 120)):
                    session.terminal = True
                    session.terminal_reason = "budget-exceeded"
                    break
                if session.turn_count > int(budget.get("max_turns", 8)):
                    session.terminal = True
                    session.terminal_reason = "budget-exceeded"
                    break
        if session and not session.terminal and pending_text:
            adapter.turn(session, pending_text)
    except PreflightUnavailable as exc:
        execution_status = "blocked"
        missing = exc.capability
    except SessionError:
        execution_status = "blocked"
        missing = "session-error"

    snapshot = adapter.snapshot(session) if session else None
    result = evaluate_trial(
        scenario=scenario,
        events=session.events if session else [],
        artifacts=session.artifacts if session else [],
        snapshot=snapshot,
        terminal_reason=session.terminal_reason if session else "blocked",
        execution_status=execution_status,
        missing_capability=missing,
    )
    cleanup = adapter.cleanup(session) if session else {"cleaned": True}
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
        }
    )
    return result


def make_adapter(mode: str) -> SessionAdapter:
    if mode == "real-model":
        return CodexSessionAdapter()
    if mode != "deterministic":
        raise ContractError(f"unknown mode: {mode}")
    return FakeSessionAdapter()
