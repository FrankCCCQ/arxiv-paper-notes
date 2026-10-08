"""Parse an arXiv LaTeX source tree into structured data for note writing."""

import os
import re
from bisect import bisect_right

MATH_ENVS = [
    "equation", "equation*", "align", "align*", "gather", "gather*",
    "multline", "multline*", "eqnarray", "eqnarray*", "flalign", "flalign*",
    "alignat", "alignat*", "displaymath", "math",
]
MULTILINE_ENVS = {"align", "gather", "eqnarray", "flalign", "alignat"}
INNER_MATH_ENVS = r"aligned|split|cases|array|matrix|pmatrix|bmatrix|vmatrix|Bmatrix|smallmatrix|gathered|subarray|dcases|rcases"
FIGURE_ENVS = ["figure", "figure*", "wrapfigure", "SCfigure"]
TABLE_ENVS = ["table", "table*", "wraptable"]
SECTION_LEVELS = {"part": 0, "section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}
IMAGE_EXTS = [".pdf", ".png", ".jpg", ".jpeg", ".eps", ".ps", ".svg"]


# ---------------------------------------------------------------- basics

def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def strip_comments(text):
    text = re.sub(r"\\begin\{comment\}.*?\\end\{comment\}", "", text, flags=re.S)
    text = re.sub(r"\\iffalse\b.*?\\fi\b", "", text, flags=re.S)
    out = []
    for line in text.split("\n"):
        m = re.search(r"(?<!\\)%", line)
        out.append(line[: m.start()] if m else line)
    return "\n".join(out)


def read_group(s, i, open_ch="{", close_ch="}"):
    """s[i] must be open_ch. Returns (inner, index_after_close)."""
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return s[i + 1: j], j + 1
        j += 1
    return s[i + 1:], len(s)


def skip_ws(s, i):
    while i < len(s) and s[i] in " \t\n":
        i += 1
    return i


def command_arg(s, cmd_end):
    """Read optional [..] args and the first {..} argument after a command."""
    i = skip_ws(s, cmd_end)
    opts = []
    while i < len(s) and s[i] == "[":
        opt, i = read_group(s, i, "[", "]")
        opts.append(opt)
        i = skip_ws(s, i)
    if i < len(s) and s[i] == "{":
        arg, i = read_group(s, i)
        return opts, arg, i
    return opts, None, i


def find_commands(s, name):
    """Yield (start, opts, arg, end) for every \\name{...} (also \\name*)."""
    for m in re.finditer(r"\\" + re.escape(name) + r"\*?(?![A-Za-z])", s):
        opts, arg, end = command_arg(s, m.end())
        if arg is not None:
            yield m.start(), opts, arg, end


# ---------------------------------------------------------------- source tree

def find_main_tex(root):
    candidates = []
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if not fn.lower().endswith(".tex"):
                continue
            p = os.path.join(dirpath, fn)
            t = strip_comments(read_text(p))
            if re.search(r"\\documentclass|\\documentstyle|\\input\s*harvmac|\\bye\b", t):
                score = 0
                if "\\begin{document}" in t:
                    score += 10
                if os.path.splitext(fn)[0].lower() in ("main", "paper", "ms", "manuscript", "arxiv"):
                    score += 3
                if dirpath == root:
                    score += 2
                if re.search(r"supp|appendix|rebuttal|response", fn, re.I):
                    score -= 5
                candidates.append((score, len(t), p))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def _resolve_tex(root, base_dir, name):
    name = name.strip().strip('"')
    for d in (base_dir, root):
        for cand in (name, name + ".tex"):
            p = os.path.normpath(os.path.join(d, cand))
            if os.path.isfile(p):
                return p
    return None


def flatten(root, path, depth=0, seen=None):
    seen = seen if seen is not None else set()
    if depth > 12 or path in seen:
        return ""
    seen.add(path)
    text = strip_comments(read_text(path))
    base_dir = os.path.dirname(path)

    def inline(m, p):
        if not p:
            return m.group(0)
        return "\n" + flatten(root, p, depth + 1, seen) + "\n"

    text = re.sub(r"\\(?:sub)?import\s*\{([^}]*)\}\s*\{([^}]*)\}",
                  lambda m: inline(m, _resolve_tex(root, os.path.join(base_dir, m.group(1)), m.group(2))), text)
    text = re.sub(r"\\(?:input|include|subfile)\s*\{([^}]*)\}",
                  lambda m: inline(m, _resolve_tex(root, base_dir, m.group(1))), text)
    text = re.sub(r"\\input\s+([A-Za-z0-9_./-]+)",
                  lambda m: inline(m, _resolve_tex(root, base_dir, m.group(1))), text)
    return text


# ---------------------------------------------------------------- macros

def parse_macros(tex):
    macros = {}
    for m in re.finditer(r"\\(?:re)?newcommand\*?|\\providecommand\*?|\\DeclareRobustCommand\*?", tex):
        i = skip_ws(tex, m.end())
        if i < len(tex) and tex[i] == "{":
            name, i = read_group(tex, i)
        else:
            nm = re.match(r"\\[A-Za-z@]+|\\.", tex[i:])
            if not nm:
                continue
            name, i = nm.group(0), i + nm.end()
        name = name.strip()
        nargs, default = 0, None
        i = skip_ws(tex, i)
        if i < len(tex) and tex[i] == "[":
            n, i = read_group(tex, i, "[", "]")
            try:
                nargs = int(n.strip())
            except ValueError:
                continue
            i = skip_ws(tex, i)
            if i < len(tex) and tex[i] == "[":
                default, i = read_group(tex, i, "[", "]")
        i = skip_ws(tex, i)
        if i < len(tex) and tex[i] == "{":
            body, _ = read_group(tex, i)
            if re.match(r"^\\[A-Za-z@]+$", name):
                macros[name] = {"nargs": nargs, "default": default, "body": body}
    for m in re.finditer(r"\\def\s*(\\[A-Za-z@]+)((?:#\d)*)\s*\{", tex):
        body, _ = read_group(tex, m.end() - 1)
        macros.setdefault(m.group(1), {"nargs": len(m.group(2)) // 2, "default": None, "body": body})
    for m in re.finditer(r"\\DeclareMathOperator(\*?)\s*\{?\s*(\\[A-Za-z]+)\s*\}?\s*\{", tex):
        body, _ = read_group(tex, m.end() - 1)
        op = "\\operatorname*" if m.group(1) else "\\operatorname"
        macros[m.group(2)] = {"nargs": 0, "default": None, "body": op + "{" + body + "}"}
    # Drop macros that only make sense for typesetting control.
    for k in list(macros):
        b = macros[k]["body"]
        if "\\makeatletter" in b or "\\def" in b or "\\newcommand" in b or len(b) > 400:
            macros.pop(k)
    return macros


def _read_macro_arg(s, i):
    i = skip_ws(s, i)
    if i >= len(s):
        return "", i
    if s[i] == "{":
        return read_group(s, i)
    if s[i] == "\\":
        m = re.match(r"\\[A-Za-z]+|\\.", s[i:])
        return m.group(0), i + m.end()
    return s[i], i + 1


def expand_macros(s, macros, passes=6):
    if not macros:
        return s
    names = sorted(macros, key=len, reverse=True)
    pat = re.compile("(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z@])")
    for _ in range(passes):
        out, pos, changed = [], 0, False
        for m in pat.finditer(s):
            if m.start() < pos:
                continue
            spec = macros[m.group(1)]
            i = m.end()
            args = []
            n = spec["nargs"]
            if spec["default"] is not None and n > 0:
                j = skip_ws(s, i)
                if j < len(s) and s[j] == "[":
                    a, i = read_group(s, j, "[", "]")
                else:
                    a = spec["default"]
                args.append(a)
                n -= 1
            for _k in range(n):
                a, i = _read_macro_arg(s, i)
                args.append(a)
            body = spec["body"]
            for k, a in enumerate(args, 1):
                body = body.replace("#" + str(k), a)
            if body and re.match(r"[A-Za-z]", s[i:i + 1] or " ") and re.search(r"\\[A-Za-z]+$", body):
                body += " "
            out.append(s[pos:m.start()])
            out.append(body)
            pos = i
            changed = True
        out.append(s[pos:])
        s = "".join(out)
        if not changed:
            break
    s = re.sub(r"\\ensuremath\s*\{", "{", s)
    s = re.sub(r"\\xspace(?![A-Za-z])", "", s)
    return s


# ---------------------------------------------------------------- environment scan

def _env_names_regex(names):
    return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))


def find_env_end(s, name, start):
    """start points right after \\begin{name}. Returns (inner_end, env_end)."""
    b = re.compile(r"\\begin\{" + re.escape(name) + r"\}")
    e = re.compile(r"\\end\{" + re.escape(name) + r"\}")
    depth, i = 1, start
    while True:
        mb, me = b.search(s, i), e.search(s, i)
        if not me:
            return len(s), len(s)
        if mb and mb.start() < me.start():
            depth += 1
            i = mb.end()
        else:
            depth -= 1
            i = me.end()
            if depth == 0:
                return me.start(), me.end()


def scan_blocks(body):
    """Find top-level figure / table / display-math blocks in document order."""
    names = MATH_ENVS + FIGURE_ENVS + TABLE_ENVS
    pat = re.compile(r"\\begin\{(" + _env_names_regex(names) + r")\}|\\\[|\$\$")
    blocks, i = [], 0
    while True:
        m = pat.search(body, i)
        if not m:
            break
        if m.group(1):
            name = m.group(1)
            inner_end, end = find_env_end(body, name, m.end())
            inner = body[m.end():inner_end]
            kind = "figure" if name in FIGURE_ENVS else "table" if name in TABLE_ENVS else "math"
            if kind == "math" and name == "math":
                i = end
                continue
            blocks.append({"kind": kind, "env": name, "start": m.start(), "end": end, "inner": inner})
        else:
            closer = r"\\\]" if m.group(0) == "\\[" else r"\$\$"
            mc = re.compile(closer).search(body, m.end())
            end = mc.end() if mc else len(body)
            inner = body[m.end(): mc.start() if mc else len(body)]
            blocks.append({"kind": "math", "env": "displaymath", "start": m.start(), "end": end, "inner": inner})
        i = blocks[-1]["end"] if blocks else m.end()
    return blocks


# ---------------------------------------------------------------- sections

def scan_sections(body):
    pat = re.compile(r"\\(part|section|subsection|subsubsection|paragraph)(\*?)(?![A-Za-z])|\\appendix(?![A-Za-z])")
    out = []
    for m in pat.finditer(body):
        if m.group(0).startswith("\\appendix"):
            out.append({"cmd": "appendix", "start": m.start()})
            continue
        opts, title, end = command_arg(body, m.end())
        if title is None:
            continue
        out.append({"cmd": m.group(1), "starred": bool(m.group(2)), "title": clean_inline(title),
                    "start": m.start(), "end": end})
    return out


def number_sections(secs):
    counters = [0, 0, 0, 0, 0]
    appendix = False
    result = []
    for s in secs:
        if s["cmd"] == "appendix":
            appendix = True
            counters = [0, 0, 0, 0, 0]
            continue
        lvl = SECTION_LEVELS[s["cmd"]]
        num = ""
        if not s["starred"] and 1 <= lvl <= 3:
            counters[lvl] += 1
            for k in range(lvl + 1, 5):
                counters[k] = 0
            parts = [counters[k] for k in range(1, lvl + 1)]
            top = chr(ord("A") + parts[0] - 1) if appendix and parts[0] > 0 else str(parts[0])
            num = ".".join([top] + [str(p) for p in parts[1:]])
        result.append(dict(s, level=lvl, number=num, appendix=appendix))
    return result


# ---------------------------------------------------------------- text cleanup

def clean_inline(t):
    t = re.sub(r"\\label\{[^}]*\}", "", t)
    t = re.sub(r"\\(?:textbf|textit|emph|textsc|texttt|mathrm|textrm|underline|text)\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\(?:footnote|thanks)\{[^{}]*\}", "", t)
    t = re.sub(r"~", " ", t)
    t = re.sub(r"\\\\", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


SIZE_CMDS = r"\\(?:tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|huge|Huge)(?![A-Za-z])\s*"


def unwrap_braces(t):
    t = t.strip()
    while t.startswith("{") and t.endswith("}"):
        inner, end = read_group(t, 0)
        if end != len(t):
            break
        t = inner.strip()
    return t


def clean_math(t):
    t = re.sub(r"\\label\{[^}]*\}", "", t)
    t = re.sub(SIZE_CMDS, "", t)
    t = re.sub(r"\n\s*\n", "\n", t)
    return t.strip()


def replace_cmd(t, names, fmt):
    """Replace \\name{arg} with fmt % arg, matching nested braces in arg."""
    pat = re.compile(r"\\(?:" + "|".join(names) + r")(?![A-Za-z])\s*\{")
    for _ in range(50):
        m = pat.search(t)
        if not m:
            break
        arg, end = read_group(t, m.end() - 1)
        inner = arg.strip()
        if inner.startswith("{") and inner.endswith("}"):
            inner = unwrap_braces(inner)
        t = t[:m.start()] + (fmt % inner) + t[end:]
    return t


FRONT_MATTER_CMDS = ["title", "author", "date", "affiliation", "affil", "address", "email", "thanks",
                     "icmltitle", "icmltitlerunning", "titlerunning", "authorrunning", "keywords"]


def latex_to_md_text(t):
    t = replace_cmd(t, FRONT_MATTER_CMDS, "%.0s")
    t = re.sub(r"\\label\{[^}]*\}", "", t)
    t = re.sub(SIZE_CMDS, "", t)
    t = unwrap_braces(t) if t.strip().startswith("{") else t
    t = re.sub(r"\\(?:cite[tp]?|citep|citet|citeauthor|citeyear|parencite|autocite)\*?(?:\[[^\]]*\])*\{([^}]*)\}",
               lambda m: "[cite:" + m.group(1).replace(" ", "") + "]", t)
    t = re.sub(r"\\eqref\{([^}]*)\}", r"(ref:\1)", t)
    t = re.sub(r"\\(?:auto|c|C)?ref\*?\{([^}]*)\}", r"[ref:\1]", t)
    t = re.sub(r"\\url\{([^}]*)\}", r"<\1>", t)
    t = re.sub(r"\\href\{([^}]*)\}\{([^}]*)\}", r"[\2](\1)", t)
    t = re.sub(r"\{\\(?:bf|bfseries)\s+([^{}]*)\}", r"**\1**", t)
    t = re.sub(r"\{\\(?:it|em|itshape)\s+([^{}]*)\}", r"*\1*", t)
    t = replace_cmd(t, ["textbf"], "**%s**")
    t = replace_cmd(t, ["textit", "emph"], "*%s*")
    t = replace_cmd(t, ["texttt"], "`%s`")
    t = replace_cmd(t, ["textsc", "textrm", "textsf", "underline", "mbox"], "%s")
    t = replace_cmd(t, ["footnote"], " (footnote: %s)")
    t = re.sub(r"\\looseness\s*=\s*-?\d+", "", t)
    t = re.sub(r"\\begin\{(itemize|enumerate|description)\}(\[[^\]]*\])?|\\end\{(itemize|enumerate|description)\}", "", t)
    t = re.sub(r"\\item\s*(\[[^\]]*\])?", lambda m: "\n- " + ((m.group(1) or "")[1:-1] + " " if m.group(1) else ""), t)
    t = re.sub(r"\\(?:vspace|hspace)\*?\{[^}]*\}", "", t)
    t = re.sub(r"\\(?:noindent|centering|medskip|smallskip|bigskip|newpage|clearpage|maketitle|par)(?![A-Za-z])", "", t)
    for _ in range(3):
        t = re.sub(r"(?<![\\\w}\]^_])\{([^{}\\$]*)\}", r"\1", t)
    t = t.replace("~", " ").replace("``", '"').replace("''", '"')
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
    return t


# ---------------------------------------------------------------- equations

def _strip_inner_envs(s):
    return re.sub(r"\\begin\{(" + INNER_MATH_ENVS + r")\}.*?\\end\{\1\}", " ", s, flags=re.S)


def equation_lines(env, inner):
    base = env.rstrip("*")
    starred = env.endswith("*") or env == "displaymath"
    if base in MULTILINE_ENVS:
        flat = _strip_inner_envs(inner)
        lines = [ln for ln in re.split(r"\\\\(?:\[[^\]]*\])?", flat) if ln.strip()]
    else:
        lines = [inner]
    result = []
    for ln in lines:
        tag = re.search(r"\\tag\*?\{([^}]*)\}", ln)
        numbered = (not starred) and not re.search(r"\\(nonumber|notag)\b", ln)
        result.append({"numbered": numbered, "tag": tag.group(1) if tag else None,
                       "labels": re.findall(r"\\label\{([^}]*)\}", ln)})
    if base not in MULTILINE_ENVS and len(result) == 1:
        result[0]["labels"] = re.findall(r"\\label\{([^}]*)\}", inner)
    return result


# ---------------------------------------------------------------- figures / tables

def find_graphicspath(tex):
    m = re.search(r"\\graphicspath\s*\{((?:\{[^}]*\})+)\}", tex)
    return re.findall(r"\{([^}]*)\}", m.group(1)) if m else []


def resolve_image(root, name, gpaths):
    name = name.strip()
    dirs = [""] + gpaths
    for d in dirs:
        base = os.path.normpath(os.path.join(root, d, name))
        if os.path.splitext(base)[1].lower() in IMAGE_EXTS and os.path.isfile(base):
            return base
        for ext in IMAGE_EXTS:
            if os.path.isfile(base + ext):
                return base + ext
    return None


def caption_of(inner):
    caps = list(find_commands(inner, "caption"))
    if not caps:
        return None, None
    _, opts, arg, _ = caps[-1]
    return arg, (opts[0] if opts else None)


def subcaptions(inner):
    out = []
    for name in ("subcaption", "subfloat", "subfigure"):
        for _, opts, arg, _ in find_commands(inner, name):
            if name == "subfloat" and opts:
                out.append(opts[0])
            elif name != "subfigure":
                out.append(arg)
    for env in ("subfigure", "minipage"):
        for m in re.finditer(r"\\begin\{" + env + r"\}", inner):
            ie, _ = find_env_end(inner, env, m.end())
            cap, _ = caption_of(inner[m.end():ie])
            if cap:
                out.append(cap)
    return [clean_inline(c) for c in out]


def caption_segments(inner):
    """Split a float with several captioned minipages into one segment per caption (LaTeX numbers each)."""
    segs, last_end = [], -1
    for m in re.finditer(r"\\begin\{minipage\}", inner):
        if m.start() < last_end:
            continue
        ie, end = find_env_end(inner, "minipage", m.end())
        seg = inner[m.end():ie]
        if next(iter(find_commands(seg, "caption")), None):
            segs.append(seg)
            last_end = end
    return segs if len(segs) >= 2 else [inner]


def tabulars(inner):
    out = []
    for m in re.finditer(r"\\begin\{(tabular\*?|tabularx|tabulary|longtable|tblr|NiceTabular)\}", inner):
        _, end = find_env_end(inner, m.group(1), m.end())
        out.append(inner[m.start():end].strip())
    return out


# ---------------------------------------------------------------- links

LINK_RE = re.compile(r"https?://(?:www\.)?(github\.com|gitlab\.com|huggingface\.co|bitbucket\.org|codeberg\.org)/[^\s}\]\)>,;\"'\\]+", re.I)


def extract_links(text, where):
    found = []
    for m in LINK_RE.finditer(text):
        url = m.group(0).rstrip(".:")
        found.append({"url": url, "host": m.group(1).lower(), "where": where, "pos": m.start()})
    # \url{github.com/...} without scheme
    for m in re.finditer(r"(?<![/\w.])(github\.com/[\w.-]+/[\w.-]+)", text):
        found.append({"url": "https://" + m.group(1).rstrip("."), "host": "github.com", "where": where, "pos": m.start()})
    return found


def normalize_repo(url):
    m = re.match(r"https?://(?:www\.)?(github\.com|gitlab\.com)/([\w.-]+)/([\w.-]+)", url, re.I)
    if not m:
        return None
    repo = re.sub(r"\.git$", "", m.group(3))
    return "https://%s/%s/%s" % (m.group(1).lower(), m.group(2), repo)


# ---------------------------------------------------------------- main entry

def parse_source(root):
    main = find_main_tex(root)
    if not main:
        return None
    tex = flatten(root, main)
    macros = parse_macros(tex)
    numberwithin = bool(re.search(r"\\numberwithin\{equation\}\{section\}", tex))
    gpaths = find_graphicspath(tex)
    main_dir = os.path.dirname(main)

    bd = re.search(r"\\begin\{document\}", tex)
    ed = re.search(r"\\end\{document\}", tex)
    body = tex[bd.end(): ed.start() if ed else len(tex)] if bd else tex
    body_main = re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", "\n", body, flags=re.S)
    body_main = re.sub(r"\\(?:bibliography|bibliographystyle|addbibresource)\{[^}]*\}|\\printbibliography(\[[^\]]*\])?",
                       "\n", body_main)
    body_main = expand_macros(body_main, macros)

    abstract = None
    am = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", body_main, re.S)
    if am:
        abstract = latex_to_md_text(am.group(1)).strip()

    sections = number_sections(scan_sections(body_main))
    sec_starts = [s["start"] for s in sections]

    def section_at(pos):
        k = bisect_right(sec_starts, pos) - 1
        if k < 0:
            return {"number": "", "title": "(front matter)", "appendix": False}
        return sections[k]

    blocks = scan_blocks(body_main)
    equations, figures, tables = [], [], []
    eq_counter, fig_counter, tab_counter = 0, 0, 0
    last_top = None
    for b in blocks:
        sec = section_at(b["start"])
        if b["kind"] == "math":
            top = sec["number"].split(".")[0] if sec["number"] else ""
            if numberwithin and top != last_top:
                eq_counter, last_top = 0, top
            numbers = []
            labels = []
            for ln in equation_lines(b["env"], b["inner"]):
                labels.extend(ln["labels"])
                if ln["tag"]:
                    numbers.append(ln["tag"])
                elif ln["numbered"]:
                    eq_counter += 1
                    numbers.append(("%s.%d" % (top, eq_counter)) if numberwithin and top else str(eq_counter))
            ctx = body_main[max(0, b["start"] - 400): b["start"]]
            ctx = latex_to_md_text(ctx).strip()[-300:]
            equations.append({
                "index": len(equations) + 1,
                "env": b["env"],
                "numbers": numbers,
                "labels": labels,
                "section": (sec["number"] + " " + sec["title"]).strip(),
                "appendix": sec.get("appendix", False),
                "latex": clean_math(b["inner"]),
                "context_before": ctx,
            })
            b["eq_numbers"] = numbers
        elif b["kind"] == "figure":
            for seg in caption_segments(b["inner"]):
                cap, short = caption_of(seg)
                if cap is None:
                    continue
                fig_counter += 1
                imgs = []
                for _, opts, arg, _ in find_commands(seg, "includegraphics"):
                    p = resolve_image(main_dir, arg, gpaths) or resolve_image(root, arg, gpaths)
                    imgs.append({"ref": arg.strip(), "path": p})
                figures.append({
                    "number": fig_counter,
                    "env": b["env"],
                    "labels": re.findall(r"\\label\{([^}]*)\}", seg),
                    "caption": latex_to_md_text(cap).strip(),
                    "subcaptions": subcaptions(seg),
                    "images": imgs,
                    "has_tikz": "\\begin{tikzpicture}" in seg or "\\begin{axis}" in seg,
                    "tabular": tabulars(seg) if not imgs else [],
                    "section": (sec["number"] + " " + sec["title"]).strip(),
                })
                b.setdefault("fig_numbers", []).append(fig_counter)
        elif b["kind"] == "table":
            for seg in caption_segments(b["inner"]):
                cap, _ = caption_of(seg)
                tab_counter += 1
                tables.append({
                    "number": tab_counter,
                    "labels": re.findall(r"\\label\{([^}]*)\}", seg),
                    "caption": latex_to_md_text(cap).strip() if cap else "",
                    "tabular": tabulars(seg),
                    "section": (sec["number"] + " " + sec["title"]).strip(),
                })
                b.setdefault("tab_numbers", []).append(tab_counter)

    links = extract_links(body_main, "body")
    return {
        "main_tex": os.path.relpath(main, root),
        "flattened_tex": tex,
        "body_main": body_main,
        "abstract": abstract,
        "macros": macros,
        "sections": [{"level": s["level"], "number": s["number"], "title": s["title"], "appendix": s["appendix"]}
                     for s in sections],
        "equations": equations,
        "figures": figures,
        "tables": tables,
        "links": links,
        "_blocks": blocks,
        "_sections_raw": sections,
    }


ALGO_RE = re.compile(r"\\begin\{(algorithm\*?|algorithmic)\}")


def render_text(seg):
    """latex_to_md_text, but keep algorithm environments verbatim in a fenced block."""
    out, pos = [], 0
    for m in ALGO_RE.finditer(seg):
        if m.start() < pos:
            continue
        inner_end, end = find_env_end(seg, m.group(1), m.end())
        inner = seg[m.end():inner_end]
        cap, _ = caption_of(inner)
        inner = replace_cmd(inner, ["caption"], "%.0s")
        inner = re.sub(r"\\label\{[^}]*\}", "", inner).strip()
        out.append(latex_to_md_text(seg[pos:m.start()]))
        out.append("\n\n[[ALGORITHM | caption=%s]]\n\n```latex\n%s\n```\n\n" % (
            latex_to_md_text(cap).strip() if cap else "", inner))
        pos = end
    out.append(latex_to_md_text(seg[pos:]))
    return "".join(out)


def render_content_md(parsed, fig_files):
    """Readable markdown rendering of the paper body for the agent to read."""
    body = parsed["body_main"]
    events = []
    for b in parsed["_blocks"]:
        events.append((b["start"], b["end"], "block", b))
    for s in parsed["_sections_raw"]:
        events.append((s["start"], s["end"], "section", s))
    events.sort(key=lambda e: e[0])
    start = 0
    for pat in (r"\\end\{abstract\}", r"\\maketitle"):
        m = re.search(pat, body)
        if m:
            start = m.end()
            break
    out, pos = [], start
    figs = {f["number"]: f for f in parsed["figures"]}
    tabs = {t["number"]: t for t in parsed["tables"]}
    for start, end, kind, obj in events:
        if start < pos:
            continue
        out.append(render_text(body[pos:start]))
        if kind == "section":
            hashes = "#" * (obj["level"] + 1)
            out.append("\n\n%s %s %s\n\n" % (hashes, obj["number"], obj["title"]))
        elif obj["kind"] == "math":
            nums = obj.get("eq_numbers") or []
            labels = re.findall(r"\\label\{([^}]*)\}", obj["inner"])
            tag = " <!-- eq %s %s -->" % (",".join(nums), ",".join(labels)) if nums or labels else ""
            inner = clean_math(obj["inner"])
            env = obj["env"].rstrip("*")
            if env in ("equation", "displaymath"):
                out.append("\n$$\n%s\n$$%s\n" % (inner, tag))
            else:
                out.append("\n$$\n\\begin{%s}\n%s\n\\end{%s}\n$$%s\n" % (
                    "aligned" if env in ("align", "eqnarray", "flalign", "alignat") else "gathered",
                    inner, "aligned" if env in ("align", "eqnarray", "flalign", "alignat") else "gathered", tag))
        elif obj["kind"] == "figure":
            for n in obj.get("fig_numbers", []):
                f = figs[n]
                files = fig_files.get(f["number"], [])
                out.append("\n\n[[FIGURE %d | labels=%s | files=%s]]\n> Caption: %s\n\n" % (
                    f["number"], ",".join(f["labels"]), ", ".join(files) or "(none)", f["caption"]))
        elif obj["kind"] == "table":
            for n in obj.get("tab_numbers", []):
                t = tabs[n]
                out.append("\n\n[[TABLE %d | labels=%s]]\n> Caption: %s\n\n```latex\n%s\n```\n\n" % (
                    t["number"], ",".join(t["labels"]), t["caption"], "\n\n".join(t["tabular"])))
        pos = end
    out.append(render_text(body[pos:]))
    text = re.sub(r"[ \t]+\n", "\n", "".join(out))
    return re.sub(r"\n{3,}", "\n\n", text)
