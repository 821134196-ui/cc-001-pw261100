import threading

import pytest

from collector.config import SourceConfig
from collector.db import Database
from collector.mock_server import MockState, create_mock_server


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "collector.db"))
    yield database
    database.close()


class MockHandle:
    """运行中的 mock 服务器句柄：base URL + 可变状态 + 来源配置工厂。"""

    def __init__(self, server, state):
        self.server = server
        self.state = state
        self.base = f"http://127.0.0.1:{server.server_address[1]}"

    def json_source(self, name="json-src", **kw) -> SourceConfig:
        defaults = dict(type="json", url=f"{self.base}/json/products",
                        page_size=10, backoff_base=0.01)
        defaults.update(kw)
        return SourceConfig(name=name, **defaults)

    def html_source(self, name="html-src", **kw) -> SourceConfig:
        defaults = dict(type="html", url=f"{self.base}/html/products",
                        backoff_base=0.01)
        defaults.update(kw)
        return SourceConfig(name=name, **defaults)


@pytest.fixture
def mock():
    state = MockState()  # 默认：JSON 25 个商品（10/页 → 3 页），HTML 20 个（8/页 → 3 页）
    server, state = create_mock_server(state=state)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield MockHandle(server, state)
    server.shutdown()
    server.server_close()
