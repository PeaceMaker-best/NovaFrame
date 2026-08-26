from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import image2_combo_batch as batch
import product_image_workflow as workflow


class ImageResponse:
    ok = True
    status_code = 200
    text = ""
    headers = {"content-type": "image/png"}
    content = b"complete-image-bytes"


def test_preview_does_not_prepare_or_mutate_prompt_baselines(monkeypatch) -> None:
    args = SimpleNamespace(
        action="preview",
        product="SKU-1",
        task=["单品"],
        shot=["main"],
        combinations_only=False,
        variants_only=False,
        refresh_prompts=False,
        overwrite=False,
        concurrency=None,
    )
    monkeypatch.setattr(workflow, "parse_args", lambda: args)
    monkeypatch.setattr(workflow, "load_mixed_env", lambda path: None)
    monkeypatch.setattr(workflow, "products", lambda selected: [Path("SKU-1")])
    monkeypatch.setattr(
        workflow,
        "prepare",
        lambda *args, **kwargs: pytest.fail("preview must not prepare files"),
    )
    monkeypatch.setattr(workflow, "collect_jobs", lambda *args, **kwargs: [])
    monkeypatch.setattr(workflow, "preview_jobs", lambda jobs: None)

    workflow.main()


def test_canvas_creative_brief_rejects_positive_claims_but_allows_exclusions() -> None:
    prompt = {
        "environment": "Neutral studio.",
        "composition": "Centered.",
        "negatives": "No unsupported claims.",
        "text": {"Content": "English only."},
    }
    with pytest.raises(ValueError, match="unsupported positive claim"):
        workflow.apply_canvas_creative_brief(
            prompt,
            {"environment": "Outdoor waterproof product scene"},
        )
    with pytest.raises(ValueError, match="marketplace-ready English"):
        workflow.apply_canvas_creative_brief(
            prompt,
            {"visible_text": "防水认证"},
        )

    compiled = workflow.apply_canvas_creative_brief(
        prompt,
        {"negatives": "No waterproof claims or certification badges."},
    )
    assert "No waterproof claims" in compiled["negatives"]


def test_submitted_references_are_validated_and_merged_with_identity_anchors(
    tmp_path: Path,
) -> None:
    run_dir = (tmp_path / "runs" / "run-reference").resolve()
    submitted_dir = run_dir / "input-bundle" / "submitted-references"
    submitted_dir.mkdir(parents=True)
    submitted = submitted_dir / "reference-01.png"
    submitted.write_bytes(b"submitted-reference")
    descriptor = {
        "path": "input-bundle/submitted-references/reference-01.png",
        "size_bytes": submitted.stat().st_size,
        "sha256": hashlib.sha256(submitted.read_bytes()).hexdigest(),
    }
    run = workflow.MuseForgeRunConfig(
        run_id="run-reference",
        run_dir=run_dir,
        variants=1,
    )

    validated = workflow.museforge_submitted_reference_images(
        {"submitted_references": [descriptor]},
        run,
    )
    assert validated == [submitted]

    main = tmp_path / "主商品-01.png"
    accessory = tmp_path / "配件-01.png"
    extra = tmp_path / "主商品-02.png"
    for path in (main, accessory, extra):
        path.write_bytes(path.name.encode())
    merged = workflow.generation_reference_images(
        [main, accessory, extra],
        validated,
        combo=True,
    )
    assert merged[:3] == [main, accessory, submitted]

    bad = dict(descriptor, sha256="0" * 64)
    with pytest.raises(ValueError, match="changed"):
        workflow.museforge_submitted_reference_images(
            {"submitted_references": [bad]},
            run,
        )


def test_request_image_uses_part_file_and_atomic_replace(tmp_path: Path, monkeypatch) -> None:
    combo_dir = tmp_path / "组合" / "SKU" / "单品"
    ref = tmp_path / "reference.png"
    ref.write_bytes(b"reference")
    target = tmp_path / "runs" / "run-1" / "SKU" / "单品" / "main" / "candidate-01.png"
    replacements: list[tuple[Path, Path]] = []
    real_replace = batch.os.replace

    def record_replace(source: str | Path, destination: str | Path) -> None:
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(batch, "ROOT", tmp_path)
    monkeypatch.setattr(batch.requests, "post", lambda *args, **kwargs: ImageResponse())
    monkeypatch.setattr(batch, "write_cost_log", lambda record: None)
    monkeypatch.setattr(batch.os, "replace", record_replace)
    monkeypatch.setenv("IMAGE_API_BASE_URL", "https://image.invalid/v1")
    monkeypatch.setenv("IMAGE_OUTPUT_FORMAT", "png")

    result = batch.request_image(
        combo_dir=combo_dir,
        refs=[ref],
        job={"filename": "SKU-standalone-main"},
        index=0,
        total=1,
        output_path=target,
        allow_overwrite=False,
    )

    assert result["status"] == "saved"
    assert target.read_bytes() == ImageResponse.content
    assert len(replacements) == 1
    assert replacements[0][0].name.endswith(".part")
    assert replacements[0][1] == target
    assert not list(target.parent.glob("*.part"))


def test_collect_jobs_expands_staged_candidates_without_overwrite(tmp_path: Path, monkeypatch) -> None:
    product_dir = tmp_path / "原始商品图" / "SKU-1"
    product_dir.mkdir(parents=True)
    output_root = tmp_path / "组合"
    job_dir = output_root / "SKU-1" / "单品"
    job_dir.mkdir(parents=True)
    (job_dir / "prompts.json").write_text(
        json.dumps([{"filename": "SKU-1-standalone-main"}]),
        encoding="utf-8",
    )
    ref = tmp_path / "reference.png"
    ref.write_bytes(b"reference")
    run_dir = (tmp_path / "runs" / "run-1").resolve()
    run_dir.mkdir(parents=True)
    run_spec_path = run_dir / "run-spec.json"
    run_spec_path.write_text(
        json.dumps(
            {
                "version": 1,
                "run_id": "run-1",
                "product": "SKU-1",
                "tasks": ["单品"],
                "shots": ["main"],
                "creative_brief": {
                    "environment": "Bright neutral tabletop.",
                    "composition": "Centered layout.",
                    "negatives": "No clutter.",
                    "visible_text": "READY TO SHIP",
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(workflow, "OUTPUT_ROOT", output_root)
    monkeypatch.setattr(workflow, "validate_prompt_set", lambda prompts, combo: None)
    monkeypatch.setattr(workflow, "curated_reference_images", lambda *args, **kwargs: [ref])
    monkeypatch.setenv("MUSEFORGE_RUN_ID", "run-1")
    monkeypatch.setenv("MUSEFORGE_RUN_DIR", str(run_dir))
    monkeypatch.setenv("MUSEFORGE_RUN_SPEC_PATH", str(run_spec_path))
    monkeypatch.setenv("MUSEFORGE_VARIANTS", "3")
    monkeypatch.setenv("IMAGE_OUTPUT_FORMAT", "png")

    jobs = workflow.collect_jobs([product_dir], overwrite=True, selected_shots=["main"])

    assert [job.candidate_index for job in jobs] == [1, 2, 3]
    assert [job.target for job in jobs] == [
        run_dir / "SKU-1" / "单品" / "main" / f"candidate-{index:02d}.png"
        for index in range(1, 4)
    ]
    assert all(not job.allow_overwrite for job in jobs)
    assert jobs[0].prompt["museforge_canvas_direction"]["composition"] == "Centered layout."
    assert "Bright neutral tabletop." in jobs[0].prompt["environment"]
    assert "READY TO SHIP" in jobs[0].prompt["text"]["Content"]

    jobs[0].target.parent.mkdir(parents=True)
    jobs[0].target.write_bytes(b"already-finished")
    resumed = workflow.collect_jobs([product_dir], overwrite=True, selected_shots=["main"])
    assert [job.candidate_index for job in resumed] == [2, 3]
    assert jobs[0].target.read_bytes() == b"already-finished"


def test_v2_run_spec_honors_sparse_task_shot_items(tmp_path: Path, monkeypatch) -> None:
    product_dir = tmp_path / "原始商品图" / "SKU-1"
    product_dir.mkdir(parents=True)
    output_root = tmp_path / "组合"
    job_dir = output_root / "SKU-1" / "单品"
    job_dir.mkdir(parents=True)
    (job_dir / "prompts.json").write_text(
        json.dumps(
            [
                {"filename": "SKU-1-standalone-main"},
                {"filename": "SKU-1-standalone-detail"},
            ]
        ),
        encoding="utf-8",
    )
    ref = tmp_path / "reference.png"
    ref.write_bytes(b"reference")
    run_dir = (tmp_path / "runs" / "run-sparse").resolve()
    run_dir.mkdir(parents=True)
    run_spec_path = run_dir / "run-spec.json"
    run_spec_path.write_text(
        json.dumps(
            {
                "schema": workflow.MUSEFORGE_RUN_SPEC_SCHEMA,
                "version": 2,
                "run_id": "run-sparse",
                "product": "SKU-1",
                "tasks": ["单品"],
                "shots": ["main", "detail"],
                "items": [{"task": "单品", "shot": "detail"}],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(workflow, "OUTPUT_ROOT", output_root)
    monkeypatch.setattr(workflow, "validate_prompt_set", lambda prompts, combo: None)
    monkeypatch.setattr(workflow, "curated_reference_images", lambda *args, **kwargs: [ref])
    monkeypatch.setenv("MUSEFORGE_RUN_ID", "run-sparse")
    monkeypatch.setenv("MUSEFORGE_RUN_DIR", str(run_dir))
    monkeypatch.setenv("MUSEFORGE_RUN_SPEC_PATH", str(run_spec_path))
    monkeypatch.setenv("MUSEFORGE_VARIANTS", "1")
    monkeypatch.setenv("IMAGE_OUTPUT_FORMAT", "png")

    jobs = workflow.collect_jobs(
        [product_dir],
        overwrite=False,
        selected_tasks=["单品"],
        selected_shots=["main", "detail"],
    )

    assert [(job.task, job.shot) for job in jobs] == [("单品", "detail")]


def test_run_spec_version_and_schema_compatibility(tmp_path: Path, monkeypatch) -> None:
    run_dir = (tmp_path / "run-contract").resolve()
    run_dir.mkdir()
    spec_path = run_dir / "run-spec.json"
    run = workflow.MuseForgeRunConfig(
        run_id="run-contract",
        run_dir=run_dir,
        variants=1,
    )
    monkeypatch.setenv("MUSEFORGE_RUN_SPEC_PATH", str(spec_path))

    for accepted in (
        {"version": 1, "run_id": "run-contract"},
        {"version": 2, "run_id": "run-contract"},
        {
            "schema": workflow.MUSEFORGE_RUN_SPEC_SCHEMA,
            "version": 2,
            "run_id": "run-contract",
        },
    ):
        spec_path.write_text(json.dumps(accepted), encoding="utf-8")
        assert workflow.museforge_run_spec(run) == accepted

    for rejected, message in (
        ({"version": 3, "run_id": "run-contract"}, "version"),
        (
            {
                "schema": "another.run-spec",
                "version": 2,
                "run_id": "run-contract",
            },
            "schema",
        ),
    ):
        spec_path.write_text(json.dumps(rejected), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            workflow.museforge_run_spec(run)


def test_run_config_requires_absolute_bounded_staging_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSEFORGE_RUN_DIR", "relative/run")
    monkeypatch.setenv("MUSEFORGE_VARIANTS", "2")
    try:
        workflow.museforge_run_config()
    except ValueError as exc:
        assert "absolute" in str(exc)
    else:
        raise AssertionError("relative run directory must be rejected")

    monkeypatch.setenv("MUSEFORGE_RUN_DIR", str(tmp_path.resolve()))
    monkeypatch.setenv("MUSEFORGE_VARIANTS", "7")
    try:
        workflow.museforge_run_config()
    except ValueError as exc:
        assert "1 to 6" in str(exc)
    else:
        raise AssertionError("variant counts above six must be rejected")


def test_empty_generate_emits_parseable_plan_event(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MUSEFORGE_RUN_ID", "run-plan")
    monkeypatch.setenv("MUSEFORGE_RUN_DIR", str(tmp_path.resolve()))
    monkeypatch.setenv("MUSEFORGE_VARIANTS", "4")

    workflow.generate([], concurrency=1)

    first_line = capsys.readouterr().out.splitlines()[0]
    assert first_line.startswith(workflow.MUSEFORGE_EVENT_PREFIX)
    event = json.loads(first_line.removeprefix(workflow.MUSEFORGE_EVENT_PREFIX))
    assert event == {
        "v": 1,
        "type": "plan",
        "run_id": "run-plan",
        "total": 0,
        "total_items": 0,
        "variants": 4,
        "staged": True,
    }


def test_generate_emits_saved_and_failed_candidate_events(tmp_path: Path, monkeypatch, capsys) -> None:
    run_dir = tmp_path.resolve()
    monkeypatch.setenv("MUSEFORGE_RUN_ID", "run-events")
    monkeypatch.setenv("MUSEFORGE_RUN_DIR", str(run_dir))
    monkeypatch.setenv("MUSEFORGE_VARIANTS", "2")
    jobs = [
        workflow.GenerationJob(
            product="SKU-1",
            task="单品",
            shot="main",
            candidate_index=index,
            job_dir=tmp_path / "组合" / "SKU-1" / "单品",
            target=run_dir / "SKU-1" / "单品" / "main" / f"candidate-{index:02d}.png",
            refs=[],
            prompt={"filename": "SKU-1-standalone-main"},
            prompt_index=0,
            prompt_total=1,
            run_dir=run_dir,
            allow_overwrite=False,
        )
        for index in (1, 2)
    ]

    def fake_execute(job: workflow.GenerationJob) -> dict:
        if job.candidate_index == 1:
            return {
                "status": "saved",
                "estimated_cost": 0.04,
                "elapsed_seconds": 1.25,
                "currency": "USD",
            }
        return {
            "status": "failed",
            "error": "synthetic failure",
            "estimated_cost": 0.0,
            "elapsed_seconds": 0.5,
        }

    monkeypatch.setattr(workflow, "execute_generation_job", fake_execute)
    with pytest.raises(RuntimeError, match="1 image requests failed"):
        workflow.generate(jobs, concurrency=1)

    events = [
        json.loads(line.removeprefix(workflow.MUSEFORGE_EVENT_PREFIX))
        for line in capsys.readouterr().out.splitlines()
        if line.startswith(workflow.MUSEFORGE_EVENT_PREFIX)
    ]
    by_type = {event["type"]: event for event in events}
    assert set(by_type) == {"plan", "item.saved", "item.failed"}
    assert by_type["item.saved"]["candidate_index"] == 1
    assert by_type["item.saved"]["relative_path"] == "SKU-1/单品/main/candidate-01.png"
    assert by_type["item.saved"]["cost"] == 0.04
    assert by_type["item.saved"]["elapsed"] == 1.25
    assert by_type["item.failed"]["candidate_index"] == 2
    assert by_type["item.failed"]["error"] == "synthetic failure"
