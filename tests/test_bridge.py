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


class TestSeenOrders:
    """订单指纹持久化(幂等接收者): 重连重放/重复消息不再处理。"""

    def test_mark_then_seen(self, tmp_path):
        from mall_ha_bridge.bridge import SeenOrders

        so = SeenOrders(str(tmp_path / "seen.json"))
        assert so.seen("o1", "takeout.paid") is False
        so.mark("o1", "takeout.paid")
        assert so.seen("o1", "takeout.paid") is True
        assert so.seen("o1", "takeout.deliverd") is False  # 不同 event 不同指纹

    def test_persist_across_reload(self, tmp_path):
        """落盘: 模拟进程重启/容器重建后指纹仍有效。"""
        from mall_ha_bridge.bridge import SeenOrders

        p = str(tmp_path / "seen.json")
        so = SeenOrders(p)
        so.mark("o1", "takeout.paid")
        so2 = SeenOrders(p)  # 重新加载(重启)
        assert so2.seen("o1", "takeout.paid") is True

    def test_bad_file_tolerated(self, tmp_path):
        from mall_ha_bridge.bridge import SeenOrders

        p = str(tmp_path / "seen.json")
        with open(p, "w") as f:
            f.write("not-json{{{")
        so = SeenOrders(p)
        assert so.seen("o1", "x") is False
        so.mark("o1", "x")  # 坏文件也能继续工作
        assert so.seen("o1", "x") is True

    def test_prune_when_over_max(self, tmp_path):
        from mall_ha_bridge.bridge import SeenOrders

        so = SeenOrders(str(tmp_path / "seen.json"))
        so.MAX = 10
        for i in range(12):
            so.mark(f"o{i}", "ev")
        assert len(so._data) <= 10  # 超过上限后清理最旧


class TestFingerprint:
    """_fingerprint 提取 + _handle_message 幂等链路。"""

    def test_fingerprint_extract(self):
        b = make_bridge()
        assert b._fingerprint({"orderNo": "o1", "event": "takeout.paid"}) == ("o1", "takeout.paid")
        assert b._fingerprint({"orderId": "x1", "event": "e"}) == ("x1", "e")  # orderId 兜底
        assert b._fingerprint({"orderNo": "o1"}) is None        # 缺 event
        assert b._fingerprint({"event": "e"}) is None            # 缺订单号
        assert b._fingerprint({}) is None
        assert b._fingerprint(None) is None

    def test_handle_message_idempotent(self, tmp_path):
        """指纹命中 → 整条跳过(不通知/不转发); 未命中 → 处理并记录。"""
        b = make_bridge(seen_file=str(tmp_path / "seen.json"))
        b.disc = TestStaticSchema._FakeDisc()
        calls = []

        class _N:
            def send(self, fields):
                calls.append(fields)

        b.notifier = _N()
        payload = b'{"orderNo":"o1","event":"takeout.paid","shopName":"x"}'
        b._handle_message(TEST_TOPIC, payload)
        assert len(calls) == 1                        # 第一次: 正常处理(通知)
        assert len(b.disc.published) > 0              # 并发布 discovery
        assert b.seen.seen("o1", "takeout.paid") is True
        n_pub = len(b.disc.published)
        b._handle_message(TEST_TOPIC, payload)        # 重放同一消息
        assert len(calls) == 1                        # 第二次: 指纹命中, 不再通知
        assert len(b.disc.published) == n_pub         # 也不发布 discovery/republish
