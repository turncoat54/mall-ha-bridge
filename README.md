# mall-ha-bridge

把商城系统的 MQTT 订单消息桥接进 Home Assistant 的轻量插件(单容器, 零 HA 配置)。

商城每笔订单更新时, 会向 `mall/ha/<用户独特标识>/takeout` 主题推送一条 JSON 消息。
本插件订阅该主题、解析消息, 然后通过 Home Assistant 的 **MQTT Discovery** 机制
把消息里的每个 JSON 字段**自动注册为一个 sensor 实体** —— HA 端不需要写任何 YAML、
不需要重启、不需要 reload, 实体自动出现, 换标识符/加账号只改一个配置文件。

## 特性

- **零 HA 配置**: 基于 MQTT Discovery, 实体全自动注册, 不碰 configuration.yaml / mqtt.yaml
- **配置即文件**: 启动容器前在 `config.yaml` 里配好 MQTT 服务端、账号密码、独特标识
- **动态字段**: 消息里出现新字段时自动创建新 sensor(可关闭)
- **富实体**: 可选「订单摘要」实体, 一个实体承载整个订单(一句话摘要 + 全部字段 attributes), AI 决策零拼装
- **多账号支持**: `devices` 列表可配多个独特标识, 每个标识生成一组独立设备/实体
- **直连远程商城 broker**: 不再依赖 mosquitto bridge 转发
- **可用性跟踪**: 插件离线时实体自动显示为"不可用"
- **字段级定制**: 每个字段可自定义显示名、图标、device_class、单位、取值模板
- **断线自动重连**: 两端 broker 均自动重连, 重连后自动补发 discovery 配置
- **自带模拟器**: `scripts/simulate_order.py` 模拟完整订单生命周期, 便于测试

## 工作原理

```
┌────────────┐   mall/ha/<标识>/takeout   ┌─────────────────┐
│  商城 MQTT │ ────────────────────────▶ │  mall-ha-bridge │
│  broker    │        (feed 连接)         │    容器(插件)    │
└────────────┘                            └────────┬────────┘
                                                   │ 1. 解析 JSON
                                                   │ 2. 发布 discovery 配置(retained)
                                                   │ 3. (可选)转发原始消息
                                                   ▼
                                          ┌─────────────────┐
                                          │  HA 侧 MQTT      │
                                          │  broker          │
                                          └────────┬────────┘
                                                   │ MQTT Discovery 自动注册
                                                   ▼
                                          ┌─────────────────┐
                                          │  Home Assistant │
                                          │  实体自动出现     │
                                          └─────────────────┘
```

要点: 插件的 discovery 配置把每个 sensor 的 `state_topic` 指向**原始消息主题**,
用 `value_template` 从中取对应字段 —— 所以插件只需在字段首次出现时注册一次实体,
后续消息到达时 HA 自己会更新所有实体的值。

## 与旧方案对比

| | 旧方案(写死 YAML) | 本插件 |
|---|---|---|
| HA 配置 | mqtt.yaml 写死 sensor + automations.yaml 写死 trigger | 零配置, 实体自动注册 |
| 改标识符 | 三处同步: mosquitto bridge → mqtt.yaml → automation | 改 config.yaml 一处, 重启容器 |
| 新字段 | 手动加 YAML 再 reload | 自动创建 sensor |
| 多账号 | 每账号复制一份 YAML | devices 列表加一项 |
| 断线恢复 | 手动排查 | 自动重连 + 补发 discovery |

## 目录结构

```
mall-ha-bridge/
├── Dockerfile              # 单容器镜像
├── docker-compose.yml      # 部署示例
├── config.example.yaml     # 配置模板(含注释)
├── src/mall_ha_bridge/     # 插件源码
│   ├── config.py           # 配置加载与校验
│   ├── parser.py           # JSON 消息解析
│   ├── discovery.py        # HA Discovery 负载生成
│   └── bridge.py           # MQTT 双连接主逻辑
├── scripts/
│   └── simulate_order.py   # 订单消息模拟器
└── tests/                  # pytest 单元测试
```

## 快速开始

### 1. 准备配置

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml: 填商城 MQTT 服务端、HA 侧 broker、你的独特标识
```

### 2. 构建并启动

```bash
docker compose up -d --build
# 或手动:
docker build -t mall-ha-bridge:latest .
docker run -d --name mall-ha-bridge --restart unless-stopped \
  -v "$PWD/config.yaml:/app/config/config.yaml:ro" \
  -e TZ=Asia/Shanghai \
  mall-ha-bridge:latest
```

> 国内网络拉不到 `python:3.12-slim` / pypi 时, 构建加参数:
> `docker build --build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
>              --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple .`

### 3. 验证

```bash
docker logs -f mall-ha-bridge
```

看到 `已订阅 mall/ha/<标识>/takeout` 即成功。之后在 HA 的
「设置 → 设备与服务 → MQTT」里会出现新设备(默认名 "商城外卖"),
设备下就是全部订单字段 sensor。

## 配置详解

### mqtt(必填) — 商城消息 broker

```yaml
mqtt:
  host: mqtt.example.com     # 商城系统 MQTT 服务端 IP/域名
  port: 1883
  username: your-username        # 商城 broker 的账号(无需账号可不填)
  password: your-password
  qos: 0                 # 订阅 QoS
```

### discovery(可选) — HA 侧 broker

填 Home Assistant 的 MQTT 集成所连接的 broker(如本地 mosquitto)。
**与 mqtt 是同一个 broker 时省略本段。**

```yaml
discovery:
  host: ha-broker.example.com
  port: 1883
  username: your-username
  password: your-password
```

### devices(必填) — 商城账号

```yaml
devices:
  - identifier: your-unique-identifier   # 登录商城后生成的独特标识
    name: 商城外卖                                 # HA 设备显示名
    icon: mdi:storefront-outline
    # topic: mall/ha/{identifier}/takeout          # 主题模板, 默认即此格式
    raw_sensor:                                    # 可选: 整条 JSON 原文传感器
      name: 最新消息
      icon: mdi:storefront-outline
    summary:                                       # 可选: 富实体(订单摘要)
      name: 外卖订单
      icon: mdi:food-takeout-box
      # value_template: "{{ value_json['orderNo'] }}"   # 可选: 自定义摘要模板
    fields:                                        # 可选: 逐字段定制
      event:      { name: 事件,   icon: mdi:bell-ring-outline }
      orderNo:    { name: 订单号, icon: mdi:identifier }
      shopName:   { name: 店铺,   icon: mdi:store }
      etaMinutes: { name: 预计送达(分钟), unit_of_measurement: 分钟 }
```

`fields` 里每个键对应消息 JSON 的一个字段, 支持:

| 配置项 | 说明 |
|---|---|
| `name` | 实体显示名(缺省自动由字段名生成, 如 orderNo → Order No) |
| `icon` | Material Design 图标 |
| `device_class` | HA device_class(如 timestamp) |
| `unit_of_measurement` | 单位 |
| `value_template` | 自定义取值模板(缺省 `{{ (value_json \| default({}))['字段'] \| default('') }}`) |
| `enabled` | 设为 false 则不为该字段创建实体 |
| `object_id` | 自定义实体 id 后缀 |

`summary`(富实体 / 订单摘要)把整个订单收敛到一个实体: state 是一句话人话摘要
(店铺 · 状态 · 订单号, 由 value_template 渲染, 默认模板含 event/taskStatus
码值→中文映射), attributes 是整条 JSON 全部字段平铺(json_attributes_template)。
AI / 自动化读这一个实体即拿到全部结构化数据, 无需从 N 个扁平 sensor 拼装
上下文 —— 适合作为"每系统一行"的 AI 决策数据层。`summary` 缺省或显式写
`summary:` 不启用; 空段 `summary: {}` 启用并全用默认值。

### 全局开关

| 配置项 | 默认 | 说明 |
|---|---|---|
| `auto_discover` | true | 消息出现未配置的新字段时自动创建 sensor |
| `republish_raw` | false | 把收到的原始消息转发到 discovery broker 的同名主题(见下) |
| `availability` | true | 实体可用性跟踪, 插件离线显示不可用 |
| `discovery_prefix` | homeassistant | HA discovery 前缀 |
| `availability_topic` | mall_ha_bridge/availability | 可用性主题 |
| `log_level` | INFO | DEBUG / INFO / WARNING |

### field_defaults — 字段级全局默认

对所有设备生效, 优先级低于 `devices[].fields`:

```yaml
field_defaults:
  status: { name: 状态 }
```

## 部署模式

### 模式 A: 直连远程商城 broker(推荐)

`mqtt` 填商城 broker, `discovery` 填本地 broker, 互不影响:

```yaml
mqtt:
  host: mqtt.example.com      # 商城 broker
  username: your-username
  password: your-password
discovery:
  host: ha-broker.example.com    # 本地 HA broker
  username: your-username
  password: your-password
republish_raw: true       # 若 HA 侧自动化要监听原主题
```

> 原方案里 mosquitto.conf 的 `connection takeout_remote` bridge 行
> 在本模式下不再需要, 可删除(否则同一主题会收到两路重复消息)。

### 模式 B: 复用本地 mosquitto bridge

`mqtt` 与 `discovery` 都填本地 broker, 远程消息仍由 mosquitto bridge 转发进来:

```yaml
mqtt:
  host: ha-broker.example.com
  username: your-username
  password: your-password
# discovery 段省略(复用 mqtt)
```

## 在 HA 中使用

### 实体

每个字段一个 sensor, 位于设备「商城外卖」下(名字、图标按配置):
`事件 / 订单ID / 订单号 / 店铺 / 状态 / 任务状态 / 预计送达(分钟) / 发生时间 / 最新消息`。

### 仪表盘卡片

编辑概览 → 添加卡片 → 按实体 → 搜索 "商城" 即可拖入。

### 手机通知(内置, 推荐)

插件可**直接推送手机通知**(无需 HA 侧自动化): 配置 `notify` 段后, 每条订单
消息到达都会通过 HA REST API 调 notify 服务推送一条美化排版的通知:

```yaml
notify:
  ha_url: http://ha.example.com:8123        # Home Assistant 地址
  token: your-long-lived-access-token       # HA 长期访问令牌(设置→账户→安全→长期访问令牌)
  target:                                   # 留空 = 自动发现全部手机 App 设备
    # - mobile_app_sm_s9280                 # 只想推特定设备时列出(字符串或列表均可)
```

- **`target` 留空 = 自动发现**: 插件每次推送前调 HA API 自动列出全部
  `mobile_app_*` 设备并群发 —— 新用户只需填 `ha_url` + `token`, 无需知道
  任何设备名; 家里新增手机/平板装好 HA App 后自动纳入通知, 无需改配置。
  想只推特定设备时, 把 `target` 填成设备名或列表即可(白名单)。
- `token` 留空则通知不启用(插件其余功能正常); 插件启动成功时会推送一条
  「mall-ha-bridge 已启动」欢迎通知, 作为配置成功的即时反馈。
- 注意: token 建议用**管理员账号**创建(普通用户角色可能无权调用 notify 服务)。

通知内容自动格式化(换行排版, 仅保留有用信息):

```
惠满家超市 · 取餐通知          ← 标题: 店铺 + 事件中文
🏪 店铺: 惠满家超市
📌 状态: 已支付               ← event → 中文(已支付/商家已接单/备餐中/待取餐/已完成)
🧾 订单号: o202608221035371
⏱ 预计送达: 约 21 小时 45 分钟  ← etaMinutes ≥60 自动换算小时
```

- 丢弃 `orderId` / `status` / `taskStatus` / `occurredAt` 等内部或冗余字段
- 未识别的事件码原样显示, 便于发现新事件值
- 通知失败只记日志, 不影响 discovery 发布 / republish 等主流程; 多设备时
  逐台发送, 一台失败不影响其他
- 不配置 `notify` 段 = 完全不推送(通知逻辑可留给下方 HA 自动化方案)

### 自动化(通知等)

消息同时到达主题 `mall/ha/<标识>/takeout`(模式 A 需开启 `republish_raw`,
模式 B 天然可用), 用 MQTT 触发器即可:

```yaml
automation:
  - alias: 外卖支付提醒
    trigger:
      - trigger: mqtt
        topic: mall/ha/your-unique-identifier/takeout
        payload: '"takeout.paid"'       # 支付事件
    action:
      - action: notify.mobile_app_sm_s9280
        data:
          title: 外卖已支付
          message: "{{ trigger.payload_json['shopName'] }} 已接单, 预计 {{ trigger.payload_json['etaMinutes'] }} 分钟后送达"
```

## 消息格式

默认主题模板: `mall/ha/{identifier}/takeout`, 负载为 JSON 对象。
字段以商城实际推送为准(2026-08-21 捕获的真实样例):

```json
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
```

- 每个顶层字段自动变成一个 sensor(数字/布尔等一律转为字符串, 嵌套对象/数组会序列化为 JSON 字符串)
- `event` 为事件名(如 `takeout.paid` 支付、`takeout.ready` 待取餐), `status`/`taskStatus` 为数字状态码
- 非 JSON 消息: 仅更新「最新消息」原始传感器
- 商城仍在开发, 字段可能增减; 未在 `fields` 配置的新字段会被 `auto_discover` 自动创建为 sensor

## 测试与模拟

### 单元测试

```bash
docker run --rm --user root -v "$PWD:/app" -w /app mall-ha-bridge:latest \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest tests/ -v"
```

### 模拟订单消息

```bash
docker run --rm --network host mall-ha-bridge:latest \
  python /app/scripts/simulate_order.py \
  --host ha-broker.example.com --port 1883 \
  --username your-username --password your-password \
  --identifier your-unique-identifier
```

按真实消息格式依次发送外卖订单事件流:
`takeout.paid → takeout.accepted → takeout.preparing → takeout.ready → takeout.finished`,
每阶段 `status`/`taskStatus`/`etaMinutes` 随之变化。

## 常见问题

**Q: HA 里看不到实体?**
1. 确认 HA 的 MQTT 集成连的是 `discovery` 段配置的 broker
2. `docker logs mall-ha-bridge` 看是否打印 `已发布 discovery ...`
3. 用 MQTT 客户端订阅 `homeassistant/sensor/#` 看是否有 retained 消息
4. 重启 HA 的 MQTT 集成(设置 → 设备与服务 → MQTT → 重新加载)

**Q: 实体在但值不更新?**
确认消息确实到达: `docker logs mall-ha-bridge` 应打印 `收到订单消息`。
模式 A 下若自动化也依赖原主题, 记得开 `republish_raw`。

**Q: 插件重启后实体消失?**
不会。discovery 配置带 retain, 且插件收到 HA 上线消息(`homeassistant/status`)
或重连时会自动补发。

**Q: 中文乱码?**
解析优先 UTF-8, 失败回退 GBK; discovery 负载以 UTF-8 发布。

## 开发

```bash
git clone https://github.com/turncoat54/mall-ha-bridge
cd mall-ha-bridge
# 本地开发(需 Python 3.10+):
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
PYTHONPATH=src python -m mall_ha_bridge -c config.yaml   # 本地运行
python -m pytest tests/ -v                               # 跑测试
# 端到端验证(隔离环境, 需已构建镜像):
python scripts/e2e_test.py
```

## License

MIT
