from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.workflow as workflow_module
from backend.config import Settings
from backend.main import create_app


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "MuseForge"
    product = root / "原始商品图" / "SKU-1"
    accessory = root / "配件超市" / "旅行 收纳袋"
    task = root / "组合" / "SKU-1" / "单品"
    for folder in (product, accessory, task / "主图", task / "场景图", task / "参考图"):
        folder.mkdir(parents=True, exist_ok=True)
    (product / "主商品.png").write_bytes(b"not-a-real-png")
    (product / "facts.txt").write_text("verified product facts", encoding="utf-8")
    (accessory / "配件参考.webp").write_bytes(b"not-a-real-webp")
    (task / "主图" / "result.png").write_bytes(b"image")
    (task / "参考图" / "主商品-01.png").write_bytes(b"image")
    (task / "prompts.json").write_text(
        json.dumps(
            [
                {"filename": f"SKU-1-standalone-{shot}"}
                for shot in (
                    "main",
                    "size",
                    "lifestyle-scene",
                    "detail",
                    "comparison",
                )
            ]
        ),
        encoding="utf-8",
    )
    (task / "reference_manifest.json").write_text(
        json.dumps({"references": []}), encoding="utf-8"
    )
    script = (
        root
        / ".agents"
        / "skills"
        / "generate-product-images"
        / "scripts"
        / "product_image_workflow.py"
    )
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# test workflow placeholder\n", encoding="utf-8")
    return root


@pytest.fixture
def settings(workspace: Path, tmp_path: Path) -> Settings:
    return Settings(
        workspace_root=workspace,
        database_path=tmp_path / "data" / "test.sqlite3",
        workflow_script=(
            workspace
            / ".agents"
            / "skills"
            / "generate-product-images"
            / "scripts"
            / "product_image_workflow.py"
        ),
        live_generation_enabled=False,
        workflow_timeout_seconds=30,
    )


def test_health_workspace_and_asset_summary(settings: Settings) -> None:
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["live_generation_enabled"] is False
        assert health.json()["workflow_ready"] is True
        assert client.get(
            "/api/health",
            headers={"Host": "attacker.example"},
        ).status_code == 400

        response = client.get("/api/workspace")
        assert response.status_code == 200
        data = response.json()
        assert data["counts"] == {
            "products": 1,
            "accessories": 1,
            "combination_products": 1,
            "tasks": 1,
            "generated_images": 1,
        }
        assert data["products"][0]["image_count"] == 1
        assert data["products"][0]["assetCount"] == 2
        assert data["products"][0]["taskCount"] == 1
        assert data["products"][0]["promptCount"] == 5
        assert data["products"][0]["outputCount"] == 1
        assert data["products"][0]["readiness"] == "ready"
        assert data["stats"]["prompts"] == 5
        assert data["liveGenerationEnabled"] is False
        assert data["accessories"][0]["name"] == "旅行 收纳袋"
        assert data["accessories"][0]["assetCount"] == 1
        task = data["combinations"][0]["tasks"][0]
        assert task["prompt_count"] == 5
        assert task["prepared_shots"] == [
            "main",
            "size",
            "lifestyle-scene",
            "detail",
            "comparison",
        ]
        assert task["reference_count"] == 1
        assert task["generated_image_count"] == 1

        asset_url = data["products"][0]["cover_image"]["url"]
        asset = client.get(asset_url)
        assert asset.status_code == 200
        assert asset.content == b"not-a-real-png"


def test_canvas_asset_import_uses_a_stable_workspace_url(settings: Settings) -> None:
    content = b"imported-image-bytes"
    data_url = "data:image/png;base64," + base64.b64encode(content).decode("ascii")
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/workspace/assets/import",
            json={
                "product": "SKU-1",
                "filename": "new-reference.png",
                "dataUrl": data_url,
            },
        )
        assert response.status_code == 201
        imported = response.json()
        assert imported["relative_path"].startswith("原始商品图/SKU-1/画布导入/")
        assert imported["original_name"] == "new-reference.png"
        assert client.get(imported["url"]).content == content

        workspace = client.get("/api/workspace").json()
        assert workspace["products"][0]["image_count"] == 2

        invalid = client.post(
            "/api/workspace/assets/import",
            json={"product": "SKU-1", "filename": "bad.svg", "dataUrl": data_url},
        )
        assert invalid.status_code == 422


def test_canvas_round_trip_and_sqlite_persistence(settings: Settings) -> None:
    first_app = create_app(settings)
    with TestClient(first_app, base_url="http://127.0.0.1") as client:
        missing = client.get("/api/canvases/studio-one")
        assert missing.status_code == 404

        created = client.put(
            "/api/canvases/studio-one",
            json={
                "name": "商品主图画布",
                "nodes": [{"id": "node-1", "type": "product"}],
                "edges": [],
                "viewport": {"x": 12, "y": 20, "zoom": 0.8},
            },
        )
        assert created.status_code == 200
        assert created.json()["version"] == 1
        assert created.json()["nodes"][0]["id"] == "node-1"

        updated = client.put(
            "/api/canvases/studio-one",
            json={"document": {"name": "第二版", "nodes": [], "edges": []}},
        )
        assert updated.status_code == 200
        assert updated.json()["ok"] is True
        assert updated.json()["version"] == 2

    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        persisted = client.get("/api/canvases/studio-one")
        assert persisted.status_code == 200
        assert persisted.json()["name"] == "第二版"
        assert persisted.json()["version"] == 2


def test_database_allows_only_one_live_api_instance(settings: Settings) -> None:
    with TestClient(
        create_app(settings),
        base_url="http://127.0.0.1",
    ):
        with pytest.raises(
            RuntimeError,
            match="Another NovaFrame API instance",
        ):
            with TestClient(
                create_app(settings),
                base_url="http://127.0.0.1",
            ):
                pass


def test_demo_job_is_seeded_and_filterable(settings: Settings) -> None:
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.get("/api/jobs?status=completed")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "demo-product-workflow"
        assert body["items"][0]["kind"] == "demo"

        demo = client.post("/api/jobs/demo")
        assert demo.status_code == 200
        assert demo.json()["id"] == "demo-product-workflow"


def test_prepare_and_preview_use_argument_array_without_shell(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict]] = []

    def fake_run(command: list[str], **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "prepared product=SKU-1\n", "")

    monkeypatch.setattr(workflow_module.subprocess, "run", fake_run)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        prepared = client.post(
            "/api/workflow/prepare",
            json={
                "product": "SKU-1",
                "task": "单品",
                "shot": "main",
                "refreshPrompts": True,
            },
        )
        assert prepared.status_code == 200
        assert prepared.json()["ok"] is True
        assert prepared.json()["status"] == "completed"
        prepare_command, prepare_kwargs = calls[0]
        assert prepare_command[2] == "prepare"
        assert "--product" in prepare_command
        assert "--task" in prepare_command
        assert "--shot" not in prepare_command
        assert "image2_combo_batch.py" not in " ".join(prepare_command)
        assert prepare_kwargs["shell"] is False
        assert isinstance(prepare_command, list)

        previewed = client.post(
            "/api/workflow/preview",
            json={"product": "SKU-1", "tasks": ["单品"], "shots": ["main"]},
        )
        assert previewed.status_code == 200
        preview_command, preview_kwargs = calls[1]
        assert preview_command[2] == "preview"
        assert preview_command[-2:] == ["--shot", "main"]
        assert preview_kwargs["shell"] is False

        jobs = client.get("/api/jobs")
        assert jobs.json()["total"] == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"product": "../escape"},
        {"product": "SKU-1", "tasks": ["foo/bar"]},
        {"product": "SKU-1", "shots": ["not-a-shot"]},
        {"product": "missing-product"},
    ],
)
def test_workflow_rejects_traversal_and_unknown_values(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict,
) -> None:
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("unsafe input reached subprocess")

    monkeypatch.setattr(workflow_module.subprocess, "run", fake_run)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.post("/api/workflow/preview", json=payload)
    assert response.status_code == 422
    assert called is False


def test_live_generation_requires_explicit_enablement(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "generated=1\n", "")

    monkeypatch.setattr(workflow_module.subprocess, "run", fake_run)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        disabled = client.post(
            "/api/workflow/generate", json={"product": "SKU-1"}
        )
        assert disabled.status_code == 403
        assert disabled.json()["detail"]["code"] == "live_generation_disabled"
    assert calls == []

    enabled_settings = replace(settings, live_generation_enabled=True)
    with TestClient(
        create_app(enabled_settings), base_url="http://127.0.0.1"
    ) as client:
        enabled = client.post(
            "/api/workflow/generate",
            json={
                "product": "SKU-1",
                "tasks": ["单品"],
                "shots": ["detail"],
                "concurrency": 2,
            },
        )
        assert enabled.status_code == 410
        assert "/api/generation-runs" in enabled.text
    assert calls == []


def test_legacy_generation_route_is_retired_for_every_scope(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    enabled_settings = replace(settings, live_generation_enabled=True)
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("unscoped live request reached subprocess")

    monkeypatch.setattr(workflow_module.subprocess, "run", fake_run)
    with TestClient(
        create_app(enabled_settings), base_url="http://127.0.0.1"
    ) as client:
        no_product = client.post("/api/workflow/generate", json={})
        no_task = client.post(
            "/api/workflow/generate",
            json={"product": "SKU-1", "shots": ["main"]},
        )
        no_shot = client.post(
            "/api/workflow/generate",
            json={"product": "SKU-1", "tasks": ["单品"]},
        )
    assert no_product.status_code == 410
    assert no_task.status_code == 410
    assert no_shot.status_code == 410
    assert called is False
