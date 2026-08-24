"""配置加载与校验。

config.yaml 结构(字段说明见 config.example.yaml):

    mqtt:           商城消息 broker(必填)
    discovery:      HA 侧 broker(可选, 缺省复用 mqtt)
    devices:        商城账号独特标识列表(必填, 至少一个)
    notify:         HA 手机通知(可选, 缺省不推送)
    field_defaults: 字段级全局默认配置(可选)
    auto_discover:  消息出现新字段时自动创建 sensor(默认 true)
    republish_raw:  原样转发消息到 discovery broker(默认 false)
    availability:   实体可用性跟踪(默认 true)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import yaml

DEFAULT_TOPIC_TEMPLATE = "mall/ha/{identifier}/takeout"
DEFAULT_DISCOVERY_PREFIX = "homeassistant"
DEFAULT_AVAILABILITY_TOPIC = "mall_ha_bridge/availability"


class ConfigError(Exception):
    """配置非法。"""


@dataclass
class BrokerConfig:
    host: str
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    qos: int = 0

    @classmethod
    def from_dict(cls, d: dict, name: str) -> "BrokerConfig":
        if not isinstance(d, dict):
            raise ConfigError(f"{name} 配置段必须是键值对象")
        host = d.get("host")
        if not host:
            raise ConfigError(f"{name}.host 必填(MQTT 服务端 IP 或域名)")
        try:
            port = int(d.get("port", 1883))
        except (TypeError, ValueError):
            raise ConfigError(f"{name}.port 必须是整数")
        if not 1 <= port <= 65535:
            raise ConfigError(f"{name}.port 非法: {port}")
        try:
            qos = int(d.get("qos", 0))
        except (TypeError, ValueError):
            raise ConfigError(f"{name}.qos 必须是 0/1/2")
        if qos not in (0, 1, 2):
            raise ConfigError(f"{name}.qos 必须是 0/1/2")
        return cls(
            host=str(host),
            port=port,
            username=d.get("username"),
            password=d.get("password"),
            qos=qos,
        )


@dataclass
class FieldConfig:
    """单个 JSON 字段在 HA 中的展示配置。"""

    name: Optional[str] = None
    icon: Optional[str] = None
    device_class: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    value_template: Optional[str] = None
    enabled: bool = True
    object_id: Optional[str] = None

    @classmethod
    def from_dict(cls, d) -> "FieldConfig":
        if d is None:
            return cls()
        if not isinstance(d, dict):
            raise ConfigError(f"字段配置必须是键值对象, 得到 {type(d).__name__}")
        return cls(
            name=d.get("name"),
            icon=d.get("icon"),
            device_class=d.get("device_class"),
            unit_of_measurement=d.get("unit_of_measurement"),
            value_template=d.get("value_template"),
            enabled=d.get("enabled", True),
            object_id=d.get("object_id"),
        )


@dataclass
class NotifyConfig:
    """HA 手机通知(可选): 收到订单消息时调 HA REST API 的 notify 服务推送。

    targets 为空列表 = 自动发现 HA 上全部 mobile_app 设备并群发;
    配置了 = 白名单, 只推这些设备。
    token 为空时通知功能自动禁用(插件其余功能不受影响)。
    """

    ha_url: str
    token: str
    targets: list[str] = field(default_factory=list)
    enabled: bool = True

    @classmethod
    def from_dict(cls, d) -> "NotifyConfig":
        if d is None:
            raise ConfigError("notify 配置段为空")
        if not isinstance(d, dict):
            raise ConfigError("notify 配置段必须是键值对象")
        ha_url = str(d.get("ha_url") or "").rstrip("/")
        if not ha_url:
            raise ConfigError("notify.ha_url 必填(Home Assistant 地址, 如 http://ha.example.com:8123)")
        token = str(d.get("token") or "")
        target_raw = d.get("target")
        if target_raw is None:
            targets: list[str] = []
        elif isinstance(target_raw, str):
            targets = [target_raw.strip()] if target_raw.strip() else []
        elif isinstance(target_raw, list):
            targets = [str(t).strip() for t in target_raw if str(t).strip()]
        else:
            raise ConfigError("notify.target 必须是字符串或字符串列表")
        return cls(
            ha_url=ha_url,
            token=token,
            targets=targets,
            enabled=bool(d.get("enabled", True)) and bool(token),
        )


@dataclass
class DeviceConfig:
    """一个商城账号(独特标识)对应的设备。"""

    identifier: str
    name: str
    icon: str = "mdi:storefront-outline"
    topic: str = DEFAULT_TOPIC_TEMPLATE
    raw_sensor: Optional[dict] = None
    fields: dict = field(default_factory=dict)  # key -> FieldConfig

    @classmethod
    def from_dict(cls, d: dict, index: int) -> "DeviceConfig":
        if not isinstance(d, dict):
            raise ConfigError(f"devices[{index}] 必须是键值对象")
        identifier = str(d.get("identifier") or "").strip()
        if not identifier:
            raise ConfigError(f"devices[{index}].identifier 必填(商城用户独特标识)")
        topic = d.get("topic") or DEFAULT_TOPIC_TEMPLATE
        if "{identifier}" not in topic:
            # 允许直接写死完整主题; 也可以带 {identifier} 占位符
            pass
        raw_sensor = d.get("raw_sensor")
        if raw_sensor is not None and not isinstance(raw_sensor, dict):
            raise ConfigError(f"devices[{index}].raw_sensor 必须是键值对象")
        fields = {str(k): FieldConfig.from_dict(v) for k, v in (d.get("fields") or {}).items()}
        return cls(
            identifier=identifier,
            name=str(d.get("name") or f"商城 {identifier[:8]}"),
            icon=str(d.get("icon") or "mdi:storefront-outline"),
            topic=str(topic),
            raw_sensor=raw_sensor,
            fields=fields,
        )


@dataclass
class Config:
    mqtt: BrokerConfig
    devices: list[DeviceConfig]
    discovery: Optional[BrokerConfig] = None
    notify: Optional[NotifyConfig] = None
    field_defaults: dict = field(default_factory=dict)  # key -> FieldConfig
    auto_discover: bool = True
    republish_raw: bool = False
    availability: bool = True
    discovery_prefix: str = DEFAULT_DISCOVERY_PREFIX
    availability_topic: str = DEFAULT_AVAILABILITY_TOPIC
    log_level: str = "INFO"

    def discovery_broker(self) -> BrokerConfig:
        """HA 侧 broker(discovery 段缺省时复用 mqtt 段)。"""
        return self.discovery or self.mqtt

    def subscribe_topics(self) -> list[str]:
        """把所有设备的订阅主题去重。"""
        topics: list[str] = []
        for dev in self.devices:
            t = dev.topic.replace("{identifier}", dev.identifier)
            if t not in topics:
                topics.append(t)
        return topics

    @classmethod
    def load(cls, path: str) -> "Config":
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            raise ConfigError(f"配置文件不存在: {path}")
        except yaml.YAMLError as e:
            raise ConfigError(f"配置文件 YAML 解析失败: {e}")
        if not isinstance(data, dict):
            raise ConfigError("配置文件顶层必须是键值对象")

        if "mqtt" not in data:
            raise ConfigError("缺少 mqtt 配置段(商城消息 broker)")
        mqtt_cfg = BrokerConfig.from_dict(data["mqtt"], "mqtt")

        discovery = None
        if data.get("discovery") is not None:
            discovery = BrokerConfig.from_dict(data["discovery"], "discovery")

        notify = None
        if data.get("notify") is not None:
            notify = NotifyConfig.from_dict(data["notify"])

        devices_raw = data.get("devices")
        if not devices_raw:
            raise ConfigError("缺少 devices 配置段(至少配置一个商城账号标识)")
        devices = [DeviceConfig.from_dict(d, i) for i, d in enumerate(devices_raw)]

        field_defaults = {
            str(k): FieldConfig.from_dict(v)
            for k, v in (data.get("field_defaults") or {}).items()
        }

        return cls(
            mqtt=mqtt_cfg,
            discovery=discovery,
            notify=notify,
            devices=devices,
            field_defaults=field_defaults,
            auto_discover=bool(data.get("auto_discover", True)),
            republish_raw=bool(data.get("republish_raw", False)),
            availability=bool(data.get("availability", True)),
            discovery_prefix=str(data.get("discovery_prefix", DEFAULT_DISCOVERY_PREFIX)),
            availability_topic=str(data.get("availability_topic", DEFAULT_AVAILABILITY_TOPIC)),
            log_level=str(data.get("log_level", "INFO")).upper(),
        )
