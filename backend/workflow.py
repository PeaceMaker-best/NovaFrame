from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .config import Settings
from .database import Repository
from .providers import ProviderService


ALLOWED_ACTIONS = {"prepare", "preview", "generate"}
ALLOWED_SHOTS = {"main", "size", "lifestyle-scene", "detail", "comparison"}
GENERATION_EVENT_PREFIX = "MUSEFORGE_EVENT "
GENERATION_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
REFERENCE_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
RUN_SPEC_SCHEMA = "museforge.run-spec"
RUN_SPEC_VERSION = 2
RUN_SPEC_MAX_BYTES = 256 * 1024
MAX_GENERATION_INPUT_FILES = 500
MAX_GENERATION_INPUT_FILE_BYTES = 64 * 1024 * 1024
MAX_GENERATION_INPUT_TOTAL_BYTES = 512 * 1024 * 1024
MAX_GENERATION_SNAPSHOT_JSON_BYTES = 192 * 1024
MAX_SUBMITTED_REFERENCE_FILES = 3
MAX_SUBMITTED_REFERENCE_FILE_BYTES = 12 * 1024 * 1024
CREATIVE_BRIEF_POSITIVE_BLOCKLIST = (
    r"\b(?:wireless|bluetooth|fcc|phone\s+control)\b",
    r"\b(?:power\s+adapter|adapter|high\s+voltage)\b",
    r"\b(?:outdoor|water\s*proof|waterproof)\b",
    r"\b(?:eco-friendly|environmental\s+friendly|environment\s+protection)\b",
    r"\b\d+(?:\.\d+)?\s*v\b",
    r"(?:无线|蓝牙|感应|防水|雷雨|夜晚|夜间|户外|环保|认证|证书|资质报告)",
)


class WorkflowValidationError(ValueError):
    pass


class WorkflowConfigurationError(RuntimeError):
    pass


def validate_folder_name(value: str, *, field: str) -> str:
    """Accept exactly one ordinary path segment and reject traversal/control input."""
    if not isinstance(value, str):
        raise WorkflowValidationError(f"{field} must be a string")
    if value != value.strip() or not value:
        raise WorkflowValidationError(f"{field} cannot be empty or padded with whitespace")
    if len(value) > 120:
        raise WorkflowValidationError(f"{field} is too long")
    if value in {".", ".."} or value.startswith("."):
        raise WorkflowValidationError(f"{field} cannot be a hidden or traversal segment")
    if "/" in value or "\\" in value or "\x00" in value:
        raise WorkflowValidationError(f"{field} must be one folder name, not a path")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise WorkflowValidationError(f"{field} contains control characters")
    if Path(value).is_absolute() or Path(value).name != value:
        raise WorkflowValidationError(f"{field} must be one relative folder name")
    return value


def validate_shot(value: str) -> str:
    if value not in ALLOWED_SHOTS:
        choices = ", ".join(sorted(ALLOWED_SHOTS))
        raise WorkflowValidationError(f"shot must be one of: {choices}")
    return value


def validate_creative_brief(value: Any) -> None:
    if value in (None, {}):
        return
    if not isinstance(value, dict):
        raise WorkflowValidationError("creative_brief must be an object")
    visible_text = str(value.get("visible_text") or "")
    if re.search(r"[\u3400-\u9fff]", visible_text):
        raise WorkflowValidationError(
            "creative_brief.visible_text must use marketplace-ready English copy"
        )
    for field in ("subject", "environment", "composition", "visible_text"):
        text = value.get(field)
        if text is None:
            continue
        if not isinstance(text, str):
            raise WorkflowValidationError(f"creative_brief.{field} must be a string")
        if any(
            re.search(pattern, text, flags=re.IGNORECASE)
            for pattern in CREATIVE_BRIEF_POSITIVE_BLOCKLIST
        ):
            raise WorkflowValidationError(
                f"creative_brief.{field} contains an unsupported positive claim"
            )


class WorkflowRunner:
    """Safe adapter around the deterministic product image workflow script."""

    def __init__(
        self, settings: Settings, *, provider_service: ProviderService | None = None
    ):
        self.settings = settings
        self.provider_service = provider_service

    def _validate_product_exists(self, product: str | None) -> None:
        if product is None:
            return
        product_root = (self.settings.workspace_root / "原始商品图").resolve()
        candidate = (product_root / product).resolve()
        try:
            candidate.relative_to(product_root)
        except ValueError as exc:  # pragma: no cover - validate_folder_name already blocks this
            raise WorkflowValidationError("product escapes 原始商品图") from exc
        if not candidate.is_dir():
            raise WorkflowValidationError(f"product does not exist: {product}")

    def _validate_generation_tasks(self, product: str, tasks: list[str]) -> None:
        accessory_root = (self.settings.workspace_root / "配件超市").resolve()
        product_output = (self.settings.workspace_root / "组合" / product).resolve()
        for task in tasks:
            if task == "单品":
                continue
            accessory = (accessory_root / task).resolve()
            prepared_variant = (product_output / task).resolve()
            try:
                accessory.relative_to(accessory_root)
                prepared_variant.relative_to(product_output)
            except ValueError as exc:  # pragma: no cover - folder validation guards this
                raise WorkflowValidationError("task escapes its workspace directory") from exc
            if accessory.is_dir():
                continue
            if task.startswith("单品-") and (
                prepared_variant.is_dir()
                and (prepared_variant / "prompts.json").is_file()
            ):
                continue
            raise WorkflowValidationError(f"generation task does not exist: {task}")

    def build_command(self, action: str, request: dict[str, Any]) -> list[str]:
        if action not in ALLOWED_ACTIONS:
            raise WorkflowValidationError(f"Unsupported workflow action: {action}")
        if not self.settings.workflow_script.is_file():
            raise WorkflowConfigurationError(
                f"Workflow script not found: {self.settings.workflow_script}"
            )

        product = request.get("product")
        if product is not None:
            product = validate_folder_name(product, field="product")
        tasks = request.get("tasks") or []
        shots = request.get("shots") or []
        if not isinstance(tasks, list) or not isinstance(shots, list):
            raise WorkflowValidationError("tasks and shots must be lists")
        validated_tasks = [
            validate_folder_name(task, field=f"tasks[{index}]")
            for index, task in enumerate(tasks)
        ]
        validated_shots = [validate_shot(shot) for shot in shots]
        if len(set(validated_tasks)) != len(validated_tasks):
            raise WorkflowValidationError("tasks cannot contain duplicates")
        if len(set(validated_shots)) != len(validated_shots):
            raise WorkflowValidationError("shots cannot contain duplicates")
        self._validate_product_exists(product)

        combinations_only = bool(request.get("combinations_only"))
        variants_only = bool(request.get("variants_only"))
        if combinations_only and variants_only:
            raise WorkflowValidationError(
                "combinations_only and variants_only are mutually exclusive"
            )
        if combinations_only and "单品" in validated_tasks:
            raise WorkflowValidationError(
                "combinations_only cannot be combined with task 单品"
            )
        if action == "generate":
            if product is None:
                raise WorkflowValidationError(
                    "live generation requires one explicit product"
                )
            if not validated_tasks:
                raise WorkflowValidationError(
                    "live generation requires at least one explicit task"
                )
            if not validated_shots:
                raise WorkflowValidationError(
                    "live generation requires at least one explicit shot"
                )
            self._validate_generation_tasks(product, validated_tasks)
            validate_creative_brief(request.get("creative_brief"))

        # The only executable is the reviewed workflow script. In particular, this
        # adapter never invokes image2_combo_batch.py or accepts a user-supplied path.
        command = [sys.executable, str(self.settings.workflow_script), action]
        if product:
            command.extend(["--product", product])
        for task in validated_tasks:
            command.extend(["--task", task])
        if action in {"preview", "generate"}:
            for shot in validated_shots:
                command.extend(["--shot", shot])
        if combinations_only:
            command.append("--combinations-only")
        if variants_only:
            command.append("--variants-only")
        if request.get("refresh_prompts"):
            command.append("--refresh-prompts")
        if action == "generate" and request.get("overwrite"):
            command.append("--overwrite")
        concurrency = request.get("concurrency")
        if action == "generate":
            concurrency = 1 if concurrency is None else concurrency
            try:
                numeric_concurrency = int(concurrency)
            except (TypeError, ValueError) as exc:
                raise WorkflowValidationError("concurrency must be an integer") from exc
            if not 1 <= numeric_concurrency <= 10:
                raise WorkflowValidationError("concurrency must be between 1 and 10")
            command.extend(["--concurrency", str(numeric_concurrency)])
        return command

    def execute(
        self,
        *,
        action: str,
        request: dict[str, Any],
        repository: Repository,
    ) -> dict[str, Any]:
        command = self.build_command(action, request)
        job = repository.create_job(action=action, request=request, command=command)
        repository.mark_job_running(job["id"])
        try:
            process_env = os.environ.copy()
            process_env["MUSEFORGE_WORKSPACE_ROOT"] = str(
                self.settings.workspace_root.resolve()
            )
            completed = subprocess.run(
                command,
                cwd=self.settings.workspace_root,
                env=process_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.workflow_timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return repository.finish_job(
                job["id"],
                status="failed",
                message=f"工作流超过 {self.settings.workflow_timeout_seconds} 秒，已停止等待",
                stdout=stdout,
                stderr=stderr,
            )
        except OSError as exc:
            return repository.finish_job(
                job["id"],
                status="failed",
                message=f"无法启动工作流：{exc}",
                stderr=str(exc),
            )

        if completed.returncode == 0:
            messages = {
                "prepare": "提示词与任务目录准备完成",
                "preview": "缺失图片预览完成，未调用生图服务",
                "generate": "实时生图工作流执行完成",
            }
            status = "completed"
            message = messages[action]
        else:
            status = "failed"
            message = f"工作流执行失败（退出码 {completed.returncode}）"
        return repository.finish_job(
            job["id"],
            status=status,
            message=message,
            stdout=completed.stdout,
            stderr=completed.stderr,
            return_code=completed.returncode,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _snapshot_digest(snapshot: dict[str, Any]) -> str:
        canonical = {
            "version": snapshot.get("version"),
            "workspace_files": snapshot.get("workspace_files"),
            "workflow_files": snapshot.get("workflow_files"),
        }
        return hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _snapshot_file(
        self,
        path: Path,
        *,
        root: Path,
        label: str,
    ) -> dict[str, Any]:
        unresolved = path
        if unresolved.is_symlink():
            raise WorkflowValidationError(f"{label} cannot be a symlink")
        resolved = unresolved.resolve()
        try:
            relative_path = resolved.relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise WorkflowValidationError(f"{label} escaped its allowed root") from exc
        if not resolved.is_file():
            raise WorkflowValidationError(f"{label} is missing")
        stat = resolved.stat()
        if stat.st_size > MAX_GENERATION_INPUT_FILE_BYTES:
            raise WorkflowValidationError(
                f"{label} exceeds the per-file snapshot limit"
            )
        return {
            "path": relative_path,
            "size_bytes": stat.st_size,
            "sha256": self._sha256(resolved),
        }

    def capture_generation_inputs(self, request: dict[str, Any]) -> dict[str, Any]:
        """Capture content fingerprints before a run is persisted or queued."""
        product = validate_folder_name(
            str(request.get("product") or ""), field="product"
        )
        tasks = [
            validate_folder_name(str(task), field=f"tasks[{index}]")
            for index, task in enumerate(request.get("tasks") or [])
        ]
        shots = [
            validate_shot(str(shot))
            for shot in request.get("shots") or []
        ]
        explicit_items = self._explicit_generation_items(request)
        pairs = (
            explicit_items
            if explicit_items is not None
            else [(task, shot) for task in tasks for shot in shots]
        )
        if not pairs:
            raise WorkflowValidationError("generation input scope cannot be empty")

        workspace_root = self.settings.workspace_root.resolve()
        product_tasks_root = (workspace_root / "组合" / product).resolve()
        try:
            product_tasks_root.relative_to((workspace_root / "组合").resolve())
        except ValueError as exc:  # pragma: no cover - validated segments guard this
            raise WorkflowValidationError("product task root escaped the workspace") from exc

        files_by_path: dict[str, dict[str, Any]] = {}
        workspace_total_bytes = 0

        def record_workspace_file(path: Path, *, label: str) -> None:
            nonlocal workspace_total_bytes
            descriptor = self._snapshot_file(
                path,
                root=workspace_root,
                label=label,
            )
            if descriptor["path"] in files_by_path:
                return
            if len(files_by_path) + 1 + 3 > MAX_GENERATION_INPUT_FILES:
                raise WorkflowValidationError(
                    "Generation input snapshot contains too many files"
                )
            next_total = workspace_total_bytes + int(descriptor["size_bytes"])
            if next_total > MAX_GENERATION_INPUT_TOTAL_BYTES:
                raise WorkflowValidationError(
                    "Generation input snapshot exceeds the total byte limit"
                )
            files_by_path[descriptor["path"]] = descriptor
            workspace_total_bytes = next_total

        pairs_by_task: dict[str, set[str]] = {}
        for task, shot in pairs:
            pairs_by_task.setdefault(task, set()).add(shot)

        for task, selected_shots in pairs_by_task.items():
            task_dir = (product_tasks_root / task).resolve()
            try:
                task_dir.relative_to(product_tasks_root)
            except ValueError as exc:  # pragma: no cover
                raise WorkflowValidationError("task escaped the product output root") from exc
            prompts_path = task_dir / "prompts.json"
            manifest_path = task_dir / "reference_manifest.json"
            reference_dir = task_dir / "参考图"
            for path, label in (
                (prompts_path, f"{task} prompts.json"),
                (manifest_path, f"{task} reference_manifest.json"),
            ):
                record_workspace_file(path, label=label)

            try:
                prompts = json.loads(prompts_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowValidationError(
                    f"{task} prompts.json is invalid: {exc}"
                ) from exc
            if not isinstance(prompts, list):
                raise WorkflowValidationError(f"{task} prompts.json must be a list")
            for shot in selected_shots:
                matches = [
                    prompt
                    for prompt in prompts
                    if isinstance(prompt, dict)
                    and str(prompt.get("filename") or "").endswith(shot)
                ]
                if len(matches) != 1:
                    raise WorkflowValidationError(
                        f"{task}/{shot} requires exactly one prepared prompt"
                    )

            try:
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8-sig")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowValidationError(
                    f"{task} reference_manifest.json is invalid: {exc}"
                ) from exc
            entries = manifest.get("references") if isinstance(manifest, dict) else None
            if not isinstance(entries, list):
                raise WorkflowValidationError(
                    f"{task} reference manifest must contain a references list"
                )
            reference_paths: list[Path] = []
            if reference_dir.is_dir():
                for path in reference_dir.rglob("*"):
                    if (
                        path.is_file()
                        and path.suffix.casefold() in REFERENCE_IMAGE_SUFFIXES
                    ):
                        reference_paths.append(path)
                        if (
                            len(files_by_path)
                            + len(reference_paths)
                            + 3
                            > MAX_GENERATION_INPUT_FILES
                        ):
                            raise WorkflowValidationError(
                                "Generation input snapshot contains too many files"
                            )
                reference_paths.sort()
            if not reference_paths:
                raise WorkflowValidationError(f"{task} has no curated references")
            manifest_names = {
                str(entry.get("filename") or "")
                for entry in entries
                if isinstance(entry, dict)
            }
            actual_names = {path.name for path in reference_paths}
            if manifest_names != actual_names:
                raise WorkflowValidationError(
                    f"{task} reference manifest does not match its files"
                )
            for index, path in enumerate(reference_paths):
                record_workspace_file(
                    path,
                    label=f"{task} reference[{index}]",
                )

        workflow_root = self.settings.workflow_script.parent.resolve()
        workflow_files = []
        for filename in (
            self.settings.workflow_script.name,
            "image2_combo_batch.py",
            "image2_test.py",
        ):
            path = workflow_root / filename
            if not path.is_file():
                raise WorkflowConfigurationError(
                    f"Required workflow file not found: {path}"
                )
            workflow_files.append(
                self._snapshot_file(
                    path,
                    root=workflow_root,
                    label=f"workflow file {filename}",
                )
            )

        snapshot: dict[str, Any] = {
            "version": 1,
            "workspace_files": sorted(
                files_by_path.values(), key=lambda item: str(item["path"])
            ),
            "workflow_files": sorted(
                workflow_files, key=lambda item: str(item["path"])
            ),
        }
        descriptors = snapshot["workspace_files"] + snapshot["workflow_files"]
        if len(descriptors) > MAX_GENERATION_INPUT_FILES:
            raise WorkflowValidationError(
                "Generation input snapshot contains too many files"
            )
        total_bytes = sum(int(item["size_bytes"]) for item in descriptors)
        if total_bytes > MAX_GENERATION_INPUT_TOTAL_BYTES:
            raise WorkflowValidationError(
                "Generation input snapshot exceeds the total byte limit"
            )
        snapshot["total_bytes"] = total_bytes
        snapshot["digest"] = self._snapshot_digest(snapshot)
        if len(
            json.dumps(
                snapshot,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ) > MAX_GENERATION_SNAPSHOT_JSON_BYTES:
            raise WorkflowValidationError(
                "Generation input snapshot metadata is too large"
            )
        return snapshot

    def verify_generation_inputs(
        self,
        snapshot: Any,
        *,
        workspace_root: Path | None = None,
        workflow_root: Path | None = None,
    ) -> None:
        """Fail closed when queued inputs no longer match their captured bytes."""
        if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
            raise WorkflowValidationError("Generation input snapshot is unavailable")
        expected_digest = snapshot.get("digest")
        if (
            not isinstance(expected_digest, str)
            or expected_digest != self._snapshot_digest(snapshot)
        ):
            raise WorkflowValidationError("Generation input snapshot is corrupted")

        roots = (
            (
                "workspace_files",
                (workspace_root or self.settings.workspace_root).resolve(),
            ),
            (
                "workflow_files",
                (workflow_root or self.settings.workflow_script.parent).resolve(),
            ),
        )
        descriptor_count = 0
        total_bytes = 0
        for field, root in roots:
            descriptors = snapshot.get(field)
            if not isinstance(descriptors, list) or not descriptors:
                raise WorkflowValidationError(
                    f"Generation input snapshot has no {field}"
                )
            for descriptor in descriptors:
                if not isinstance(descriptor, dict):
                    raise WorkflowValidationError(
                        f"Generation input snapshot {field} is invalid"
                    )
                relative_path = descriptor.get("path")
                if (
                    not isinstance(relative_path, str)
                    or not relative_path
                    or Path(relative_path).is_absolute()
                    or "\x00" in relative_path
                ):
                    raise WorkflowValidationError(
                        "Generation input snapshot contains an invalid path"
                    )
                unresolved = root / relative_path
                if unresolved.is_symlink():
                    raise WorkflowValidationError(
                        f"Queued input changed: {relative_path}"
                    )
                path = unresolved.resolve()
                try:
                    path.relative_to(root)
                except ValueError as exc:
                    raise WorkflowValidationError(
                        "Generation input snapshot path escaped its root"
                    ) from exc
                expected_size = descriptor.get("size_bytes")
                expected_sha256 = descriptor.get("sha256")
                if (
                    type(expected_size) is not int
                    or expected_size < 0
                    or expected_size > MAX_GENERATION_INPUT_FILE_BYTES
                    or not isinstance(expected_sha256, str)
                    or len(expected_sha256) != 64
                ):
                    raise WorkflowValidationError(
                        "Generation input snapshot contains invalid file metadata"
                    )
                descriptor_count += 1
                total_bytes += expected_size
                if (
                    descriptor_count > MAX_GENERATION_INPUT_FILES
                    or total_bytes > MAX_GENERATION_INPUT_TOTAL_BYTES
                ):
                    raise WorkflowValidationError(
                        "Generation input snapshot exceeds its resource limits"
                    )
                if (
                    not path.is_file()
                    or path.stat().st_size != expected_size
                    or self._sha256(path) != expected_sha256
                ):
                    raise WorkflowValidationError(
                        f"Queued input changed: {relative_path}"
                    )
        if snapshot.get("total_bytes") != total_bytes:
            raise WorkflowValidationError(
                "Generation input snapshot total is inconsistent"
            )

    def materialize_generation_inputs(
        self,
        snapshot: Any,
        *,
        run_dir: Path,
        product: str,
        submitted_references: Any = None,
    ) -> dict[str, Path]:
        """Copy verified bytes into a run-owned execution bundle."""
        self.verify_generation_inputs(snapshot)
        validated_product = validate_folder_name(product, field="product")
        bundle_root = run_dir / "input-bundle"
        if bundle_root.exists():
            raise WorkflowValidationError("Generation input bundle already exists")

        stage = Path(
            tempfile.mkdtemp(prefix=".input-bundle-", dir=run_dir)
        ).resolve()
        workspace_bundle = stage / "workspace"
        workflow_bundle = stage / "workflow"
        submitted_bundle = stage / "submitted-references"
        try:
            for folder in (
                workspace_bundle / "原始商品图" / validated_product,
                workspace_bundle / "配件超市",
                workspace_bundle / "组合",
                workspace_bundle / ".tmp",
                workflow_bundle,
            ):
                folder.mkdir(parents=True, exist_ok=True)

            roots = (
                (
                    "workspace_files",
                    self.settings.workspace_root.resolve(),
                    workspace_bundle,
                ),
                (
                    "workflow_files",
                    self.settings.workflow_script.parent.resolve(),
                    workflow_bundle,
                ),
            )
            for field, source_root, destination_root in roots:
                for descriptor in snapshot[field]:
                    relative_path = str(descriptor["path"])
                    unresolved_source = source_root / relative_path
                    if unresolved_source.is_symlink():
                        raise WorkflowValidationError(
                            f"Queued input changed: {relative_path}"
                        )
                    source = unresolved_source.resolve()
                    destination = (destination_root / relative_path).resolve()
                    try:
                        source.relative_to(source_root)
                        destination.relative_to(destination_root)
                    except ValueError as exc:
                        raise WorkflowValidationError(
                            "Generation input bundle path escaped its root"
                        ) from exc
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination, follow_symlinks=False)
                    if (
                        destination.stat().st_size != descriptor["size_bytes"]
                        or self._sha256(destination) != descriptor["sha256"]
                    ):
                        raise WorkflowValidationError(
                            f"Queued input changed while copying: {relative_path}"
                        )

            references = submitted_references or []
            if not isinstance(references, list) or len(references) > MAX_SUBMITTED_REFERENCE_FILES:
                raise WorkflowValidationError("Submitted generation references are invalid")
            submitted_source = (run_dir / "submitted-references").resolve()
            if references:
                if (run_dir / "submitted-references").is_symlink() or not submitted_source.is_dir():
                    raise WorkflowValidationError("Submitted generation references are missing")
                submitted_bundle.mkdir(parents=True, exist_ok=False)
            for index, descriptor in enumerate(references):
                if not isinstance(descriptor, dict):
                    raise WorkflowValidationError(
                        f"Submitted generation reference {index + 1} is invalid"
                    )
                filename = descriptor.get("filename")
                expected_size = descriptor.get("size_bytes")
                expected_sha256 = descriptor.get("sha256")
                if (
                    not isinstance(filename, str)
                    or not filename
                    or Path(filename).name != filename
                    or Path(filename).suffix.casefold() not in REFERENCE_IMAGE_SUFFIXES
                    or type(expected_size) is not int
                    or not 0 < expected_size <= MAX_SUBMITTED_REFERENCE_FILE_BYTES
                    or not isinstance(expected_sha256, str)
                    or len(expected_sha256) != 64
                ):
                    raise WorkflowValidationError(
                        f"Submitted generation reference {index + 1} has invalid metadata"
                    )
                unresolved_source = submitted_source / filename
                if unresolved_source.is_symlink():
                    raise WorkflowValidationError(
                        f"Submitted generation reference changed: {filename}"
                    )
                source = unresolved_source.resolve()
                destination = (submitted_bundle / filename).resolve()
                try:
                    source.relative_to(submitted_source)
                    destination.relative_to(submitted_bundle.resolve())
                except ValueError as exc:
                    raise WorkflowValidationError(
                        "Submitted generation reference escaped its input directory"
                    ) from exc
                if (
                    not source.is_file()
                    or source.stat().st_size != expected_size
                    or self._sha256(source) != expected_sha256
                ):
                    raise WorkflowValidationError(
                        f"Submitted generation reference changed: {filename}"
                    )
                shutil.copyfile(source, destination, follow_symlinks=False)
                if (
                    destination.stat().st_size != expected_size
                    or self._sha256(destination) != expected_sha256
                ):
                    raise WorkflowValidationError(
                        f"Submitted generation reference changed while copying: {filename}"
                    )

            self.verify_generation_inputs(
                snapshot,
                workspace_root=workspace_bundle,
                workflow_root=workflow_bundle,
            )
            os.replace(stage, bundle_root)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

        workspace_bundle = bundle_root / "workspace"
        workflow_bundle = bundle_root / "workflow"
        submitted_bundle = bundle_root / "submitted-references"
        for path in bundle_root.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
        for path in sorted(
            (path for path in bundle_root.rglob("*") if path.is_dir()),
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            path.chmod(0o555)
        # The provider compatibility layer writes an append-only cost log here.
        (workspace_bundle / ".tmp").chmod(0o700)
        workspace_bundle.chmod(0o755)
        submitted_source = run_dir / "submitted-references"
        if submitted_source.is_dir():
            shutil.rmtree(submitted_source)
        return {
            "root": bundle_root,
            "workspace": workspace_bundle,
            "workflow": workflow_bundle,
            "submitted_references": submitted_bundle,
        }

    @staticmethod
    def _parse_generation_event(line: str) -> dict[str, Any] | None:
        if not line.startswith(GENERATION_EVENT_PREFIX):
            return None
        raw = line[len(GENERATION_EVENT_PREFIX) :].strip()
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return {"type": "event.invalid", "error": "invalid JSON", "raw": raw[:1000]}
        if not isinstance(event, dict):
            return {"type": "event.invalid", "error": "event must be an object"}
        return event

    @staticmethod
    def _explicit_generation_items(
        request: dict[str, Any],
    ) -> list[tuple[str, str]] | None:
        raw_items = request.get("items")
        if raw_items is None:
            return None
        if not isinstance(raw_items, list):
            raise WorkflowValidationError("items must be a list")
        requested_tasks = request.get("tasks") or []
        requested_shots = request.get("shots") or []
        pairs: list[tuple[str, str]] = []
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                raise WorkflowValidationError(f"items[{index}] must be an object")
            task = validate_folder_name(
                str(item.get("task") or ""), field=f"items[{index}].task"
            )
            shot = validate_shot(str(item.get("shot") or ""))
            pair = (task, shot)
            if pair in pairs:
                raise WorkflowValidationError("items cannot contain duplicates")
            if task not in requested_tasks or shot not in requested_shots:
                raise WorkflowValidationError(
                    f"items[{index}] is outside the requested tasks/shots"
                )
            pairs.append(pair)
        return pairs

    def _validate_event_scope(
        self,
        event: dict[str, Any],
        request: dict[str, Any],
    ) -> tuple[str, str, str, int]:
        product = validate_folder_name(str(event.get("product") or ""), field="event.product")
        task = validate_folder_name(str(event.get("task") or ""), field="event.task")
        shot = validate_shot(str(event.get("shot") or ""))
        try:
            candidate_index = int(event.get("candidate_index"))
        except (TypeError, ValueError) as exc:
            raise WorkflowValidationError("event.candidate_index must be an integer") from exc
        variants = int(request.get("variants") or 1)
        if product != request.get("product"):
            raise WorkflowValidationError("event product is outside the generation run")
        if task not in (request.get("tasks") or []):
            raise WorkflowValidationError("event task is outside the generation run")
        if shot not in (request.get("shots") or []):
            raise WorkflowValidationError("event shot is outside the generation run")
        explicit_items = self._explicit_generation_items(request)
        if explicit_items is not None and (task, shot) not in explicit_items:
            raise WorkflowValidationError("event item is outside the generation run")
        if not 1 <= candidate_index <= variants:
            raise WorkflowValidationError("event candidate index is outside the generation run")
        return product, task, shot, candidate_index

    def _apply_generation_event(
        self,
        *,
        job_id: str,
        event: dict[str, Any],
        request: dict[str, Any],
        run_dir: Path,
        repository: Repository,
    ) -> None:
        event_type = str(event.get("type") or "event.unknown")
        reported_run_id = event.get("run_id")
        if reported_run_id is not None and reported_run_id != job_id:
            raise WorkflowValidationError("event run_id does not match the active run")
        if event_type in {"plan", "run.started", "run.finished"}:
            repository.add_event(job_id, event_type, event)
            return
        if event_type == "event.invalid":
            repository.add_event(job_id, event_type, event)
            return
        if event_type not in {"item.started", "item.saved", "item.failed"}:
            repository.add_event(job_id, "event.unknown", event)
            return

        product, task, shot, candidate_index = self._validate_event_scope(event, request)
        common = {
            "job_id": job_id,
            "product": product,
            "task": task,
            "shot": shot,
            "candidate_index": candidate_index,
        }
        if event_type == "item.started":
            item = repository.update_generation_item(
                **common,
                status="running",
                metadata=event,
            )
        elif event_type == "item.failed":
            item = repository.update_generation_item(
                **common,
                status="failed",
                error=str(event.get("error") or "Image generation failed")[:4000],
                metadata=event,
            )
        else:
            relative_path = event.get("relative_path")
            if not isinstance(relative_path, str) or not relative_path:
                raise WorkflowValidationError("saved event is missing relative_path")
            if Path(relative_path).is_absolute() or "\x00" in relative_path:
                raise WorkflowValidationError("saved event path must be workspace-relative")
            relative_to = event.get("relative_to", "workspace")
            if relative_to == "workspace":
                candidate_path = (
                    self.settings.workspace_root / relative_path
                ).resolve()
            elif relative_to == "run_dir":
                candidate_path = (run_dir / relative_path).resolve()
            else:
                raise WorkflowValidationError(
                    "saved event has an unsupported relative path base"
                )
            try:
                candidate_path.relative_to(run_dir.resolve())
            except ValueError as exc:
                raise WorkflowValidationError("saved candidate is outside its run directory") from exc
            expected_parent = (run_dir / product / task / shot).resolve()
            expected_name = f"candidate-{candidate_index:02d}"
            if candidate_path.parent != expected_parent or candidate_path.stem != expected_name:
                raise WorkflowValidationError("saved candidate path does not match its planned item")
            if (
                not candidate_path.is_file()
                or candidate_path.suffix.casefold() not in GENERATION_IMAGE_SUFFIXES
            ):
                raise WorkflowValidationError("saved candidate is not an allowed image file")
            canonical_relative = candidate_path.relative_to(
                self.settings.workspace_root.resolve()
            ).as_posix()
            stat = candidate_path.stat()
            item = repository.update_generation_item(
                **common,
                status="generated",
                relative_path=canonical_relative,
                filename=candidate_path.name,
                prompt_filename=str(event.get("prompt_filename") or event.get("filename") or ""),
                mime_type=mimetypes.guess_type(candidate_path.name)[0]
                or "application/octet-stream",
                size_bytes=stat.st_size,
                sha256=self._sha256(candidate_path),
                model=str(event["model"]) if event.get("model") else None,
                quality=str(event["quality"]) if event.get("quality") else None,
                estimated_cost=float(
                    event.get("estimated_cost", event.get("cost"))
                )
                if event.get("estimated_cost", event.get("cost")) is not None
                else None,
                elapsed_seconds=float(
                    event.get("elapsed_seconds", event.get("elapsed"))
                )
                if event.get("elapsed_seconds", event.get("elapsed")) is not None
                else None,
                metadata=event,
            )
        if item is None:
            raise WorkflowValidationError("event does not match a planned generation item")
        repository.add_event(job_id, event_type, event, item_id=item["id"])

    def execute_generation_run(
        self,
        *,
        job_id: str,
        request: dict[str, Any],
        command: list[str],
        repository: Repository,
    ) -> dict[str, Any]:
        """Keep a persisted run terminal even when pre-launch setup fails."""
        try:
            return self._execute_generation_run(
                job_id=job_id,
                request=request,
                command=command,
                repository=repository,
            )
        except Exception as exc:
            repository.fail_unfinished_items(job_id, str(exc)[:4000])
            result = repository.finish_job(
                job_id,
                status="failed",
                message=f"无法准备候选生成：{exc}",
                stderr=str(exc),
            )
            repository.add_event(job_id, "run.failed", {"reason": str(exc)})
            return repository.get_generation_run(job_id) or result

    def _execute_generation_run(
        self,
        *,
        job_id: str,
        request: dict[str, Any],
        command: list[str],
        repository: Repository,
    ) -> dict[str, Any]:
        """Execute one persisted run while streaming trusted machine events."""

        workspace_root = self.settings.workspace_root.resolve()
        runs_root = (workspace_root / ".museforge" / "runs").resolve()
        try:
            runs_root.relative_to(workspace_root)
        except ValueError as exc:
            raise RuntimeError("Generation runs root escaped the workspace") from exc
        run_dir = (runs_root / job_id).resolve()
        try:
            run_dir.relative_to(runs_root)
        except ValueError as exc:  # pragma: no cover - job ids are server generated
            raise RuntimeError("Generation run directory escaped its root") from exc
        run_dir.mkdir(parents=True, exist_ok=True)
        run_spec_path = run_dir / "run-spec.json"
        input_snapshot = dict(request.get("input_snapshot") or {})
        execution_bundle = self.materialize_generation_inputs(
            input_snapshot,
            run_dir=run_dir,
            product=str(request.get("product") or ""),
            submitted_references=request.get("submitted_references"),
        )
        bundled_workflow = (
            execution_bundle["workflow"] / self.settings.workflow_script.name
        )
        runtime_command = list(command)
        if (
            len(runtime_command) < 2
            or Path(runtime_command[1]).resolve()
            != self.settings.workflow_script.resolve()
            or not bundled_workflow.is_file()
        ):
            raise WorkflowConfigurationError(
                "Generation command does not match the snapshotted workflow"
            )
        runtime_command[1] = str(bundled_workflow)
        tasks = list(request.get("tasks") or [])
        shots = list(request.get("shots") or [])
        explicit_items = self._explicit_generation_items(request)
        item_pairs = (
            explicit_items
            if explicit_items is not None
            else [(task, shot) for task in tasks for shot in shots]
        )
        run_spec = {
            "schema": RUN_SPEC_SCHEMA,
            "version": RUN_SPEC_VERSION,
            "run_id": job_id,
            "product": request.get("product"),
            "tasks": tasks,
            "shots": shots,
            "items": [
                {"task": task, "shot": shot}
                for task, shot in item_pairs
            ],
            "variants": int(request.get("variants") or 1),
            "concurrency": int(request.get("concurrency") or 1),
            "creative_brief": dict(request.get("creative_brief") or {}),
            "submitted_references": [
                {
                    **dict(reference),
                    "path": (
                        execution_bundle["submitted_references"]
                        / str(reference.get("filename") or "")
                    ).relative_to(run_dir).as_posix(),
                }
                for reference in (request.get("submitted_references") or [])
                if isinstance(reference, dict)
            ],
            "provider": dict(request.get("provider") or {}),
            "input_snapshot": input_snapshot,
            "execution_bundle": {
                "workspace": execution_bundle["workspace"]
                .relative_to(run_dir)
                .as_posix(),
                "workflow": execution_bundle["workflow"]
                .relative_to(run_dir)
                .as_posix(),
                "submitted_references": execution_bundle["submitted_references"]
                .relative_to(run_dir)
                .as_posix(),
                "source_digest": input_snapshot.get("digest"),
            },
        }
        serialized_run_spec = json.dumps(
            run_spec,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(serialized_run_spec) > RUN_SPEC_MAX_BYTES:
            raise WorkflowValidationError("Generation run spec is too large")
        run_spec_path.write_bytes(serialized_run_spec)
        run_spec_path.chmod(0o444)
        repository.mark_job_running(job_id)
        repository.add_event(
            job_id,
            "run.started",
            {
                "run_id": job_id,
                "spec": run_spec_path.relative_to(workspace_root).as_posix(),
                "creative_brief_applied": any(run_spec["creative_brief"].values()),
                "submitted_reference_count": len(run_spec["submitted_references"]),
                "provider": run_spec["provider"],
                "input_snapshot_digest": run_spec["input_snapshot"].get("digest"),
                "execution_bundle": run_spec["execution_bundle"],
            },
        )

        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("MUSEFORGE_", "IMAGE_"))
        }
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
                "MUSEFORGE_WORKSPACE_ROOT": str(execution_bundle["workspace"]),
                "MUSEFORGE_RUN_ID": job_id,
                "MUSEFORGE_RUN_DIR": str(run_dir),
                "MUSEFORGE_RUN_SPEC_PATH": str(run_spec_path),
                "MUSEFORGE_VARIANTS": str(int(request.get("variants") or 1)),
            }
        )
        if self.provider_service is not None:
            env.update(self.provider_service.runtime_environment(job_id))
        output_lines: deque[str] = deque(maxlen=4000)
        process: subprocess.Popen[str] | None = None
        try:
            self.verify_generation_inputs(
                run_spec["input_snapshot"],
                workspace_root=execution_bundle["workspace"],
                workflow_root=execution_bundle["workflow"],
            )
            process = subprocess.Popen(
                runtime_command,
                cwd=execution_bundle["workspace"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
            )
            if process.stdout is None:  # pragma: no cover - PIPE guarantees a stream
                raise RuntimeError("Generation workflow did not expose stdout")

            stream: queue.Queue[str | None] = queue.Queue()

            def read_stdout() -> None:
                try:
                    for line in process.stdout:
                        stream.put(line)
                finally:
                    stream.put(None)

            reader = threading.Thread(
                target=read_stdout,
                name=f"novaframe-output-{job_id}",
                daemon=True,
            )
            reader.start()
            deadline = time.monotonic() + self.settings.workflow_timeout_seconds
            stream_finished = False
            while not stream_finished:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(
                        runtime_command,
                        self.settings.workflow_timeout_seconds,
                    )
                try:
                    line = stream.get(timeout=min(0.25, remaining))
                except queue.Empty:
                    if process.poll() is not None and not reader.is_alive():
                        break
                    continue
                if line is None:
                    stream_finished = True
                    continue
                output_lines.append(line)
                event = self._parse_generation_event(line.rstrip("\r\n"))
                if event is None:
                    continue
                try:
                    self._apply_generation_event(
                        job_id=job_id,
                        event=event,
                        request=request,
                        run_dir=run_dir,
                        repository=repository,
                    )
                except (WorkflowValidationError, OSError, ValueError) as exc:
                    repository.add_event(
                        job_id,
                        "event.rejected",
                        {"error": str(exc), "event": event},
                    )
            return_code = process.wait(
                timeout=max(0.1, deadline - time.monotonic())
            )
            missing = repository.fail_unfinished_items(
                job_id, "工作流结束但未收到该候选的结果"
            )
            run = repository.get_generation_run(job_id)
            failed_count = int(run["failed_count"] if run else missing)
            succeeded = return_code == 0 and failed_count == 0
            result = repository.finish_job(
                job_id,
                status="completed" if succeeded else "failed",
                message=(
                    "候选图已生成，等待审核"
                    if succeeded
                    else f"候选生成结束，{failed_count} 张失败"
                ),
                stdout="".join(output_lines),
                return_code=return_code,
            )
            repository.add_event(
                job_id,
                "run.finished",
                {
                    "status": result["status"],
                    "return_code": return_code,
                    "failed_count": failed_count,
                },
            )
            return repository.get_generation_run(job_id) or result
        except subprocess.TimeoutExpired:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            repository.fail_unfinished_items(job_id, "工作流执行超时")
            result = repository.finish_job(
                job_id,
                status="failed",
                message=f"工作流超过 {self.settings.workflow_timeout_seconds} 秒，已停止",
                stdout="".join(output_lines),
            )
            repository.add_event(job_id, "run.failed", {"reason": "timeout"})
            return repository.get_generation_run(job_id) or result
        except Exception as exc:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            repository.fail_unfinished_items(job_id, str(exc)[:4000])
            result = repository.finish_job(
                job_id,
                status="failed",
                message=f"无法完成候选生成：{exc}",
                stdout="".join(output_lines),
                stderr=str(exc),
            )
            repository.add_event(job_id, "run.failed", {"reason": str(exc)})
            return repository.get_generation_run(job_id) or result
