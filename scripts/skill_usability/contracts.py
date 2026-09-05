"""Versioned contracts for the representative skill-usability campaign."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_DIR = ROOT / "tests" / "skill_usability"
CAMPAIGN_PATH = CAMPAIGN_DIR / "campaign.json"
SCENARIO_DIR = CAMPAIGN_DIR / "scenarios"
CATALOG_SKILLS = ROOT / "catalog" / "skills.json"
CATALOG_WORKFLOWS = ROOT / "catalog" / "workflows.json"
USER_PATHS = ROOT / "plugins" / "modbus-skills" / "references" / "user-paths.md"

CAMPAIGN_SCHEMA = "skill-usability-campaign/v1"
SCENARIO_SCHEMA = "skill-usability-scenario/v1"
REPORT_SCHEMA = "skill-usability-report/v1"
REPRESENTATIVE_COUNT = 8
PUBLIC_STATUSES = frozenset({"passed", "failed", "blocked", "not-run", "inconclusive"})
EVIDENCE_CLASSES = frozenset({"deterministic", "simulated-user"})
INVOCATIONS = frozenset({"explicit", "router"})
DIMENSIONS = (
    "routing",
    "outcome_completion",
    "artifact_usefulness",
    "question_burden",
    "grouped_decisions",
    "correction_handling",
    "resume_behavior",
    "unsafe_refusal",
)
PROHIBITED_OPS = frozenset(
    {
        "write",
        "broadcast",
        "discovery",
        "unbounded-poll",
        "credential-access",
        "live-device",
        "auto-select-byte-order",
    }
)
WRITE_FUNCTIONS = frozenset({5, 6, 15, 16, 21, 22, 23})
UNSAFE_FIXTURE_MARKERS = (
    "password",
    "api_key",
    "secret",
    "credential",
    "token",
)
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_WINDOWS_ABS = re.compile(r"^[A-Za-z]:[\\/]")
_LIVE_ENDPOINT = re.compile(r"(?:https?://|tcp://|[0-9]{1,3}(?:\.[0-9]{1,3}){3})")


class ContractError(ValueError):
    """Campaign or scenario data cannot reach a session adapter."""


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{label} must be an object")
    return dict(value)


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    return value.strip()


def catalog_skill_ids() -> set[str]:
    payload = _json(CATALOG_SKILLS)
    skills = payload.get("skills", ())
    if not isinstance(skills, Sequence):
        raise ContractError("skill catalog is malformed")
    return {str(item["id"]) for item in skills if isinstance(item, Mapping) and item.get("id")}


def catalog_workflow_ids() -> set[str]:
    payload = _json(CATALOG_WORKFLOWS)
    workflows = payload.get("workflows", ())
    if not isinstance(workflows, Sequence):
        raise ContractError("workflow catalog is malformed")
    return {str(item["id"]) for item in workflows if isinstance(item, Mapping) and item.get("id")}


def _reject_unsafe_text(text: str, label: str) -> None:
    lowered = text.lower()
    if any(marker in lowered for marker in UNSAFE_FIXTURE_MARKERS):
        raise ContractError(f"{label} contains a credential-like marker")
    if _LIVE_ENDPOINT.search(text) and "127.0.0.1" not in text and "localhost" not in lowered:
        raise ContractError(f"{label} contains a live endpoint")
    if text.startswith("/") or _WINDOWS_ABS.match(text) or text.startswith("\\\\"):
        raise ContractError(f"{label} must be a relative path")
    if ".." in Path(text).parts:
        raise ContractError(f"{label} must not traverse directories")


def _validate_fixture(entry: Mapping[str, Any], *, campaign_dir: Path) -> None:
    identifier = _string(entry.get("id"), "fixture id")
    if not _ID.match(identifier):
        raise ContractError(f"fixture id is invalid: {identifier}")
    relative = _string(entry.get("path"), f"fixture {identifier} path")
    _reject_unsafe_text(relative, f"fixture {identifier} path")
    path = (campaign_dir / relative).resolve()
    try:
        path.relative_to(campaign_dir.resolve())
    except ValueError as exc:
        raise ContractError(f"fixture {identifier} escapes the campaign directory") from exc
    if not path.is_file():
        raise ContractError(f"fixture {identifier} does not exist")
    if path.is_symlink():
        raise ContractError(f"fixture {identifier} may not be a symlink")


def _validate_oracle(profile: Mapping[str, Any]) -> None:
    _string(profile.get("id"), "oracle profile id")
    terminal = _string(profile.get("expected_terminal"), "expected_terminal")
    if terminal not in PUBLIC_STATUSES:
        raise ContractError(f"unknown expected_terminal: {terminal}")
    _string(profile.get("expected_route"), "expected_route")
    recommended = profile.get("expected_recommended_skill")
    if recommended is not None:
        _string(recommended, "expected_recommended_skill")
    artifacts = profile.get("required_artifacts", ())
    schemas = profile.get("artifact_schemas", ())
    holds = profile.get("acceptable_holds", ())
    prohibited = profile.get("prohibited_operations", ())
    conditions = profile.get("completion_conditions", ())
    for label, values in (
        ("required_artifacts", artifacts),
        ("artifact_schemas", schemas),
        ("acceptable_holds", holds),
        ("prohibited_operations", prohibited),
        ("completion_conditions", conditions),
    ):
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            raise ContractError(f"{label} must be an array")
        for item in values:
            _string(item, label)
    if terminal in {"passed"} and not conditions:
        raise ContractError("oracle profile is missing completion conditions")
    if terminal in {"passed"} and any(
        code in {"unsafe-request-refused", "byte-order-human-confirmation-required", "stale-or-tampered-case"}
        for code in holds
    ) and not holds:
        raise ContractError("expected hold/refusal is missing terminal evidence")
    missing_ops = set(str(item) for item in prohibited) - PROHIBITED_OPS
    if missing_ops:
        raise ContractError("unknown prohibited operation: " + ", ".join(sorted(missing_ops)))
    dimensions = _mapping(profile.get("dimensions"), "oracle dimensions")
    if set(dimensions) != set(DIMENSIONS):
        raise ContractError("oracle dimensions must declare every v1 dimension")
    for name, enabled in dimensions.items():
        if enabled not in {True, False}:
            raise ContractError(f"dimension {name} must be a boolean")
    if terminal in {"passed"} and any(code in str(holds) for code in ("hold", "refus")):
        if not holds:
            raise ContractError("expected hold/refusal lacks explicit terminal evidence")


def _validate_safety(envelope: Mapping[str, Any]) -> None:
    codes = envelope.get("allowed_function_codes")
    if not isinstance(codes, Sequence) or set(codes) - {1, 2, 3, 4}:
        raise ContractError("safety envelope may allow only FC01-FC04")
    if set(codes) & WRITE_FUNCTIONS:
        raise ContractError("safety envelope contains a write function")
    for flag in (
        "writes",
        "broadcasts",
        "discovery_scans",
        "unbounded_polling",
        "credentials",
        "live_endpoints",
    ):
        if envelope.get(flag) is not False:
            raise ContractError(f"safety envelope must forbid {flag}")


def _validate_budget(budget: Mapping[str, Any], *, label: str) -> None:
    for key in ("max_turns", "max_questions", "max_seconds", "max_tool_calls", "max_output_bytes"):
        if key not in budget and label == "campaign budget":
            raise ContractError(f"{label} missing {key}")
        if key in budget:
            value = budget[key]
            if not isinstance(value, int) or value < 0 or value > 10_000_000:
                raise ContractError(f"{label} {key} is unbounded or invalid")
    if int(budget.get("max_turns", 1)) < 1 or int(budget.get("max_turns", 1)) > 32:
        raise ContractError(f"{label} max_turns is unbounded")


def validate_scenario(
    scenario: Mapping[str, Any],
    *,
    skills: set[str],
    workflows: set[str],
    personas: set[str],
    campaign_dir: Path,
) -> dict[str, Any]:
    value = _mapping(scenario, "scenario")
    if value.get("schema_version") != SCENARIO_SCHEMA:
        raise ContractError("unsupported scenario schema")
    scenario_id = _string(value.get("scenario_id"), "scenario_id")
    if not _ID.match(scenario_id):
        raise ContractError(f"scenario_id is invalid: {scenario_id}")
    skill = _string(value.get("skill"), "skill")
    if skill not in skills:
        raise ContractError(f"unknown skill: {skill}")
    workflow = value.get("workflow")
    if workflow not in (None, ""):
        workflow = _string(workflow, "workflow")
        if workflow not in workflows:
            raise ContractError(f"unknown workflow: {workflow}")
    persona = _string(value.get("persona"), "persona")
    if persona not in personas:
        raise ContractError(f"unknown persona: {persona}")
    _string(value.get("goal"), "goal")
    entry = _mapping(value.get("entry_policy"), "entry_policy")
    invocation = _string(entry.get("invocation"), "entry_policy.invocation")
    if invocation not in INVOCATIONS:
        raise ContractError("implicit specialist discovery is not a valid entry policy")
    entry_skill = _string(entry.get("skill"), "entry_policy.skill")
    if invocation == "router" and entry_skill != "modbus-help":
        raise ContractError("router entry policy must use modbus-help")
    if invocation == "explicit" and entry_skill != skill:
        raise ContractError("explicit entry policy must name the scenario skill")
    if invocation == "explicit" and skill == "modbus-help":
        raise ContractError("modbus-help must use router entry policy")
    fixtures = value.get("fixtures", ())
    if not isinstance(fixtures, Sequence) or isinstance(fixtures, (str, bytes, bytearray)):
        raise ContractError("fixtures must be an array")
    seen_fixtures: set[str] = set()
    for item in fixtures:
        fixture = _mapping(item, "fixture")
        identifier = _string(fixture.get("id"), "fixture id")
        if identifier in seen_fixtures:
            raise ContractError(f"duplicate fixture id: {identifier}")
        seen_fixtures.add(identifier)
        _validate_fixture(fixture, campaign_dir=campaign_dir)
    facts = _mapping(value.get("permitted_facts"), "permitted_facts")
    prompts = _mapping(value.get("prompts"), "prompts")
    if "opening" not in prompts:
        raise ContractError("scenario is missing an opening prompt")
    for name, text in prompts.items():
        _string(text, f"prompt {name}")
        if any(marker in text.lower() for marker in ("sk-", "ghp_", "password=")):
            raise ContractError(f"prompt {name} contains a credential")
    transitions = value.get("transitions")
    if not isinstance(transitions, Sequence) or not transitions:
        raise ContractError("scenario is missing transitions")
    seen_transitions: set[str] = set()
    for item in transitions:
        step = _mapping(item, "transition")
        step_id = _string(step.get("id"), "transition id")
        if step_id in seen_transitions:
            raise ContractError(f"duplicate transition id: {step_id}")
        seen_transitions.add(step_id)
        kind = _string(step.get("kind"), "transition kind")
        if kind not in {"prompt", "reply-if-asked", "interrupt", "tamper", "supply-capture"}:
            raise ContractError(f"unknown transition kind: {kind}")
        if kind in {"prompt", "reply-if-asked"}:
            prompt_id = _string(step.get("prompt_id"), "transition prompt_id")
            if prompt_id not in prompts:
                raise ContractError(f"transition references missing prompt {prompt_id}")
        if kind == "reply-if-asked":
            fact = _string(step.get("fact"), "response fact")
            if fact not in facts:
                raise ContractError(f"answer is not present in scenario state: {fact}")
        if kind == "supply-capture":
            fixture_id = _string(step.get("fixture_id"), "supply-capture fixture")
            if fixture_id not in seen_fixtures:
                raise ContractError(f"supply-capture references missing fixture {fixture_id}")
    for rule in value.get("response_rules", ()):
        item = _mapping(rule, "response rule")
        fact = _string(item.get("fact"), "response rule fact")
        if fact not in facts:
            raise ContractError(f"answer is not present in scenario state: {fact}")
        prompt_id = _string(item.get("prompt_id"), "response rule prompt")
        if prompt_id not in prompts:
            raise ContractError(f"response rule references missing prompt {prompt_id}")
    _validate_budget(_mapping(value.get("attention_budget"), "attention_budget"), label="attention budget")
    _validate_safety(_mapping(value.get("safety_envelope"), "safety_envelope"))
    oracle = _mapping(value.get("oracle_profile"), "oracle_profile")
    _validate_oracle(oracle)
    if not value.get("oracle_profile"):
        raise ContractError("runnable case is missing a deterministic oracle profile")
    return value


def validate_campaign(campaign: Mapping[str, Any], *, campaign_dir: Path | None = None) -> dict[str, Any]:
    directory = campaign_dir or CAMPAIGN_DIR
    value = _mapping(campaign, "campaign")
    if value.get("schema_version") != CAMPAIGN_SCHEMA:
        raise ContractError("unsupported campaign schema")
    _string(value.get("campaign_id"), "campaign_id")
    _string(value.get("worker_model"), "worker_model")
    if not USER_PATHS.is_file():
        raise ContractError("user-paths.md is missing")
    skills = catalog_skill_ids()
    workflows = catalog_workflow_ids()
    personas = {
        _string(item, "persona")
        for item in value.get("personas", ())
    }
    if not personas:
        raise ContractError("campaign personas are missing")
    evidence = value.get("evidence_classes", ())
    if not isinstance(evidence, Sequence) or set(evidence) - EVIDENCE_CLASSES:
        raise ContractError("campaign evidence classes are invalid")
    if "model-judged" in set(evidence) or "native" in set(evidence):
        raise ContractError("v1 campaigns cannot claim model-judged or native evidence")
    _validate_budget(_mapping(value.get("budget"), "budget"), label="campaign budget")
    names = value.get("scenarios")
    if not isinstance(names, Sequence) or isinstance(names, (str, bytes, bytearray)):
        raise ContractError("campaign scenarios must be an array")
    kind = value.get("campaign_kind", "representative")
    if kind == "representative":
        if len(names) != REPRESENTATIVE_COUNT:
            raise ContractError(f"representative campaign must contain exactly {REPRESENTATIVE_COUNT} scenarios")
    elif kind == "extended":
        if not 1 <= len(names) <= 100:
            raise ContractError("extended campaign must contain 1 through 100 scenarios")
    else:
        raise ContractError("unsupported campaign kind")
    loaded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in names:
        identifier = _string(name, "scenario name")
        path = directory / "scenarios" / f"{identifier}.json"
        if not path.is_file():
            raise ContractError(f"scenario file is missing: {identifier}")
        scenario = validate_scenario(
            _json(path),
            skills=skills,
            workflows=workflows,
            personas=personas,
            campaign_dir=directory,
        )
        scenario_id = scenario["scenario_id"]
        if scenario_id in seen:
            raise ContractError(f"duplicate scenario ID: {scenario_id}")
        if scenario_id != identifier:
            raise ContractError(f"scenario file {identifier} does not match scenario_id {scenario_id}")
        seen.add(scenario_id)
        loaded.append(scenario)
    value = dict(value)
    value["loaded_scenarios"] = loaded
    return value


def load_campaign(path: Path | None = None) -> dict[str, Any]:
    campaign_path = path or CAMPAIGN_PATH
    return validate_campaign(_json(campaign_path), campaign_dir=campaign_path.parent)


def validate_report_shape(report: Mapping[str, Any], schema: Mapping[str, Any] | None = None) -> None:
    payload = schema or _json(CAMPAIGN_DIR / "expected-report.schema.json")
    required = payload.get("required", ())
    for field in required:
        if field not in report:
            raise ContractError(f"report missing {field}")
    if report.get("schema_version") != REPORT_SCHEMA:
        raise ContractError("unsupported report schema")
    status = report.get("status")
    if status not in PUBLIC_STATUSES:
        raise ContractError(f"unknown report status: {status}")
    if report.get("evidence_class") not in EVIDENCE_CLASSES:
        raise ContractError("report claimed a disallowed evidence class")
