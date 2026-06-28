# efinance

> 网址: https://github.com/Micro-sheep/efinance  
> 语言: Python | License: MIT

## 简介

基于 requests 的轻量级 A 股/基金/债券数据获取库。API 设计简洁，一行代码拿数据。

## 为什么适合小白

- ✅ **无需注册 Token**：开箱即用
- ✅ **API 极简**：`efinance.get_k_data()` 一行拿数据
- ✅ **支持多种品种**：股票、基金、债券
- ✅ **依赖少**：仅依赖 requests + pandas

## 快速开始

```python
import efinance as ef

# 获取平安银行日线数据
df = ef.stock.get_quote_history('000001')
print(df)

# 获取基金净值
df = ef.fund.get_asset_mix('110011')  # 易方达中小盘
print(df)
```

## 一句话总结

**比 Ashare 多一点点功能，比 akshare 少很多复杂度，平衡之选。**
