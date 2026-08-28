# astock

个人 A 股投研与 Windows 盘中盯盘工具。

## 快速开始

```powershell
python -m pip install pystray pillow pandas
python .\tools\intraday_macd_alert.py 002837 --bar
```

使用说明海报：[docs/usage-poster.md](docs/usage-poster.md)

常用入口：

```powershell
# Windows 主窗口：价格、成交量、MACD
pythonw .\tools\intraday_macd_alert.py 002837 --gui

# Windows 任务栏紧凑行情条
pythonw .\tools\intraday_macd_alert.py 002837 --bar

# 检查已登记的上游项目
python -m astock_hub doctor
```

项目说明：[docs/by-requirement/个人A股投研中枢/README.md](docs/by-requirement/个人A股投研中枢/README.md)

> 仅供研究和观察，不自动下单，也不构成投资建议。
