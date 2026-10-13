# 系统架构

本文说明履约诊断 Agent 的运行时结构：入口、工具、综合链、记忆和回答契约。数据准备见 [data-pipeline.md](data-pipeline.md)。

## 分层

```text
Web / CLI
    → fulfillment_agent（工具决策 + 综合 + 记忆）
        → MCP Client  ──SSE──► mcp_server（履约概览 / 分组排名）
        │                         → MySQL serving 表（只读）
        └→ search_review_evidence
              → 查询翻译 → BGE-M3 → Redis KNN → MaxSim 融合
```

| 层 | 职责 |
|---|---|
| 入口 | `agent/web_app.py`、`agent/cli.py` 接收问题和 `session_id` |
| 总协调 | `agent/fulfillment_agent.py` 装载三个工具，由模型决定是否调用 |
| 综合 | `agent/synthesis.py` 把本轮全部 ToolMessage 压成紧凑证据，生成结构化回答 |
| 记忆 | `agent/memory.py` 将用户问题与最终 JSON 回答写入 Redis DB 1 |
| 统计工具 | `mcp_server/fulfillment_server.py` 通过 MCP SSE 暴露两个只读查询 |
| 本地服务 | `services/fulfillment.py` 提供同口径 SQL，便于不经过 MCP 时核对指标 |
| 评论工具 | `agent_tools/review_evidence.py` 封装 RAG 检索 |
| 配置 | `config.py` 从项目根目录 `.env` 读取，缺项即失败 |

Agent 进程与 MCP 进程分开启动。网页默认 `127.0.0.1:28081`，MCP SSE 默认 `127.0.0.1:28080/sse`。

## 一次提问怎么走完

```text
用户问题
  → 与 Redis 中的历史消息拼成 Agent 输入
  → create_agent：理解问题，按需调用 0～3 个工具
  → 工具返回 JSON（指标 / 排名 / 评论）
  → LCEL 综合链：只根据本轮工具结果写 summary、findings 等
  → 程序从 ToolMessage 覆盖 evidence
  → 程序按工具是否完整校准 answer_status
  → 结构化结果返回给界面，并写入对话历史
```

要点：

- 涉及数量、比率、排名或具体评论时，必须走工具，不能靠模型记忆里的旧数字。
- 同一轮可以调用多个工具；综合链会检查全部成功结果，而不是只看最后一次调用。
- 若本轮已有真实工具结果，综合阶段会丢掉 Agent 草稿，避免半成品回复干扰数字。
- 对话记忆只用于继承日期范围、顾客州等已声明条件，不能代替当轮工具证据。

## 三个业务工具

### `get_fulfillment_overview`

- 类型：MCP Tool
- 输入：`start_date`、`end_date`（`YYYY-MM-DD`，默认数据集全区间），可选 `customer_state`（两位巴西州代码）
- 输出：订单量、妥投率、取消率、可计算配送订单数、迟到件数与迟到率、平均配送天数、时间线异常数量，以及筛选条件和口径说明

### `rank_fulfillment_segments`

- 类型：MCP Tool
- 分组维度：`purchase_month`、`customer_state`、`product_category`、`seller`
- 排名指标：迟到率、取消率、时间线异常率、平均配送天数、发货截止时间错过率、订单量
- 订单级维度读 `serving_order_fulfillment`；商品类别 / 卖家读 `serving_order_item_detail`（避免一单多行把订单指标重复计算）

### `search_review_evidence`

- 类型：项目内 LangChain Tool（不经过 MCP）
- 输入：自然语言问题，以及返回条数 `k`（1～10，默认 5）
- 流程：中文等问题译成多个巴西葡萄牙语检索式，与原问题一起做多路向量检索，MaxSim 融合后按父评论和重复文本去重
- 输出：完整评论、订单号、评分、州、配送标签、相似度等；每条都标明是语义候选，不是总体比例

启动时会校验工具名集合必须恰好为上述三个，缺少或多余都会直接报错。

## 最终回答结构

`FulfillmentAgentResponse`（`agent/schemas.py`）字段：

| 字段 | 含义 |
|---|---|
| `answer_status` | `answered` / `partially_answered` / `out_of_scope` |
| `summary` | 直接结论 |
| `findings` | 由证据支持的分点发现 |
| `evidence` | 仅本轮工具事实；程序会用来自 ToolMessage 的文本覆盖模型草稿 |
| `limitations` | 数据范围、口径和因果限制 |
| `next_steps` | 可继续做的分析，或需要补充的外部数据 |

状态约定：

- `answered`：主要问题已被工具结果覆盖
- `partially_answered`：只能回答一部分、证据不足，或无法证明因果关系
- `out_of_scope`：核心依赖项目中不存在的数据或执行能力（例如退款、承运商责任）

综合链使用 `with_structured_output`（JSON Schema）约束模型输出。之后 `attach_authoritative_tool_evidence` 与 `calibrate_answer_status` 在 Python 侧再校正一次，避免模型漏写评论原文或把不完整结果标成「已完整回答」。

## 对话记忆

- Redis 逻辑库与向量索引分开：DB 0 存评论切块，DB 1 存聊天记录
- Key 前缀：`ecommerce:chat_history:`
- 同一 `session_id` 可继续对话；CLI 默认会话名见 `agent/cli.py`，网页可在页面上区分会话
- 当前未设置 TTL，历史会随 Redis 数据卷保留，直到主动清空

同一会话中，后续问题若省略日期或州，Agent 应按最近一次明确指定的条件调用工具，并在回答里写出实际采用的筛选。

## 本地查询与 MCP 的关系

`services/fulfillment.py` 实现履约概览等 SQL，可在不启动 MCP 时核对口径。Agent 运行时通过 MCP 调用 `mcp_server` 中的工具。两边应对齐同一套 serving 表与迟到/妥投定义；改口径时需要同时检查服务层、MCP 查询和 curated 视图。
