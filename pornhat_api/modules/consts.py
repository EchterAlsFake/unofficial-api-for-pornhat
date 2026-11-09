"""Verified Pornhat routes and extraction helpers (no network requests)."""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from base_api import NotFound, VideoUnavailable
from selectolax.lexbor import LexborHTMLParser

BASE_URL = "https://www.pornhat.com"
HOSTS = frozenset(
    {
        "pornhat.com",
        "www.pornhat.com",
        "pornhat.one",
        "www.pornhat.one",
        *(f"{lang}.pornhat.com" for lang in ("de", "fr", "it", "es", "pt", "hi")),
    }
)
VIDEO_REGION = "#custom_list_videos_common_videos_list_items, #custom_list_videos_videos_list_search_result_items, #custom_list_videos_videos_items"
RELATED_REGION = "#custom_list_videos_related_videos_new_items"


def normalize_url(url: str, *, kind: str | None = None) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or parts.hostname not in HOSTS
        or parts.username
        or parts.password
        or parts.port not in {None, 80, 443}
    ):
        raise ValueError("Expected a URL on a verified Pornhat host")
    path = parts.path.rstrip("/") + "/"
    if kind and not re.fullmatch(rf"/{kind}/[^/]+/", path):
        raise ValueError(f"Expected /{kind}/<slug>/")
    return urlunsplit(("https", parts.hostname, path, "", ""))


def text(parser, selector: str) -> str | None:
    node = parser.css_first(selector)
    return node.text(strip=True) or None if node else None


def attr(parser, selector: str, name: str) -> str | None:
    node = parser.css_first(selector)
    return node.attributes.get(name) if node else None


def setup(parser, selector: str) -> dict:
    value = attr(parser, selector, "data-setup")
    return json.loads(value) if value else {}


def number(value) -> int | None:
    if value is None:
        return None
    match = re.search(r"[\d][\d\s,.]*", str(value))
    return int(re.sub(r"\D", "", match[0])) if match else None


def checked_parser(content: str, url: str) -> LexborHTMLParser:
    resolved = getattr(content, "url", url)
    if urlsplit(resolved).hostname not in HOSTS:
        raise VideoUnavailable(
            "Pornhat redirected outside the verified host family", url=url
        )
    parser = LexborHTMLParser(content)
    if text(parser, "h1") == "Not Found":
        raise NotFound("Pornhat resource was not found", url=url)
    if not parser.css_first(".wrapper"):
        raise VideoUnavailable("Response is not a public Pornhat content page", url=url)
    return parser


def page_url(url: str, page: int) -> str:
    return url if page == 1 else f"{url.rstrip('/')}/{page}/"


def page_count(parser) -> int:
    numbers = [
        int(n.text(strip=True))
        for n in parser.css(".pagination .pages a, .pagination .pages span")
        if n.text(strip=True).isdigit()
    ]
    return max(numbers, default=1)


def extract_videos(content: str, *, base_url: str, region: str = VIDEO_REGION):
    parser = checked_parser(content, base_url)
    root = parser.css_first(region)
    if root is None:
        # Empty searches have a named block and an explicit empty-state marker.
        if parser.css_first(
            "#custom_list_videos_videos_list_search_result .empty-content, #custom_list_videos_common_videos_list .empty-content"
        ):
            return
        raise VideoUnavailable("Expected video listing region is missing", url=base_url)
    for card in root.css(".thumb-bl-video"):
        link = card.css_first('.thumb-video > a[href*="/video/"]')
        if link is None:
            continue
        url = normalize_url(urljoin(base_url, link.attributes["href"]), kind="video")
        thumb = attr(link, "img", "data-original") or attr(link, "img", "src")
        match = re.search(r"/videos_screenshots/\d+/(\d+)/", thumb or "")
        yield {"url": url, "video_id": match[1] if match else None}


def extract_profiles(content: str, *, base_url: str, kind: str):
    parser = checked_parser(content, base_url)
    # Only real directory cards; navigation, alphabet controls and footer excluded.
    for card in parser.css(".thumb-ctr"):
        link = card.css_first(f'a[href^="/{kind}/"]')
        if link is not None:
            yield {
                "url": normalize_url(
                    urljoin(base_url, link.attributes["href"]), kind=kind
                )
            }


def video_json(parser, video_id: str) -> dict:
    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            yield value
            if "@graph" in value:
                yield from walk(value["@graph"])

    for script in parser.css('script[type="application/ld+json"]'):
        try:
            values = json.loads(script.text())
        except (ValueError, TypeError):
            continue
        for data in walk(values):
            if data.get("@type") == "VideoObject" and str(
                data.get("embedUrl", "")
            ).rstrip("/").endswith("/" + video_id):
                return data
    raise VideoUnavailable("No structured metadata matching the requested player ID")
