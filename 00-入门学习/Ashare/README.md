# Ashare

> 网址: https://github.com/mpquant/Ashare  
> 语言: Python | License: MIT

## 简介

**单文件** 的 A 股实时行情数据 API。整个项目就一个 `Ashare.py` 文件，复制到项目里就能用。

## 为什么适合小白

- ✅ **零安装**：不用 pip install，不用注册，不用 token
- ✅ **单文件**：复制 `Ashare.py` 到项目就搞定
- ✅ **双数据源**：新浪+腾讯双核，自动故障切换
- ✅ **返回 DataFrame**：直接上手 pandas 分析

## 快速开始

```python
# 把 Ashare.py 放到项目目录，然后：
from Ashare import *

# 获取历史日线数据
df = get_price('sh000001', frequency='1d', count=10)
print(df)

# 获取分钟数据
df = get_price('sz000001', frequency='1m', count=120)
print(df)
```

## 一句话总结

**想最快拿到A股数据写代码，就用 Ashare。**
