# Pass 3 自检清单

逐项核对，未通过的项回到对应章节修补，再更新 frontmatter 的 `status: reviewed` 和 `revision`。

## 结构

- [ ] frontmatter 字段完整：`arxiv_id`、`title`、`version`、`authors`、`code_repo`、`status`、`revision`、`updated`
- [ ] 八个一级章节都存在，且没有残留的 `<!-- TODO` 占位
- [ ] TL;DR 不超过 3 句，包含「问题 + 方案 + 关键创新点」

## 公式（对照 `parsed.json` 的 `equations`）

- [ ] 方法章节中带 label 的公式都已出现在笔记中，或在「公式覆盖说明」里写明略过理由
- [ ] 每个公式都有「其中：」变量解释
- [ ] 自定义宏已展开，没有 `\mymacro` 这类无法渲染的命令
- [ ] 公式编号与原文一致（`\tag{n}`）

## 图表（对照 `parsed.json` 的 `figures` / `tables`）

- [ ] 整体架构图已插入
- [ ] 方法核心流程图已插入（如有）
- [ ] 主实验结果图或表已插入
- [ ] 主结果表、消融表已完整转写为 Markdown 表格
- [ ] 所有图片路径 `images/<arxiv_id>/...` 实际存在（运行 `scripts/check_note.py`）
- [ ] 每张图都有原图注翻译和「关键信息」

## 代码

- [ ] `parsed.json` 的 `links.code` 中的仓库已记录；若无，写明「论文未提供代码」
- [ ] 有 Mermaid 架构图（来自 gitdiagram 或本地分析），且 Mermaid 语法可渲染
- [ ] 「论文模块与代码对应」表至少覆盖方法章节的核心模块

## 写作

- [ ] 没有 `references/writing-style.md` 中列出的禁用套话（`scripts/check_note.py` 会扫描）
- [ ] 关键结论都带具体数字
- [ ] 推测内容已标注「推测」
- [ ] 问答至少 4 个，覆盖四种问题类型
