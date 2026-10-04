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

### Selectable DreamShaper 8 and SDXL profiles

Original DreamShaper 8 and SDXL Base 1.0 checkpoints were installed alongside SD-Turbo. Pinned revisions, complete SHA-256 hashes, exact filenames and companion-document verification are recorded in [the integration guide](INTEGRATIONS.md#comfyui-on-windows). Both profiles use core ComfyUI nodes, DPM++ 2M/Karras, 28 steps, batch size one and denoise 1.0. DreamShaper uses CFG 6.5 at 512 × 512; SDXL uses CFG 5.5 at 1024 × 1024 with tiled VAE decoding. The same low-VRAM launcher settings were retained.

| First recorded job | Result | ComfyUI execution time |
| --- | --- | --- |
| DreamShaper 8, 512 × 512 | Completed PNG, no execution error | 14.15 seconds |
| SDXL Base 1.0, 1024 × 1024 | Completed PNG, no execution error | 49.55 seconds |

The first SDXL interval from job creation to saved artifact was approximately 50.62 seconds. The first DreamShaper pixel-art sample placed the fox indoors and framed the cabin; the first SDXL sample followed the requested outdoor fox/cabin placement more closely.

The second pair used seed `4242` and the same photographic prompt: “A photograph of one red fox standing on moss at the edge of a pine forest, soft overcast daylight, natural fur, realistic proportions, wildlife photography.” Sampling settings and each profile's dimensions remained unchanged.

| Second photographic job | ComfyUI execution | Job creation → saved artifact | Monitoring |
| --- | --- | --- | --- |
| DreamShaper 8, 512 × 512 | 23.78 seconds | 24.66 seconds | 5 samples, five-second intervals |
| SDXL Base 1.0, 1024 × 1024 | 125.69 seconds | 127.63 seconds | 26 samples, five-second intervals, including active generation |

Visual inspection of both PNGs found detailed photographic fur in the DreamShaper image, with unusual hind legs and watermark-like text at the lower left. The SDXL sample showed a sharp, natural-looking fox. These observations cover individual prompt/seed cases at different resolutions; they do not establish a general quality ranking. Likewise, the four timings are individual jobs rather than a repeatable performance benchmark; queueing, checkpoint loading, paging and later prompts can change latency.

Monitoring missed the first SDXL job, so no sampled first-job RAM or VRAM peak is claimed. Its initial process-lifetime peak private allocation was approximately 17.9 GB. During the second SDXL job, the sampled maximum ComfyUI-reported Torch pool was **5,838,471,168 bytes**, sampled maximum process private allocation was **17,972,326,400 bytes**, and minimum sampled system available memory was **2,719,744 bytes**. Five-second sampling can miss short peaks. The Torch pool is not total device VRAM, and private allocation is virtual memory rather than physical RAM or a working-set measurement. These observations indicate heavy memory pressure and reliance on Windows paging on this 8 GB RAM/6 GB VRAM machine. Tiled VAE decoding does not remove checkpoint, text-encoder or denoising memory requirements.

All four new native Studio jobs completed with no recorded error, no remote-cleanup requirement and no handoff warning. ComfyUI reported a 33,554,432-byte Torch pool at termination; `/free` acknowledged the request without a further observed reduction because the reported allocation had already fallen before the request. The launcher uses `--cache-none` to avoid retaining intermediate node results between these jobs. The counters and acknowledgement do not claim that all device memory was free.

SDXL was selected as the quality default through Studio and retained after an app restart. A fifth native job came from a real Chrome chat request without structured dimensions: “Generate an image of a tiny orange fox outside a wooden cabin at dusk, blue pine forest, pixel art.” The persisted request and PNG were 1024 × 1024 using SDXL, with no error, cleanup requirement or handoff warning. ComfyUI execution took 122.15 seconds; creation to saved artifact was 124.05 seconds. The random-seed output placed an oversized fox on the cabin roof, a scale/placement defect despite successful execution. This is a routing/dimension/execution check, not a quality pass. Mocked regressions verify the 630-second default image orchestration budget and alignment with a custom provider deadline; this native job did not exercise a duration beyond the old 180-second limit.

After restart, Studio restored the prior DreamShaper job at 512 × 512 and seed 4242 despite the new global default. Explicitly selecting SDXL applied 1024 × 1024. The SDXL photographic library download matched the saved PNG's SHA-256. A fresh Chrome tab showed both distinct forest URLs, the source-specific LiquidAI labels and exact selected-model IDs, with no console warnings/errors or horizontal overflow at the native 1920-pixel viewport. The right forest drawer was also inspected at 390 × 844. Its public asset/tool/prompt provenance is recorded in [ASSETS](design/ASSETS.md); temporary QA screenshots and generated runtime images remain outside Git.

Final isolated verification: 363 backend tests and 64 frontend tests passed, with Ruff, all 29 backend mypy source files, frontend type/build/format checks and ComfyUI launcher syntax passing. SD-Turbo remains installed and all three profiles remain selectable.

### Native SearXNG setup and Web checks

Before the full Restart on 2026-10-04, Windows servicing had not installed the compute service despite an enabled Virtual Machine Platform and active hypervisor. After the owner's full Restart at 09:02 local time, WSL 3.0.1.0/kernel 6.18.40.1-1 reported a working version-2 backend, the compute service/binary existed and pending servicing cleared. No BIOS change or additional distribution was needed.

Official Docker Desktop 4.93.0 was installed per user with the WSL2 backend from its checksum/signature-verified installer. The owner completed Docker sign-in. Docker Engine 29.8.1 then ran the inspected official SearXNG 2026.10.4-44b98e610 image, digest `sha256:2ddcbc64e1b96cd4c73fce2e0ddd9351f0c405d3282bed7dcbc27a4905f0811e`, published only on `127.0.0.1:8888`. The local random secret, installer and verification records remain ignored. The Compose file now pins that digest and applies a 512 MiB container cap.

Real API searches returned official documentation. A provider research DAG executed two searches and two source fetches within its six-tool-call budget. It retrieved the actual API and search-settings pages with no errors. A local-network source URL was rejected with `unsafe_url`. Rendered desktop checks covered search/research results, source selection, rapid preview changes and Use in chat with a real source chip. A native official API-page fetch exposed SVG-title/navigation pollution; the corrected parser returned a clean document title and 2,203 characters beginning with the API article. Isolated preview tests cover delayed success/error races, retained fetch errors and successful empty searches.

Repeated restrictive searches later returned no sources while Brave and Google CSE reported rate limits and DuckDuckGo reported a CAPTCHA. This is an upstream limitation, not a successful research result. The provider now distinguishes reported engine failures from a genuine empty result and retains partial usable results. Enabling Mwmbl in the real container returned official repository/documentation sources for an ordinary SearXNG query; its smaller index does not guarantee restrictive-query coverage. CAPTCHAs were not bypassed.

An 18-sample observation across approximately 57 seconds measured the container at 114.8–117.5 MiB before the cap and system available memory as low as 279.4 MiB. After the cap, a separate 12-sample observation over 37 seconds measured 114.8 MiB/512 MiB, minimum available memory 351.7 MiB and maximum sampled WSL working set 249.6 MiB. These short samples can miss peaks and exclude parts of Docker/host overhead; they do not establish comfortable operation on 8 GB RAM. ComfyUI was stopped during Web QA.

The first native attached-source `/research` request was incorrectly dispatched as a normal supplied-source answer. Its fetch succeeded, but the model requested unsupported tools on both bounded answer attempts and the run ended in error after 130 seconds. Embedding hit its 45-second cold-load deadline; Ollama reported memory pressure and a GPU-discovery warning. This failed record is retained. Explicit research dispatch with attached sources is being corrected and model-planned research remains under native QA. Ollama had updated to 0.35.1 by these checks, so earlier 0.32.14 timings are not matched comparisons. This turn used the in-app browser because Chrome was unavailable to browser control. See [Windows setup guidance](INTEGRATIONS.md#searxng).

Google still needs owner-created Desktop OAuth credentials and personal consent. Bundled interface art was created with Codex's Image Gen tool; the native checks above independently verify the application's ComfyUI adapter.
