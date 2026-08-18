# NovaFrame Whole-Product Roadmap

NovaFrame should evolve as a truth-constrained e-commerce visual production
system, not as another general-purpose prompt box. Its durable advantage is the
closed loop between product evidence, prepared tasks, exact generation scope,
human review, canvas refinement, and reproducible delivery.

## Product north star

An operator should be able to turn one verified product folder into a
marketplace-ready, reviewable image set with predictable cost and no loss of
product truth.

The primary health metric is **cost per approved deliverable**. Supporting
metrics are:

- time from source ingestion to the first ready generation cell;
- first-pass candidate acceptance rate by task and shot;
- provider cost and latency per approved image;
- percentage of blocked cells with an actionable readiness diagnosis;
- review time per candidate group;
- regeneration rate caused by product-fidelity, copy, or policy defects;
- percentage of delivered assets with reproducible provenance.

## Current foundation

The local-first release already provides the critical production spine:

- exact task/shot matrix selection and bounded multi-candidate runs;
- server-side live-generation and queue-cap gates;
- atomic run creation with a frozen provider snapshot;
- run-owned, fingerprinted prompt/reference/workflow input bundles;
- structured event ingestion and staged candidate storage;
- explicit review, atomic promotion, cleanup, and canvas handoff;
- a single-instance restart contract that never silently retries paid work;
- loopback-only defaults and clearly labelled demonstration behavior.

This foundation should remain small and dependable while the product loop is
made faster and more measurable.

## Phase 1 — Close the production loop

Priority: next.

### Readiness that users can fix

- expose prompt, manifest, reference-role, and policy failures per matrix cell;
- add manifest editing and publishing with validation and change provenance;
- mark prepared cells stale when their product truth or curated references
  change;
- provide a guided “fix next blocker” flow from the matrix.

### Cost-controlled execution

- add per-run and daily monetary budgets, a global provider-call semaphore, and
  an idempotency key for every paid submission;
- validate provider response byte size, image signature, decoded dimensions,
  and pixel budget before accepting a candidate;
- add explicit cancellation for calls that have not started;
- make retry a new, user-confirmed run that records its source run and never
  pretends to resume an uncertain provider charge;
- reconcile estimated and reported cost by run, channel, task, and shot.

### Review that improves the next run

- show the full event and failure timeline in the queue;
- add a structured review scorecard for product fidelity, composition, copy,
  and commercial usability;
- preserve rejection reasons and aggregate them by prompt, provider, and shot;
- compare variants side by side and retain one deliberate decision per work
  item;
- export an approved set with naming rules and a provenance manifest.

Phase 1 exit criteria: an operator can diagnose readiness, approve a bounded
cost, run, review, and export without inspecting files or logs manually.

## Phase 2 — Turn successful work into a reusable system

- introduce brand kits for typography, colors, logos, disclaimers, and copy
  policy;
- add channel-specific artboards, safe areas, output presets, and validation;
- convert approved compositions into slot-based templates;
- adapt one approved direction across ratios without rebuilding it manually;
- add named canvas versions and non-destructive crop, masking, text, and
  grouping operations;
- use review outcomes to recommend prompt/template improvements while keeping
  changes reviewable.

Phase 2 exit criteria: one approved visual direction can produce a consistent
channel set, and a team can measure which templates and prompts create accepted
images most efficiently.

## Phase 3 — Scale execution and collaboration deliberately

- move paid execution to persistent workers with leases, heartbeats, bounded
  retries, and provider idempotency support;
- use a local companion when a hosted review UI needs access to local source
  material;
- upload temporary previews only with explicit retention and access policies;
- store permanent remote assets only after approval;
- add authentication, organization workspaces, roles, comments, and audit
  policies before exposing write APIs beyond a trusted machine;
- support remote GPU or provider workers behind organization-level budgets and
  routing policy.

Phase 3 exit criteria: multiple users and workers can collaborate without
weakening product-truth, cost, provenance, or data-locality guarantees.

## Deliberate non-goals

Until the production loop has measurable adoption, avoid:

- a public prompt marketplace;
- an open-ended node graph that bypasses the verified workflow;
- autonomous provider retry after an uncertain charge;
- broad cloud synchronization of raw product folders;
- adding many image models before routing quality and cost are measurable.

These features increase surface area without improving the core metric of cost
per approved, reproducible deliverable.
