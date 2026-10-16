# 悄盯一股 · AStock

专注一只股票，悄悄融入任务栏。变色龙底色随背景适配，支持拖拽、分时行情、MACD、五档盘口与可选声音提醒，小巧低调，随时看一眼。

## uTools 插件

Windows x64 独立版自带运行组件，安装后输入 `悄盯一股` / `astock` / `stock` 即可使用，
无需 Python、项目源码或目录配置。也可输入 `盯盘 002837` / `图表 002837` 启动窗口。
开发者通过 `tools/build_utools.py` 生成完整发布目录，再在 uTools 开发者工具中打包发布。

安装、配置和分发说明：[utools/README.md](utools/README.md)。

## 源码开发运行（插件安装者无需执行）

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
