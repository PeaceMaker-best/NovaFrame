# Canvas Evolution Roadmap

NovaFrame's canvas is the refinement and delivery stage of an e-commerce image-production workflow. It should preserve product truth, make repetitive layout work fast, and keep generated candidates connected to their source task rather than becoming an isolated drawing tool.

## Product principles

1. **The artboard is primary.** Panels should provide context without reducing the working area more than necessary.
2. **Generation and editing stay connected.** Retained candidates, references, prompts, and canvas layers share the same product/task/shot identity.
3. **Common actions are direct.** Selection, alignment, crop, text editing, and replacement should not require modal dialogs.
4. **Automation remains reversible.** AI-assisted edits create a new layer or version and never silently destroy the source.
5. **Delivery constraints are visible early.** Channel ratio, safe area, bleed, and output size belong in the document model rather than only at export time.

## Milestone 1 — Editing foundation (implemented)

- marquee and additive multi-selection;
- grouped movement and multi-node transforms;
- artboard edge and center snapping;
- batch alignment and equal distribution;
- layer naming, visibility, locking, and ordering;
- stable local image import through the workspace API (canvas documents retain URLs, not base64 payloads);
- retained-results shelf backed by promoted candidates;
- one undo snapshot per grouped operation;
- backward-compatible persistence for existing canvas documents;
- shareable, validated product/task/shot URL context;
- canvas creative briefs persisted into real generation-run snapshots;
- independently collapsible asset and generation panels for focused artboard work;
- truthful blank-canvas onboarding that never enters the saved document.

## Milestone 2 — Precision editing

Priority: next.

- non-destructive image crop with fill, fit, and focal-point modes;
- inline text editing with typography presets, line spacing, letter spacing, and auto-fit;
- reusable groups with enter/exit group editing;
- rulers, configurable grids, margins, bleed, and marketplace safe areas;
- snapping to nearby layer edges, centers, and spacing intervals;
- numeric size, rotation, opacity, and aspect-ratio controls;
- copy/paste across product/task/shot documents with asset-reference validation.

Exit criteria: a designer can reproduce a marketplace-ready composition without leaving NovaFrame for basic crop, typography, spacing, or alignment work.

## Milestone 3 — E-commerce artboards and templates

- multiple artboards inside one product task;
- presets for common marketplace and social ratios;
- template slots for product, accessory, badge, title, price, and legal copy;
- responsive layout rules when adapting one approved design to several ratios;
- batch application of one template to retained candidates;
- shared brand tokens for typography, color, logo clear space, and copy rules.

Exit criteria: one approved visual direction can produce a consistent channel set without manually rebuilding each size.

## Milestone 4 — AI-assisted local refinement

- select-and-edit for background replacement, cleanup, relighting, and controlled expansion;
- reference-bound product replacement that preserves composition while protecting identity;
- local batch variants generated from the current artboard and structured prompt;
- before/after comparison and automatic creation of a new result layer;
- provenance metadata linking each AI edit to model, prompt, references, and source version.

Exit criteria: every AI action is observable, reversible, and traceable to product truth and its source artboard.

## Milestone 5 — Versioning and delivery

- named versions and visual history rather than latest-state-only persistence;
- review comments anchored to layers or coordinates;
- delivery presets for file type, dimensions, naming, compression, and metadata;
- pre-export checks for missing assets, hidden required copy, safe-area violations, and unsupported claims;
- package export with an audit manifest and stable links to promoted assets.

Exit criteria: a reviewer can approve a known version and operations can reproduce the exact delivered package later.

## Visual direction

- use teal for selection, editing state, and structural guidance;
- reserve lime for generation and primary production actions;
- keep controls at a minimum 38–44 px hit target where space allows;
- prefer contextual controls and collapsible panels over permanently dense toolbars;
- keep the artboard visually dominant with restrained shadows, neutral workspace chrome, and clear safe-area guides;
- show honest empty, loading, offline, and unsaved states instead of fabricated activity.

## Engineering sequence

Before Milestone 2 expands, extract document operations from `StudioPage` into a tested canvas-document module. Geometry, selection, ordering, and history operations should be pure functions; rendering and pointer handling should remain in `StudioCanvas`. This separation will make crop, grouping, multi-artboard documents, and version migrations safer to implement.
