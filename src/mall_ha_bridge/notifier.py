"""HA 手机通知: 收到订单消息时调 HA REST API 的 notify 服务推送美化通知。

设计要点:
- 通知逻辑归属插件(而非 HA 侧自动化): 消息到达 → 解析 → 直接 POST
  /api/services/notify/<target>, 模板与消息字段耦合处都在插件内维护。
- 任何失败只记日志并返回 False, 绝不抛出 —— 通知失败不能影响
  discovery 发布 / republish 等主流程。
- 使用标准库 urllib(无需新增依赖, 容器为 slim 镜像)。
"""
from __future__ import annotations

import json
import logging
import urllib.request
from urllib.error import HTTPError

from .config import NotifyConfig

log = logging.getLogger("mall-ha-bridge")

# 事件码 → 中文展示名(以商城实测事件流为准, 见 scripts/simulate_order.py)
EVENT_LABELS = {
    "takeout.paid": "已支付",
    "takeout.accepted": "商家已接单",
    "takeout.preparing": "备餐中",
    "takeout.ready": "待取餐",
    "takeout.finished": "已完成",
}

HTTP_TIMEOUT = 10  # 秒; HA 无响应不阻塞消息处理太久


def humanize_eta(value) -> str:
    """预计送达(分钟) → 友好文本: 35 → '35 分钟', 1305 → '约 21 小时 45 分钟'。"""
    try:
        m = int(value)
    except (TypeError, ValueError):
        m = 0
    if m >= 60:
        return f"约 {m // 60} 小时 {m % 60} 分钟"
    return f"{m} 分钟"


def build_notification(fields: dict) -> tuple[str, str]:
    """parse_payload 平铺后的字段字典 → (title, message)。

    仅保留对用户有用的信息: 店铺 / 状态(中文) / 订单号 / 预计送达;
    丢弃 orderId、status、taskStatus、occurredAt 等内部或冗余字段。
    """
    event = str(fields.get("event", "") or "")
    shop = str(fields.get("shopName", "") or "")
    label = EVENT_LABELS.get(event, event or "未知事件")
    title = f"{shop} · 取餐通知" if shop else "取餐通知"
    lines = [
        f"🏪 店铺: {shop}" if shop else None,
        f"📌 状态: {label}",
        f"🧾 订单号: {fields.get('orderNo')}" if fields.get("orderNo") else None,
        f"⏱ 预计送达: {humanize_eta(fields.get('etaMinutes'))}",
    ]
    return title, "\n".join(line for line in lines if line is not None)


class Notifier:
    """通过 HA REST API 调 notify 服务发手机通知。"""

    def __init__(self, cfg: NotifyConfig):
        self.cfg = cfg

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def send(self, fields: dict) -> bool:
        """发送通知; 成功返回 True, 任何失败记日志返回 False。"""
        if not self.cfg.enabled:
            return False
        try:
            title, message = build_notification(fields)
            req = urllib.request.Request(
                f"{self.cfg.ha_url}/api/services/notify/{self.cfg.target}",
                data=json.dumps({"title": title, "message": message}, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.cfg.token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                ok = 200 <= resp.status < 300
                if not ok:
                    log.error("HA notify 返回 HTTP %s", resp.status)
                else:
                    log.info("已推送通知 -> %s (%s)", self.cfg.target, title)
                return ok
        except HTTPError as e:
            body = e.read(200)
            if isinstance(body, bytes):
                body = body.decode("utf-8", "replace")
            log.error("HA notify HTTP %s: %s", e.code, body)
            return False
        except Exception:
            log.exception("发送 HA 通知失败")
            return False
