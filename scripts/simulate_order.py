#!/usr/bin/env python3
"""模拟商城订单 MQTT 消息(测试/演示用)。

向商城 broker 的 mall/ha/<identifier>/takeout 主题发布一条完整的
订单生命周期消息流: 新订单 → 商家已接单 → 制作中 → 待取餐 → 已完成。

"待取餐" 阶段会新增 pickupNo(取餐号)字段, 用来演示插件对新字段的
自动 discovery。

用法(容器内):
    python /app/scripts/simulate_order.py --host ha-broker.example.com --port 1883 \
        --username your-username --password your-password --identifier your-identifier
"""
from __future__ import annotations

import argparse
import json
import random
import string
import time

import paho.mqtt.client as mqtt

DEFAULT_TOPIC = "mall/ha/{identifier}/takeout"


def rand(n: int) -> str:
    return "".join(random.choices(string.digits, k=n))


def build_stages(order_no: str) -> list[tuple[str, dict]]:
    pickup_no = rand(2)
    return [
        ("新订单", {
            "orderNo": order_no,
            "status": "created",
            "statusText": "新订单",
            "shop": "米村拌饭(万达店)",
            "note": "少辣,不要香菜",
            "amount": "38.50",
            "items": [{"name": "石锅拌饭", "qty": 1}, {"name": "大酱汤", "qty": 1}],
        }),
        ("商家已接单", {
            "orderNo": order_no,
            "status": "accepted",
            "statusText": "商家已接单",
            "shop": "米村拌饭(万达店)",
            "amount": "38.50",
            "pickupNo": pickup_no,
        }),
        ("制作中", {
            "orderNo": order_no,
            "status": "preparing",
            "statusText": "制作中",
            "pickupNo": pickup_no,
        }),
        ("待取餐", {
            "orderNo": order_no,
            "status": "ready",
            "statusText": "待取餐",
            "pickupNo": pickup_no,
        }),
        ("已完成", {
            "orderNo": order_no,
            "status": "finished",
            "statusText": "已完成",
            "pickupNo": pickup_no,
        }),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="模拟商城订单 MQTT 消息")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--username", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--identifier", default="your-unique-identifier")
    ap.add_argument("--topic", default=DEFAULT_TOPIC)
    ap.add_argument("--interval", type=float, default=4.0, help="每个状态之间的间隔秒数")
    ap.add_argument("--cycles", type=int, default=1, help="完整订单周期数")
    ap.add_argument("--qos", type=int, default=0)
    args = ap.parse_args()

    topic = args.topic.replace("{identifier}", args.identifier)
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"mall-order-sim-{random.randrange(100000)}",
    )
    if args.username:
        client.username_pw_set(args.username, args.password)
    client.connect(args.host, args.port, keepalive=60)
    client.loop_start()
    print(f"已连接 {args.host}:{args.port}, 发布主题 {topic}")

    try:
        for i in range(args.cycles):
            order_no = rand(12)
            for label, payload in build_stages(order_no):
                msg = json.dumps({**payload, "ts": int(time.time())}, ensure_ascii=False)
                client.publish(topic, msg, qos=args.qos)
                print(f"[{label}] {msg}")
                time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        client.loop_stop()
        print("模拟结束")


if __name__ == "__main__":
    main()
