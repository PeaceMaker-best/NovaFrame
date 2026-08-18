from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


TRUE_VALUES = {"1", "true", "yes", "y", "on"}


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in TRUE_VALUES


def bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        return max(minimum, min(maximum, int(raw)))
    except ValueError:
        return default


def _path_from_env(name: str, default: Path, *, relative_to: Path) -> Path:
    raw = os.getenv(name)
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = relative_to / path
    return path.resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    workspace_root: Path
    database_path: Path
    workflow_script: Path
    live_generation_enabled: bool = False
    workflow_timeout_seconds: int = 3600
    max_generation_items: int = 120
    max_active_generation_runs: int = 4

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = Path(__file__).resolve().parents[1]
        default_workspace = project_root / "workspace"
        # Local secrets and the explicit live-generation gate are optional. Shell
        # environment variables keep precedence over values in the project file.
        load_dotenv(project_root / ".env", override=False)
        workspace = _path_from_env(
            "MUSEFORGE_WORKSPACE_ROOT",
            default_workspace,
            relative_to=project_root,
        )
        database = _path_from_env(
            "MUSEFORGE_DB_PATH",
            Path(__file__).resolve().parent / "data" / "museforge.sqlite3",
            relative_to=project_root,
        )
        workflow = _path_from_env(
            "MUSEFORGE_WORKFLOW_SCRIPT",
            project_root
            / ".agents"
            / "skills"
            / "generate-product-images"
            / "scripts"
            / "product_image_workflow.py",
            relative_to=project_root,
        )
        return cls(
            workspace_root=workspace,
            database_path=database,
            workflow_script=workflow,
            live_generation_enabled=env_flag("MUSEFORGE_ENABLE_LIVE_GENERATION"),
            workflow_timeout_seconds=bounded_env_int(
                "MUSEFORGE_WORKFLOW_TIMEOUT_SECONDS",
                3600,
                minimum=10,
                maximum=21600,
            ),
            max_generation_items=bounded_env_int(
                "MUSEFORGE_MAX_GENERATION_ITEMS",
                120,
                minimum=1,
                maximum=3000,
            ),
            max_active_generation_runs=bounded_env_int(
                "MUSEFORGE_MAX_ACTIVE_GENERATION_RUNS",
                4,
                minimum=1,
                maximum=50,
            ),
        )
