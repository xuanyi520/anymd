# anymd

**多格式与 Markdown 双向转换工具：让 AI 读得懂你的文档，也让 AI 写的东西变成能交付的文件。**

```
PDF / Word / PPT / Excel / HTML / EPUB / CSV / JSON / 图片 / 网页  ──→  Markdown
Markdown  ──→  Word / PDF / PPT / HTML / EPUB / Excel / LaTeX / RST / ODT / RTF
```

一行命令、拖个文件、或者在 Claude Code 里直接说话，三种用法随你挑。

---

## 为什么会有这个工具

做文献综述和课程汇报时有两件反复发生的苦活：

1. **AI 读不懂二进制文档**。Zotero 里的上百篇 PDF、老师发的 PPT、别人给的 Word，全都得先变成纯文本 AI 才啃得动。手动一次一次复制粘贴，一天就没了。
2. **AI 写的东西交付不了**。模型给你一篇 Markdown，但导师要 Word、答辩要 PPT、发给别人要 PDF。转格式又得开一堆软件。

anymd 就是这两件事的固定答案：**入向**把一切格式压平成 Markdown，**出向**把 Markdown 变成任何交付格式。

底层用三个成熟开源引擎，不造轮子：

| 能力 | 引擎 | 版本 |
|---|---|---|
| 学术 PDF → Markdown | pymupdf4llm | 1.28 |
| 其他格式 → Markdown | Microsoft markitdown | 0.1.8 |
| Markdown → 各种格式 | pandoc | 3.8 |
| Markdown → 中文 PDF | pandoc 生 HTML + Edge 无头打印 | — |

---

## 快速开始

```bash
# 任意格式 → Markdown
anymd to-md 论文.pdf

# PDF → Markdown，带元数据、抽出图片
anymd to-md 论文.pdf --frontmatter --extract-images

# 课件 PPT 的全部文字
anymd to-md 第3讲.pptx

# Markdown → Word
anymd from-md 报告.md --to docx

# Markdown → PDF（中文、带目录）
anymd from-md 报告.md --to pdf --toc

# 整个目录批量转
anymd batch D:\文献 --to md --outdir D:\文献_md
```

把文件或文件夹**直接拖到 `anymd.cmd` 的图标上**也能用，会弹出菜单问你转成什么。

---

## 自带 MCP server

装好之后在 Claude Code 里可以直接说人话：

> 把 D:\Paper\那篇.pdf 转成 markdown，加上元数据
> 把这个 md 生成一份 Word 和一份 PDF
> 把 D:\资料 整个目录批量转成 markdown

可用工具：`to_markdown`、`from_markdown`、`batch_convert`、`list_formats`。

---

## 能拿来干什么

- **文献综述流水线** — Zotero 的 PDF 批量转 md，AI 精读后直接填提取表
- **个人知识库 / RAG** — 整个资料盘归一化成 md，统一检索
- **交付物生成** — AI 写 md，一键出 Word / PPT / PDF
- **PPT 转讲稿** — 课件转 md，按页生成讲稿大纲
- **网页归档** — 公众号、博客、文档页转 md 永久保存
- **Excel 直读** — 每个 sheet 一张 Markdown 表，AI 不用猜结构
- **格式归一化** — 把乱七八糟的历史资料统一成可检索文本
- **电子书笔记** — epub ↔ md 互转，摘录进 Obsidian

---

## 安装

需要 Python 3.10+，另需本机有 [pandoc](https://pandoc.org/installing.html)（md 出向必需）和 Microsoft Edge（md → PDF 中文必需）。

```bash
git clone https://github.com/xuanyi520/anymd.git
cd anymd
python -m venv .venv
.venv\Scripts\pip install -e .
```

Windows 下再用 `anymd.cmd` 免激活调用，或者把项目目录加进 PATH 后直接敲 `anymd`。

**完整安装步骤、全部参数说明、故障排查见 [docs/使用手册.md](docs/使用手册.md)。**

---

## 文档

| 文档 | 内容 |
|---|---|
| [docs/使用手册.md](docs/使用手册.md) | 完整使用手册：安装、每个命令的全部参数、MCP 接入、视觉模型、故障排查、FAQ |
| [docs/设计说明.md](docs/设计说明.md) | 架构与取舍：引擎怎么选、为什么是两步转换、五个必踩坑 |

---

## 支持的格式

**入向**：`pdf` `docx` `doc` `pptx` `xlsx` `xls` `html` `htm` `epub` `csv` `json` `xml` `txt` 各种图片 `URL`

**出向**：`docx` `pdf` `pptx` `xlsx` `html` `epub` `latex/tex` `rst` `odt` `rtf` `org` `txt` `json` `mediawiki` `asciidoc`

完整别名列表见 `anymd formats`。

---

## 已知限制

- **扫描版 PDF 需要视觉模型**：不加 `--vision` 时只能拿到空文本，会给出警告
- **`.doc` 旧格式依赖本机 Word**：不想装就另存为 `.docx`
- **md → pptx 版式朴素**：pandoc 按标题分页，复杂设计建议用专业 PPT 工作流
- **md → PDF 依赖 Edge**：本机没配 LaTeX，走 HTML + 无头打印

---

## License

MIT
