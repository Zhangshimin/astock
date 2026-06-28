# AKShare

> 网址: https://github.com/akfamily/akshare  
> 语言: Python | Stars: 13k+ | License: MIT

## 简介

AKShare 是一个开源、免费的 A 股数据接口库，覆盖行情、财务、宏观等多维度数据。无需注册即可使用，数据来源于东方财富、新浪财经、生意社等权威网站。

## 核心功能

- **行情数据**: 实时行情、历史日线/分钟线、复权数据
- **财务数据**: 利润表、资产负债表、现金流表
- **基本面**: 行业板块、概念板块、龙虎榜
- **宏观数据**: 中国经济数据、全球指数
- **另类数据**: 舆情、机构调研

## 快速开始

```python
import akshare as ak

# 获取A股历史数据
stock_zh_a_hist_df = ak.stock_zh_a_hist(
    symbol="000001",
    period="daily",
    start_date="20240101",
    end_date="20240630",
    adjust="qfq"
)
print(stock_zh_a_hist_df)
```

## 安装

```bash
pip install akshare --upgrade
```

## 特点

- ✅ 完全免费，无需注册
- ✅ 数据维度丰富（3000+ 接口）
- ✅ 文档完善（中文文档）
- ✅ 社区活跃，更新频繁

## 典型应用场景

- 量化策略数据源
- 股票分析工具开发
- 金融数据研究
