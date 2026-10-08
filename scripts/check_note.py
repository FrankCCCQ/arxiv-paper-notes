#!/usr/bin/env python3
"""Mechanical self-check of a paper note against parsed.json (Pass 3).

Usage:
    check_note.py <arxiv_id> [--out DIR]

Prints a JSON report: {"pass": bool, "issues": [...], "stats": {...}}.
"""

import argparse
import json
import os
import re
import sys

DEFAULT_OUT = os.environ.get("PAPER_NOTES_DIR") or os.path.expanduser("~/paper-notes")
BANNED = ["本文的核心贡献是", "该方法的优势在于", "综上所述", "值得注意的是", "具有重要意义", "广泛应用前景",
          "提供了新思路", "这是一篇重要的工作", "不难发现", "众所周知", "总而言之", "我认为", "我的理解是"]
REQUIRED_FM = ["arxiv_id", "title", "version", "authors", "code_repo", "status", "revision", "updated"]
SECTIONS = ["TL;DR", "一、背景", "二、问题", "三、方法", "四、实验", "五、代码仓库", "六、局限与适用场景",
            "七、深度理解问答", "八、总结"]


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arxiv_id")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    fid = args.arxiv_id.replace("/", "_")
    out = os.path.abspath(os.path.expanduser(args.out))
    note_path = os.path.join(out, fid + ".md")
    if not os.path.exists(note_path):
        sys.exit("笔记不存在: " + note_path)
    text = open(note_path, encoding="utf-8").read()
    parsed_path = os.path.join(out, ".cache", fid, "parsed.json")
    parsed = json.load(open(parsed_path, encoding="utf-8")) if os.path.exists(parsed_path) else {}
    meta_path = os.path.join(out, ".cache", fid, "meta.json")
    meta = json.load(open(meta_path, encoding="utf-8")) if os.path.exists(meta_path) else {}
    issues = []

    fm = frontmatter(text)
    for k in REQUIRED_FM:
        if not fm.get(k) and fm.get(k) != "0":
            issues.append("frontmatter 缺少字段: " + k)
    for s in SECTIONS:
        if not re.search(r"^##\s+" + re.escape(s), text, re.M):
            issues.append("缺少章节: " + s)
    todos = re.findall(r"<!--\s*TODO[^>]*-->", text)
    if todos:
        issues.append("残留 %d 处 TODO 占位: %s" % (len(todos), "; ".join(t[:40] for t in todos[:5])))

    tldr = re.search(r"## TL;DR\n(.*?)\n## ", text, re.S)
    if tldr and len(re.sub(r"\s", "", tldr.group(1))) > 220:
        issues.append("TL;DR 过长（>200 字）")

    # images
    img_refs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text)
    missing = [p for p in img_refs if not p.startswith("http") and not os.path.exists(os.path.join(out, p))]
    if missing:
        issues.append("图片路径不存在: " + ", ".join(missing))
    fig_numbers_in_note = set()
    for f in parsed.get("figures", []):
        files = f.get("files") or []
        if any(p in img_refs for p in files):
            fig_numbers_in_note.add(f["number"])
    if parsed.get("figures") and not fig_numbers_in_note:
        issues.append("笔记中没有插入任何论文原图")

    # equations
    eq_missing = []
    for e in parsed.get("equations", []):
        if e.get("appendix") or not e.get("numbers"):
            continue
        hit = any(re.search(r"\\tag\{%s\}" % re.escape(n), text) for n in e["numbers"]) or \
            any(lb and lb in text for lb in e["labels"]) or \
            any(re.search(r"(公式|式|Eq\.?)\s*[（(]?%s[)）]" % re.escape(n), text) for n in e["numbers"])
        if not hit:
            eq_missing.append("(%s) %s" % (",".join(e["numbers"]), ",".join(e["labels"])))
    if eq_missing:
        issues.append("正文带编号公式未覆盖，且未在「公式覆盖说明」中提及: " + "; ".join(eq_missing))
    macros = parsed.get("macros", {})
    leaked = sorted({m for m in macros if re.search(re.escape(m) + r"(?![A-Za-z])", text)})
    if leaked:
        issues.append("笔记中可能残留论文自定义宏（若是标准 LaTeX 命令可忽略）: " + ", ".join(leaked[:10]))

    # tables
    md_tables = len(re.findall(r"^\|.*\|\s*\n\|[\s:|-]+\|\s*$", text, re.M))
    if parsed.get("tables") and md_tables == 0:
        issues.append("论文有 %d 个表格，但笔记中没有 Markdown 表格" % len(parsed["tables"]))

    # code
    code_section = re.search(r"## 五、代码仓库\n(.*?)(?=\n## )", text, re.S)
    code_links = meta.get("code_links") or []
    if code_links:
        top = code_links[0]["repo"].lower()
        if top not in text.lower():
            issues.append("未记录代码仓库: " + code_links[0]["repo"])
        if code_section and "```mermaid" not in code_section.group(1):
            issues.append("代码仓库章节缺少 Mermaid 架构图")
        if code_section and not re.search(r"^\|.*\|", code_section.group(1), re.M):
            issues.append("代码仓库章节缺少「论文模块与代码对应」表")
    for block in re.findall(r"```mermaid\n(.*?)```", text, re.S):
        if block.count('"') % 2:
            issues.append("Mermaid 代码块引号不成对")
        first = block.strip().splitlines()[0] if block.strip() else ""
        if not re.match(r"(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram)", first):
            issues.append("Mermaid 代码块首行不是图类型声明: " + first[:30])

    # writing
    hits = [w for w in BANNED if w in text]
    if hits:
        issues.append("出现禁用套话: " + "、".join(hits))
    qa = re.search(r"## 七、深度理解问答\n(.*?)(?=\n## |\Z)", text, re.S)
    nq = len(re.findall(r"^###\s*Q\d+", qa.group(1), re.M)) if qa else 0
    if nq < 4:
        issues.append("深度理解问答少于 4 个（当前 %d）" % nq)

    report = {
        "pass": not issues, "note": note_path, "status": fm.get("status"), "revision": fm.get("revision"),
        "issues": issues,
        "stats": {"chars": len(text), "images": len(img_refs), "figures_covered": sorted(fig_numbers_in_note),
                  "figures_total": len(parsed.get("figures", [])), "md_tables": md_tables,
                  "tables_total": len(parsed.get("tables", [])), "display_equations": text.count("$$") // 2,
                  "qa": nq},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
