"""测试辅助: 构造内存中的 Config。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 兼容两种布局: 镜像内 /app/mall_ha_bridge(顶层) 与 源码树 /app/src/mall_ha_bridge
for p in (ROOT, ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mall_ha_bridge.config import BrokerConfig, Config, DeviceConfig, FieldConfig  # noqa: E402

TEST_IDENTIFIER = "test-1234"
TEST_TOPIC = "mall/ha/test-1234/takeout"


def make_cfg(
    *,
    devices: list[DeviceConfig] | None = None,
    field_defaults: dict | None = None,
    **kwargs,
) -> Config:
    dev = devices or [
        DeviceConfig(
            identifier=TEST_IDENTIFIER,
            name="测试商城",
            raw_sensor={"name": "最新消息"},
            fields={"orderNo": FieldConfig(name="订单号")},
        )
    ]
    return Config(
        mqtt=BrokerConfig(host="127.0.0.1", port=18831),
        discovery=BrokerConfig(host="127.0.0.1", port=18832),
        devices=dev,
        field_defaults=field_defaults or {},
        **kwargs,
    )
