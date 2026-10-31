"""核心转换引擎。

入向（任意格式 -> Markdown）：
  - PDF       : pymupdf4llm（保留标题层级/表格），效果不佳时回退 markitdown
  - 其余格式  : markitdown（Word/PPT/Excel/HTML/EPUB/CSV/JSON/图片/URL...）

出向（Markdown -> 任意格式）：
  - docx/html/epub/pptx/odt/rtf/latex/rst/... : pandoc
  - pdf      : pandoc 生成 HTML，再交给 Edge 无头打印（本机无 LaTeX）
  - xlsx     : 解析 Markdown 表格后用 openpyxl 生成
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 常量

MD_EXTS = {".md", ".markdown", ".mdown", ".mkd", ".mkdn"}

# 所有“其他格式 -> Markdown”的默认输出目录
DEFAULT_MD_DIR = Path(
    os.environ.get("ANYMD_OUTPUT_DIR")
    or (Path(__file__).resolve().parent.parent / "md输出")
).expanduser()
TEXT_EXTS = {
    ".txt", ".text", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".xml", ".ini", ".cfg", ".conf", ".toml", ".py", ".js", ".ts", ".css",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}

FORMAT_ALIASES = {
    "md": "md", "markdown": "md",
    "docx": "docx", "word": "docx", "doc": "docx",
    "html": "html", "htm": "html",
    "pdf": "pdf",
    "pptx": "pptx", "ppt": "pptx", "powerpoint": "pptx",
    "xlsx": "xlsx", "excel": "xlsx", "xls": "xlsx",
    "epub": "epub",
    "latex": "latex", "tex": "latex",
    "rst": "rst",
    "odt": "odt",
    "rtf": "rtf",
    "org": "org",
    "txt": "txt", "text": "txt",
    "json": "json",
    "mediawiki": "mediawiki",
    "asciidoc": "asciidoc",
    "png": "png", "jpg": "jpg", "jpeg": "jpg",
}

PANDOC_OUT = {
    "html": "html5",
    "docx": "docx",
    "pptx": "pptx",
    "epub": "epub3",
    "latex": "latex",
    "rst": "rst",
    "odt": "odt",
    "rtf": "rtf",
    "txt": "plain",
    "json": "json",
    "org": "org",
    "mediawiki": "mediawiki",
    "asciidoc": "asciidoc",
}

TARGET_EXT = {
    "md": ".md", "docx": ".docx", "html": ".html", "pdf": ".pdf",
    "pptx": ".pptx", "xlsx": ".xlsx", "epub": ".epub", "latex": ".tex",
    "rst": ".rst", "odt": ".odt", "rtf": ".rtf", "org": ".org",
    "txt": ".txt", "json": ".json", "mediawiki": ".md", "asciidoc": ".adoc",
    "png": ".png", "jpg": ".jpg",
}

MD_READER = (
    "markdown+pipe_tables+task_lists+strikeout+footnotes+fenced_divs"
    "+tex_math_dollars+yaml_metadata_block+raw_html"
)

PRINT_CSS = """
@page { size: A4; margin: 22mm 18mm; }
html { -webkit-print-color-adjust: exact; }
body {
  font-family: "Source Han Serif SC","Noto Serif CJK SC","SimSun","Songti SC",
               "Microsoft YaHei",-apple-system,"Segoe UI",sans-serif;
  font-size: 11.5pt; line-height: 1.75; color: #1a1a1a; margin: 0;
}
h1,h2,h3,h4,h5 { font-family: "Microsoft YaHei","PingFang SC","SimHei",sans-serif;
  line-height: 1.35; margin: 1.2em 0 .5em; page-break-after: avoid; }
h1 { font-size: 22pt; border-bottom: 2px solid #333; padding-bottom: .3em; }
h2 { font-size: 16pt; border-bottom: 1px solid #ddd; padding-bottom: .2em; }
h3 { font-size: 13.5pt; }
p { margin: .6em 0; text-align: justify; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 10pt;
  page-break-inside: avoid; }
th,td { border: 1px solid #bbb; padding: 6px 8px; vertical-align: top; }
th { background: #f2f2f2; font-weight: 600; }
tr:nth-child(even) td { background: #fafafa; }
code { font-family: Consolas,"Courier New",monospace; background:#f5f5f5;
  padding:1px 4px; border-radius:3px; font-size: .92em; }
pre { background:#f6f8fa; padding:12px; border-radius:6px; white-space:pre-wrap;
  overflow-wrap:anywhere; line-height:1.5; }
pre code { background:none; padding:0; }
blockquote { border-left:4px solid #ccc; margin:1em 0; padding-left:1em; color:#555; }
img { max-width:100%; }
ul,ol { padding-left: 1.6em; }
hr { border:none; border-top:1px solid #ddd; margin:1.5em 0; }
a { color:#0b57d0; text-decoration:none; }
"""


# ---------------------------------------------------------------- 结果对象

@dataclass
class ConvertResult:
    source: str
    output: str | None = None
    format: str = ""
    engine: str = ""
    chars: int = 0
    pages: int | None = None
    elapsed: float = 0.0
    ok: bool = True
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- 格式化辅助

def _norm_format(value: str) -> str:
    key = (value or "").strip().lower().lstrip(".")
    return FORMAT_ALIASES.get(key, key)


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _read_text(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _unique_output(path: Path) -> Path:
    """默认输出目录里遇到同名文件时自动加 _1、_2，不覆盖。"""
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def _guess_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        m = re.match(r"^#\s+(.+?)\s*#*\s*$", line)
        if m:
            return m.group(1).strip()
    return Path(fallback).stem if not _is_url(fallback) else fallback


def _frontmatter_block(source: str, engine: str, markdown: str,
                       pages: int | None = None) -> str:
    title = _guess_title(markdown, source)
    items: list[tuple[str, str | int]] = [
        ("title", title),
        ("source", source),
        ("type", Path(source).suffix.lower().lstrip(".") or "url"),
        ("engine", engine),
        ("converted_at", datetime.now().astimezone().isoformat(timespec="seconds")),
    ]
    if pages:
        items.append(("pages", pages))
    lines = ["---"]
    for key, value in items:
        if isinstance(value, int):
            lines.append(f"{key}: {value}")
        else:
            # YAML 双引号串里反斜杠是转义符，Windows 路径必须改成正斜杠
            safe = (str(value).replace("\\", "/").replace('"', "'")
                    .replace("\n", " ").replace("\r", " "))
            lines.append(f'{key}: "{safe}"')
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _clean_markdown(text: str, *, join_lines: bool = False) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    if join_lines:
        text = _join_wrapped_lines(text)
    return text.strip() + "\n"


def _join_wrapped_lines(text: str) -> str:
    """把中文/英文段落里被硬换行切断的行接回去（保守策略）。"""
    out: list[str] = []
    protected = ("#", ">", "|", "-", "*", "+", "`", "```", "    ")
    for line in text.split("\n"):
        if not out or not line:
            out.append(line)
            continue
        prev = out[-1]
        prev_stripped = prev.strip()
        cur_stripped = line.strip()
        prev_is_special = (
            not prev_stripped
            or prev_stripped.startswith(protected)
            or bool(re.match(r"^\d+[.)]\s", prev_stripped))
        )
        cur_is_special = cur_stripped.startswith(protected) or bool(
            re.match(r"^\d+[.)]\s", cur_stripped)
        )
        if prev_is_special or cur_is_special:
            out.append(line)
            continue
        # 上一行以句末标点结尾，视为自然段结束
        if prev_stripped and prev_stripped[-1] in "。！？.!?:：；;":
            out.append(line)
            continue
        prev_cjk = bool(re.search(r"[\u4e00-\u9fff]$", prev_stripped))
        cur_cjk = bool(re.search(r"^[\u4e00-\u9fff]", cur_stripped))
        joiner = "" if (prev_cjk or cur_cjk) else " "
        out[-1] = prev.rstrip() + joiner + cur_stripped
    return "\n".join(out)


def _page_count(path: str) -> int | None:
    try:
        import pymupdf  # type: ignore
        with pymupdf.open(path) as doc:
            return doc.page_count
    except Exception:
        try:
            import fitz  # type: ignore
            with fitz.open(path) as doc:
                return doc.page_count
        except Exception:
            return None


# ---------------------------------------------------------------- 入向引擎

def _pdf_to_markdown(path: Path, *, extract_images: bool,
                     image_dir: Path | None) -> tuple[str, list[str]]:
    warnings: list[str] = []
    # pymupdf 会执行 `tesseract --list-langs` 探测语言包；中文 Windows 下该命令
    # 输出 GBK，在 UTF-8 模式下会让 subprocess 的 reader 线程抛 UnicodeDecodeError。
    # 本工具不做 OCR，设一个占位值即可跳过探测。
    os.environ.setdefault("TESSDATA_PREFIX", str(Path(__file__).resolve().parent))
    import pymupdf4llm  # 懒加载

    # use_ocr=False：本工具不内置 OCR，关掉它可以避免 pymupdf 探测 Tesseract
    # 并往 stdout 打印消息（MCP 的 stdout 必须是干净协议流）。
    kwargs: dict = {"show_progress": False, "use_ocr": False}
    if extract_images and image_dir is not None:
        image_dir.mkdir(parents=True, exist_ok=True)
        kwargs.update(write_images=True, image_path=str(image_dir))
    try:
        text = pymupdf4llm.to_markdown(str(path), **kwargs)
    except TypeError:
        # 不同版本参数名可能略有差异，退回最简调用
        text = pymupdf4llm.to_markdown(str(path))

    if extract_images and image_dir is not None:
        # 把绝对路径改写成相对路径，方便 md 单独搬走
        text = text.replace(str(image_dir).replace("\\", "/") + "/", "assets/")
        text = text.replace(str(image_dir) + "\\", "assets/")

    pages = _page_count(str(path))
    dense = len(re.sub(r"\s", "", text or ""))
    if pages and dense < max(30, pages * 20):
        warnings.append(
            f"正文只抽到 {dense} 个字符（{pages} 页），疑似扫描版 PDF；"
            "需要 OCR 或改用 --engine markitdown"
        )
    return text or "", warnings


def _markitdown_to_markdown(source: str) -> str:
    from markitdown import MarkItDown  # 懒加载

    converter = MarkItDown(enable_plugins=False)
    return converter.convert(source).text_content or ""


def _doc_to_docx(path: Path, workdir: Path) -> Path:
    """老式 .doc 先用本机 Word 另存为 .docx（需要 pywin32 + Word）。"""
    target = workdir / (path.stem + ".docx")
    try:
        import win32com.client  # type: ignore
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        try:
            doc = word.Documents.Open(str(path.resolve()), ReadOnly=True)
            doc.SaveAs2(str(target.resolve()), FileFormat=16)  # wdFormatXMLDocument
            doc.Close(False)
        finally:
            word.Quit()
        return target
    except Exception as exc:
        raise RuntimeError(
            "读取 .doc 需要本机安装 Word 或 LibreOffice；"
            "也可以先手动另存为 .docx。"
        ) from exc


@dataclass
class RenderResult:
    text: str
    engine: str
    pages: int | None = None
    warnings: list[str] = field(default_factory=list)


def _append_vision_images(text: str, src: Path, image_dir: Path | None, *,
                          mode: str, model: str | None,
                          warnings: list[str] | None = None) -> str:
    """抽取文档内嵌图片，逐张交给视觉模型，追加到 Markdown 末尾。"""
    if image_dir is None:
        return text
    from . import vision as vision_mod

    try:
        images = vision_mod.extract_images(src, image_dir)
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"内嵌图片提取失败：{exc}")
        return text
    if not images:
        return text

    described = vision_mod.describe_images(images, mode=mode, model=model)
    parts = ["\n\n## 图片描述\n"]
    for index, (image, desc) in enumerate(described, start=1):
        rel = f"assets/{image_dir.name}/{image.name}"
        parts.append(
            f"\n### 图片 {index}（{image.name}）\n\n"
            f"![{image.name}]({rel})\n\n{desc.strip()}\n"
        )
    if len(images) > len(described):
        parts.append(f"\n> 图片较多，只识别了前 {len(described)} 张。\n")
    return text.rstrip() + "\n" + "".join(parts)


def render_markdown(
    source: str | os.PathLike,
    *,
    engine: str = "auto",
    extract_images: bool = False,
    join_lines: bool = False,
    image_dir: Path | None = None,
    vision: bool = False,
    vision_mode: str = "auto",
    vision_model: str | None = None,
    vision_max_pages: int = 50,
) -> RenderResult:
    """只把文档渲染成 Markdown 文本，不落盘（MCP 和 to_markdown 共用）。"""
    import tempfile

    src = str(source)
    warnings: list[str] = []
    workdir: Path | None = None
    try:
        if _is_url(src):
            text = _markitdown_to_markdown(src)
            return RenderResult(_clean_markdown(text, join_lines=join_lines),
                                "markitdown")

        path = Path(src).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"文件不存在：{path}")
        if path.is_dir():
            raise IsADirectoryError(f"这是目录，请用 batch 命令：{path}")
        ext = path.suffix.lower()
        pages: int | None = None

        if ext in MD_EXTS:
            text, used_engine = _read_text(path), "copy"
        elif ext in IMAGE_EXTS and vision:
            from . import vision as vision_mod
            text = vision_mod.describe_image(
                path, mode=vision_mode, model=vision_model)
            text = f"# 图片：{path.name}\n\n{text.strip()}\n"
            used_engine = "vision"
        elif ext == ".pdf" and engine in ("auto", "pymupdf", "pymupdf4llm"):
            pages = _page_count(src)
            text, pdf_warnings = _pdf_to_markdown(
                path, extract_images=extract_images, image_dir=image_dir)
            warnings.extend(pdf_warnings)
            used_engine = "pymupdf4llm"
            dense = len(re.sub(r"\s", "", text))
            if vision and dense < max(30, (pages or 1) * 20):
                from . import vision as vision_mod
                # 已经走视觉识别了，去掉“需要 OCR”的旧警告
                warnings[:] = [
                    w for w in warnings if "疑似扫描版" not in w
                ]
                warnings.append(
                    f"检测到扫描版 PDF，改用视觉模型逐页识别"
                    f"（最多 {vision_max_pages} 页）"
                )
                text = vision_mod.pdf_pages_markdown(
                    path, max_pages=vision_max_pages,
                    model=vision_model, mode=vision_mode,
                )
                used_engine = "vision"
            elif dense < 20:
                warnings.append("PDF 未抽出文本，回退到 markitdown")
                text = _markitdown_to_markdown(str(path))
                used_engine = "markitdown"
                if vision:
                    text = _append_vision_images(
                        text, path, image_dir, mode=vision_mode,
                        model=vision_model, warnings=warnings)
                    used_engine += "+vision"
            elif vision:
                text = _append_vision_images(
                    text, path, image_dir, mode=vision_mode,
                    model=vision_model, warnings=warnings)
                used_engine += "+vision"
        elif ext == ".doc":
            workdir = Path(tempfile.mkdtemp(prefix=".anymd_", dir=path.parent))
            converted = _doc_to_docx(path, workdir)
            text = _markitdown_to_markdown(str(converted))
            used_engine = "markitdown+word"
        elif engine in ("auto", "markitdown"):
            text = _markitdown_to_markdown(str(path))
            used_engine = "markitdown"
            if ext in IMAGE_EXTS and len(re.sub(r"\s", "", text)) < 10:
                warnings.append(
                    "图片未识别出文字；可加 --vision 使用视觉模型，"
                    "或改用 OCR 引擎"
                )
            if vision and ext in (".docx", ".pptx"):
                text = _append_vision_images(
                    text, path, image_dir, mode=vision_mode,
                    model=vision_model, warnings=warnings)
                used_engine += "+vision"
        else:
            raise ValueError(f"不支持的入向引擎：{engine}")

        return RenderResult(
            _clean_markdown(text, join_lines=join_lines),
            used_engine, pages, warnings,
        )
    finally:
        if workdir is not None and workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)


def to_markdown(
    source: str | os.PathLike,
    output: str | os.PathLike | None = None,
    *,
    engine: str = "auto",
    frontmatter: bool = False,
    extract_images: bool = False,
    join_lines: bool = False,
    overwrite: bool = True,
    vision: bool = False,
    vision_mode: str = "auto",
    vision_model: str | None = None,
    vision_max_pages: int = 50,
) -> ConvertResult:
    """把任意受支持的文件/URL 转成 Markdown 并写出。"""
    started = time.perf_counter()
    src = str(source)
    result = ConvertResult(source=src, format="md")

    try:
        if _is_url(src):
            if output:
                out_path = Path(output)
                if out_path.suffix.lower() not in MD_EXTS:
                    out_path = out_path.with_suffix(".md")
            else:
                DEFAULT_MD_DIR.mkdir(parents=True, exist_ok=True)
                out_path = _unique_output(DEFAULT_MD_DIR / "download.md")
            image_dir = None
        else:
            path = Path(src).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"文件不存在：{path}")
            if path.is_dir():
                raise IsADirectoryError(f"这是目录，请用 batch 命令：{path}")

            if output is None:
                DEFAULT_MD_DIR.mkdir(parents=True, exist_ok=True)
                out_path = _unique_output(
                    DEFAULT_MD_DIR / (path.stem + ".md"))
            else:
                out_path = Path(output).expanduser()
                if out_path.exists() and out_path.is_dir():
                    out_path = out_path / (path.stem + ".md")
                elif str(output).endswith(("/", "\\")):
                    out_path.mkdir(parents=True, exist_ok=True)
                    out_path = out_path / (path.stem + ".md")
                elif out_path.suffix.lower() not in MD_EXTS:
                    out_path = out_path.with_suffix(".md")

            if out_path.resolve() == path.resolve():
                out_path = path.with_name(path.stem + ".converted.md")
            image_dir = (
                out_path.parent / "assets" / path.stem
                if (extract_images or vision) else None
            )

        rendered = render_markdown(
            src, engine=engine, extract_images=extract_images,
            join_lines=join_lines, image_dir=image_dir,
            vision=vision, vision_mode=vision_mode,
            vision_model=vision_model, vision_max_pages=vision_max_pages,
        )
        text = rendered.text
        if frontmatter:
            text = _frontmatter_block(src, rendered.engine, text, rendered.pages) + text

        if not overwrite and out_path.exists():
            result.ok = False
            result.error = f"输出已存在（未加 --overwrite）：{out_path}"
        else:
            _write_text(out_path, text)

        result.output = str(out_path)
        result.engine = rendered.engine
        result.chars = len(text)
        result.pages = rendered.pages
        result.warnings = rendered.warnings
    except Exception as exc:
        result.ok = False
        result.error = f"{exc}"
    finally:
        result.elapsed = round(time.perf_counter() - started, 3)
    return result


# ---------------------------------------------------------------- 出向引擎

def _find_pandoc() -> str:
    exe = shutil.which("pandoc")
    if not exe:
        raise RuntimeError("未找到 pandoc，请先安装：https://pandoc.org/installing.html")
    return exe


def _find_edge() -> str | None:
    candidates = [
        os.environ.get("EDGE_PATH"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("msedge"),
    ]
    for item in candidates:
        if item and Path(item).exists():
            return item
    return None


def _frontmatter_title(text: str) -> str | None:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return None
    for line in m.group(1).splitlines():
        if line.startswith("title:"):
            return line[6:].strip().strip("\"'")
    return None


def _strip_frontmatter(text: str) -> str:
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S)


def _metadata_args(text: str, src: Path,
                   removed_title: str | None = None) -> list[str]:
    """避免 frontmatter 标题与正文首个 H1 在 docx/pptx/pdf 里重复。"""
    if removed_title:
        # pagetitle 只影响 HTML 的 <title>，不会渲染成正文标题
        return [f"--metadata=pagetitle:{removed_title}"]
    title = _frontmatter_title(text)
    if title:
        return []  # 交给 frontmatter 自己生效
    return [f"--metadata=title:{src.stem}"]


def _pandoc_input(src: Path, text: str) -> tuple[Path, Path | None, str | None]:
    """若 frontmatter title 与正文首个 H1 相同，生成去掉 title 字段的临时文件。

    返回 (输入文件, 需清理的临时文件或 None, 被移除的标题)。
    """
    title = _frontmatter_title(text)
    if not title:
        return src, None, None
    body = _strip_frontmatter(text)
    first = next((ln for ln in body.splitlines() if ln.strip()), "")
    m = re.match(r"^#\s+(.+?)\s*$", first)
    if not (m and m.group(1).strip() == title.strip()):
        return src, None, None

    def repl(match: re.Match) -> str:
        kept = [
            ln for ln in match.group(1).splitlines()
            if not ln.strip().startswith("title:")
        ]
        return ("---\n" + "\n".join(kept) + "\n---\n\n") if kept else ""

    new_text = re.sub(
        r"^---\s*\n(.*?)\n---\s*\n", repl, text, count=1, flags=re.S)
    tmp = src.parent / f".{src.stem}.anymd-{uuid.uuid4().hex[:8]}.md"
    tmp.write_text(new_text, encoding="utf-8")
    return tmp, tmp, title


def _resolve_out(source: Path, output, ext: str) -> Path:
    if output is None:
        return source.with_suffix(ext)
    out = Path(output).expanduser()
    if out.exists() and out.is_dir():
        return out / (source.stem + ext)
    if str(output).endswith(("/", "\\")):
        return out / (source.stem + ext)
    if not out.suffix:
        return out.with_suffix(ext)
    return out


def _run(cmd: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )


def _pandoc_convert(src: Path, dst: Path, target: str, *,
                    toc: bool, reference_doc: str | None,
                    standalone: bool) -> None:
    pandoc = _find_pandoc()
    text = _read_text(src)
    pandoc_src, cleanup, removed = _pandoc_input(src, text)
    try:
        cmd = [pandoc, str(pandoc_src), "-f", MD_READER, "-t",
               PANDOC_OUT[target], "-o", str(dst)]
        if target == "html" and standalone:
            cmd.append("-s")
        if toc and target in ("html", "docx", "pdf", "epub", "odt", "latex"):
            cmd.append("--toc")
        if reference_doc and target in ("docx", "odt", "pptx"):
            cmd.append(f"--reference-doc={reference_doc}")
        cmd.extend(_metadata_args(text, src, removed))
        if target in ("docx", "odt"):
            cmd.append("--metadata=lang:zh-CN")
        cmd.append(f"--resource-path={src.parent}")
        proc = _run(cmd)
        if proc.returncode != 0 or not dst.exists():
            raise RuntimeError(
                f"pandoc 转换失败：{(proc.stderr or proc.stdout).strip()[:800]}")
    finally:
        if cleanup is not None:
            cleanup.unlink(missing_ok=True)


def _md_to_pdf(src: Path, dst: Path, *, toc: bool,
               pdf_engine: str = "auto") -> str:
    """Markdown -> PDF。优先 Edge 无头打印，其次 Word。"""
    order = ["edge", "word"] if pdf_engine == "auto" else [pdf_engine]
    errors: list[str] = []
    for eng in order:
        try:
            if eng == "edge":
                _md_to_pdf_edge(src, dst, toc=toc)
                return "edge"
            if eng == "word":
                _md_to_pdf_word(src, dst, toc=toc)
                return "word"
        except Exception as exc:
            errors.append(f"{eng}: {exc}")
    raise RuntimeError("PDF 生成失败；" + "；".join(errors))


def _md_to_pdf_edge(src: Path, dst: Path, *, toc: bool) -> None:
    edge = _find_edge()
    if not edge:
        raise RuntimeError("未找到 Microsoft Edge")
    pandoc = _find_pandoc()
    html_path = dst.with_name(f".{dst.stem}.anymd.tmp.html")
    css_path = dst.with_name(f".{dst.stem}.anymd.tmp.css")
    text = _read_text(src)
    pandoc_src, cleanup, removed = _pandoc_input(src, text)
    try:
        _write_text(css_path, PRINT_CSS)
        cmd = [
            pandoc, str(pandoc_src), "-f", MD_READER, "-t", "html5", "-s",
            "--embed-resources", "-o", str(html_path),
            f"--css={css_path}", f"--resource-path={src.parent}",
        ]
        if toc:
            cmd.append("--toc")
        cmd.extend(_metadata_args(text, src, removed))
        proc = _run(cmd)
        if proc.returncode != 0 or not html_path.exists():
            raise RuntimeError(f"HTML 生成失败：{(proc.stderr or proc.stdout).strip()[:600]}")

        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        proc = _run([
            edge, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
            f"--print-to-pdf={dst}", html_path.resolve().as_uri(),
        ], timeout=120)
        # Edge 有时先退出、文件稍后落盘
        for _ in range(20):
            if dst.exists() and dst.stat().st_size > 0:
                return
            time.sleep(0.2)
        raise RuntimeError(
            f"Edge 未生成 PDF：{(proc.stderr or proc.stdout).strip()[:400]}"
        )
    finally:
        html_path.unlink(missing_ok=True)
        css_path.unlink(missing_ok=True)
        if cleanup is not None:
            cleanup.unlink(missing_ok=True)


def _md_to_pdf_word(src: Path, dst: Path, *, toc: bool) -> None:
    import win32com.client  # type: ignore
    tmp_docx = dst.with_name(f".{dst.stem}.anymd.tmp.docx")
    try:
        _pandoc_convert(src, tmp_docx, "docx", toc=toc,
                        reference_doc=None, standalone=True)
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        try:
            doc = word.Documents.Open(str(tmp_docx.resolve()))
            doc.SaveAs2(str(dst.resolve()), FileFormat=17)  # wdFormatPDF
            doc.Close(False)
        finally:
            word.Quit()
    finally:
        tmp_docx.unlink(missing_ok=True)


def _md_to_xlsx(src: Path, dst: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    text = _strip_frontmatter(_read_text(src))
    tables = _extract_pipe_tables(text)

    wb = Workbook()
    if not tables:
        ws = wb.active
        ws.title = "Content"
        for r, line in enumerate(text.splitlines(), start=1):
            ws.cell(row=r, column=1, value=line)
        ws.column_dimensions["A"].width = 100
    else:
        wb.remove(wb.active)
        for idx, rows in enumerate(tables, start=1):
            ws = wb.create_sheet(title=f"Table{idx}")
            for r, row in enumerate(rows, start=1):
                for c, cell in enumerate(row, start=1):
                    ws.cell(row=r, column=c, value=cell)
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="F2F2F2")
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            ws.freeze_panes = "A2"
            for c in range(1, len(rows[0]) + 1):
                longest = max((len(str(r[c - 1])) if c - 1 < len(r) else 0) for r in rows)
                ws.column_dimensions[get_column_letter(c)].width = min(max(10, longest + 2), 60)
    dst.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dst)


def _extract_pipe_tables(text: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    block: list[str] = []

    def flush() -> None:
        if len(block) >= 2:
            rows = []
            for i, line in enumerate(block):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if i == 1 and all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells):
                    continue  # 分隔行
                rows.append(cells)
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                tables.append(rows)
        block.clear()

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            block.append(s)
        else:
            flush()
    flush()
    return tables


def from_markdown(
    source: str | os.PathLike,
    to: str,
    output: str | os.PathLike | None = None,
    *,
    toc: bool = False,
    reference_doc: str | os.PathLike | None = None,
    pdf_engine: str = "auto",
    overwrite: bool = True,
) -> ConvertResult:
    """把 Markdown 转成目标格式。"""
    started = time.perf_counter()
    src = Path(str(source)).expanduser()
    target = _norm_format(to)
    result = ConvertResult(source=str(src), format=target)

    try:
        if not src.exists():
            raise FileNotFoundError(f"文件不存在：{src}")
        if src.suffix.lower() not in MD_EXTS | {".txt"}:
            raise ValueError(f"入向不是 Markdown 文件：{src}")

        ext = TARGET_EXT.get(target)
        if ext is None:
            raise ValueError(
                f"不支持的目标格式：{to}。可用：{', '.join(sorted(set(FORMAT_ALIASES)))}"
            )
        dst = _resolve_out(src, output, ext)
        if dst.resolve() == src.resolve():
            raise ValueError("输出路径不能与源文件相同")

        if not overwrite and dst.exists():
            raise FileExistsError(f"输出已存在（未加 --overwrite）：{dst}")

        dst.parent.mkdir(parents=True, exist_ok=True)
        used_engine = "copy" if target == "md" else ""
        if target == "md":
            shutil.copyfile(src, dst)
        elif target == "pdf":
            used_engine = _md_to_pdf(src, dst, toc=toc, pdf_engine=pdf_engine)
        elif target == "xlsx":
            _md_to_xlsx(src, dst)
            used_engine = "openpyxl"
        elif target in PANDOC_OUT:
            _pandoc_convert(
                src, dst, target, toc=toc,
                reference_doc=str(reference_doc) if reference_doc else None,
                standalone=True,
            )
            used_engine = "pandoc"
        elif target in ("png", "jpg"):
            raise ValueError("图片输出暂未开放；可先转 PDF，再截图")
        else:
            raise ValueError(f"目标格式暂不支持：{target}")

        result.output = str(dst)
        result.engine = used_engine
        result.chars = dst.stat().st_size if dst.exists() else 0
    except Exception as exc:
        result.ok = False
        result.error = f"{exc}"
    finally:
        result.elapsed = round(time.perf_counter() - started, 3)
    return result


# ---------------------------------------------------------------- 批量

def _iter_inputs(root: Path, recursive: bool, outdir: Path | None):
    pattern = root.rglob("*") if recursive else root.glob("*")
    for path in pattern:
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if outdir is not None:
            try:
                path.resolve().relative_to(outdir.resolve())
                continue
            except ValueError:
                pass
        yield path


def batch_convert(
    input_dir: str | os.PathLike,
    to: str = "md",
    outdir: str | os.PathLike | None = None,
    *,
    recursive: bool = True,
    engine: str = "auto",
    frontmatter: bool = False,
    extract_images: bool = False,
    join_lines: bool = False,
    overwrite: bool = True,
    workers: int = 4,
    vision: bool = False,
    vision_mode: str = "auto",
    vision_model: str | None = None,
    vision_max_pages: int = 50,
) -> list[ConvertResult]:
    """批量转换一个目录。to=md 表示入向，否则出向。"""
    root = Path(str(input_dir)).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"不是目录：{root}")
    direction_to_md = _norm_format(to) == "md"
    if outdir is None:
        outdir = (
            DEFAULT_MD_DIR / root.name
            if direction_to_md
            else root.parent / (root.name + "_converted")
        )
    out = Path(str(outdir)).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    files = list(_iter_inputs(root, recursive, out))
    if direction_to_md:
        files = [p for p in files if p.suffix.lower() not in MD_EXTS]
    else:
        # 反向批量只吃 Markdown，避免把 manifest.json 之类的文件也塞给 pandoc
        files = [p for p in files if p.suffix.lower() in MD_EXTS]

    lock = threading.Lock()
    results: list[ConvertResult] = []

    def work(path: Path) -> ConvertResult:
        rel = path.relative_to(root)
        target_ext = ".md" if direction_to_md else TARGET_EXT.get(_norm_format(to), ".out")
        dst = out / rel.with_suffix(target_ext)
        if dst.exists() and not overwrite:
            return ConvertResult(source=str(path), output=str(dst), format="md" if direction_to_md else _norm_format(to),
                                 ok=False, error="输出已存在")
        if direction_to_md:
            return to_markdown(
                path, dst, engine=engine, frontmatter=frontmatter,
                extract_images=extract_images, join_lines=join_lines,
                overwrite=overwrite, vision=vision,
                vision_mode=vision_mode, vision_model=vision_model,
                vision_max_pages=vision_max_pages,
            )
        return from_markdown(path, to, dst, overwrite=overwrite)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, p): p for p in files}
        for fut in as_completed(futures):
            try:
                res = fut.result()
            except Exception as exc:
                res = ConvertResult(source=str(futures[fut]), ok=False,
                                    error=f"{exc}")
            with lock:
                results.append(res)

    results.sort(key=lambda r: r.source.lower())
    manifest = {
        "input_dir": str(root),
        "output_dir": str(out),
        "mode": "to-md" if direction_to_md else f"from-md:{_norm_format(to)}",
        "total": len(results),
        "succeeded": sum(1 for r in results if r.ok),
        "failed": sum(1 for r in results if not r.ok),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "items": [r.to_dict() for r in results],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


# ---------------------------------------------------------------- 格式清单

def supported_formats() -> dict:
    return {
        "to_markdown": {
            "pdf": "pymupdf4llm（回退 markitdown）",
            "docx/doc/pptx/xlsx/xls": "markitdown",
            "html/htm/epub/csv/json/xml": "markitdown",
            "图片(jpg/png/tif/webp)": "markitdown（默认）/ 视觉模型（--vision）",
            "扫描页/图表/公式(--vision)": "火山方舟豆包视觉（OpenAI 兼容）",
            "URL": "markitdown",
            "md/txt": "直接读取",
        },
        "from_markdown": {
            **{k: f"pandoc({v})" for k, v in PANDOC_OUT.items()},
            "pdf": "Edge 无头打印 / Word",
            "xlsx": "openpyxl",
        },
        "aliases": sorted(FORMAT_ALIASES),
    }
