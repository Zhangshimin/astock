# baoStock

> 网址: https://github.com/baostock/baostock  
> 语言: Python | License: MIT

## 简介

免费、无需注册的证券数据平台。覆盖 A 股日线/分钟线、财务数据、行业分类。适合初学者入门。

## 为什么适合小白

- ✅ **无需注册**：装好库直接调用
- ✅ **文档中文**：官网有详细中文文档
- ✅ **数据规范**：直接返回规范的 DataFrame
- ✅ **财务数据**：不仅能拿K线，还能拿财务数据

## 快速开始

```python
import baostock as bs
import pandas as pd

# 登录
bs.login()

# 获取历史K线
rs = bs.query_history_k_data_plus(
    "sh.600519",  # 贵州茅台
    "date,code,open,high,low,close,volume,amount",
    start_date="2024-01-01",
    end_date="2024-12-31",
    frequency="d",  # 日线
    adjustflag="3"  # 不复权
)

df = rs.get_data()
print(df)

# 登出
bs.logout()
```

## 一句话总结

**最省心的入门数据源，装好就用，没有坑。**
