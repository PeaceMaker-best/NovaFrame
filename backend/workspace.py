from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
DOCUMENT_SUFFIXES = {".txt", ".md", ".json", ".docx", ".pdf"}
SHOT_FOLDERS = {
    "main": "主图",
    "size": "尺寸图",
    "lifestyle-scene": "场景图",
    "detail": "细节图",
    "comparison": "对比图",
}


def _is_file_with_suffix(path: Path, suffixes: set[str]) -> bool:
    return path.is_file() and path.suffix.casefold() in suffixes


def _safe_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    root = folder.resolve()
    result: list[Path] = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        result.append(path)
    return sorted(result, key=lambda item: item.as_posix().casefold())


def _modified_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(
        timespec="seconds"
    )


def _asset(path: Path, workspace_root: Path) -> dict[str, Any]:
    relative_path = path.resolve().relative_to(workspace_root.resolve()).as_posix()
    return {
        "name": path.name,
        "relative_path": relative_path,
        "url": f"/api/workspace/assets/{quote(relative_path, safe='/')}",
        "size_bytes": path.stat().st_size,
        "modified_at": _modified_at(path),
    }


def workspace_asset_summary(path: Path, workspace_root: Path) -> dict[str, Any]:
    """Return the stable API representation for a validated workspace image."""
    return _asset(path, workspace_root)


def _folder_summary(folder: Path, workspace_root: Path) -> dict[str, Any]:
    files = _safe_files(folder)
    images = [path for path in files if _is_file_with_suffix(path, IMAGE_SUFFIXES)]
    documents = [path for path in files if _is_file_with_suffix(path, DOCUMENT_SUFFIXES)]
    preferred = [
        path
        for path in images
        if any(token in path.stem.casefold() for token in ("主商品", "主图", "main", "hero"))
    ]
    cover = (preferred or images or [None])[0]
    latest = max((path.stat().st_mtime for path in files), default=folder.stat().st_mtime)
    return {
        "id": folder.name,
        "name": folder.name,
        "relative_path": folder.resolve().relative_to(workspace_root.resolve()).as_posix(),
        "image_count": len(images),
        "document_count": len(documents),
        "file_count": len(files),
        "cover_image": _asset(cover, workspace_root) if cover else None,
        "images": [_asset(path, workspace_root) for path in images[:24]],
        "updated_at": datetime.fromtimestamp(latest, tz=timezone.utc).isoformat(
            timespec="seconds"
        ),
    }


def _prompt_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 0
    if isinstance(data, list):
        return sum(isinstance(item, dict) for item in data)
    return int(isinstance(data, dict))


def _prepared_prompt_shots(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [
        shot
        for shot in SHOT_FOLDERS
        if sum(
            isinstance(item, dict)
            and str(item.get("filename") or "").endswith(shot)
            for item in data
        )
        == 1
    ]


def _task_summary(
    product_name: str,
    task_dir: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    shot_summaries: dict[str, Any] = {}
    generated_count = 0
    for shot, folder_name in SHOT_FOLDERS.items():
        images = [
            path
            for path in _safe_files(task_dir / folder_name)
            if _is_file_with_suffix(path, IMAGE_SUFFIXES)
        ]
        generated_count += len(images)
        shot_summaries[shot] = {
            "folder": folder_name,
            "image_count": len(images),
            "images": [_asset(path, workspace_root) for path in images[:8]],
        }
    references = [
        path
        for path in _safe_files(task_dir / "参考图")
        if _is_file_with_suffix(path, IMAGE_SUFFIXES)
    ]
    prompt_path = task_dir / "prompts.json"
    return {
        "id": f"{product_name}/{task_dir.name}",
        "name": task_dir.name,
        "product": product_name,
        "relative_path": task_dir.resolve()
        .relative_to(workspace_root.resolve())
        .as_posix(),
        "kind": "standalone" if task_dir.name == "单品" or task_dir.name.startswith("单品-") else "combination",
        "has_prompts": prompt_path.is_file(),
        "prompt_count": _prompt_count(prompt_path),
        "prepared_shots": _prepared_prompt_shots(prompt_path),
        "reference_count": len(references),
        "references": [_asset(path, workspace_root) for path in references[:5]],
        "has_reference_manifest": (task_dir / "reference_manifest.json").is_file(),
        "generated_image_count": generated_count,
        "shots": shot_summaries,
    }


def _combination_summary(output_root: Path, workspace_root: Path) -> list[dict[str, Any]]:
    if not output_root.is_dir():
        return []
    products: list[dict[str, Any]] = []
    for product_dir in sorted(
        (path for path in output_root.iterdir() if path.is_dir() and not path.name.startswith("_")),
        key=lambda item: item.name.casefold(),
    ):
        tasks = [
            _task_summary(product_dir.name, task_dir, workspace_root)
            for task_dir in sorted(
                (path for path in product_dir.iterdir() if path.is_dir()),
                key=lambda item: item.name.casefold(),
            )
        ]
        products.append(
            {
                "id": product_dir.name,
                "name": product_dir.name,
                "relative_path": product_dir.resolve()
                .relative_to(workspace_root.resolve())
                .as_posix(),
                "task_count": len(tasks),
                "prompt_count": sum(task["prompt_count"] for task in tasks),
                "generated_image_count": sum(
                    task["generated_image_count"] for task in tasks
                ),
                "tasks": tasks,
            }
        )
    return products


def scan_workspace(workspace_root: Path) -> dict[str, Any]:
    workspace_root = workspace_root.resolve()
    source_root = workspace_root / "原始商品图"
    accessory_root = workspace_root / "配件超市"
    output_root = workspace_root / "组合"
    warnings: list[str] = []
    for path in (source_root, accessory_root, output_root):
        if not path.is_dir():
            warnings.append(f"目录尚不存在：{path.name}")

    products = (
        [
            _folder_summary(path, workspace_root)
            for path in sorted(
                (item for item in source_root.iterdir() if item.is_dir()),
                key=lambda item: item.name.casefold(),
            )
        ]
        if source_root.is_dir()
        else []
    )
    accessories = (
        [
            _folder_summary(path, workspace_root)
            for path in sorted(
                (item for item in accessory_root.iterdir() if item.is_dir()),
                key=lambda item: item.name.casefold(),
            )
        ]
        if accessory_root.is_dir()
        else []
    )
    combinations = _combination_summary(output_root, workspace_root)
    combinations_by_product = {item["id"]: item for item in combinations}
    for product in products:
        output = combinations_by_product.get(product["id"], {})
        task_count = int(output.get("task_count", 0))
        prompt_count = int(output.get("prompt_count", 0))
        output_count = int(output.get("generated_image_count", 0))
        if product["image_count"] == 0:
            readiness = "blocked"
        elif task_count == 0:
            readiness = "draft"
        elif prompt_count < task_count * len(SHOT_FOLDERS):
            readiness = "stale"
        else:
            readiness = "ready"
        product.update(
            {
                # Camel-case presentation fields are kept alongside the detailed
                # scanner fields so the workstation UI can render without a
                # second transformation pass.
                "assetCount": product["image_count"] + product["document_count"],
                "taskCount": task_count,
                "promptCount": prompt_count,
                "outputCount": output_count,
                "readiness": readiness,
                "thumbnail": (
                    product["cover_image"]["url"]
                    if product["cover_image"] is not None
                    else None
                ),
                "updatedAt": product["updated_at"],
            }
        )
    for accessory in accessories:
        accessory["assetCount"] = (
            accessory["image_count"] + accessory["document_count"]
        )
    task_count = sum(item["task_count"] for item in combinations)
    prompt_count = sum(item["prompt_count"] for item in combinations)
    generated_count = sum(item["generated_image_count"] for item in combinations)
    return {
        "root": str(workspace_root),
        "directories": {
            "products": "原始商品图",
            "accessories": "配件超市",
            "outputs": "组合",
        },
        "counts": {
            "products": len(products),
            "accessories": len(accessories),
            "combination_products": len(combinations),
            "tasks": task_count,
            "generated_images": generated_count,
        },
        "stats": {
            "products": len(products),
            "accessories": len(accessories),
            "tasks": task_count,
            "prompts": prompt_count,
            "outputs": generated_count,
            "pendingReview": generated_count,
        },
        "products": products,
        "accessories": accessories,
        "combinations": combinations,
        "warnings": warnings,
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def resolve_workspace_asset(workspace_root: Path, relative_path: str) -> Path:
    if not relative_path or "\x00" in relative_path:
        raise ValueError("Invalid asset path")
    workspace_root = workspace_root.resolve()
    candidate = (workspace_root / relative_path).resolve()
    try:
        first_segment = candidate.relative_to(workspace_root).parts[0]
    except (ValueError, IndexError) as exc:
        raise ValueError("Asset path escapes the workspace") from exc
    if first_segment not in {"原始商品图", "配件超市", "组合"}:
        raise ValueError("Asset path is outside a media directory")
    if not candidate.is_file() or candidate.suffix.casefold() not in IMAGE_SUFFIXES:
        raise FileNotFoundError(relative_path)
    return candidate
