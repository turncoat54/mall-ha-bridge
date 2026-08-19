"""parser 模块单元测试。"""
import json

import pytest

from mall_ha_bridge.parser import humanize_key, parse_payload, sanitize_object_id


class TestParsePayload:
    def test_flat_json(self):
        p = parse_payload(
            b'{"orderNo":"A1","status":"ready","amount":38.5,"ok":true,"nil":null}'
        )
        assert p == {
            "orderNo": "A1",
            "status": "ready",
            "amount": "38.5",
            "ok": "true",
            "nil": "",
        }

    def test_nested_dict_and_list_stringified(self):
        p = parse_payload(b'{"a":{"x":1},"b":[1,2]}')
        assert json.loads(p["a"]) == {"x": 1}
        assert json.loads(p["b"]) == [1, 2]

    def test_chinese_utf8(self):
        p = parse_payload('{"note":"少辣"}'.encode("utf-8"))
        assert p["note"] == "少辣"

    def test_gbk_fallback(self):
        raw = '{"note":"少辣"}'.encode("gbk")
        p = parse_payload(raw)
        assert p["note"] == "少辣"

    @pytest.mark.parametrize(
        "payload",
        [b"hello world", b"{not json", b'[1,2,3]', b'"just a string"', b""],
    )
    def test_non_json_returns_none(self, payload):
        assert parse_payload(payload) is None


class TestHumanizeKey:
    @pytest.mark.parametrize(
        "key,expected",
        [
            ("orderNo", "Order No"),
            ("order_no", "Order No"),
            ("pickupNo", "Pickup No"),
            ("status", "Status"),
            ("订单号", "订单号"),
            ("", ""),
        ],
    )
    def test_humanize(self, key, expected):
        assert humanize_key(key) == expected


class TestSanitizeObjectId:
    def test_camel_case_to_snake(self):
        assert sanitize_object_id("orderNo") == "order_no"

    def test_snake_stays(self):
        assert sanitize_object_id("order_no") == "order_no"

    def test_spaces_and_dots(self):
        assert sanitize_object_id("a.b c") == "a_b_c"

    def test_chinese_falls_back_to_hash(self):
        out = sanitize_object_id("订单号")
        assert out.startswith("field_")
        assert out == "field_" + __import__("hashlib").md5("订单号".encode()).hexdigest()[:8]

    def test_identifier_with_dash(self):
        assert sanitize_object_id("test-1234") == "test_1234"
