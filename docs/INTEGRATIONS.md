# Optional integrations

Pixel Station uses local Ollama for inference. Web search, image generation and Google access have separate providers. Unavailable providers return actionable setup messages. No integration uses a commercial LLM or paid search fallback. Configuring these services is independent of ordinary chat.

## SearXNG

The local search endpoint defaults to `http://127.0.0.1:8888`. This repository includes a single-service optional Compose configuration; JSON search is explicitly enabled. Search queries leave the machine through the upstream engines selected by SearXNG. Source fetches use public HTTP(S) webpages. [SearXNG search API](https://docs.searxng.org/dev/search_api.html).

On Windows, install Docker Desktop with a working Linux-container backend first. Enabling WSL/virtualization may require administrator approval and a restart. Check `wsl --version` and `wsl --status` before starting Docker. If feature installation remains pending after shutting down and turning the PC back on, save your work and choose **Start → Power → Restart**, allowing Windows updates to finish. Fast Startup can retain a prior kernel session and leave installation pending; an enabled feature alone does not prove its services are ready. [Microsoft's Fast Startup troubleshooting](https://learn.microsoft.com/en-us/troubleshoot/windows-client/setup-upgrade-and-drivers/updates-not-install-with-fast-startup).

Once Docker is running, execute from the repository root:

```powershell
$searchSecret = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
[IO.File]::WriteAllText((Join-Path (Get-Location) 'config/.env'), "SEARXNG_SECRET=$searchSecret`n")
docker compose --env-file config/.env -f config/docker-compose.optional.yml up -d
```

The ignored `config/.env` is local configuration. The Compose configuration publishes only on the host loopback address, so the service is not exposed to the LAN. Change Settings → Web → SearXNG endpoint if your service uses another address. **Test connection** checks the SearXNG configuration endpoint; it does not prove an upstream search succeeds. Use the Web workspace to perform an actual search, open a returned source and then request research before treating the integration as verified.

```powershell
docker compose --env-file config/.env -f config/docker-compose.optional.yml logs --tail 100
docker compose --env-file config/.env -f config/docker-compose.optional.yml down
```

For updates, review the upstream image changes and pull deliberately; this example uses the official `latest` image. Pin an inspected image digest if you need reproducible deployment. A Docker installation is unnecessary if a SearXNG endpoint is already running elsewhere. [Official container installation](https://docs.searxng.org/admin/installation-docker.html).

A 403 from search usually means JSON is absent from `search.formats`. Connection refused means SearXNG is not started or its endpoint is wrong. Search-engine CAPTCHAs or rate limits can produce no results even when the service is healthy.

Pixel Station limits searches to 20 results, research to four queries and six fetched pages, fetch downloads to 2 MB and total fetch time to 30 seconds. Redirects are revalidated. The resolved public address is pinned for the actual connection; private/reserved/local-network addresses, file URLs, credentials in URLs and nonstandard ports are rejected. HTML text is extracted locally; JavaScript-only pages return an explanation and can be opened in a browser. The current provider does not enable a Playwright fallback.

## ComfyUI on Windows

The endpoint defaults to `http://127.0.0.1:8188`. Use an official local ComfyUI distribution compatible with your GPU. The portable distribution includes its own Python and GPU runtime. Update your NVIDIA driver before enabling GPU inference. Extract the portable archive, launch its GPU batch file, and keep its process running while generating. CPU mode is available but can be slow. [Official Windows portable instructions](https://docs.comfy.org/installation/comfyui_portable_windows).

For a portable installation under `.tools/comfyui/ComfyUI_windows_portable`, run `./start-comfyui.ps1` in a separate terminal while Pixel Station is running. Pass `-PortableRoot 'C:\path\ComfyUI_windows_portable'` for another installation. This launcher binds only to `127.0.0.1:8188`, disables ComfyUI's cloud API nodes and automatic browser launch, and requests low-VRAM mode with `--disable-dynamic-vram`. The latter uses standard model loading to avoid the observed ComfyUI 0.38.0 dynamic-loader error with SD-Turbo's text encoder; model files remain unchanged. It does not install or update software or download weights. Close it with Ctrl+C when finished.

`config/comfyui-sd-turbo-workflow.example.json` provides a complete one-step, 512 × 512 **SD-Turbo** API graph using Euler, `SDTurboScheduler` and CFG 1 (unconditional guidance disabled). Its empty negative prompt is intentional. Install the official `stabilityai/sd-turbo` FP16 Diffusers files in `ComfyUI/models/diffusers/sd-turbo-fp16`; the installed ComfyUI loader must support their FP16 filenames. The example binds `seed` to `SamplerCustom.noise_seed`. In Image Studio, paste the wrapper's `workflow` object and enter its `bindings` explicitly. The example is not imported automatically. Consult the model's license and card before choosing another use or distributing weights. [SD-Turbo model card](https://huggingface.co/stabilityai/sd-turbo), [ComfyUI Turbo scheduler example](https://comfyanonymous.github.io/ComfyUI_examples/sdturbo/).

Choose a diffusion model independently of the chat model. Model weights are not bundled or downloaded automatically. The development installation retains SD-Turbo and adds selectable DreamShaper 8 and SDXL Base 1.0 workflows:

| Profile | Complete API example | Size / batch | Sampling |
| --- | --- | --- | --- |
| SD-Turbo | [SD-Turbo](../config/comfyui-sd-turbo-workflow.example.json) | 512 × 512 / 1 | One step, Euler, SDTurboScheduler, CFG 1 |
| DreamShaper 8 | [DreamShaper 8](../config/comfyui-dreamshaper8-workflow.example.json) | 512 × 512 / 1 | 28 steps, DPM++ 2M / Karras, CFG 6.5 |
| SDXL Base 1.0 | [SDXL Base](../config/comfyui-sdxl-base-workflow.example.json) | 1024 × 1024 / 1 | 28 steps, DPM++ 2M / Karras, CFG 5.5 |

DreamShaper and SDXL use the installed core `CheckpointLoaderSimple`, `CLIPTextEncode`, `EmptyLatentImage`, `KSampler` and `SaveImage` nodes, with denoise 1.0. Both examples include the fixed negative prompt `blurry, low quality`. DreamShaper uses ordinary `VAEDecode`; SDXL uses `VAEDecodeTiled` with tile size 512 and overlap 64. These are selectable starting presets, not empirically optimal settings. The SDXL graph uses the base checkpoint alone, without a refiner.

Place the following full checkpoint files in `ComfyUI/models/checkpoints`; their filenames must match the graph's `ckpt_name`. The development machine installed the original author-hosted files without conversion, verified file sizes and SHA-256 against pinned repository LFS metadata, and inspected the safetensors headers:

- **DreamShaper 8:** [Lykon/DreamShaper](https://huggingface.co/Lykon/DreamShaper/tree/228d79cb20811466f5c5710aa91f05dabd0b8a14), revision `228d79cb20811466f5c5710aa91f05dabd0b8a14`; `DreamShaper_8_pruned.safetensors`, 2,132,625,894 bytes; SHA-256 `879db523c30d3b9017143d56705015e15a2cb5628762c11d086fed9538abd7fd`. The pinned README was verified against its Git blob hash. This checkpoint repository labels its license `other` and points to the author's Civitai page; no standalone checkpoint license file was present in the verified companion documents. Review those author terms rather than assuming the license of another DreamShaper distribution applies.
- **SDXL Base 1.0:** [stabilityai/stable-diffusion-xl-base-1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/tree/462165984030d82259a11f4367a4eed129e94a7b), revision `462165984030d82259a11f4367a4eed129e94a7b`; `sd_xl_base_1.0.safetensors`, 6,938,078,334 bytes; SHA-256 `31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b`. The pinned README and [CreativeML Open RAIL++-M license](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/462165984030d82259a11f4367a4eed129e94a7b/LICENSE.md) were verified against their Git blob hashes.

Verification records and companion documents are saved locally under ignored `.tools/downloads/model-provenance`; checkpoint weights remain outside Git. These records identify this installation, not files downloaded from another source or revision.

SDXL is selected as the quality default on the verified development installation. DreamShaper and SD-Turbo remain installed and selectable. Other installations import their own workflows and choose a default explicitly; the examples do not download weights or change settings automatically. Reopening a saved Studio job restores that job's workflow and dimensions even when the global default has changed.

On the tested 6 GB VRAM/8 GB RAM machine, use batch size one and the launcher's `--cache-none --lowvram --disable-dynamic-vram` settings. Disabling intermediate node caching limits retained allocations between jobs. SDXL's CPU offloading can substantially exceed physical RAM and depend on Windows paging. Tiled decoding reduces the VAE working memory; checkpoint loading, text encoders and denoising still need their own memory. Actual single-job timings and measurement limits are recorded in [QA](QA.md#selectable-dreamshaper-8-and-sdxl-profiles). Image Studio provides direct control of the workflow, seed and dimensions for comparable checks.

The older `config/comfyui-sdxl-workflow.example.json` is a separate **SDXL-Lightning** four-step full-checkpoint example using Euler, SGM uniform and CFG 1. Those distilled settings are not the ordinary SDXL Base preset above. Install its matching checkpoint before using it; the example is not automatically imported. [ByteDance SDXL-Lightning](https://huggingface.co/ByteDance/SDXL-Lightning).

FLUX.2 klein 4B is another available local option. Its standard weights use Apache 2.0 and the upstream model card reports approximately 13 GB VRAM; quantized variants and offloading change resource use, so select a workflow suitable for your hardware rather than assuming the parameter count guarantees a fit. [Official model card](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B).

Prepare and run your desired workflow in ComfyUI first. Enable the interface's developer/API export option and save **API format** JSON. Canvas format contains a `nodes` array and cannot be passed to ComfyUI's prompt endpoint. Include a `SaveImage` node.

Import the API graph in Pixel Station Image Studio with explicit bindings:

```json
{
  "name": "My tested workflow",
  "workflow": {"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}}, "...": {}},
  "bindings": {
    "prompt": {"node": "6", "input": "text"},
    "seed": {"node": "3", "input": "seed"},
    "width": {"node": "5", "input": "width"},
    "height": {"node": "5", "input": "height"}
  }
}
```

The abbreviated graph above illustrates the wrapper only; use your complete exported graph or one of the complete examples. Bindings must reference real node inputs. `prompt` is required; the others are optional. The workflow listing exposes independently validated bound width/height defaults, and selecting a workflow applies them in Image Studio. Regeneration preserves the saved image's dimensions. In API and chat requests, each omitted dimension uses the selected/default workflow's valid bound graph value, with a 512-pixel fallback when no valid bound default exists. Accepted defaults are integers from 256 through 2048 in multiples of eight. Explicit dimensions override the corresponding binding. A workflow without a width/height binding retains its own graph dimensions, and the library stores the actual output dimensions. Natural-language size extraction is not implemented; use Studio or structured API dimensions for an exact size. Positive prompts are passed exactly as entered. Image Studio calls ComfyUI directly without an LLM rewriting them.

Generation submits to `/prompt`, follows websocket progress when available, and confirms output through `/history/{prompt_id}`. Completed images are downloaded via `/view` into the local data directory and validated as PNG/JPEG/WebP. Workflows, job state, prompts, seeds and the image library persist under `data/images`. Interrupted jobs are marked on restart; they are not silently retried. Each provider serializes image jobs. In the application, jobs also hold the existing inference lock shared with chat, Poker, embeddings and summaries. Before submitting an image, fresh Ollama `/api/ps` observations identify resident local models to unload with `keep_alive: 0`; a second observation must confirm absence. This sends no generation prompt.

Once the submitted ComfyUI prompt is confirmed inactive and its queue is otherwise empty, the provider requests `/free` with model unload and memory release. It records the server acknowledgement and fresh reported device counters separately; acknowledgement alone does not prove that GPU memory was released. Other active ComfyUI jobs, unavailable counters or uncertain release produce a visible handoff warning without discarding a completed image. These locks coordinate Pixel Station's own work; another program using Ollama or ComfyUI can still compete for resources. Cancelling removes the job's queued prompt and interrupts ComfyUI only when that specific prompt is observed executing. Coordinated jobs poll for that prompt's absence before releasing the shared inference lock. [ComfyUI server API](https://docs.comfy.org/development/comfyui-server/comms_routes).

Each job retains the endpoint chosen when it was submitted. Changing Settings affects later jobs; existing queued/running jobs continue polling, downloading and cancelling at their original ComfyUI endpoint.

Polling/download failures attempt cleanup of that job's remote prompt. If ComfyUI cannot confirm cleanup, the persisted job explicitly says it may still be running and retains a Cancel control so cleanup can be retried when the service returns. Failed/interrupted jobs with a submitted prompt are never silently described as stopped.

An invalid workflow generally indicates a missing checkpoint/custom node or a canvas export. A workflow with no saved output returns an error. Out-of-memory failures require reducing dimensions, using an appropriate model, or freeing GPU memory. Generations have a ten-minute deadline including inference queue waits and residency handoff; bounded remote cleanup/memory-release attempts can take up to another 30 seconds. The image tool and chat image wrapper allow the provider budget plus those cleanup allowances, 630 seconds with the default settings. Other integration routes retain their separate three-minute orchestration limit. Image downloads have a 40 MB cap.

## Gmail and Google Calendar

Google integration requires your own account configuration and browser consent. Pixel Station does not receive your Google password. For a personal installation:

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create/select a project.
2. Enable Gmail API and Google Calendar API in the API Library.
3. Configure Google Auth Platform branding and audience. For a personal Gmail account choose External; while testing, add your account as a test user.
4. Create an OAuth client with type **Desktop app**, download its JSON credentials, and import the file in Pixel Station Settings → Google.
5. Choose Connect Google. The system browser opens Google's consent page; log in and grant the displayed scopes.
6. Return to Pixel Station and refresh connection status after the callback says Google connected.

The client supports a loopback callback with OAuth state and PKCE. Pending authorization expires after ten minutes and is single-use. Client credentials are stored under the configured local data directory; refresh/access tokens are stored in the OS credential keyring, using a separate entry per data directory. Token storage fails explicitly if a functioning keyring is unavailable rather than falling back to plaintext files. [Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app).

Requested scopes:

| Scope | Purpose |
| --- | --- |
| `gmail.readonly` | Search and read email threads |
| `gmail.compose` | Create Gmail drafts and explicitly send reviewed messages |
| `calendar.events` | Read and manage events on accessible calendars |
| `calendar.calendarlist.readonly` | List accessible calendars |

Full `https://mail.google.com/` access is not requested. The draft provider creates MIME messages and verifies the returned Gmail draft ID by reopening it. Email bodies are used as task evidence, not automatically saved as general memory. [Gmail drafts API](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/create).

Email send and calendar create/update/delete return a pending proposal. The frontend shows the exact payload for review. Confirmation atomically consumes that proposal once, with a ten-minute expiry. Retries need a new proposal, preventing a second click or network retry from silently repeating an external action. Calendar update/delete proposals capture the existing event and its ETag; Google receives `If-Match`, so an intervening edit requires fresh review. Event times must carry explicit timezone offsets. The initial event editor supports title, description, location, start and end; invitations/recurrence are outside that editor's current scope. [Calendar event reference](https://developers.google.com/workspace/calendar/api/v3/reference/events).

If Google accepts a Calendar create/update or Gmail draft but the follow-up verification fails, the error retains the operation and returned ID. It asks you to inspect Calendar or Gmail Drafts before proposing the operation again; the confirmation remains consumed. The API exposes `accepted`, `operation` and `returned_id` alongside the actionable message, and Calendar confirmation outcomes preserve those fields locally. A send response without a message ID similarly asks you to inspect Gmail Sent before another send.

An External OAuth app left in **Testing** receives refresh tokens that expire after seven days for Gmail/Calendar scopes. Move a long-lived personal app to **In production** when appropriate for your account and Google's policies. Google can still revoke credentials, and changing a Google password can invalidate Gmail refresh tokens. Pixel Station then asks you to reconnect. [Google refresh-token rules](https://developers.google.com/identity/protocols/oauth2#expiration).

Google's verification requirements depend on the selected scopes and distribution. Personal-use exceptions can apply for an owner/small known group; an unverified-app warning or user cap may still appear. Review the console's current requirements before publishing an app for others. A Workspace organization may need administrator approval for these scopes. No public deployment or verification is performed by Pixel Station.

Disconnect removes this installation's local OS-keyring token. To revoke the app's access at Google as well, remove it from your Google Account's third-party connections. Never add credential JSON, tokens, private email, generated artifacts or databases to Git.

## Managed file edits

The Files editor and explicit chat edit requests create a reviewed proposal before changing a file. TXT and Markdown replace text, CSV replaces CSV text, XLSX replaces first-worksheet values from CSV while preserving other sheets and cell styles, and DOCX replaces paragraphs while preserving existing tables and paragraph styles. Edited spreadsheet values become text and formula-like input is escaped. DOCX inline formatting is reset. PDF edits rebuild a local PDF from reviewed text, resetting original layout, images and annotations. These scope limitations are shown before confirmation.

Edits affect the managed library copy. They never overwrite the source file on your Desktop or another original host path. A proposal binds the exact replacement, plan and original content hash, expires after ten minutes, and can be confirmed once. A changed original or altered replacement requires a new proposal. Validation finishes before updating the library; applying an edit preserves the prior bytes, keeps the same library file ID, and rebuilds retrieval chunks and provenance. The revision API lists and downloads prior originals. File proposals and revisions use Alembic revision `0002` in the core database, and their staged/revised bytes live under the configured data directory. Include the full data directory in backups.

## Provider/API boundary

`IntegrationServices` accepts a data directory and two settings callbacks. `create_integrations_router` mounts `/api/web`, `/api/images`, `/api/google` and `/api/integrations/approvals`. The classes in `backend/pixel_station/providers` expose the same functionality directly to typed chat tools. Mutating Google tools must call `propose_action`, not the provider's private transport methods. `chat_context` returns evidence for read operations and image generation; mutations need validated structured arguments and a concrete proposal.

The core application database uses Alembic migrations. Image workflows/jobs/library metadata use a separate `images/library.sqlite3`, and proposals use `integration_approvals.sqlite3`; these isolated integration stores currently create their tables directly rather than sharing the core Alembic revision chain. Back up the entire configured data directory to preserve these stores alongside image files, uploaded files and the main database. OS-keyring OAuth tokens are managed separately and are not included in a file backup; reconnect Google after restoring on another machine.

All mock HTTP transports and test credentials are confined to tests. Live Google, SearXNG and ComfyUI verification requires actually connected services and a user's consent; passing the isolated tests does not imply those services are installed or online.
