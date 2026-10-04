"""Versioned critical gates, isolated fixtures, and explicitly requested native probes."""

import asyncio
import base64
import hashlib
import json
import random
import re
import tempfile
import time
from email import policy
from email.parser import BytesParser
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
RUNNER_VERSION = 6


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
        "task_source_sha256": hashlib.sha256(
            (Path(__file__).parent / "adaptive.py").read_bytes()
            + (Path(__file__).parent / "chat.py").read_bytes()
        ).hexdigest(),
        "calendar_source_sha256": hashlib.sha256(
            (Path(__file__).parent / "google_tools.py").read_bytes()
            + (Path(__file__).parent / "providers" / "google.py").read_bytes()
        ).hexdigest(),
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

    def gmail_mime():
        from .providers.google import GoogleConnection
        from .providers.web import IntegrationError

        email = fixture["gmail_mime"]
        attachment = email["attachment_text"].encode("utf-8")
        payload = GoogleConnection.email_payload(
            email["recipient"],
            email["subject"],
            email["body"],
            thread_id=email["thread_id"],
            in_reply_to=email["message_id"],
            attachments=[
                {
                    "filename": email["attachment_name"],
                    "media_type": "text/plain",
                    "content": attachment,
                }
            ],
        )
        decoded = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(payload["raw"])
        )
        actual_attachments = list(decoded.iter_attachments())
        plain_body = decoded.get_body(preferencelist=("plain",))
        body_preserved = plain_body is not None and plain_body.get_content().rstrip(
            "\r\n"
        ) == email["body"].rstrip("\r\n")
        attachment_preserved = (
            len(actual_attachments) == 1
            and actual_attachments[0].get_payload(decode=True) == attachment
            and actual_attachments[0].get_filename() == email["attachment_name"]
        )
        headers_preserved = all(
            str(decoded[name]) == email[key]
            for name, key in [
                ("To", "recipient"),
                ("Subject", "subject"),
                ("In-Reply-To", "message_id"),
                ("References", "message_id"),
            ]
        )
        blocked = 0
        for key in ("to", "subject", "in_reply_to"):
            hostile = {
                "to": email["recipient"],
                "subject": email["subject"],
                "body": email["body"],
                "in_reply_to": email["message_id"],
            }
            hostile[key] += "\r\nBcc: attacker@example.invalid"
            try:
                GoogleConnection.email_payload(**hostile)
            except IntegrationError:
                blocked += 1
        return {
            "passed": body_preserved
            and attachment_preserved
            and headers_preserved
            and payload.get("threadId") == email["thread_id"]
            and blocked == 3,
            "unicode_paragraphs_preserved": body_preserved,
            "attachment_bytes_preserved": attachment_preserved,
            "reply_headers_preserved": headers_preserved,
            "header_injections_rejected": blocked,
            "external_requests": 0,
        }

    def web_citations():
        from .chat import validated_citations

        rows = fixture["web_citations"]
        expected = rows["allowed_url"]
        content = f"[Observed fact]({expected}) and [Unsupported claim]({rows['unobserved_url']})"
        public, removed = validated_citations(
            content, [{"url": expected, "text": "Observed fixture fact."}]
        )
        no_sources, empty_removed = validated_citations(content, [])
        passed = (
            f"]({expected})" in public
            and rows["unobserved_url"] not in public
            and removed == [rows["unobserved_url"]]
            and "](https://" not in no_sources
            and set(empty_removed) == {expected, rows["unobserved_url"]}
        )
        return {
            "passed": passed,
            "observed_link_retained": f"]({expected})" in public,
            "unobserved_links_removed": len(removed),
            "links_without_evidence_removed": len(empty_removed),
            "scope_note": "Retrieved-URL integrity for Markdown citations; does not grade factual support or freshness.",
        }

    def observed_workflow():
        from .chat import ResearchQueries, research_plan_from_queries
        from .orchestration import Tool, ToolRegistry
        from .research import run_research, validate_research_plan

        rows = fixture["observed_workflow"]
        plan = research_plan_from_queries(ResearchQueries(queries=rows["queries"]), 3)

        async def execute(empty_first):
            registry = ToolRegistry()
            fetched = []

            async def search(query):
                index = rows["queries"].index(query)
                return (
                    []
                    if empty_first and index == 0
                    else [
                        {
                            "url": rows["observed_urls"][index],
                            "title": "Fixture source",
                            "snippet": "Fixture search observation",
                        }
                    ]
                )

            async def fetch(url):
                fetched.append(url)
                return {"url": url, "title": "Fixture source", "text": "Observed fixture page"}

            for identifier, operation, argument in (
                ("web_search", search, "query"),
                ("web_fetch", fetch, "url"),
            ):
                registry.register(
                    Tool(
                        identifier,
                        identifier,
                        "Isolated evaluation fixture",
                        "web",
                        {
                            "type": "object",
                            "properties": {argument: {"type": "string"}},
                            "required": [argument],
                            "additionalProperties": False,
                        },
                        operation,
                    )
                )
            result = await run_research(plan, registry, 3)
            return result, fetched

        populated, fetched = asyncio.run(execute(False))
        empty_first, missing_fetches = asyncio.run(execute(True))
        injected = Plan.model_validate(
            {
                "steps": [
                    *([step.model_dump() for step in plan.steps[:2]]),
                    {
                        "id": "f1",
                        "tool": "web_fetch",
                        "args": {"url": rows["invented_url"]},
                        "depends_on": ["s1"],
                        "args_from": "s1",
                    },
                ]
            }
        )
        rejected = False
        try:
            validate_research_plan(injected, 3)
        except ValueError:
            rejected = True
        passed = (
            fetched == [rows["observed_urls"][0]]
            and populated["tool_steps"]["total"] == 3
            and not missing_fetches
            and empty_first["tool_steps"]["fetch"] == 0
            and bool(empty_first["errors"])
            and rejected
        )
        return {
            "passed": passed,
            "fetch_used_observed_url": fetched == [rows["observed_urls"][0]],
            "missing_observation_skipped_fetch": not missing_fetches,
            "model_invented_target_rejected": rejected,
            "executed_steps": populated["tool_steps"]["total"],
            "scope_note": "Production research DAG with synthetic provider observations; not a live provider/model quality probe.",
        }

    def document_dates():
        from .app import create_app
        from .chat import ConversationCreate, MessageInput, generate_response, new_conversation
        from .files import ingest, retrieve_files

        rows = fixture["document_dates"]
        calls = 0
        distractor = "Give the exact signature and rental dates. " * 70
        content = "\n\n".join(
            [
                "Rental date: " + rows["values"][1],
                *([distractor] * rows["distractor_paragraphs"]),
                "Recorded signing: " + rows["values"][0],
            ]
        )

        class NoInference:
            async def stream(self, *args, **kwargs):
                nonlocal calls
                calls += 1
                raise AssertionError("Explicit literal dates should not be rewritten by a model")
                yield ""

        async def execute(directory):
            app = create_app(Path(directory), llm=NoInference(), discover=False)
            try:
                with app.state.database.session() as session:
                    attachment = ingest(
                        session, Path(directory), rows["filename"], content.encode("utf-8")
                    )
                    conversation = new_conversation(ConversationCreate(), session)
                    attachment_id = attachment.id
                    ordinary = retrieve_files(session, [attachment_id], rows["prompt"])
                    late_field_omitted_by_ordinary_ranking = all(
                        rows["values"][0] not in source["text"] for source in ordinary
                    )
                events = [
                    json.loads(event)
                    async for event in generate_response(
                        app,
                        conversation["id"],
                        MessageInput(content=rows["prompt"], attachment_ids=[attachment_id]),
                    )
                ]
                return events[-1]["message"], late_field_omitted_by_ordinary_ranking
            finally:
                if app.state.background_tasks:
                    await asyncio.gather(*app.state.background_tasks)
                app.state.database.engine.dispose()

        with tempfile.TemporaryDirectory(prefix="pixel-station-dates-") as directory:
            message, ordinary_omits_late_field = asyncio.run(execute(directory))
        exact = all(value in message["content"] for value in rows["values"])
        cited = rows["filename"] in message["content"]
        return {
            "passed": exact
            and cited
            and ordinary_omits_late_field
            and message["status"] == "complete"
            and calls == 0,
            "range_and_qualifiers_preserved": exact,
            "source_citation_present": cited,
            "late_date_beyond_ordinary_retrieval_covered": ordinary_omits_late_field,
            "model_calls": calls,
            "scope_note": "Production chat and parsed source for explicit exact/verbatim numeric date questions; no semantic field attribution or general model-quality claim.",
        }

    def adaptive_stop():
        from .app import create_app
        from .chat import ConversationCreate, MessageInput, generate_response, new_conversation

        rows = fixture["adaptive_stop"]
        executions = []

        class RepeatChooser:
            def __init__(self):
                self.actions = [
                    {"tool": "web_search", "args": {"query": "fixture release"}},
                    {"tool": "web_fetch", "args": {"url": rows["url"]}},
                    {"tool": "web_fetch", "args": {"url": rows["url"]}},
                ]

            async def structured(self, model, messages, schema, **kwargs):
                return schema.model_validate(self.actions.pop(0))

            async def stream(self, model, messages, **kwargs):
                assert rows["fact"] in json.dumps(messages)
                yield rows["fact"]

        async def execute(directory):
            app = create_app(Path(directory), llm=RepeatChooser(), discover=False)
            settings = app.state.settings()
            settings.roles["primary_chat"] = "fixture:model"
            app.state.set_settings(settings)

            async def search(query):
                executions.append("search")
                return [{"url": rows["url"], "title": "Observed fixture"}]

            async def fetch(url):
                executions.append("fetch")
                return {"url": url, "text": rows["fact"]}

            app.state.tool_registry.tools["web_search"].execute = search
            app.state.tool_registry.tools["web_fetch"].execute = fetch
            try:
                with app.state.database.session() as session:
                    conversation = new_conversation(ConversationCreate(), session)
                events = [
                    json.loads(event)
                    async for event in generate_response(
                        app, conversation["id"], MessageInput(content=rows["prompt"])
                    )
                ]
                return events[-1]["message"]
            finally:
                if app.state.background_tasks:
                    await asyncio.gather(*app.state.background_tasks)
                app.state.database.engine.dispose()

        with tempfile.TemporaryDirectory(prefix="pixel-station-adaptive-") as directory:
            message = asyncio.run(execute(directory))
        retained = rows["fact"] in message["content"]
        signals = [
            trace
            for trace in message["traces"]
            if trace.get("error_code") == "adaptive_repeated_action"
        ]
        return {
            "passed": executions == ["search", "fetch"]
            and retained
            and message["status"] == "interrupted"
            and bool(signals),
            "duplicate_tool_not_executed": executions == ["search", "fetch"],
            "observed_evidence_retained": retained,
            "confirmed_success_not_reported": message["status"] == "interrupted",
            "structured_stop_signal_present": bool(signals),
            "scope_note": "Production task and answer paths with an intentionally repeating synthetic chooser; not a live-model quality score.",
        }

    def calendar_bounds():
        from pydantic import ValidationError

        from .google_tools import CalendarReadArguments
        from .providers.google import GoogleConnection
        from .providers.web import IntegrationError

        rows = fixture["calendar_bounds"]
        read = CalendarReadArguments(time_min=rows["time_min"], time_max=rows["time_max"])
        timed = {
            "summary": "Fixture appointment",
            "start": {"dateTime": rows["time_min"]},
            "end": {"dateTime": rows["time_max"]},
        }
        all_day = {
            "summary": "Fixture day",
            "start": {"date": rows["all_day_start"]},
            "end": {"date": rows["all_day_end"]},
        }
        valid = (
            GoogleConnection.validate_event(timed) == timed
            and GoogleConnection.validate_event(all_day) == all_day
        )
        rejected = 0
        for start, end in (
            (rows["time_max"], rows["time_min"]),
            ("2099-10-25T00:00:00", "2099-10-26T00:00:00"),
            ("2099-01-01T00:00:00Z", "2101-01-01T00:00:00Z"),
        ):
            try:
                CalendarReadArguments.model_validate({"time_min": start, "time_max": end})
            except ValidationError:
                rejected += 1
        for event in (
            {**timed, "end": timed["start"]},
            {**timed, "start": {"dateTime": "2099-10-25T00:00:00"}},
            {**timed, "end": all_day["end"]},
            {**all_day, "end": all_day["start"]},
        ):
            try:
                GoogleConnection.validate_event(event)
            except IntegrationError:
                rejected += 1
        offsets = (
            read.time_min.isoformat() == rows["time_min"]
            and read.time_max.isoformat() == rows["time_max"]
        )
        return {
            "passed": valid and offsets and rejected == 7,
            "explicit_offsets_preserved": offsets,
            "all_day_exclusive_end_preserved": valid,
            "invalid_ranges_rejected": rejected,
            "external_requests": 0,
            "scope_note": "Production Calendar schemas and payload validation with explicit timestamps; does not test natural-language interpretation, Google authorization or live writes.",
        }

    for identifier, label, operation in (
        ("routing", "Deterministic intent routing", routing),
        ("latest_context", "Latest request and context budget", context),
        ("tool_budget", "DAG step-limit rejection", tool_budget),
        ("csv_names", "Identical CSV bytes keep requested names", csv_identity),
        ("memory_retrieval", "FTS5 memory retrieval", memory_retrieval),
        ("poker_invariants", "Legal Poker hand and chip conservation", poker),
        ("reasoning_boundary", "Reasoning is excluded from public output", reasoning),
        ("gmail_mime", "Gmail Unicode, reply and attachment MIME integrity", gmail_mime),
        ("web_citations", "Markdown citations require retrieved URLs", web_citations),
        (
            "observed_workflow",
            "Research fetch targets follow actual observations",
            observed_workflow,
        ),
        (
            "document_dates",
            "Exact document dates preserve full ranges and qualifiers",
            document_dates,
        ),
        (
            "adaptive_stop",
            "Repeated tasks preserve evidence and report stopped work",
            adaptive_stop,
        ),
        ("calendar_bounds", "Calendar offsets, exclusive ends and invalid ranges", calendar_bounds),
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
