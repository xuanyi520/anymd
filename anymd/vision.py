"""视觉模型接入层（OpenAI 兼容接口，默认火山方舟豆包）。

职责：
  - 读取配置（环境变量 -> anymd/.env -> ppt-master/.env 的 ARK_API_KEY）
  - 图片文件 -> Markdown 描述
  - PDF 扫描页 -> 逐页识别
  - PDF/PPTX/DOCX 内嵌图片 -> 提取并逐张描述
  - 图表/公式 -> Markdown 表格 / LaTeX

所有错误信息都会脱敏，不会把 API Key 写进日志或返回值。
"""

from __future__ import annotations

import base64
import io
import os
import time
import zipfile
from pathlib import Path
from typing import Callable, Iterable

import requests

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seed-2-1-lite-260915"
MAX_IMAGES = 30

PROMPTS = {
    "auto": (
        "请把这张图片转换成适合 AI 阅读的 Markdown。要求：\n"
        "1. 如果是图表，先给出标题和坐标轴含义，再用 Markdown 表格还原数据；\n"
        "2. 如果是数学公式，用 LaTeX 输出；\n"
        "3. 如果是文档截图或扫描页，按阅读顺序输出正文，保留标题层级、列表和表格；\n"
        "4. 如果是照片或示意图，客观描述主体、结构和其中出现的文字；\n"
        "5. 看不清的内容写 [无法辨认]，不要编造。\n"
        "只输出 Markdown 正文，不要额外解释。"
    ),
    "describe": (
        "请客观描述这张图片的内容，包括主体、结构、文字和图表信息。"
        "只输出描述文本，不要额外解释。"
    ),
    "ocr": (
        "请提取图片中的全部文字，按原有阅读顺序和段落结构输出 Markdown；"
        "表格用 Markdown 表格。不要翻译，不要总结，不要编造。"
    ),
    "chart": (
        "请把这张图表转换成 Markdown：先写图表标题和坐标轴含义，"
        "再用 Markdown 表格还原数据。看不清的数值写 [无法辨认]。"
    ),
    "formula": (
        "请识别图片中的数学公式，用 LaTeX 输出；如果有多个公式，按顺序列出。"
    ),
}

MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}


class VisionError(RuntimeError):
    pass


# ---------------------------------------------------------------- 配置

def _parse_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def load_config() -> dict[str, str]:
    """优先级：环境变量 > anymd/.env > ppt-master/.env。"""
    env: dict[str, str] = {}
    for key in (
        "VISION_API_KEY", "ARK_API_KEY", "VISION_BASE_URL",
        "VISION_MODEL", "VOLCENGINE_BASE_URL",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value

    local = _parse_env(Path(__file__).resolve().parent.parent / ".env")
    skill = _parse_env(
        Path.home() / ".claude" / "skills" / "ppt-master" / ".env")

    def pick(*names: str, default: str = "") -> str:
        for name in names:
            for source in (env, local, skill):
                if source.get(name):
                    return source[name]
        return default

    return {
        "api_key": pick("VISION_API_KEY", "ARK_API_KEY"),
        "base_url": pick(
            "VISION_BASE_URL", "VOLCENGINE_BASE_URL", default=DEFAULT_BASE_URL),
        "model": pick("VISION_MODEL", default=DEFAULT_MODEL),
    }


def vision_available() -> tuple[bool, str]:
    cfg = load_config()
    if not cfg["api_key"]:
        return False, "未找到 ARK_API_KEY 或 VISION_API_KEY"
    return True, f"{cfg['model']} @ {cfg['base_url']}"


# ---------------------------------------------------------------- 图片编码

def _encode_image(
    source: str | Path | bytes,
    *,
    max_side: int = 1800,
    quality: int = 88,
) -> tuple[str, str]:
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        ext = ".png"
    else:
        path = Path(source)
        data = path.read_bytes()
        ext = path.suffix.lower()

    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        width, height = image.size
        if max(width, height) > max_side:
            scale = max_side / max(width, height)
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.LANCZOS,
            )
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality)
        return base64.b64encode(buffer.getvalue()).decode(), "image/jpeg"
    except Exception:
        mime = MIME_BY_EXT.get(ext, "image/png")
        return base64.b64encode(data).decode(), mime


# ---------------------------------------------------------------- 请求

def _friendly_error(status: int, body: str, model: str) -> str:
    body = (body or "").replace("\n", " ")
    if status == 401:
        return "视觉模型鉴权失败：API Key 无效或已过期"
    if "ModelNotOpen" in body:
        return (
            f"模型 {model} 未开通。请到火山方舟控制台"
            "（https://console.volcengine.com/ark）的模型广场开通该模型，"
            "开通后无需改代码，直接重试。"
        )
    if "InvalidEndpointOrModel" in body:
        return f"模型 {model} 不存在或当前账号无权限访问"
    if status == 429:
        return "视觉模型触发限流，请稍后重试或降低并发"
    if status >= 500:
        return f"视觉模型服务端错误（HTTP {status}），请稍后重试"
    return f"视觉模型调用失败（HTTP {status}）：{body[:300]}"


def _post(image_b64: str, mime: str, prompt: str, *,
          model: str | None = None, max_tokens: int = 4096) -> str:
    cfg = load_config()
    if not cfg["api_key"]:
        raise VisionError(
            "未配置视觉模型密钥。请在 anymd/.env 写 VISION_API_KEY，"
            "或复用 ppt-master/.env 中的 ARK_API_KEY。"
        )
    active_model = model or cfg["model"]
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": active_model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            ],
        }],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }

    last_error = ""
    for attempt in range(3):
        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=180)
        except requests.RequestException as exc:
            last_error = "网络请求失败，请检查网络连接或代理设置"
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise VisionError(last_error) from None

        if response.status_code == 200:
            try:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError):
                raise VisionError("视觉模型返回了无法解析的响应") from None
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") for part in content
                    if isinstance(part, dict)
                )
            return str(content).strip()

        last_error = _friendly_error(
            response.status_code, response.text, active_model)
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        raise VisionError(last_error)

    raise VisionError(last_error or "视觉模型调用失败")


def describe_image(
    source: str | Path | bytes,
    *,
    mode: str = "auto",
    model: str | None = None,
    prompt_prefix: str = "",
    max_side: int = 1800,
) -> str:
    prompt = PROMPTS.get(mode, PROMPTS["auto"])
    if prompt_prefix:
        prompt = prompt_prefix.strip() + "\n\n" + prompt
    image_b64, mime = _encode_image(source, max_side=max_side)
    return _post(image_b64, mime, prompt, model=model)


# ---------------------------------------------------------------- PDF

def pdf_pages_markdown(
    path: str | Path,
    *,
    dpi: int = 180,
    max_pages: int | None = 50,
    model: str | None = None,
    mode: str = "auto",
    progress: Callable[[int, int], None] | None = None,
) -> str:
    import pymupdf

    doc = pymupdf.open(str(path))
    total = doc.page_count
    limit = total if not max_pages else min(total, max_pages)
    parts: list[str] = []
    try:
        for index in range(limit):
            page = doc[index]
            pixmap = page.get_pixmap(dpi=dpi)
            png = pixmap.tobytes("png")
            text = describe_image(
                png, mode=mode, model=model,
                prompt_prefix=f"这是 PDF 的第 {index + 1} 页。",
                max_side=2200,
            )
            parts.append(f"## 第 {index + 1} 页\n\n{text.strip()}")
            if progress:
                progress(index + 1, limit)
    finally:
        doc.close()

    if limit < total:
        parts.append(f"\n> 全文共 {total} 页，只识别了前 {limit} 页。")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 内嵌图片

def extract_images(path: str | Path, outdir: Path) -> list[Path]:
    """把 PDF/PPTX/DOCX 里的图片抽到 outdir，返回图片路径列表。"""
    path = Path(path)
    outdir.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _extract_pdf_images(path, outdir)
    if ext == ".pptx":
        return _extract_pptx_images(path, outdir)
    if ext == ".docx":
        return _extract_docx_images(path, outdir)
    return []


def _extract_pdf_images(path: Path, outdir: Path) -> list[Path]:
    import pymupdf

    doc = pymupdf.open(str(path))
    seen: set[int] = set()
    result: list[Path] = []
    try:
        for page_index in range(doc.page_count):
            for image in doc[page_index].get_images(full=True):
                xref = image[0]
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    info = doc.extract_image(xref)
                except Exception:
                    continue
                data = info.get("image")
                if not data or len(data) < 1024:
                    continue  # 跳过图标/装饰性小图
                suffix = info.get("ext", "png")
                target = outdir / f"p{page_index + 1}_x{xref}.{suffix}"
                target.write_bytes(data)
                result.append(target)
    finally:
        doc.close()
    return result


def _extract_pptx_images(path: Path, outdir: Path) -> list[Path]:
    from pptx import Presentation

    result: list[Path] = []
    presentation = Presentation(str(path))
    for slide_index, slide in enumerate(presentation.slides, start=1):
        for shape_index, shape in enumerate(slide.shapes, start=1):
            if getattr(shape, "shape_type", None) != 13:  # PICTURE
                continue
            try:
                image = shape.image
            except Exception:
                continue
            if len(image.blob) < 1024:
                continue
            target = outdir / f"s{slide_index}_i{shape_index}.{image.ext}"
            target.write_bytes(image.blob)
            result.append(target)
    return result


def _extract_docx_images(path: Path, outdir: Path) -> list[Path]:
    result: list[Path] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith("word/media/"):
                continue
            data = archive.read(name)
            if len(data) < 1024:
                continue
            target = outdir / Path(name).name
            target.write_bytes(data)
            result.append(target)
    return result


def describe_images(
    images: Iterable[Path],
    *,
    mode: str = "auto",
    model: str | None = None,
    limit: int = MAX_IMAGES,
    progress: Callable[[int, int], None] | None = None,
) -> list[tuple[Path, str]]:
    items = list(images)[:limit]
    result: list[tuple[Path, str]] = []
    for index, image in enumerate(items, start=1):
        try:
            text = describe_image(image, mode=mode, model=model)
        except Exception as exc:
            text = f"[图片识别失败：{exc}]"
        result.append((image, text))
        if progress:
            progress(index, len(items))
    return result
