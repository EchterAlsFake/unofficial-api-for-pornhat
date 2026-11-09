import json

import pytest
from base_api import CachePolicy, DataNotLoadedError, MediaLoadError
from selectolax.lexbor import LexborHTMLParser

from pornhat_api import Client
from pornhat_api.modules.consts import HOSTS

from .conftest import VIDEO

pytestmark = pytest.mark.live


async def test_metadata_and_relationships(client):
    raw = await client.core.fetch_text(VIDEO, cache_policy=CachePolicy.BYPASS)
    parser = LexborHTMLParser(raw)
    expected = json.loads(parser.css_first('script[type="application/ld+json"]').text())
    video = await client.get_video(VIDEO)
    assert video.video_id == "806155"
    assert video.title == expected["name"]
    assert video.description == expected["description"]
    assert video.duration == 991 and video.length == "16:31"
    assert video.publish_date == expected["uploadDate"]
    assert video.views == int(expected["interactionStatistic"]["userInteractionCount"])
    assert video.thumbnail_url == expected["thumbnailUrl"]
    assert video.embed_url == expected["embedUrl"]
    assert video.pornstars == expected["actor"]
    assert video.tags == expected["keywords"]
    assert video.preview_video_url and video.poster_url and video.is_hd
    assert len(video.player_sources) == 3
    assert video.loaded_sources == {"html"}
    assert "m3u8_base_url" not in video.to_dict()
    channel = await video.get_channel()
    assert channel.name == expected["author"] and channel.object_id == "180"
    models = await video.get_models()
    assert [model.name for model in models] == expected["actor"]
    site = await video.get_site()
    assert site.url.endswith("/paysite/naughty-america/")
    assert site.total_pages > 1


@pytest.mark.parametrize(
    "order",
    [(), ("html",), ("media",), ("html", "media"), ("media", "html"), ("both",)],
)
async def test_deferred_sources(client, order):
    before = client.core.total_requests
    video = await client.get_video(VIDEO, load_html=False)
    assert client.core.total_requests == before
    assert video.to_dict()["url"] == VIDEO
    with pytest.raises(DataNotLoadedError):
        _ = video.title
    for source in order:
        if source == "both":
            await video.load_sources("html", "media")
        else:
            await video.load_sources(source)
    await video.load_fields("duration")
    assert video.duration == 991 and video.video_id == "806155"
    if "media" in order or "both" in order:
        assert await video.get_available_qualities() == [360, 480, 720]
        assert video.loaded_sources == {"html", "media"}
        assert all(s["format"] == "hls" for s in video.media_sources)
        assert video.content_url is None


@pytest.mark.parametrize("host", sorted(HOSTS))
async def test_hosts_and_profile_identity(client, host):
    url = VIDEO.replace("www.pornhat.com", host)
    video = await client.get_video(url)
    assert video.video_id == "806155" and video.duration == 991
    expected_language = (
        host.split(".")[0]
        if host.split(".")[0] in {"de", "fr", "it", "es", "pt", "hi"}
        else "en"
    )
    assert video.language == expected_language
    assert await video.get_available_qualities() == [360, 480, 720]
    profile = await Client(client.core, base_url=f"https://{host}").get_model(
        f"https://{host}/models/venus-valencia/"
    )
    assert profile.object_id == "22070" and profile.total_videos == 53


async def test_missing_video_and_rejected_hosts(client):
    with pytest.raises(MediaLoadError):
        await client.get_video(
            "https://www.pornhat.com/video/this-video-does-not-exist-pornhat-api/"
        )
    for url in [
        "https://evil.pornhat.com/video/a/",
        "https://pornhat.com.evil.test/video/a/",
        "https://www.pornhat.com/models/",
    ]:
        with pytest.raises(ValueError):
            await client.get_video(url, load_html=False)


@pytest.mark.parametrize(
    "path,expected_id,duration",
    [
        ("/video/wife-mom-granny-dildo-lover/", "138266", 311),
        (
            "/video/foot-fucking-trailer-with-cover-girl-gizelle-blanco-and-venus-valencia-from-love-her-feet/",
            "684951",
            156,
        ),
    ],
)
async def test_metadata_variants(client, path, expected_id, duration):
    url = "https://www.pornhat.com" + path
    parser = LexborHTMLParser(await client.core.fetch_text(url))
    expected = json.loads(parser.css_first('script[type="application/ld+json"]').text())
    video = await client.get_video(url, load_html=False, load_media=True)
    assert video.video_id == expected_id and video.duration == duration
    assert video.title == expected["name"]
    assert video.publish_date == expected["uploadDate"]
    assert video.thumbnail_url == expected["thumbnailUrl"]
    assert video.pornstars == expected["actor"]
    assert video.tags == expected["keywords"]
    assert video.loaded_sources == {"html", "media"}
