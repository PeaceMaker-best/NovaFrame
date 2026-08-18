from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


def find_workspace_root() -> Path:
    configured = os.getenv("MUSEFORGE_WORKSPACE_ROOT", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if (candidate / "原始商品图").is_dir() and (candidate / "配件超市").is_dir():
            return candidate
        raise RuntimeError(
            f"MUSEFORGE_WORKSPACE_ROOT does not contain 原始商品图 and 配件超市: {candidate}"
        )
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "原始商品图").is_dir() and (candidate / "配件超市").is_dir():
            return candidate
        nested = candidate / "workspace"
        if (nested / "原始商品图").is_dir() and (nested / "配件超市").is_dir():
            return nested
    raise RuntimeError("Could not locate workspace containing 原始商品图 and 配件超市")


ROOT = find_workspace_root()
LOG_PATH = ROOT / ".tmp" / "image2_cost_log.jsonl"


def find_env_path() -> Path:
    configured = os.getenv("MUSEFORGE_ENV_FILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    for candidate in (ROOT / ".env", ROOT.parent / ".env"):
        if candidate.is_file():
            return candidate
    return ROOT / ".env"


ENV_PATH = find_env_path()


def load_mixed_env(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("[") or line in {"{", "}"}:
            continue

        if ":" in line and "=" not in line:
            key, value = line.split(":", 1)
        elif "=" in line:
            key, value = line.split("=", 1)
        else:
            continue

        key = key.strip().strip('"').strip("'")
        value = value.strip().rstrip(",").strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def clean_base_url(value: str) -> str:
    base = value.strip().strip('"').strip("'")
    if not base:
        raise ValueError("Missing IMAGE_API_BASE_URL or base_url in .env")
    return base.rstrip("/") + "/"


def read_prompt_objects(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    items: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        obj, end = decoder.raw_decode(text, index)
        if not isinstance(obj, dict):
            raise ValueError(f"Expected JSON object in {path}, got {type(obj).__name__}")
        items.append(obj)
        index = end
    return items


def pick_output_folder(filename: str) -> str:
    mapping = {
        "lifestyle-scene": "场景图",
        "comparison": "对比图",
        "detail": "细节图",
        "size": "尺寸图",
        "main": "主图",
    }
    for suffix, folder in mapping.items():
        if filename.endswith(suffix):
            return folder
    return "主图"


def reference_images(ref_dir: Path) -> list[Path]:
    allowed = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted(p for p in ref_dir.iterdir() if p.is_file() and p.suffix.lower() in allowed)


def build_url() -> str:
    base = os.getenv("IMAGE_API_BASE_URL") or os.getenv("base_url") or ""
    endpoint = os.getenv("IMAGE_API_ENDPOINT", "/images/edits").strip()
    return urljoin(clean_base_url(base), endpoint.lstrip("/"))


def auth_headers() -> dict[str, str]:
    api_key = (
        os.getenv("IMAGE_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("api_key")
        or os.getenv("API_KEY")
    )
    requires_auth = parse_bool(os.getenv("requires_openai_auth"))
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key.strip().strip(chr(34)).strip(chr(39))}"
    elif requires_auth:
        raise ValueError("requires_openai_auth is true, but no IMAGE_API_KEY / OPENAI_API_KEY / api_key is set")
    return headers


def cost_per_image(quality: str) -> float:
    key = f"IMAGE_COST_PER_IMAGE_{quality.upper()}_USD"
    raw = (
        os.getenv("MUSEFORGE_PROVIDER_UNIT_PRICE")
        or os.getenv(key)
        or os.getenv("IMAGE_COST_PER_IMAGE_USD")
        or "0"
    )
    try:
        return float(str(raw).strip().strip('"').strip("'"))
    except ValueError:
        return 0.0


def decode_image_response(response: requests.Response) -> bytes:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("image/"):
        return response.content

    payload = response.json()
    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            if first.get("b64_json"):
                return base64.b64decode(first["b64_json"])
            if first.get("url"):
                image_response = requests.get(first["url"], timeout=180)
                image_response.raise_for_status()
                return image_response.content

    if payload.get("b64_json"):
        return base64.b64decode(payload["b64_json"])

    raise ValueError(f"Could not find image bytes in response keys: {sorted(payload.keys())}")


def write_cost_log(record: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_test() -> Path:
    load_mixed_env(ENV_PATH)

    combo_rel = os.getenv("IMAGE_TEST_COMBO", "组合/ZJJMT009/25-25毛巾")
    combo_dir = (ROOT / combo_rel).resolve()
    prompts_path = combo_dir / "prompts.json"
    ref_dir = combo_dir / "参考图"

    prompts = read_prompt_objects(prompts_path)
    if not prompts:
        raise ValueError(f"No prompts found in {prompts_path}")

    prompt_index = int(os.getenv("IMAGE_TEST_PROMPT_INDEX", "0"))
    job = prompts[prompt_index]
    filename = str(job.get("filename") or f"image2-test-{prompt_index}")

    refs = reference_images(ref_dir)
    if not refs:
        raise ValueError(f"No reference images found in {ref_dir}")

    model = os.getenv("IMAGE_MODEL", "gpt-image-2")
    size = os.getenv("IMAGE_SIZE", "1024x1024")
    quality = os.getenv("IMAGE_QUALITY", "low")
    output_format = os.getenv("IMAGE_OUTPUT_FORMAT", "png")
    file_field = os.getenv("IMAGE_FILE_FIELD", "image[]")

    prompt_text = json.dumps(job, ensure_ascii=False, indent=2)
    url = build_url()

    files = []
    handles = []
    try:
        for ref in refs:
            handle = ref.open("rb")
            handles.append(handle)
            mime = mimetypes.guess_type(ref.name)[0] or "application/octet-stream"
            files.append((file_field, (ref.name, handle, mime)))

        data = {
            "model": model,
            "prompt": prompt_text,
            "size": size,
            "quality": quality,
            "output_format": output_format,
        }

        start = time.perf_counter()
        response = requests.post(url, headers=auth_headers(), data=data, files=files, timeout=600)
        elapsed = round(time.perf_counter() - start, 2)

        if not response.ok and file_field == "image[]":
            for handle in handles:
                handle.seek(0)
            files_retry = []
            for ref, handle in zip(refs, handles):
                mime = mimetypes.guess_type(ref.name)[0] or "application/octet-stream"
                files_retry.append(("image", (ref.name, handle, mime)))
            response = requests.post(url, headers=auth_headers(), data=data, files=files_retry, timeout=600)

        if not response.ok:
            body = response.text[:1200].replace("\n", " ")
            raise RuntimeError(f"Image API failed: HTTP {response.status_code}; {body}")

        image_bytes = decode_image_response(response)
        out_dir = combo_dir / pick_output_folder(filename)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{filename}-api-test.{output_format}"
        out_path.write_bytes(image_bytes)

        unit_cost = cost_per_image(quality)
        currency = os.getenv("IMAGE_COST_CURRENCY", "USD")
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "combo": str(combo_dir.relative_to(ROOT)),
            "filename": out_path.name,
            "model": model,
            "size": size,
            "quality": quality,
            "reference_image_count": len(refs),
            "estimated_cost": unit_cost,
            "currency": currency,
            "elapsed_seconds": elapsed,
        }
        write_cost_log(record)

        print(f"saved={out_path}")
        print(f"refs={len(refs)} model={model} size={size} quality={quality}")
        print(f"estimated_cost={unit_cost:.6f} {currency} (from .env)")
        print(f"cost_log={LOG_PATH}")
        return out_path
    finally:
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    try:
        run_test()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
