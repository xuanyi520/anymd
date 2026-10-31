"""anymd —— 多格式与 Markdown 双向转换工具。"""

__version__ = "0.1.0"

from .core import (
    ConvertResult,
    batch_convert,
    from_markdown,
    render_markdown,
    supported_formats,
    to_markdown,
)

__all__ = [
    "__version__",
    "ConvertResult",
    "render_markdown",
    "to_markdown",
    "from_markdown",
    "batch_convert",
    "supported_formats",
]
