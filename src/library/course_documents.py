"""Persistent user Markdown, separate from disposable inference/export caches."""

import hashlib
import json
import re
import shutil
import threading
from pathlib import Path
from uuid import uuid4

DOCUMENT_LOCK = threading.RLock()


class DocumentConflict(ValueError):
    pass


def revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def lesson_diagnostics(notes: dict) -> dict:
    """Keep routine recognition traces available, but out of lesson prose."""
    reviews = list(notes.get("batch_reviews", [])) + [
        {
            "title": step["title"],
            "message": "引用未能与原文逐字对应，该条未放入教程正文。模型引用："
            + step["evidence"],
            "start": step["start"],
        }
        for step in notes.get("unverified", [])
    ]
    sources = []
    for step in notes["steps"]:
        visual = step["visual"]
        sources.append(
            {
                k: step.get(k)
                for k in ("title", "start", "end", "evidence", "image_time")
            }
        )
        messages = [visual["uncertainty"]] + [
            t for t in step["tips"] if t.startswith("待核对")
        ]
        for message in messages:
            for clause in re.split(r"[；;\n]", message):
                if re.search(
                    r"截断|不完整|模糊|遮挡|无法辨认|看不清|未展开|参数.*(?:不确定|核对)|版本.*核对|(?:缺少|缺失).*(?:末尾|开头|字符|参数值)",
                    clause,
                ):
                    reviews.append(
                        {
                            "title": step["title"],
                            "message": clause,
                            "start": step["start"],
                        }
                    )
    return {"reviews": reviews, "sources": sources, "precision": notes["precision"]}


class CourseDocuments:
    def __init__(self, folder: Path):
        self.folder = folder
        self.root = folder / "documents"
        self.generated = folder / "generated"
        self.state_file = folder / "document-state.json"

    def _state(self) -> dict:
        return (
            json.loads(self.state_file.read_text(encoding="utf-8"))
            if self.state_file.exists()
            else {}
        )

    def _path(self, path: str, generated: bool = False) -> Path:
        root = (self.generated if generated else self.root).resolve()
        target = (root / path).resolve()
        if (
            not target.is_relative_to(root)
            or target.suffix.lower() != ".md"
            or not target.is_file()
        ):
            raise FileNotFoundError("文档不存在")
        return target

    def sync_generated(self, staged: Path) -> None:
        """Seed missing working copies; never replace an existing working document."""
        with DOCUMENT_LOCK:
            state = self._state()
            # Assets are named by content; older edited Markdown keeps its images.
            for asset in (staged / "assets").glob("*"):
                destination = self.root / "assets" / asset.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    shutil.copyfile(asset, destination)
            for source in staged.rglob("*.md"):
                path = source.relative_to(staged).as_posix()
                text = source.read_text(encoding="utf-8")
                destination = self.root / path
                if not destination.exists():
                    write_text(destination, text)
                    state[path] = {"base": revision(text)}
            if self.generated.exists():
                shutil.rmtree(self.generated)
            shutil.copytree(staged, self.generated)
            write_text(self.state_file, json.dumps(state, ensure_ascii=False, indent=2))

    def list(self) -> list[dict]:
        with DOCUMENT_LOCK:
            result = []
            for path in sorted(self.root.rglob("*.md")):
                relative = path.relative_to(self.root).as_posix()
                item = self.read(relative)
                result.append(
                    {k: item[k] for k in ("path", "title", "revision", "has_new_draft")}
                )
            return result

    def read(self, path: str, generated: bool = False) -> dict:
        with DOCUMENT_LOCK:
            text = self._path(path, generated).read_text(encoding="utf-8")
            state = self._state()
            draft = self.generated / path
            has_new_draft = draft.is_file() and revision(
                draft.read_text(encoding="utf-8")
            ) != state.get(path, {}).get("base")
            clean_lines = [l.strip() for l in text.splitlines() if l.strip()]
            if clean_lines and clean_lines[0] == "---":
                try:
                    end_idx = clean_lines.index("---", 1)
                    clean_lines = clean_lines[end_idx + 1:]
                except ValueError:
                    pass
            title = next(
                (
                    line.lstrip("# ").strip()
                    for line in clean_lines
                    if line
                ),
                Path(path).stem,
            )
            return {
                "path": path,
                "title": title,
                "markdown": text,
                "revision": revision(text),
                "has_new_draft": bool(has_new_draft),
            }

    def save(self, path: str, text: str, expected: str) -> dict:
        with DOCUMENT_LOCK:
            target = self._path(path)
            current = target.read_text(encoding="utf-8")
            if revision(current) != expected:
                raise DocumentConflict(
                    "文档已被其他页面修改。当前编辑内容仍保留，请先比较最新版本。"
                )
            write_text(target, text)
            return self.read(path)

    def adopt(self, path: str, expected: str, draft_revision: str) -> dict:
        with DOCUMENT_LOCK:
            draft = self.read(path, generated=True)
            if draft["revision"] != draft_revision:
                raise DocumentConflict("AI 新稿已更新，请重新查看后采用。")
            self.save(path, draft["markdown"], expected)
            state = self._state()
            state[path] = {"base": draft["revision"]}
            write_text(self.state_file, json.dumps(state, ensure_ascii=False, indent=2))
            return self.read(path)

    def export_to(self, destination: Path) -> None:
        with DOCUMENT_LOCK:
            for source in self.root.rglob("*"):
                if source.is_file() and not source.name.endswith(".tmp"):
                    path = destination / source.relative_to(self.root)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, path)
