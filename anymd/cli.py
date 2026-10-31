"""anymd 命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core import (
    FORMAT_ALIASES,
    batch_convert,
    from_markdown,
    supported_formats,
    to_markdown,
)


def _setup_console() -> None:
    # Git Bash / MSYS 终端按 UTF-8 解码；PowerShell 5.1 和 CMD 默认是 GBK。
    # 显式设置了 PYTHONIOENCODING 时尊重用户设置，否则按终端类型自适应。
    if os.environ.get("PYTHONIOENCODING"):
        target = None
    elif os.environ.get("MSYSTEM") or os.environ.get("TERM"):
        target = "utf-8"
    else:
        target = None  # 保持控制台编码（中文 Windows 为 cp936）
    for stream in (sys.stdout, sys.stderr):
        try:
            if target:
                stream.reconfigure(encoding=target, errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except Exception:
            pass


def _print_result(res, as_json: bool) -> None:
    if as_json:
        print(json.dumps(res.to_dict(), ensure_ascii=False))
        return
    if res.ok:
        size = f"{res.chars:,} 字符" if res.format == "md" else f"{res.chars:,} 字节"
        extra = f", {res.pages} 页" if res.pages else ""
        print(f"[成功]   {res.source} -> {res.output}  ({size}{extra}, {res.elapsed}s, {res.engine})")
    else:
        print(f"[失败] {res.source}: {res.error}")
    for warning in res.warnings:
        print(f"       [警告] {warning}")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="输出 JSON 结果")
    p.add_argument("--no-overwrite", dest="overwrite", action="store_false",
                   help="输出文件已存在时跳过，不覆盖")


def cmd_to_md(args) -> int:
    if len(args.inputs) > 1 and args.output and not Path(args.output).is_dir():
        print("多个输入时 -o 必须是已存在的目录", file=sys.stderr)
        return 2
    failures = 0
    results = []
    for item in args.inputs:
        out = args.output
        if len(args.inputs) > 1 and out:
            out = Path(out) / (Path(item).stem + ".md")
        res = to_markdown(
            item, out, engine=args.engine, frontmatter=args.frontmatter,
            extract_images=args.extract_images, join_lines=args.join_lines,
            overwrite=args.overwrite, vision=args.vision,
            vision_mode=args.vision_mode, vision_model=args.vision_model,
            vision_max_pages=args.vision_max_pages,
        )
        results.append(res)
        failures += 0 if res.ok else 1
        _print_result(res, args.json)
    return 1 if failures else 0


def cmd_from_md(args) -> int:
    if len(args.inputs) > 1 and args.output and not Path(args.output).is_dir():
        print("多个输入时 -o 必须是已存在的目录", file=sys.stderr)
        return 2
    from .core import TARGET_EXT, _norm_format
    ext = TARGET_EXT.get(_norm_format(args.to), ".out")
    failures = 0
    for item in args.inputs:
        out = args.output
        if len(args.inputs) > 1 and out:
            out = Path(out) / (Path(item).stem + ext)
        res = from_markdown(
            item, args.to, out, toc=args.toc,
            reference_doc=args.reference_doc, pdf_engine=args.pdf_engine,
            overwrite=args.overwrite,
        )
        failures += 0 if res.ok else 1
        _print_result(res, args.json)
    return 1 if failures else 0


def cmd_batch(args) -> int:
    results = batch_convert(
        args.input_dir, to=args.to, outdir=args.outdir,
        recursive=args.recursive, engine=args.engine,
        frontmatter=args.frontmatter, extract_images=args.extract_images,
        join_lines=args.join_lines, overwrite=args.overwrite,
        workers=args.workers, vision=args.vision,
        vision_mode=args.vision_mode, vision_model=args.vision_model,
        vision_max_pages=args.vision_max_pages,
    )
    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
    else:
        for res in results:
            _print_result(res, False)
        ok = sum(1 for r in results if r.ok)
        print(f"\n完成：{ok}/{len(results)} 成功；清单见 manifest.json")
    return 0 if all(r.ok for r in results) else 1


def cmd_vision_test(args) -> int:
    import io

    from . import vision

    ok, info = vision.vision_available()
    print(f"视觉配置：{info}")
    if not ok:
        print("未找到密钥。请设置 VISION_API_KEY，或复用 ppt-master/.env 的 ARK_API_KEY。")
        return 1

    try:
        if args.image:
            text = vision.describe_image(
                args.image, mode=args.mode, model=args.model)
        else:
            from PIL import Image, ImageDraw
            image = Image.new("RGB", (320, 160), (245, 245, 245))
            draw = ImageDraw.Draw(image)
            draw.text((20, 20), "anymd vision test", fill=(20, 20, 20))
            draw.rectangle([20, 70, 160, 130], outline=(200, 30, 30), width=4)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            text = vision.describe_image(
                buffer.getvalue(), mode=args.mode, model=args.model)
        print("\n模型返回：")
        print(text)
        print("\n[成功] 视觉模型可用。")
        return 0
    except Exception as exc:
        print(f"\n[失败] {exc}")
        if "未开通" in str(exc):
            print("\n开通指引：")
            print("  1. 打开 https://console.volcengine.com/ark")
            print("  2. 进入「模型广场」，搜索 doubao-seed-2-1-lite 或 doubao-seed-2-1-pro")
            print("  3. 点击「开通服务」，然后重新运行 anymd vision-test")
        return 1


def cmd_formats(_args) -> int:
    data = supported_formats()
    print("入向（任意格式 -> Markdown）")
    for k, v in data["to_markdown"].items():
        print(f"  {k:<32} {v}")
    print("\n出向（Markdown -> 任意格式）")
    for k, v in data["from_markdown"].items():
        print(f"  {k:<32} {v}")
    print("\n格式别名：" + ", ".join(data["aliases"]))
    return 0


class _ChineseParser(argparse.ArgumentParser):
    """把 argparse 的默认英文标签换成中文。"""

    def format_help(self) -> str:
        text = super().format_help()
        return (
            text.replace("usage:", "用法：")
            .replace("positional arguments:", "位置参数：")
            .replace("options:", "选项：")
            .replace("show this help message and exit", "显示帮助并退出")
        )


def build_parser() -> argparse.ArgumentParser:
    parser = _ChineseParser(
        prog="anymd",
        description="多格式与 Markdown 双向转换：让 AI 读文档，也让文档交给 AI 写。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  anymd to-md 论文.pdf
  anymd to-md 课件.pptx -o 课件.md --frontmatter
  anymd from-md 报告.md --to docx
  anymd from-md 报告.md --to pdf
  anymd batch E:\\资料 --to md --outdir E:\\资料_md
  anymd mcp

中文命令（与英文命令等价）：
  转md     任意格式 -> Markdown
  转出     Markdown -> 其他格式
  批量     批量转换目录
  格式     列出支持的格式
  视觉测试  测试视觉模型是否可用
  服务     启动 MCP server
  帮助     显示帮助
""",
    )
    sub = parser.add_subparsers(
        dest="command", required=True, parser_class=_ChineseParser)

    p = sub.add_parser("to-md", help="任意格式 -> Markdown")
    p.add_argument("inputs", nargs="+", help="文件或 URL")
    p.add_argument("-o", "--output", help="输出文件或目录")
    p.add_argument("--engine", choices=["auto", "markitdown", "pymupdf", "pymupdf4llm"],
                   default="auto", help="入向引擎（默认 auto）")
    p.add_argument("--frontmatter", action="store_true", help="写入 YAML 元数据头")
    p.add_argument("--extract-images", action="store_true", help="抽取图片到 assets/")
    p.add_argument("--join-lines", action="store_true", help="尝试修复被硬换行切断的段落")
    p.add_argument("--vision", action="store_true",
                   help="启用视觉模型（图片描述/扫描页识别/图表公式）")
    p.add_argument("--vision-mode", choices=["auto", "describe", "ocr", "chart", "formula"],
                   default="auto", help="视觉识别模式（默认 auto）")
    p.add_argument("--vision-model", help="覆盖默认视觉模型")
    p.add_argument("--vision-max-pages", type=int, default=50,
                   help="扫描版 PDF 最多识别页数（默认 50）")
    _add_common(p)
    p.set_defaults(func=cmd_to_md)

    p = sub.add_parser("from-md", help="Markdown -> 任意格式")
    p.add_argument("inputs", nargs="+", help="Markdown 文件")
    p.add_argument("--to", required=True, help="目标格式（docx/pdf/html/pptx/epub/xlsx...）")
    p.add_argument("-o", "--output", help="输出文件或目录")
    p.add_argument("--toc", action="store_true", help="生成目录")
    p.add_argument("--reference-doc", help="docx/pptx 的模板文件")
    p.add_argument("--pdf-engine", choices=["auto", "edge", "word"], default="auto",
                   help="PDF 生成方式")
    _add_common(p)
    p.set_defaults(func=cmd_from_md)

    p = sub.add_parser("batch", help="批量转换目录")
    p.add_argument("input_dir", help="输入目录")
    p.add_argument("--to", default="md",
                   help="md 表示入向，其余表示出向（默认 md）")
    p.add_argument("--outdir", help="输出目录（默认 <输入名>_converted）")
    p.add_argument("--no-recursive", dest="recursive", action="store_false",
                   help="不递归子目录")
    p.add_argument("--engine", choices=["auto", "markitdown", "pymupdf", "pymupdf4llm"],
                   default="auto")
    p.add_argument("--workers", type=int, default=4, help="并发数（默认 4）")
    p.add_argument("--frontmatter", action="store_true")
    p.add_argument("--extract-images", action="store_true")
    p.add_argument("--join-lines", action="store_true")
    p.add_argument("--vision", action="store_true",
                   help="批量转换时启用视觉模型")
    p.add_argument("--vision-mode", choices=["auto", "describe", "ocr", "chart", "formula"],
                   default="auto")
    p.add_argument("--vision-model")
    p.add_argument("--vision-max-pages", type=int, default=50)
    _add_common(p)
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("formats", help="列出支持的格式")
    p.set_defaults(func=cmd_formats)

    p = sub.add_parser("vision-test", help="测试视觉模型是否可用")
    p.add_argument("--image", help="测试图片路径（不填则用内置小图）")
    p.add_argument("--mode", choices=["auto", "describe", "ocr", "chart", "formula"],
                   default="describe")
    p.add_argument("--model", help="覆盖默认视觉模型")
    p.set_defaults(func=cmd_vision_test)

    p = sub.add_parser("mcp", help="启动 MCP server（stdio）")
    p.set_defaults(func=lambda _a: _run_mcp())

    return parser


def _run_mcp() -> int:
    from .mcp_server import run
    run()
    return 0


COMMAND_ALIASES = {
    "转md": "to-md",
    "转出": "from-md",
    "批量": "batch",
    "格式": "formats",
    "视觉测试": "vision-test",
    "服务": "mcp",
    "帮助": "--help",
}


def main(argv=None) -> int:
    _setup_console()
    parser = build_parser()
    args_list = sys.argv[1:] if argv is None else list(argv)
    if args_list:
        args_list[0] = COMMAND_ALIASES.get(args_list[0], args_list[0])

    if args_list and args_list[0] in ("-h", "--help"):
        parser.print_help()
        return 0

    # 无参数，或第一个参数不是子命令（例如把文件拖到 anymd.cmd 上），
    # 都进入交互模式。
    known = {"to-md", "from-md", "batch", "formats", "mcp", "vision-test"}
    if not args_list or args_list[0] not in known:
        from .interactive import run as interactive_run
        return interactive_run(args_list)

    args = parser.parse_args(args_list)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
