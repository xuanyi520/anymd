"""anymd 的 MCP server（stdio）。

注意：本机 mcp SDK 为 2.x，必须用 mcp.server.mcpserver.MCPServer；
旧教程里的 mcp.server.fastmcp.FastMCP 在 2.x 中已不可用。
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from .core import (
    _frontmatter_block,
    batch_convert as _batch_convert,
    from_markdown as _from_markdown,
    render_markdown,
    supported_formats,
    to_markdown as _to_markdown,
)

mcp = MCPServer("anymd")


@mcp.tool()
def to_markdown(
    path: str,
    output: str | None = None,
    engine: str = "auto",
    frontmatter: bool = False,
    extract_images: bool = False,
    max_chars: int = 0,
    vision: bool = False,
    vision_mode: str = "auto",
    vision_model: str | None = None,
) -> str:
    """把 PDF/Word/PPT/Excel/HTML/EPUB/图片/URL 等转换成 Markdown 文本。

    path: 本地文件绝对路径或 http(s) URL。
    output: 可选；填写则写入该文件并返回摘要，不填则直接返回 Markdown 正文。
    engine: auto（默认）、markitdown、pymupdf。
    frontmatter: 是否在正文前加 YAML 元数据（标题/来源/页数）。
    extract_images: PDF 是否抽取图片到 assets/（仅 output 模式下生效）。
    max_chars: 返回正文的最大字符数，0 表示不截断。
    vision: 是否调用视觉模型，识别图片、扫描页、图表和公式。
    vision_mode: auto / describe / ocr / chart / formula。
    vision_model: 覆盖默认视觉模型。
    """
    if output:
        res = _to_markdown(
            path, output, engine=engine, frontmatter=frontmatter,
            extract_images=extract_images, vision=vision,
            vision_mode=vision_mode, vision_model=vision_model,
        )
        return json.dumps({
            "ok": res.ok,
            "output": res.output,
            "chars": res.chars,
            "pages": res.pages,
            "engine": res.engine,
            "warnings": res.warnings,
            "error": res.error,
        }, ensure_ascii=False)

    rendered = render_markdown(
        path, engine=engine, extract_images=extract_images,
        vision=vision, vision_mode=vision_mode, vision_model=vision_model)
    text = rendered.text
    if frontmatter:
        text = _frontmatter_block(path, rendered.engine, text, rendered.pages) + text
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + f"\n\n...(已截断，原文共 {len(text)} 字符)"
    if rendered.warnings:
        text = "\n".join(f"> 警告：{w}" for w in rendered.warnings) + "\n\n" + text
    return text


@mcp.tool()
def from_markdown(
    path: str,
    to: str,
    output: str | None = None,
    toc: bool = False,
    reference_doc: str | None = None,
) -> str:
    """把 Markdown 文件转换成 Word/PDF/PPT/HTML/EPUB/Excel 等格式。

    path: Markdown 文件路径。
    to: 目标格式，如 docx、pdf、pptx、html、epub、xlsx、rtf、odt、latex。
    output: 可选输出路径；不填则与源文件同目录同名。
    toc: 是否生成目录。
    reference_doc: docx/pptx 的模板文件路径（控制字体和版式）。
    """
    res = _from_markdown(
        path, to, output, toc=toc, reference_doc=reference_doc)
    return json.dumps({
        "ok": res.ok,
        "output": res.output,
        "format": res.format,
        "engine": res.engine,
        "bytes": res.chars,
        "elapsed": res.elapsed,
        "error": res.error,
    }, ensure_ascii=False)


@mcp.tool()
def batch_convert(
    input_dir: str,
    to: str = "md",
    outdir: str | None = None,
    recursive: bool = True,
    workers: int = 4,
    frontmatter: bool = False,
) -> str:
    """批量转换整个目录。

    input_dir: 输入目录。
    to: "md" 表示把各种格式批量转成 Markdown；其他值表示把目录里的 Markdown 转成该格式。
    outdir: 输出目录；不填则默认为 <输入目录名>_converted。
    recursive: 是否递归子目录。
    workers: 并发数。
    frontmatter: 入向转换时是否加 YAML 元数据。
    """
    results = _batch_convert(
        input_dir, to=to, outdir=outdir, recursive=recursive,
        workers=workers, frontmatter=frontmatter,
    )
    failed = [r for r in results if not r.ok]
    return json.dumps({
        "total": len(results),
        "succeeded": len(results) - len(failed),
        "failed": len(failed),
        "output_dir": str(outdir) if outdir else None,
        "failures": [
            {"source": r.source, "error": r.error} for r in failed[:20]
        ],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def list_formats() -> str:
    """列出 anymd 支持的入向和出向格式。"""
    return json.dumps(supported_formats(), ensure_ascii=False, indent=2)


def run() -> None:
    mcp.run()


if __name__ == "__main__":
    run()
