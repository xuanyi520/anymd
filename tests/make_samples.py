"""生成测试样本：md/pdf/docx/pptx/xlsx/html/epub。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pymupdf
from openpyxl import Workbook

HERE = Path(__file__).resolve().parent
OUT = HERE / "samples"


def make_md(path: Path) -> None:
    path.write_text(
        """# 机器学习在肝细胞癌早期筛查中的应用

## 研究背景

肝细胞癌（HCC）起病隐匿，多数患者确诊时已处于中晚期。早期筛查对改善预后至关重要。

## 主要方法

- 血清标志物：AFP、GALAD 评分
- 影像组学：CT、MRI 特征提取
- 机器学习模型：XGBoost、随机森林、深度学习

| 模型 | 样本量 | AUC | 敏感度 |
|---|---:|---:|---:|
| GALAD | 1200 | 0.89 | 82% |
| XGBoost | 860 | 0.92 | 86% |
| CNN | 540 | 0.94 | 88% |

## 结论

多模态融合模型在早期筛查中表现最佳，但外部验证仍然不足。

```python
print("hello anymd")
```
""",
        encoding="utf-8",
    )


def make_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    lines = [
        ("机器学习在肝细胞癌早期筛查中的应用", 20),
        ("摘要", 14),
        ("本文综述了机器学习在肝细胞癌早期筛查中的研究进展。", 11),
        ("方法包括血清标志物、影像组学和临床数据建模。", 11),
        ("结果显示多模态模型具有较高的诊断效能。", 11),
        ("关键词：肝细胞癌；早期筛查；机器学习", 11),
    ]
    y = 80
    for text, size in lines:
        page.insert_text((60, y), text, fontname="china-s", fontsize=size)
        y += size * 2.2
    doc.save(path)
    doc.close()


def make_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "模型对比"
    ws.append(["模型", "样本量", "AUC", "敏感度"])
    ws.append(["GALAD", 1200, 0.89, "82%"])
    ws.append(["XGBoost", 860, 0.92, "86%"])
    ws2 = wb.create_sheet("数据字典")
    ws2.append(["字段", "含义"])
    ws2.append(["AUC", "受试者工作特征曲线下面积"])
    wb.save(path)


def pandoc(src: Path, dst: Path, *extra: str) -> None:
    exe = shutil.which("pandoc")
    if not exe:
        raise RuntimeError("pandoc 不在 PATH 中")
    subprocess.run([exe, str(src), "-o", str(dst), *extra], check=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    md = OUT / "note.md"
    make_md(md)
    make_pdf(OUT / "paper.pdf")
    make_xlsx(OUT / "data.xlsx")
    pandoc(md, OUT / "report.docx")
    pandoc(md, OUT / "slides.pptx")
    pandoc(md, OUT / "page.html", "-s")
    pandoc(md, OUT / "book.epub")
    print(f"样本已生成：{OUT}")
    for p in sorted(OUT.iterdir()):
        print(f"  {p.name:<16} {p.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
