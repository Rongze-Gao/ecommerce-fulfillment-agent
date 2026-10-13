# 数据与 RAG 准备

本文说明从 Olist CSV 到 MySQL serving 表、再到 Redis 评论索引的顺序。运行命令见根目录 [README.md](../README.md)。

原始数据需自行下载，仓库不收录 CSV / zip：

```text
https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
```

解压后的文件放到 [`data/raw/olist/`](../data/raw/olist/README.md)。来源、许可和引用见 README 的「数据来源」。

## 分层原则

| 层 | 内容 | 是否可重建 |
|---|---|---|
| raw | 接近 CSV 的原始表，保留源字段 | 可按 CSV 重新导入 |
| curated | 视图：一单一行履约口径、明细、评论语料 | 改 SQL 后重建视图即可 |
| serving | 物化表，供 Agent 反复查询 | 由 curated 重新 `CREATE TABLE AS SELECT` |
| Redis 索引 | 评论切块与 1024 维向量 | 由 `serving_review_corpus` 全量重建 |

raw 表不在导入时做业务清洗。迟到、可计算配送、时间线异常等规则写在 curated 视图中，再物化到 serving，避免 Agent 每次查询重复拼接。

## 迁移顺序

必须按文件名顺序执行，不要跳步：

1. `001_create_raw_tables.sql`  
   创建 `raw_customers`、`raw_orders`、`raw_order_items` 等表。
2. 运行 `load_raw.py`  
   按约定行数分批写入；行数与表头不符会失败。
3. `002_create_curated_views.sql`  
   定义订单履约主视图、订单明细、评论语料等。
4. `003_materialize_curated_tables.sql`  
   生成 `serving_order_fulfillment`、`serving_order_item_detail`、`serving_review_corpus`。
5. `004_add_order_fulfillment_indexes.sql`  
   为日期、州、迟到标记等查询加索引。

`load_raw.py` 只负责导入，不建表。没有第 1 步会插入失败。

## 主要 serving 表

### `serving_order_fulfillment`

一单一行，供总体指标和按月 / 按州排名使用。其中包括：

- 购买日期、购买月份、顾客州
- `is_delivered` / `is_canceled`
- `is_delivery_metric_eligible`：已妥投，且送达时间不早于下单时间，并具备预计送达日期
- `is_late_delivery`：在可计算前提下，实际送达**日历日**晚于预计送达日
- 配送天数、时间线异常标记等

### `serving_order_item_detail`

一行一个订单明细，用于商品类别、卖家、发货截止时间错过率。排名时按订单去重，避免多商品订单放大指标。

### `serving_review_corpus`

仅包含非空评论文本的记录，供 Embedding 使用。一单一行履约分析里的「代表评论」与语料库规则不同：语料保留有文本的评论，检索时再按父评论去重。

## 校验与画像

| 模块 | 作用 |
|---|---|
| `validate_raw.py` | CSV 是否存在、表头是否匹配、行数是否等于约定值 |
| `validate_serving.py` | serving 行数是否与 raw 对齐，以及迟到标记、分母等约束是否为 0 条违规 |
| `profile_raw.py` | 原始表空值、状态分布等 |
| `profile_rag_corpus.py` | 评论文本长度、语言相关统计 |
| `profile_chunking.py` | 切块策略试算，决定 chunk 大小 |

约定行数写在 `validate_raw.py` / `load_raw.py` 的表规格里，与 README 中的文件列表一致。若使用不完整的 CSV 子集，这些检查会失败。

## 评论索引

`index_review_chunks.py` 从 MySQL 读取语料，切块后调用 Ollama 中的 BGE-M3，写入 Redis DB 0。

实现要点：

- 完整评论作为父文档，切块作为检索单元（父子索引）
- 向量维度 1024，余弦距离，索引名 `idx:ecommerce:review_chunks`
- `PARENT_DOCUMENT_LIMIT` 为 `None` 时索引全部有文本的父评论；调试可改为 `10` 或 `500`
- 全量结果约为 40,958 个父文档、53,943 个切块（以实际跑批日志为准）

检索侧（`rag/review_retriever.py`）：

1. 用聊天模型把用户问题译成三个互不相同的巴西葡萄牙语查询，并保留原问题
2. 各路做 KNN，候选集大于最终 `k`
3. MaxSim：同一父评论保留各路中的最高相似度
4. 再按重复评论文本去重，返回完整父评论及订单元数据

评论只作为定性证据。工具输出里会带上这一限制，综合提示词也不允许把 5 条检索结果写成「X% 顾客投诉」。

## 地理数据

`olist_geolocation_dataset.csv` 会进入 raw 表，当前 Agent 工具不以经纬度为筛选条件。顾客州来自订单-顾客维度，而不是每次现场计算距离。

## 重建建议

- 只改 curated 口径：重新执行 002 → 003 → 004，并再跑 `validate_serving`
- 只改切块或 Embedding 模型：清空对应 Redis 索引后重跑 `index_review_chunks.py`
- 从零开始：删除 MySQL / Redis 数据卷后按 README「快速开始」整表重来（会丢掉对话历史）
