# Olist 原始 CSV

本目录用来放官方数据集解压后的 CSV。仓库里**没有**数据文件，需要自己下载。

## 下载地址

| 说明 | 网址 |
|---|---|
| 数据集主页（点 Download） | https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce |
| 所含文件列表 | https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce/data |
| 许可（Kaggle 标注） | https://creativecommons.org/licenses/by-nc-sa/4.0/ |

需要免费 Kaggle 账号。浏览器下载的压缩包通常叫 `brazilian-ecommerce.zip`。

已安装 [Kaggle CLI](https://github.com/Kaggle/kaggle-api) 时，可在**项目根目录**执行：

```powershell
kaggle datasets download -d olistbr/brazilian-ecommerce -p data/raw/olist
```

把 zip 解压到**本目录**。9 个 CSV 应与本 README 出现在同一层，路径例如：

```text
data/raw/olist/olist_orders_dataset.csv
```

不要解压成 `data/raw/olist/brazilian-ecommerce/*.csv` 这种多一层目录。不要改文件名。zip 可以留在本目录，Git 不会提交 csv/zip。

## 需要的文件

导入脚本 `load_raw.py` 按文件名读取，缺少任意一个都会失败：

```text
olist_customers_dataset.csv
olist_geolocation_dataset.csv
olist_order_items_dataset.csv
olist_order_payments_dataset.csv
olist_order_reviews_dataset.csv
olist_orders_dataset.csv
olist_products_dataset.csv
olist_sellers_dataset.csv
product_category_name_translation.csv
```

各文件期望行数见项目根目录 README 的「准备 Olist CSV」一节。

## 校验

在项目根目录设置 `PYTHONPATH` 为 `src` 后：

```powershell
python -m ecommerce_fulfillment_agent.data_pipeline.validate_raw
```
