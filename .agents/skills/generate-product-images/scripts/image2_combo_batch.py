from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from image2_test import (
    ENV_PATH,
    ROOT,
    LOG_PATH,
    auth_headers,
    build_url,
    cost_per_image,
    decode_image_response,
    load_mixed_env,
    parse_bool,
    read_prompt_objects,
    reference_images,
    write_cost_log,
)


FOLDERS = {
    "combo_root": "\u7ec4\u5408",
    "refs": "\u53c2\u8003\u56fe",
    "main": "\u4e3b\u56fe",
    "scene": "\u573a\u666f\u56fe",
    "comparison": "\u5bf9\u6bd4\u56fe",
    "detail": "\u7ec6\u8282\u56fe",
    "size": "\u5c3a\u5bf8\u56fe",
}

COMBO_ROOT_CANDIDATES = (
    "\u7ec4\u5408",
    "ZJJMT009-ZJJMT016-ZJJMT018-ZJJMT027",
)

PRODUCT_REFERENCE_SOURCE = {
    "ZJJMT009": "ZJJMT009",
    "ZJJMT016": "ZJJMT016",
    "ZJJMT018": "ZJJMT016",
    "ZJJMT027": "ZJJMT016",
}

SHOT_FOLDER_MAP = {
    "lifestyle-scene": FOLDERS["scene"],
    "comparison": FOLDERS["comparison"],
    "detail": FOLDERS["detail"],
    "size": FOLDERS["size"],
    "main": FOLDERS["main"],
}

SUPPORTED_OUTPUT_FORMATS = {"png", "jpeg", "jpg", "webp"}


def image_output_format() -> str:
    """Return a safe file extension accepted by the image workflow."""
    value = os.getenv("IMAGE_OUTPUT_FORMAT", "png").strip().lower().lstrip(".")
    if value not in SUPPORTED_OUTPUT_FORMATS:
        allowed = ", ".join(sorted(SUPPORTED_OUTPUT_FORMATS))
        raise ValueError(f"Unsupported IMAGE_OUTPUT_FORMAT={value!r}; expected one of: {allowed}")
    return value


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Write a complete sibling .part file, then atomically publish it."""
    if not content:
        raise ValueError("Image API returned an empty image")
    path.parent.mkdir(parents=True, exist_ok=True)
    part_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        part_path.write_bytes(content)
        os.replace(part_path, path)
    finally:
        # os.replace removes the source on success. Clean up an interrupted write.
        try:
            part_path.unlink()
        except FileNotFoundError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Image2 images for combo prompt folders.")
    parser.add_argument("--combo", help="One combo folder, for example: 组合/ZJJMT009/压缩毛巾")
    parser.add_argument("--product", help="Run all combo folders under one product, for example: ZJJMT009")
    parser.add_argument("--all", action="store_true", help="Run all combo folders under 组合.")
    parser.add_argument("--concurrency", type=int, help="Concurrent image requests. Defaults to IMAGE_COMBO_CONCURRENCY.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing images for this run.")
    parser.add_argument("--dry-run", action="store_true", help="Only list missing images; do not call the API.")
    return parser.parse_args()


def combo_root() -> Path:
    configured = os.getenv("IMAGE_COMBO_ROOT", "").strip()
    candidates = [configured] if configured else list(COMBO_ROOT_CANDIDATES)
    for candidate in candidates:
        path = (ROOT / candidate).resolve()
        if path.is_dir():
            return path
    raise ValueError(f"Combo root does not exist. Checked: {', '.join(candidates)}")


def output_folder_for_filename(filename: str) -> str:
    for suffix, folder in SHOT_FOLDER_MAP.items():
        if filename.endswith(suffix):
            return folder
    return FOLDERS["main"]


def discover_combo_dirs(product: str | None = None) -> list[Path]:
    root = combo_root()
    search_root = root / product if product else root
    if not search_root.exists():
        raise ValueError(f"Combo root does not exist: {search_root}")
    pattern = "*" if product else "*/*"
    return sorted(path for path in search_root.glob(pattern) if path.is_dir() and (path / "prompts.json").exists())


def combo_dirs_from_args(args: argparse.Namespace) -> list[Path]:
    if args.combo:
        requested = Path(args.combo)
        direct = (ROOT / requested).resolve()
        if direct.exists():
            return [direct]
        parts = requested.parts
        if parts and parts[0] == FOLDERS["combo_root"]:
            direct = combo_root().joinpath(*parts[1:]).resolve()
        return [direct]
    if args.product:
        return discover_combo_dirs(args.product)
    if args.all:
        return discover_combo_dirs()

    combo_rel = os.getenv("IMAGE_BATCH_COMBO") or os.getenv("IMAGE_TEST_COMBO")
    if combo_rel:
        requested = (ROOT / combo_rel).resolve()
        if requested.exists():
            return [requested]
    return [(combo_root() / "ZJJMT009" / "25-25\u6bdb\u5dfe").resolve()]


def source_reference_images(combo_dir: Path) -> list[Path]:
    """Resolve references from source catalogs so combo folders stay clean."""
    product_code = combo_dir.parent.name
    accessory_name = combo_dir.name
    source_code = PRODUCT_REFERENCE_SOURCE.get(product_code, product_code)
    product_dir = ROOT / source_code / FOLDERS["main"]
    accessory_dir = ROOT / "\u914d\u4ef6\u8d85\u5e02" / accessory_name

    product_refs = reference_images(product_dir) if product_dir.is_dir() else []
    accessory_refs = reference_images(accessory_dir) if accessory_dir.is_dir() else []
    refs = product_refs[:3] + accessory_refs[:2]
    if refs:
        return refs[:5]

    local_ref_dir = combo_dir / FOLDERS["refs"]
    return reference_images(local_ref_dir) if local_ref_dir.is_dir() else []


def combo_has_missing_images(combo_dir: Path, prompts: list[dict[str, Any]]) -> bool:
    if parse_bool(os.getenv("IMAGE_OVERWRITE")):
        return True
    output_format = image_output_format()
    for job in prompts:
        filename = str(job.get("filename") or "image2")
        out_path = combo_dir / output_folder_for_filename(filename) / f"{filename}.{output_format}"
        if not out_path.exists():
            return True
    return False


def missing_outputs(combo_dir: Path, prompts: list[dict[str, Any]]) -> list[Path]:
    output_format = image_output_format()
    missing: list[Path] = []
    for job in prompts:
        filename = str(job.get("filename") or "image2")
        out_path = combo_dir / output_folder_for_filename(filename) / f"{filename}.{output_format}"
        if parse_bool(os.getenv("IMAGE_OVERWRITE")) or not out_path.exists():
            missing.append(out_path)
    return missing


def request_image(
    *,
    combo_dir: Path,
    refs: list[Path],
    job: dict[str, Any],
    index: int,
    total: int,
    output_path: Path | None = None,
    allow_overwrite: bool | None = None,
) -> dict[str, Any]:
    filename = str(job.get("filename") or f"image2-{index + 1:02d}")
    output_format = image_output_format()
    out_path = output_path or combo_dir / output_folder_for_filename(filename) / f"{filename}.{output_format}"
    if not out_path.is_absolute():
        raise ValueError("request_image output_path must be absolute")
    controlled_run_dir = os.getenv("MUSEFORGE_RUN_DIR", "").strip()
    if output_path is not None and controlled_run_dir:
        run_dir = Path(controlled_run_dir)
        if not run_dir.is_absolute():
            raise ValueError("MUSEFORGE_RUN_DIR must be absolute")
        resolved_run_dir = run_dir.resolve()
        resolved_out_path = out_path.resolve()
        try:
            resolved_out_path.relative_to(resolved_run_dir)
        except ValueError as exc:
            raise ValueError("request_image output_path escaped MUSEFORGE_RUN_DIR") from exc
        out_path = resolved_out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    overwrite = parse_bool(os.getenv("IMAGE_OVERWRITE")) if allow_overwrite is None else allow_overwrite
    if out_path.exists() and not overwrite:
        return {
            "status": "skipped",
            "index": index,
            "filename": filename,
            "path": str(out_path),
            "reason": "exists",
            "estimated_cost": 0.0,
        }

    model = os.getenv("IMAGE_MODEL", "gpt-image-2")
    size = os.getenv("IMAGE_SIZE", "1024x1024")
    quality = os.getenv("IMAGE_QUALITY", "low")
    file_field = os.getenv("IMAGE_FILE_FIELD", "image[]")
    url = build_url()

    prompt_text = json.dumps(job, ensure_ascii=False, indent=2)
    data = {
        "model": model,
        "prompt": prompt_text,
        "size": size,
        "quality": quality,
        "output_format": output_format,
    }

    handles = []
    try:
        files = []
        for ref in refs:
            handle = ref.open("rb")
            handles.append(handle)
            mime = mimetypes.guess_type(ref.name)[0] or "application/octet-stream"
            files.append((file_field, (ref.name, handle, mime)))

        start = time.perf_counter()
        response = requests.post(url, headers=auth_headers(), data=data, files=files, timeout=600)

        if not response.ok and file_field == "image[]":
            for handle in handles:
                handle.seek(0)
            retry_files = []
            for ref, handle in zip(refs, handles):
                mime = mimetypes.guess_type(ref.name)[0] or "application/octet-stream"
                retry_files.append(("image", (ref.name, handle, mime)))
            response = requests.post(url, headers=auth_headers(), data=data, files=retry_files, timeout=600)

        elapsed = round(time.perf_counter() - start, 2)
        if not response.ok:
            body = response.text[:1200].replace("\n", " ")
            raise RuntimeError(f"HTTP {response.status_code}; {body}")

        image_bytes = decode_image_response(response)
        # A different process may have completed this candidate while the request
        # was in flight. Staged candidates are immutable, so keep the first result.
        if out_path.exists() and not overwrite:
            return {
                "status": "skipped",
                "index": index,
                "filename": filename,
                "path": str(out_path),
                "reason": "exists",
                "estimated_cost": 0.0,
                "elapsed_seconds": elapsed,
            }
        atomic_write_bytes(out_path, image_bytes)

        unit_cost = cost_per_image(quality)
        currency = os.getenv("IMAGE_COST_CURRENCY", "USD")
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "combo": combo_dir.relative_to(ROOT).as_posix(),
            "filename": out_path.name,
            "prompt_index": index,
            "prompt_total": total,
            "model": model,
            "size": size,
            "quality": quality,
            "reference_image_count": len(refs),
            "estimated_cost": unit_cost,
            "currency": currency,
            "elapsed_seconds": elapsed,
            "provider_channel_id": os.getenv("MUSEFORGE_PROVIDER_CHANNEL_ID", "legacy"),
            "provider_channel_name": os.getenv(
                "MUSEFORGE_PROVIDER_CHANNEL_NAME", "本地环境兼容渠道"
            ),
            "provider_routing_mode": os.getenv(
                "MUSEFORGE_PROVIDER_ROUTING_MODE", "legacy"
            ),
        }
        run_id = os.getenv("MUSEFORGE_RUN_ID", "").strip()
        if run_id:
            record["run_id"] = run_id
        write_cost_log(record)

        return {
            "status": "saved",
            "index": index,
            "filename": filename,
            "path": str(out_path),
            "model": model,
            "size": size,
            "quality": quality,
            "reference_image_count": len(refs),
            "estimated_cost": unit_cost,
            "currency": currency,
            "elapsed_seconds": elapsed,
            "provider_channel_id": record["provider_channel_id"],
            "provider_channel_name": record["provider_channel_name"],
            "provider_routing_mode": record["provider_routing_mode"],
        }
    finally:
        for handle in handles:
            handle.close()


def run_one_combo(combo_dir: Path, max_workers: int) -> list[dict[str, Any]]:
    prompts_path = combo_dir / "prompts.json"

    prompts = read_prompt_objects(prompts_path)
    if not prompts:
        raise ValueError(f"No prompts found in {prompts_path}")

    refs = source_reference_images(combo_dir)
    if not refs:
        raise ValueError(
            f"No reference images found for product={combo_dir.parent.name} "
            f"accessory={combo_dir.name}"
        )

    if not combo_has_missing_images(combo_dir, prompts):
        print(f"combo skipped complete: {combo_dir.relative_to(ROOT).as_posix()}")
        return [{"status": "skipped", "reason": "combo complete", "estimated_cost": 0.0}]

    workers = max(1, min(max_workers, len(prompts)))
    print(f"combo={combo_dir.relative_to(ROOT).as_posix()}")
    print(f"prompts={len(prompts)} refs={len(refs)} concurrency={workers}")
    print(
        "model={model} size={size} quality={quality} overwrite={overwrite}".format(
            model=os.getenv("IMAGE_MODEL", "gpt-image-2"),
            size=os.getenv("IMAGE_SIZE", "1024x1024"),
            quality=os.getenv("IMAGE_QUALITY", "low"),
            overwrite=os.getenv("IMAGE_OVERWRITE", "false"),
        )
    )

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                request_image,
                combo_dir=combo_dir,
                refs=refs,
                job=job,
                index=index,
                total=len(prompts),
            )
            for index, job in enumerate(prompts)
        ]

        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                result = {"status": "failed", "error": str(exc), "estimated_cost": 0.0}
            results.append(result)
            if result["status"] == "saved":
                print(
                    "saved index={index} cost={cost:.6f} {currency} elapsed={elapsed}s path={path}".format(
                        index=result["index"],
                        cost=result["estimated_cost"],
                        currency=result.get("currency", os.getenv("IMAGE_COST_CURRENCY", "USD")),
                        elapsed=result["elapsed_seconds"],
                        path=result["path"],
                    )
                )
            elif result["status"] == "skipped":
                print(f"skipped index={result.get('index', '-')} reason={result.get('reason')} path={result.get('path', '')}")
            else:
                print(f"failed error={result.get('error')}")

    return results


def run_batches(args: argparse.Namespace) -> list[dict[str, Any]]:
    load_mixed_env(ENV_PATH)
    if args.overwrite:
        os.environ["IMAGE_OVERWRITE"] = "true"

    max_workers = args.concurrency or int(os.getenv("IMAGE_COMBO_CONCURRENCY", "10"))
    max_workers = max(1, max_workers)
    combo_dirs = combo_dirs_from_args(args)

    print(f"combo_count={len(combo_dirs)} global_concurrency_setting={max_workers}")
    all_results: list[dict[str, Any]] = []
    for combo_dir in combo_dirs:
        if args.dry_run:
            prompts = read_prompt_objects(combo_dir / "prompts.json")
            missing = missing_outputs(combo_dir, prompts)
            if missing:
                print(f"would run {combo_dir.relative_to(ROOT).as_posix()} missing={len(missing)}")
                for path in missing:
                    print(f"  - {path.relative_to(ROOT).as_posix()}")
                all_results.append({"status": "dry-run", "missing": len(missing), "estimated_cost": 0.0})
            else:
                print(f"would skip complete {combo_dir.relative_to(ROOT).as_posix()}")
                all_results.append({"status": "skipped", "reason": "combo complete", "estimated_cost": 0.0})
            continue
        all_results.extend(run_one_combo(combo_dir, max_workers))

    saved_count = sum(1 for item in all_results if item["status"] == "saved")
    skipped_count = sum(1 for item in all_results if item["status"] == "skipped")
    failed_count = sum(1 for item in all_results if item["status"] == "failed")
    total_cost = sum(float(item.get("estimated_cost", 0)) for item in all_results)
    currency = os.getenv("IMAGE_COST_CURRENCY", "USD")
    print(f"summary saved={saved_count} skipped={skipped_count} failed={failed_count}")
    print(f"estimated_total_cost={total_cost:.6f} {currency}")
    print(f"cost_log={LOG_PATH}")
    return all_results


def main() -> None:
    args = parse_args()
    run_batches(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
