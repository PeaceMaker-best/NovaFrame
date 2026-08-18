---
name: generate-product-images
description: Scan complete product folders containing source images, descriptions, specifications, and instructions; prepare and refresh five standalone e-commerce image prompts plus five accessory-combination prompts per accessory under 组合, using product-truth, reference-boundary, shot-specific, immersive-scene, and diversity rules. Use when Codex should generate or audit prompt files for the 原始商品图/配件超市/组合 workflow while leaving Image2 execution to the user.
---

# Generate Product Images

Resolve the product workspace from `MUSEFORGE_WORKSPACE_ROOT` when set. In MuseForge the default is `workspace/`; a legacy layout with `原始商品图`, `配件超市`, and `组合` directly beside the project is also accepted. Provider secrets remain in the project `.env`, outside product data.

## Workflow

1. Treat each directory directly below `原始商品图` as one product. Use its folder name as the output product name.
2. Read product images recursively. Read facts from `txt`, `md`, `json`, `docx`, and `pdf` files.
3. Treat each directory directly below `配件超市` as one accessory.
4. Route rules by purpose:
   - Always apply [01-product-truth-rules.md](references/01-product-truth-rules.md) and [02-reference-image-boundary.md](references/02-reference-image-boundary.md).
   - Apply [03-standalone-prompt-rules.md](references/03-standalone-prompt-rules.md) to standalone work.
   - Apply [04-combination-prompt-rules.md](references/04-combination-prompt-rules.md) to accessory combinations.
   - Apply [05-immersive-scene-prompt-rules.md](references/05-immersive-scene-prompt-rules.md) to every `场景图`.
   - Apply the matching section of [06-shot-specific-rules.md](references/06-shot-specific-rules.md) to all five image types.
   - Apply [07-diversity-planning-rules.md](references/07-diversity-planning-rules.md) before writing a batch of prompts: classify the main product's scene domain first, then diversify only inside compatible environments.
   - Apply [08-physical-interaction-rules.md](references/08-physical-interaction-rules.md) to every scene or use-state image: identify the verified interaction mechanism, support surface, contact points, load direction, completed state, and forbidden state before designing the environment.
   - Apply [09-visual-reference-curation-rules.md](references/09-visual-reference-curation-rules.md) before handoff: visually inspect source candidates, copy only approved images into each task's `参考图` directory, and write `reference_manifest.json`.
   - Apply [10-incremental-workflow-rules.md](references/10-incremental-workflow-rules.md) to avoid repeating unchanged source analysis, visual inspection, prompt review, and reference selection. Reuse is allowed only when its source fingerprint and quality record remain valid.
   - On overlap, product truth and compliance win first, then physical interaction validity, combination hierarchy, shot purpose, and scene diversity.
5. Write results under `组合/<product>/单品` and `组合/<product>/<accessory>`, each with `主图`, `尺寸图`, `场景图`, `细节图`, and `对比图`.
6. Generate prompt files and curate task-local reference images only. Do not call Image2 or generate new images as part of this Skill workflow.
7. Preserve every existing image. Prompt refresh must only update `prompts.json` and `prompts.md`.

## Quality-First Operation

Use the deterministic script to produce a complete evidence-based baseline, then audit quality before handoff. Accuracy and image usefulness take priority over token reduction.

1. Verify extracted product facts against source documents and reference-image filenames.
2. Verify scene-domain classification for every product. Correct misclassification before accepting prompts.
3. Inspect all five standalone prompt types and at least one complete five-prompt accessory set per product.
4. Inspect every standalone and combination `场景图` for pairing logic, incompatible environments, complete physical contact, credible support, and correct load direction.
5. Visually inspect source-image candidates and populate every task's `参考图` folder. Do not use filename order as a substitute for visual review.
6. Revise prompt files when product-specific features, dimensions, installation behavior, or scene logic are not expressed strongly enough by the baseline.
7. Run prompt and curated-reference structural quality checks again after revisions.

Save tokens only by avoiding full prompt dumps in chat. Do not shorten prompts, skip source reading, reduce validation, or accept generic scenes for token reasons. Report product count, accessory count, prompt count, output paths, scene-domain decisions, validation findings, and material manual corrections.

## Incremental Operation

For an already prepared product, read its compact workflow records before reopening all source files or displaying all prompts. Work in this order:

1. Check source fingerprints and the previous quality record.
2. Reuse unchanged verified facts and visual findings; inspect only new or changed source files.
3. Reuse a previously approved main-product reference selection across task-local folders when the same SKU and reference role are required. Keep the physical copies and manifests task-local.
4. Inspect each accessory's source images once, then reuse that visual finding for every combination involving the unchanged accessory.
5. Regenerate and re-audit only selected tasks whose product facts, accessory facts, references, rules, or prompt files changed.
6. Run deterministic structural validation on every selected task, but reserve full semantic review for changed prompts. Always fully review changed scene and physical-interaction prompts.

Never claim a cache hit from timestamps alone. A reusable record must identify its source files with path, size, modification time, and SHA-256. Missing, stale, or contradictory records require full review.

## Commands

Set the script path relative to the workspace:

```powershell
$workflow = ".agents\skills\generate-product-images\scripts\product_image_workflow.py"
```

Prepare or refresh one product's prompts without calling the image API:

```powershell
python $workflow prepare --product "PRODUCT_NAME" --refresh-prompts
```

Prepare or refresh every product's prompts:

```powershell
python $workflow prepare --refresh-prompts
```

Optionally preview which images are missing without calling the API:

```powershell
python $workflow preview --product "PRODUCT_NAME" --concurrency 10
```

Preview every prepared accessory combination for every source product while skipping all standalone tasks:

```powershell
python -B $workflow preview --combinations-only --concurrency 10
```

Omit `--product` to process all source-product folders. During normal Skill use, stop after `prepare` or `preview`. Never execute `generate` or `all`; the user runs image generation separately.

## Image-Generation Handoff

After prompts are ready, give the user this command for one product:

```powershell
python -B .agents\skills\generate-product-images\scripts\product_image_workflow.py generate --product "PRODUCT_NAME" --concurrency 10
```

Give this command for all prepared products:

```powershell
python -B .agents\skills\generate-product-images\scripts\product_image_workflow.py generate --concurrency 10
```

Generate only accessory combinations for every source product, excluding every `单品` task:

```powershell
python -B .agents\skills\generate-product-images\scripts\product_image_workflow.py generate --combinations-only --concurrency 10
```

For a source folder that has prebuilt per-SKU tasks named `单品-<color>-<SKU>`, generate only those color variants and skip the regular standalone task and all accessory combinations:

```powershell
python -B .agents\skills\generate-product-images\scripts\product_image_workflow.py generate --product "PRODUCT_FOLDER" --variants-only --concurrency 10
```

These commands consume existing prompts, skip existing images, and use at most 10 concurrent Image2 requests. Mention `--overwrite` only when the user explicitly wants to regenerate existing images.

## Reference Selection

- Visually inspect candidates; never select references by filename order alone.
- Copy approved references into `组合/<product>/<task>/参考图` and write `reference_manifest.json`.
- Use at most five `主商品-*` images for standalone generation.
- Use at most three `主商品-*` images plus two `配件-*` images for combinations.
- Exclude certification, night, low-light, incorrect-installation, ambiguous-SKU, distorted, text-heavy, branded, and redundant images.
- `preview` and `generate` must read only the curated task-local folder and must stop when it is missing or invalid.

## Validation

After preparing prompts, verify that every task folder contains five substantial prompt objects and the five expected output directories. Check product facts, supported dimensions, main/accessory hierarchy, scene-domain compatibility, `reference_image_policy`, `diversity_plan`, and the scene prompt's `interaction_plan`. Treat script validation as a minimum gate, not a substitute for product-specific judgment. Do not dump full JSON into chat. Report the separate image-generation command as the final handoff.
