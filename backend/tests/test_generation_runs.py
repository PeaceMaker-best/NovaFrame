from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.database import GenerationQueueFullError, Repository
from backend.main import create_app
from backend.workflow import (
    WorkflowConfigurationError,
    WorkflowRunner,
    WorkflowValidationError,
)


def _workspace(tmp_path: Path, *, script_body: str = "# placeholder\n") -> tuple[Path, Path]:
    root = tmp_path / "MuseForge"
    product = root / "原始商品图" / "SKU-1"
    task = root / "组合" / "SKU-1" / "单品"
    reference = task / "参考图"
    product.mkdir(parents=True)
    reference.mkdir(parents=True)
    (root / "配件超市").mkdir()
    (product / "source.png").write_bytes(b"source")
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
    (reference / "主商品-01.png").write_bytes(b"reference")
    (task / "reference_manifest.json").write_text(
        json.dumps({"references": [{"filename": "主商品-01.png"}]}),
        encoding="utf-8",
    )
    script = (
        root
        / ".agents"
        / "skills"
        / "generate-product-images"
        / "scripts"
        / "product_image_workflow.py"
    )
    script.parent.mkdir(parents=True)
    script.write_text(script_body, encoding="utf-8")
    for dependency in ("image2_combo_batch.py", "image2_test.py"):
        (script.parent / dependency).write_text("# test dependency\n", encoding="utf-8")
    return root, script


def _settings(tmp_path: Path, root: Path, script: Path, *, enabled: bool) -> Settings:
    return Settings(
        workspace_root=root,
        database_path=tmp_path / "data" / "test.sqlite3",
        workflow_script=script,
        live_generation_enabled=enabled,
        workflow_timeout_seconds=10,
    )


FAKE_GENERATOR = r'''
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

prefix = "MUSEFORGE_EVENT "
root = Path.cwd().resolve()
run_id = os.environ["MUSEFORGE_RUN_ID"]
run_dir = Path(os.environ["MUSEFORGE_RUN_DIR"]).resolve()
variants = int(os.environ["MUSEFORGE_VARIANTS"])

def values(flag: str) -> list[str]:
    result = []
    for index, value in enumerate(sys.argv):
        if value == flag and index + 1 < len(sys.argv):
            result.append(sys.argv[index + 1])
    return result

product = values("--product")[0]
tasks = values("--task")
shots = values("--shot")
print(prefix + json.dumps({"type": "plan", "run_id": run_id, "total_items": len(tasks) * len(shots) * variants}), flush=True)
time.sleep(0.35)
for task in tasks:
    for shot in shots:
        for candidate_index in range(1, variants + 1):
            target = run_dir / product / task / shot / f"candidate-{candidate_index:02d}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"image-{candidate_index}".encode())
            event = {
                "type": "item.saved",
                "run_id": run_id,
                "product": product,
                "task": task,
                "shot": shot,
                "candidate_index": candidate_index,
                "relative_path": target.relative_to(run_dir).as_posix(),
                "relative_to": "run_dir",
                "filename": target.name,
                "prompt_filename": f"{product}-{shot}",
                "model": "test-image-model",
                "quality": "test",
                "estimated_cost": 0.01,
                "elapsed_seconds": 0.02,
            }
            print(prefix + json.dumps(event, ensure_ascii=False), flush=True)
'''

ENV_CAPTURE_GENERATOR = FAKE_GENERATOR.replace(
    'variants = int(os.environ["MUSEFORGE_VARIANTS"])\n',
    '''variants = int(os.environ["MUSEFORGE_VARIANTS"])
(run_dir / "captured-env.json").write_text(
    json.dumps(
        {
            key: os.environ[key]
            for key in (
                "MUSEFORGE_ENV_FILE",
                "MUSEFORGE_UNTRUSTED",
                "IMAGE_UNTRUSTED",
                "IMAGE_MODEL",
                "NO_PROXY",
            )
            if key in os.environ
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
''',
)


def _wait_for_run(client: TestClient, run_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/generation-runs/{run_id}")
        assert response.status_code == 200
        run = response.json()
        if run["status"] in {"completed", "failed"}:
            return run
        time.sleep(0.02)
    raise AssertionError("generation run did not finish")


def test_generation_run_requires_gate_and_narrow_scope(tmp_path: Path) -> None:
    root, script = _workspace(tmp_path)
    disabled = _settings(tmp_path, root, script, enabled=False)
    with TestClient(create_app(disabled), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/generation-runs",
            json={"product": "SKU-1", "tasks": ["单品"], "shots": ["main"]},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "live_generation_disabled"

    enabled = replace(disabled, live_generation_enabled=True)
    with TestClient(create_app(enabled), base_url="http://127.0.0.1") as client:
        preflight = client.options(
            "/api/candidates/candidate-id",
            headers={
                "Origin": "http://localhost:33020",
                "Access-Control-Request-Method": "DELETE",
            },
        )
        assert preflight.status_code == 200
        assert "DELETE" in preflight.headers["access-control-allow-methods"]
        assert client.get("/api/generation-runs/missing").status_code == 404
        assert client.get("/api/candidates/missing/image").status_code == 404
        assert client.post("/api/generation-runs", json={}).status_code == 422
        assert client.post(
            "/api/generation-runs",
            json={"product": "SKU-1", "tasks": [], "shots": ["main"]},
        ).status_code == 422
        assert client.post(
            "/api/generation-runs",
            json={"product": "SKU-1", "tasks": ["单品"], "shots": []},
        ).status_code == 422
        assert client.post(
            "/api/generation-runs",
            json={
                "product": "SKU-1",
                "tasks": ["单品"],
                "shots": ["main"],
                "variants": 7,
            },
        ).status_code == 422
        assert client.post(
            "/api/generation-runs",
            json={"product": "../escape", "tasks": ["单品"], "shots": ["main"]},
        ).status_code == 422
        assert client.post(
            "/api/generation-runs",
            json={"product": "SKU-1", "tasks": ["missing"], "shots": ["main"]},
        ).status_code == 422
        unsupported_claim = client.post(
            "/api/generation-runs",
            json={
                "product": "SKU-1",
                "tasks": ["单品"],
                "shots": ["main"],
                "variants": 1,
                "creativeBrief": {
                    "visibleText": "WATERPROOF CERTIFIED",
                },
            },
        )
        assert unsupported_claim.status_code == 422
        assert "unsupported positive claim" in unsupported_claim.text


def test_generation_run_creation_is_atomic(tmp_path: Path) -> None:
    database = tmp_path / "atomic.sqlite3"
    repository = Repository(database)
    repository.initialize()

    try:
        repository.create_generation_run(
            request={
                "product": "SKU-1",
                "tasks": ["单品"],
                "shots": ["main"],
                "variants": 2,
            },
            command=["python", "workflow.py", "generate"],
            # Missing required provider fields deliberately fails after the job
            # insert has begun. The surrounding transaction must roll it all back.
            provider_snapshot={"channel_name": "broken"},
        )
    except KeyError:
        pass
    else:  # pragma: no cover - the malformed snapshot must be rejected
        raise AssertionError("malformed provider snapshot unexpectedly succeeded")

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM generation_jobs WHERE action = 'generate'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM generation_items"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM generation_events WHERE event_type = 'run.queued'"
        ).fetchone()[0] == 0


def test_generation_queue_cap_is_enforced_in_the_creation_transaction(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "queue-cap.sqlite3")
    repository.initialize()
    request = {
        "product": "SKU-1",
        "tasks": ["单品"],
        "shots": ["main"],
        "variants": 1,
    }
    repository.create_generation_run(
        request=request,
        command=["python", "workflow.py", "generate"],
        max_active_runs=1,
    )

    with pytest.raises(GenerationQueueFullError):
        repository.create_generation_run(
            request=request,
            command=["python", "workflow.py", "generate"],
            max_active_runs=1,
        )

    assert repository.list_generation_runs(limit=10)["total"] == 1


def test_startup_recovery_fails_interrupted_runs_without_retrying(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "restart-recovery.sqlite3")
    repository.initialize()
    run = repository.create_generation_run(
        request={
            "product": "SKU-1",
            "tasks": ["单品"],
            "shots": ["main"],
            "variants": 2,
        },
        command=["python", "workflow.py", "generate"],
    )
    repository.mark_job_running(run["id"])

    assert repository.recover_interrupted_generation_runs() == 1
    recovered = repository.get_generation_run(run["id"])
    assert recovered is not None
    assert recovered["status"] == "failed"
    assert recovered["failed_count"] == 2
    assert "未自动重试" in recovered["message"]
    assert any(
        event["type"] == "run.recovered_as_failed"
        for event in repository.list_events(run["id"])
    )
    assert repository.recover_interrupted_generation_runs() == 0


def test_generation_run_plans_only_explicit_items(tmp_path: Path) -> None:
    database = tmp_path / "explicit-items.sqlite3"
    repository = Repository(database)
    repository.initialize()
    run = repository.create_generation_run(
        request={
            "product": "SKU-1",
            "tasks": ["单品", "配件"],
            "shots": ["main", "detail"],
            "items": [
                {"task": "单品", "shot": "main"},
                {"task": "配件", "shot": "detail"},
            ],
            "variants": 2,
        },
        command=["python", "workflow.py", "generate"],
    )

    with sqlite3.connect(database) as connection:
        planned = connection.execute(
            """
            SELECT task, shot, candidate_index
            FROM generation_items
            WHERE job_id = ?
            ORDER BY task, shot, candidate_index
            """,
            (run["id"],),
        ).fetchall()
    assert planned == [
        ("单品", "main", 1),
        ("单品", "main", 2),
        ("配件", "detail", 1),
        ("配件", "detail", 2),
    ]
    assert run["expected_candidate_count"] == 4


def test_generation_item_rejects_duplicate_and_terminal_transitions(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "item-transitions.sqlite3")
    repository.initialize()
    run = repository.create_generation_run(
        request={
            "product": "SKU-1",
            "tasks": ["单品"],
            "shots": ["main"],
            "variants": 1,
        },
        command=["python", "workflow.py", "generate"],
    )
    common = {
        "job_id": run["id"],
        "product": "SKU-1",
        "task": "单品",
        "shot": "main",
        "candidate_index": 1,
    }

    assert repository.update_generation_item(**common, status="running") is not None
    assert repository.update_generation_item(**common, status="running") is None
    assert repository.update_generation_item(**common, status="generated") is not None
    assert repository.update_generation_item(**common, status="failed") is None


def test_generation_run_and_candidate_listing_support_offsets(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "pagination.sqlite3")
    repository.initialize()
    run_ids: list[str] = []
    candidate_ids: list[str] = []
    for product in ("SKU-1", "SKU-2", "SKU-3"):
        run = repository.create_generation_run(
            request={
                "product": product,
                "tasks": ["单品"],
                "shots": ["main"],
                "variants": 1,
            },
            command=["python", "workflow.py", "generate"],
        )
        run_ids.append(run["id"])
        item = repository.update_generation_item(
            run["id"],
            product=product,
            task="单品",
            shot="main",
            candidate_index=1,
            status="generated",
            relative_path=f".museforge/runs/{run['id']}/candidate.png",
            filename="candidate.png",
        )
        assert item is not None
        candidate_ids.append(item["id"])
        repository.finish_job(
            run["id"],
            status="completed",
            message="done",
        )

    # Candidate creation timestamps have millisecond precision, so concurrent
    # runs can legitimately tie. Make the tie deterministic and prove that the
    # final id key keeps offset pagination stable.
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE generation_items SET created_at = ?, candidate_index = 1",
            ("2026-01-01T00:00:00.000+00:00",),
        )

    first_runs = repository.list_generation_runs(limit=2, offset=0)
    next_runs = repository.list_generation_runs(limit=2, offset=2)
    assert first_runs["total"] == 3
    assert len(first_runs["items"]) == 2
    assert len(next_runs["items"]) == 1
    assert {
        item["id"] for item in first_runs["items"]
    }.isdisjoint({item["id"] for item in next_runs["items"]})

    first_candidates = repository.list_candidates(limit=2, offset=0)
    next_candidates = repository.list_candidates(limit=2, offset=2)
    assert first_candidates["total"] == 3
    assert len(first_candidates["items"]) == 2
    assert len(next_candidates["items"]) == 1
    assert {
        item["id"] for item in first_candidates["items"]
    }.isdisjoint({item["id"] for item in next_candidates["items"]})
    assert [
        item["id"]
        for item in first_candidates["items"] + next_candidates["items"]
    ] == sorted(candidate_ids)


def test_candidate_api_allows_full_batch_limit_only_with_job_filter(
    tmp_path: Path,
) -> None:
    root, script = _workspace(tmp_path)
    settings = _settings(tmp_path, root, script, enabled=False)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        repository: Repository = client.app.state.repository
        run = repository.create_generation_run(
            request={
                "product": "SKU-1",
                "tasks": ["单品"],
                "shots": ["main"],
                "variants": 1,
            },
            command=["python", "workflow.py", "generate"],
        )
        candidate = repository.update_generation_item(
            run["id"],
            product="SKU-1",
            task="单品",
            shot="main",
            candidate_index=1,
            status="generated",
            relative_path=f".museforge/runs/{run['id']}/candidate.png",
            filename="candidate.png",
        )
        assert candidate is not None
        repository.finish_job(run["id"], status="completed", message="done")

        full_batch = client.get(
            "/api/candidates",
            params={"job_id": run["id"], "limit": 3000},
        )
        assert full_batch.status_code == 200
        assert full_batch.json()["total"] == 1

        assert client.get("/api/candidates", params={"limit": 500}).status_code == 200
        unfiltered_overflow = client.get(
            "/api/candidates",
            params={"limit": 501},
        )
        assert unfiltered_overflow.status_code == 422
        assert "without a job_id filter" in unfiltered_overflow.text

        job_overflow = client.get(
            "/api/candidates",
            params={"job_id": run["id"], "limit": 3001},
        )
        assert job_overflow.status_code == 422


def test_generation_run_rejects_inconsistent_items_and_batch_overflow(
    tmp_path: Path,
) -> None:
    root, script = _workspace(tmp_path)
    settings = replace(
        _settings(tmp_path, root, script, enabled=True),
        max_generation_items=1,
    )
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        inconsistent = client.post(
            "/api/generation-runs",
            json={
                "product": "SKU-1",
                "tasks": ["单品"],
                "shots": ["main", "detail"],
                "items": [{"task": "单品", "shot": "main"}],
            },
        )
        assert inconsistent.status_code == 422
        assert "exactly summarize" in inconsistent.text

        too_large = client.post(
            "/api/generation-runs",
            json={
                "product": "SKU-1",
                "tasks": ["单品"],
                "shots": ["main"],
                "items": [{"task": "单品", "shot": "main"}],
                "variants": 2,
            },
        )
        assert too_large.status_code == 422
        assert "超过服务端上限" in too_large.text
        assert client.get("/api/generation-runs").json()["total"] == 0


def test_generation_input_snapshot_detects_queued_file_changes(tmp_path: Path) -> None:
    root, script = _workspace(tmp_path)
    runner = WorkflowRunner(_settings(tmp_path, root, script, enabled=True))
    request = {
        "product": "SKU-1",
        "tasks": ["单品"],
        "shots": ["main"],
        "items": [{"task": "单品", "shot": "main"}],
    }

    snapshot = runner.capture_generation_inputs(request)
    runner.verify_generation_inputs(snapshot)
    assert len(snapshot["digest"]) == 64

    prompts_path = root / "组合" / "SKU-1" / "单品" / "prompts.json"
    prompts_path.write_text(
        prompts_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(WorkflowValidationError, match="Queued input changed"):
        runner.verify_generation_inputs(snapshot)


@pytest.mark.parametrize("missing_dependency", ["image2_combo_batch.py", "image2_test.py"])
def test_generation_input_snapshot_requires_complete_workflow_bundle(
    tmp_path: Path,
    missing_dependency: str,
) -> None:
    root, script = _workspace(tmp_path)
    (script.parent / missing_dependency).unlink()
    runner = WorkflowRunner(_settings(tmp_path, root, script, enabled=True))
    request = {
        "product": "SKU-1",
        "tasks": ["单品"],
        "shots": ["main"],
        "items": [{"task": "单品", "shot": "main"}],
    }

    with pytest.raises(
        WorkflowConfigurationError,
        match=missing_dependency,
    ):
        runner.capture_generation_inputs(request)


def test_prelaunch_failure_always_finishes_the_persisted_run(tmp_path: Path) -> None:
    root, script = _workspace(tmp_path)
    settings = _settings(tmp_path, root, script, enabled=True)
    repository = Repository(settings.database_path)
    repository.initialize()

    class BrokenProviderRuntime:
        def runtime_environment(self, job_id: str) -> dict[str, str]:
            raise RuntimeError(f"credential snapshot unavailable for {job_id}")

    runner = WorkflowRunner(settings, provider_service=BrokenProviderRuntime())  # type: ignore[arg-type]
    request = {
        "product": "SKU-1",
        "tasks": ["单品"],
        "shots": ["main"],
        "items": [{"task": "单品", "shot": "main"}],
        "variants": 1,
    }
    request["input_snapshot"] = runner.capture_generation_inputs(request)
    run = repository.create_generation_run(
        request=request,
        command=[sys.executable, str(script), "generate"],
    )

    finished = runner.execute_generation_run(
        job_id=run["id"],
        request=request,
        command=[sys.executable, str(script), "generate"],
        repository=repository,
    )

    assert finished["status"] == "failed"
    assert finished["failed_count"] == 1
    assert "credential snapshot unavailable" in finished["message"]
    assert any(
        event["type"] == "run.failed"
        for event in repository.list_events(run["id"])
    )


def test_generation_subprocess_rebuilds_controlled_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, script = _workspace(tmp_path, script_body=ENV_CAPTURE_GENERATOR)
    settings = _settings(tmp_path, root, script, enabled=True)
    repository = Repository(settings.database_path)
    repository.initialize()

    class ControlledProviderRuntime:
        def runtime_environment(self, job_id: str) -> dict[str, str]:
            assert job_id
            return {
                "IMAGE_API_KEY": "controlled-key",
                "IMAGE_MODEL": "controlled-model",
            }

    external_env = tmp_path / "mutable-provider.env"
    external_env.write_text("IMAGE_UNTRUSTED=from-external-file\n", encoding="utf-8")
    monkeypatch.setenv("MUSEFORGE_ENV_FILE", str(external_env))
    monkeypatch.setenv("MUSEFORGE_UNTRUSTED", "inherited-museforge-value")
    monkeypatch.setenv("IMAGE_UNTRUSTED", "inherited-image-value")
    monkeypatch.setenv("IMAGE_MODEL", "inherited-model")
    monkeypatch.setenv("NO_PROXY", "preserved-general-value")

    runner = WorkflowRunner(  # type: ignore[arg-type]
        settings,
        provider_service=ControlledProviderRuntime(),
    )
    request = {
        "product": "SKU-1",
        "tasks": ["单品"],
        "shots": ["main"],
        "items": [{"task": "单品", "shot": "main"}],
        "variants": 1,
        "concurrency": 1,
    }
    command = runner.build_command("generate", request)
    request["input_snapshot"] = runner.capture_generation_inputs(request)
    run = repository.create_generation_run(request=request, command=command)

    finished = runner.execute_generation_run(
        job_id=run["id"],
        request=request,
        command=command,
        repository=repository,
    )

    assert finished["status"] == "completed"
    captured = json.loads(
        (
            root
            / ".museforge"
            / "runs"
            / run["id"]
            / "captured-env.json"
        ).read_text(encoding="utf-8")
    )
    assert captured == {
        "IMAGE_MODEL": "controlled-model",
        "NO_PROXY": "preserved-general-value",
    }


def test_api_runs_bundled_workflow_against_local_image_provider(
    tmp_path: Path, monkeypatch
) -> None:
    root, _ = _workspace(tmp_path)
    # Let the real bundled workflow prepare its own valid five-shot prompt set.
    (root / "组合" / "SKU-1" / "单品" / "prompts.json").unlink()
    accessory = root / "配件超市" / "收纳袋"
    accessory.mkdir()
    (accessory / "source.png").write_bytes(b"accessory-source")
    combination_reference = root / "组合" / "SKU-1" / "收纳袋" / "参考图"
    combination_reference.mkdir(parents=True)
    (combination_reference / "主商品-01.png").write_bytes(b"main-reference")
    (combination_reference / "配件-01.png").write_bytes(b"accessory-reference")
    (combination_reference.parent / "reference_manifest.json").write_text(
        json.dumps(
            {
                "references": [
                    {"filename": "主商品-01.png"},
                    {"filename": "配件-01.png"},
                ]
            }
        ),
        encoding="utf-8",
    )
    bundled_workflow = (
        Path(__file__).resolve().parents[2]
        / ".agents"
        / "skills"
        / "generate-product-images"
        / "scripts"
        / "product_image_workflow.py"
    )
    assert bundled_workflow.is_file()
    prepare_env = os.environ.copy()
    prepare_env["MUSEFORGE_WORKSPACE_ROOT"] = str(root)
    prepared = subprocess.run(
        [
            sys.executable,
            str(bundled_workflow),
            "prepare",
            "--product",
            "SKU-1",
            "--refresh-prompts",
        ],
        cwd=root,
        env=prepare_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr

    image_bytes = b"\x89PNG\r\n\x1a\nmuseforge-local-provider"
    provider_requests: list[dict[str, object]] = []

    class LocalImageProvider(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            provider_requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                    "body": body,
                }
            )
            if len(provider_requests) == 1:
                for reference_path in (root / "组合").glob(
                    "SKU-1/*/参考图/*.png"
                ):
                    reference_path.write_bytes(b"mutated-after-first-provider-call")
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(image_bytes)))
            self.end_headers()
            self.wfile.write(image_bytes)

        def log_message(self, format: str, *args: object) -> None:
            return

    provider = ThreadingHTTPServer(("127.0.0.1", 0), LocalImageProvider)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    provider_thread.start()
    monkeypatch.setenv("IMAGE_OUTPUT_FORMAT", "png")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    settings = _settings(tmp_path, root, bundled_workflow, enabled=True)

    try:
        with TestClient(
            create_app(settings), base_url="http://127.0.0.1"
        ) as client:
            channel_response = client.post(
                "/api/provider-channels",
                json={
                    "name": "Local test provider",
                    "base_url": f"http://127.0.0.1:{provider.server_port}/v1",
                    "endpoint": "/images/edits",
                    "api_key": "test-only-key",
                    "model": "local-image-contract-test",
                    "currency": "USD",
                    "rates": {"low": 0.001, "medium": 0, "high": 0},
                },
            )
            assert channel_response.status_code == 201
            channel_id = channel_response.json()["id"]

            response = client.post(
                "/api/generation-runs",
                json={
                    "product": "SKU-1",
                    "tasks": ["单品", "收纳袋"],
                    "shots": ["main", "detail"],
                    "items": [
                        {"task": "单品", "shot": "main"},
                        {"task": "收纳袋", "shot": "detail"},
                    ],
                    "variants": 1,
                    "concurrency": 1,
                    "providerMode": "fixed",
                    "providerChannelId": channel_id,
                    "quality": "low",
                    "size": "1024x1024",
                    "creativeBrief": {
                        "composition": "LOCAL CONTRACT TEST centered composition."
                    },
                },
            )
            assert response.status_code == 202
            queued = response.json()
            run = _wait_for_run(client, queued["id"])

            assert run["status"] == "completed", run
            assert run["expected_candidate_count"] == 2
            assert run["candidate_count"] == 2
            run_spec = json.loads(
                (
                    root
                    / ".museforge"
                    / "runs"
                    / queued["id"]
                    / "run-spec.json"
                ).read_text(encoding="utf-8")
            )
            assert run_spec["schema"] == "museforge.run-spec"
            assert run_spec["version"] == 2
            assert run_spec["items"] == [
                {"task": "单品", "shot": "main"},
                {"task": "收纳袋", "shot": "detail"},
            ]
            assert len(run_spec["input_snapshot"]["digest"]) == 64
            assert run_spec["input_snapshot"]["workspace_files"]
            assert run_spec["execution_bundle"]["source_digest"] == run_spec[
                "input_snapshot"
            ]["digest"]
            bundle_root = (
                root
                / ".museforge"
                / "runs"
                / queued["id"]
                / "input-bundle"
            )
            assert (
                bundle_root
                / "workspace"
                / "组合"
                / "SKU-1"
                / "收纳袋"
                / "参考图"
                / "配件-01.png"
            ).read_bytes() == b"accessory-reference"
            assert (
                bundle_root / "workflow" / bundled_workflow.name
            ).is_file()

            candidates = client.get(
                "/api/candidates", params={"job_id": queued["id"]}
            ).json()
            assert candidates["total"] == 2
            assert {
                (candidate["task"], candidate["shot"])
                for candidate in candidates["items"]
            } == {("单品", "main"), ("收纳袋", "detail")}
            assert all(
                client.get(candidate["url"]).content == image_bytes
                for candidate in candidates["items"]
            )
    finally:
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=2)

    assert len(provider_requests) == 2
    for request in provider_requests:
        assert request["path"] == "/v1/images/edits"
        assert request["authorization"] == "Bearer test-only-key"
        assert str(request["content_type"]).startswith("multipart/form-data;")
        assert b"local-image-contract-test" in request["body"]
        assert b"LOCAL CONTRACT TEST centered composition." in request["body"]
        assert b"mutated-after-first-provider-call" not in request["body"]


def test_background_run_candidate_review_and_delete_round_trip(tmp_path: Path) -> None:
    root, script = _workspace(tmp_path, script_body=FAKE_GENERATOR)
    settings = _settings(tmp_path, root, script, enabled=True)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        started = time.monotonic()
        response = client.post(
            "/api/generation-runs",
            json={
                "product": "SKU-1",
                "tasks": ["单品"],
                "shots": ["main"],
                "variants": 2,
                "concurrency": 2,
                "creativeBrief": {
                    "subject": "Keep the verified product complete.",
                    "environment": "Bright neutral tabletop.",
                    "composition": "Centered with generous safe margins.",
                    "negatives": "No unrelated props.",
                    "visibleText": "READY TO SHIP",
                },
            },
        )
        elapsed = time.monotonic() - started
        assert response.status_code == 202
        assert elapsed < 0.3
        queued = response.json()
        assert queued["status"] == "queued"
        assert queued["expected_candidate_count"] == 2
        assert queued["candidate_count"] == 0

        run = _wait_for_run(client, queued["id"])
        assert run["status"] == "completed"
        assert run["progress"] == 100
        assert run["candidate_count"] == 2
        assert run["pending_review_count"] == 2
        assert any(event["type"] == "item.saved" for event in run["events"])

        listing = client.get(
            "/api/generation-runs", params={"status": "completed"}
        ).json()
        assert listing["total"] == 1
        assert listing["items"][0]["request"]["variants"] == 2
        assert listing["items"][0]["request"]["creative_brief"]["visible_text"] == "READY TO SHIP"
        run_spec = json.loads(
            (root / ".museforge" / "runs" / queued["id"] / "run-spec.json").read_text(
                encoding="utf-8"
            )
        )
        assert run_spec["run_id"] == queued["id"]
        assert run_spec["creative_brief"]["environment"] == "Bright neutral tabletop."
        assert any(
            event["type"] == "run.started"
            and event["payload"]["creative_brief_applied"] is True
            for event in run["events"]
        )

        candidates = client.get(
            "/api/candidates",
            params={"job_id": queued["id"], "review_status": "pending"},
        ).json()
        assert candidates["total"] == 2
        first, second = candidates["items"]
        assert first["url"] == f"/api/candidates/{first['id']}/image"
        assert first["storage_status"] == "staged"
        image = client.get(first["url"])
        assert image.status_code == 200
        assert image.content in {b"image-1", b"image-2"}
        assert client.get(
            f"/api/workspace/assets/{first['relative_path']}"
        ).status_code == 403
        assert client.patch(
            f"/api/candidates/{first['id']}", json={"decision": "rejected"}
        ).status_code == 422

        selected_response = client.patch(
            f"/api/candidates/{first['id']}", json={"decision": "selected"}
        )
        assert selected_response.status_code == 200
        selected = selected_response.json()
        assert selected["review_status"] == "selected"
        assert selected["storage_status"] == "promoted"
        assert selected["relative_path"].startswith("组合/SKU-1/单品/主图/")
        promoted = root / selected["relative_path"]
        assert promoted.is_file()
        assert client.get(selected["url"]).status_code == 200

        # Selection is idempotent and never overwrites a second destination.
        repeated = client.patch(
            f"/api/candidates/{first['id']}", json={"decision": "selected"}
        )
        assert repeated.status_code == 200
        assert repeated.json()["relative_path"] == selected["relative_path"]

        second_path = root / second["relative_path"]
        assert second_path.is_file()
        assert client.delete(f"/api/candidates/{second['id']}").status_code == 204
        assert not second_path.exists()
        assert client.get(second["url"]).status_code == 404

        assert client.delete(f"/api/candidates/{first['id']}").status_code == 204
        assert not promoted.exists()
        assert client.get(first["url"]).status_code == 404
        assert client.get(
            "/api/candidates", params={"job_id": queued["id"]}
        ).json()["total"] == 0


def test_candidate_id_lookup_never_serves_tampered_paths(tmp_path: Path) -> None:
    root, script = _workspace(tmp_path)
    settings = _settings(tmp_path, root, script, enabled=True)
    repository = Repository(settings.database_path)
    repository.initialize()
    request = {
        "product": "SKU-1",
        "tasks": ["单品"],
        "shots": ["main"],
        "variants": 1,
        "concurrency": 1,
    }
    run = repository.create_generation_run(
        request=request,
        command=["python", str(script), "generate"],
    )
    secret = root / ".env"
    secret.write_text("SECRET=never-return-this", encoding="utf-8")
    item = repository.update_generation_item(
        run["id"],
        product="SKU-1",
        task="单品",
        shot="main",
        candidate_index=1,
        status="generated",
        relative_path=".env",
        filename="candidate.png",
    )
    assert item is not None

    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        image = client.get(f"/api/candidates/{item['id']}/image")
        assert image.status_code == 403
        deleted = client.delete(f"/api/candidates/{item['id']}")
        assert deleted.status_code == 403
        assert secret.read_text(encoding="utf-8") == "SECRET=never-return-this"


def test_provider_registry_auto_routing_and_secret_redaction(tmp_path: Path) -> None:
    root, script = _workspace(tmp_path, script_body=FAKE_GENERATOR)
    settings = _settings(tmp_path, root, script, enabled=True)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        channels = [
            {
                "name": "渠道 A",
                "base_url": "https://channel-a.invalid/v1",
                "endpoint": "/images/edits",
                "api_key": "sk-secret-channel-a",
                "model": "gpt-image-2",
                "currency": "CNY",
                "rates": {"low": 0.08, "medium": 0.16, "high": 0.32},
            },
            {
                "name": "渠道 B",
                "base_url": "https://channel-b.invalid/v1",
                "endpoint": "/images/edits",
                "api_key": "sk-secret-channel-b",
                "model": "gpt-image-2-2026-04-21",
                "currency": "CNY",
                "rates": {"low": 0.03, "medium": 0.12, "high": 0.28},
            },
            {
                "name": "美元渠道",
                "base_url": "https://usd-channel.invalid/v1",
                "endpoint": "/images/edits",
                "api_key": "sk-secret-usd",
                "model": "gpt-image-2",
                "currency": "USD",
                "rates": {"low": 0.001, "medium": 0.002, "high": 0.003},
            },
        ]
        created = []
        for payload in channels:
            response = client.post("/api/provider-channels", json=payload)
            assert response.status_code == 201
            created.append(response.json())
            assert "api_key" not in response.json()
            assert "api_key_encrypted" not in response.json()
            assert "secret" not in response.text

        config = client.get("/api/provider-config")
        assert config.status_code == 200
        assert config.json()["summary"]["active_channel_count"] == 3
        assert "sk-secret" not in config.text
        assert all(item["api_key_hint"].startswith("••••") for item in config.json()["channels"])

        routing = client.put(
            "/api/provider-routing",
            json={"mode": "auto", "currency": "CNY"},
        )
        assert routing.status_code == 200

        response = client.post(
            "/api/generation-runs",
            json={
                "product": "SKU-1",
                "tasks": ["单品"],
                "shots": ["main"],
                "variants": 1,
                "providerMode": "auto",
                "quality": "low",
                "size": "1024x1024",
            },
        )
        assert response.status_code == 202
        queued = response.json()
        assert queued["provider"]["channel_name"] == "渠道 B"
        assert queued["provider"]["unit_price"] == 0.03
        assert queued["provider"]["currency"] == "CNY"
        assert "api_key" not in response.json()["provider"]
        assert "api_key_encrypted" not in response.json()["provider"]
        run = _wait_for_run(client, queued["id"])
        assert run["status"] == "completed"

        run_spec = (root / ".museforge" / "runs" / queued["id"] / "run-spec.json")
        assert "sk-secret" not in run_spec.read_text(encoding="utf-8")

        cheapest_id = next(item["id"] for item in created if item["name"] == "渠道 B")
        disabled = client.patch(
            f"/api/provider-channels/{cheapest_id}", json={"active": False}
        )
        assert disabled.status_code == 200
        fixed = client.post(
            "/api/generation-runs",
            json={
                "product": "SKU-1",
                "tasks": ["单品"],
                "shots": ["main"],
                "providerMode": "fixed",
                "providerChannelId": cheapest_id,
            },
        )
        assert fixed.status_code == 422
        assert "停用" in fixed.text

    with sqlite3.connect(settings.database_path) as connection:
        encrypted = connection.execute(
            "SELECT api_key_encrypted FROM provider_channels WHERE name = '渠道 B'"
        ).fetchone()[0]
        assert encrypted != "sk-secret-channel-b"
        assert "sk-secret-channel-b" not in encrypted
        snapshot = connection.execute(
            "SELECT channel_name, unit_price FROM generation_provider_snapshots WHERE job_id = ?",
            (queued["id"],),
        ).fetchone()
        assert snapshot == ("渠道 B", 0.03)
    key_path = settings.database_path.with_suffix(settings.database_path.suffix + ".key")
    assert key_path.stat().st_mode & 0o777 == 0o600


def test_legacy_database_is_migrated_without_losing_jobs(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    timestamp = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE canvases (
                id TEXT PRIMARY KEY, document_json TEXT NOT NULL, version INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE generation_jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, action TEXT NOT NULL,
                status TEXT NOT NULL, request_json TEXT NOT NULL, command_json TEXT,
                message TEXT NOT NULL DEFAULT '', stdout TEXT NOT NULL DEFAULT '',
                stderr TEXT NOT NULL DEFAULT '', return_code INTEGER, created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO generation_jobs(
                id, kind, action, status, request_json, created_at, updated_at
            ) VALUES ('legacy-job', 'workflow', 'preview', 'completed', '{}', ?, ?)
            """,
            (timestamp, timestamp),
        )

    repository = Repository(database)
    repository.initialize()
    assert repository.get_job("legacy-job") is not None
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "generation_items",
            "generation_events",
            "provider_channels",
            "provider_routing_settings",
            "generation_provider_snapshots",
        } <= tables
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
