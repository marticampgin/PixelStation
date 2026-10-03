# Optional integrations

Pixel Station uses local Ollama for inference. Web search, image generation and Google access have separate providers. Unavailable providers return actionable setup messages. No integration uses a commercial LLM or paid search fallback. Configuring these services is independent of ordinary chat.

## SearXNG

The local search endpoint defaults to `http://127.0.0.1:8888`. This repository includes a single-service optional Compose configuration; JSON search is explicitly enabled. Search queries leave the machine through the upstream engines selected by SearXNG. Source fetches use public HTTP(S) webpages. [SearXNG search API](https://docs.searxng.org/dev/search_api.html).

On Windows, install Docker Desktop with a working Linux-container backend first. Enabling WSL/virtualization may require administrator approval and a restart. Once Docker is running, execute from the repository root:

```powershell
$searchSecret = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
[IO.File]::WriteAllText((Join-Path (Get-Location) 'config/.env'), "SEARXNG_SECRET=$searchSecret`n")
docker compose --env-file config/.env -f config/docker-compose.optional.yml up -d
```

The ignored `config/.env` is local configuration. The Compose configuration publishes only on the host loopback address, so the service is not exposed to the LAN. Change Settings → Web → SearXNG endpoint if your service uses another address. Use Test search to verify it with an actual query.

```powershell
docker compose --env-file config/.env -f config/docker-compose.optional.yml logs --tail 100
docker compose --env-file config/.env -f config/docker-compose.optional.yml down
```

For updates, review the upstream image changes and pull deliberately; this example uses the official `latest` image. Pin an inspected image digest if you need reproducible deployment. A Docker installation is unnecessary if a SearXNG endpoint is already running elsewhere. [Official container installation](https://docs.searxng.org/admin/installation-docker.html).

A 403 from search usually means JSON is absent from `search.formats`. Connection refused means SearXNG is not started or its endpoint is wrong. Search-engine CAPTCHAs or rate limits can produce no results even when the service is healthy.

Pixel Station limits searches to 20 results, research to four queries and six fetched pages, fetch downloads to 2 MB and total fetch time to 30 seconds. Redirects are revalidated. The resolved public address is pinned for the actual connection; private/reserved/local-network addresses, file URLs, credentials in URLs and nonstandard ports are rejected. HTML text is extracted locally; JavaScript-only pages return an explanation and can be opened in a browser. The current provider does not enable a Playwright fallback.

## ComfyUI on Windows

The endpoint defaults to `http://127.0.0.1:8188`. Use an official local ComfyUI distribution compatible with your GPU. The portable distribution includes its own Python and GPU runtime. Update your NVIDIA driver before enabling GPU inference. Extract the portable archive, launch its GPU batch file, and keep its process running while generating. CPU mode is available but can be slow. [Official Windows portable instructions](https://docs.comfy.org/installation/comfyui_portable_windows).

Choose a diffusion model independently of the chat model. Model weights are not bundled or downloaded automatically. A small GPU should begin with a modest image size and one image per job. SDXL Lightning offers distilled image workflows; use the matching checkpoint and number of sampling steps. The example under `config/comfyui-sdxl-workflow.example.json` uses a four-step **full checkpoint**, Euler with the SGM uniform scheduler and CFG 1. Replace its checkpoint name with a model actually installed in `ComfyUI/models/checkpoints`. This file is an editable setup example and is not automatically imported as a production workflow. [ByteDance SDXL-Lightning](https://huggingface.co/ByteDance/SDXL-Lightning).

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

The abbreviated graph above illustrates the wrapper only; use your complete exported graph or the complete checkpoint example. Bindings must reference real node inputs. `prompt` is required; the others are optional. A workflow without a width/height binding retains its own dimensions, and the library stores the actual output dimensions. Positive prompts are passed exactly as entered. Image Studio calls ComfyUI directly without an LLM rewriting them.

Generation submits to `/prompt`, follows websocket progress when available, and confirms output through `/history/{prompt_id}`. Completed images are downloaded via `/view` into the local data directory and validated as PNG/JPEG/WebP. Workflows, job state, prompts, seeds and the image library persist under `data/images`. Interrupted jobs are marked on restart; they are not silently retried. Each provider serializes jobs to avoid overlapping inference on low-memory systems. Cancelling a job removes its queued prompt and only interrupts ComfyUI when that specific prompt is executing. [ComfyUI server API](https://docs.comfy.org/development/comfyui-server/comms_routes).

An invalid workflow generally indicates a missing checkpoint/custom node or a canvas export. A workflow with no saved output returns an error. Out-of-memory failures require reducing dimensions, using an appropriate model, or freeing GPU memory. Generations have a ten-minute deadline; image downloads have a 40 MB cap.

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

An External OAuth app left in **Testing** receives refresh tokens that expire after seven days for Gmail/Calendar scopes. Move a long-lived personal app to **In production** when appropriate for your account and Google's policies. Google can still revoke credentials, and changing a Google password can invalidate Gmail refresh tokens. Pixel Station then asks you to reconnect. [Google refresh-token rules](https://developers.google.com/identity/protocols/oauth2#expiration).

Google's verification requirements depend on the selected scopes and distribution. Personal-use exceptions can apply for an owner/small known group; an unverified-app warning or user cap may still appear. Review the console's current requirements before publishing an app for others. A Workspace organization may need administrator approval for these scopes. No public deployment or verification is performed by Pixel Station.

Disconnect removes this installation's local OS-keyring token. To revoke the app's access at Google as well, remove it from your Google Account's third-party connections. Never add credential JSON, tokens, private email, generated artifacts or databases to Git.

## Provider/API boundary

`IntegrationServices` accepts a data directory and two settings callbacks. `create_integrations_router` mounts `/api/web`, `/api/images`, `/api/google` and `/api/integrations/approvals`. The classes in `backend/pixel_station/providers` expose the same functionality directly to typed chat tools. Mutating Google tools must call `propose_action`, not the provider's private transport methods. `chat_context` returns evidence for read operations and image generation; mutations need validated structured arguments and a concrete proposal.

The core application database uses Alembic migrations. Image workflows/jobs/library metadata use a separate `images/library.sqlite3`, and proposals use `integration_approvals.sqlite3`; these isolated integration stores currently create their tables directly rather than sharing the core Alembic revision chain. Back up the entire configured data directory to preserve these stores alongside image files, uploaded files and the main database. OS-keyring OAuth tokens are managed separately and are not included in a file backup; reconnect Google after restoring on another machine.

All mock HTTP transports and test credentials are confined to tests. Live Google, SearXNG and ComfyUI verification requires actually connected services and a user's consent; passing the isolated tests does not imply those services are installed or online.
