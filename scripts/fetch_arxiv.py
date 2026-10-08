#!/usr/bin/env python3
"""Fetch an arXiv paper (LaTeX source first, HTML fallback) and prepare note materials.

Usage:
    fetch_arxiv.py <arxiv_id_or_url> [--out DIR] [--force] [--no-note]

Outputs (DIR defaults to $PAPER_NOTES_DIR, else ~/paper-notes):
    DIR/.cache/<id>/meta.json      arXiv metadata + ranked code links
    DIR/.cache/<id>/parsed.json    sections / equations / figures / tables / macros
    DIR/.cache/<id>/content.md     readable full text with figure/table/equation markers
    DIR/.cache/<id>/src/           extracted LaTeX source (if available)
    DIR/images/<id>/               figures converted to png
    DIR/<id>.md                    note skeleton rendered from templates/note.md (if absent)
"""

import argparse
import datetime
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import latex_parse  # noqa: E402
import html_parse  # noqa: E402

DEFAULT_OUT = os.environ.get("PAPER_NOTES_DIR") or os.path.expanduser("~/paper-notes")
UA = "arxiv-paper-notes/1.0 (personal research notes)"
TEMPLATE = os.path.join(os.path.dirname(HERE), "templates", "note.md")


def log(*a):
    print("[fetch]", *a, file=sys.stderr)


def http_get(url, timeout=60, retries=3):
    last = None
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), r.geturl(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):
                raise
            last = e
        except Exception as e:  # network hiccup
            last = e
        time.sleep(2 * (k + 1))
    raise last


# ---------------------------------------------------------------- id + metadata

def normalize_id(s):
    s = s.strip()
    m = re.search(r"arxiv\.org/(?:abs|pdf|html|src|e-print)/([^\s?#]+)", s)
    if m:
        s = m.group(1)
    s = re.sub(r"\.pdf$", "", s).strip("/")
    s = re.sub(r"^arxiv:", "", s, flags=re.I)
    m = re.match(r"^((?:\d{4}\.\d{4,5})|(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}))(v\d+)?$", s)
    if not m:
        raise SystemExit("无法识别的 arXiv 编号: %s" % s)
    return m.group(1), (m.group(2) or "")


def fetch_metadata(aid):
    data, _, _ = http_get("http://export.arxiv.org/api/query?id_list=" + aid)
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(data)
    e = root.find("a:entry", ns)
    if e is None or e.find("a:title", ns) is None:
        raise SystemExit("arXiv API 未找到论文: " + aid)

    def txt(path):
        n = e.find(path, ns)
        return re.sub(r"\s+", " ", n.text).strip() if n is not None and n.text else ""

    full_id = txt("a:id").rsplit("/abs/", 1)[-1]
    vm = re.search(r"(v\d+)$", full_id)
    return {
        "arxiv_id": aid,
        "version": vm.group(1) if vm else "",
        "title": txt("a:title"),
        "authors": [re.sub(r"\s+", " ", a.find("a:name", ns).text).strip() for a in e.findall("a:author", ns)],
        "published": txt("a:published")[:10],
        "updated": txt("a:updated")[:10],
        "summary": txt("a:summary"),
        "comment": txt("x:comment"),
        "journal_ref": txt("x:journal_ref"),
        "primary_category": (e.find("x:primary_category", ns).attrib.get("term")
                             if e.find("x:primary_category", ns) is not None else ""),
        "categories": [c.attrib.get("term") for c in e.findall("a:category", ns)],
        "abs_url": "https://arxiv.org/abs/" + aid,
        "pdf_url": "https://arxiv.org/pdf/" + aid,
    }


def fetch_hf(aid):
    try:
        data, _, _ = http_get("https://huggingface.co/api/papers/" + aid, timeout=20, retries=1)
        d = json.loads(data)
        return {"githubRepo": d.get("githubRepo"), "projectPage": d.get("projectPage"),
                "ai_keywords": d.get("ai_keywords")}
    except Exception:
        return {}


# ---------------------------------------------------------------- source download

def download_source(aid, ver, cache, force):
    src_dir = os.path.join(cache, "src")
    marker = os.path.join(cache, "source_kind.txt")
    if not force and os.path.exists(marker):
        return open(marker).read().strip(), src_dir
    if os.path.isdir(src_dir):
        shutil.rmtree(src_dir)
    os.makedirs(src_dir)
    try:
        data, _, ctype = http_get("https://arxiv.org/src/" + aid + ver, timeout=120)
    except urllib.error.HTTPError as e:
        log("源码下载失败:", e)
        kind = "none"
        open(marker, "w").write(kind)
        return kind, src_dir

    kind = "none"
    if data[:4] == b"%PDF":
        kind = "pdf_only"
    else:
        raw = data
        if data[:2] == b"\x1f\x8b":
            raw = gzip.decompress(data)
        try:
            with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
                for m in tf.getmembers():
                    target = os.path.normpath(os.path.join(src_dir, m.name))
                    if not target.startswith(os.path.abspath(src_dir)) or m.issym() or m.islnk():
                        continue
                    tf.extract(m, src_dir)
            kind = "latex"
        except tarfile.TarError:
            if raw[:4] == b"%PDF":
                kind = "pdf_only"
            elif re.search(rb"\\documentclass|\\documentstyle|\\begin\{document\}|\\input\s*harvmac|\\bye\b", raw):
                with open(os.path.join(src_dir, "main.tex"), "wb") as f:
                    f.write(raw)
                kind = "latex"
    open(marker, "w").write(kind)
    return kind, src_dir


def fetch_html(aid, ver, cache, force):
    path = os.path.join(cache, "page.html")
    base_path = os.path.join(cache, "page_base.txt")
    if not force and os.path.exists(path):
        return open(path, encoding="utf-8").read(), open(base_path).read().strip()
    try:
        data, final, ctype = http_get("https://arxiv.org/html/" + aid + ver, timeout=60)
    except Exception as e:
        log("HTML 版本不可用:", e)
        return None, None
    html = data.decode("utf-8", "ignore")
    if "ltx_document" not in html and "ltx_page_main" not in html:
        return None, None
    m = re.search(r"<base\s+href=\"([^\"]+)\"", html)
    base = urllib.parse.urljoin(final, m.group(1)) if m else final
    open(path, "w", encoding="utf-8").write(html)
    open(base_path, "w").write(base)
    return html, base


# ---------------------------------------------------------------- images

try:
    import fitz  # PyMuPDF, optional
except Exception:  # pragma: no cover
    fitz = None


def pdf_to_png(src, dst):
    if fitz is not None:
        try:
            doc = fitz.open(src)
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(200 / 72.0, 200 / 72.0), alpha=False)
            pix.save(dst)
            return True
        except Exception as e:
            log("PyMuPDF 转换失败", src, e)
    if shutil.which("qlmanage"):
        tmp = dst + ".ql"
        os.makedirs(tmp, exist_ok=True)
        subprocess.run(["qlmanage", "-t", "-s", "1600", "-o", tmp, src],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        produced = [f for f in os.listdir(tmp) if f.endswith(".png")]
        if produced:
            shutil.move(os.path.join(tmp, produced[0]), dst)
            shutil.rmtree(tmp, ignore_errors=True)
            return True
        shutil.rmtree(tmp, ignore_errors=True)
    if shutil.which("sips"):
        r = subprocess.run(["sips", "-s", "format", "png", src, "--out", dst],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0 and os.path.exists(dst)
    return False


def convert_image(src, dst_noext):
    ext = os.path.splitext(src)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg"):
        dst = dst_noext + (".jpg" if ext == ".jpeg" else ext)
        shutil.copyfile(src, dst)
        return dst
    if ext == ".pdf":
        dst = dst_noext + ".png"
        return dst if pdf_to_png(src, dst) else None
    if ext in (".eps", ".ps") and shutil.which("gs"):
        dst = dst_noext + ".png"
        r = subprocess.run(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dEPSCrop", "-sDEVICE=png16m",
                            "-r200", "-sOutputFile=" + dst, src], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return dst if r.returncode == 0 else None
    return None


def slug_of(fig):
    for lb in fig.get("labels") or []:
        s = re.sub(r"^(fig|figure|f)[:_.-]?", "", lb, flags=re.I)
        s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
        if s and not s.startswith("s1-f") and not re.match(r"^s\d", s):
            return s[:40]
    return ""


def fig_basename(fig, k, total):
    slug = slug_of(fig)
    name = "fig%d" % fig["number"] + ("_" + slug if slug else "")
    return name + ("_%d" % (k + 1) if total > 1 else "")


def download_html_images(fig, base, img_dir):
    files = []
    imgs = fig["images"]
    for k, im in enumerate(imgs):
        url = urllib.parse.urljoin(base, im["ref"])
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or ".png"
        dst = os.path.join(img_dir, fig_basename(fig, k, len(imgs)) + ext)
        try:
            data, _, _ = http_get(url, timeout=60, retries=2)
            open(dst, "wb").write(data)
            files.append(dst)
        except Exception as e:
            log("图片下载失败", url, e)
    return files


# ---------------------------------------------------------------- links

JUNK_REPO = re.compile(r"github\.com/(pwc-\d+|paperswithcode|arXiv|brucemiller)/", re.I)


def rank_code_links(meta, hf, parsed_links, body_len):
    scored = {}

    def add(url, score, where):
        repo = latex_parse.normalize_repo(url)
        if not repo or JUNK_REPO.search(repo + "/"):
            return
        cur = scored.get(repo)
        if cur is None or score > cur["score"]:
            scored[repo] = {"repo": repo, "score": round(score, 1), "where": where}

    for l in latex_parse.extract_links(meta.get("comment", ""), "comment"):
        add(l["url"], 100, "arXiv comment")
    for l in latex_parse.extract_links(meta.get("summary", ""), "abstract"):
        add(l["url"], 100, "abstract")
    if hf.get("githubRepo"):
        add(hf["githubRepo"], 80, "huggingface papers")
    for l in parsed_links:
        frac = (l.get("pos", 0) / float(body_len)) if body_len else 1.0
        add(l["url"], 70 - 50 * min(frac, 1.0), "body@%.0f%%" % (100 * frac))
    others = sorted({l["url"] for l in parsed_links if not latex_parse.normalize_repo(l["url"])})
    return sorted(scored.values(), key=lambda x: -x["score"]), others


# ---------------------------------------------------------------- note skeleton

def render_note(meta, code_repo, source_kind, note_path):
    tpl = open(TEMPLATE, encoding="utf-8").read()
    authors = meta["authors"]
    author_str = ", ".join(authors[:6]) + (" et al." if len(authors) > 6 else "")
    venue = meta["journal_ref"] or meta["comment"] or ""
    published = meta["published"] + (" · " + venue if venue else "")
    values = {
        "arxiv_id": meta["arxiv_id"], "title": meta["title"].replace('"', "'"), "version": meta["version"],
        "authors": author_str.replace('"', "'"), "published": published.replace('"', "'"),
        "categories": ", ".join(meta["categories"]), "code_repo": code_repo or "论文未提供代码",
        "source_kind": source_kind, "today": datetime.date.today().isoformat(),
    }
    for k, v in values.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(tpl)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paper")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true", help="重新下载源码与 HTML")
    ap.add_argument("--no-note", action="store_true", help="不生成笔记骨架")
    ap.add_argument("--source", choices=["auto", "html"], default="auto",
                    help="auto: LaTeX 源码优先；html: 强制使用 arXiv HTML 版本")
    args = ap.parse_args()

    aid, ver_req = normalize_id(args.paper)
    fid = aid.replace("/", "_")
    out = os.path.abspath(os.path.expanduser(args.out))
    cache = os.path.join(out, ".cache", fid)
    img_dir = os.path.join(out, "images", fid)
    os.makedirs(cache, exist_ok=True)
    os.makedirs(img_dir, exist_ok=True)

    meta = fetch_metadata(aid)
    ver = ver_req or meta["version"]
    meta["version"] = ver
    hf = fetch_hf(aid)
    log("论文:", meta["title"], ver)

    if args.source == "html":
        source_kind, src_dir = "skipped", None
    else:
        source_kind, src_dir = download_source(aid, ver, cache, args.force)
    parsed = None
    if source_kind == "latex" and args.source == "auto":
        parsed = latex_parse.parse_source(src_dir)
        if parsed is None:
            log("源码中未找到主 tex，改用 HTML")
            source_kind = "latex_unparsable"

    html, base = (None, None)
    need_html = parsed is None or any(
        not any(im["path"] for im in f["images"]) for f in parsed["figures"])
    if need_html:
        html, base = fetch_html(aid, ver, cache, args.force)
    hparsed = html_parse.parse_html(html) if html else None

    if parsed is None and hparsed is None:
        print(json.dumps({"ok": False, "arxiv_id": aid,
                          "error": "该论文既没有 LaTeX 源码也没有 HTML 版本，当前 skill 不支持"}, ensure_ascii=False))
        sys.exit(2)

    for f in os.listdir(img_dir):
        if f.startswith("fig") and args.force:
            os.remove(os.path.join(img_dir, f))

    fig_files = {}
    warnings = []
    if parsed is not None:
        mode = "latex"
        html_figs = {f["number"]: f for f in (hparsed["figures"] if hparsed else [])}
        for fig in parsed["figures"]:
            files = []
            ok_imgs = [im for im in fig["images"] if im["path"]]
            for k, im in enumerate(ok_imgs):
                dst = convert_image(im["path"], os.path.join(img_dir, fig_basename(fig, k, len(ok_imgs))))
                if dst:
                    files.append(dst)
            if not files and fig["number"] in html_figs and base:
                hf_fig = dict(html_figs[fig["number"]], labels=fig["labels"])
                files = download_html_images(hf_fig, base, img_dir)
                if files:
                    fig["image_source"] = "html"
            if not files and fig.get("tabular"):
                warnings.append("Figure %d 内容是表格，按 parsed.json 中的 tabular 转写为 Markdown" % fig["number"])
            elif not files:
                warnings.append("Figure %d 无可用图片（%s）" % (
                    fig["number"], "TikZ 绘制" if fig["has_tikz"] else "格式无法转换"))
            fig["files"] = [os.path.relpath(p, out) for p in files]
            fig_files[fig["number"]] = fig["files"]
        if not parsed["sections"]:
            warnings.append("未识别到 \\section 结构（可能是 plain TeX / harvmac 等旧格式），请直接阅读 .cache 中的 flattened.tex")
        content = latex_parse.render_content_md(parsed, fig_files)
        equations, figures, tables = parsed["equations"], parsed["figures"], parsed["tables"]
        sections, macros, abstract = parsed["sections"], parsed["macros"], parsed["abstract"]
        links = parsed["links"]
        body_len = len(parsed["body_main"])
        main_tex = parsed["main_tex"]
        with open(os.path.join(cache, "flattened.tex"), "w", encoding="utf-8") as f:
            f.write(parsed["flattened_tex"])
    else:
        mode = "html"
        for fig in hparsed["figures"]:
            files = download_html_images(fig, base, img_dir)
            fig["files"] = [os.path.relpath(p, out) for p in files]
            fig_files[fig["number"]] = fig["files"]
            if not files:
                warnings.append("Figure %d 图片下载失败" % fig["number"])
        content = hparsed["content_md"]
        for n, files in fig_files.items():
            content = content.replace("{fig%s}" % n, ", ".join(files) or "(none)")
        equations, figures, tables = hparsed["equations"], hparsed["figures"], hparsed["tables"]
        sections, macros, abstract = hparsed["sections"], {}, hparsed["abstract"]
        links = latex_parse.extract_links("\n".join(hparsed["hrefs"]), "body")
        body_len = len("\n".join(hparsed["hrefs"])) or 1
        main_tex = None

    code_links, other_links = rank_code_links(meta, hf, links, body_len)
    meta.update({"source_kind": mode, "main_tex": main_tex, "huggingface": hf,
                 "code_links": code_links, "other_links": other_links[:30]})

    parsed_out = {
        "arxiv_id": aid, "version": ver, "source": mode, "abstract": abstract or meta["summary"],
        "sections": sections, "macros": macros, "equations": equations,
        "figures": figures, "tables": tables, "warnings": warnings,
    }
    json.dump(meta, open(os.path.join(cache, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(parsed_out, open(os.path.join(cache, "parsed.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    header = "# %s\n\narXiv: %s%s | source: %s\n\n## Abstract\n\n%s\n\n" % (
        meta["title"], aid, ver, mode, abstract or meta["summary"])
    with open(os.path.join(cache, "content.md"), "w", encoding="utf-8") as f:
        f.write(header + content)

    note_path = os.path.join(out, fid + ".md")
    note_created = False
    if not args.no_note and not os.path.exists(note_path):
        render_note(meta, code_links[0]["repo"] if code_links else "", mode, note_path)
        note_created = True

    numbered = [e for e in equations if e.get("numbers") and not e.get("appendix")]
    summary = {
        "ok": True, "arxiv_id": aid, "version": ver, "title": meta["title"], "source": mode,
        "note": note_path, "note_created": note_created,
        "cache": cache, "content_md": os.path.join(cache, "content.md"),
        "parsed_json": os.path.join(cache, "parsed.json"), "images_dir": img_dir,
        "counts": {"sections": len(sections), "equations": len(equations),
                   "numbered_equations_main": len(numbered), "figures": len(figures),
                   "figure_files": sum(len(v) for v in fig_files.values()), "tables": len(tables),
                   "macros": len(macros)},
        "code_links": code_links[:5], "warnings": warnings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
