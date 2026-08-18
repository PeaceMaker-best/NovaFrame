# NovaFrame Technical Architecture

## 1. System overview

```text
┌──────────────────────────────────────────────────────────────┐
│ React 19 + TypeScript + Vite                                │
│                                                              │
│ Overview · Matrix · Queue · Review · Studio · Settings       │
│ Zustand state                    Konva editing canvas          │
└───────────────────────────┬──────────────────────────────────┘
                            │ /api (Vite proxy in development)
┌───────────────────────────▼──────────────────────────────────┐
│ FastAPI + Pydantic                                            │
│                                                              │
│ Workspace scan · Canvas API · Run API · Candidate API         │
│ Workflow validation · Static asset boundary                   │
└───────────────┬───────────────────────┬──────────────────────┘
                │                       │
        ┌───────▼────────┐      ┌──────▼──────────────────────┐
        │ SQLite metadata │      │ Workspace filesystem       │
        │ canvases, jobs, │      │ source, prompts, refs,     │
        │ items, events   │      │ staging, formal outputs    │
        └────────────────┘      └──────────┬──────────────────┘
                                           │
                                ┌──────────▼──────────────────┐
                                │ generate-product-images     │
                                │ Skill + Image2 adapter      │
                                └─────────────────────────────┘
```

Default development addresses:

- UI: `127.0.0.1:33020`
- API: `127.0.0.1:38120`
- optional ComfyUI/CanvasPilot service: `127.0.0.1:38188`

Both the Vite UI and API bind to loopback by default. Vite proxies `/api` to the local API during development and build preview and rewrites the upstream host to loopback. FastAPI rejects non-loopback host headers to reduce DNS-rebinding exposure. Setting `MUSEFORGE_ALLOW_LAN=true` changes only the Vite listener to `0.0.0.0`; because `/api` remains available through that listener, this opt-in exposes all current UI/API capabilities to the trusted LAN without user authentication. The FastAPI process remains loopback-only.

## 2. Frontend architecture

### Page responsibilities

| Module | Responsibility |
| --- | --- |
| `AppShell` | Workspace bootstrap, health state, navigation, and notifications |
| `OverviewPage` | Production summary and pipeline overview |
| `MatrixPage` | Product/task/shot selection and batch submission |
| `QueuePage` | Real generation-run polling and progress presentation |
| `ReviewPage` | Candidate grouping, keep/delete decisions, and canvas handoff |
| `StudioPage` | Canvas document lifecycle, retained assets, layer metadata, alignment, prompt editor, history, and export |
| `StudioCanvas` | Konva stage, multi-selection, snapping, transforms, content clipping, viewport interaction, and rendering |

### Client state

Zustand holds lightweight cross-page context:

- loaded workspace summary;
- API and demonstration-mode state;
- selected product, task, and shot;
- active generation-run ID;
- pending candidate-to-canvas insert request;
- demonstration jobs and notifications.

Server-owned run and candidate collections are fetched by their pages rather than treated as authoritative browser state.

### Canvas persistence

The document ID is a bounded, stable value derived from product, task, shot, and a hash of the full identity.

Each document contains:

- nodes;
- structured prompt;
- task and shot metadata;
- viewport position, zoom, and fit/custom mode.

The product, task, and shot identity is also encoded in the Studio query string. Zustand keeps fast in-session navigation state, while the URL is the recoverable and shareable context source after workspace validation.

The browser uses:

1. SQLite through `GET/PUT /api/canvases/{id}` as the online authority;
2. session storage as a synchronous recovery layer;
3. a module-level per-canvas save queue to serialize writes across component mounts;
4. revision tracking to schedule a trailing save when editing continues during an in-flight request.

Load error and save error are distinct states. A load error blocks editing and automatic writes until a successful reload, preventing fallback content from overwriting an existing server document.

## 3. Backend architecture

### Settings

`Settings.from_env()` resolves the product workspace, database, Skill entrypoint, live-generation gate, and workflow timeout. Product data defaults to the repository's `workspace/` directory, while the Skill remains project tooling under `.agents/`. A project `.env` is loaded with `override=False`, so explicitly exported shell variables keep precedence.

### Workspace scanner

The scanner reads the directory contract and returns:

- products and accessories;
- prepared task folders;
- prompt and reference-manifest readiness;
- output counts and representative images;
- warnings and pending-review totals.

The generic workspace asset route only serves validated image paths inside allowed business directories. `.museforge` is excluded.

### SQLite repository

The default runtime database is:

```text
backend/data/museforge.sqlite3
```

It is a runtime artifact and is ignored by Git.

Primary records:

| Table | Purpose |
| --- | --- |
| `canvases` | Latest saved canvas document and monotonic version |
| `jobs` | Workflow and generation-run request snapshots and output |
| `generation_items` | One planned candidate per explicitly selected task/shot pair and candidate index |
| `generation_events` | Structured run/item/review audit events |
| `provider_channels` | Redacted connection metadata, rate cards, state, and encrypted credentials |
| `provider_routing_settings` | Workspace Auto/fixed default and comparison currency |
| `generation_provider_snapshots` | Immutable encrypted execution credential and price snapshot per run |

Schema creation and migration are performed on repository initialization. Existing version-1 databases are upgraded without dropping canvas or job data.

## 4. Generation-run lifecycle

`POST /api/generation-runs` validates the request, creates the complete planned item set, and returns `202` without waiting for image generation.

The current executor is process-local and intentionally never auto-retries an interrupted paid run. The API holds an exclusive database-adjacent instance lock; a second API process using the same database is refused. Graceful shutdown keeps that lock while all accepted paid workers drain. Only after a new process gains exclusive ownership does startup atomically close any stale `queued` or `running` run as `failed`, fail its unfinished candidates, and append a recovery audit event. A future persistent-worker phase can replace this single-instance contract with leases and deliberate resume semantics without risking implicit duplicate charges.

```text
HTTP request
  → validate product/task/shot/items/limits and creative-brief policy
  → build reviewed command argv
  → fingerprint prepared prompts, reference manifests/images, and workflow code
  → resolve provider
  → atomically persist run + exact planned items + initial event + encrypted provider snapshot
  → submit background executor task
  → return 202 queued
```

The executor launches only the configured `product_image_workflow.py` entrypoint with `shell=False`. It passes these server-owned environment variables:

- `MUSEFORGE_RUN_ID`
- `MUSEFORGE_RUN_DIR`
- `MUSEFORGE_RUN_SPEC_PATH`
- `MUSEFORGE_VARIANTS`
- `PYTHONUNBUFFERED=1`
- `IMAGE_API_BASE_URL`, `IMAGE_API_ENDPOINT`, `IMAGE_API_KEY`
- `IMAGE_MODEL`, `IMAGE_SIZE`, `IMAGE_QUALITY`
- `MUSEFORGE_PROVIDER_CHANNEL_ID`, name, and routing mode

Before launch, the backend verifies that every captured input still matches its queued size and SHA-256 fingerprint, copies those exact bytes into the run-owned `input-bundle/`, and executes the copied workflow against copied prompts, manifests, and references. It then writes `workspace/.museforge/runs/<run-id>/run-spec.json` with the validated exact item scope, execution limits, source fingerprints, bundle location, optional canvas creative brief, and redacted provider/cost attribution. The encrypted API key remains in the separate database snapshot. Later edits to source workspace files cannot change an already materialized paid run. The Skill appends creative instructions to the selected task prompt while preserving product truth, references, physical rules, and compliance text.

The Skill emits line-delimited events prefixed with `MUSEFORGE_EVENT `. The adapter validates every event against the persisted request scope before updating a planned item.

Saved candidates must match exactly:

```text
workspace/.museforge/runs/<run-id>/<product>/<task>/<shot>/candidate-XX.<format>
```

Paths, suffixes, candidate indices, task names, shot IDs, and run IDs are checked again at ingestion.

## 5. Candidate lifecycle

### Staged candidate

- stored below one run directory;
- served only by `/api/candidates/{candidate-id}/image`;
- not visible through the generic workspace route;
- deletable with its database row.

### Promotion

`PATCH /api/candidates/{id}` with `{"decision":"selected"}`:

1. loads the database-owned candidate path;
2. verifies it is a regular in-scope file;
3. builds the formal destination from product, task, and shot;
4. atomically moves the file using `os.replace`;
5. updates review/storage status and relative path;
6. records a selection event.

Formal files are served through the stable workspace asset route. This decouples saved canvas documents from the candidate-row lifetime.

### Deletion

`DELETE /api/candidates/{id}` removes the current staged or formal file, records a deletion event, and deletes the candidate row. The UI excludes promoted assets from bulk “unselected” cleanup and warns before an explicit formal-asset deletion.

## 6. Skill boundary

The bundled Skill remains the domain-rules owner for:

- product truth and sensitive claims;
- standalone and combination prompt construction;
- shot-specific requirements;
- scene diversity and physical interaction;
- curated visual-reference roles and limits;
- incremental prompt preparation.

The API is the execution and storage boundary. Browser callers cannot pass a script path, arbitrary command, output directory, or shell fragment.

The lower-level `image2_combo_batch.py` is used by the reviewed Skill flow; it is not exposed as an arbitrary browser-selected API entrypoint.

## 7. Security boundaries

### Input validation

- product and task values must be a single non-hidden folder segment;
- shots belong to a fixed allowlist;
- variants are limited to 1–6;
- concurrency is limited to 1–10;
- canvas and candidate IDs have bounded lengths;
- arbitrary absolute paths, traversal segments, and control characters are rejected; generation inputs reject symlinks, while generic asset paths are resolved and required to remain inside their allowed root.

### Execution safety

- live generation is disabled unless `MUSEFORGE_ENABLE_LIVE_GENERATION=true`;
- subprocesses receive argument arrays and use `shell=False`;
- only the server-configured Skill entrypoint is executable;
- execution has a bounded timeout;
- event output is treated as untrusted until scope and file checks pass;
- managed provider secrets are encrypted at rest, redacted from API responses, excluded from run specs, and exposed only to the selected child process environment;
- the local master key is database-adjacent, mode `0600`, ignored by Git, and must travel with database backups;
- Auto routing compares only enabled channels with positive rates in the configured currency.

### File safety

- source products, curated references, prompts, and prior formal outputs are not candidate-cleanup targets;
- candidate serving resolves paths from the database, not request-provided filenames;
- staging files are private to the candidate endpoint;
- promotion rejects destination collisions instead of overwriting an existing file;
- image writes use temporary `.part` files followed by atomic replacement.
- browser image imports are base64-decoded, bounded by size, checked against declared image MIME/extension allowlists, and persisted below `原始商品图/<product>/画布导入/`; canvas documents store only the returned workspace URL. Pixel-level decoding remains a future hardening step.

## 8. API surface

| Area | Endpoints |
| --- | --- |
| Service | `GET /api/health` |
| Workspace | `GET /api/workspace`, `POST /api/workspace/assets/import`, `GET /api/workspace/assets/{path}` |
| Canvas | `GET /api/canvases/{id}`, `PUT /api/canvases/{id}` |
| Legacy jobs/demo | `GET /api/jobs`, `POST /api/jobs/demo` |
| Runs | `POST /api/generation-runs`, `GET /api/generation-runs`, `GET /api/generation-runs/{id}` |
| Candidates | `GET /api/candidates`, image `GET`, decision `PATCH`, deletion `DELETE` |
| Workflow | `POST /api/workflow/prepare` and `preview`; legacy direct `generate` returns `410`, and all generation uses `/api/generation-runs` |
| Providers | `GET /api/provider-config`, `POST/PATCH /api/provider-channels`, `PUT /api/provider-routing` |

OpenAPI schemas are available from `/docs` while the API is running.

## 9. Testing strategy

Backend and Skill tests cover:

- health, workspace, stable asset imports, canvas round trips, and persistence;
- canvas creative-brief request normalization and immutable run-spec persistence;
- workspace traversal and hidden-path protection;
- old database migration;
- non-blocking run creation;
- event parsing and rejection;
- multi-candidate planning and progress;
- candidate image serving, promotion, and deletion;
- CORS methods needed by review actions;
- run-directory scope enforcement;
- `.part` file and atomic output behavior.

Frontend correctness is gated by strict TypeScript checking and a production Vite build. Critical canvas context switching has also been exercised in a real Chromium session.

## 10. Cloud evolution

The current build is intentionally local-first. A cloud-hosted UI should not gain arbitrary access to local files or secrets.

Recommended evolution:

```text
local filesystem + Skill
        ↕
local companion ── outbound encrypted event channel ── cloud control plane
        │                                               │
        └── retained file upload via signed URL ◀───────┘
```

Rejected original candidates remain local. If cross-device candidate preview is required, the deployment must explicitly choose either direct companion access or temporary low-resolution preview objects with TTL and deletion guarantees.
