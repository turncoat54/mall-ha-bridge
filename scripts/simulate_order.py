#!/usr/bin/env python3
"""模拟商城订单 MQTT 消息(测试/演示用)。

按商城真实推送的消息格式构造外卖订单事件流:
    takeout.paid → takeout.accepted → takeout.preparing → takeout.ready → takeout.finished

真实消息样例(2026-08-21 捕获):
    {
      "event": "takeout.paid",
      "orderId": "2090612409536401409",
      "orderNo": "o202608210930141",
      "shopName": "惠满家超市",
      "status": 0,
      "taskStatus": 1,
      "etaMinutes": 1305,
      "occurredAt": "2026-08-21T09:30:17.202960982+08:00"
    }

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
from datetime import datetime

import paho.mqtt.client as mqtt

DEFAULT_TOPIC = "mall/ha/{identifier}/takeout"

# 事件流: (event, status, taskStatus, etaMinutes)
STAGES = [
    ("takeout.paid",      0, 1, 35),
    ("takeout.accepted",  1, 2, 30),
    ("takeout.preparing", 2, 3, 20),
    ("takeout.ready",     3, 4, 5),
    ("takeout.finished",  4, 5, 0),
]

SHOP_NAMES = ["惠满家超市", "米村拌饭(万达店)", "沙县小吃(人民路店)"]


def rand(n: int) -> str:
    return "".join(random.choices(string.digits, k=n))


def build_order_id() -> str:
    """19 位数字订单 ID(与商城真实格式一致)。"""
    return str(int(time.time() * 1000)) + rand(6)


def build_order_no() -> str:
    """o + 年月日时分秒(14位) + 3位随机 = 18 字符(与商城真实格式一致)。"""
    return "o" + datetime.now().strftime("%Y%m%d%H%M%S") + rand(3)


def build_stages(order_no: str, order_id: str, shop_name: str) -> list[tuple[str, dict]]:
    """按真实消息格式构造完整事件流。"""
    stages = []
    for event, status, task_status, eta in STAGES:
        payload = {
            "event": event,
            "orderId": order_id,
            "orderNo": order_no,
            "shopName": shop_name,
            "status": status,
            "taskStatus": task_status,
            "etaMinutes": eta,
            "occurredAt": datetime.now().astimezone().isoformat(),
        }
        stages.append((event, payload))
    return stages


def main() -> None:
    ap = argparse.ArgumentParser(description="模拟商城订单 MQTT 消息")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--username", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--identifier", default="your-unique-identifier")
    ap.add_argument("--topic", default=DEFAULT_TOPIC)
    ap.add_argument("--interval", type=float, default=4.0, help="每个事件之间的间隔秒数")
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
            order_no = build_order_no()
            order_id = build_order_id()
            shop_name = random.choice(SHOP_NAMES)
            for event, payload in build_stages(order_no, order_id, shop_name):
                msg = json.dumps(payload, ensure_ascii=False)
                client.publish(topic, msg, qos=args.qos)
                print(f"[{event}] {msg}")
                time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        client.loop_stop()
        print("模拟结束")


if __name__ == "__main__":
    main()
