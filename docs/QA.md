# Interface, Poker and watchtower verification

Verified on Windows 11 with Chrome through its connected extension, Ollama 0.32.14 and the installed official `LiquidAI/lfm2.5-2.6b:latest` model. These are actual local UI/API checks, separate from isolated automated tests. Runtime conversations, table IDs, diagnostic downloads and temporary QA captures are not committed.

## Rendered interface

- Compared the rendered 1448 × 1086 desktop against the supplied concept: fox/pixel wordmark, navy/violet palette, left rail and right inspector, compact navigation spacing, centered SVG carets, lower forest/cabin scenery on both panels, and composer geometry.
- The reference's sample travel answer, decorative game placeholders and unavailable tool success indicators are not application data. The public README capture shows a real empty conversation with installed-model information and no private history.
- Checked 390 × 844 drawers and Poker seats without horizontal overflow. Drawer dismissal restores header focus; only one mobile drawer opens at a time.
- Chats, Games and inspector disclosures retain independent preferences. Closed contents are inert; focus exposes the chat trash button. The concrete delete dialog supports cancellation; the actual API rejected unconfirmed deletion and removed an explicitly confirmed disposable QA conversation.
- Native select options use readable foreground/background colors. Page navigation is immediate; expansion and panel resizing use transitions with reduced-motion support.
- A fresh final Chrome tab showed the correct page identity, meaningful content and no console warnings/errors.

## Real local Poker

A six-seat hand used the actual local model, distinct fox portraits and a two-second reading pace. The current actor and legal human controls remained visible. Five opponents resolved the human all-in sequentially; flop, turn, river and showdown appeared in separate saved steps. The public log retained timestamps, streets, paid chips, pot changes and the result. Fifteen events completed the hand with no model fallback; all 600 starting chips were conserved. A separate streamed API check observed queued, choosing, committed and done phases for one bot action without advancing the board early. Saved table state survived application restart.

### Strategy refinement check

A separate six-seat table started with 1,000 chips per seat. Two ordinary hands completed, including a full flop/turn/river/showdown with small calls and checks. Thirteen bot decisions contained no preflop all-in attempts or selected all-ins; all 6,000 chips were conserved. A free-fold rejection was repaired to a check, and an excessive river raise was repaired to a check; neither needed fallback. This small sample was mostly passive play and does **not** establish balanced betting frequencies or strong Poker skill.

Four real native strategy trials preserved their failed reports while instructions were refined. The last recorded trial passed two of six strategy scenarios and failed four: weak-hand re-raising, folding premium hands, and missing bounded river value remained detectable judgment problems. All selected actions were legal; a passing repaired result still exposes its original rejection. The subsequent free-check guard was verified in the ordinary table and automated tests. Further prompt tuning was stopped at the user's request. These synthetic gates remain useful failure detectors; they are not claimed as a completed strategy-quality benchmark.

## Measurable evaluation scope

The native gates exercise the production answer validator. An initial supplied-file probe exposed a guard bypass; its failed report remains in the local history. The corrected path records rejected protocol, repairs, resets and attempted inference even when the final answer passes. Runner versions keep changed execution/validation rules out of matched comparisons. The supplied-file validator rejects contradictory or competing verification codes.

The final runner records checkpoint digest, Ollama identity, package versions, fixture hash and safe configuration. Matched repeated runs test comparison plumbing; their latency differences are not a performance benchmark. Passive completion rates likewise do not grade answer correctness. The default diagnostic export was downloaded through Chrome and checked for omission of prompts, answers, fixture filenames and discarded reasoning. See [evaluation scope and limit rationale](EVALUATIONS.md).

## Real local image generation

The official ComfyUI 0.38.0 NVIDIA portable archive and all 14 selected SD-Turbo FP16 files were verified against publisher metadata. Model revision `b261bac6fd2cf515557d5d0707481eafa0485ec2` is installed under ignored `.tools`; weights and licenses are not bundled into Git. The actual runtime is Python 3.13.14/PyTorch 2.14.0 with CUDA 13 on the same RTX 3060 Laptop GPU.

The initial native request exposed a dynamic-loader CLIP matrix-shape failure. The installed upstream loader constructs an unused final SD2 CLIP layer absent from the model's 23-layer file; its dynamic initializer supplied the wrong matrix orientation. The official `--disable-dynamic-vram` option resolved the execution error using unchanged verified files. `start-comfyui.ps1` was actually used to restart the service with that option and loopback-only access. The first failed job remains recorded.

Actual Chrome/API checks imported the complete one-step 512 × 512 Euler/SDTurboScheduler workflow, generated fox/cabin PNGs in Image Studio, downloaded and reopened a PNG, and compared the browser download's hash with the saved output. A separate chat request displayed its real generated cabin image inline and added it to the same library. Saved workflows, image dimensions and seeds survived application restart. A valid large seed was submitted through the Studio form after aligning its bounds with the backend.

Fresh residency observations showed the real LFM model unloaded before image submission and an empty Ollama resident-model list afterward. Chat reloaded successfully and answered the arithmetic follow-up. Queued cancellation submitted no remote prompt. Running cancellation through the UI removed the owned prompt, left ComfyUI's queue empty, saved no new image, and preserved earlier previews. That cancellation's reported Torch allocation fell from 1,912,602,624 to 33,554,432 bytes. A successful job's unchanged small Torch pool remains an observation, not proof of model residency or a release failure; acknowledgements and measurements are recorded separately.

During native QA, a separate pytest import exposed a recovery-isolation bug that could mark a live job interrupted. Test collection now redirects the global application to an owned temporary directory before importing it. The regression and complete isolated suite pass. Runtime failure records and temporary captures remain outside Git.

Web research and live Gmail/Calendar operations still require their separate setup and remain unverified end to end. WSL and Docker are absent; administrator WSL enablement and restart precede SearXNG. Google still needs owner-created Desktop OAuth credentials and personal consent. Bundled interface art was created with Codex's Image Gen tool; the native checks above independently verify the application's ComfyUI adapter.
