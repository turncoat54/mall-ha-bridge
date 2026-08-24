"""MQTT 桥接主逻辑。

两条 MQTT 连接:
  - feed: 连接商城消息 broker, 订阅 mall/ha/<identifier>/takeout
  - disc: 连接 HA 侧 broker, 发布 Discovery 配置(retained)、可用性(LWT)、
          可选的原消息转发(republish_raw)

收到商城消息 → 解析 JSON → 为每个字段发布一条 retained discovery 配置。
HA 的 MQTT 集成自动订阅 discovery 主题并创建实体, 实体状态通过
value_template 直接从原始主题取值, 因此插件无需再发布任何状态消息。
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from typing import Optional

import paho.mqtt.client as mqtt

from . import __version__
from .config import Config, DeviceConfig
from .discovery import (
    build_field_payload,
    build_raw_payload,
    discovery_topic,
    field_object_id,
    resolve_field,
)
from .notifier import Notifier
from .parser import parse_payload

log = logging.getLogger("mall-ha-bridge")

CALLBACK_API = mqtt.CallbackAPIVersion.VERSION2


def _client_id(role: str) -> str:
    return f"mall-ha-bridge-{role}-{os.getpid()}"


class Bridge:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # object_id -> (discovery_topic, payload_json): 已发布过的 discovery 缓存
        self._published: dict[str, tuple[str, str]] = {}
        # (topic, payload) -> 最近收到时间: 去重窗口, 防止 republish 回环
        # (单 broker 部署时, 转发的消息会被自己的 feed 订阅再次收到, 造成无限循环)
        self._recent: dict[tuple[str, bytes], float] = {}
        self._dedupe_window = 5.0  # 秒
        self._same_broker_warned = False
        self.feed: Optional[mqtt.Client] = None
        self.disc: Optional[mqtt.Client] = None
        # 可选: HA 手机通知(配置 notify 段时启用)
        self.notifier = Notifier(cfg.notify) if cfg.notify is not None else None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def run(self) -> None:
        self._setup_feed()
        self._setup_disc()
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)
        log.info(
            "mall-ha-bridge %s 启动: feed=%s:%s discovery=%s:%s devices=%s",
            __version__,
            self.cfg.mqtt.host, self.cfg.mqtt.port,
            self.cfg.discovery_broker().host, self.cfg.discovery_broker().port,
            [d.identifier for d in self.cfg.devices],
        )
        if self.notifier is not None:
            if self.notifier.enabled:
                targets_desc = (
                    ", ".join(self.notifier.cfg.targets)
                    if self.notifier.cfg.targets
                    else "(自动发现全部 mobile_app 设备)"
                )
                log.info(
                    "通知已启用: HA=%s targets=%s",
                    self.notifier.cfg.ha_url, targets_desc,
                )
                # 启动欢迎通知: 验证 token/链路, 给用户即时反馈(失败只记日志)
                self.notifier.send_welcome()
            else:
                log.warning(
                    "notify 已配置但未启用(token 为空或 enabled: false), 手机通知关闭"
                )
        while not self._stop.wait(1.0):
            pass  # 连接由 paho 后台线程自动维护
        self._shutdown()

    def _on_signal(self, signum, frame) -> None:
        log.info("收到信号 %s, 正在退出...", signum)
        self._stop.set()

    def _shutdown(self) -> None:
        if self.disc is not None:
            try:
                self.disc.publish(self.cfg.availability_topic, "offline", qos=1, retain=True)
                self.disc.disconnect()
            except Exception:
                log.exception("discovery broker 断开失败")
        if self.feed is not None:
            try:
                self.feed.disconnect()
            except Exception:
                pass
        log.info("已退出")

    # ------------------------------------------------------------------ #
    # feed 连接(商城消息来源)
    # ------------------------------------------------------------------ #
    def _setup_feed(self) -> None:
        client = mqtt.Client(CALLBACK_API, client_id=_client_id("feed"), clean_session=True)
        client.on_connect = self._on_feed_connect
        client.on_message = self._on_feed_message
        client.on_disconnect = lambda c, u, df, rc, p=None: log.warning(
            "feed 连接断开 rc=%s, 自动重连中...", rc)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        b = self.cfg.mqtt
        if b.username:
            client.username_pw_set(b.username, b.password)
        self.feed = client
        # connect_async: 连接由后台循环线程管理, broker 未就绪时自动退避重试
        client.connect_async(b.host, b.port, keepalive=60)
        client.loop_start()

    def _on_feed_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False):
            log.error("feed broker 连接被拒绝: %s", reason_code)
            return
        for topic in self.cfg.subscribe_topics():
            client.subscribe(topic, qos=self.cfg.mqtt.qos)
            log.info("已订阅 %s", topic)

    def _on_feed_message(self, client, userdata, message):
        try:
            self._handle_message(message.topic, message.payload)
        except Exception:
            log.exception("处理消息失败 topic=%s", message.topic)

    # ------------------------------------------------------------------ #
    # discovery 连接(HA 侧)
    # ------------------------------------------------------------------ #
    def _setup_disc(self) -> None:
        b = self.cfg.discovery_broker()
        client = mqtt.Client(CALLBACK_API, client_id=_client_id("disc"), clean_session=True)
        client.on_connect = self._on_disc_connect
        client.on_message = self._on_disc_message
        client.on_disconnect = lambda c, u, df, rc, p=None: log.warning(
            "discovery 连接断开 rc=%s, 自动重连中...", rc)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        if b.username:
            client.username_pw_set(b.username, b.password)
        if self.cfg.availability:
            client.will_set(self.cfg.availability_topic, "offline", qos=1, retain=True)
        self.disc = client
        # connect_async: 连接由后台循环线程管理, broker 未就绪时自动退避重试
        client.connect_async(b.host, b.port, keepalive=60)
        client.loop_start()

    def _on_disc_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False):
            log.error("discovery broker 连接被拒绝: %s", reason_code)
            return
        client.subscribe("homeassistant/status", qos=0)
        if self.cfg.availability:
            client.publish(self.cfg.availability_topic, "online", qos=1, retain=True)
        # 每次(重)连接后补发已缓存的 discovery 配置
        self._republish_all()

    def _on_disc_message(self, client, userdata, message):
        if message.topic == "homeassistant/status" and message.payload.strip().lower() == b"online":
            log.info("收到 HA 上线消息, 重新发布 discovery 配置")
            self._republish_all()

    # ------------------------------------------------------------------ #
    # 核心处理
    # ------------------------------------------------------------------ #
    def _match_device(self, topic: str) -> Optional[DeviceConfig]:
        for dev in self.cfg.devices:
            expected = dev.topic.replace("{identifier}", dev.identifier)
            if topic == expected:
                return dev
        return None

    def _is_duplicate(self, topic: str, payload: bytes) -> bool:
        """去重窗口内出现过的 (topic, payload) 视为重复(回环/重复投递)。"""
        key = (topic, payload)
        now = time.monotonic()
        with self._lock:
            last = self._recent.get(key)
            if last is not None and now - last < self._dedupe_window:
                return True
            self._recent[key] = now
            if len(self._recent) > 500:
                cutoff = now - self._dedupe_window
                self._recent = {k: v for k, v in self._recent.items() if v >= cutoff}
        return False

    def _same_broker(self) -> bool:
        """feed 与 discovery 是否指向同一 broker(同 host 同 port)。"""
        b1, b2 = self.cfg.mqtt, self.cfg.discovery_broker()
        return b1.host == b2.host and b1.port == b2.port

    def _handle_message(self, topic: str, payload: bytes) -> None:
        if self._is_duplicate(topic, payload):
            log.debug("忽略重复消息 topic=%s", topic)
            return
        dev = self._match_device(topic)
        if dev is None:
            log.debug("无匹配设备, 忽略 topic=%s", topic)
            return
        fields = parse_payload(payload)
        log.info(
            "收到订单消息 topic=%s fields=%s",
            topic,
            sorted(fields) if fields else "(非 JSON, 仅更新原始消息)",
        )
        # 手机通知(独立于 discovery 连接状态; 内部容错, 失败不影响主流程)
        if self.notifier is not None:
            self.notifier.send(fields or {})
        if self.disc is None or not self.disc.is_connected():
            log.warning("discovery broker 未连接, 跳过 discovery 发布")
            return
        self._publish_discovery(dev, topic, fields)
        if self.cfg.republish_raw:
            if self._same_broker():
                if not self._same_broker_warned:
                    self._same_broker_warned = True
                    log.warning(
                        "mqtt 与 discovery 为同一 broker, 消息已在本 broker 上, "
                        "republish_raw 无意义, 已忽略"
                    )
            else:
                self.disc.publish(topic, payload, qos=self.cfg.mqtt.qos)
                log.debug("原消息已转发: %s", topic)

    def _publish_discovery(
        self,
        dev: DeviceConfig,
        topic: str,
        fields: Optional[dict],
    ) -> None:
        assert self.disc is not None
        if fields:
            for key in fields:
                fc = resolve_field(self.cfg, dev, key)
                if not fc.enabled:
                    continue
                obj_id = field_object_id(dev, key, fc)
                self._publish_once(
                    obj_id,
                    build_field_payload(self.cfg, dev, key, fc, topic, __version__),
                )
        raw = build_raw_payload(self.cfg, dev, topic, __version__)
        if raw is not None:
            self._publish_once(raw["object_id"], raw)

    def _publish_once(self, object_id: str, payload: dict) -> None:
        """每个 object_id 仅在首次(或内容变化)时发布, 消息 retained。"""
        t = discovery_topic(self.cfg, object_id)
        data = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            cached = self._published.get(object_id)
            if cached == (t, data):
                return
            self._published[object_id] = (t, data)
        self.disc.publish(t, data, qos=1, retain=True)
        log.info("已发布 discovery %s -> %s", object_id, t)

    def _republish_all(self) -> None:
        if self.disc is None or not self.disc.is_connected():
            return
        with self._lock:
            items = list(self._published.values())
        for t, data in items:
            self.disc.publish(t, data, qos=1, retain=True)
        if items:
            log.info("已补发 %d 条 discovery 配置", len(items))
