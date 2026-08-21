#!/usr/bin/env python3
"""端到端验证脚本(隔离环境, 不触生产)。

自动完成:
  1. 启动临时 mosquitto 容器(18831=商城 feed broker, 18832=HA discovery broker)
  2. 以测试配置启动 mall-ha-bridge 容器(--network host)
  3. simulate_order.py 发布完整外卖订单事件流(真实消息格式)
  4. 校验 discovery 注册/可用性/原消息转发/去重防回环
  5. 清理全部测试容器

用法:
    python scripts/e2e_test.py

依赖: 宿主机可执行 docker; 镜像 mall-ha-bridge:latest 已构建。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IDENT = "e2e-test-0001"
TOPIC = f"mall/ha/{IDENT}/takeout"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail else ""))


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def sh(cmd: str) -> str:
    return run(["bash", "-c", cmd]).stdout.strip()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="mall-bridge-e2e-"))
    collect = tmp / "collect.txt"

    # mosquitto 双端口配置 + 插件测试配置
    (tmp / "mosquitto.conf").write_text(
        "listener 18831 0.0.0.0\nlistener 18832 0.0.0.0\n"
        "allow_anonymous true\npersistence false\nlog_dest stdout\n",
        encoding="utf-8",
    )
    (tmp / "config.yaml").write_text(
        f"""mqtt:
  host: 127.0.0.1
  port: 18831
discovery:
  host: 127.0.0.1
  port: 18832
devices:
  - identifier: {IDENT}
    name: E2E测试商城
    raw_sensor:
      name: 最新消息
    fields:
      orderNo: {{ name: 订单号 }}
      status: {{ name: 状态, icon: mdi:state-machine }}
      shopName: {{ name: 店铺 }}
      taskStatus: {{ name: 任务状态 }}
      etaMinutes: {{ name: 预计送达(分钟) }}
republish_raw: true
log_level: INFO
""",
        encoding="utf-8",
    )

    # 0. 清理残留
    for c in ("mall-ha-bridge-test", "mosquitto-test"):
        run(["docker", "rm", "-f", c])

    # 1. 测试 broker
    r = run(["docker", "run", "-d", "--name", "mosquitto-test",
             "-p", "18831:18831", "-p", "18832:18832",
             "-v", f"{tmp / 'mosquitto.conf'}:/mosquitto/config/mosquitto.conf",
             "eclipse-mosquitto:2"])
    check("mosquitto-test 容器启动", r.returncode == 0,
          r.stdout.strip() or r.stderr.strip())
    time.sleep(3)
    ready = False
    for _ in range(10):
        r = run(["docker", "exec", "mosquitto-test", "mosquitto_pub",
                 "-h", "127.0.0.1", "-p", "18831", "-t", "probe", "-m", "1"])
        if r.returncode == 0:
            ready = True
            break
        time.sleep(1)
    check("mosquitto-test broker 就绪(18831/18832)", ready)

    # 2. 被测插件
    r = run(["docker", "run", "-d", "--name", "mall-ha-bridge-test", "--network", "host",
             "-v", f"{tmp / 'config.yaml'}:/app/config/config.yaml:ro",
             "mall-ha-bridge:latest"])
    check("mall-ha-bridge-test 容器启动", r.returncode == 0,
          r.stdout.strip() or r.stderr.strip())
    subscribed = False
    for _ in range(20):
        time.sleep(1)
        if "已订阅" in sh(f"docker logs mall-ha-bridge-test 2>&1 | tail -8"):
            subscribed = True
            break
    check("插件已订阅主题", subscribed)

    # 3. 收集器(常驻)
    sub = subprocess.Popen(
        ["docker", "exec", "mosquitto-test", "mosquitto_sub",
         "-h", "127.0.0.1", "-p", "18832", "-t", "#", "-v"],
        stdout=open(collect, "w"), stderr=subprocess.DEVNULL,
    )
    time.sleep(3)

    try:
        # 4. 模拟订单
        r = run(["docker", "run", "--rm", "--network", "host", "mall-ha-bridge:latest",
                 "python", "/app/scripts/simulate_order.py",
                 "--host", "127.0.0.1", "--port", "18831",
                 "--identifier", IDENT, "--interval", "1", "--cycles", "1"],
                timeout=60)
        check("模拟器发布 5 阶段消息", r.returncode == 0)
        time.sleep(2)

        # 5. 优雅停止
        r = run(["docker", "stop", "mall-ha-bridge-test"], timeout=30)
        check("插件优雅停止", r.returncode == 0)
        time.sleep(1)
    finally:
        if sub.poll() is None:
            sub.kill()
        sub.wait()

    lines = [l.strip() for l in open(collect, encoding="utf-8", errors="replace") if l.strip()]
    msgs = []
    for l in lines:
        parts = l.split(" ", 1)
        msgs.append((parts[0], parts[1]) if len(parts) == 2 else (l, ""))
    topics = [t for t, _ in msgs]
    joined = "\n".join(lines)

    # 断言
    expected_objects = [
        "mall_e2e_test_0001_event",
        "mall_e2e_test_0001_order_id",
        "mall_e2e_test_0001_order_no",
        "mall_e2e_test_0001_shop_name",
        "mall_e2e_test_0001_status",
        "mall_e2e_test_0001_task_status",
        "mall_e2e_test_0001_eta_minutes",
        "mall_e2e_test_0001_occurred_at",
        "mall_e2e_test_0001_raw",
    ]
    for obj in expected_objects:
        check(f"discovery 注册 {obj}",
              any(t == f"homeassistant/sensor/{obj}/config" for t in topics))

    status_payload = None
    for t, p in msgs:
        if t == f"homeassistant/sensor/mall_e2e_test_0001_status/config":
            try:
                status_payload = json.loads(p)
            except json.JSONDecodeError:
                pass
    if status_payload:
        check("status discovery: name=状态", status_payload.get("name") == "状态")
        check("status discovery: value_template",
              status_payload.get("value_template")
              == "{{ (value_json | default({}))['status'] | default('') }}")
        check("status discovery: state_topic=原始主题",
              status_payload.get("state_topic") == TOPIC)
        check("status discovery: device 归属",
              status_payload.get("device", {}).get("identifiers")
              == ["mall_ha_e2e-test-0001"])
    else:
        check("status discovery 负载解析", False)

    check("availability online 已发布", "mall_ha_bridge/availability online" in joined)
    check("availability offline 已发布(优雅停止)",
          "mall_ha_bridge/availability offline" in joined)

    # 去重防回环: 单 broker 拓扑下每事件恰 2 条(原始 + 转发), 而非无限循环
    raw_count = sum(1 for t, _ in msgs if t == TOPIC)
    check("原消息转发 + 回环去重", 5 <= raw_count <= 10, f"{raw_count} 条")
    check("转发消息含支付事件", "takeout.paid" in joined)

    r = run(["docker", "exec", "mosquitto-test", "mosquitto_sub",
             "-h", "127.0.0.1", "-p", "18832",
             "-t", "homeassistant/sensor/mall_e2e_test_0001_status/config",
             "-v", "-W", "3", "-C", "1"])
    check("discovery 配置 retained(重订阅即可收到)",
          "homeassistant/sensor/mall_e2e_test_0001_status/config" in r.stdout)

    # 清理
    run(["docker", "rm", "-f", "mall-ha-bridge-test", "mosquitto-test"])

    fails = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} checks passed")
    print(f"消息收集证据: {collect}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
