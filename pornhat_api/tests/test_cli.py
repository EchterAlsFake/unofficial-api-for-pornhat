import asyncio
import json
import sys

import pytest

from .test_downloads import TRAILER, probe

pytestmark = pytest.mark.live


async def test_cli_metadata():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pornhat_api",
        TRAILER,
        "--info",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, error = await process.communicate()
    assert process.returncode == 0, error.decode()
    data = json.loads(out)
    assert data["video_id"] == "684951" and data["duration"] == 156
    assert "m3u8_base_url" not in data


async def test_cli_download(tmp_path):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pornhat_api",
        TRAILER,
        "--quality",
        "worst",
        "--output",
        str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, error = await process.communicate()
    assert process.returncode == 0, error.decode()
    files = list(tmp_path.glob("*.mp4"))
    assert len(files) == 1
    data = await probe(files[0])
    assert (
        next(s for s in data["streams"] if s["codec_type"] == "video")["height"] == 360
    )
    assert abs(float(data["format"]["duration"]) - 156) < 3
    files[0].unlink()
