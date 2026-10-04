# Local watchtower and evaluations

Open **Settings → Harness** for passive observations, manual critical gates, and a downloadable diagnostic bundle. Records stay in the local application database; there is no telemetry service, automatic code modification, or Codex automation.

## Why six steps and four memories?

These are conservative starting limits, **not empirically tuned optima**. Six research steps allow a few independent searches plus a few fetched pages while bounding latency and evidence volume. Four retrieved memories limit competition between memories and the cost of context. The appropriate values depend on the task, model, available context, and memory quality. The application does not have evidence that these values maximize answer quality.

`max_steps` defaults to 6 and accepts 1–12. It bounds the read-only research DAG, the direct research provider's combined search/fetch requests, selected web sources read in chat, and adaptive-task tool calls. DAG validation rejects excess steps before execution. It is **not a universal inference or tool quota**: routing, planning, answering, optional validation, direct Google operations, file creation, and image workflows have their own bounds. Direct integrations without an explicit request counter are labeled unmeasured rather than assigned a fabricated step count.

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

Poker strategy counts cover newly instrumented **production decisions with a selected action**, not synthetic evaluation scenarios. Final action counts include known native and fallback decisions, with those sources reported separately. Selected preflop all-ins count decisions; raw preflop all-in attempts count parsed model proposals, including rejected proposals and a possible repair. A rejected all-in followed by a selected fold therefore increases the attempt count without increasing selected all-ins. Rejection counts remain visible. Uninstrumented historical actions are unknown; no measured decisions means unavailable evidence, not zero-percent quality. These frequencies describe observed behavior, not skill or an optimal action distribution.

Scheduled Harness reports compute local passive statistics only. The application's existing independent idle summary/embedding maintenance can still use its assigned local models. Active native evaluation probes are never scheduled.

Passive reports also group repeated failures by their actual recorded route, model, status and allowlisted structured error/tool/stage/validation evidence. Counts and linked run metadata support controlled reproductions and checkable regression candidates. Free-form private error text is never parsed to infer a root cause; old errors without structured signals stay unclassified. Default downloads omit raw examples, while explicit private-content exports retain bounded local evidence for review before sharing. Adaptive retries count only actual bounded repair calls, and repeated actions stop without another repair.

## Poker judgment and its limits

`poker.py` gives the chooser only allowlisted public table facts, recent public actions, and the acting player's own two hole cards. Opponent pockets, the deck, and future cards are absent. `poker_strategy.py` derives hand/board strength, position, active opponents, stack depth, call cost, pot odds, and bounded raise guidance. Balanced, value-focused, position-aware, selective-pressure, and patient styles add small nudges; evidence and risk limits take priority.

The estimator samples **128** deterministic seeded combinations of uniform random unseen opponent cards and remaining board runouts, splitting ties among active opponents. It reports estimated showdown pot share and sampling error. This is not the actual win probability or solved betting EV: it ignores action-conditioned ranges, fold equity, equity realization, rake, and side-pot eligibility, and sampling noise affects close decisions. Starting-hand tiers and commitment limits are conservative heuristics rather than solved ranges.

A production decision has a whole-decision budget of **up to 15 seconds**, including evidence calculation, model queue waits, generation, and at most **one repair**. Each structured inference attempt has a **180-token** ceiling and returns an action, optional total-round raise amount, and optional coded decision basis. Legality and a separate risk policy validate it. Rejected or unavailable inference leads to a disclosed legal fallback using available evidence; context timeout permits a check/fold fallback without inventing equity. There is no unbounded retry loop. Native strategy gates may still FAIL or ERROR; inspect the recorded report before treating model judgment as validated.

## Repeatable critical gates

The public fixtures are `backend/pixel_station/fixtures/evaluations_v1.json` and `backend/pixel_station/fixtures/poker_evaluations_v1.json`. Every run includes **19 deterministic gates**: the thirteen core gates below and six Poker context/risk scenarios. Reports store fixture versions and SHA-256 identities, runner/policy versions, safe configuration, package/runtime versions, statuses, measurements, and timing. Gates use isolated synthetic inputs and temporary databases/directories; they do not write production conversations, memories, files, or Poker sessions.

1. Deterministic routing, including plural Gmail search and unsupported deletion handling.
2. Latest-request preservation under bounded context allocation.
3. Research DAG step-limit rejection and valid-plan acceptance.
4. Identical CSV bytes retaining distinct requested filenames and immutable shared storage.
5. FTS5 retrieval of a known relevant memory over a distractor.
6. A seeded legal Poker hand, hidden opponent cards, and conserved chips.
7. Split-tag reasoning excluded from public output.
8. Production Gmail MIME encoding of Unicode body/subject, selected-thread reply headers, attachment bytes/name/hash and header-injection rejection.
9. Markdown citation URLs restricted to actual retrieved evidence, including query-string identity. This does not measure whether a citation supports each factual claim or is fresh.
10. Research fetch URLs derived from actual search observations and empty-search dependent fetches skipped. This tests the existing research controller, not arbitrary native adaptive judgment.
11. Exact numeric date quotations preserve complete ranges and surrounding qualifiers in a date-only request, including signature evidence beyond ordinary top-ranked retrieval.
12. Adaptive repeated-action recovery preserves gathered evidence, discloses the stop and records an interrupted outcome.
13. Calendar structured date-window and event-payload validation: explicit offsets, exclusive all-day ends, valid ranges and bounded windows. This does not test arbitrary natural-language date interpretation or live account authorization.

The six Poker scenarios are 72 offsuit facing a deep-stack open, J2 offsuit facing a deep-stack re-raise, deep-stack pocket aces in an unopened pot, short-stack pocket kings facing an open, a river nut straight, and river unpaired 72 facing a meaningful bet. Their deterministic gates check expected evidence, bounded 128-sample estimates, hidden-card isolation, unchanged inputs, chip conservation, admissible legal actions, and rejection of a legal but fixture-inappropriate action. Premium/value cases prevent an always-fold policy from passing. The fixed criteria permit multiple reasonable actions where appropriate; they do not define optimal EV.

Statuses are **PASS** (explicit gate met), **FAIL** (gate measured an incorrect result), **ERROR** (gate could not execute), and **SKIP** (not requested or unavailable). Injected-failure unit tests prove that routing and native task defects become failures rather than successful reports. These checks establish their named invariants; they do not establish the quality of external Web, Google, or ComfyUI services, semantic recall, or arbitrary answers. A completed evaluation containing only deterministic passes and native skips is labeled with both counts so it cannot be mistaken for native model validation.

The two native scopes are independently opted in and default off:

- **Native chat probes** run a three-step plan with each step under 15 words after historical CSV context, and supplied-file QA with a known verification code. They use the real context builder, provider, and production answer-stream validator, including one bounded protocol repair and replacement of rejected partial output. Each attempt uses temperature zero and a 256-token ceiling. The scope permits at most four attempts within 90 seconds, with 45 seconds per probe including queue waits and repair. The file gate requires one brief affirmative code answer and rejects negation, uncertainty, or competing codes. A second rejected answer fails the gate.
- **Poker strategy probes** run the same six synthetic scenarios through the production `choose_bot_action` path, with up to 15 seconds per decision and 90 seconds for the scope. PASS requires a legal, fixture-acceptable model decision, unchanged input, and **no fallback**. A legal fallback cannot pass native judgment validation. A repaired native decision can pass, while its parsed raw action labels, coded rejections, validation/repair counts, final action, numeric evidence, and available native counters remain inspectable. Free-form model reasoning is not retained.

Selecting both permits up to **180 seconds of local inference across the sequential scopes**; deterministic work and report bookkeeping are additional. Unselected native cases are SKIP. Attempt counts do not invent missing native counters, and fixed inputs do not make model generation deterministic. Native starts are rejected while local inference is busy; settings changes are rejected while a native evaluation runs to preserve its configuration.

Only one evaluation can run at a time. Persistent RUNNING/COMPLETE/ERROR/INTERRUPTED states survive navigation; interrupted jobs are identified on restart. The UI disables duplicate starts and polls the recorded job. Chat probe output is stored locally, excluding discarded reasoning, and omitted from default UI responses and diagnostics. Poker stores bounded coded decisions and numeric evidence instead of a reasoning narrative.

The comparison baseline is the previous matching run among the latest **30 completed evaluations**, with the **same runner version, fixture identity, configuration, package versions, and both native opt-in flags**. Passive reports do not consume this lookback. **Runner version 6** includes both fixture identities, the Poker policy version and source hashes for the tested Poker, answer, adaptive and Calendar paths. Policy edits therefore create a new comparison group even if fixtures are unchanged. Older reports remain preserved but cannot match runner 6. Native comparisons additionally require a known identical model digest and Ollama runtime identity from fresh local `/api/tags` and `/api/version` responses; unknown identity disables matching, and a mutable alias alone is insufficient. A prior PASS becoming FAIL/ERROR is a critical regression. Latency deltas from one comparison have no statistical significance. A model artifact, runtime, package, configuration, or policy change must not be treated as a matched baseline.

## Privacy and diagnostics

The default JSON download contains bounded recent run metadata, aggregates, safe configuration, package versions, cached installed model capability/context metadata, active gate results, and correlation IDs. It omits prompts, answers, source bodies, filenames, URLs, raw traces, and private regression inputs. Model names and IDs remain for reproduction and correlation. No hidden reasoning is collected by the measurement observer.

The explicit **Include bounded private local details** checkbox adds existing private evidence, regression inputs, and active probe output where stored. Strings, list lengths, nesting, and record counts are capped. Review such exports before sharing them. Raw local message/tool traces elsewhere in the app remain private application data; the export redaction does not erase those records.

## Using evidence to adjust defaults

Record the model, input fixtures, policy/source identity, native version, configuration, and cold/warm state. Collect repeated native gate results and route-specific latency samples before drawing conclusions. Change one limit at a time, compare failures and constraint adherence as well as latency, and preserve both configurations' diagnostic bundles. Add a versioned task-specific gate when a reproducible failure matters; the 19 deterministic gates alone cannot establish model judgment, optimal Poker strategy, or an optimal retrieval/research budget. There is no universal heuristic grade or automatic claim that six/four are best.

## API

- `GET /api/harness`: redacted reports, events, runs, measured aggregates, and default rationales.
- `POST /api/harness/run`: save a passive report.
- `POST /api/harness/evaluations` with `{ "native": false, "poker_native": false }`: start the 16 deterministic gates. Either flag independently requests its native chat or Poker scope; both true request both scopes. Returns 202 plus a persistent report ID.
- `GET /api/harness/evaluations/{id}`: recorded status, gates, and matching baseline comparison.
- `GET /api/harness/diagnostics`: redacted downloadable JSON; `?include_content=true` explicitly includes bounded private details.
- `GET /api/harness/regressions`: content-free regression references by default; `?include_content=true` accesses stored private local inputs.
