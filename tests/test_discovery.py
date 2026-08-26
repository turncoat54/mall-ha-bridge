"""discovery 模块单元测试。"""
from mall_ha_bridge.config import FieldConfig
from mall_ha_bridge.discovery import (
    DEFAULT_SUMMARY_TEMPLATE,
    availability_block,
    build_field_payload,
    build_raw_payload,
    build_summary_payload,
    discovery_topic,
    field_object_id,
    resolve_field,
)
from mall_ha_bridge.parser import sanitize_object_id

from conftest import TEST_IDENTIFIER, TEST_TOPIC, make_cfg

SW = "0.1.0"
SID = sanitize_object_id(TEST_IDENTIFIER)  # test-1234 -> test_1234


def test_field_payload_structure():
    cfg = make_cfg()
    dev = cfg.devices[0]
    fc = FieldConfig(name="订单号", icon="mdi:identifier")
    p = build_field_payload(cfg, dev, "orderNo", fc, TEST_TOPIC, SW)
    assert p["name"] == "订单号"
    assert p["unique_id"] == f"mall_ha_{TEST_IDENTIFIER}_order_no"
    assert p["object_id"] == f"mall_{SID}_order_no"
    assert p["state_topic"] == TEST_TOPIC
    assert p["value_template"] == "{{ (value_json | default({}))['orderNo'] | default('') }}"
    assert p["device"]["identifiers"] == [f"mall_ha_{TEST_IDENTIFIER}"]
    assert p["device"]["name"] == "测试商城"
    assert p["availability_topic"] == cfg.availability_topic
    assert p["payload_available"] == "online"


def test_field_default_name_and_template_for_missing_key():
    cfg = make_cfg()
    dev = cfg.devices[0]
    fc = FieldConfig()  # 未配置
    p = build_field_payload(cfg, dev, "pickupNo", fc, TEST_TOPIC, SW)
    assert p["name"] == "Pickup No"  # 自动 humanize
    assert p["value_template"] == "{{ (value_json | default({}))['pickupNo'] | default('') }}"


def test_custom_value_template():
    cfg = make_cfg()
    dev = cfg.devices[0]
    fc = FieldConfig(value_template="{{ value_json['status'] }}")
    p = build_field_payload(cfg, dev, "status", fc, TEST_TOPIC, SW)
    assert p["value_template"] == "{{ value_json['status'] }}"


def test_raw_payload():
    cfg = make_cfg()
    dev = cfg.devices[0]
    p = build_raw_payload(cfg, dev, TEST_TOPIC, SW)
    assert p is not None
    assert p["unique_id"] == f"mall_ha_{TEST_IDENTIFIER}_raw"
    assert p["object_id"] == f"mall_{SID}_raw"
    assert p["value_template"] == "{{ value }}"
    assert p["state_topic"] == TEST_TOPIC


def test_no_raw_sensor():
    cfg = make_cfg()
    cfg.devices[0].raw_sensor = None
    assert build_raw_payload(cfg, cfg.devices[0], TEST_TOPIC, SW) is None


def test_availability_disabled():
    cfg = make_cfg(availability=False)
    assert availability_block(cfg) == {}
    dev = cfg.devices[0]
    p = build_field_payload(
        cfg, dev, "orderNo", FieldConfig(), TEST_TOPIC, SW
    )
    assert "availability_topic" not in p


def test_discovery_topic():
    cfg = make_cfg()
    assert discovery_topic(cfg, "mall_x_order_no") == (
        "homeassistant/sensor/mall_x_order_no/config"
    )


def test_custom_discovery_prefix():
    cfg = make_cfg(discovery_prefix="myha")
    assert discovery_topic(cfg, "abc") == "myha/sensor/abc/config"


def test_resolve_field_priority():
    cfg = make_cfg(field_defaults={"status": FieldConfig(name="全局状态")})
    dev = cfg.devices[0]
    dev.fields["status"] = FieldConfig(name="设备状态")
    assert resolve_field(cfg, dev, "status").name == "设备状态"
    assert resolve_field(cfg, dev, "orderNo").name == "订单号"  # devices 配置优先
    assert resolve_field(cfg, dev, "note").name is None  # 未配置 → 默认


def test_disabled_field_object_id():
    cfg = make_cfg()
    dev = cfg.devices[0]
    fc = FieldConfig(object_id="custom_id")
    assert field_object_id(dev, "orderNo", fc) == f"mall_{SID}_custom_id"


# --------------------------------------------------------------------------- #
# 富实体(订单摘要)
# --------------------------------------------------------------------------- #
def test_summary_payload_structure():
    cfg = make_cfg()
    dev = cfg.devices[0]
    dev.summary = {"name": "外卖订单", "icon": "mdi:food-takeout-box"}
    p = build_summary_payload(cfg, dev, TEST_TOPIC, SW)
    assert p is not None
    assert p["name"] == "外卖订单"
    assert p["unique_id"] == f"mall_ha_{TEST_IDENTIFIER}_summary"
    assert p["object_id"] == f"mall_{SID}_summary"
    assert p["state_topic"] == TEST_TOPIC
    assert p["icon"] == "mdi:food-takeout-box"
    # attributes 平铺整条 JSON: AI 读一个实体拿全部结构化字段
    # (json_attributes_topic 必须显式指定, 2026.x 缺省时 attributes 订阅不生效;
    #  模板须 tojson, 否则 Jinja 输出 Python repr 导致 HA 报 Erroneous JSON)
    assert p["json_attributes_topic"] == TEST_TOPIC
    assert p["json_attributes_template"] == "{{ value_json | tojson }}"
    # 默认摘要模板含事件码值映射
    assert "takeout.paid" in p["value_template"]
    assert "taskStatus" in p["value_template"]
    assert p["device"]["identifiers"] == [f"mall_ha_{TEST_IDENTIFIER}"]
    assert p["device"]["name"] == "测试商城"
    assert p["availability_topic"] == cfg.availability_topic


def test_summary_defaults_name_and_icon():
    cfg = make_cfg()
    dev = cfg.devices[0]
    dev.summary = {}
    p = build_summary_payload(cfg, dev, TEST_TOPIC, SW)
    assert p["name"] == "外卖订单"  # 默认名
    assert p["icon"] == dev.icon  # 回退设备图标
    assert p["value_template"] == DEFAULT_SUMMARY_TEMPLATE


def test_summary_custom_template():
    cfg = make_cfg()
    dev = cfg.devices[0]
    dev.summary = {"value_template": "{{ value_json['orderNo'] }}"}
    p = build_summary_payload(cfg, dev, TEST_TOPIC, SW)
    assert p["value_template"] == "{{ value_json['orderNo'] }}"


def test_no_summary_returns_none():
    cfg = make_cfg()
    assert build_summary_payload(cfg, cfg.devices[0], TEST_TOPIC, SW) is None
