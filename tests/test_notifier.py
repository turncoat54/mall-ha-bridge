"""notifier 模块单元测试: 消息美化 + HA REST 调用。"""
import json
from unittest import mock

import pytest

from mall_ha_bridge.config import ConfigError, NotifyConfig
from mall_ha_bridge.notifier import (
    EVENT_LABELS,
    Notifier,
    build_notification,
    discover_notify_targets,
    humanize_eta,
)

REAL_MSG = {
    "event": "takeout.paid",
    "orderId": "2090991251358797826",
    "orderNo": "o202608221035371",
    "shopName": "惠满家超市",
    "status": "0",
    "taskStatus": "1",
    "etaMinutes": "1305",
    "occurredAt": "2026-08-22T10:35:40.148741074+08:00",
}


def make_notify(**kw: object) -> NotifyConfig:
    defaults: dict[str, object] = dict(
        ha_url="http://ha.local:8123", token="tok123", targets=["mobile_app_test"]
    )
    defaults.update(kw)
    return NotifyConfig(**defaults)  # type: ignore[arg-type]


# ---------------- humanize_eta ----------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("35", "35 分钟"),
        (35, "35 分钟"),
        ("1305", "约 21 小时 45 分钟"),
        (0, "0 分钟"),
        ("", "0 分钟"),
        (None, "0 分钟"),
        ("abc", "0 分钟"),
        (59, "59 分钟"),
        (60, "约 1 小时 0 分钟"),
        (61, "约 1 小时 1 分钟"),
    ],
)
def test_humanize_eta(value, expected):
    assert humanize_eta(value) == expected


# ---------------- build_notification ----------------

def test_build_notification_real_message():
    title, message = build_notification(REAL_MSG)
    assert title == "惠满家超市 · 取餐通知"
    lines = message.split("\n")
    assert len(lines) == 4
    assert lines[0] == "🏪 店铺: 惠满家超市"
    assert lines[1] == "📌 状态: 支付成功"
    assert lines[2] == "🧾 订单号: o202608221035371"
    assert lines[3] == "⏱ 预计送达: 约 21 小时 45 分钟"
    # 无用字段被丢弃(注意: orderNo 里含数字 0, 断言用键名而非字符)
    for junk in ("orderId", "status", "taskStatus", "occurredAt", "2090991251358797826"):
        assert junk not in message


def test_build_notification_all_events_labeled():
    for event, label in EVENT_LABELS.items():
        title, message = build_notification({**REAL_MSG, "event": event})
        assert f"📌 状态: {label}" in message


def test_build_notification_unknown_event_passthrough():
    _, message = build_notification({**REAL_MSG, "event": "takeout.cancelled"})
    assert "📌 状态: takeout.cancelled" in message


def test_build_notification_missing_fields():
    title, message = build_notification({})
    assert title == "取餐通知"
    assert "未知事件" in message
    assert "🏪 店铺:" not in message
    assert "🧾 订单号:" not in message
    assert "⏱ 预计送达: 0 分钟" in message


# ---------------- Notifier.send (mock urllib) ----------------

def test_send_success():
    n = Notifier(make_notify())
    with mock.patch("mall_ha_bridge.notifier.urllib.request.urlopen") as m:
        resp = mock.MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        m.return_value = resp
        assert n.send(REAL_MSG) is True
    # 白名单单目标: 只调一次, URL 指向该 notify 服务
    assert m.call_count == 1
    req = m.call_args.args[0]
    assert req.full_url == "http://ha.local:8123/api/services/notify/mobile_app_test"
    assert req.method == "POST"
    assert req.headers["Authorization"] == "Bearer tok123"
    assert req.headers["Content-type"] == "application/json"
    body = req.data.decode("utf-8")
    assert "惠满家超市 · 取餐通知" in body
    assert "\\n" in body  # 消息含换行


def test_send_auto_discover_all_devices():
    """targets 留空 = 自动发现全部 mobile_app 设备并逐个推送。"""
    n = Notifier(make_notify(targets=[]))
    with (
        mock.patch(
            "mall_ha_bridge.notifier.discover_notify_targets",
            return_value=["mobile_app_a", "mobile_app_b"],
        ) as discover,
        mock.patch("mall_ha_bridge.notifier.urllib.request.urlopen") as m,
    ):
        resp = mock.MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        m.return_value = resp
        assert n.send(REAL_MSG) is True
    discover.assert_called_once()
    assert m.call_count == 2
    urls = sorted(c.args[0].full_url for c in m.call_args_list)
    assert urls == [
        "http://ha.local:8123/api/services/notify/mobile_app_a",
        "http://ha.local:8123/api/services/notify/mobile_app_b",
    ]


def test_send_auto_discover_no_devices():
    """自动发现无结果(如用户 HA 没装手机 App): 跳过推送, 不抛错。"""
    n = Notifier(make_notify(targets=[]))
    with (
        mock.patch(
            "mall_ha_bridge.notifier.discover_notify_targets", return_value=[]
        ),
        mock.patch("mall_ha_bridge.notifier.urllib.request.urlopen") as m,
    ):
        assert n.send(REAL_MSG) is False
    m.assert_not_called()


def test_send_partial_failure():
    """一台失败不影响其他设备(错误隔离), 至少一台成功即整体成功。"""
    from urllib.error import HTTPError
    from email.message import Message

    n = Notifier(make_notify(targets=["mobile_app_bad", "mobile_app_good"]))
    err = HTTPError("http://ha", 404, "Not Found", Message(), None)

    def fake_urlopen(req, *a, **kw):
        if "mobile_app_bad" in req.full_url:
            raise err
        resp = mock.MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        return resp

    with mock.patch("mall_ha_bridge.notifier.urllib.request.urlopen", side_effect=fake_urlopen):
        assert n.send(REAL_MSG) is True


def test_send_disabled():
    n = Notifier(make_notify(enabled=False))
    with mock.patch("mall_ha_bridge.notifier.urllib.request.urlopen") as m:
        assert n.send(REAL_MSG) is False
        m.assert_not_called()


def test_send_http_error():
    from email.message import Message
    from urllib.error import HTTPError

    n = Notifier(make_notify())
    err = HTTPError("http://ha", 401, "Unauthorized", Message(), None)
    with mock.patch("mall_ha_bridge.notifier.urllib.request.urlopen", side_effect=err):
        assert n.send(REAL_MSG) is False  # 不抛出


def test_send_connection_error():
    n = Notifier(make_notify())
    with mock.patch(
        "mall_ha_bridge.notifier.urllib.request.urlopen",
        side_effect=ConnectionError("refused"),
    ):
        assert n.send(REAL_MSG) is False  # 不抛出


def test_send_welcome():
    n = Notifier(make_notify())
    with mock.patch("mall_ha_bridge.notifier.urllib.request.urlopen") as m:
        resp = mock.MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        m.return_value = resp
        assert n.send_welcome() is True
    req = m.call_args.args[0]
    body = req.data.decode("utf-8")
    assert "mall-ha-bridge 已启动" in body


# ---------------- discover_notify_targets ----------------


def _fake_services_payload():
    return [
        {"domain": "notify", "services": {
            "mobile_app_sm_s9280": {}, "mobile_app_ipad": {}, "notify": {},
        }},
        {"domain": "light", "services": {"turn_on": {}}},
        {"domain": "notify", "services": {"telegram_bot": {}}},
    ]


def test_discover_notify_targets():
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(_fake_services_payload()).encode("utf-8")
    resp.__enter__.return_value = resp
    with mock.patch("mall_ha_bridge.notifier.urllib.request.urlopen", return_value=resp) as m:
        found = discover_notify_targets("http://ha.local:8123", "tok123")
    assert found == ["mobile_app_ipad", "mobile_app_sm_s9280"]  # 过滤掉 notify/telegram
    req = m.call_args.args[0]
    assert req.full_url == "http://ha.local:8123/api/services"
    assert req.headers["Authorization"] == "Bearer tok123"


def test_discover_notify_targets_http_401():
    from email.message import Message
    from urllib.error import HTTPError

    err = HTTPError("http://ha", 401, "Unauthorized", Message(), None)
    with mock.patch(
        "mall_ha_bridge.notifier.urllib.request.urlopen", side_effect=err
    ):
        assert discover_notify_targets("http://ha.local:8123", "bad") == []


def test_discover_notify_targets_connection_error():
    with mock.patch(
        "mall_ha_bridge.notifier.urllib.request.urlopen",
        side_effect=ConnectionError("refused"),
    ):
        assert discover_notify_targets("http://ha.local:8123", "t") == []


# ---------------- NotifyConfig 解析 ----------------

def test_notify_config_from_dict():
    c = NotifyConfig.from_dict(
        {"ha_url": "http://ha.local:8123/", "token": "t", "target": "mobile_app_x"}
    )
    assert c.ha_url == "http://ha.local:8123"  # 尾斜杠去除
    assert c.targets == ["mobile_app_x"]
    assert c.enabled is True


def test_notify_config_target_list():
    c = NotifyConfig.from_dict(
        {"ha_url": "http://ha.local:8123", "token": "t",
         "target": ["mobile_app_a", "mobile_app_b"]}
    )
    assert c.targets == ["mobile_app_a", "mobile_app_b"]


def test_notify_config_target_empty_auto_discover():
    """target 留空/不写 = 自动发现全部设备。"""
    for d in (
        {"ha_url": "http://ha.local:8123", "token": "t"},
        {"ha_url": "http://ha.local:8123", "token": "t", "target": ""},
    ):
        c = NotifyConfig.from_dict(d)
        assert c.targets == []


def test_notify_config_token_empty_disables():
    """token 留空: 配置解析成功但不启用通知(插件其余功能不受影响)。"""
    c = NotifyConfig.from_dict({"ha_url": "http://ha.local:8123", "token": ""})
    assert c.enabled is False
    assert c.token == ""


def test_notify_config_missing_fields():
    with pytest.raises(ConfigError, match="ha_url"):
        NotifyConfig.from_dict({"token": "t", "target": "x"})
    with pytest.raises(ConfigError, match="键值对象"):
        NotifyConfig.from_dict("nope")
