# arxiv-paper-notes

一个 Agent Skill（适用于 Claude Code、Cursor 等支持 `SKILL.md` 的 Agent），把一篇 arXiv 论文转成分层的中文精读笔记 `<arxiv_id>.md`：

- 背景、问题、方法分层展开，带编号的公式全部保留（`\tag{n}`）并逐项解释变量
- 原图转成 png 后插入，主结果表和消融表完整转写成 Markdown 表格
- 论文附带代码仓库时，拉取 [GitDiagram](https://gitdiagram.com) 架构图，浅克隆仓库，把论文公式对应到具体的源码位置
- 分多轮写作和修缮（draft → filled → reviewed），frontmatter 记录 `status` 和 `revision`，可以随时续写

## 工作方式

优先使用 arXiv 的 LaTeX 源码（`/src/<id>`），公式原文、`\caption`、`tabular` 都能无损取到；没有源码时退回 arXiv HTML 版本（LaTeXML）。不解析 PDF，只有 PDF 的论文暂不支持。

```
Pass 0 抓取  ->  Pass 1 骨架(draft)  ->  Pass 2 逐节填充(filled)  ->  Pass 3 自检回补(reviewed)
```

| 脚本 | 作用 |
|---|---|
| `scripts/fetch_arxiv.py` | 元数据、源码下载、主 tex 展开、宏展开、公式/图/表/算法/代码链接抽取、图片转 png、生成笔记骨架 |
| `scripts/repo_diagram.py` | GitDiagram Mermaid 架构图、仓库浅克隆、文件树和符号索引 |
| `scripts/check_note.py` | 检查章节完整性、公式覆盖、图片路径、表格、代码章节、禁用词等 |

完整流程和写作规则见 [SKILL.md](SKILL.md)。

## 安装

```bash
# Claude Code
git clone https://github.com/FrankCCCQ/arxiv-paper-notes.git ~/.claude/skills/arxiv-paper-notes
# Cursor
git clone https://github.com/FrankCCCQ/arxiv-paper-notes.git ~/.cursor/skills/arxiv-paper-notes
```

依赖只有 Python 3 标准库。PDF 格式的插图依次尝试 PyMuPDF、macOS `qlmanage`、`sips` 转换；EPS 需要 Ghostscript（`gs`），缺失时改用 HTML 版本的图片。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PAPER_NOTES_DIR` | `~/paper-notes` | 笔记输出目录，也可用 `--out DIR` 覆盖 |
| `GITHUB_TOKEN` | 无 | 可选，用于 GitHub API，避免未认证请求被限流 |

## 使用

在 Agent 中直接说「精读 arXiv 2402.17152」或给出论文链接即可。也可以手动运行：

```bash
python3 scripts/fetch_arxiv.py 2402.17152
python3 scripts/repo_diagram.py https://github.com/meta-recsys/generative-recommenders --arxiv-id 2402.17152
python3 scripts/check_note.py 2402.17152
```

产物：

```
$PAPER_NOTES_DIR/
├── 2402.17152.md            # 笔记
├── images/2402.17152/       # 论文原图
└── .cache/2402.17152/       # content.md、parsed.json、源码、仓库克隆
```

## 已知限制

- 只支持 arXiv，且需要有 LaTeX 源码或 HTML 版本
- HTML 模式下，LaTeXML 没渲染出来的图会缺失
- 老式 plain TeX 论文不解析章节结构，只给出警告
- GitDiagram 没生成过的仓库会返回 404，需要先在浏览器打开一次 `https://gitdiagram.com/<owner>/<repo>`；没有图时脚本会用本地文件树作为替代
