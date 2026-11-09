import re

import pytest
from base_api import CachePolicy, make_iterator_config
from selectolax.lexbor import LexborHTMLParser

from pornhat_api.modules.sorting import ProfileSort, Sort

from .conftest import collect

pytestmark = pytest.mark.live


async def test_search_pages_and_loaded_results(client, lazy_config):
    first = await collect(client.search("lesbian", iterator_config=lazy_config))
    second = await collect(
        client.search("lesbian", start_page=2, iterator_config=lazy_config)
    )
    assert len(first) == len(second) == 59
    expected = []
    for page in (1, 2):
        url = "https://www.pornhat.com/search/lesbian/" + ("2/" if page == 2 else "")
        parser = LexborHTMLParser(
            await client.core.fetch_text(url, cache_policy=CachePolicy.BYPASS)
        )
        links = parser.css(
            "#custom_list_videos_videos_list_search_result_items .thumb-video > a"
        )
        ids = []
        for link in links:
            image = link.css_first("img[data-original]")
            ids.append(
                re.search(
                    r"/videos_screenshots/\d+/(\d+)/", image.attributes["data-original"]
                )[1]
            )
        expected.append(ids)
    assert [v.id for v in first] == expected[0]
    assert [v.id for v in second] == expected[1]
    assert expected[0] != expected[1]
    # Pornhat currently repeats four boundary cards in this search. Preserve its
    # rendered page semantics, including duplicates, rather than losing items.
    assert ({v.id for v in first} & {v.id for v in second}) == (
        set(expected[0]) & set(expected[1])
    )
    loaded = await collect(
        client.search(
            "Armani Black wants her husband",
            iterator_config=make_iterator_config(load_specific_fields=("title",)),
        )
    )
    assert loaded and all(v.title for v in loaded)
    assert "806155" in {v.id for v in loaded}
    assert (
        await collect(
            client.search(
                "zzzznonexistentpornhatquery905821", iterator_config=lazy_config
            )
        )
        == []
    )


@pytest.mark.parametrize("sort", list(Sort))
async def test_video_sort(client, lazy_config, sort):
    videos = await collect(client.get_videos(sort=sort, iterator_config=lazy_config))
    assert len(videos) == 59 and len({v.id for v in videos}) == 59


@pytest.mark.parametrize("sort", list(ProfileSort))
@pytest.mark.parametrize("getter", ["get_models", "get_channels"])
async def test_profile_directories(client, lazy_config, getter, sort):
    kwargs = {"letter": "A"} if sort is ProfileSort.ABC else {}
    profiles = await collect(
        getattr(client, getter)(sort=sort, iterator_config=lazy_config, **kwargs)
    )
    assert profiles and len({p.url for p in profiles}) == len(profiles)
    await profiles[0].load_fields("name", "total_pages")
    assert profiles[0].name and profiles[0].total_pages >= 1


async def test_categories_and_site_collection(client, lazy_config):
    categories = await client.get_categories()
    assert len(categories) > 1000
    category = next(c for c in categories if c.url.endswith("/tags/blowjob/"))
    await category.load_sources("html")
    assert category.name == "blowjob" and category.object_id == "13"
    videos = await collect(category.videos(iterator_config=lazy_config))
    assert len(videos) == 59
    site = await client.get_site("https://www.pornhat.com/paysite/naughty-america/")
    assert site.total_pages > 1
    assert await collect(site.videos(iterator_config=lazy_config))


@pytest.mark.parametrize("getter", ["get_models", "get_channels"])
@pytest.mark.parametrize("letter", [None, "other"])
async def test_alphabet_special_groups(client, lazy_config, getter, letter):
    if getter == "get_models" and letter == "other":
        with pytest.raises(ValueError, match="no 'other' group"):
            client.get_models(sort=ProfileSort.ABC, letter=letter)
        return
    profiles = await collect(
        getattr(client, getter)(
            sort=ProfileSort.ABC, letter=letter, iterator_config=lazy_config
        )
    )
    assert profiles
    await profiles[0].load_fields("name")
    assert profiles[0].name


@pytest.mark.parametrize("getter", ["get_models", "get_channels"])
async def test_directory_pagination(client, lazy_config, getter):
    first = await collect(getattr(client, getter)(iterator_config=lazy_config))
    second = await collect(
        getattr(client, getter)(start_page=2, iterator_config=lazy_config)
    )
    assert first and second
    assert [p.url for p in first] != [p.url for p in second]
