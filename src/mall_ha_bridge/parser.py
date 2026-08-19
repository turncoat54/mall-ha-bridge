"""MQTT 消息解析: JSON → 顶层字段平铺(供 HA sensor 使用)。"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional


def parse_payload(payload: bytes) -> Optional[dict[str, str]]:
    """解析 MQTT payload 为顶层字符串字段字典。

    - 嵌套 dict/list 序列化为 JSON 字符串(保证每个键都是可显示的字符串值)
    - bool 转为 "true"/"false"
    - None 转为 ""
    - 非 JSON 或顶层非对象返回 None(由调用方按纯文本处理)
    """
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = payload.decode("gbk")  # 中文 GBK 编码兜底
        except UnicodeDecodeError:
            return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    result: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            result[str(key)] = json.dumps(value, ensure_ascii=False)
        elif value is None:
            result[str(key)] = ""
        elif isinstance(value, bool):
            result[str(key)] = "true" if value else "false"
        else:
            result[str(key)] = str(value)
    return result


def humanize_key(key: str) -> str:
    """JSON 键 → 展示名: orderNo → 'Order No', order_no → 'Order No', 中文原样。"""
    if re.search(r"[\u4e00-\u9fff]", key):
        return key
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)  # camelCase 分词
    s = re.sub(r"[_\-]+", " ", s)
    s = s.strip().title()
    return s or key


def sanitize_object_id(key: str) -> str:
    """JSON 键 → 合法的 HA entity_id 片段(小写 + [a-z0-9_])。

    中文等非 ASCII 键回退为 field_<md5 前 8 位>, 保证唯一且合法。
    """
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)  # camelCase → snake
    s = re.sub(r"[^\w]+", "_", s).strip("_").lower()
    if not s or not s.isascii():
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:8]
        return f"field_{digest}"
    return s
