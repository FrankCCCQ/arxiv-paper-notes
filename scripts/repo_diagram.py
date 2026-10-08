#!/usr/bin/env python3
"""Collect code-repository context for a paper note.

Usage:
    repo_diagram.py <repo_url | owner/repo> --arxiv-id <id> [--out DIR] [--no-clone]

1. GitHub metadata (stars, language, license, last push)
2. GitDiagram architecture (https://gitdiagram.com/<owner>/<repo>.md) if already generated
3. Shallow clone into DIR/.cache/<id>/repo, then file tree + README + Python/C++ symbol index

Writes DIR/.cache/<id>/repo.md and prints a JSON summary.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_OUT = os.environ.get("PAPER_NOTES_DIR") or os.path.expanduser("~/paper-notes")
UA = "arxiv-paper-notes/1.0"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", ".vscode",
             "wandb", "outputs", "logs", "checkpoints", ".github", "assets", "images", "img", "docs/_build"}
CODE_EXT = {".py", ".cu", ".cuh", ".cpp", ".cc", ".h", ".hpp", ".rs", ".go", ".ts", ".js", ".jl", ".java", ".scala"}


def log(*a):
    print("[repo]", *a, file=sys.stderr)


def get(url, timeout=40):
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def parse_repo(s):
    s = s.strip().rstrip("/")
    m = re.match(r"(?:https?://)?(?:www\.)?(github\.com|gitlab\.com)/([\w.-]+)/([\w.-]+)", s)
    if m:
        return m.group(1), m.group(2), re.sub(r"\.git$", "", m.group(3))
    m = re.match(r"^([\w.-]+)/([\w.-]+)$", s)
    if m:
        return "github.com", m.group(1), m.group(2)
    raise SystemExit("无法识别的仓库地址: " + s)


def github_meta(owner, repo):
    try:
        d = json.loads(get("https://api.github.com/repos/%s/%s" % (owner, repo)))
        return {"full_name": d.get("full_name"), "description": d.get("description"),
                "stars": d.get("stargazers_count"), "forks": d.get("forks_count"),
                "language": d.get("language"), "license": (d.get("license") or {}).get("spdx_id"),
                "default_branch": d.get("default_branch"), "pushed_at": (d.get("pushed_at") or "")[:10],
                "archived": d.get("archived"), "topics": d.get("topics")}
    except Exception as e:
        log("GitHub API 失败:", e)
        return {}


def gitdiagram(owner, repo):
    url = "https://gitdiagram.com/%s/%s.md" % (owner, repo)
    try:
        text = get(url, timeout=60)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore") if hasattr(e, "read") else ""
        if "no stored diagram" in body:
            return {"status": "not_generated", "page": "https://gitdiagram.com/%s/%s" % (owner, repo)}
        return {"status": "error", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": str(e)}
    m = re.search(r"```mermaid\n(.*?)```", text, re.S)
    if not m:
        return {"status": "no_mermaid", "raw": text}
    mermaid = m.group(1)
    clicks = re.findall(r'^\s*click\s+(\S+)\s+"([^"]+)"', mermaid, re.M)
    labels = dict(re.findall(r'^\s*(\w+)\s*[\[\(\{]+"([^"]+)"', mermaid, re.M))
    clean = "\n".join(ln for ln in mermaid.splitlines() if not re.match(r"\s*click\s", ln)).strip()
    ov = re.search(r"## Overview\n(.*?)(?=\n## )", text, re.S)
    overview = re.sub(r"</?explanation>", "", ov.group(1)).strip() if ov else ""
    components = []
    for node, link in clicks:
        path = re.sub(r"^https://github\.com/[^/]+/[^/]+/(?:blob|tree)/[^/]+/?", "", link)
        components.append({"node": node, "label": re.sub(r"<br/>.*", "", labels.get(node, node)),
                           "path": path or "/", "url": link})
    updated = re.search(r"last updated (\d{4}-\d{2}-\d{2})", text)
    return {"status": "ok", "page": "https://gitdiagram.com/%s/%s" % (owner, repo),
            "updated": updated.group(1) if updated else "", "overview": overview,
            "mermaid": clean, "components": components}


def clone(host, owner, repo, dest):
    if os.path.isdir(os.path.join(dest, ".git")):
        return True
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    url = "https://%s/%s/%s.git" % (host, owner, repo)
    r = subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:limit=2m", "--quiet", url, dest],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    if r.returncode != 0:
        log("clone 失败:", r.stderr.decode()[-300:])
        return False
    return True


def file_tree(root, max_depth=4, max_entries=400):
    lines, count = [], 0
    for dirpath, dirs, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        if depth >= max_depth:
            dirs[:] = []
        indent = "  " * depth
        if rel != ".":
            lines.append("%s%s/" % ("  " * (depth - 1), os.path.basename(dirpath)))
        shown = [f for f in sorted(files) if not f.startswith(".") and not re.search(
            r"\.(png|jpe?g|gif|pdf|svg|ipynb_checkpoints|pkl|pt|bin|npy|npz|parquet|csv|json)$", f, re.I)]
        for f in shown[:40]:
            lines.append(indent + f)
            count += 1
        if len(shown) > 40:
            lines.append(indent + "... (%d more files)" % (len(shown) - 40))
        if count > max_entries:
            lines.append("... (truncated)")
            break
    return "\n".join(lines)


def symbol_index(root, max_files=150):
    out, n = [], 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(files):
            ext = os.path.splitext(f)[1]
            if ext not in CODE_EXT or re.search(r"(^|/)(tests?|examples?)/", os.path.relpath(dirpath, root) + "/"):
                continue
            p = os.path.join(dirpath, f)
            try:
                text = open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            if ext == ".py":
                syms = re.findall(r"^(class\s+\w+[^:]*|def\s+\w+)", text, re.M)
            else:
                syms = re.findall(r"^(?:__global__\s+)?(?:template\s*<[^>]*>\s*)?(?:class|struct)\s+\w+|^\w[\w:<>\s\*&]*\s+(\w+)\s*\([^;]*\)\s*\{",
                                  text, re.M)
                syms = [s for s in syms if s]
            if syms:
                rel = os.path.relpath(p, root)
                out.append("%s (%d lines): %s" % (rel, text.count("\n"), "; ".join(s.strip()[:60] for s in syms[:25])))
                n += 1
            if n >= max_files:
                return "\n".join(out) + "\n... (truncated)"
    return "\n".join(out)


def readme(root, limit=6000):
    for name in ("README.md", "README.rst", "README.txt", "README", "readme.md"):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            return open(p, encoding="utf-8", errors="ignore").read()[:limit]
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--arxiv-id", required=True)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-clone", action="store_true")
    args = ap.parse_args()

    host, owner, repo = parse_repo(args.repo)
    fid = args.arxiv_id.replace("/", "_")
    cache = os.path.join(os.path.abspath(os.path.expanduser(args.out)), ".cache", fid)
    os.makedirs(cache, exist_ok=True)
    repo_url = "https://%s/%s/%s" % (host, owner, repo)

    meta = github_meta(owner, repo) if host == "github.com" else {}
    gd = gitdiagram(owner, repo) if host == "github.com" else {"status": "unsupported_host"}
    log("gitdiagram:", gd["status"])

    local = os.path.join(cache, "repo")
    cloned = False if args.no_clone else clone(host, owner, repo, local)

    md = ["# %s/%s" % (owner, repo), "", "- URL: " + repo_url]
    for k in ("description", "stars", "language", "license", "pushed_at", "archived"):
        if meta.get(k) not in (None, ""):
            md.append("- %s: %s" % (k, meta[k]))
    md.append("- gitdiagram: %s %s" % (gd["status"], gd.get("page", "")))
    md.append("")
    if gd["status"] == "ok":
        md += ["## GitDiagram overview (updated %s)" % gd["updated"], "", gd["overview"], "",
               "## GitDiagram mermaid", "", "```mermaid", gd["mermaid"], "```", "",
               "## GitDiagram components", ""]
        md += ["- %s: `%s`" % (c["label"], c["path"]) for c in gd["components"]]
        md.append("")
    if cloned:
        md += ["## File tree", "", "```", file_tree(local), "```", "",
               "## Symbol index", "", "```", symbol_index(local), "```", "",
               "## README (head)", "", readme(local)]
    with open(os.path.join(cache, "repo.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    json.dump({"repo": repo_url, "github": meta, "gitdiagram": gd},
              open(os.path.join(cache, "repo.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(json.dumps({
        "repo": repo_url, "stars": meta.get("stars"), "gitdiagram": gd["status"],
        "gitdiagram_page": gd.get("page"), "mermaid_nodes": len(re.findall(r"^\s*\w+\[", gd.get("mermaid", ""), re.M)),
        "cloned": cloned, "local_path": local if cloned else None,
        "repo_md": os.path.join(cache, "repo.md"),
        "next": ("把 repo.md 中的 mermaid 和组件表写入笔记「五、代码仓库」" if gd["status"] == "ok" else
                 "gitdiagram 未收录：可在浏览器打开 gitdiagram_page 触发生成（约 1 分钟）后重跑本脚本；"
                 "或根据 repo.md 的文件树和符号索引自行绘制 Mermaid"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
