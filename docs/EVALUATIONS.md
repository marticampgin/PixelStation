# Local watchtower and evaluations

Open **Settings → Harness** for passive observations, manual critical gates, and a downloadable diagnostic bundle. Records stay in the local application database; there is no telemetry service, automatic code modification, or Codex automation.

## Why six steps and four memories?

These are conservative starting limits, **not empirically tuned optima**. Six research steps allow a few independent searches plus a few fetched pages while bounding latency and evidence volume. Four retrieved memories limit competition between memories and the cost of context. The appropriate values depend on the task, model, available context, and memory quality. The application does not have evidence that these values maximize answer quality.

`max_steps` defaults to 6 and accepts 1–12. It bounds the read-only research DAG, the direct research provider's combined search/fetch requests, and the number of explicitly selected web sources read in chat. DAG validation rejects excess steps before execution. It is **not a universal inference or tool quota**: routing, planning, answering, optional validation, Google operations, file creation, and image workflows have their own bounds. Direct integrations without an explicit request counter are labeled unmeasured rather than assigned a fabricated step count.

`retrieval_count` defaults to 4 and accepts 1–8. It limits selected memories; selected document excerpts have a separate six-chunk cap. Retrieval combines lexical relevance, optional local embeddings, importance/pins, and recency. Without a usable assigned embedding model, retrieval remains lexical. A fallback is recorded only when retrieval actually runs and the configured query embedding is unavailable. Four candidates may still be clipped by the memory allocation in the context builder.

The context builder estimates tokens from characters and reserves room for the latest request and response. Its recorded allocation and public-output token estimate are **estimates**, not tokenizer measurements. Ollama receives `num_ctx` and a bounded `num_predict`; native terminal token counters provide separate actual engine measurements. Private reasoning may consume generated tokens even though its text is discarded. Poker and manual native probes use their own smaller output ceilings.

## What passive observations measure

Each newly instrumented chat or Poker decision records its actual outcome, route, model, total elapsed time, known repair attempts/fallbacks, safe configuration, and available usage. Historical runs remain visible but are counted separately from runs with detailed measurements. Evaluation runs are kept separate from passive production observations.

| Field | Meaning and limitation |
| --- | --- |
| Workflow completion | Completed terminal runs / all terminal runs. Completion does not establish answer correctness. Errors and interruptions are shown separately. |
| Median / p95 latency | Nearest-rank percentiles of recorded elapsed times, with sample counts. Queue waits, tool work, cold model loads, and generation are included. Route/model groups avoid merging everything into one headline. |
| First public token | Elapsed time until the first visible nonempty chat token, including queue/tool work. Missing samples are unavailable. A discarded partial answer may have produced this first token before a repair. |
| Repair attempts | Actual bounded protocol/plan repair attempts and Poker validation retries. Repeated validation rejection after the final attempt is not another repair. |
| Fallbacks | Known lexical retrieval or legal Poker fallback uses. These are behavior counts, not an invented model-quality score. |
| Native prompt/output tokens | Ollama's `prompt_eval_count` and `eval_count`, only where returned. Missing counters are not zero usage. |
| Native throughput | `eval_count / (eval_duration / 1e9)` tokens/second, with available sample counts. Duration denominator is engine evaluation time, not whole-request latency. |
| Native durations | Original Ollama total/load/prompt-evaluation/evaluation durations in nanoseconds and native result/done status, preserved in diagnostic run metadata. |
| Retrieval/context/output | Selected memory/chunk counts, estimated context allocation, estimated public-output tokens, configured ceilings, and explicit research request counters when available. |

The default observation window is seven days, capped to the latest 1,000 runs (configurable to 1–90 days and 100–5,000 samples). The UI reports when matching records exceed the cap. Friction kinds, recent failures, and correlation IDs make individual cases discoverable. Small samples and mixed cold/warm loads should not be read as stable performance benchmarks.

Scheduled Harness reports compute local passive statistics only. The application's existing independent idle summary/embedding maintenance can still use its assigned local models. Active native evaluation probes are never scheduled.

## Repeatable critical gates

The versioned public synthetic fixture is `backend/pixel_station/fixtures/evaluations_v1.json`. Every active run stores its fixture version and SHA-256, safe configuration, package/runtime versions, case statuses, measurements, and timing. Gates exercise real core functions in temporary isolated databases and directories; they do not write production conversations, memories, files, or Poker sessions.

1. Deterministic routing, including plural Gmail search and unsupported deletion handling.
2. Latest-request preservation under bounded context allocation.
3. Research DAG step-limit rejection and valid-plan acceptance.
4. Identical CSV bytes retaining distinct requested filenames and immutable shared storage.
5. FTS5 retrieval of a known relevant memory over a distractor.
6. A seeded legal Poker hand, hidden opponent cards, and conserved chips.
7. Split-tag reasoning excluded from public output.

Statuses are **PASS** (explicit gate met), **FAIL** (gate measured an incorrect result), **ERROR** (gate could not execute), and **SKIP** (not requested or unavailable). Injected-failure unit tests prove that routing and native task defects become failures rather than successful reports. These checks establish their named invariants; they do not establish the quality of external Web, Google, or ComfyUI services, semantic recall, or arbitrary answers. A completed evaluation containing only deterministic passes and native skips is labeled with both counts so it cannot be mistaken for native model validation.

Selecting **Include native model probes** and clicking **Run evaluations** explicitly requests two read-only local inference probes: a three-step plan with each step under 15 words after historical CSV context, and supplied-file QA with a known verification code. These use the real context builder, provider, and production answer-stream validator, including its single bounded protocol repair and replacement of rejected partial output. Each inference attempt uses temperature zero and a 256-token generation ceiling; two probes permit at most four inference attempts total. The combined native phase ceiling is 90 seconds (45 seconds per probe including queue waits and repair). Raw validation rejection counts, repair attempts, requested provider stream attempts, and available terminal-metadata samples are reported even if a repaired answer passes. A second rejected answer fails the gate. Attempt counts do not invent missing native counters. They validate task-specific constraints and reject unsupported tool protocol. Model generation remains nondeterministic despite fixed input and temperature. The probe is rejected while local inference is busy, and settings changes are rejected while a native evaluation is running so its recorded configuration remains consistent.

Only one evaluation can run at a time. Persistent RUNNING/COMPLETE/ERROR/INTERRUPTED states survive navigation; interrupted jobs are identified on application restart. The UI disables duplicate starts and polls the recorded job. Active model output is stored locally in the evaluation report, excluding discarded reasoning; it is omitted from default UI responses and diagnostics.

The comparison baseline is the previous matching run among the latest **30 completed evaluations**, with the **same runner version, fixture, configuration, package versions, and native opt-in**. Passive reports do not consume this bounded lookback. Native comparisons additionally require a known identical model digest and Ollama runtime identity, captured from fresh local `/api/tags` and `/api/version` responses. Unknown identity disables baseline matching; a mutable model alias alone is insufficient. Runner version 3 strengthens the supplied-file gate to require one brief affirmative code answer and reject negation, uncertainty, or competing codes. Versions 1 and 2 are preserved but excluded from matched version-3 comparisons; fixtures and expected code are unchanged. A prior PASS becoming FAIL/ERROR is a critical regression. Per-case latency deltas are available, but a single comparison has no statistical significance. Changing a limit, model artifact, runtime, package version, or execution method creates a different comparison group rather than silently treating the old run as a matched baseline.

## Privacy and diagnostics

The default JSON download contains bounded recent run metadata, aggregates, safe configuration, package versions, cached installed model capability/context metadata, active gate results, and correlation IDs. It omits prompts, answers, source bodies, filenames, URLs, raw traces, and private regression inputs. Model names and IDs remain for reproduction and correlation. No hidden reasoning is collected by the measurement observer.

The explicit **Include bounded private local details** checkbox adds existing private evidence, regression inputs, and active probe output where stored. Strings, list lengths, nesting, and record counts are capped. Review such exports before sharing them. Raw local message/tool traces elsewhere in the app remain private application data; the export redaction does not erase those records.

## Using evidence to adjust defaults

Record the model, input fixtures, native version, configuration, and cold/warm state. Collect repeated native gate results and route-specific latency samples before drawing conclusions. Change one limit at a time, compare failures and constraint adherence as well as latency, and preserve both configurations' diagnostic bundles. Add a versioned task-specific gate when a reproducible failure matters; the seven core gates alone cannot identify an optimal retrieval count or research budget. There is no universal heuristic grade or automatic claim that six/four are best.

## API

- `GET /api/harness`: redacted reports, events, runs, measured aggregates, and default rationales.
- `POST /api/harness/run`: save a passive report.
- `POST /api/harness/evaluations` with `{ "native": false }`: start isolated gates; `{ "native": true }` also requests the bounded local probes. Returns 202 plus a persistent report ID.
- `GET /api/harness/evaluations/{id}`: recorded status, gates, and matching baseline comparison.
- `GET /api/harness/diagnostics`: redacted downloadable JSON; `?include_content=true` explicitly includes bounded private details.
- `GET /api/harness/regressions`: content-free regression references by default; `?include_content=true` accesses stored private local inputs.
