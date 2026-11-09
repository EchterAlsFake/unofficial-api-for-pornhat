import asyncio
import json
import shutil

import pytest
from base_api import DownloadConfigHLS
from base_api.modules.static_functions import choose_quality_from_list

from .conftest import VIDEO

pytestmark = pytest.mark.live

TRAILER = "https://www.pornhat.com/video/foot-fucking-trailer-with-cover-girl-gizelle-blanco-and-venus-valencia-from-love-her-feet/"
OLDER = "https://www.pornhat.com/video/wife-mom-granny-dildo-lover/"


async def probe(path):
    assert shutil.which("ffprobe"), "Full download validation requires ffprobe"
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:format_tags=title:stream=codec_type,width,height",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, error = await process.communicate()
    assert process.returncode == 0, error.decode()
    return json.loads(out)


async def download_checked(video, quality, path, *, refresh=False, metadata=False):
    callbacks = []
    config = DownloadConfigHLS(
        quality=quality,
        path=str(path),
        no_title=True,
        remux=True,
        return_report=True,
        metadata=video.to_dict() if metadata else None,
        callback=lambda current, total: callbacks.append((current, total)),
    )
    async with asyncio.timeout(180):
        result = await video.download(config, refresh=refresh)
    assert result.status == "completed" and not result.missing
    assert result.downloaded == result.total > 0
    assert config.m3u8_base_url is None and config.path == str(path)
    assert callbacks
    payload = await probe(path)
    if metadata:
        assert result.metadata_written
        assert payload["format"]["tags"]["title"] == video.title
    stream = next(s for s in payload["streams"] if s["codec_type"] == "video")
    selected = choose_quality_from_list(await video.get_available_qualities(), quality)
    assert stream["height"] == selected
    assert any(s["codec_type"] == "audio" for s in payload["streams"])
    assert abs(float(payload["format"]["duration"]) - video.duration) < 3
    assert int(payload["format"]["size"]) == path.stat().st_size > 10000
    evidence = {
        "video_id": video.id,
        "requested": quality,
        "height": stream["height"],
        "width": stream["width"],
        "duration": payload["format"]["duration"],
        "bytes": path.stat().st_size,
        "status": result.status,
        "segments": result.total,
    }
    path.with_suffix(".json").write_text(json.dumps(evidence, indent=2))
    print("DOWNLOAD " + json.dumps(evidence))
    path.unlink()  # Keep small evidence, not downloaded adult media.
    return result


@pytest.mark.parametrize("url", [VIDEO, TRAILER, OLDER])
async def test_complete_quality_matrix(client, tmp_path, url):
    video = await client.get_video(url)
    qualities = await video.get_available_qualities()
    assert qualities == [360, 480, 720]
    for quality in qualities:
        await download_checked(video, quality, tmp_path / f"{video.id}-{quality}.mp4")


@pytest.mark.parametrize("quality", ["best", "half", "worst", 1080])
async def test_quality_selectors_and_fallback(client, tmp_path, quality):
    video = await client.get_video(TRAILER)
    await download_checked(video, quality, tmp_path / f"selector-{quality}.mp4")


async def test_repeat_refresh_and_concurrent_context(client, tmp_path):
    first, second = await asyncio.gather(
        client.get_video(TRAILER), client.get_video(OLDER)
    )
    await asyncio.gather(
        download_checked(first, 360, tmp_path / "first.mp4", refresh=True),
        download_checked(second, 480, tmp_path / "second.mp4"),
    )
    await download_checked(first, 360, tmp_path / "repeat.mp4")


@pytest.mark.parametrize("host", ["www.pornhat.one", "de.pornhat.com"])
async def test_host_family_download(client, tmp_path, host):
    video = await client.get_video(TRAILER.replace("www.pornhat.com", host))
    await download_checked(video, 360, tmp_path / f"{host}.mp4")


async def test_remux_metadata(client, tmp_path):
    video = await client.get_video(TRAILER)
    await download_checked(video, 360, tmp_path / "metadata.mp4", metadata=True)
