"""CLI subcommands: library serve / doctor / backup / restore."""

import logging
import sys
from pathlib import Path

import click

from library.app import create_app
from library.config import LibraryConfig, load_library_config
from library import media

logger = logging.getLogger(__name__)


def _cfg(config: Path) -> LibraryConfig:
    try:
        return load_library_config(config)
    except Exception as exc:
        raise click.ClickException(f"配置加载失败: {exc}")


@click.group("library")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default="config.yaml",
    help="配置文件路径",
)
@click.pass_context
def library_cli(ctx, config_path: Path):
    """本地绘画视频资料库。"""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


@library_cli.command()
@click.option("--host", default=None, help="覆盖监听地址")
@click.option("--port", default=None, type=int, help="覆盖端口")
@click.pass_context
def serve(ctx, host, port):
    """启动本地 Web 应用（默认 127.0.0.1）。"""
    import uvicorn

    cfg = _cfg(ctx.obj["config_path"])
    app = create_app(cfg)
    uvicorn.run(app, host=host or cfg.host, port=port or cfg.port, log_level="info")


@library_cli.command()
@click.pass_context
def doctor(ctx):
    """环境体检：ffmpeg、SQLite FTS、oMLX 连通性与模型。"""
    ok = True

    if media.has_ffmpeg():
        click.echo("✓ ffmpeg / ffprobe 可用")
    else:
        click.echo("✗ 未找到 ffmpeg/ffprobe（brew install ffmpeg）")
        ok = False

    import sqlite3
    import tempfile

    with tempfile.TemporaryDirectory():
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
            click.echo("✓ SQLite FTS5 trigram 可用")
        except sqlite3.OperationalError:
            click.echo("! trigram 不可用，中文搜索将回退 LIKE 模式")
        finally:
            conn.close()

    cfg = _cfg(ctx.obj["config_path"])
    click.echo(f"· 数据目录: {cfg.data_dir.resolve()}")
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.data_dir / ".writable"
        probe.write_text("ok")
        probe.unlink()
        click.echo("✓ 数据目录可写")
    except OSError as exc:
        click.echo(f"✗ 数据目录不可写: {exc}")
        ok = False

    from library.omlx_client import OmlxClient, OmlxError

    client = OmlxClient(
        cfg.omlx.base_url,
        cfg.omlx.effective_api_key,
        max_retries=1,
    )
    try:
        models = client.list_models()
    except OmlxError as exc:
        click.echo(f"✗ oMLX 不可达（{cfg.omlx.base_url}）: {exc}")
        click.echo("  请确认 oMLX 应用已启动，或用 OMLX_BASE_URL 覆盖地址")
        return
    finally:
        client.close()
    click.echo(f"✓ oMLX 可达（{cfg.omlx.base_url}），已加载 {len(models)} 个模型")
    for purpose, model in (
        ("语音转写", cfg.omlx.asr_model),
        ("视觉分析", cfg.omlx.vision_model),
    ):
        if model in models:
            click.echo(f"✓ {purpose}模型已加载: {model}")
        else:
            click.echo(f"! {purpose}模型未加载: {model}（请在 oMLX 中添加）")
    sys.exit(0 if ok else 1)


@library_cli.command()
@click.argument("out_zip", type=click.Path(path_type=Path), required=False)
@click.pass_context
def backup(ctx, out_zip):
    """备份资料库（数据库 + 截图），不含原视频。"""
    from library.backup import create_backup

    cfg = _cfg(ctx.obj["config_path"])
    path = create_backup(cfg, out_zip)
    click.echo(f"备份完成: {path}")


@library_cli.command()
@click.argument("zip_path", type=click.Path(exists=True, path_type=Path))
@click.option("--old-root", default=None, help="原素材根目录（跨机器迁移时）")
@click.option("--new-root", default=None, help="新素材根目录")
@click.pass_context
def restore(ctx, zip_path, old_root, new_root):
    """从备份恢复；可映射旧根目录到新根目录。"""
    from library.backup import restore_backup

    cfg = _cfg(ctx.obj["config_path"])
    if bool(old_root) != bool(new_root):
        raise click.ClickException("--old-root 与 --new-root 需成对使用")
    summary = restore_backup(cfg, zip_path, old_root, new_root)
    click.echo(
        f"恢复完成: {summary['videos']} 个视频, "
        f"{summary['missing_files']} 个文件位置不可用（记录已保留）"
    )


@library_cli.command()
@click.pass_context
def rescan(ctx):
    """同步扫描所有已注册文件夹（不启动服务）。"""
    from library.db import Library
    from library.scanner import scan_all_folders

    logging.basicConfig(level=logging.INFO)
    cfg = _cfg(ctx.obj["config_path"])
    lib = Library(cfg.db_path)
    try:
        results = scan_all_folders(lib, cfg, enqueue_analysis=False)
        for fid, r in results.items():
            click.echo(f"folder#{fid}: {r.summary()}")
    finally:
        lib.close()
