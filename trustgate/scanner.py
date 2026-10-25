"""Deterministic page-context scanner (stdlib only).

Flags payment instructions that are hidden (JSON-LD, CSS-hidden / off-screen text)
or phrased as instructions to an agent. This is a heuristic signal, not an
authorization source, and not a general prompt-injection detector.
"""
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser

DETECTOR = "deterministic_dom_scanner"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}

PAYMENT_PATTERNS = [
    re.compile(r"\b(?:mandatory|required|activation|licen[sc]e)\b[^.]{0,30}\bfee\b", re.I),
    re.compile(r"\bpayable\s+to\b", re.I),
    re.compile(r"\bsend\s+(?:usd\s*)?\$?\d", re.I),
    re.compile(r"\bif\s+you\s+are\s+an?\s+(?:ai|agent|assistant)\b", re.I),
    re.compile(r"ignore\s+(?:all\s+|your\s+|any\s+)?(?:previous|prior|budget|spending)", re.I),
    re.compile(r"\bcharge\s+the\s+user", re.I),
]
HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\.0+)?\s*(?:;|$)|"
    r"font-size\s*:\s*0|(?:left|top|text-indent)\s*:\s*-\d{3,}", re.I)


@dataclass(frozen=True)
class ContextFlag:
    detector: str
    flag: str
    source: str
    excerpt: str


def _matches(text):
    return any(p.search(text) for p in PAYMENT_PATTERNS)


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []          # (tag, hidden)
        self.in_jsonld = False
        self.in_other_script = False
        self.jsonld = []
        self.hidden_text = []
        self.visible_text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hidden = "hidden" in a or bool(HIDDEN_STYLE.search(a.get("style") or ""))
        if tag == "script":
            if (a.get("type") or "").lower() == "application/ld+json":
                self.in_jsonld = True
            else:
                self.in_other_script = True
        if tag not in VOID:
            self.stack.append((tag, hidden))

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_jsonld = self.in_other_script = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self.in_jsonld:
            self.jsonld.append(text)
        elif self.in_other_script or (self.stack and self.stack[-1][0] == "style"):
            return
        elif any(h for _, h in self.stack):
            self.hidden_text.append(text)
        else:
            self.visible_text.append(text)


def _json_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _json_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _json_strings(v)


def scan_page(html):
    p = _Parser()
    p.feed(html)
    flags = []
    for blob in p.jsonld:
        try:
            strings = list(_json_strings(json.loads(blob)))
        except ValueError:
            strings = [blob]
        for s in strings:
            if _matches(s):
                flags.append(ContextFlag(DETECTOR, "HIDDEN_PAYMENT_INSTRUCTION", "json_ld", s[:200]))
    for t in p.hidden_text:
        if _matches(t):
            flags.append(ContextFlag(DETECTOR, "HIDDEN_PAYMENT_INSTRUCTION", "css_hidden", t[:200]))
    for t in p.visible_text:
        if _matches(t):
            flags.append(ContextFlag(DETECTOR, "PAYMENT_INSTRUCTION_IN_PAGE", "visible_text", t[:200]))
    return list(dict.fromkeys(flags))
