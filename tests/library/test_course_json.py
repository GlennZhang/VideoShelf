import pytest
from library.course_pipeline import parse_course_notes
from library.omlx_client import OmlxError


def test_complete_reply_with_unescaped_quotes_preserves_steps():
    reply = """{"steps":[{"title":"视频生成", "detail":"使用视频生成工具", "segment":0,"evidence":"视频生成", "tips":["使用"ComfyUI"生成视频"]}]}"""
    notes = parse_course_notes(reply)
    assert len(notes.steps) == 1
    assert "ComfyUI" in notes.steps[0].tips[0]
    assert notes.steps[0].evidence == "视频生成"


def test_incomplete_reply_is_not_fabricated():
    with pytest.raises(OmlxError):
        parse_course_notes('{"steps":[{"title":"视频生成')
