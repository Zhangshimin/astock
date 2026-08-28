# astock 项目总览

## 项目定位

`astock` 不是单一应用仓库，而是一个 **A 股投研 / 量化 / Agent 项目工作区**。

这个工作区主要承担三类职责：

- 聚合多个独立克隆仓库，统一放在 `repos/` 下，便于横向对比和复用。
- 在根目录保留少量自定义分析脚本，用来做临时分析或快速验证。
- 把批量分析产物输出到 `docs/`，形成可直接阅读的 Markdown 报告。

当前结构更像一个“研究中台”或“个人量化工作台”，而不是面向生产部署的单体系统。

文中对各子仓库做了“数据源 / 量化框架 / Agent / 独立应用”的分组，这是一种便于理解的工作分组，不代表这些项目之间有严格边界；有些仓库本身就是混合型项目。

## 根目录结构

| 路径 | 说明 |
|---|---|
| `repos/` | 多个 A 股相关子仓库的聚合目录，是工作区的主体。 |
| `astock_hub/` | 个人 A 股投研中枢，统一管理能力路由、论坛评论归一化和情绪报告。 |
| `config/astock_hub.json` | 投研中枢的项目注册表、能力路由和安全开关。 |
| `tools/` | 当前工作区自写的批量分析工具。 |
| `docs/` | 报告输出目录，当前以 `by-requirement/` 方式组织。 |
| `analyze_bbg.py` | 根目录单股分析脚本之一。 |
| `analyze_klhc.py` | 根目录单股分析脚本之一。 |
| `my_analysis.py` | 根目录个人分析脚本。 |
| `update_repos.ps1` | 批量更新 `repos/` 下 Git 子仓库的脚本。 |

## 当前工作区的核心层次

### 0. 统一投研中枢

`astock_hub/` 是当前推荐的统一入口。它不复制各开源项目源码，而是通过 Python API、文件适配器、子进程或本地服务调用 `repos/` 中的能力。

当前支持：

- 检查 17 个核心上游项目的状态。
- 归一化雪球、东方财富等来源的 JSONL、JSON、CSV 和 TXT 数据。
- 对论坛内容去重并执行本地词典或可选 LLM 情绪分析。
- 统一初始化、登录和采集雪球股票帖子。

入口命令：

```powershell
python -m astock_hub doctor
python -m astock_hub forum xueqiu-login
python -m astock_hub forum collect-xueqiu --symbol 600519.SH --pages 3
```

### 1. 根工具层

根目录和 `tools/` 下的脚本，负责把多个外部仓库里的能力串起来，形成当前工作区自己的分析流程。

重点文件：

- `tools/batch_analyze_all.py`
  - 面向 **全 A 股批量分析**。
  - 依赖 `repos/OpenAshare` 提供行情读取能力。
  - 依赖 `repos/adata` 提供股票基础清单缓存。
  - 输出个股明细和汇总报告到 `docs/by-requirement/全A股趋势分析/`。

- `tools/generate_watchlist_reports.py`
  - 面向 **自选股清单批量分析**。
  - 输入是一组内置股票列表。
  - 输出到 `docs/by-requirement/26.06趋势票/同花顺自选趋势票/`。

- `tools/sentiment_analyzer.py`
  - 负责 **情绪面分析**。
  - 聚合热门行业、热门概念、热股、龙虎榜、北向资金等信息。
  - 供批量分析脚本叠加情绪评分使用。

### 2. 子仓库层

`repos/` 是整个工作区最重要的部分。这里放的是多个已克隆的独立项目，每个项目有自己的技术栈、README、脚本和开发规则。

从当前内容看，可以粗分为几类：

#### 数据源 / 数据服务

- `repos/akshare`
- `repos/easy_tdx`

- `repos/OpenAshare`
  - 当前工作区最直接依赖的数据仓库之一。
  - `tools/batch_analyze_all.py` 通过它的 `Ashare.get_price` 读取行情。

- `repos/adata`
  - 当前工作区最直接依赖的数据仓库之一。
  - 主要用于读取全市场股票代码缓存等基础数据。

- `repos/a-stock-data`
  - A 股数据服务类仓库，可作为额外数据源参考。

#### 量化框架 / 回测 / 交易系统

- `repos/qlib`
- `repos/rqalpha`
- `repos/hikyuu`
- `repos/QUANTAXIS`
- `repos/vnpy`（实盘能力默认禁用）

- `repos/EasyQuant`
- `repos/new-tea-quant`
- `repos/autoquant`
- `repos/jh_quant`
- `repos/xiami-quant`
- `repos/BreadFree-Simu`

这些仓库偏向量化框架、回测引擎、策略执行或模拟交易。

#### 多 Agent / AI 投研方向

- `repos/UZI-Skill`

- `repos/TradingAgents-astock`
- `repos/TradingAgents-AShare`
- `repos/tradingagent_a`
- `repos/FinAgent`
- `repos/StockAgent`
- `repos/AShareAgent`
- `repos/AStockArena`
- `repos/llm_trade`

这些仓库偏向多角色协同分析、投资决策、研究报告生成或 Agent 工作流。

#### 独立应用 / 专项实验

- `repos/tickflow-stock-panel`
- `repos/PanWatch`

#### 论坛评论 / 舆情采集

- `repos/EastMoney_Crawler`
- `repos/xueqiu-crawler`
- `repos/xueqiu_spider_LQH_LZQ`（无正式开源许可证，仅作研究参考）

- `repos/stockfinlens`
- `repos/stock-scanner`
- `repos/AI_Stock_V3.0`
- `repos/aimoon`
- `repos/A-share-Ai`
- `repos/stock-trader-ai`
- `repos/daily_stock_analysis`

这些仓库更像独立产品、桌面应用、Web 应用或特定方向实验项目。

## 当前工作区的实际依赖关系

从现有脚本看，当前根工作区不是平均使用所有子仓库，而是 **重点依赖少量数据仓库 + 自写工具链**。

最清晰的一条链路是：

1. `tools/batch_analyze_all.py` 计算项目根目录。
2. 它把 `repos/OpenAshare` 加入 Python 路径。
3. 它从 `repos/adata/adata/stock/info/cache/all_code.csv` 读取股票列表。
4. 它调用 `tools/sentiment_analyzer.py` 叠加情绪面分析。
5. 最终把结果写到 `docs/by-requirement/...`。

这说明当前工作区的核心价值，不是把所有子仓库合并成一个系统，而是把它们当作“可复用能力源”。

## docs 目录现状

当前 `docs/` 下已经存在真实业务产物，而不是纯说明文档。

已确认的主要内容：

- `docs/by-requirement/全A股趋势分析/README.md`
  - 全市场趋势分析总报告。
- `docs/by-requirement/全A股趋势分析/stocks/`
  - 个股维度的分析明细。
- `docs/by-requirement/26.06趋势票/同花顺自选趋势票/README.md`
  - 自选股批量分析报告。

因此，新增“项目说明”时不建议继续塞到 `by-requirement/`，因为那一层更适合放分析结果。项目导读文档更适合放在 `docs/06-team/onboarding/`。

## 适合怎么使用这个工作区

### 场景 1：快速查看不同开源项目能力

如果你在比较多个 A 股 Agent / 量化项目，可以直接进入 `repos/` 下各子仓库，查看其 README、架构和脚本实现。

### 场景 2：基于已有数据源做快速分析

如果你只是想快速产出一份趋势分析报告，优先看：

- `tools/batch_analyze_all.py`
- `tools/generate_watchlist_reports.py`
- `tools/sentiment_analyzer.py`

### 场景 3：维护本地克隆仓库的新鲜度

由于 `repos/` 下仓库数量较多，逐个手动 `git pull` 成本很高。建议直接使用根目录的 `update_repos.ps1` 批量更新。

## 快速使用示例

### 运行批量更新

在项目根目录执行：

```powershell
pwsh .\update_repos.ps1
```

常见参数：

- 仅抓取远端，不执行合并：

```powershell
pwsh .\update_repos.ps1 -FetchOnly
```

- 遇到工作区有未提交改动的仓库也继续更新：

```powershell
pwsh .\update_repos.ps1 -IncludeDirty
```

- 抓取时顺便清理远端已删除分支：

```powershell
pwsh .\update_repos.ps1 -Prune
```

### 运行全市场分析

```powershell
python .\tools\batch_analyze_all.py
```

### 运行自选股分析

```powershell
python .\tools\generate_watchlist_reports.py
```

## 已知特点与限制

- 根目录目前 **不是 Git 仓库**，而是一个本地工作区。
- `repos/` 下各项目是彼此独立的 Git 仓库，规则文件和技术栈也各不相同。
- 当前根工具脚本里存在部分绝对路径或对子仓库路径的强依赖，迁移目录时需要一并检查。
- `docs/` 目前以分析结果为主，项目层说明文档较少，这也是新增本文件的原因。

## 建议的维护方式

- 新增根目录工具时，尽量统一放到 `tools/`。
- 新增项目说明、使用说明、入门索引时，优先放到 `docs/06-team/onboarding/`。
- 新增分析输出时，继续放到 `docs/by-requirement/`，避免把“说明文档”和“分析产物”混在一起。
- 更新子仓库时，优先使用 `update_repos.ps1`，减少人工逐个进入目录更新的成本。

## 快速入口

- 统一项目检查：`python -m astock_hub doctor`
- 雪球采集与情绪分析：`python -m astock_hub forum collect-xueqiu --symbol 600519.SH`
- 投研中枢说明：`docs/by-requirement/个人A股投研中枢/README.md`

- 看全市场分析：`docs/by-requirement/全A股趋势分析/README.md`
- 看自选股分析：`docs/by-requirement/26.06趋势票/同花顺自选趋势票/README.md`
- 看全市场批量分析脚本：`tools/batch_analyze_all.py`
- 看自选股批量分析脚本：`tools/generate_watchlist_reports.py`
- 看情绪面模块：`tools/sentiment_analyzer.py`
- 批量更新子仓库：`update_repos.ps1`
