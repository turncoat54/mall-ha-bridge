"""生成 Home Assistant MQTT Discovery 配置消息。

每个 JSON 字段对应一个 sensor 实体, 通过 discovery 消息(带 retain)
注册到 HA; HA 收到后自动创建实体, 无需任何 YAML 配置。
"""
from __future__ import annotations

from typing import Optional

from .config import Config, DeviceConfig, FieldConfig
from .parser import humanize_key, sanitize_object_id

DEVICE_MANUFACTURER = "Mall MQTT Bridge"
DEVICE_MODEL = "mall-ha-bridge"

# 实体 object_id / unique_id 的前缀
ENTITY_PREFIX = "mall"


def discovery_topic(cfg: Config, object_id: str) -> str:
    """该实体对应的 discovery 主题。"""
    return f"{cfg.discovery_prefix}/sensor/{object_id}/config"


def device_info(cfg: Config, dev: DeviceConfig, sw_version: str) -> dict:
    return {
        "identifiers": [f"mall_ha_{dev.identifier}"],
        "name": dev.name,
        "manufacturer": DEVICE_MANUFACTURER,
        "model": DEVICE_MODEL,
        "sw_version": sw_version,
    }


def availability_block(cfg: Config) -> dict:
    """实体可用性跟踪(可选)。"""
    if not cfg.availability:
        return {}
    return {
        "availability_topic": cfg.availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
    }


def resolve_field(cfg: Config, dev: DeviceConfig, key: str) -> FieldConfig:
    """字段配置优先级: devices[].fields[key] > field_defaults[key] > 默认值。"""
    if key in dev.fields:
        return dev.fields[key]
    if key in cfg.field_defaults:
        return cfg.field_defaults[key]
    return FieldConfig()


def field_object_id(dev: DeviceConfig, key: str, fc: FieldConfig) -> str:
    """实体的 object_id(entity_id 由它生成): mall_<identifier>_<field>。"""
    if fc.object_id:
        return f"{ENTITY_PREFIX}_{sanitize_object_id(dev.identifier)}_{sanitize_object_id(fc.object_id)}"
    return f"{ENTITY_PREFIX}_{sanitize_object_id(dev.identifier)}_{sanitize_object_id(key)}"


def field_unique_id(dev: DeviceConfig, key: str) -> str:
    return f"mall_ha_{dev.identifier}_{sanitize_object_id(key)}"


def _value_template(key: str, fc: FieldConfig) -> str:
    """从原始消息 JSON 中取出字段值的模板。

    value_json 未定义(非 JSON 消息)时兜底为空字符串, 避免模板报错。
    """
    if fc.value_template:
        return fc.value_template
    escaped = key.replace("\\", "\\\\").replace("'", "\\'")
    return "{{ (value_json | default({}))['%s'] | default('') }}" % escaped


def build_field_payload(
    cfg: Config,
    dev: DeviceConfig,
    key: str,
    fc: FieldConfig,
    state_topic: str,
    sw_version: str,
) -> dict:
    """单个 JSON 字段的 sensor discovery 负载。"""
    payload = {
        "name": fc.name or humanize_key(key),
        "unique_id": field_unique_id(dev, key),
        "object_id": field_object_id(dev, key, fc),
        "state_topic": state_topic,
        "value_template": _value_template(key, fc),
        "qos": cfg.mqtt.qos,
        "device": device_info(cfg, dev, sw_version),
    }
    if fc.icon:
        payload["icon"] = fc.icon
    if fc.device_class:
        payload["device_class"] = fc.device_class
    if fc.unit_of_measurement:
        payload["unit_of_measurement"] = fc.unit_of_measurement
    payload.update(availability_block(cfg))
    return payload


def build_raw_payload(
    cfg: Config,
    dev: DeviceConfig,
    state_topic: str,
    sw_version: str,
) -> Optional[dict]:
    """'最新消息' 传感器(整条 JSON 原文), 未配置 raw_sensor 时返回 None。"""
    raw = dev.raw_sensor
    if not raw:
        return None
    object_id = f"{ENTITY_PREFIX}_{sanitize_object_id(dev.identifier)}_raw"
    payload = {
        "name": raw.get("name") or "最新消息",
        "unique_id": f"mall_ha_{dev.identifier}_raw",
        "object_id": object_id,
        "state_topic": state_topic,
        "value_template": "{{ value }}",
        "qos": cfg.mqtt.qos,
        "device": device_info(cfg, dev, sw_version),
    }
    if raw.get("icon"):
        payload["icon"] = raw["icon"]
    elif dev.icon:
        payload["icon"] = dev.icon
    payload.update(availability_block(cfg))
    return payload


# 富实体(订单摘要)默认摘要模板: 店铺 · 状态 · 订单号。
# event/taskStatus 码值映射与 config.yaml fields 注释一致(商城契约 2026-08-22)。
# 未知 event/taskStatus 时对应项留空(被 select 过滤), 不显示错误文本。
DEFAULT_SUMMARY_TEMPLATE = (
    "{% set d = value_json | default({}) %}"
    "{% set ev = {'takeout.paid':'支付成功','takeout.picked_up':'骑手已取餐',"
    "'takeout.out_for_delivery':'配送中/已发货','takeout.near_door':'快到家(进入收货围栏)',"
    "'takeout.deliverd':'已送达'} %}"
    "{% set ts = {'1':'待派单','2':'骑手取货中','3':'配送中','4':'待收货','9':'已取消'} %}"
    "{% set st = ev.get(d.get('event','')|string) or ts.get(d.get('taskStatus','')|string) or '' %}"
    "{{ [d.get('shopName',''), st, d.get('orderNo','')] | select() | select('!=','') | join(' · ') }}"
)


def build_summary_payload(
    cfg: Config,
    dev: DeviceConfig,
    state_topic: str,
    sw_version: str,
) -> Optional[dict]:
    """富实体(订单摘要): 一个实体承载整个订单。

    - state: 一句话人话摘要(店铺 · 状态 · 订单号), 由 value_template 渲染;
    - attributes: 整条 JSON 平铺(json_attributes_template), AI/自动化读这一个
      实体的 attributes 即可拿到全部结构化字段, 无需从 N 个扁平 sensor 拼装。

    未配置 dev.summary 时返回 None。模板可用 summary.value_template 覆盖。
    """
    s = dev.summary
    if s is None:  # 未配置(devices[].summary 缺省或显式 null); 空 dict = 启用+全默认
        return None
    object_id = f"{ENTITY_PREFIX}_{sanitize_object_id(dev.identifier)}_summary"
    payload = {
        "name": s.get("name") or "外卖订单",
        "unique_id": f"mall_ha_{dev.identifier}_summary",
        "object_id": object_id,
        "state_topic": state_topic,
        "value_template": s.get("value_template") or DEFAULT_SUMMARY_TEMPLATE,
        # attributes 需显式 json_attributes_topic(2026.x 未设置时订阅不生效),
        # 与 state 同一主题; 模板必须 tojson(Jinja 渲染 dict 默认输出 Python
        # repr 单引号格式, HA json_loads 会报 Erroneous JSON)
        "json_attributes_topic": state_topic,
        "json_attributes_template": "{{ value_json | tojson }}",
        "qos": cfg.mqtt.qos,
        "device": device_info(cfg, dev, sw_version),
    }
    if s.get("icon"):
        payload["icon"] = s["icon"]
    elif dev.icon:
        payload["icon"] = dev.icon
    payload.update(availability_block(cfg))
    return payload
