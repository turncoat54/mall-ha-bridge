"""mall-ha-bridge: 商城 MQTT 订单消息 → Home Assistant 桥接插件。

通过 Home Assistant 的 MQTT Discovery 机制,把商城订单消息的每个
JSON 字段自动注册为 sensor 实体,HA 端零 YAML 配置。
"""

__version__ = "0.1.0"
