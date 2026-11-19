"""Shot timing, resumability, agy routing and persistent document acceptance."""

import json
from pathlib import Path
import subprocess
from unittest.mock import Mock
import zipfile

from fastapi.testclient import TestClient
import pytest

from library.agy_client import AgyClient, DramaSettings
from library.config import LibraryConfig
from library.course_documents import CourseDocuments
from library.drama_media import extract_shots
from library.drama_pipeline import DramaPipeline
from library import media


def shot_reply(count=3):
    return dict(
        title="人物抬头",
        observed_image_count=count,
        scene="室内木桌旁",
        characters=["黑发白衣角色"],
        composition="近景，主体居中",
        lighting="右侧暖光",
        action="角色缓慢抬头",
        camera_motion="无法确认运镜",
        image_prompt="白衣黑发人物，木桌，近景，右侧暖光",
        video_prompt="3秒，角色缓慢抬头，固定机位，保持服装与面容一致",
        editing="硬切",
    )


def summary_reply():
    card = dict(
        name="白衣角色",
        description="黑发白衣",
        prompt="白衣黑发角色参考图，正侧背三视图",
        shot_ids=["001"],
    )
    return dict(
        synopsis="人物在室内抬头。",
        visual_style="暖光写实",
        characters=[card],
        scenes=[dict(card, name="木桌房间")],
        rhythm="单个连续镜头",
        workflow=["制作角色参考图", "制作场景图", "生成动作镜头并剪辑"],
    )


@pytest.fixture
def drama(tmp_path, tiny_video):
    cfg = LibraryConfig(data_dir=tmp_path / "data")
    DramaSettings(backend="compatible").save(cfg.data_dir)
    client = Mock()
    client.transcribe.return_value = "这里没有坏。"
    client.chat_vision.side_effect = lambda *args: json.dumps(
        summary_reply() if "逐镜观察：" in args[2] else shot_reply(len(args[3])),
        ensure_ascii=False,
    )
    pipeline = DramaPipeline(cfg, client)
    item = pipeline.create(tiny_video)
    return pipeline, item, client


def test_real_cut_detection_and_long_take_sampling(tmp_path):
    video = tmp_path / "cuts.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x120:d=2:r=10",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:d=10:r=10",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(video),
        ],
        check=True,
    )
    shots = extract_shots(video, tmp_path / "frames", 12)
    assert len(shots) == 3
    assert shots[0]["start"] == 0
    assert shots[0]["end"] == pytest.approx(2, abs=0.11)
    assert shots[-1]["end"] == 12
    assert [s["subdivision"] for s in shots] == [False, True, True]
    for left, right in zip(shots, shots[1:]):
        assert left["end"] == right["start"]
    for shot in shots:
        times = [f["time"] for f in shot["frames"]]
        assert len(times) >= 3 and times == sorted(times)
        assert shot["start"] <= times[0] <= times[-1] < shot["end"]
        assert all((tmp_path / "frames" / f["file"]).is_file() for f in shot["frames"])


def test_pipeline_cache_regeneration_and_saved_export(drama):
    pipeline, item, client = drama
    pipeline.run(item["id"])
    assert pipeline.read(item["id"])["status"] == "done"
    store = CourseDocuments(pipeline.folder(item["id"]))
    doc = store.read("shots/001.md")
    assert "复现建议" in doc["markdown"] and "../assets/" in doc["markdown"]
    store.save(doc["path"], doc["markdown"] + "\n我的制作笔记。", doc["revision"])
    pipeline.run(item["id"])
    assert client.transcribe.call_count == 1
    assert client.chat_vision.call_count == 2
    pipeline.run(item["id"], regenerate=True)
    assert client.transcribe.call_count == 1
    assert client.chat_vision.call_count == 4
    with zipfile.ZipFile(pipeline.archive(item["id"])) as archive:
        assert "我的制作笔记。" in archive.read("shots/001.md").decode()
        assert any(p.startswith("assets/") for p in archive.namelist())


def test_retry_keeps_completed_shots_and_transcript(drama):
    pipeline, item, client = drama
    client.chat_vision.side_effect = [
        json.dumps(shot_reply()),
        RuntimeError("model offline"),
    ]
    with pytest.raises(RuntimeError):
        pipeline.run(item["id"])
    assert pipeline.read(item["id"])["status"] == "failed"
    assert pipeline.read(item["id"])["shots"][0]["analysis"]["title"]
    client.chat_vision.side_effect = None
    client.chat_vision.return_value = json.dumps(summary_reply())
    pipeline.run(item["id"])
    assert client.transcribe.call_count == 1
    assert client.chat_vision.call_count == 3


def test_silent_video_and_subtitle_precedence(drama, monkeypatch):
    pipeline, item, client = drama
    real_probe = media.probe
    from dataclasses import replace

    monkeypatch.setattr(
        media, "probe", lambda path: replace(real_probe(path), audio_codec=None)
    )
    pipeline.run(item["id"])
    assert not client.transcribe.called
    assert (
        "没有音轨"
        in json.loads((pipeline.folder(item["id"]) / "transcript.json").read_text())[
            "precision"
        ]
    )
    Path(item["path"]).with_suffix(".srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,500\n字幕优先。\n"
    )
    pipeline.run(item["id"])
    assert not client.transcribe.called
    assert (
        "字幕优先"
        in (pipeline.folder(item["id"]) / "generated/transcript.md").read_text()
    )


def test_bad_structure_bounded_and_no_invalid_cache(drama):
    pipeline, item, client = drama
    client.chat_vision.side_effect = None
    client.chat_vision.return_value = '{"error":"invalid"}'
    with pytest.raises(ValueError, match="连续三次"):
        pipeline.run(item["id"])
    assert client.chat_vision.call_count == 3
    assert client.transcribe.call_count == 1
    assert not list(pipeline.folder(item["id"]).rglob("shot-001.json"))
    assert len(list(pipeline.folder(item["id"]).rglob("shot-001-raw-*.txt"))) == 3


def test_agy_proxy_scoped_and_image_paths_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr("library.agy_client.shutil.which", lambda _: "/bin/agy")
    monkeypatch.setenv("HTTPS_PROXY", "http://original:1234")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, '{"color":"红色"}', ""))
    monkeypatch.setattr("library.agy_client.subprocess.run", run)
    image = tmp_path / "frame.jpg"
    reply = AgyClient(DramaSettings()).analyze("分析图片", [image], tmp_path)
    import os

    assert os.environ["HTTPS_PROXY"] == "http://original:1234"
    assert run.call_args.kwargs["env"]["HTTPS_PROXY"] == "http://127.0.0.1:7887"
    command = run.call_args.args[0]
    assert "--sandbox" in command and "--dangerously-skip-permissions" not in command
    assert str(image) in command[-1] and "read_file" in command[-1]
    assert json.loads(reply)["color"] == "红色"


def test_api_queue_documents_conflict_download_delete(drama, monkeypatch):
    from library.app import create_app, get_state

    pipeline, item, client = drama
    monkeypatch.setattr("library.worker.Worker.start", lambda self: None)
    monkeypatch.setattr("library.worker.Worker.stop", lambda self, **kwargs: None)
    with TestClient(create_app(pipeline.cfg)) as http:
        state = get_state()
        state.worker.pipeline.client = client
        state.worker.model_factory = None
        response = http.post("/api/dramas", json={"path": item["path"]})
        assert response.status_code == 200
        did = response.json()["id"]
        assert http.post(f"/api/dramas/{did}/retry").status_code == 409
        assert http.get(f"/api/dramas/{did}/download").status_code == 409
        state.worker._handle(state.lib.next_task())
        assert http.get(f"/api/dramas/{did}").json()["status"] == "done"
        doc = http.get(
            f"/api/dramas/{did}/document", params={"path": "README.md"}
        ).json()
        assert (
            http.put(
                f"/api/dramas/{did}/document?path=README.md",
                json={
                    "markdown": doc["markdown"] + "\n笔记。",
                    "revision": doc["revision"],
                },
            ).status_code
            == 200
        )
        assert (
            http.put(
                f"/api/dramas/{did}/document?path=README.md",
                json={"markdown": "旧版本", "revision": doc["revision"]},
            ).status_code
            == 409
        )
        assert http.get(f"/api/dramas/{did}/download").content.startswith(b"PK")
        assert (
            http.get(
                f"/api/dramas/{did}/source", headers={"Range": "bytes=0-15"}
            ).status_code
            == 206
        )
        assert (
            http.get(f"/api/dramas/{did}/document?path=../drama.json").status_code
            == 404
        )
        assert http.get(f"/api/dramas/{did}/frames/drama.json").status_code == 404
        assert http.get(f"/api/dramas/{did}/shots/001/diagnostics").status_code == 200
        assert http.get("/api/dramas/not-an-id").status_code == 404
        assert http.delete(f"/api/dramas/{did}").status_code == 200
        assert Path(item["path"]).exists()
        assert http.get(f"/api/dramas/{did}").status_code == 404


def test_running_delete_refused(drama, monkeypatch):
    from library.app import create_app, get_state

    pipeline, item, _ = drama
    monkeypatch.setattr("library.worker.Worker.start", lambda self: None)
    monkeypatch.setattr("library.worker.Worker.stop", lambda self, **kwargs: None)
    with TestClient(create_app(pipeline.cfg)) as http:
        state = get_state()
        task = state.lib.enqueue_task("drama", {"drama_id": item["id"]})
        state.lib.update_task(task, status="running")
        assert http.delete(f"/api/dramas/{item['id']}").status_code == 409
        assert pipeline.folder(item["id"]).exists()


def test_summary_unknown_shot_is_retried_before_cache(drama):
    pipeline, item, client = drama
    valid = summary_reply()
    invalid = summary_reply()
    invalid["scenes"][0]["shot_ids"] = ["999"]
    client.chat_vision.side_effect = [
        json.dumps(shot_reply()),
        json.dumps(invalid),
        json.dumps(valid),
    ]
    pipeline.run(item["id"])
    assert client.chat_vision.call_count == 3
    assert pipeline.read(item["id"])["documents_ready"]
    summary_path = next(pipeline.folder(item["id"]).rglob("summary-v2.json"))
    assert "999" not in summary_path.read_text()


def test_folder_import_queues_each_video_and_rejects_empty(
    tmp_path, tiny_video, monkeypatch
):
    from library.app import create_app, get_state
    import shutil

    folder = tmp_path / "input"
    folder.mkdir()
    shutil.copyfile(tiny_video, folder / "01.mp4")
    shutil.copyfile(tiny_video, folder / "02.MP4")
    (folder / "notes.txt").write_text("not a video")
    monkeypatch.setattr("library.worker.Worker.start", lambda self: None)
    monkeypatch.setattr("library.worker.Worker.stop", lambda self, **kwargs: None)
    with TestClient(create_app(LibraryConfig(data_dir=tmp_path / "data"))) as http:
        response = http.post("/api/dramas", json={"path": str(folder)})
        assert response.status_code == 200
        assert len(response.json()["imported"]) == 2
        assert len(get_state().lib.list_tasks()) == 2
        empty = tmp_path / "empty"
        empty.mkdir()
        assert http.post("/api/dramas", json={"path": str(empty)}).status_code == 400
        assert (
            http.put(
                "/api/drama-settings",
                json={"https_proxy": "http://user:secret@localhost:7887"},
            ).status_code
            == 422
        )


@pytest.mark.parametrize("legacy", [False, True])
def test_relink_moved_source_keeps_documents_cache_and_tasks(
    drama, monkeypatch, tmp_path, legacy
):
    from library.app import create_app, get_state
    from library.drama_pipeline import dump

    pipeline, item, _ = drama
    pipeline.run(item["id"])
    folder = pipeline.folder(item["id"])
    saved = pipeline.read(item["id"])
    if legacy:
        saved.pop("source_sha256")
        dump(folder / "drama.json", saved)
    store = CourseDocuments(folder)
    doc = store.read("README.md")
    store.save(doc["path"], doc["markdown"] + "\n保留人工笔记", doc["revision"])
    before = {
        p.relative_to(folder): p.read_bytes()
        for p in folder.rglob("*")
        if p.is_file() and p.name != "drama.json"
    }
    target = tmp_path / "moved-and-renamed.mp4"
    Path(item["path"]).rename(target)
    monkeypatch.setattr("library.worker.Worker.start", lambda self: None)
    monkeypatch.setattr("library.worker.Worker.stop", lambda self, **kwargs: None)
    with TestClient(create_app(pipeline.cfg)) as http:
        assert http.get(f"/api/dramas/{item['id']}").json()["source_available"] is False
        response = http.put(
            f"/api/dramas/{item['id']}/source", json={"path": str(target)}
        )
        assert response.status_code == 200
        assert response.json()["path"] == str(target.resolve())
        assert response.json()["source_available"] is True
        assert len(response.json()["source_sha256"]) == 64
        assert (
            http.get(
                f"/api/dramas/{item['id']}/source", headers={"Range": "bytes=0-15"}
            ).status_code
            == 206
        )
        assert not get_state().lib.list_tasks()
    after = {
        p.relative_to(folder): p.read_bytes()
        for p in folder.rglob("*")
        if p.is_file() and p.name != "drama.json"
    }
    assert before == after


@pytest.mark.parametrize(
    "case", ["different", "missing", "directory", "queued", "running", "permission"]
)
def test_relink_rejected_preserves_original_record(drama, monkeypatch, tmp_path, case):
    from library.app import create_app, get_state

    pipeline, item, _ = drama
    original = (pipeline.folder(item["id"]) / "drama.json").read_bytes()
    target = tmp_path / "replacement.mp4"
    target.write_bytes(b"different video")
    monkeypatch.setattr("library.worker.Worker.start", lambda self: None)
    monkeypatch.setattr("library.worker.Worker.stop", lambda self, **kwargs: None)
    with TestClient(create_app(pipeline.cfg)) as http:
        if case == "missing":
            target.unlink()
        elif case == "directory":
            target = tmp_path
        elif case in ("queued", "running"):
            state = get_state()
            task = state.lib.enqueue_task("drama", {"drama_id": item["id"]})
            state.lib.update_task(task, status=case)
        elif case == "permission":

            def denied(_):
                raise PermissionError("denied")

            monkeypatch.setattr("library.drama_api.file_sha256", denied)
        response = http.put(
            f"/api/dramas/{item['id']}/source", json={"path": str(target)}
        )
        assert response.status_code == (
            409 if case in ("different", "queued", "running") else 400
        )
    assert (pipeline.folder(item["id"]) / "drama.json").read_bytes() == original


def test_series_grouping_and_report(tmp_path, tiny_video, monkeypatch):
    from library.app import create_app

    cfg = LibraryConfig(data_dir=tmp_path / "data")
    monkeypatch.setattr("library.worker.Worker.start", lambda self: None)
    monkeypatch.setattr("library.worker.Worker.stop", lambda self, **kwargs: None)

    with TestClient(create_app(cfg)) as http:
        # 单条创建带 group_title
        r = http.post(
            "/api/dramas",
            json={
                "path": str(tiny_video),
                "group_title": "美妖姐姐合集",
                "group_id": "grp1",
            },
        )
        assert r.status_code == 200
        item = r.json()
        assert item["group_title"] == "美妖姐姐合集"

        # 批量创建
        r_batch = http.post(
            "/api/dramas/batch",
            json={
                "group_title": "美妖姐姐合集",
                "group_id": "grp1",
                "items": [{"path": str(tiny_video), "title": "第2集"}],
            },
        )
        assert r_batch.status_code == 200
        assert len(r_batch.json()["imported"]) == 1

        # 验证 report 路由
        pipeline = DramaPipeline(cfg, Mock())
        folder = pipeline.folder(item["id"])
        (folder / "report.html").write_text("<html><head></head><body><h1>Report</h1></body></html>", encoding="utf-8")
        (folder / "storyboard.jpg").write_bytes(b"fake image")

        res_report = http.get(f"/api/dramas/{item['id']}/report")
        assert res_report.status_code == 200
        assert f'<base href="/api/dramas/{item["id"]}/file/">' in res_report.text

        res_file = http.get(f"/api/dramas/{item['id']}/file/storyboard.jpg")
        assert res_file.status_code == 200
        assert res_file.content == b"fake image"


def test_rea_settings_defaults_are_portable():
    settings = DramaSettings()
    assert settings.rea_executable == "rea-video"
    assert settings.rea_toon_executable == "rea-toon"


def test_run_rea_video_builds_env_without_proxy_for_local_vlm(tmp_path, monkeypatch):
    class _Abort(Exception):
        pass

    captured = {}

    class _FakeProcess:
        stdout = iter([])
        returncode = 0

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        raise _Abort()

    monkeypatch.setattr(
        media,
        "probe",
        lambda path: media.MediaInfo(
            duration_s=float("inf"),
            width=0,
            height=0,
            video_codec="h264",
            audio_codec=None,
            container="mp4",
        ),
    )
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    settings = DramaSettings(backend="rea_video")
    pipeline = DramaPipeline(LibraryConfig(data_dir=tmp_path / "data"), Mock())
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    with pytest.raises(_Abort):
        pipeline.run_rea_video({}, tmp_path / "folder", video, settings, lambda *args: None)

    env = captured["env"]
    assert env["REA_VLM_BASE_URL"] == settings.rea_vlm_base_url
    assert env["REA_VLM_API_KEY"] == settings.rea_vlm_api_key
    assert env["REA_VLM_MODEL"] == settings.rea_vlm_model
    # 本地 VLM 不走代理
    assert "HTTPS_PROXY" not in env and "https_proxy" not in env
    assert "127.0.0.1" in env["no_proxy"]
