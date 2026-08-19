"""bridge 模块单元测试(去重/回环防护逻辑)。"""
import time

from mall_ha_bridge.bridge import Bridge
from mall_ha_bridge.config import BrokerConfig

from conftest import make_cfg


def make_bridge(**cfg_kwargs):
    return Bridge(make_cfg(**cfg_kwargs))


class TestDedupe:
    def test_same_message_within_window_is_duplicate(self):
        b = make_bridge()
        payload = b'{"status":"ready"}'
        assert b._is_duplicate("mall/ha/x/takeout", payload) is False
        assert b._is_duplicate("mall/ha/x/takeout", payload) is True

    def test_different_payload_not_duplicate(self):
        b = make_bridge()
        assert b._is_duplicate("t", b"a") is False
        assert b._is_duplicate("t", b"b") is False

    def test_different_topic_not_duplicate(self):
        b = make_bridge()
        assert b._is_duplicate("t1", b"a") is False
        assert b._is_duplicate("t2", b"a") is False

    def test_window_expiry(self):
        b = make_bridge()
        b._dedupe_window = 0.05
        assert b._is_duplicate("t", b"a") is False
        time.sleep(0.1)
        assert b._is_duplicate("t", b"a") is False  # 窗口过期, 不再是重复


class TestSameBroker:
    def test_same_host_port(self):
        b = make_bridge()
        b.cfg.discovery = BrokerConfig(host="127.0.0.1", port=18831)  # 与 mqtt 相同
        assert b._same_broker() is True

    def test_different_port(self):
        b = make_bridge()  # mqtt=18831, discovery=18832
        assert b._same_broker() is False

    def test_discovery_unset_means_same(self):
        b = make_bridge()
        b.cfg.discovery = None  # discovery 段缺省 → 复用 mqtt
        assert b._same_broker() is True
