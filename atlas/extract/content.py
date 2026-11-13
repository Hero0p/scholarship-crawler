"""Deterministic HTML/PDF -> text conversion.

The text produced here is THE evidence base: every extracted value carries a snippet that is a
verbatim substring of `Document.text`, and the text is stored next to the raw snapshot so that
traceability can be re-checked at any time (`atlas.extract.evidence.verify_trace`).
"""
from __future__ import annotations

import bisect
import io
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urljoin

import lxml.html
from lxml import etree

BLOCK_TAGS = {"p", "div", "section", "article", "main", "li", "ul", "ol", "h1", "h2", "h3", "h4",
              "h5", "h6", "tr", "table", "tbody", "thead", "dt", "dd", "dl", "blockquote", "pre",
              "caption", "br", "hr", "header", "footer", "form", "fieldset", "legend", "figure",
              "figcaption", "address", "center", "td", "th", "span_block"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
DROP_TAGS = ["script", "style", "noscript", "template", "svg", "iframe", "object", "embed",
             "select", "option", "button", "input", "textarea", "canvas", "map", "video", "audio"]
BOILERPLATE_RE = re.compile(
    r"(?:^|[\s_-])(nav|navbar|navigation|menu|mainmenu|main-menu|megamenu|mega-menu|footer|"
    r"breadcrumbs?|sidebar|side-bar|cookie|cookies|skip-link|skiplink|topbar|top-bar|social|"
    r"share|sharing|accessibility|screen-reader|offcanvas|off-canvas|modal|popup|marquee|ticker|"
    r"copyright|site-header|header-top|header-menu|top-header|bottom-header|whatsnew|news-ticker)"
    r"(?:$|[\s_-])", re.I)
WS_RE = re.compile(r"[ \t\r\f\v ​  ]+")


@dataclass
class Block:
    text: str
    tag: str
    start: int = 0
    end: int = 0
    page: int | None = None      # PDF page number (1-based)
    bold: bool = False


@dataclass
class Link:
    text: str
    href: str
    in_content: bool


@dataclass
class Document:
    url: str
    kind: str                     # HTML | PDF
    title: str = ""
    og_title: str = ""
    site_name: str = ""
    h1: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    text: str = ""
    meta_description: str = ""

    def block_at(self, pos: int) -> Block | None:
        """Binary search over block start offsets (blocks are sorted and non-overlapping)."""
        starts = self.__dict__.get("_starts")
        if starts is None or len(starts) != len(self.blocks):
            starts = [b.start for b in self.blocks]
            self.__dict__["_starts"] = starts
        i = bisect.bisect_right(starts, pos) - 1
        if 0 <= i < len(self.blocks) and self.blocks[i].start <= pos < self.blocks[i].end:
            return self.blocks[i]
        return None


def norm_ws(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("–", "-").replace("—", "-").replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    return WS_RE.sub(" ", s).strip()


def _assemble(doc: Document, header_lines: list[str]) -> None:
    parts, pos = [], 0
    for line in header_lines:
        parts.append(line)
        pos += len(line) + 1
    for b in doc.blocks:
        b.start = pos
        b.end = pos + len(b.text)
        parts.append(b.text)
        pos = b.end + 1
    doc.text = "\n".join(parts)


# ----------------------------------------------------------------------------- HTML

def _is_boilerplate(el) -> bool:
    tag = el.tag if isinstance(el.tag, str) else ""
    attrs = " ".join(filter(None, [el.get("id"), el.get("class"), el.get("role")]))
    if tag in ("nav", "footer", "aside"):
        return True
    if tag == "header":
        # keep article/entry headers (they often hold the page title)
        parent_tags = {a.tag for a in el.iterancestors() if isinstance(a.tag, str)}
        return not ({"article", "main"} & parent_tags) and "entry" not in attrs.lower()
    if el.get("role") in ("navigation", "banner", "contentinfo"):
        return True
    if el.get("aria-hidden") == "true" or "display:none" in (el.get("style") or "").replace(" ", ""):
        return True
    return bool(attrs and BOILERPLATE_RE.search(attrs))


def _collect_blocks(root) -> list[Block]:
    blocks: list[Block] = []
    buf: list[str] = []
    bold_chars = [0, 0]  # [bold, total]

    def flush(tag: str):
        txt = norm_ws("".join(buf))
        if txt:
            is_bold = bold_chars[1] > 0 and bold_chars[0] >= 0.9 * bold_chars[1]
            blocks.append(Block(text=txt, tag=tag, bold=is_bold))
        buf.clear()
        bold_chars[0] = bold_chars[1] = 0

    def walk(el, in_bold: bool, block_tag: str):
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag == "tr":
            flush(block_tag)
            cells = [norm_ws(c.text_content()) for c in el if isinstance(c.tag, str) and c.tag in ("td", "th")]
            cells = [c for c in cells if c]
            if cells:
                blocks.append(Block(text=" | ".join(cells), tag="tr",
                                    bold=all(isinstance(c.tag, str) and c.tag == "th" for c in el if isinstance(c.tag, str))))
            if el.tail:
                buf.append(el.tail)
            return
        is_block = tag in BLOCK_TAGS
        if is_block:
            flush(block_tag)
        cur_tag = tag if is_block else block_tag
        bold = in_bold or tag in ("b", "strong") or tag in HEADING_TAGS
        if el.text:
            buf.append(el.text)
            bold_chars[1] += len(el.text.strip())
            if bold:
                bold_chars[0] += len(el.text.strip())
        for child in el:
            if isinstance(child.tag, str):
                walk(child, bold, cur_tag)
            elif child.tail:  # comments / PIs
                buf.append(child.tail)
        if is_block:
            flush(cur_tag)
        if el.tail:
            buf.append(el.tail)
            bold_chars[1] += len(el.tail.strip())
            if in_bold:
                bold_chars[0] += len(el.tail.strip())

    if tag_of(root) == "br":
        return blocks
    walk(root, False, "div")
    flush("div")
    return blocks


def tag_of(el) -> str:
    return el.tag if isinstance(el.tag, str) else ""


def parse_html(raw: bytes, url: str) -> Document:
    try:
        root = lxml.html.document_fromstring(raw)
    except (etree.ParserError, ValueError):
        root = lxml.html.document_fromstring(raw.decode("utf-8", "replace"))
    doc = Document(url=url, kind="HTML")
    t = root.find(".//title")
    doc.title = norm_ws(t.text_content()) if t is not None else ""
    for m in root.iter("meta"):
        prop = (m.get("property") or m.get("name") or "").lower()
        if prop == "og:title":
            doc.og_title = norm_ws(m.get("content") or "")
        elif prop == "og:site_name":
            doc.site_name = norm_ws(m.get("content") or "")
        elif prop == "description":
            doc.meta_description = norm_ws(m.get("content") or "")

    # links from the whole page (before boilerplate removal)
    all_links: dict[str, Link] = {}
    for a in root.iter("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absu = urljoin(url, href)
        txt = norm_ws(a.text_content() or a.get("title") or "")
        all_links.setdefault(absu + "\x00" + txt, Link(text=txt, href=absu, in_content=False))

    for bad in root.xpath("//" + " | //".join(DROP_TAGS)):
        bad.drop_tree()
    _pair_tabs(root)
    body = root.find("body")
    if body is None:
        body = root
    body_len = max(1, len(body.text_content()))
    for el in list(body.iter()):
        if not isinstance(el.tag, str) or el.getparent() is None:
            continue
        if _is_boilerplate(el):
            # never strip a wrapper that holds a large share of the page (e.g. Drupal's
            # "dialog-off-canvas-main-canvas" wraps the whole page)
            if el.tag not in ("nav", "footer") and len(el.text_content()) > 0.4 * body_len:
                continue
            el.drop_tree()

    for a in body.iter("a"):
        href = (a.get("href") or "").strip()
        absu = urljoin(url, href)
        txt = norm_ws(a.text_content() or a.get("title") or "")
        key = absu + "\x00" + txt
        if key in all_links:
            all_links[key].in_content = True
    doc.links = list(all_links.values())
    doc.h1 = [norm_ws(h.text_content()) for h in body.iter("h1") if norm_ws(h.text_content())]
    doc.blocks = _dedupe(_collect_blocks(body))
    header = [f"TITLE: {doc.title}"] if doc.title else []
    _assemble(doc, header)
    return doc


def _label_section(text: str) -> bool:
    from atlas.extract.sections import match_section  # local import (cycle)
    return bool(text) and len(text) <= 45 and match_section(text) is not None


def _add_heading(pane, label: str) -> None:
    if pane.get("data-atlas-labelled"):
        return
    h = lxml.html.Element("h3")
    h.text = label
    h.set("data-atlas", "tab-label")
    pane.insert(0, h)
    pane.set("data-atlas-labelled", "1")


def _pair_tabs(root) -> None:
    """Tabs / accordions / icon-lists keep their labels apart from their content panes. Pair them
    (by explicit target id, or by position for parallel sibling lists) and put the page's own label
    text as a heading in front of the pane, so section segmentation sees 'Eligibility' -> content."""
    by_id = {el.get("id"): el for el in root.iter() if isinstance(el.tag, str) and el.get("id")}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        target = None
        for attr in ("data-target", "data-bs-target", "aria-controls", "href"):
            v = (el.get(attr) or "").strip()
            if v.startswith("#") and len(v) > 1:
                target = by_id.get(v[1:])
            elif attr == "aria-controls" and v:
                target = by_id.get(v)
            if target is not None:
                break
        if target is None:
            continue
        label = norm_ws(el.text_content())
        if _label_section(label) and len(norm_ws(target.text_content())) > 20:
            _add_heading(target, label)
    # parallel lists: <ul>label1,label2,..</ul> ... <ul>pane1,pane2,..</ul>
    for parent in root.iter():
        if not isinstance(parent.tag, str):
            continue
        kids = [k for k in parent if isinstance(k.tag, str)]
        for i, a in enumerate(kids):
            items = [c for c in a if isinstance(c.tag, str)]
            if len(items) < 3:
                continue
            labels = [norm_ws(c.text_content()) for c in items]
            if sum(_label_section(t) for t in labels) < len(labels) - 1:
                continue
            for b in kids[i + 1:i + 4]:
                panes = [c for c in b if isinstance(c.tag, str)]
                if len(panes) == len(items) and all(len(norm_ws(p.text_content())) > 20 for p in panes):
                    for lab, pane in zip(labels, panes):
                        if _label_section(lab):
                            _add_heading(pane, lab)
                    break


def _dedupe(blocks: list[Block]) -> list[Block]:
    """Drop exact consecutive duplicates (responsive templates often repeat content)."""
    out: list[Block] = []
    for b in blocks:
        if out and out[-1].text == b.text:
            continue
        out.append(b)
    return out


# ----------------------------------------------------------------------------- PDF

def parse_pdf(raw: bytes, url: str, max_pages: int = 15) -> Document:
    import pdfplumber

    doc = Document(url=url, kind="PDF")
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for i, page in enumerate(pdf.pages[:max_pages], start=1):
            txt = page.extract_text() or ""
            for line in txt.splitlines():
                line = norm_ws(line)
                if line:
                    doc.blocks.append(Block(text=line, tag="pdfline", page=i))
    first = [b.text for b in doc.blocks[:8]]
    doc.title = next((ln for ln in first if len(ln) > 8), "")
    _assemble(doc, [])
    return doc


def parse(raw: bytes, url: str, content_type: str | None) -> Document:
    ct = (content_type or "").lower()
    if "pdf" in ct or raw[:5] == b"%PDF-" or url.lower().split("?")[0].endswith(".pdf"):
        return parse_pdf(raw, url)
    return parse_html(raw, url)
