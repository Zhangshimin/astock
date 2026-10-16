# 个人 A 股投研中枢

## 目标

将工作区中的数据源、论坛评论、AI 深度分析、选股、回测和盯盘能力统一到一个个人投研入口，同时保持各上游项目独立升级。

当前优先顺序：

1. 数据稳定性。
2. 雪球和东方财富股吧情绪。
3. 个股深度研究。
4. 选股与回测验证。
5. 持仓监控。

自动实盘交易默认关闭。

## 当前能力

- `python -m astock_hub doctor`：检查已注册的上游仓库、分支和工作区状态。
- `python -m astock_hub forum analyze`：读取 JSONL、JSON、CSV 或 TXT 评论数据，完成字段归一化、去重、情绪评分和报告生成。
- `python -m astock_hub forum collect-xueqiu`：采集雪球股票帖子并直接生成情绪报告。
- 统一路由配置：`config/astock_hub.json`。

## 快速使用

### MACD 分时盯盘

交互选择一只股票，出现 1 分钟 MACD 金叉或死叉时显示 Windows 弹窗：

```powershell
python .\tools\intraday_macd_alert.py
```

也可以直接指定股票代码：

```powershell
python .\tools\intraday_macd_alert.py 600519
```

使用任务栏图形窗口，显示当日分时价格线和 MACD，并在信号出现时闪烁任务栏、显示右下角提醒：

```powershell
pythonw .\tools\intraday_macd_alert.py 600519 --gui
```

不指定代码时会先显示选股输入框：

```powershell
pythonw .\tools\intraday_macd_alert.py --gui
```

显示贴在任务栏上沿的紧凑行情条，同时展示分时线、V 量能和 MACD：

```powershell
pythonw .\tools\intraday_macd_alert.py 600519 --bar
```

行情条采用 565×32 紧凑模式，直接覆盖在 Windows 任务栏通知区左侧，并自动匹配任务栏底色。左侧代码框输入 6 位代码并按回车立即换股；金叉和死叉触发时会播放系统提示音，并用绿色/红色色条与“买/卖”文字高亮 30 秒。中部 `V 24.9w` 表示最新已完成分钟的成交量，单位为万股；右侧默认用两行依次显示买一到买五、卖一到卖五的挂单手数。超过一万手时会缩写为“万”，封板一侧的第一档直接加“封”字，例如“买 封29万”。原 `--tray` 参数保留兼容，但现在同样打开行情条，不再显示难以辨认的托盘图标。

在行情条上点击右键，可以分别测试买入和卖出提醒。提醒使用连续上行/下行音阶，并让任务栏信号条快速闪烁。

任务栏上的五档手数始终可见。右键勾选“买卖五档”还可打开详细盘口小窗，按行情条的轮询频率显示卖五到卖一、买一到买五的价格与挂单数量（单位：手）。当买一锁在涨停价或卖一锁在跌停价时，任务栏和详细窗口都会显示涨停/跌停封单；详细窗口同时给出手数和折算股数。切换股票时盘口会同步切换，不保留上一只股票的数据。

右键菜单还可以调整候选公式。默认“平衡”反 T 公式使用两根 MACD 确认和 VWAP，并结合大盘、所选板块给卖点评级；“稳健”增加到三根确认并结合 EMA20，“灵敏”只确认单根交叉。所有模式都只允许先卖一成、再买回一成。

选择“历史回测（日内做T）”会使用新浪最近 1023 根真实分钟行情复用当前候选公式，并按交易日分页绘制历史买卖点。每天只配对反 T（先卖后买回），14:45 后不再开新候选，未完成的半笔 T 按收盘价强制完成并标记“收”，不跨日配对。报告分别统计一成仓位的价差收益和对总仓的收益贡献。

行情条第二行以红涨绿跌显示对应大盘指数与所选板块的当日涨跌幅；右键“对照板块”可在个股所属行业和概念之间切换。属于液冷概念的股票默认优先使用液冷板块。大盘和板块最近约五分钟的动量用于给卖出候选标记“强 / 中 / 弱”，但不会阻止已经卖出的一成仓位买回。

行情条同时读取隔夜美股（标普500、纳指、SOXX、VIX）、布伦特原油和美国银行 ETF（KBE）日涨跌，并与 A 股大盘、行业板块合成“强 / 震 / 弱”环境判断。Yahoo 不可用时自动回退腾讯行情源；数据时间和单项缺失不会阻塞个股行情。该判断用于提示当前环境和仓位倾向，不直接生成下单指令。

实时小图和历史回测统一采用 A 股配色：红色表示上涨和买入候选，绿色表示下跌和卖出候选。历史图在每个候选位置直接显示“买”或“卖”。

脚本采用东方财富免费分钟行情，只判断已经结束的分钟 K 线，按 `Ctrl+C` 停止。信号仅供观察，不会自动下单，也不构成投资建议。

### 投研命令

检查所有核心项目：

```powershell
python -m astock_hub doctor
```

分析雪球示例数据：

```powershell
python -m astock_hub forum analyze `
  --symbol 600519 `
  --source xueqiu `
  --input .\repos\xueqiu-crawler\data\stock_posts\SH600519.jsonl
```

默认报告输出到：

```text
docs/by-requirement/个人A股投研中枢/reports/<股票代码>-forum-sentiment.md
```

首次使用雪球采集时初始化并登录：

```powershell
python -m astock_hub forum xueqiu-setup
python -m astock_hub forum xueqiu-init
python -m astock_hub forum xueqiu-login
```

登录完成后，采集并分析一只股票：

```powershell
python -m astock_hub forum collect-xueqiu --symbol 600519.SH --pages 3
```

Cookie 和采集原始数据保存在 `var/xueqiu/`，不会写进上游仓库。
登录直接调用本机 Google Chrome，不额外下载 Playwright Chromium。

需要识别反讽和复杂语境时，可显式启用 OpenAI 兼容模型：

```powershell
$env:ASTOCK_LLM_API_KEY = "新生成的密钥"
$env:ASTOCK_LLM_BASE_URL = "https://你的服务/v1"
$env:ASTOCK_LLM_MODEL = "模型名称"
python -m astock_hub forum analyze `
  --symbol 600519 `
  --source xueqiu `
  --engine llm `
  --input .\repos\xueqiu-crawler\data\stock_posts\SH600519.jsonl
```

只有指定 `--engine llm` 时评论才会发送给模型服务。不要继续使用已经出现在旧脚本中的明文密钥。

## 上游分工

| 能力 | 首选 | 备选 |
|---|---|---|
| 行情与基础数据 | AKShare | adata、OpenAshare、easy_tdx |
| 论坛数据 | EastMoney_Crawler、xueqiu-crawler | xueqiu_spider_LQH_LZQ（仅参考） |
| 深度研究 | UZI-Skill | TradingAgents-astock |
| 选股 | tickflow-stock-panel | stock-scanner |
| 回测 | Qlib、RQAlpha | Hikyuu、QUANTAXIS |
| 盯盘 | PanWatch | 根工具脚本 |
| 实盘 | 默认禁用 | vn.py |

## 已知边界

- 当前情绪模型是可解释词典基线，不能可靠识别反讽、水军和复杂语境。
- 雪球 Cookie 不进入报告或 Git 仓库。
- 无许可证或仅限研究的项目不复制源码，只读取其导出文件。
- 所有分析结果仅作为研究因子，不构成投资建议。
