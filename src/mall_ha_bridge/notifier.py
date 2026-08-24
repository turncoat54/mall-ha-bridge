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

# 自动发现时只认 HA mobile_app 集成注册的服务(形如 mobile_app_sm_s9280),
# 避免误推 telegram / email / persistent_notification 等其他 notify 服务
MOBILE_APP_PREFIX = "mobile_app_"

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
    """通过 HA REST API 调 notify 服务发手机通知。

    目标设备: 白名单(cfg.targets)优先; 未配置时自动发现 HA 上全部
    mobile_app 设备并群发 —— 新用户只需填 ha_url + token, 无需知道
    任何设备名(entity_id)。
    """

    def __init__(self, cfg: NotifyConfig):
        self.cfg = cfg

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def send(self, fields: dict) -> bool:
        """发送订单通知; 成功(至少一台收到)返回 True, 任何失败记日志返回 False。"""
        if not self.cfg.enabled:
            return False
        title, message = build_notification(fields)
        return self._send_to_targets(title, message)

    def send_welcome(self) -> bool:
        """启动欢迎通知: 验证 token/链路 + 给用户即时反馈(配置成功即会收到)。"""
        if not self.cfg.enabled:
            return False
        return self._send_to_targets(
            "✅ mall-ha-bridge 已启动",
            "订单通知配置成功, 有新订单消息时会推送到这里。",
        )

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _resolve_targets(self) -> list[str]:
        """目标设备: 白名单优先, 否则自动发现全部 mobile_app 设备。"""
        if self.cfg.targets:
            return list(self.cfg.targets)
        return discover_notify_targets(self.cfg.ha_url, self.cfg.token)

    def _send_to_targets(self, title: str, message: str) -> bool:
        """逐设备发送, 错误隔离(一台失败不影响其他); 至少一台成功即整体成功。"""
        targets = self._resolve_targets()
        if not targets:
            log.warning("通知目标为空(未配置 target 且自动发现无结果), 跳过推送")
            return False
        ok = 0
        for t in targets:
            if self._post(t, title, message):
                ok += 1
        log.info("通知已推送 %d/%d 台设备: %s", ok, len(targets), ", ".join(targets))
        return ok > 0

    def _post(self, target: str, title: str, message: str) -> bool:
        """向单个 notify 服务发送; 成功返回 True, 任何失败记日志返回 False。"""
        try:
            req = urllib.request.Request(
                f"{self.cfg.ha_url}/api/services/notify/{target}",
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
                    log.error("HA notify %s 返回 HTTP %s", target, resp.status)
                return ok
        except HTTPError as e:
            body = e.read(200)
            if isinstance(body, bytes):
                body = body.decode("utf-8", "replace")
            if e.code == 401:
                log.error("HA notify %s HTTP 401: token 无效或权限不足, 请检查长期访问令牌", target)
            else:
                log.error("HA notify %s HTTP %s: %s", target, e.code, body)
            return False
        except Exception:
            log.exception("发送 HA 通知失败 target=%s", target)
            return False


def discover_notify_targets(ha_url: str, token: str, timeout: int = HTTP_TIMEOUT) -> list[str]:
    """调 HA `GET /api/services`, 自动发现全部 mobile_app notify 服务。

    返回形如 ["mobile_app_sm_s9280", "mobile_app_ipad", ...] 的服务名列表。
    任何失败记日志并返回空列表(通知跳过, 不影响主流程)。
    """
    try:
        req = urllib.request.Request(
            f"{ha_url}/api/services",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 401:
            log.error("自动发现设备失败: HA 返回 401, token 无效或权限不足, 请检查长期访问令牌")
        else:
            log.error("自动发现设备失败: HA 返回 HTTP %s", e.code)
        return []
    except Exception:
        log.exception("自动发现设备失败(HA API 不可达?)")
        return []
    if not isinstance(data, list):
        log.error("自动发现设备失败: /api/services 返回结构异常")
        return []
    targets = [
        name
        for entry in data
        if isinstance(entry, dict) and entry.get("domain") == "notify"
        for name in entry.get("services", {})
        if name.startswith(MOBILE_APP_PREFIX)
    ]
    return sorted(set(targets))
