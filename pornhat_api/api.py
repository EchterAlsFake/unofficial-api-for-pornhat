from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from functools import partial
from typing import ClassVar, Literal
from urllib.parse import quote, urljoin, urlsplit

from base_api import (
    BaseCore,
    BaseMedia,
    DownloadConfigHLS,
    ErrorMode,
    Helper,
    IteratorConfig,
    ScrapeStream,
    VideoUnavailable,
    make_iterator_config,
    media_field,
)
from base_api.modules.logger import get_logger
from base_api.modules.provider import (
    download_errors,
    fetch_content,
    prepare_download_config,
)

from .modules import consts as c
from .modules.sorting import ProfileSort, Sort

logger = get_logger(__name__)


async def get_html_content(core: BaseCore, url: str, *, owner=None) -> str:
    return await fetch_content(core, url, logger=logger, owner=owner)


def _iterator_config() -> IteratorConfig:
    return make_iterator_config(
        page_error_mode=ErrorMode.YIELD, item_error_mode=ErrorMode.YIELD
    )


def _listing(core, url, constructor, extractor, *, pages, start_page, iterator_config):
    if (
        not isinstance(start_page, int)
        or isinstance(start_page, bool)
        or start_page < 1
        or (
            pages != "all"
            and (not isinstance(pages, int) or isinstance(pages, bool) or pages < 1)
        )
    ):
        raise ValueError(
            "pages must be a positive integer or 'all'; start_page must be positive"
        )
    config = iterator_config if iterator_config is not None else _iterator_config()

    async def generate():
        # Clamp ranges to the real last page; beyond-last URLs otherwise return 404.
        content = await get_html_content(core, url)
        last = c.page_count(c.checked_parser(content, url))
        end = last if pages == "all" else min(last, start_page + pages - 1)
        urls = [c.page_url(url, n) for n in range(start_page, end + 1)]
        async with Helper(core=core, constructor=constructor).iterator(
            target_page_urls=urls,
            item_extractor=extractor,
            iterator_config=config,
        ) as stream:
            async for result in stream:
                yield result

    return ScrapeStream(generate())


@dataclass(slots=True, kw_only=True, repr=False)
class Video(BaseMedia):
    core: BaseCore
    video_id: str | None = media_field("html")
    title: str | None = media_field("html")
    description: str | None = media_field("html")
    thumbnail_url: str | None = media_field("html")
    poster_url: str | None = media_field("html")
    preview_video_url: str | None = media_field("html")
    publish_date: str | None = media_field("html")
    length: str | None = media_field("html")
    duration: int | None = media_field("html")
    views: int | None = media_field("html")
    author_name: str | None = media_field("html")
    author_link: str | None = media_field("html")
    site_url: str | None = media_field("html")
    pornstars: list[str] | None = media_field("html")
    pornstars_urls: list[str] | None = media_field("html")
    tags: list[str] | None = media_field("html")
    tags_urls: list[str] | None = media_field("html")
    embed_url: str | None = media_field("html")
    language: str | None = media_field("html")
    is_hd: bool | None = media_field("html")
    player_sources: list[dict] | None = media_field("html")
    media_sources: list[dict] | None = media_field("media")
    m3u8_base_url: str | None = media_field("media")
    content_url: str | None = media_field("media")
    loader_methods: ClassVar[dict[str, str]] = {
        "html": "_load_html",
        "media": "_load_media",
    }

    def __post_init__(self):
        self.url = c.normalize_url(self.url, kind="video")

    async def _load_html(self) -> dict:
        content = await get_html_content(self.core, self.url, owner=self)
        data = await asyncio.to_thread(self._extract_html, content, self.url)
        if (
            self.is_field_loaded("video_id")
            and self.video_id is not None
            and self.video_id != data["video_id"]
        ):
            raise VideoUnavailable(
                "Player ID does not match the listing ID", url=self.url
            )
        return data

    @staticmethod
    def _extract_html(content: str, url: str) -> dict:
        parser = c.checked_parser(content, url)
        video_id = c.attr(parser, ".player-wrap", "data-video_id")
        if not video_id:
            raise VideoUnavailable("Public video player is missing", url=url)
        data = c.video_json(parser, video_id)
        info = parser.css_first(".video-info")
        if info is None:
            raise VideoUnavailable("Video metadata region is missing", url=url)
        saved = c.setup(info, '[data-type="video"][data-setup]')
        sources = []
        for source in parser.css("#my-video source"):
            label = source.attributes.get("label", "")
            if (
                label.endswith("p")
                and label[:-1].isdigit()
                and source.attributes.get("src")
            ):
                sources.append(
                    {
                        "quality": int(label[:-1]),
                        "url": urljoin(url, source.attributes["src"]),
                        "type": source.attributes.get("type"),
                    }
                )
        if not sources:
            raise VideoUnavailable("Public player has no media sources", url=url)
        models = info.css('a[href^="/models/"]')
        tags = info.css('.video-tags a[href^="/tags/"]')
        thumb = data.get("thumbnailUrl")
        if isinstance(thumb, list):
            thumb = thumb[0] if thumb else None
        return {
            "video_id": video_id,
            "title": data.get("name"),
            "description": data.get("description"),
            "thumbnail_url": thumb,
            "poster_url": c.attr(parser, "#my-video", "poster"),
            "preview_video_url": saved.get("preview"),
            "publish_date": data.get("uploadDate"),
            "length": c.text(info, ".fa-clock-o + span"),
            "duration": c.number(c.attr(parser, ".player-wrap", "data-duration")),
            "views": c.number(
                data.get("interactionStatistic", {}).get("userInteractionCount")
            ),
            "author_name": data.get("author"),
            "author_link": urljoin(url, c.attr(info, 'a[href^="/sites/"]', "href"))
            if info.css_first('a[href^="/sites/"]')
            else None,
            "site_url": urljoin(url, c.attr(info, 'a[href^="/paysite/"]', "href"))
            if info.css_first('a[href^="/paysite/"]')
            else None,
            "pornstars": [n.text(strip=True) for n in models],
            "pornstars_urls": [urljoin(url, n.attributes["href"]) for n in models],
            "tags": [n.text(strip=True) for n in tags],
            "tags_urls": [urljoin(url, n.attributes["href"]) for n in tags],
            "embed_url": data.get("embedUrl"),
            "language": c.attr(parser, "html", "lang"),
            "is_hd": max(n["quality"] for n in sources) >= 720,
            "player_sources": sources,
        }

    async def _load_media(self) -> dict:
        await self.load_fields("player_sources")
        sources, seen, master = [], set(), None
        for source in self.player_sources or []:
            response = await self.core.request(source["url"], method="HEAD")
            resolved = response.url
            content_type = response.headers.get("Content-Type", "").lower()
            if (
                "mpegurl" in content_type
                or ".m3u8" in urlsplit(resolved).path
                or "/hls/" in urlsplit(resolved).path
            ):
                master = master or resolved
                if resolved not in seen:
                    seen.add(resolved)
                    sources.append({"format": "hls", "url": resolved, "quality": None})
            elif content_type.startswith("video/"):
                sources.append(
                    {"format": "mp4", "url": resolved, "quality": source["quality"]}
                )
            else:
                raise VideoUnavailable(
                    "Player source did not resolve to public media", url=self.url
                )
        return {
            "media_sources": sources,
            "m3u8_base_url": master,
            "content_url": next(
                (n["url"] for n in sources if n["format"] == "mp4"), None
            ),
        }

    @property
    def id(self) -> str | None:
        if self.is_field_loaded("video_id") and self.video_id is not None:
            return self.video_id
        if self.is_field_loaded("embed_url") and self.embed_url:
            return self.embed_url.rstrip("/").rsplit("/", 1)[-1]
        return None

    async def get_available_qualities(self) -> list[int]:
        await self.load_fields("media_sources")
        qualities = {
            n["quality"] for n in self.media_sources or [] if n["quality"] is not None
        }
        if self.m3u8_base_url:
            qualities.update(
                await self.core.list_available_qualities(self.m3u8_base_url)
            )
        return sorted(qualities)

    async def get_channel(self, *, load_html: bool = True) -> Channel | None:
        await self.load_fields("author_link")
        return (
            await Client(self.core).get_channel(self.author_link, load_html=load_html)
            if self.author_link
            else None
        )

    async def get_models(self, *, load_html: bool = True) -> list[Model]:
        await self.load_fields("pornstars_urls")
        return [
            await Client(self.core).get_model(url, load_html=load_html)
            for url in self.pornstars_urls or []
        ]

    async def get_site(self, *, load_html: bool = True) -> Site | None:
        await self.load_fields("site_url")
        return (
            await Client(self.core).get_site(self.site_url, load_html=load_html)
            if self.site_url
            else None
        )

    @download_errors()
    async def download(
        self,
        configuration: DownloadConfigHLS,
        *,
        refresh: bool = False,
    ):
        if refresh:
            # A new object keeps BaseMedia's source state atomic and caller metadata stable.
            self.core.cache.invalidate_url(self.url)
            fresh = Video(url=self.url, core=self.core, video_id=self.id)
            return await fresh.download(configuration)
        await self.load_fields("title", "media_sources")
        config = prepare_download_config(configuration, self.title)
        if isinstance(config, DownloadConfigHLS):
            if not self.m3u8_base_url:
                raise VideoUnavailable(
                    "This video has no public HLS stream", url=self.url
                )
            config.m3u8_base_url = self.m3u8_base_url
            return await self.core.download(configuration=config)
        raise TypeError("Expected DownloadConfigHLS")


@dataclass(slots=True, kw_only=True, repr=False)
class Collection(BaseMedia):
    core: BaseCore
    name: str | None = media_field("html")
    description: str | None = media_field("html")
    thumbnail_url: str | None = media_field("html")
    object_id: str | None = media_field("html")
    total_videos: int | None = media_field("html")
    total_pages: int | None = media_field("html")
    language: str | None = media_field("html")
    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}
    route: ClassVar[str] = ""
    setup_key: ClassVar[str] = ""

    def __post_init__(self):
        self.url = c.normalize_url(self.url, kind=self.route)

    async def _load_html(self) -> dict:
        content = await get_html_content(self.core, self.url, owner=self)
        parser = c.checked_parser(content, self.url)
        saved = c.setup(parser, ".title_favourites [data-setup]")
        if (
            not parser.css_first(c.VIDEO_REGION)
            and not (
                c.number(saved.get("total_videos")) == 0
                and parser.css_first(
                    "#custom_list_videos_common_videos_list .empty-content"
                )
            )
        ) or not c.text(parser, "h1"):
            raise VideoUnavailable(
                "Expected collection listing is missing", url=self.url
            )
        if self.setup_key and (
            not saved.get(self.setup_key)
            or saved.get("url", "").rstrip("/") != urlsplit(self.url).path.rstrip("/")
        ):
            raise VideoUnavailable(
                "Collection identity does not match the requested route", url=self.url
            )
        return {
            "name": saved.get("title") or c.text(parser, "h1"),
            "description": c.attr(parser, 'meta[name="description"]', "content"),
            "thumbnail_url": saved.get("screenshot"),
            "object_id": saved.get(self.setup_key),
            "total_videos": c.number(saved.get("total_videos")),
            "total_pages": c.page_count(parser),
            "language": c.attr(parser, "html", "lang"),
        }

    def videos(
        self,
        *,
        pages: int | Literal["all"] = 1,
        start_page: int = 1,
        iterator_config: IteratorConfig | None = None,
    ) -> ScrapeStream[Video]:
        return _listing(
            self.core,
            self.url,
            Video,
            partial(c.extract_videos, base_url=self.url),
            pages=pages,
            start_page=start_page,
            iterator_config=iterator_config,
        )

    get_videos = videos


class Model(Collection):
    route = "models"
    setup_key = "model_id"


class Channel(Collection):
    route = "sites"
    setup_key = "dvd_group_id"


class Category(Collection):
    route = "tags"
    setup_key = "tag_id"


class Site(Collection):
    """A paysite/brand collection, distinct from the site's channel grouping."""

    route = "paysite"


class Client:
    def __init__(self, core: BaseCore | None = None, *, base_url: str = c.BASE_URL):
        self.core = core if core is not None else BaseCore()
        self._owns_core = core is None
        normalized = c.normalize_url(base_url)
        if urlsplit(normalized).path != "/":
            raise ValueError("base_url must be a verified Pornhat origin")
        self.base_url = normalized.rstrip("/")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        if self._owns_core:
            await self.core.close()

    async def get_video(
        self, url: str, *, load_html: bool = True, load_media: bool = False
    ) -> Video:
        video = Video(url=url, core=self.core)
        if load_html:
            await video.load_sources("html")
        if load_media:
            await video.load_sources("media")
        return video

    async def _collection(self, kind, url, load_html):
        item = kind(url=url, core=self.core)
        if load_html:
            await item.load_sources("html")
        return item

    async def get_model(self, url: str, *, load_html: bool = True) -> Model:
        return await self._collection(Model, url, load_html)

    async def get_channel(self, url: str, *, load_html: bool = True) -> Channel:
        return await self._collection(Channel, url, load_html)

    async def get_category(self, url: str, *, load_html: bool = True) -> Category:
        return await self._collection(Category, url, load_html)

    async def get_site(self, url: str, *, load_html: bool = True) -> Site:
        return await self._collection(Site, url, load_html)

    def search(
        self,
        query: str,
        *,
        pages: int | Literal["all"] = 1,
        start_page: int = 1,
        iterator_config: IteratorConfig | None = None,
    ) -> ScrapeStream[Video]:
        if not query.strip():
            raise ValueError("Search query cannot be empty")
        url = f"{self.base_url}/search/{quote(re.sub(r'\s', '-', query.strip()), safe='')}/"
        return _listing(
            self.core,
            url,
            Video,
            partial(c.extract_videos, base_url=url),
            pages=pages,
            start_page=start_page,
            iterator_config=iterator_config,
        )

    def get_videos(
        self,
        *,
        sort: Sort = Sort.NEW,
        pages: int | Literal["all"] = 1,
        start_page: int = 1,
        iterator_config: IteratorConfig | None = None,
    ) -> ScrapeStream[Video]:
        value = Sort(sort).value
        url = f"{self.base_url}/{value}/" if value else f"{self.base_url}/"
        return _listing(
            self.core,
            url,
            Video,
            partial(c.extract_videos, base_url=url),
            pages=pages,
            start_page=start_page,
            iterator_config=iterator_config,
        )

    def _profiles(self, kind, *, sort, letter, pages, start_page, iterator_config):
        sort = ProfileSort(sort)
        route = "models" if kind is Model else "channels"
        url = f"{self.base_url}/{route}/"
        if sort is ProfileSort.ABC:
            if kind is Model and letter == "other":
                raise ValueError("The model alphabet has no 'other' group")
            if letter is not None and letter not in {
                "other",
                *"ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            }:
                raise ValueError("letter must be A-Z, 'other', or None")
            url += "abc/" + (f"{letter}/" if letter else "")
        elif letter is not None:
            raise ValueError("letter requires ProfileSort.ABC")
        elif sort.value:
            url += f"{sort.value}/"
        return _listing(
            self.core,
            url,
            kind,
            partial(c.extract_profiles, base_url=url, kind=kind.route),
            pages=pages,
            start_page=start_page,
            iterator_config=iterator_config,
        )

    def get_models(
        self,
        *,
        sort: ProfileSort = ProfileSort.RANK,
        letter: str | None = None,
        pages: int | Literal["all"] = 1,
        start_page: int = 1,
        iterator_config: IteratorConfig | None = None,
    ) -> ScrapeStream[Model]:
        return self._profiles(
            Model,
            sort=sort,
            letter=letter,
            pages=pages,
            start_page=start_page,
            iterator_config=iterator_config,
        )

    def get_channels(
        self,
        *,
        sort: ProfileSort = ProfileSort.RANK,
        letter: str | None = None,
        pages: int | Literal["all"] = 1,
        start_page: int = 1,
        iterator_config: IteratorConfig | None = None,
    ) -> ScrapeStream[Channel]:
        return self._profiles(
            Channel,
            sort=sort,
            letter=letter,
            pages=pages,
            start_page=start_page,
            iterator_config=iterator_config,
        )

    async def get_categories(self, *, load_html: bool = False) -> list[Category]:
        url = f"{self.base_url}/tags/"
        parser = c.checked_parser(await get_html_content(self.core, url), url)
        links = dict.fromkeys(
            n.attributes["href"]
            for n in parser.css('.tags-holder a.item[href^="/tags/"]')
        )
        if not links:
            raise VideoUnavailable("Tag directory is missing", url=url)
        return [
            await self.get_category(urljoin(url, link), load_html=load_html)
            for link in links
        ]


async def _run_cli(args):
    async with Client() as client:
        video = await client.get_video(args.url)
        if args.info:
            print(json.dumps(video.to_dict(), ensure_ascii=False, indent=2))
            return
        config = DownloadConfigHLS(quality=args.quality, path=args.output, remux=True)
        await video.download(config)


def main():
    parser = argparse.ArgumentParser(
        description="Inspect or download a public Pornhat video"
    )
    parser.add_argument("url")
    parser.add_argument("-q", "--quality", default="best")
    parser.add_argument("-o", "--output", default="./")
    parser.add_argument("--info", action="store_true", help="Print metadata as JSON")
    asyncio.run(_run_cli(parser.parse_args()))
