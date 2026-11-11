"""Reduce arbitrary web HTML to a small, safe subset that MuPDF can lay out as pages.

Scripts, styles, embeds and forms are dropped with their content; layout wrappers become
plain <div>s; unknown tags are unwrapped (their text is kept). Only http/https/mailto links
survive, and images are referenced by archive name once they've been downloaded.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .pdf_pages import safe_uri

KEEP_TAGS = frozenset(
    "div h1 h2 h3 h4 h5 h6 p a img ul ol li blockquote pre code em strong b i u s sub sup "
    "br hr figure figcaption table thead tbody tr td th dl dt dd".split()
)
CONTAINER_TAGS = frozenset("div section article main header footer aside details summary center".split())
DROP_TAGS = frozenset(
    "head script style noscript template iframe frame frameset object embed svg math canvas "
    "video audio nav input button select textarea dialog".split()
)
VOID_TAGS = frozenset("img br hr input meta link source wbr area base col embed param track".split())
LAZY_SRC_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-url")
AUTHOR_META = frozenset({"author", "article:author", "byl"})
WEB_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class PageInfo:
    title: str = ""
    author: str = ""
    source: str = ""  # canonical URL of the page, when known
    lang: str = ""


class _Cleaner(HTMLParser):
    """One pass over the HTML. image_names=None means "discover images" rather than emit them."""

    def __init__(self, base_url: str, image_names: Mapping[str, str] | None) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.image_names = image_names
        self.image_urls: list[str] = []
        self.out: list[str] = []
        self.open_tags: list[str] = []
        self.drop_depth = 0
        self.meta: dict[str, str] = {}
        self._title_parts: list[str] | None = None

    # -- metadata (read even inside <head>, which is otherwise dropped) --
    def _capture_meta(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == "title" and "title" not in self.meta:
            self._title_parts = []
        elif tag == "html" and attrs.get("lang"):
            self.meta.setdefault("lang", attrs["lang"].strip())
        elif tag == "meta":
            key = (attrs.get("name") or attrs.get("property") or "").lower()
            content = (attrs.get("content") or "").strip()
            if key in AUTHOR_META and content and not content.startswith("http"):
                self.meta.setdefault("author", content)
            elif key == "og:url" and content:
                self.meta.setdefault("og_url", content)
        elif tag == "link" and "canonical" in (attrs.get("rel") or "").lower().split():
            if attrs.get("href"):
                self.meta.setdefault("canonical", attrs["href"].strip())

    def _url(self, raw: str | None) -> str | None:
        if not raw or not raw.strip():
            return None
        return safe_uri(urljoin(self.base_url, raw.strip()) if self.base_url else raw.strip())

    def _image_source(self, attrs: dict[str, str]) -> str | None:
        srcset = (attrs.get("srcset") or "").split(",")[-1].strip().split(" ")[0]
        for raw in (*(attrs.get(k) for k in LAZY_SRC_ATTRS), attrs.get("src"), srcset):
            if not raw:
                continue
            if raw.startswith("data:image/"):
                return raw
            url = self._url(raw)
            if url and urlparse(url).scheme in WEB_SCHEMES:
                return url
        return None

    def _image(self, attrs: dict[str, str]) -> None:
        source = self._image_source(attrs)
        if source is None:
            return
        if self.image_names is None:
            self.image_urls.append(source)
            return
        name = self.image_names.get(source)
        if name:
            alt = escape(attrs.get("alt") or "")
            self.out.append(f'<img src="{escape(name)}" alt="{alt}">')

    # -- HTMLParser callbacks --
    def handle_starttag(self, tag: str, attr_list: list[tuple[str, str | None]]) -> None:
        attrs = {k: v or "" for k, v in attr_list}
        self._capture_meta(tag, attrs)
        if tag == "body":
            self.drop_depth = 0  # recover from an unclosed <head>
        if self.drop_depth or tag in DROP_TAGS:
            if tag in DROP_TAGS and tag not in VOID_TAGS:
                self.drop_depth += 1
            return
        name = "div" if tag in CONTAINER_TAGS else tag
        if name not in KEEP_TAGS:
            return
        if name == "img":
            self._image(attrs)
            return
        if name in VOID_TAGS:
            self.out.append(f"<{name}>")
            return
        extra = ""
        if name == "a":
            href = self._url(attrs.get("href"))
            extra = f' href="{escape(href)}"' if href else ""
        self.out.append(f"<{name}{extra}>")
        self.open_tags.append(name)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._title_parts is not None:
            self.meta["title"] = " ".join("".join(self._title_parts).split())
            self._title_parts = None
        if self.drop_depth:
            if tag in DROP_TAGS and tag not in VOID_TAGS:
                self.drop_depth -= 1
            return
        name = "div" if tag in CONTAINER_TAGS else tag
        if name in self.open_tags:
            while self.open_tags:
                closed = self.open_tags.pop()
                self.out.append(f"</{closed}>")
                if closed == name:
                    break

    def handle_data(self, data: str) -> None:
        if self._title_parts is not None:
            self._title_parts.append(data)
            return
        if not self.drop_depth:
            self.out.append(escape(data, quote=False))

    def result(self) -> str:
        self.close()
        return "".join(self.out) + "".join(f"</{t}>" for t in reversed(self.open_tags))


def _run(html: str, base_url: str, image_names: Mapping[str, str] | None) -> _Cleaner:
    cleaner = _Cleaner(base_url, image_names)
    cleaner.feed(html)
    cleaner.result()
    return cleaner


def page_info(html: str) -> PageInfo:
    meta = _run(html, "", {}).meta
    source = safe_uri(meta.get("canonical") or meta.get("og_url")) or ""
    return PageInfo(
        title=meta.get("title", ""),
        author=meta.get("author", ""),
        source=source if urlparse(source).scheme in WEB_SCHEMES else "",
        lang=meta.get("lang", ""),
    )


def image_sources(html: str, base_url: str = "") -> tuple[str, ...]:
    """Image URLs (or data: URIs) in document order, without duplicates."""
    return tuple(dict.fromkeys(_run(html, base_url, None).image_urls))


def clean_body(html: str, base_url: str = "", image_names: Mapping[str, str] | None = None) -> str:
    """The safe body markup; images not present in image_names are left out."""
    cleaner = _Cleaner(base_url, image_names or {})
    cleaner.feed(html)
    return cleaner.result()
