"""Parse an arXiv LaTeXML HTML page (arxiv.org/html/<id>) with the standard library."""

import re
from html.parser import HTMLParser

VOID = {"img", "br", "hr", "meta", "link", "input", "source", "col", "area", "base", "wbr", "embed", "param", "track"}
SKIP = {"script", "style", "nav", "header", "footer", "noscript", "button", "svg"}


class Node(object):
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs), [], parent

    def cls(self):
        return self.attrs.get("class", "") or ""

    def has(self, c):
        return c in self.cls().split()

    def iter(self):
        yield self
        for ch in self.children:
            if isinstance(ch, Node):
                for x in ch.iter():
                    yield x

    def find_all(self, pred):
        return [n for n in self.iter() if pred(n)]

    def text(self):
        parts = []
        for ch in self.children:
            if isinstance(ch, Node):
                if ch.tag == "math":
                    parts.append("$" + (ch.attrs.get("alttext") or "") + "$")
                else:
                    parts.append(ch.text())
            else:
                parts.append(ch)
        return "".join(parts)


class _Builder(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.root = Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def build(html):
    b = _Builder()
    b.feed(html)
    return b.root


def _ws(s):
    return re.sub(r"\s+", " ", s).strip()


def _tag_text(node, kind):
    tags = node.find_all(lambda n: n.tag == "span" and n.has("ltx_tag_" + kind))
    return _ws(tags[0].text()) if tags else ""


def _caption(node):
    caps = node.find_all(lambda n: n.tag == "figcaption")
    if not caps:
        return ""
    t = _ws(caps[0].text())
    return re.sub(r"^(Figure|Table|Fig\.)\s*[\w.]+\s*[:.]\s*", "", t)


def _table_md(table):
    rows = []
    for tr in table.find_all(lambda n: n.tag == "tr"):
        cells = [c for c in tr.children if isinstance(c, Node) and c.tag in ("td", "th")]
        row = []
        for c in cells:
            txt = _ws(c.text()).replace("|", "\\|")
            span = int(c.attrs.get("colspan", "1") or 1)
            row.extend([txt] + [""] * (span - 1))
        if row:
            rows.append(row)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


class _Renderer(object):
    def __init__(self):
        self.out = []
        self.sections, self.equations, self.figures, self.tables = [], [], [], []
        self.cur_section = ""

    def render(self, node):
        for ch in node.children:
            if isinstance(ch, Node):
                self.node(ch)
            else:
                self.out.append(re.sub(r"\s+", " ", ch))

    def node(self, n):
        t = n.tag
        if t in SKIP or n.has("ltx_bibliography") or n.has("ltx_page_header") or n.has("ltx_page_footer"):
            return
        if t == "math":
            alt = n.attrs.get("alttext") or ""
            if n.attrs.get("display") == "block":
                self.out.append("\n$$\n%s\n$$\n" % alt)
            else:
                self.out.append("$%s$" % alt)
            return
        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            title = _ws(n.text())
            level = int(t[1])
            self.sections.append({"level": level - 1, "title": title})
            self.cur_section = title
            self.out.append("\n\n%s %s\n\n" % ("#" * level, title))
            return
        if t == "table" and (n.has("ltx_equation") or n.has("ltx_equationgroup")):
            maths = [m.attrs.get("alttext", "") for m in n.find_all(lambda x: x.tag == "math")]
            tags = [_ws(x.text()).strip("()") for x in n.find_all(lambda x: x.tag == "span" and x.has("ltx_tag_equation"))]
            latex = " \\\\\n".join(maths)
            self.equations.append({"index": len(self.equations) + 1, "env": "html", "numbers": tags,
                                   "labels": [n.attrs.get("id", "")], "section": self.cur_section,
                                   "latex": latex, "context_before": _ws("".join(self.out[-6:]))[-300:]})
            tag = " <!-- eq %s -->" % ",".join(tags) if tags else ""
            self.out.append("\n$$\n%s\n$$%s\n" % (latex, tag))
            return
        if t == "figure" and n.has("ltx_table"):
            md = "\n\n".join(_table_md(tb) for tb in n.find_all(lambda x: x.tag == "table" and x.has("ltx_tabular")))
            num = re.sub(r"\D", "", _tag_text(n, "table")) or str(len(self.tables) + 1)
            cap = _caption(n)
            self.tables.append({"number": int(num), "labels": [n.attrs.get("id", "")], "caption": cap,
                                "markdown": md, "section": self.cur_section})
            self.out.append("\n\n[[TABLE %s]]\n> Caption: %s\n\n%s\n\n" % (num, cap, md))
            return
        if t == "figure" and n.has("ltx_figure") and not (n.parent is not None and n.parent.tag == "figure"):
            imgs = [i.attrs.get("src") for i in n.find_all(lambda x: x.tag == "img") if i.attrs.get("src")]
            num = re.sub(r"[^\d]", "", _tag_text(n, "figure")) or str(len(self.figures) + 1)
            cap = _caption(n)
            self.figures.append({"number": int(num), "labels": [n.attrs.get("id", "")], "caption": cap,
                                 "subcaptions": [], "images": [{"ref": s, "path": None} for s in imgs],
                                 "has_tikz": False, "section": self.cur_section})
            self.out.append("\n\n[[FIGURE %s | files={fig%s}]]\n> Caption: %s\n\n" % (num, num, cap))
            return
        if t in ("p", "div") and (n.has("ltx_para") or n.has("ltx_p") or t == "p"):
            self.out.append("\n\n")
            self.render(n)
            self.out.append("\n\n")
            return
        if t == "li":
            self.out.append("\n- ")
            self.render(n)
            return
        if t == "a" and n.has("ltx_ref") and "ltx_bib" not in (n.attrs.get("href") or ""):
            self.render(n)
            return
        self.render(n)


def parse_html(html):
    root = build(html)
    title_nodes = root.find_all(lambda n: n.tag == "h1" and n.has("ltx_title_document"))
    abstract_nodes = root.find_all(lambda n: n.tag == "div" and n.has("ltx_abstract"))
    article = root.find_all(lambda n: n.tag == "article") or [root]
    r = _Renderer()
    r.render(article[0])
    text = re.sub(r"[ \t]+\n", "\n", "".join(r.out))
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    links = []
    bib = set()
    for b in article[0].find_all(lambda n: n.has("ltx_bibliography")):
        bib.update(id(x) for x in b.iter())
    for a in article[0].find_all(lambda n: n.tag == "a" and n.attrs.get("href") and id(n) not in bib):
        links.append(a.attrs["href"])
    return {
        "title": _ws(title_nodes[0].text()) if title_nodes else None,
        "abstract": _ws(abstract_nodes[0].text()).replace("Abstract", "", 1).strip() if abstract_nodes else None,
        "sections": r.sections,
        "equations": r.equations,
        "figures": sorted(r.figures, key=lambda f: f["number"]),
        "tables": r.tables,
        "content_md": text,
        "hrefs": links,
    }
