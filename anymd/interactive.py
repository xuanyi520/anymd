"""交互式拖拽转换界面。

用法：
  anymd                         进入交互模式
  anymd 文件.pdf                直接处理拖入的文件
  把文件拖到 anymd.cmd 图标上    同上
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from .cli import _setup_console
from .core import (
    DEFAULT_MD_DIR,
    MD_EXTS,
    TARGET_EXT,
    _norm_format,
    batch_convert,
    from_markdown,
    to_markdown,
)

BANNER = """
============================================================
  anymd 交互转换
------------------------------------------------------------
  直接把文件或文件夹拖进窗口，回车确认
  Markdown  -> 询问要转成什么格式
  其他格式  -> 默认转成 Markdown
  输入 q 退出
============================================================
"""

MD_MENU = [
    ("1", "Word 文档 (.docx)", "docx"),
    ("2", "PDF（中文排版）", "pdf"),
    ("3", "PowerPoint (.pptx)", "pptx"),
    ("4", "HTML 网页", "html"),
    ("5", "EPUB 电子书", "epub"),
    ("6", "Excel 表格 (.xlsx)", "xlsx"),
    ("7", "纯文本 (.txt)", "txt"),
    ("8", "其他格式（手动输入扩展名）", "__other__"),
    ("0", "跳过", None),
]

INPUT_MENU = [
    ("1", "转成 Markdown", "md"),
    ("2", "转成 Markdown + 元数据（标题/来源/页数）", "md_frontmatter"),
    ("3", "转成 Markdown + 抽取图片", "md_images"),
    ("4", "转成 Markdown + 视觉识别（图片/扫描页/图表）", "md_vision"),
    ("5", "转成其他格式（先转 Markdown，再转目标格式）", "other"),
    ("0", "跳过", None),
]


# ---------------------------------------------------------------- 输入输出

def _ask(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)


def _menu(title: str, options: list[tuple[str, str, object]]):
    print(f"\n{title}")
    for key, label, _value in options:
        print(f"  {key}. {label}")
    keys = "/".join(key for key, _, _ in options)
    default = "1" if any(key == "1" for key, _, _ in options) else "0"
    while True:
        raw = _ask(f"选择 [{keys}，默认 {default}]：").strip()
        if not raw:
            raw = default
        if raw.lower() in ("q", "quit", "exit"):
            return None
        for key, _label, value in options:
            if raw == key:
                return value
        print("  输入无效，请重新选择。")


def _split_paths(raw: str) -> list[str]:
    """解析拖入窗口的路径，支持带引号的多个文件。"""
    parts: list[str] = []
    buf = ""
    quote: str | None = None
    for ch in raw:
        if quote:
            if ch == quote:
                quote = None
            else:
                buf += ch
        elif ch in ('"', "'"):
            quote = ch
        elif ch.isspace():
            if buf:
                parts.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def _resolve_conflict(out: Path) -> Path | None:
    if not out.exists():
        return out
    print(f"\n输出已存在：{out}")
    print("  1. 覆盖")
    print("  2. 自动改名")
    print("  0. 取消")
    choice = _ask("选择 [1/2/0，默认 2]：").strip() or "2"
    if choice == "0":
        return None
    if choice == "1":
        return out
    for i in range(1, 1000):
        candidate = out.with_name(f"{out.stem}_{i}{out.suffix}")
        if not candidate.exists():
            return candidate
    print("  找不到可用文件名。")
    return None


def _resolve_dir(outdir: Path) -> Path | None:
    if not outdir.exists():
        return outdir
    print(f"\n输出目录已存在：{outdir}")
    print("  1. 继续（覆盖同名文件）")
    print("  2. 自动改名")
    print("  0. 取消")
    choice = _ask("选择 [1/2/0，默认 2]：").strip() or "2"
    if choice == "0":
        return None
    if choice == "1":
        return outdir
    for i in range(1, 1000):
        candidate = outdir.with_name(f"{outdir.name}_{i}")
        if not candidate.exists():
            return candidate
    return None


def _open_folder(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            print(f"  {path}")
    except Exception as exc:
        print(f"  打开失败：{exc}")


def _report(res, *, ask_open: bool = True) -> None:
    if res.ok:
        size = f"{res.chars:,} 字符" if res.format == "md" else f"{res.chars:,} 字节"
        extra = f"，{res.pages} 页" if res.pages else ""
        print(f"\n[成功] {res.source} -> {res.output}")
        print(f"     {size}{extra}，耗时 {res.elapsed}s")
        for warning in res.warnings:
            print(f"     [提示] {warning}")
        if ask_open and res.output:
            if _ask("  打开所在文件夹？(y/N)：").strip().lower() == "y":
                _open_folder(Path(res.output).parent)
    else:
        print(f"\n[失败] {res.source}")
        print(f"     {res.error}")


def _report_batch(results, outdir: Path | None) -> None:
    ok = sum(1 for r in results if r.ok)
    print(f"\n完成：{ok}/{len(results)} 成功")
    for res in results:
        if not res.ok:
            print(f"  [失败] {res.source}: {res.error}")
    if outdir is not None:
        print(f"输出目录：{outdir}")
        print(f"清单文件：{outdir / 'manifest.json'}")
        if _ask("  打开输出文件夹？(y/N)：").strip().lower() == "y":
            _open_folder(outdir)


# ---------------------------------------------------------------- 格式选择

def _choose_target() -> str | None:
    value = _menu("要转成什么格式？", MD_MENU)
    if value != "__other__":
        return value  # type: ignore[return-value]
    raw = _ask("  输入目标格式（docx/pdf/pptx/epub/odt/rtf/latex...）：").strip()
    target = _norm_format(raw.lstrip("."))
    if target not in TARGET_EXT or target in ("md", "png", "jpg"):
        print(f"  不支持的目标格式：{raw}")
        return None
    return target


# ---------------------------------------------------------------- 单文件处理

def _handle_markdown(path: Path) -> None:
    print(f"\n检测到 Markdown：{path}")
    target = _choose_target()
    if not target:
        return
    out = _resolve_conflict(path.with_suffix(TARGET_EXT[target]))
    if out is None:
        return
    toc = False
    if target in ("pdf", "html", "docx", "epub"):
        toc = _ask("  生成目录？(y/N)：").strip().lower() == "y"
    res = from_markdown(path, target, out, toc=toc)
    _report(res)


def _handle_other(path: Path) -> None:
    kind = path.suffix.upper().lstrip(".") or "文件"
    print(f"\n检测到 {kind} 文件：{path}")
    action = _menu("要怎么处理？", INPUT_MENU)
    if action is None:
        return

    if action in ("md", "md_frontmatter", "md_images", "md_vision"):
        if action == "md_vision":
            from . import vision
            ok, info = vision.vision_available()
            if not ok:
                print(f"  视觉模型不可用：{info}")
                return
            print(f"  使用视觉模型：{info}")
        DEFAULT_MD_DIR.mkdir(parents=True, exist_ok=True)
        out = _resolve_conflict(DEFAULT_MD_DIR / (path.stem + ".md"))
        if out is None:
            return
        res = to_markdown(
            path, out,
            frontmatter=(action == "md_frontmatter"),
            extract_images=(action == "md_images"),
            vision=(action == "md_vision"),
        )
        _report(res)
        return

    if action == "other":
        target = _choose_target()
        if not target:
            return
        out = _resolve_conflict(path.with_suffix(TARGET_EXT[target]))
        if out is None:
            return
        print("\n  提示：会先转成 Markdown 再转目标格式，复杂版式可能有损失。")
        _report(_two_step(path, target, out))


def _two_step(src: Path, target: str, out: Path):
    tmp = src.with_name(f".{src.stem}.anymd-{uuid.uuid4().hex[:8]}.md")
    try:
        first = to_markdown(src, tmp, frontmatter=True)
        if not first.ok:
            return first
        second = from_markdown(tmp, target, out)
        second.source = str(src)
        return second
    finally:
        tmp.unlink(missing_ok=True)


def _handle_directory(path: Path) -> None:
    files = [
        p for p in path.rglob("*")
        if p.is_file()
        and not any(part.startswith(".") for part in p.relative_to(path).parts)
    ]
    print(f"\n检测到文件夹：{path}（{len(files)} 个文件）")
    action = _menu("要怎么处理？", [
        ("1", "批量转成 Markdown（含子文件夹）", "md_rec"),
        ("2", "批量转成 Markdown（仅当前层）", "md_flat"),
        ("3", "批量转成 Markdown + 视觉识别（含子文件夹）", "md_vision"),
        ("4", "把里面的 Markdown 批量转成其他格式", "from_md"),
        ("0", "返回", None),
    ])
    if action is None:
        return

    if action in ("md_rec", "md_flat", "md_vision"):
        use_vision = action == "md_vision"
        if use_vision:
            from . import vision
            ok, info = vision.vision_available()
            if not ok:
                print(f"  视觉模型不可用：{info}")
                return
            print(f"  使用视觉模型：{info}")
        outdir = _resolve_dir(DEFAULT_MD_DIR / path.name)
        if outdir is None:
            return
        if _ask("  转换时写入 YAML 元数据？(y/N)：").strip().lower() == "y":
            frontmatter = True
        else:
            frontmatter = False
        results = batch_convert(
            path, to="md", outdir=outdir,
            recursive=(action != "md_flat"), frontmatter=frontmatter,
            vision=use_vision,
        )
        _report_batch(results, outdir)
        return

    target = _choose_target()
    if not target:
        return
    outdir = _resolve_dir(path.parent / f"{path.name}_{target}")
    if outdir is None:
        return
    results = batch_convert(path, to=target, outdir=outdir, recursive=True)
    _report_batch(results, outdir)


def _handle_url(url: str) -> None:
    print(f"\n检测到网页：{url}")
    name = _ask("  保存文件名 [默认 download.md]：").strip() or "download.md"
    DEFAULT_MD_DIR.mkdir(parents=True, exist_ok=True)
    out = DEFAULT_MD_DIR / name
    if out.suffix.lower() not in MD_EXTS:
        out = out.with_suffix(".md")
    out = _resolve_conflict(out)
    if out is None:
        return
    _report(to_markdown(url, out))


def _handle_one(path: Path) -> None:
    if path.is_dir():
        _handle_directory(path)
    elif path.suffix.lower() in MD_EXTS:
        _handle_markdown(path)
    else:
        _handle_other(path)


# ---------------------------------------------------------------- 多文件处理

def _handle_many(paths: list[Path]) -> None:
    print(f"\n检测到 {len(paths)} 个对象")
    action = _menu("要怎么处理？", [
        ("1", "逐个选择", "each"),
        ("2", "统一处理", "all"),
        ("0", "返回", None),
    ])
    if action is None:
        return
    if action == "each":
        for path in paths:
            _handle_one(path)
        return

    if any(p.is_dir() for p in paths):
        print("  统一处理不适用于文件夹，改为逐个处理。")
        for path in paths:
            _handle_one(path)
        return

    md_files = [p for p in paths if p.suffix.lower() in MD_EXTS]
    other_files = [p for p in paths if p.suffix.lower() not in MD_EXTS]

    if md_files and not other_files:
        target = _choose_target()
        if not target:
            return
        for path in md_files:
            out = _resolve_conflict(path.with_suffix(TARGET_EXT[target]))
            if out is not None:
                _report(from_markdown(path, target, out), ask_open=False)
        return

    if other_files and not md_files:
        action = _menu("统一怎么处理？", INPUT_MENU)
        if action is None:
            return
        if action == "other":
            for path in other_files:
                _handle_one(path)
            return
        DEFAULT_MD_DIR.mkdir(parents=True, exist_ok=True)
        for path in other_files:
            out = _resolve_conflict(DEFAULT_MD_DIR / (path.stem + ".md"))
            if out is not None:
                _report(
                    to_markdown(
                        path, out,
                        frontmatter=(action == "md_frontmatter"),
                        extract_images=(action == "md_images"),
                        vision=(action == "md_vision"),
                    ),
                    ask_open=False,
                )
        return

    print("  文件类型混合，改为逐个处理。")
    for path in paths:
        _handle_one(path)


# ---------------------------------------------------------------- 入口

def _handle_targets(items: list[str]) -> None:
    paths: list[Path] = []
    for item in items:
        if item.startswith(("http://", "https://")):
            _handle_url(item)
            continue
        path = Path(item).expanduser()
        if not path.exists():
            print(f"[提示] 找不到：{path}")
            continue
        paths.append(path)

    if not paths:
        return
    if len(paths) == 1:
        _handle_one(paths[0])
    else:
        _handle_many(paths)


def run(argv=None) -> int:
    _setup_console()
    print(BANNER)
    args = [str(a) for a in (sys.argv[1:] if argv is None else argv) if str(a).strip()]
    if args:
        _handle_targets(args)

    while True:
        raw = _ask("\n拖入文件/文件夹，或输入路径（q 退出）：").strip()
        if raw.lower() in ("q", "quit", "exit"):
            print("再见。")
            return 0
        if not raw:
            continue
        _handle_targets(_split_paths(raw))


if __name__ == "__main__":
    raise SystemExit(run())
