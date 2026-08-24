"""config 模块单元测试。"""
import pytest

from mall_ha_bridge.config import Config, ConfigError, DEFAULT_TOPIC_TEMPLATE


@pytest.fixture
def example_cfg_path():
    return "config.example.yaml"


def test_load_example(example_cfg_path):
    cfg = Config.load(example_cfg_path)
    assert cfg.mqtt.host == "mqtt.example.com"
    assert cfg.discovery is not None
    assert cfg.discovery.host == "ha-broker.example.com"
    assert len(cfg.devices) == 1
    dev = cfg.devices[0]
    assert dev.identifier == "your-unique-identifier"
    assert dev.topic == DEFAULT_TOPIC_TEMPLATE
    assert "orderNo" in dev.fields
    assert dev.raw_sensor is not None
    assert cfg.auto_discover is True
    assert cfg.republish_raw is False
    assert cfg.notify is not None
    assert cfg.notify.ha_url == "http://ha.example.com:8123"
    assert cfg.notify.targets == []  # example 中留空 = 自动发现全部手机
    assert cfg.notify.token == "your-long-lived-access-token"


def test_notify_optional(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text(
        "mqtt:\n  host: 10.0.0.1\ndevices:\n  - identifier: abc\n",
        encoding="utf-8",
    )
    cfg = Config.load(str(p))
    assert cfg.notify is None


def test_notify_token_empty_disables(tmp_path):
    """token 留空 = 通知不启用, 但配置解析成功(插件其余功能正常)。"""
    p = tmp_path / "cfg.yaml"
    p.write_text(
        "mqtt:\n  host: 10.0.0.1\nnotify:\n  ha_url: http://h\ndevices:\n  - identifier: abc\n",
        encoding="utf-8",
    )
    cfg = Config.load(str(p))
    assert cfg.notify is not None
    assert cfg.notify.enabled is False


def test_subscribe_topics():
    cfg = Config.load("config.example.yaml")
    assert cfg.subscribe_topics() == [
        "mall/ha/your-unique-identifier/takeout"
    ]


def test_discovery_falls_back_to_mqtt(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text(
        """
mqtt:
  host: 10.0.0.1
devices:
  - identifier: abc
    name: x
""",
        encoding="utf-8",
    )
    cfg = Config.load(str(p))
    assert cfg.discovery is None
    assert cfg.discovery_broker() is cfg.mqtt


def test_missing_mqtt(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("devices:\n  - identifier: abc\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mqtt"):
        Config.load(str(p))


def test_missing_devices(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("mqtt:\n  host: 1.2.3.4\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="devices"):
        Config.load(str(p))


def test_invalid_port(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text(
        "mqtt:\n  host: 1.2.3.4\n  port: 99999\ndevices:\n  - identifier: abc\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="port"):
        Config.load(str(p))


def test_missing_file():
    with pytest.raises(ConfigError, match="不存在"):
        Config.load("/nonexistent/config.yaml")


def test_invalid_yaml(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("mqtt: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML"):
        Config.load(str(p))
