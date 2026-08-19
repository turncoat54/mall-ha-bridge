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
