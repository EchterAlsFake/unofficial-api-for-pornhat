import json
import re
from urllib.parse import urlsplit

import pytest
from base_api import CachePolicy
from selectolax.lexbor import LexborHTMLParser

from .conftest import CHANNEL, MODEL, SMALL_MODEL, collect

pytestmark = pytest.mark.live


async def independent_pages(core, url):
    """Read the rendered listing with separate card/identity selectors."""
    pages, page = [], 1
    while True:
        current = url if page == 1 else f"{url}{page}/"
        parser = LexborHTMLParser(
            await core.fetch_text(current, cache_policy=CachePolicy.BYPASS)
        )
        wrapper = parser.css_first("#custom_list_videos_common_videos_list")
        assert wrapper is not None
        rows = []
        for card in wrapper.css(".item"):
            image = card.css_first('img[data-original*="/videos_screenshots/"]')
            if image is not None:
                match = re.search(
                    r"/videos_screenshots/\d+/(\d+)/", image.attributes["data-original"]
                )
                assert match
                rows.append(match[1])
        assert rows
        pages.append(rows)
        if page == 1:
            setup = json.loads(
                parser.css_first(".title_favourites [data-setup]").attributes[
                    "data-setup"
                ]
            )
            total = int(re.sub(r"\D", "", setup["total_videos"]))
        next_link = parser.css_first(".pagination-next a")
        if next_link is None:
            return pages, total
        page += 1
        assert (
            urlsplit(next_link.attributes["href"]).path
            == urlsplit(f"{url}{page}/").path
        )


@pytest.mark.parametrize(
    "url,getter",
    [
        (MODEL, "get_model"),
        (SMALL_MODEL, "get_model"),
        (CHANNEL, "get_channel"),
        ("https://www.pornhat.com/sites/deep-throat-sirens/", "get_channel"),
    ],
)
async def test_complete_profile_reconciliation(client, lazy_config, url, getter):
    expected_pages, public_total = await independent_pages(client.core, url)
    profile = await getattr(client, getter)(url)
    assert profile.total_videos == public_total
    assert profile.total_pages == len(expected_pages)
    assert (
        profile.name
        and profile.description
        and profile.thumbnail_url
        and profile.object_id
    )
    actual = await collect(profile.videos(pages="all", iterator_config=lazy_config))
    expected = [item for page in expected_pages for item in page]
    actual_ids = [video.video_id for video in actual]
    assert actual_ids == expected
    assert len(set(actual_ids)) == len(actual_ids) == public_total
    offset = 0
    for page in expected_pages:
        assert actual_ids[offset : offset + len(page)] == page
        offset += len(page)
    # First, middle and last page ranges have identical IDs and stable ordering.
    for page in sorted({1, (len(expected_pages) + 1) // 2, len(expected_pages)}):
        results = await collect(
            profile.videos(start_page=page, pages=1, iterator_config=lazy_config)
        )
        assert [video.video_id for video in results] == expected_pages[page - 1]
    assert (
        await collect(
            profile.videos(
                start_page=len(expected_pages) + 1,
                pages="all",
                iterator_config=lazy_config,
            )
        )
        == []
    )
    assert (
        await collect(
            profile.videos(
                start_page=len(expected_pages) + 1, pages=2, iterator_config=lazy_config
            )
        )
        == []
    )
    print(
        f"RECONCILED {profile.route}: expected={public_total}, actual={len(actual)}, pages={[len(p) for p in expected_pages]}"
    )


async def test_partial_consumption_and_injected_core(client, lazy_config):
    profile = await client.get_model(SMALL_MODEL, load_html=False)
    async with profile.videos(iterator_config=lazy_config) as stream:
        first = (await anext(stream)).unwrap()
        assert first.id
    await client.close()
    assert client.core.session is not None
    await first.load_fields("title")
    assert first.title


async def test_empty_channel(client, lazy_config):
    channel = await client.get_channel(
        "https://www.pornhat.com/sites/cannon-productions/"
    )
    assert channel.name == "Cannon Productions" and channel.total_videos == 0
    assert await collect(channel.videos(pages="all", iterator_config=lazy_config)) == []
