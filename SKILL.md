---
name: arxiv-paper-notes
description: 读 arXiv 论文并生成分层的中文 Markdown 精读笔记（文件名为 arXiv 编号），保留关键公式、抄录关键图表、记录代码仓库并附 GitDiagram 架构图，支持多轮修缮。当用户给出 arXiv 编号或链接并要求阅读、精读、解析、做笔记，或要求继续修改某篇已有论文笔记时使用。
---

# arXiv 论文精读笔记

## 产物

输出目录记为 `$OUT`：取环境变量 `PAPER_NOTES_DIR`，未设置时为 `~/paper-notes`；三个脚本都可用 `--out DIR` 覆盖。

| 路径 | 内容 |
|---|---|
| `$OUT/<id>.md` | 笔记主文件（旧格式编号中的 `/` 替换为 `_`） |
| `$OUT/images/<id>/` | 论文原图（png） |
| `$OUT/.cache/<id>/` | `content.md`、`parsed.json`、`meta.json`、`repo.md`、源码与仓库克隆 |

笔记中图片使用相对路径 `images/<id>/xxx.png`。设置了 `GITHUB_TOKEN` 时，`repo_diagram.py` 会用它调用 GitHub API，避免限流。

## 流程总览

```
Pass 0 抓取  ->  Pass 1 骨架(draft)  ->  Pass 2 逐节填充(filled)  ->  Pass 3 自检回补(reviewed)
```

每次被调用时，先看 `<id>.md` 是否存在：

- 不存在：从 Pass 0 开始
- 存在：读 frontmatter 的 `status` 和 `revision`，从下一个 Pass 继续；如果用户指定了章节或问题（如「把 HSTU 的公式再讲细一点」），只修改对应章节，`revision` 加 1，更新 `updated`

## Pass 0：抓取与解析

```bash
python3 ~/.claude/skills/arxiv-paper-notes/scripts/fetch_arxiv.py <arxiv_id_or_url>
```

- 优先下载 arXiv LaTeX 源码（`/src/<id>`）：公式原文、`\caption`、`tabular` 零损失；无源码时用 arXiv HTML 版本（`/html/<id>`）
- 两者都没有时脚本返回 `ok: false`，告知用户「该论文只有 PDF，当前 skill 暂不支持」，不要自行改用其他方式
- LaTeX 解析效果差（如 `warnings` 提示无章节结构）时，可加 `--source html` 重跑对比
- 已下载内容会缓存；需要更新到新版本时加 `--force`
- 首次运行会用 `templates/note.md` 生成笔记骨架

脚本的 stdout 是 JSON 摘要，重点看 `counts`、`code_links`、`warnings`。

有代码仓库时（`code_links` 第一项；分数低于 50 的需结合正文判断是否为官方仓库，可能是引用的第三方库）：

```bash
python3 ~/.claude/skills/arxiv-paper-notes/scripts/repo_diagram.py <repo_url> --arxiv-id <id>
```

- 拉取 GitDiagram 的 `https://gitdiagram.com/<owner>/<repo>.md`（架构概述 + Mermaid + 组件到文件映射）
- 浅克隆仓库到 `.cache/<id>/repo`，生成文件树、符号索引、README，写入 `.cache/<id>/repo.md`
- `gitdiagram: not_generated` 时：如果浏览器工具可用，打开 `gitdiagram_page` 等待约 1 分钟生成后重跑脚本；否则根据 `repo.md` 的文件树和符号索引自己绘制 Mermaid

## Pass 1：骨架（status: draft）

1. 通读 `.cache/<id>/content.md`。它是按章节渲染的全文：
   - `[[FIGURE n | labels=... | files=...]]` 标记图的位置和已提取的图片
   - `[[TABLE n]]` 后附 `tabular` 原文（同一个 float 中并排的多个带 caption 的 minipage 已按 LaTeX 规则分别编号）
   - `[[ALGORITHM | caption=...]]` 后附算法环境原文，笔记中改写为伪代码
   - `<!-- eq n label -->` 标记公式编号
   - `[cite:key]`、`[ref:label]` 是引用和交叉引用
2. 填写元信息（阅读时长、难度、前置知识）和 TL;DR
3. 每个章节先写 2-5 条要点（以列表代替 TODO 注释），确定方法章节要拆成哪几个模块
4. 把 frontmatter 的 `status` 改为 `draft`

## Pass 2：逐节填充（status: filled）

按章节顺序填充，每次只编辑一个章节（用局部替换，不要整篇重写），每节写完再进行下一节。对照 `parsed.json` 取公式、图表原文。

### 一、背景 / 二、问题

- 背景写领域现状、代表性已有方法及其做法、本文所在的技术路线
- 问题写瓶颈和根因；论文给出形式化定义时（输入、输出、目标函数）要保留

### 三、方法（重点，篇幅最大）

- 3.1 先放整体架构图，然后用一句话说明数据流
- 3.2 按模块展开，每个模块包括：输入输出与维度、关键公式、设计动机、伪代码（如有必要）
- 公式规则：
  - 使用 `parsed.json` 中 `equations[].latex`（宏已展开、`\label` 已去掉），以 `$$ ... \tag{n} $$` 写入，编号与原文一致
  - 公式后紧跟「其中：」逐项解释变量，再写直觉或「为什么这样设计」
  - 正文中带编号的公式都要覆盖；不写进笔记的，在「公式覆盖说明」列出编号和理由（如「(7) 为附录中的收敛性证明，与主线无关」）
  - 未编号但关键的公式（如损失函数）也要写
- 3.3 训练细节：损失、优化器、超参数、工程技巧

### 四、实验

- 主结果表、消融表必须完整转写为 Markdown 表格：数值与原表一致，保留原表加粗的最优值
- `\multicolumn`、`\multirow` 合并单元格展开为重复表头或空单元格
- 表下用 1-3 句话提炼结论，结论要带具体数字
- 关键结果图（scaling 曲线、效率对比）插入原图并给出读图要点

### 图片插入格式

```markdown
![Figure 3: DLRM 与 GR 的模型组件对比](images/<id>/fig3_xxx.png)

> 原图注：<caption 中文翻译>

**关键信息**：
- ...

**对应正文**：第 3 节
```

- 文件名使用 `parsed.json` 中 `figures[].files` 给出的实际路径，不要自行改名
- 一个 Figure 有多个子图文件（`_1`、`_2`）时，逐个插入，并标注左、中、右
- `warnings` 中提示「内容是表格」的 Figure，用其 `tabular` 字段转写为 Markdown 表格
- 无图片的 Figure（TikZ 绘制或格式无法转换）：用文字或 Mermaid 描述其结构，并注明「原图见论文 Figure n」

### 五、代码仓库

依据 `.cache/<id>/repo.md` 编写：

1. 仓库信息：链接、star 数、语言、许可证、最近更新时间
2. 架构图：GitDiagram 的 Mermaid（去掉 `click` 行）；附 GitDiagram 页面链接，并注明生成日期。图的生成日期明显早于仓库最近提交时，对照文件树核对，删除不存在的节点
3. 「论文模块与代码对应」表：

   | 论文模块 / 公式 | 代码位置 | 说明 |
   |---|---|---|
   | 公式 (2) Spatial Aggregation | `generative_recommenders/ops/...:函数名` | ... |

   代码位置必须打开克隆下来的源码确认，不能只凭文件名猜测
4. 关键实现片段：摘取核心函数的 10-30 行代码，标注文件路径，并与公式对照
5. 运行入口：训练、推理命令（来自 README）

论文没有代码时，写「论文未提供代码」，并在 frontmatter 中保持 `code_repo: 论文未提供代码`。

### 六、局限与适用场景 / 七、深度理解问答 / 八、总结

- 局限区分「作者承认的」与「分析得出的」（后者标注推测）
- 问答至少 4 个，用 `### Q1: ...` 格式，覆盖原理理解、细节辨析、边界条件、延伸思考
- 总结写 2-3 条核心贡献，要具体到技术点

全部章节写完后，把 `status` 改为 `filled`。

## Pass 3：自检回补（status: reviewed）

```bash
python3 ~/.claude/skills/arxiv-paper-notes/scripts/check_note.py <id>
```

1. 逐条修复 `issues`，然后重跑，直到 `pass: true`
2. 再按 [references/checklist.md](references/checklist.md) 人工核对脚本查不到的项（公式变量解释是否完整、结论是否带数字、推测是否已标注）
3. `status` 改为 `reviewed`，`revision` 加 1，`updated` 改为当天日期
4. 向用户报告：笔记路径、覆盖了哪些公式和图表、代码对应关系、仍存在的不确定点

## 写作规范

严格遵循 [references/writing-style.md](references/writing-style.md)：中文写作，术语保留英文原文；禁用套话；不用 emoji；客观叙述，不用第一人称。

## 注意事项

- 公式、数值、图注以 `parsed.json` 和原文为准，不凭记忆补写
- 修缮时只改目标章节，不要因重写而丢失已有内容
- `content.md` 很长时，先按章节标题定位，用 offset 和 limit 分段读取
- 依赖：仅需 Python 3 标准库；PDF 图片转换依次尝试 PyMuPDF、macOS `qlmanage`、`sips`；EPS 需要 `gs`（没有时会改用 HTML 版本的图片）
