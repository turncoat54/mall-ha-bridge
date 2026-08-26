"""bridge 模块单元测试(去重/回环防护逻辑)。"""
import time

from mall_ha_bridge.bridge import Bridge
from mall_ha_bridge.config import BrokerConfig

from conftest import TEST_TOPIC, make_cfg


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
        b = make_bridge()  # mqtt=18831, discovery=18832
        b.cfg.discovery = None  # discovery 段缺省 → 复用 mqtt
        assert b._same_broker() is True


class TestStaticSchema:
    """静态 schema: 未在配置中定义的字段不建实体。"""

    class _FakeDisc:
        def __init__(self):
            self.published = []

        def is_connected(self):
            return True

        def publish(self, topic, data, qos=0, retain=False):
            self.published.append((topic, data))

    def test_unconfigured_field_skipped(self):
        b = make_bridge()
        b.disc = self._FakeDisc()
        dev = b.cfg.devices[0]  # 仅配置了 orderNo + raw_sensor
        b._publish_discovery(dev, TEST_TOPIC, {"orderNo": "x", "brandNewField": "y"})
        ids = set(b._published)
        assert "mall_test_1234_order_no" in ids                      # 配置字段照常
        assert "mall_test_1234_brand_new_field" not in ids           # 未配置字段跳过

    def test_field_configured_lookup(self):
        b = make_bridge()
        dev = b.cfg.devices[0]
        assert b._field_configured(dev, "orderNo") is True   # devices[].fields
        assert b._field_configured(dev, "status") is False   # 未定义
        b.cfg.field_defaults["status"] = None
        assert b._field_configured(dev, "status") is True    # field_defaults 兜底
