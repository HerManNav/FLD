"""Pure key extraction, source normalization, and filename parsing."""

from __future__ import annotations

from html.parser import HTMLParser
import os
import re
from urllib.parse import unquote, urlsplit


KEY_PATTERN = re.compile(
    r"(?:CHK|SSK|USK)(?:@|%40)[A-Za-z0-9~%\-]+,[A-Za-z0-9~%\-]+,[A-Za-z0-9~%\-]+"
    r"|KSK(?:@|%40)",
    re.IGNORECASE,
)
MEDIA_END = re.compile(r"\.(?:mp4|jpg|rar|zip)(?=$|[\s)\]}>.,;:!?\"'])", re.I)


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "p", "div", "li", "tr", "pre"}:
            self.parts.append("\n")
        for name, value in attrs:
            if name == "href" and value:
                self.parts.extend(("\n", value, "\n"))

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "tr", "pre", "a"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def extract_keys(document: str) -> list[str]:
    """Find plain-text and linked keys, preserving order and removing duplicates."""
    parser = PageText()
    parser.feed(document)
    found = []
    seen = set()
    # Keep each href separate from its label; visible text is parsed line by line.
    searchable = re.sub(r"%40", "@", "".join(parser.parts), flags=re.I)
    searchable = re.sub(r"%2c", ",", searchable, flags=re.I)
    searchable = re.sub(r"%2f", "/", searchable, flags=re.I)
    for line in searchable.splitlines():
        matches = list(KEY_PATTERN.finditer(line))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            tail = line[match.end():end]
            if match.group()[:3].upper() != "KSK" and not tail.startswith("/"):
                tail = ""
            tail = re.split(r"[<>\"|\\?#]", tail, maxsplit=1)[0]
            # Common file extensions bound links followed by descriptions or markup.
            endings = list(MEDIA_END.finditer(tail))
            if endings:
                tail = tail[:endings[-1].end()]
            candidate = (match.group() + tail).rstrip().rstrip(".,;:!")
            while candidate.endswith(")") and candidate.count(")") > candidate.count("("):
                candidate = candidate[:-1]
            key = unquote(candidate)
            key = key[:3].upper() + key[3:]
            if any(ord(char) < 32 or ord(char) == 127 for char in key):
                continue
            if key not in seen:
                seen.add(key)
                found.append(key)
    return found


def source_key(source: str) -> str | None:
    source = source.strip()
    if source.lower().startswith("freenet:"):
        source = source[8:].lstrip("/")
    if re.match(r"^(CHK|SSK|USK|KSK)(@|%40)", source, re.I):
        return unquote(source.split("?", 1)[0].split("#", 1)[0])
    if source.lower().startswith(("localhost:", "127.0.0.1:")):
        source = "http://" + source
    parsed = urlsplit(source)
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"} and parsed.port == 8888:
        key = unquote(parsed.path.lstrip("/"))
        if re.match(r"^(CHK|SSK|USK|KSK)@", key, re.I):
            return key
    return None


def filename_for(key: str) -> str:
    tail = key.rstrip("/").rsplit("/", 1)[-1]
    if "/" not in key:
        tail = key[4:] if key.startswith("KSK@") else key[:28] + ".bin"
    tail = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", tail).strip(" .")
    if not tail:
        tail = "download.bin"
    if tail.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}:
        tail = "_" + tail
    if len(tail) > 160:
        stem, suffix = os.path.splitext(tail)
        tail = stem[:160 - len(suffix)] + suffix if len(suffix) <= 20 else tail[:160]
    return tail.rstrip(" .")
