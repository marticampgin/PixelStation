"""Versioned critical gates, isolated fixtures, and explicitly requested native probes."""

import asyncio
import hashlib
import json
import random
import re
import tempfile
import time
from pathlib import Path

from .config import AppSettings
from .context import build_context, input_budget
from .database import Database
from .files import FileCreate, write_generated
from .memory import MemoryInput, create_memory, search_memory
from .observability import provider_observations
from .orchestration import Plan, route_prompt
from .poker import act, legal_actions, new_game, public_view
from .providers.reasoning import ReasoningFilter

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "evaluations_v1.json"
RUNNER_VERSION = 4


def fixtures() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def fixture_identity() -> dict:
    from .poker_evaluations import poker_fixture_identity

    poker_identity = {
        **poker_fixture_identity(),
        "decision_source_sha256": hashlib.sha256(
            (Path(__file__).parent / "poker.py").read_bytes()
        ).hexdigest(),
        "context_source_sha256": hashlib.sha256(
            (Path(__file__).parent / "poker_strategy.py").read_bytes()
        ).hexdigest(),
    }
    return {
        "version": fixtures()["version"],
        "sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "poker": poker_identity,
    }


def validate_plan_text(text: str, fixture: dict) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    steps = [re.fullmatch(r"\d+[.)]\s+(.+)", line) for line in lines]
    word_counts = [len(re.findall(r"\b[\w'-]+\b", match[1])) for match in steps if match]
    valid = (
        len(lines) == fixture["steps"]
        and all(steps)
        and all(words < fixture["max_words_exclusive"] for words in word_counts)
        and fixture["historical_filename"].lower() not in text.lower()
        and "<think" not in text.lower()
    )
    return {
        "passed": valid,
        "numbered_steps": len(word_counts),
        "word_counts": word_counts,
        "historical_artifact_mentioned": fixture["historical_filename"].lower() in text.lower(),
    }


def validate_file_answer(text: str, fixture: dict) -> dict:
    """A task-specific brief affirmative answer; a quoted/negated code is insufficient."""
    source = re.escape(fixture["filename"]) + r"(?:\s*,\s*L1)?"
    normalized = re.sub(r"\[\s*" + source + r"\s*\](?:\([^\)\n]{1,1000}\))?", "", text)
    normalized = re.sub(r"\(\s*" + source + r"\s*\)", "", normalized)
    normalized = re.sub(r"[`*_>]", "", normalized)
    normalized = " ".join(normalized.lower().split()).strip()
    codes = re.findall(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)*-\d+\b", normalized)
    expected = fixture["expected"].lower()
    negated = bool(
        re.search(
            r"\b(not|never|no|unknown|unavailable|incorrect|wrong|invalid|cannot)\b|\bisn['’]?t\b",
            normalized,
        )
    )
    lead = r"(?:(?:according to (?:the )?(?:supplied )?notes[, :]*)?(?:(?:the )?(?:verification )?(?:code|answer)(?: in (?:the )?(?:supplied )?notes)?(?: is| equals|\s*[:=])?|it is|it's)\s+)?"
    shape = bool(re.fullmatch(lead + r"['\"]?" + re.escape(expected) + r"['\"]?[.!]?", normalized))
    return {
        "passed": shape and not negated and codes == [expected],
        "verification_code_present": expected in codes,
        "brief_affirmative_answer": shape,
        "negated_or_uncertain": negated,
        "code_occurrences": len(codes),
        "distinct_codes": len(set(codes)),
    }


def known_native_identity(identity) -> bool:
    if not isinstance(identity, dict):
        return False
    digest = identity.get("digest")
    return (
        identity.get("provider") == "OllamaProvider"
        and bool(identity.get("model"))
        and isinstance(digest, str)
        and bool(re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", digest))
        and isinstance(identity.get("ollama"), str)
        and identity.get("ollama") not in {None, "", "unknown", "unavailable", "not_available"}
    )


def deterministic_cases() -> list[dict]:
    fixture = fixtures()
    cases = []

    def check(identifier, label, operation):
        started = time.monotonic()
        try:
            measured = operation()
            passed = measured.pop("passed")
            cases.append(
                {
                    "id": identifier,
                    "label": label,
                    "scope": "isolated_fixture",
                    "critical": True,
                    "status": "PASS" if passed else "FAIL",
                    "measurements": measured,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                }
            )
        except Exception as exc:
            cases.append(
                {
                    "id": identifier,
                    "label": label,
                    "scope": "isolated_fixture",
                    "critical": True,
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                }
            )

    def routing():
        mismatches = [
            {
                "case": index,
                "expected": row["expected"],
                "actual": route_prompt(row["input"]).intent,
            }
            for index, row in enumerate(fixture["routes"])
            if route_prompt(row["input"]).intent != row["expected"]
        ]
        return {"passed": not mismatches, "cases": len(fixture["routes"]), "mismatches": mismatches}

    def context():
        settings = AppSettings(context_tokens=2048)
        history = [
            {"role": "user", "content": "Create a CSV " * 1000},
            {"role": "assistant", "content": "Created fixture-first.csv"},
            {"role": "user", "content": fixture["plan"]["prompt"]},
        ]
        messages, allocation = build_context(settings, history, evidence="fixture evidence " * 1000)
        return {
            "passed": messages[-1]["content"] == fixture["plan"]["prompt"]
            and allocation["total"] <= input_budget(settings),
            "estimated_tokens": allocation["total"],
            "input_budget": input_budget(settings),
            "latest_request_preserved": messages[-1]["content"] == fixture["plan"]["prompt"],
        }

    def tool_budget():
        plan = Plan.model_validate(
            {
                "steps": [
                    {"id": f"step{index}", "tool": "web_search", "args": {"query": "fixture"}}
                    for index in range(4)
                ]
            }
        )
        try:
            plan.validate_dag({"web_search"}, 3)
            return {"passed": False, "rejected_over_budget": False}
        except ValueError:
            plan.validate_dag({"web_search"}, 4)
            return {"passed": True, "rejected_over_budget": True, "allowed_steps": 4}

    def csv_identity():
        with tempfile.TemporaryDirectory(prefix="pixel-station-eval-") as directory:
            db = Database(Path(directory))
            db.migrate()
            try:
                with db.session() as session:
                    first, second = [
                        write_generated(
                            session,
                            Path(directory),
                            FileCreate(
                                filename=name, format="csv", content=fixture["artifact"]["content"]
                            ),
                        )
                        for name in fixture["artifact"]["names"]
                    ]
                    passed = (
                        first.id != second.id
                        and first.filename != second.filename
                        and first.path == second.path
                        and Path(second.path).read_text().splitlines()
                        == fixture["artifact"]["content"].splitlines()
                    )
                    return {
                        "passed": passed,
                        "distinct_named_records": first.id != second.id,
                        "shared_bytes": first.path == second.path,
                    }
            finally:
                db.engine.dispose()

    def memory_retrieval():
        with tempfile.TemporaryDirectory(prefix="pixel-station-eval-") as directory:
            db = Database(Path(directory))
            db.migrate()
            try:
                with db.session() as session:
                    wanted = create_memory(session, MemoryInput(text=fixture["memory"]["text"]))
                    create_memory(session, MemoryInput(text="Project deadline is Friday"))
                    results = search_memory(session, fixture["memory"]["query"], 1)
                    return {
                        "passed": bool(results) and results[0]["id"] == wanted.id,
                        "retrieved": len(results),
                        "retrieval_mode": "FTS5_lexical_fixture",
                    }
            finally:
                db.engine.dispose()

    def poker():
        state = new_game(3, rng=random.Random(fixture["poker_seed"]))
        hidden = all(
            seat["hole"] == [] for seat in public_view(state, 0)["seats"] if seat["index"] != 0
        )
        steps = 0
        while not state["completed"] and steps < 100:
            choices = {row["action"] for row in legal_actions(state)}
            act(state, "check" if "check" in choices else "call" if "call" in choices else "fold")
            steps += 1
        return {
            "passed": hidden
            and state["completed"]
            and sum(seat["stack"] for seat in state["seats"]) == state["initial_chips"],
            "legal_actions": steps,
            "completed": state["completed"],
            "hidden_cards_protected": hidden,
            "chip_total": sum(seat["stack"] for seat in state["seats"]),
        }

    def reasoning():
        filter = ReasoningFilter()
        public = (
            "".join(
                filter.feed(character)
                for character in "<think>private fixture reasoning</think>Public answer."
            )
            + filter.finish()
        )
        return {"passed": public == "Public answer.", "public_only": public == "Public answer."}

    for identifier, label, operation in (
        ("routing", "Deterministic intent routing", routing),
        ("latest_context", "Latest request and context budget", context),
        ("tool_budget", "DAG step-limit rejection", tool_budget),
        ("csv_names", "Identical CSV bytes keep requested names", csv_identity),
        ("memory_retrieval", "FTS5 memory retrieval", memory_retrieval),
        ("poker_invariants", "Legal Poker hand and chip conservation", poker),
        ("reasoning_boundary", "Reasoning is excluded from public output", reasoning),
    ):
        check(identifier, label, operation)
    return cases


def synthesis_measurements(traces, observations, resets):
    return {
        "completion_path": "production_answer_stream",
        "validation_rejections": sum(
            trace.get("validation") == "unsupported_tool_protocol" for trace in traces
        ),
        "repair_attempts": sum(
            trace.get("validation") == "unsupported_tool_protocol" and trace.get("retry") == 1
            for trace in traces
        ),
        "provider_call_attempts": sum("answer_attempt" in trace for trace in traces),
        "terminal_metadata_samples": len(observations),
        "stream_resets": resets,
        "generation_tokens_per_attempt": 256,
    }


async def native_runtime_identity(app, settings) -> dict:
    from .providers import OllamaProvider

    model = settings.roles["primary_chat"]
    runtime = {
        "ollama": "not_available",
        "model": model,
        "digest": None,
        "provider": type(app.state.llm).__name__,
    }
    if isinstance(app.state.llm, OllamaProvider):
        import httpx

        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                version_response, tags_response = await asyncio.gather(
                    client.get(f"{settings.ollama_url}/api/version"),
                    client.get(f"{settings.ollama_url}/api/tags"),
                )
                version_response.raise_for_status()
                tags_response.raise_for_status()
                runtime["ollama"] = version_response.json().get("version", "not_available")
                tag: dict = next(
                    (
                        row
                        for row in tags_response.json().get("models", [])
                        if row.get("name") == model
                    ),
                    {},
                )
                runtime["digest"] = (
                    tag.get("digest") if isinstance(tag.get("digest"), str) else None
                )
        except (httpx.HTTPError, ValueError):
            pass
    return runtime


async def native_cases(app, settings=None) -> list[dict]:
    from .chat import AnswerReset, answer_prose, answer_stream, unsupported_answer_protocol

    settings = settings or app.state.settings()
    model = settings.roles["primary_chat"]
    fixture = fixtures()
    results = []
    if not model:
        return [
            {
                "id": identifier,
                "label": identifier.replace("_", " "),
                "scope": "native_model",
                "critical": True,
                "status": "SKIP",
                "reason": "No primary local model assigned",
            }
            for identifier in ("native_plan", "native_file_qa")
        ]
    native_deadline = time.monotonic() + 90
    runtime = await native_runtime_identity(app, settings)
    for identifier in ("native_plan", "native_file_qa"):
        started = time.monotonic()
        observations: list[dict] = []
        traces: list[dict] = []
        resets = 0
        output = ""
        scope = provider_observations.set(observations)

        try:
            if identifier == "native_plan":
                messages, _ = build_context(
                    settings,
                    [
                        {"role": "user", "content": "Create fixture-first.csv"},
                        {
                            "role": "assistant",
                            "content": "Created fixture-first.csv. The action is complete.",
                        },
                        {"role": "user", "content": fixture["plan"]["prompt"]},
                    ],
                )
            else:
                messages, _ = build_context(
                    settings,
                    [{"role": "user", "content": fixture["file_qa"]["prompt"]}],
                    files=[
                        {
                            "filename": fixture["file_qa"]["filename"],
                            "location": "L1",
                            "text": fixture["file_qa"]["text"],
                        }
                    ],
                )
            timeout = min(45, max(0.001, native_deadline - time.monotonic()))
            async with asyncio.timeout(timeout), app.state.model_queue.lock:
                async for part in answer_stream(
                    app,
                    model,
                    messages,
                    asyncio.Event(),
                    traces,
                    stream_kwargs={
                        "options": {
                            "num_ctx": settings.context_tokens,
                            "num_predict": 256,
                            "temperature": 0,
                        },
                    },
                    settings=settings,
                ):
                    if isinstance(part, AnswerReset):
                        output = ""
                        resets += 1
                        continue
                    output += part
                    if len(output) > 12000:
                        raise ValueError("Native probe exceeded output limit")
            measured = (
                validate_plan_text(output, fixture["plan"])
                if identifier == "native_plan"
                else validate_file_answer(output, fixture["file_qa"])
            )
            passed = measured.pop("passed")
            measured.update(synthesis_measurements(traces, observations, resets))
            measured["unsupported_tool_protocol"] = unsupported_answer_protocol(
                answer_prose(output), final=True
            )
            passed = passed and not measured["unsupported_tool_protocol"]
            results.append(
                {
                    "id": identifier,
                    "label": "Native latest-request plan"
                    if identifier == "native_plan"
                    else "Native supplied-file answer",
                    "scope": "native_model",
                    "critical": True,
                    "status": "PASS" if passed else "FAIL",
                    "model": model,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    "measurements": measured,
                    "provider_calls": observations,
                    "runtime": runtime,
                    "private_output": output,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "id": identifier,
                    "label": identifier.replace("_", " "),
                    "scope": "native_model",
                    "critical": True,
                    "status": "FAIL"
                    if synthesis_measurements(traces, observations, resets)["validation_rejections"]
                    == 2
                    else "ERROR",
                    "error_type": type(exc).__name__,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    "measurements": synthesis_measurements(traces, observations, resets),
                    "provider_calls": observations,
                    "runtime": runtime,
                    "private_output": output,
                }
            )
        finally:
            provider_observations.reset(scope)
    return results
