"""命令行入口: python -m mall_ha_bridge [-c config.yaml]"""
from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .bridge import Bridge
from .config import Config, ConfigError


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mall-ha-bridge",
        description="商城 MQTT 订单消息 → Home Assistant 桥接插件",
    )
    parser.add_argument(
        "-c", "--config",
        default="/app/config/config.yaml",
        help="配置文件路径(默认 /app/config/config.yaml)",
    )
    args = parser.parse_args()

    try:
        cfg = Config.load(args.config)
    except ConfigError as e:
        print(f"[mall-ha-bridge] 配置错误: {e}", file=sys.stderr)
        sys.exit(2)

    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        Bridge(cfg).run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
