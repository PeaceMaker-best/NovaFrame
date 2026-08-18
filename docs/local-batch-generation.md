# Local Skill Batch Generation and Review

## 1. Purpose

NovaFrame uses the bundled `generate-product-images` Skill as the product-image domain layer and wraps it with a safe local execution, staging, progress, and review system.

The main user journey is:

```text
Select product / task / shot cells
  → validate prompts and curated references
  → create a persisted local run
  → generate temporary candidates
  → visualize progress in the website
  → keep selected candidates
  → delete rejected candidates
  → optionally continue in canvas
```

The website stores run state, planned items, progress events, and review decisions. Original candidate bytes remain on the local filesystem until a reviewer promotes or deletes them.

## 2. Required workspace material

For a product named `<product>`:

```text
workspace/原始商品图/<product>/
  ├── product images
  └── description/specification files

workspace/组合/<product>/单品/
  ├── prompts.json
  ├── prompts.md
  ├── reference_manifest.json
  └── 参考图/

workspace/组合/<product>/<accessory>/
  ├── prompts.json
  ├── prompts.md
  ├── reference_manifest.json
  └── 参考图/
```

Accessory source material lives under `workspace/配件超市/<accessory>/`.

The Skill preparation phase creates task and prompt baselines. Reference curation remains an explicit publishing step because incorrect identity references can invalidate an entire batch.

## 3. Prepare and preview

```bash
.venv/bin/python \
  .agents/skills/generate-product-images/scripts/product_image_workflow.py \
  prepare --product "MF-DEMO-001" --refresh-prompts

.venv/bin/python \
  .agents/skills/generate-product-images/scripts/product_image_workflow.py \
  preview --product "MF-DEMO-001" --task "单品" --shot lifestyle-scene
```

`prepare` may create or refresh prompt files. `preview` reports the selected generation scope without calling the image provider.

Before live generation, verify:

- every selected task has prompt files;
- `参考图/` contains only approved identity references;
- `reference_manifest.json` exactly describes the published reference set;
- reference roles and counts comply with standalone/combination limits;
- product facts and visible claims are supported by source evidence.

## 4. Enable managed providers

```bash
cp .env.example .env
```

Minimum gate configuration:

```dotenv
MUSEFORGE_ENABLE_LIVE_GENERATION=true
IMAGE_OUTPUT_FORMAT=png
```

Restart the API, then register channels and rate cards in **连接与设置 → 渠道与路由**. The old `IMAGE_API_*` variables remain available as a compatibility fallback when no managed channel exists. See [GPT Image 2 provider routing](provider-routing.md).

Live generation is disabled when the switch is missing or false, even if an API key is present.

## 5. Create a batch from the UI

1. Open **任务矩阵**.
2. Select the product.
3. Select one or more task rows.
4. Select one or more shot columns.
5. Choose 1–6 candidates per work item.
6. Choose the workspace default, Auto, or one explicit channel and quality.
7. Choose local concurrency from 1–10.
8. Resolve all blocked cells.
9. Submit the batch.

The expected candidate count is:

```text
selected tasks × selected shots × candidates per work item
```

The server returns a run immediately and executes it in the background. The browser navigates to the real queue record and polls every two seconds.

## 6. Create a batch through the API

```bash
curl -X POST http://127.0.0.1:38120/api/generation-runs \
  -H 'Content-Type: application/json' \
  -d '{
    "product": "MF-DEMO-001",
    "tasks": ["单品", "旅行收纳袋"],
    "shots": ["main", "lifestyle-scene"],
    "variants": 4,
    "concurrency": 2,
    "creativeBrief": {
      "environment": "Bright neutral tabletop with soft daylight.",
      "composition": "Keep the complete product centered with safe margins.",
      "negatives": "No unrelated props or invented features.",
      "visibleText": "READY TO SHIP"
    }
  }'
```

`creativeBrief` is optional. It is saved with the run and appended to the existing verified prompt; it does not overwrite task facts, reference policy, physical constraints, or compliance rules.

The endpoint returns `202 Accepted`. When live generation is disabled it returns `403` and does not create a run.

Read progress:

```bash
curl http://127.0.0.1:38120/api/generation-runs
curl http://127.0.0.1:38120/api/generation-runs/<run-id>
```

The detail response includes persisted run data and structured events.

## 7. Staging layout

Candidates never write directly into formal task output folders:

```text
workspace/.museforge/runs/<run-id>/<product>/<task>/<shot>/candidate-01.png
workspace/.museforge/runs/<run-id>/<product>/<task>/<shot>/candidate-02.png
workspace/.museforge/runs/<run-id>/run-spec.json
...
```

The Skill receives the run directory from the server. It writes image data to a same-directory `.part` file and uses atomic replacement only after the response is complete.

Candidate filenames are deterministic within a run, while run IDs isolate retries and repeated batches. Existing formal files are never overwritten by generation staging.

## 8. Structured events

The Skill emits one JSON object per machine event:

```text
MUSEFORGE_EVENT {"type":"item.saved", ...}
```

Common event types:

- `plan`
- `run.started`
- `item.started`
- `item.saved`
- `item.failed`
- `run.finished`

The backend does not trust event paths or scope. It verifies run ID, product, task, shot, candidate index, expected parent directory, filename, suffix, and regular-file status before recording a candidate.

Ordinary console output is retained as bounded run output but does not mutate generation items.

## 9. Review and storage decisions

Candidates are grouped by task and shot in **审核与交付**.

### Keep selected

For every checked staged candidate:

```http
PATCH /api/candidates/<candidate-id>
Content-Type: application/json

{"decision":"selected"}
```

The server atomically promotes the file to:

```text
workspace/组合/<product>/<task>/<中文图型>/<candidate-id>.<format>
```

The formal relative path becomes the candidate's stable workspace asset URL.

### Clean rejected

```http
DELETE /api/candidates/<candidate-id>
```

The operation removes the current candidate file and candidate row. A minimal deletion event remains in the run audit trail. Bulk “clean unselected” does not include already promoted candidates.

### Send to canvas

A staged candidate is promoted before being inserted. The handoff includes:

- product ID;
- task ID;
- shot ID;
- stable formal asset URL;
- insertion mode: background or layer.

Studio loads the matching `product + task + shot` canvas and inserts the asset after hydration completes.
The browser URL carries the same context, so the exact canvas can be refreshed or linked directly.

## 10. Storage guarantees

- Source product and accessory files are never candidate cleanup targets.
- Curated references and manifests are never candidate cleanup targets.
- Existing prompt files are not modified during generation.
- Existing formal outputs are not overwritten during promotion.
- Staging images are inaccessible through the generic workspace asset API.
- Candidate image requests use opaque database IDs rather than caller-provided paths.
- Delete, promote, and event ingestion revalidate the database-owned path.

## 11. Failure behavior

| Failure | Result |
| --- | --- |
| Live switch disabled | `403`, no run created |
| Invalid product/task/shot | validation response, no subprocess |
| Missing references/manifest | run or preflight blocked |
| Provider request failure | item marked failed; other planned items may continue |
| Invalid Skill event | event rejected; item is not silently accepted |
| Missing expected event | unfinished item marked failed when process exits |
| Workflow timeout | process killed and unfinished items failed |
| Promotion collision | `409`; existing formal file preserved |
| Candidate file missing | `404`; database state remains inspectable |

## 12. Verification

```bash
npm run verify

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  backend/tests \
  .agents/skills/generate-product-images/scripts/test_staged_generation.py \
  -q -p no:cacheprovider
```

The combined suite currently contains 20 backend and Skill-level tests.

## 13. Cloud-hosted UI evolution

The current website and API are local. If the UI is later hosted remotely, use a local companion that opens an outbound encrypted channel:

```text
local Skill/files ←→ local companion ── events/metadata ──→ cloud UI
                                      ←─ review decisions ──

selected file ── signed upload ──→ permanent object storage
rejected file ── local deletion only
```

Cross-device previews require an explicit privacy/storage policy. The viable options are direct companion access or temporary low-resolution previews with TTL and deletion guarantees. A deployment cannot promise cloud-visible original candidates while also claiming those bytes never leave the local machine.
