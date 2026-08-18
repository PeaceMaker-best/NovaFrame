from __future__ import annotations

from pathlib import Path

import pytest

from backend.workspace import resolve_workspace_asset


def test_asset_resolver_stays_inside_media_roots(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    (root / "原始商品图" / "SKU").mkdir(parents=True)
    image = root / "原始商品图" / "SKU" / "main.png"
    image.write_bytes(b"image")
    secret = root / ".env"
    secret.write_text("SECRET=value", encoding="utf-8")

    assert resolve_workspace_asset(root, "原始商品图/SKU/main.png") == image.resolve()
    with pytest.raises(ValueError):
        resolve_workspace_asset(root, ".env")
    with pytest.raises(ValueError):
        resolve_workspace_asset(root, "原始商品图/../../.env")
    with pytest.raises(FileNotFoundError):
        resolve_workspace_asset(root, "原始商品图/SKU/missing.png")
