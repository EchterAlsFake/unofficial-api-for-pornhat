import pytest
import pytest_asyncio
from base_api import BaseCore, make_iterator_config
from base_api.modules.config import RuntimeConfig

from pornhat_api import Client

VIDEO = (
    "https://www.pornhat.com/video/armani-black-wants-her-husband-s-friend-deep-inside/"
)
MODEL = "https://www.pornhat.com/models/armani-black/"
SMALL_MODEL = "https://www.pornhat.com/models/venus-valencia/"
CHANNEL = "https://www.pornhat.com/sites/60-plus-milfs/"


@pytest_asyncio.fixture
async def client():
    config = RuntimeConfig()
    config.videos_concurrency = 2
    config.pages_concurrency = 2
    config.max_workers_download = 4
    async with BaseCore(configuration=config) as core:
        async with Client(core=core) as client:
            yield client


@pytest.fixture
def lazy_config():
    return make_iterator_config(
        load_specific_sources=(), page_error_mode="raise", item_error_mode="raise"
    )


async def collect(stream):
    async with stream:
        return [result.unwrap() async for result in stream]
