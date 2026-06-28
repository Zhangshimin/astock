# quant-easy-start

## 简介

针对完全零基础小白设计的**极简回测入门指南**。不装任何框架，只用 pandas 就能写第一个策略。

## 怎么写你的第一个策略

### 第1步：拿数据

```python
# 用 Ashare 或 baostock 拿数据
import pandas as pd
# 假设 df 有 columns: date, open, high, low, close, volume
```

### 第2步：写一个均线策略

```python
# 计算 5日 和 20日 均线
df['MA5'] = df['close'].rolling(5).mean()
df['MA20'] = df['close'].rolling(20).mean()

# 生成信号：MA5 上穿 MA20 买入，下穿卖出
df['signal'] = 0
df.loc[df['MA5'] > df['MA20'], 'signal'] = 1
df.loc[df['MA5'] <= df['MA20'], 'signal'] = -1

# 计算每日收益
df['return'] = df['close'].pct_change()
df['strategy_return'] = df['signal'].shift(1) * df['return']
```

### 第3步：看结果

```python
# 算累计收益
df['cumulative_return'] = (1 + df['strategy_return']).cumprod()
print(f"策略收益: {df['cumulative_return'].iloc[-1]:.2%}")

# 和买入持有对比
df['buy_hold'] = (1 + df['return']).cumprod()
print(f"买入持有: {df['buy_hold'].iloc[-1]:.2%}")
```

## 推荐学习顺序

```
第1周: 装 Python + pandas，学会拿数据
第2周: 写均线策略，算收益
第3周: 加止损条件，看最大回撤
第4周: 学 quanteval / EasyQuant 做正式回测
第5周: 研究因子，学 ai-quant-trade
第6周: 搭建自己的分析系统
```

## 工具推荐

| 阶段 | 数据源 | 回测工具 |
|------|--------|----------|
| 入门 | Ashare / baostock | 自己写 pandas |
| 进阶 | efinance / AKShare | quanteval / EasyQuant |
| 深入 | Tushare Pro | VeighNa / backtrader |
| 专业 | 多源组合 | deltafq / Hikyuu |
