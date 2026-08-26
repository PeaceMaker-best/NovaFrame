from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path as FileSystemPath
from typing import Any
from typing import Literal
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .database import GenerationQueueFullError, Repository
from .providers import ProviderConfigurationError, ProviderService
from .secrets import SecretCipher
from .workflow import (
    WorkflowConfigurationError,
    WorkflowRunner,
    WorkflowValidationError,
    validate_folder_name,
)
from .workspace import (
    IMAGE_SUFFIXES,
    SHOT_FOLDERS,
    resolve_workspace_asset,
    scan_workspace,
    workspace_asset_summary,
)


CANVAS_DOCUMENT_MAX_BYTES = 2 * 1024 * 1024
WORKSPACE_IMPORT_MAX_BYTES = 12 * 1024 * 1024
GENERATION_REFERENCE_MAX_BYTES = 12 * 1024 * 1024
GENERATION_REFERENCE_MAX_COUNT = 3
CANDIDATE_LIST_MAX_LIMIT = 500
CANDIDATE_JOB_LIST_MAX_LIMIT = 3000
JOB_STATUSES = {"queued", "running", "completed", "failed"}
REVIEW_STATUSES = {"pending", "selected"}


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str | None = Field(default=None, max_length=120)
    tasks: list[str] = Field(default_factory=list, max_length=100)
    shots: list[str] = Field(default_factory=list, max_length=5)
    combinations_only: bool = False
    variants_only: bool = False
    refresh_prompts: bool = False
    overwrite: bool = False
    concurrency: int | None = Field(default=None, ge=1, le=10)
    variants: int = Field(default=1, ge=1, le=6)

    @model_validator(mode="before")
    @classmethod
    def normalize_singular_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        camel_case_fields = {
            "combinationsOnly": "combinations_only",
            "variantsOnly": "variants_only",
            "refreshPrompts": "refresh_prompts",
        }
        for source, target in camel_case_fields.items():
            if source in normalized and target not in normalized:
                normalized[target] = normalized.pop(source)
        if "task" in normalized and "tasks" not in normalized:
            normalized["tasks"] = normalized.pop("task")
        if "shot" in normalized and "shots" not in normalized:
            normalized["shots"] = normalized.pop("shot")
        if isinstance(normalized.get("tasks"), str):
            normalized["tasks"] = [normalized["tasks"]]
        if isinstance(normalized.get("shots"), str):
            normalized["shots"] = [normalized["shots"]]
        return normalized

    @field_validator("product")
    @classmethod
    def validate_product_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return validate_folder_name(value, field="product")
        except WorkflowValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("tasks")
    @classmethod
    def validate_task_names(cls, values: list[str]) -> list[str]:
        try:
            return [
                validate_folder_name(value, field=f"tasks[{index}]")
                for index, value in enumerate(values)
            ]
        except WorkflowValidationError as exc:
            raise ValueError(str(exc)) from exc


class GenerationCreativeBrief(BaseModel):
    """Canvas-authored additions layered on top of the verified task prompt."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(default="", max_length=2000)
    environment: str = Field(default="", max_length=4000)
    composition: str = Field(default="", max_length=4000)
    negatives: str = Field(default="", max_length=4000)
    visible_text: str = Field(default="", max_length=1000)

    @model_validator(mode="before")
    @classmethod
    def normalize_visible_text(cls, value: Any) -> Any:
        if isinstance(value, dict) and "visibleText" in value and "visible_text" not in value:
            normalized = dict(value)
            normalized["visible_text"] = normalized.pop("visibleText")
            return normalized
        return value


class GenerationRunItem(BaseModel):
    """One exact task and shot pair selected for generation."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=120)
    shot: Literal["main", "size", "lifestyle-scene", "detail", "comparison"]

    @field_validator("task")
    @classmethod
    def validate_task(cls, value: str) -> str:
        try:
            return validate_folder_name(value, field="items[].task")
        except WorkflowValidationError as exc:
            raise ValueError(str(exc)) from exc


class GenerationReferenceImage(BaseModel):
    """One browser-submitted image used only by the generation run."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=180)
    data_url: str = Field(min_length=1, max_length=17 * 1024 * 1024)

    @model_validator(mode="before")
    @classmethod
    def normalize_data_url(cls, value: Any) -> Any:
        if isinstance(value, dict) and "dataUrl" in value and "data_url" not in value:
            normalized = dict(value)
            normalized["data_url"] = normalized.pop("dataUrl")
            return normalized
        return value

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if value != value.strip() or FileSystemPath(value).name != value:
            raise ValueError("filename must be one ordinary file name")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("filename contains control characters")
        if FileSystemPath(value).suffix.casefold() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError("filename must use PNG, JPG, or WebP")
        return value


class GenerationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=120)
    tasks: list[str] = Field(min_length=1, max_length=100)
    shots: list[str] = Field(min_length=1, max_length=5)
    items: list[GenerationRunItem] | None = Field(default=None, min_length=1, max_length=500)
    variants: int = Field(default=4, ge=1, le=6)
    concurrency: int = Field(default=4, ge=1, le=10)
    creative_brief: GenerationCreativeBrief | None = None
    reference_images: list[GenerationReferenceImage] = Field(
        default_factory=list,
        max_length=GENERATION_REFERENCE_MAX_COUNT,
    )
    provider_mode: Literal["default", "auto", "fixed"] = "default"
    provider_channel_id: str | None = Field(default=None, max_length=80)
    quality: Literal["low", "medium", "high"] = "low"
    size: Literal["1024x1024"] = "1024x1024"

    @model_validator(mode="before")
    @classmethod
    def normalize_creative_brief(cls, value: Any) -> Any:
        if isinstance(value, dict):
            normalized = dict(value)
            camel_case_fields = {
                "creativeBrief": "creative_brief",
                "referenceImages": "reference_images",
                "providerMode": "provider_mode",
                "providerChannelId": "provider_channel_id",
            }
            for source, target in camel_case_fields.items():
                if source in normalized and target not in normalized:
                    normalized[target] = normalized.pop(source)
            return normalized
        return value

    @field_validator("product")
    @classmethod
    def validate_product(cls, value: str) -> str:
        try:
            return validate_folder_name(value, field="product")
        except WorkflowValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("tasks")
    @classmethod
    def validate_tasks(cls, values: list[str]) -> list[str]:
        try:
            validated = [
                validate_folder_name(value, field=f"tasks[{index}]")
                for index, value in enumerate(values)
            ]
        except WorkflowValidationError as exc:
            raise ValueError(str(exc)) from exc
        if len(set(validated)) != len(validated):
            raise ValueError("tasks cannot contain duplicates")
        return validated

    @field_validator("shots")
    @classmethod
    def validate_shots(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("shots cannot contain duplicates")
        return values

    @model_validator(mode="after")
    def validate_provider_selection(self) -> "GenerationRunRequest":
        if self.provider_mode == "fixed" and not self.provider_channel_id:
            raise ValueError("provider_channel_id is required in fixed mode")
        if self.items:
            pairs = [(item.task, item.shot) for item in self.items]
            if len(set(pairs)) != len(pairs):
                raise ValueError("items cannot contain duplicate task and shot pairs")
            item_tasks = {item.task for item in self.items}
            item_shots = {item.shot for item in self.items}
            if item_tasks != set(self.tasks) or item_shots != set(self.shots):
                raise ValueError(
                    "tasks and shots must exactly summarize the explicit items"
                )
        return self


class ProviderRates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low: float = Field(default=0, ge=0, le=1000000)
    medium: float = Field(default=0, ge=0, le=1000000)
    high: float = Field(default=0, ge=0, le=1000000)


def _validate_provider_base_url(value: str) -> str:
    cleaned = value.strip().rstrip("/")
    parts = urlsplit(cleaned)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("base_url must be an http(s) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("base_url cannot contain credentials, query, or fragment")
    return cleaned


def _validate_provider_endpoint(value: str) -> str:
    cleaned = value.strip()
    if (
        not cleaned.startswith("/")
        or cleaned.startswith("//")
        or "?" in cleaned
        or "#" in cleaned
        or "\x00" in cleaned
        or any(segment in {".", ".."} for segment in cleaned.split("/"))
    ):
        raise ValueError("endpoint must be one absolute URL path")
    return cleaned


class ProviderChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=500)
    endpoint: str = Field(default="/images/edits", min_length=2, max_length=200)
    api_key: str = Field(min_length=1, max_length=4096)
    model: str = Field(default="gpt-image-2", min_length=1, max_length=120)
    active: bool = True
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    rates: ProviderRates = Field(default_factory=ProviderRates)

    @field_validator("name", "model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
            raise ValueError("value cannot be blank")
        return cleaned

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _validate_provider_base_url(value)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return _validate_provider_endpoint(value)

    @field_validator("api_key")
    @classmethod
    def strip_key(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("api_key cannot be blank")
        return cleaned


class ProviderChannelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    endpoint: str | None = Field(default=None, min_length=2, max_length=200)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    active: bool | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    rates: ProviderRates | None = None

    @field_validator("name", "model")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
            raise ValueError("value cannot be blank")
        return cleaned

    @field_validator("base_url")
    @classmethod
    def validate_optional_base_url(cls, value: str | None) -> str | None:
        return _validate_provider_base_url(value) if value is not None else None

    @field_validator("endpoint")
    @classmethod
    def validate_optional_endpoint(cls, value: str | None) -> str | None:
        return _validate_provider_endpoint(value) if value is not None else None

    @field_validator("api_key")
    @classmethod
    def strip_optional_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("api_key cannot be blank")
        return cleaned


class ProviderRoutingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "fixed"]
    fixed_channel_id: str | None = Field(default=None, max_length=80)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def validate_fixed_channel(self) -> "ProviderRoutingUpdate":
        if self.mode == "fixed" and not self.fixed_channel_id:
            raise ValueError("fixed_channel_id is required in fixed mode")
        return self


class CandidateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["selected"]


class WorkspaceAssetImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=120)
    filename: str = Field(min_length=1, max_length=180)
    data_url: str = Field(min_length=1, max_length=17 * 1024 * 1024)

    @model_validator(mode="before")
    @classmethod
    def normalize_data_url(cls, value: Any) -> Any:
        if isinstance(value, dict) and "dataUrl" in value and "data_url" not in value:
            normalized = dict(value)
            normalized["data_url"] = normalized.pop("dataUrl")
            return normalized
        return value

    @field_validator("product")
    @classmethod
    def validate_product(cls, value: str) -> str:
        try:
            return validate_folder_name(value, field="product")
        except WorkflowValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if value != value.strip() or FileSystemPath(value).name != value:
            raise ValueError("filename must be one ordinary file name")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("filename contains control characters")
        if FileSystemPath(value).suffix.casefold() not in IMAGE_SUFFIXES:
            raise ValueError("filename must use a supported image extension")
        return value


def _decode_generation_references(
    images: list[GenerationReferenceImage],
) -> list[tuple[dict[str, Any], bytes]]:
    mime_suffixes = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    decoded: list[tuple[dict[str, Any], bytes]] = []
    for index, image in enumerate(images, start=1):
        header, separator, encoded = image.data_url.partition(",")
        if separator != "," or not header.startswith("data:image/") or ";base64" not in header:
            raise HTTPException(
                status_code=422,
                detail=f"Reference image {index} must be a base64 image data URL",
            )
        mime_type = header[5:].split(";", 1)[0].casefold()
        suffix = mime_suffixes.get(mime_type)
        if suffix is None:
            raise HTTPException(
                status_code=422,
                detail=f"Reference image {index} must be PNG, JPG, or WebP",
            )
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Reference image {index} data is invalid",
            ) from exc
        if not content:
            raise HTTPException(status_code=422, detail=f"Reference image {index} is empty")
        if len(content) > GENERATION_REFERENCE_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Reference image {index} exceeds 12 MiB",
            )
        signatures_match = (
            mime_type == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n")
        ) or (
            mime_type == "image/jpeg" and content.startswith(b"\xff\xd8\xff")
        ) or (
            mime_type == "image/webp"
            and len(content) >= 12
            and content[:4] == b"RIFF"
            and content[8:12] == b"WEBP"
        )
        if not signatures_match:
            raise HTTPException(
                status_code=422,
                detail=f"Reference image {index} content does not match its image type",
            )
        metadata = {
            "display_name": image.filename,
            "filename": f"reference-{index:02d}{suffix}",
            "mime_type": mime_type,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        decoded.append((metadata, content))
    return decoded


def _stage_generation_references(
    settings: Settings,
    *,
    job_id: str,
    references: list[tuple[dict[str, Any], bytes]],
) -> None:
    if not references:
        return
    runs_root = (settings.workspace_root / ".museforge" / "runs").resolve()
    run_dir = (runs_root / job_id).resolve()
    try:
        run_dir.relative_to(runs_root)
    except ValueError as exc:  # pragma: no cover - server-authored ids guard this
        raise RuntimeError("Generation reference directory escaped runs root") from exc
    if run_dir.exists():
        raise RuntimeError("Generation run directory already exists")
    reference_dir = run_dir / "submitted-references"
    reference_dir.mkdir(parents=True, exist_ok=False)
    try:
        for metadata, content in references:
            destination = reference_dir / str(metadata["filename"])
            temporary = destination.with_suffix(destination.suffix + ".part")
            temporary.write_bytes(content)
            os.replace(temporary, destination)
    except Exception:
        import shutil

        shutil.rmtree(run_dir, ignore_errors=True)
        raise


def _cors_origins() -> list[str]:
    raw = os.getenv(
        "MUSEFORGE_CORS_ORIGINS",
        "http://localhost:33020,http://127.0.0.1:33020",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _enforce_generation_candidate_limit(
    settings: Settings,
    *,
    work_item_count: int,
    variants: int,
) -> int:
    expected_candidate_count = work_item_count * variants
    if expected_candidate_count > settings.max_generation_items:
        raise HTTPException(
            status_code=422,
            detail=(
                f"本批次预计生成 {expected_candidate_count} 张，超过服务端上限 "
                f"{settings.max_generation_items} 张"
            ),
        )
    return expected_candidate_count


def _generation_queue_full_error(settings: Settings) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=(
            "本地生成队列已满；请等待当前批次结束后再提交，"
            f"最多允许 {settings.max_active_generation_runs} 个排队或运行批次"
        ),
    )


def _normalize_canvas_document(canvas_id: str, value: dict[str, Any]) -> dict[str, Any]:
    try:
        validate_folder_name(canvas_id, field="canvas id")
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # The browser client sends {"document": {...}} while direct API callers may
    # send the document itself. Persist the same canonical shape for both forms.
    if set(value) == {"document"} and isinstance(value.get("document"), dict):
        document = dict(value["document"])
    else:
        document = dict(value)
    for server_field in ("id", "version", "created_at", "updated_at"):
        document.pop(server_field, None)
    name = document.get("name")
    if name is not None and (not isinstance(name, str) or len(name.strip()) > 160):
        raise HTTPException(status_code=422, detail="Canvas name must be a short string")
    for list_field in ("nodes", "edges"):
        if list_field in document and not isinstance(document[list_field], list):
            raise HTTPException(status_code=422, detail=f"Canvas {list_field} must be a list")
    if "viewport" in document and not isinstance(document["viewport"], dict):
        raise HTTPException(status_code=422, detail="Canvas viewport must be an object")
    document.setdefault("name", canvas_id)
    document.setdefault("nodes", [])
    document.setdefault("edges", [])
    document.setdefault("viewport", {"x": 0, "y": 0, "zoom": 1})
    serialized_size = len(json.dumps(document, ensure_ascii=False).encode("utf-8"))
    if serialized_size > CANVAS_DOCUMENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Canvas document exceeds 2 MiB")
    return document


def _candidate_path(
    settings: Settings,
    candidate: dict[str, Any],
    *,
    require_file: bool,
) -> FileSystemPath:
    relative_path = candidate.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
        raise HTTPException(status_code=404, detail="Candidate image is unavailable")
    relative = FileSystemPath(relative_path)
    if relative.is_absolute():
        raise HTTPException(status_code=403, detail="Candidate path must be relative")
    workspace_root = settings.workspace_root.resolve()
    unresolved_path = workspace_root / relative
    if unresolved_path.is_symlink():
        raise HTTPException(status_code=403, detail="Candidate symlinks are not allowed")
    path = unresolved_path.resolve()
    if candidate.get("storage_status") == "staged":
        storage_anchor = (
            workspace_root / ".museforge" / "runs" / str(candidate["job_id"])
        ).resolve()
        allowed_root = (
            storage_anchor
            / str(candidate["product"])
            / str(candidate["task"])
            / str(candidate["shot"])
        ).resolve()
    elif candidate.get("storage_status") == "promoted":
        folder = SHOT_FOLDERS.get(str(candidate.get("shot")))
        if folder is None:
            raise HTTPException(status_code=403, detail="Candidate has an invalid shot")
        storage_anchor = (workspace_root / "组合").resolve()
        allowed_root = (
            storage_anchor
            / str(candidate["product"])
            / str(candidate["task"])
            / folder
        ).resolve()
    else:
        raise HTTPException(status_code=403, detail="Candidate storage state is invalid")
    try:
        storage_anchor.relative_to(workspace_root)
        allowed_root.relative_to(storage_anchor)
        path.relative_to(allowed_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=403, detail="Candidate path escapes its storage directory"
        ) from exc
    if path.suffix.casefold() not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=403, detail="Candidate is not an allowed image")
    if candidate.get("storage_status") == "staged" and path.stem != (
        f"candidate-{int(candidate['candidate_index']):02d}"
    ):
        raise HTTPException(status_code=403, detail="Candidate filename is invalid")
    if require_file and not path.is_file():
        raise HTTPException(status_code=404, detail="Candidate image is unavailable")
    return path


def _candidate_destination(settings: Settings, candidate: dict[str, Any]) -> FileSystemPath:
    folder = SHOT_FOLDERS.get(str(candidate.get("shot")))
    if folder is None:
        raise HTTPException(status_code=422, detail="Candidate has an invalid shot")
    try:
        product = validate_folder_name(str(candidate["product"]), field="product")
        task = validate_folder_name(str(candidate["task"]), field="task")
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    suffix = FileSystemPath(str(candidate.get("name") or "candidate.png")).suffix.casefold()
    if suffix not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=422, detail="Candidate has an invalid image suffix")
    output_root = (settings.workspace_root / "组合").resolve()
    destination = (
        output_root / product / task / folder / f"{candidate['id']}{suffix}"
    ).resolve()
    try:
        destination.relative_to(output_root)
    except ValueError as exc:  # pragma: no cover - validated folder segments guard this
        raise HTTPException(status_code=403, detail="Promotion path escaped output root") from exc
    return destination


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        instance_lock_path = resolved_settings.database_path.with_name(
            f"{resolved_settings.database_path.name}.instance.lock"
        )
        instance_lock = FileLock(str(instance_lock_path))
        try:
            instance_lock.acquire(timeout=0)
        except Timeout as exc:
            raise RuntimeError(
                "Another NovaFrame API instance is already using this database"
            ) from exc
        generation_executor: ThreadPoolExecutor | None = None
        try:
            repository = Repository(resolved_settings.database_path)
            repository.initialize()
            repository.recover_interrupted_generation_runs()
            secret_cipher = SecretCipher.for_database(
                resolved_settings.database_path
            )
            provider_service = ProviderService(repository, secret_cipher)
            generation_executor = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="novaframe-generation"
            )
            app.state.settings = resolved_settings
            app.state.repository = repository
            app.state.provider_service = provider_service
            app.state.workflow_runner = WorkflowRunner(
                resolved_settings, provider_service=provider_service
            )
            app.state.generation_executor = generation_executor
            yield
        finally:
            # Keep the process/database lock until paid workers are terminal.
            # This makes hot reload and graceful restart single-writer safe.
            if generation_executor is not None:
                generation_executor.shutdown(wait=True, cancel_futures=False)
            instance_lock.release()

    application = FastAPI(
        title="NovaFrame API",
        version="0.1.0",
        description="Local-first product image workstation API",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost"],
        www_redirect=False,
    )

    @application.get("/api/health")
    def health(request: Request) -> dict[str, Any]:
        current: Settings = request.app.state.settings
        return {
            "status": "ok",
            "service": "novaframe-api",
            "version": application.version,
            "workspace_ready": current.workspace_root.is_dir(),
            "workflow_ready": current.workflow_script.is_file(),
            "live_generation_enabled": current.live_generation_enabled,
            "max_generation_items": current.max_generation_items,
            "max_active_generation_runs": current.max_active_generation_runs,
        }

    @application.get("/api/workspace")
    def workspace(request: Request) -> dict[str, Any]:
        current: Settings = request.app.state.settings
        repository: Repository = request.app.state.repository
        summary = scan_workspace(current.workspace_root)
        summary["stats"]["pendingReview"] = repository.list_candidates(
            limit=1, review_status="pending"
        )["total"]
        summary["live_generation_enabled"] = current.live_generation_enabled
        summary["liveGenerationEnabled"] = current.live_generation_enabled
        return summary

    @application.post("/api/workspace/assets/import", status_code=201)
    def import_workspace_asset(
        payload: WorkspaceAssetImport,
        request: Request,
    ) -> dict[str, Any]:
        current: Settings = request.app.state.settings
        product_root = (current.workspace_root / "原始商品图" / payload.product).resolve()
        source_root = (current.workspace_root / "原始商品图").resolve()
        try:
            product_root.relative_to(source_root)
        except ValueError as exc:  # pragma: no cover - product validation guards this
            raise HTTPException(status_code=403, detail="Import target escaped source assets") from exc
        if not product_root.is_dir():
            raise HTTPException(status_code=404, detail="Product workspace not found")

        header, separator, encoded = payload.data_url.partition(",")
        if separator != "," or not header.startswith("data:image/") or ";base64" not in header:
            raise HTTPException(status_code=422, detail="Asset must be a base64 image data URL")
        mime_type = header[5:].split(";", 1)[0].casefold()
        if mime_type not in {
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/gif",
            "image/avif",
        }:
            raise HTTPException(status_code=422, detail="Asset image type is not supported")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(status_code=422, detail="Asset image data is invalid") from exc
        if not content:
            raise HTTPException(status_code=422, detail="Asset image is empty")
        if len(content) > WORKSPACE_IMPORT_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Asset image exceeds 12 MiB")

        import_dir = product_root / "画布导入"
        import_dir.mkdir(parents=True, exist_ok=True)
        destination = import_dir / f"{uuid.uuid4().hex[:12]}-{payload.filename}"
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            temporary.write_bytes(content)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        result = workspace_asset_summary(destination, current.workspace_root)
        result["original_name"] = payload.filename
        return result

    @application.get("/api/workspace/assets/{asset_path:path}")
    def workspace_asset(request: Request, asset_path: str) -> FileResponse:
        current: Settings = request.app.state.settings
        try:
            path = resolve_workspace_asset(current.workspace_root, asset_path)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Workspace image not found") from exc
        return FileResponse(path)

    @application.get("/api/canvases/{canvas_id}")
    def get_canvas(
        request: Request,
        canvas_id: str = Path(min_length=1, max_length=120),
    ) -> dict[str, Any]:
        try:
            validate_folder_name(canvas_id, field="canvas id")
        except WorkflowValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        repository: Repository = request.app.state.repository
        canvas = repository.get_canvas(canvas_id)
        if canvas is None:
            raise HTTPException(status_code=404, detail="Canvas not found")
        return canvas

    @application.put("/api/canvases/{canvas_id}")
    def put_canvas(
        request: Request,
        canvas_id: str = Path(min_length=1, max_length=120),
        document: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        normalized = _normalize_canvas_document(canvas_id, document)
        repository: Repository = request.app.state.repository
        saved = repository.upsert_canvas(canvas_id, normalized)
        saved["ok"] = True
        return saved

    @application.get("/api/jobs")
    def list_jobs(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        status: str | None = Query(default=None),
    ) -> dict[str, Any]:
        if status is not None and status not in JOB_STATUSES:
            raise HTTPException(status_code=422, detail="Unknown job status")
        repository: Repository = request.app.state.repository
        return repository.list_jobs(limit=limit, status=status)

    @application.post("/api/jobs/demo")
    def demo_job(request: Request) -> dict[str, Any]:
        repository: Repository = request.app.state.repository
        return repository.ensure_demo_job()

    def require_live_generation(request: Request) -> Settings:
        current: Settings = request.app.state.settings
        if not current.live_generation_enabled:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "live_generation_disabled",
                    "message": (
                        "实时生图默认关闭；仅在明确设置 "
                        "MUSEFORGE_ENABLE_LIVE_GENERATION=true 后开放"
                    ),
                },
            )
        return current

    @application.get("/api/provider-config")
    def get_provider_config(request: Request) -> dict[str, Any]:
        repository: Repository = request.app.state.repository
        provider_service: ProviderService = request.app.state.provider_service
        channels = provider_service.list_channels()
        routing = repository.get_provider_routing()
        active_channels = [item for item in channels if item["active"]]
        return {
            "channels": channels,
            "routing": routing,
            "summary": {
                "channel_count": len(channels),
                "active_channel_count": len(active_channels),
                "priced_channel_count": sum(
                    1
                    for item in active_channels
                    if item["currency"] == routing["currency"]
                    and any(float(value) > 0 for value in item["rates"].values())
                ),
            },
        }

    @application.post("/api/provider-channels", status_code=201)
    def create_provider_channel(
        payload: ProviderChannelCreate, request: Request
    ) -> dict[str, Any]:
        provider_service: ProviderService = request.app.state.provider_service
        values = payload.model_dump()
        values["rates"] = payload.rates.model_dump()
        try:
            return provider_service.create_channel(values)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="渠道名称已存在") from exc

    @application.patch("/api/provider-channels/{channel_id}")
    def update_provider_channel(
        payload: ProviderChannelUpdate,
        request: Request,
        channel_id: str = Path(min_length=1, max_length=80),
    ) -> dict[str, Any]:
        provider_service: ProviderService = request.app.state.provider_service
        values = payload.model_dump(exclude_unset=True, exclude_none=True)
        if payload.rates is not None:
            values["rates"] = payload.rates.model_dump()
        try:
            channel = provider_service.update_channel(channel_id, values)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="渠道名称已存在") from exc
        if channel is None:
            raise HTTPException(status_code=404, detail="Provider channel not found")
        return channel

    @application.put("/api/provider-routing")
    def update_provider_routing(
        payload: ProviderRoutingUpdate, request: Request
    ) -> dict[str, Any]:
        repository: Repository = request.app.state.repository
        if payload.fixed_channel_id:
            channel = repository.get_provider_channel(payload.fixed_channel_id)
            if channel is None:
                raise HTTPException(status_code=404, detail="固定渠道不存在")
            if not channel["active"]:
                raise HTTPException(status_code=422, detail="固定渠道必须处于启用状态")
        return repository.update_provider_routing(
            mode=payload.mode,
            fixed_channel_id=payload.fixed_channel_id,
            currency=payload.currency,
        )

    @application.post("/api/generation-runs", status_code=202)
    def create_generation_run(
        payload: GenerationRunRequest,
        background_tasks: BackgroundTasks,
        request: Request,
    ) -> dict[str, Any]:
        require_live_generation(request)
        runner: WorkflowRunner = request.app.state.workflow_runner
        repository: Repository = request.app.state.repository
        provider_service: ProviderService = request.app.state.provider_service
        decoded_references = _decode_generation_references(payload.reference_images)
        normalized = payload.model_dump(exclude={"reference_images"})
        normalized["submitted_references"] = [
            metadata for metadata, _content in decoded_references
        ]
        exact_item_count = (
            len(payload.items)
            if payload.items
            else len(payload.tasks) * len(payload.shots)
        )
        current: Settings = request.app.state.settings
        _enforce_generation_candidate_limit(
            current,
            work_item_count=exact_item_count,
            variants=payload.variants,
        )
        if (
            repository.count_active_generation_runs()
            >= current.max_active_generation_runs
        ):
            raise _generation_queue_full_error(current)
        try:
            command = runner.build_command("generate", normalized)
            normalized["input_snapshot"] = runner.capture_generation_inputs(normalized)
        except WorkflowValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except WorkflowConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        try:
            provider_snapshot = provider_service.resolve_for_run(
                mode=payload.provider_mode,
                channel_id=payload.provider_channel_id,
                quality=payload.quality,
                size=payload.size,
            )
        except ProviderConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        normalized["provider"] = provider_service.public_snapshot(provider_snapshot)
        try:
            run = repository.create_generation_run(
                request=normalized,
                command=command,
                provider_snapshot=provider_snapshot,
                max_active_runs=current.max_active_generation_runs,
            )
        except GenerationQueueFullError as exc:
            raise _generation_queue_full_error(current) from exc
        try:
            _stage_generation_references(
                current,
                job_id=run["id"],
                references=decoded_references,
            )
        except (OSError, RuntimeError) as exc:
            repository.fail_unfinished_items(run["id"], str(exc))
            repository.finish_job(
                run["id"],
                status="failed",
                message="无法保存本次参考图",
                stderr=str(exc),
            )
            raise HTTPException(status_code=500, detail="无法保存本次参考图") from exc
        executor: ThreadPoolExecutor = request.app.state.generation_executor

        def submit() -> None:
            try:
                executor.submit(
                    runner.execute_generation_run,
                    job_id=run["id"],
                    request=normalized,
                    command=command,
                    repository=repository,
                )
            except RuntimeError as exc:
                repository.fail_unfinished_items(run["id"], str(exc))
                repository.finish_job(
                    run["id"],
                    status="failed",
                    message=f"无法调度候选生成：{exc}",
                    stderr=str(exc),
                )

        background_tasks.add_task(submit)
        return run

    @application.get("/api/generation-runs")
    def list_generation_runs(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        status: str | None = Query(default=None),
    ) -> dict[str, Any]:
        if status is not None and status not in JOB_STATUSES:
            raise HTTPException(status_code=422, detail="Unknown generation run status")
        repository: Repository = request.app.state.repository
        return repository.list_generation_runs(
            limit=limit,
            offset=offset,
            status=status,
        )

    @application.get("/api/generation-runs/{job_id}")
    def get_generation_run(
        request: Request,
        job_id: str = Path(min_length=1, max_length=120),
        events_after: int = Query(default=0, ge=0),
        event_limit: int = Query(default=500, ge=1, le=2000),
    ) -> dict[str, Any]:
        repository: Repository = request.app.state.repository
        run = repository.get_generation_run(job_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Generation run not found")
        run["events"] = repository.list_events(
            job_id, after=events_after, limit=event_limit
        )
        return run

    @application.get("/api/candidates")
    def list_candidates(
        request: Request,
        job_id: str | None = Query(default=None, max_length=120),
        review_status: str | None = Query(default=None),
        limit: int = Query(
            default=200,
            ge=1,
            le=CANDIDATE_JOB_LIST_MAX_LIMIT,
            description=(
                "Maximum 500 candidates globally, or 3000 when job_id is provided"
            ),
        ),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        if review_status is not None and review_status not in REVIEW_STATUSES:
            raise HTTPException(status_code=422, detail="Unknown candidate review status")
        if job_id is None and limit > CANDIDATE_LIST_MAX_LIMIT:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Candidate limit cannot exceed {CANDIDATE_LIST_MAX_LIMIT} "
                    "without a job_id filter"
                ),
            )
        repository: Repository = request.app.state.repository
        if job_id is not None and repository.get_generation_run(job_id) is None:
            raise HTTPException(status_code=404, detail="Generation run not found")
        return repository.list_candidates(
            limit=limit,
            offset=offset,
            job_id=job_id,
            review_status=review_status,
        )

    @application.get("/api/candidates/{candidate_id}/image")
    def candidate_image(
        request: Request,
        candidate_id: str = Path(min_length=1, max_length=120),
    ) -> FileResponse:
        repository: Repository = request.app.state.repository
        candidate = repository.get_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        current: Settings = request.app.state.settings
        path = _candidate_path(current, candidate, require_file=True)
        return FileResponse(
            path,
            media_type=candidate.get("mime_type") or None,
            headers={"Cache-Control": "private, no-store"},
        )

    @application.patch("/api/candidates/{candidate_id}")
    def review_candidate(
        payload: CandidateDecision,
        request: Request,
        candidate_id: str = Path(min_length=1, max_length=120),
    ) -> dict[str, Any]:
        repository: Repository = request.app.state.repository
        candidate = repository.get_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if payload.decision != "selected":  # pragma: no cover - Pydantic enforces this
            raise HTTPException(status_code=422, detail="Unsupported candidate decision")
        if candidate["review_status"] == "selected":
            _candidate_path(request.app.state.settings, candidate, require_file=True)
            return candidate

        current: Settings = request.app.state.settings
        source = _candidate_path(current, candidate, require_file=False)
        destination = _candidate_destination(current, candidate)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            if source.is_symlink():
                raise HTTPException(status_code=403, detail="Candidate symlinks are not allowed")
            if destination.exists():
                raise HTTPException(status_code=409, detail="Candidate destination already exists")
            try:
                os.replace(source, destination)
            except OSError as exc:
                raise HTTPException(
                    status_code=500, detail=f"Could not atomically promote candidate: {exc}"
                ) from exc
        elif not destination.is_file():
            raise HTTPException(status_code=404, detail="Candidate image is unavailable")
        relative_path = destination.relative_to(current.workspace_root.resolve()).as_posix()
        selected = repository.select_candidate(candidate_id, relative_path)
        if selected is None:  # pragma: no cover - row was read directly above
            raise HTTPException(status_code=404, detail="Candidate not found")
        repository.add_event(
            candidate["job_id"],
            "candidate.selected",
            {"candidate_id": candidate_id, "relative_path": relative_path},
            item_id=candidate_id,
        )
        return selected

    @application.delete("/api/candidates/{candidate_id}", status_code=204)
    def delete_candidate(
        request: Request,
        candidate_id: str = Path(min_length=1, max_length=120),
    ) -> Response:
        repository: Repository = request.app.state.repository
        candidate = repository.get_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        path = _candidate_path(request.app.state.settings, candidate, require_file=False)
        if path.exists():
            if not path.is_file() or path.is_symlink():
                raise HTTPException(status_code=403, detail="Candidate is not a regular file")
            path.unlink()
        repository.add_event(
            candidate["job_id"],
            "candidate.deleted",
            {"candidate_id": candidate_id},
            item_id=candidate_id,
        )
        if not repository.delete_candidate(candidate_id):
            raise HTTPException(status_code=404, detail="Candidate not found")
        return Response(status_code=204)

    def run_workflow(
        action: str,
        payload: WorkflowRequest,
        request: Request,
    ) -> dict[str, Any]:
        runner: WorkflowRunner = request.app.state.workflow_runner
        repository: Repository = request.app.state.repository
        try:
            result = runner.execute(
                action=action,
                request=payload.model_dump(),
                repository=repository,
            )
        except WorkflowValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except WorkflowConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if result["status"] == "failed":
            detail = result["message"]
            if result.get("stderr"):
                detail = f"{detail}：{result['stderr'].strip().splitlines()[-1]}"
            raise HTTPException(status_code=502, detail=detail)
        return result

    @application.post("/api/workflow/prepare")
    def prepare_workflow(
        payload: WorkflowRequest,
        request: Request,
    ) -> dict[str, Any]:
        return run_workflow("prepare", payload, request)

    @application.post("/api/workflow/preview")
    def preview_workflow(
        payload: WorkflowRequest,
        request: Request,
    ) -> dict[str, Any]:
        return run_workflow("preview", payload, request)

    @application.post("/api/workflow/generate")
    def generate_workflow(
        payload: WorkflowRequest,
        request: Request,
    ) -> dict[str, Any]:
        require_live_generation(request)
        raise HTTPException(
            status_code=410,
            detail=(
                "旧版直写生成入口已停用；请使用 /api/generation-runs，"
                "以保留精确计划、输入快照、候选暂存和审核边界"
            ),
        )

    return application


app = create_app()
