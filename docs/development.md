# Development and Operations Guide

## 1. Repository layout

```text
backend/                  FastAPI, SQLite repository, workflow adapter, tests
src/                      React application and Konva canvas
public/demo/              Browser demonstration assets
.agents/skills/           Bundled product-image Skill and its tests
workspace/                Runtime product workspace
  原始商品图/              Product sources
  配件超市/                Accessory sources
  组合/                    Prepared tasks, curated references, formal outputs
docs/                     Product and technical documentation
```

Runtime artifacts such as `.env`, `.venv`, `dist`, `workspace/.museforge/runs`, SQLite files, Python caches, and generated outputs are ignored by Git.

## 2. Local environment

```bash
npm install
npm run setup:api
```

Start both services:

```bash
npm run dev
```

Or run them separately:

```bash
npm run dev:api
npm run dev:ui
```

## 3. Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `MUSEFORGE_ALLOW_LAN` | `false` | Set exactly to `true` to bind the Vite development/preview UI to `0.0.0.0` instead of loopback |
| `MUSEFORGE_ENABLE_LIVE_GENERATION` | `false` | Explicit live image-call gate |
| `MUSEFORGE_MAX_GENERATION_ITEMS` | `120` | Maximum planned candidates accepted in one batch, clamped to 1–3000 |
| `MUSEFORGE_MAX_ACTIVE_GENERATION_RUNS` | `4` | Maximum queued plus running generation batches, clamped to 1–50 |
| `MUSEFORGE_WORKSPACE_ROOT` | `./workspace` | Product workspace root |
| `MUSEFORGE_DB_PATH` | `backend/data/museforge.sqlite3` | Runtime database |
| `MUSEFORGE_WORKFLOW_SCRIPT` | bundled Skill entrypoint | Reviewed workflow executable |
| `MUSEFORGE_WORKFLOW_TIMEOUT_SECONDS` | `3600` | Run timeout, clamped to 10–21600 seconds |
| `MUSEFORGE_CORS_ORIGINS` | local UI origins | Allowed browser origins |
| `IMAGE_API_BASE_URL` | empty | Legacy provider fallback base URL |
| `IMAGE_API_ENDPOINT` | `/images/edits` | Legacy fallback endpoint |
| `IMAGE_API_KEY` | empty | Legacy fallback secret; managed-channel keys are encrypted in SQLite |
| `IMAGE_MODEL` | `gpt-image-2` | Legacy fallback model |
| `IMAGE_SIZE` | `1024x1024` | Legacy fallback output size |
| `IMAGE_QUALITY` | `medium` | Legacy fallback quality |
| `IMAGE_OUTPUT_FORMAT` | `png` | Output format |

Project `.env` values are optional and never override variables already exported in the process environment.
Managed providers are configured from the Settings UI. Back up `museforge.sqlite3` and `museforge.sqlite3.key` together.

Both UI modes bind to `127.0.0.1` by default. To open the development UI to other devices on a trusted LAN:

```bash
MUSEFORGE_ALLOW_LAN=true npm run dev
```

Use `MUSEFORGE_ALLOW_LAN=true npm run preview` for the production-build preview. Connect through `http://<host-lan-ip>:33020`, not `0.0.0.0`. Vite continues to proxy `/api` to the loopback-only API process, so enabling LAN UI access also gives LAN clients the same API capabilities as a local browser. There is currently no LAN-user authentication; never enable this mode on a public or untrusted network.

## 4. Frontend commands

```bash
npm run typecheck
npm run build
npm run preview
```

`npm run typecheck` explicitly checks both application and Vite configuration projects. Do not replace it with a bare `tsc --noEmit` at the repository root; the root TypeScript configuration contains project references and that command can report a false success without checking the application.

## 5. Test commands

```bash
npm run test:frontend
npm run test:backend
npm run verify
```

Full backend and Skill suite:

```bash
npm run python -- -m pytest \
  backend/tests \
  .agents/skills/generate-product-images/scripts/test_staged_generation.py \
  -q -p no:cacheprovider
```

## 6. API diagnostics

```bash
curl http://127.0.0.1:38120/api/health
curl http://127.0.0.1:38120/api/workspace
curl http://127.0.0.1:38120/api/generation-runs
curl http://127.0.0.1:38120/api/candidates
```

A healthy safe-default development instance reports `live_generation_enabled: false`.

## 7. Database notes

SQLite runs in WAL mode. Do not commit files from `backend/data/`.

Canvas documents are latest-version records rather than a complete revision-history log. Generation events provide an append-only audit trail for run/item/review activity.

When adding schema changes:

1. make table creation idempotent;
2. preserve existing canvas and job records;
3. update `PRAGMA user_version` only after migration succeeds;
4. add a migration test using an old-schema database fixture.

## 8. Workflow adapter rules

Changes to the generation adapter must preserve these invariants:

- never accept an executable or script path from the browser;
- never invoke the workflow through a shell;
- validate every task and shot before process creation;
- keep run output inside the server-created run directory;
- treat all structured events as untrusted input;
- preserve reference-manifest gates;
- do not overwrite existing formal assets;
- keep live generation disabled by default.

## 9. Canvas correctness checklist

When changing Studio persistence or interaction, verify:

1. edits during an in-flight save become the final server document;
2. switching product, task, or shot does not cross-write documents;
3. leaving within the debounce window preserves session data;
4. a failed GET cannot trigger a fallback PUT;
5. offline edits retain `pendingSync` until reconnect;
6. two asynchronously loaded images do not overwrite each other;
7. custom pan and zoom survive reload and resize;
8. background movement remains locked through drag, keyboard, and properties;
9. PNG export is exactly 1024 × 1024 and excludes editor overlays;
10. export failure restores Stage position, scale, and layer visibility.
11. marquee and additive selection exclude hidden and locked nodes;
12. a grouped drag, alignment, or distribution operation creates one undo snapshot;
13. hidden image layers do not block export readiness or appear in the PNG;

## 10. Commit hygiene

Before committing:

```bash
npm run verify
npm run python -- -m pytest \
  .agents/skills/generate-product-images/scripts/test_staged_generation.py \
  -q -p no:cacheprovider
git status --short
```

Check that no `.env`, database, generated candidate, provider response, or credential is staged.
