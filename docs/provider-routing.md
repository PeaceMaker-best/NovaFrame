# GPT Image 2 Provider Routing

NovaFrame can manage several GPT Image 2 compatible channels from the local web interface. A channel combines connection details, an encrypted credential, a model identifier, a settlement currency, and user-maintained per-image estimates for low, medium, and high quality.

The registry is designed for cost visibility and deterministic execution. It is not a billing authority: a saved rate is an estimate supplied by the workspace administrator, while the provider invoice remains authoritative.

## 1. Register a channel

Open **连接与设置 → 渠道与路由 → 注册新渠道** and provide:

- a distinct display name;
- the provider base URL, such as `https://api.openai.com/v1`;
- the image edit path, normally `/images/edits`;
- the API key;
- the model ID, normally `gpt-image-2` or a compatible provider alias;
- one settlement currency;
- the estimated low, medium, and high per-image rates.

NovaFrame uses the edit endpoint because the e-commerce Skill sends curated product and accessory reference images. Registering a channel does not make a paid call.

The API key is accepted only on create or explicit key replacement. Read responses contain `has_api_key` and a short tail hint, never the credential or ciphertext.

## 2. Credential storage

Managed credentials are encrypted with Fernet before SQLite persistence. On first backend start, NovaFrame creates a database-adjacent master key:

```text
backend/data/museforge.sqlite3.key
```

On POSIX systems the file is created with mode `0600`. On Windows, inherited ACLs are removed and read/write access is granted only to the current account. The file is ignored by Git and must be backed up together with the database; a database backup without its matching key cannot decrypt managed credentials.

Credentials are never written to:

- browser state or API responses;
- canvas documents;
- prompt files;
- `workspace/.museforge/runs/<run-id>/run-spec.json`;
- subprocess command arguments.

The backend decrypts the selected run snapshot immediately before launch and passes it only in the child process environment.

## 3. Routing modes

### Workspace default

Settings stores one default routing policy. Canvas and matrix requests may use this default without naming a provider.

### Auto lowest rate

Auto applies these filters in order:

1. channel is enabled;
2. channel currency equals the workspace routing currency;
3. the requested quality has a positive saved rate.

The server then selects the smallest rate. A stable name-and-ID tie-break makes the decision deterministic. No exchange-rate conversion is attempted, and a zero/missing price is treated as unknown rather than free.

### Fixed channel

Fixed mode requires one enabled channel ID. A disabled or missing channel blocks a new run with a validation error.

Canvas and matrix controls may override the default with Auto or a fixed channel for one batch. This is how concurrent batches can deliberately use different channels.

## 4. Run snapshot and cost attribution

Provider selection happens synchronously before a run receives `202 queued`. NovaFrame stores:

- redacted provider metadata in the generation request and `run-spec.json`;
- an encrypted provider execution snapshot in `generation_provider_snapshots`;
- channel, routing mode, model, size, quality, unit price, and currency on generated-item events.

Changing or disabling a channel does not rewrite an already queued batch. This protects auditability and prevents a queued run from silently changing cost assumptions.

The initial implementation intentionally locks one provider per batch. It does not silently fail over after a paid request may have reached a provider, because an automatic retry could create duplicate charges. Operators can start a new batch with another channel after inspecting the failure.

## 5. Legacy environment fallback

When no managed channel exists and a request uses the workspace default, NovaFrame preserves the original `.env` behavior:

```dotenv
IMAGE_API_BASE_URL=https://your-provider.example/v1
IMAGE_API_ENDPOINT=/images/edits
IMAGE_API_KEY=replace-me
IMAGE_MODEL=gpt-image-2
IMAGE_COST_PER_IMAGE_LOW_USD=0.01
IMAGE_COST_CURRENCY=USD
```

Once any managed channel is registered, configure an explicit Auto or fixed policy instead of relying on the fallback.

## 6. API surface

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/provider-config` | Return redacted channels, routing settings, and counts |
| `POST` | `/api/provider-channels` | Register and encrypt a channel |
| `PATCH` | `/api/provider-channels/{id}` | Edit configuration, replace a key, or change active state |
| `PUT` | `/api/provider-routing` | Save workspace Auto/fixed routing and comparison currency |
| `POST` | `/api/generation-runs` | Resolve and snapshot a provider for a batch |

Example per-batch Auto request:

```json
{
  "product": "SKU-001",
  "tasks": ["单品"],
  "shots": ["main"],
  "variants": 4,
  "concurrency": 2,
  "providerMode": "auto",
  "quality": "medium",
  "size": "1024x1024"
}
```

## 7. Operational checklist

- Keep `MUSEFORGE_ENABLE_LIVE_GENERATION=false` while configuring or auditing channels.
- Enter comparable rates in one currency before enabling Auto.
- Mark expired channels inactive instead of deleting audit history.
- Rotate a credential by editing the channel and entering a new key.
- Back up the SQLite database and matching `.key` file as one unit.
- Verify provider invoices independently of NovaFrame cost estimates.
