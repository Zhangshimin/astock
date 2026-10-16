# 悄盯一股 · Windows x64 独立插件

专注一只股票，悄悄融入任务栏。完整发布包附带 Python/Tk 和盯盘程序，安装者无需 Python、源码或目录配置。首次运行自动准备内置组件，不联网下载依赖；行情查询需要网络。

## 使用

输入 `悄盯一股`、`astock`、`stock` 或六位股票代码打开工作台。`盯盘 002837` 打开任务栏行情条，`图表 002837` 打开分时图表，`股票设置` 打开设置。

每个功能最多 5 个指令（含股票代码匹配）：工作台支持 `悄盯一股`、`astock`、`stock`、`股票` 和股票代码；行情条支持 `盯盘`、`任务栏股票`、`行情条`、`隐蔽盯盘` 和 `盯盘 股票代码`；图表支持 `股票图表`、`分时图`、`MACD` 和 `图表 股票代码`；设置支持 `悄盯一股设置`、`股票设置`、`盯盘设置`、`行情条设置`、`变色龙设置`。

默认全天显示在任务栏内部，每 3 秒按通知区边界自动挂靠。拖动后保存自由位置，双击恢复挂靠。变色龙模式依据周围背景主色调整底色，并排除行情条自身的红绿线条。

声音有独立总开关，可选择提示音/语音、买卖方向、观察与价量条件、提醒时段和发声间隔。静音不影响视觉提醒。高级配置折叠，保存后自动应用到新版窗口。

反 T 策略、历史回测、五档详情、实际卖出/买回记录仍在行情条右键菜单中。关闭插件面板不会停止盯盘，需关闭窗口或右键退出。

## 数据与升级

数据自动保存到 `%LOCALAPPDATA%/QiaodingYigu`：`engine/` 为按构建哈希隔离的组件缓存，`runtime/` 为窗口配置和状态，`logs/` 为日志，`intraday_trades.json` 为手动交易记录。

「用户数据」打开此目录，「运行状态」检查内置组件。插件安装目录只读也可运行。旧版 uTools 配置保留，项目/Python 路径不再使用。若旧配置含项目路径，会将旧交易记录复制到新目录，不覆盖已有记录、不删除原文件。升级后需重启旧窗口一次。

投研上游仓库与雪球采集仍是源码项目的开发功能，不是独立盯盘插件的运行依赖。

## 开发者构建

在 Windows x64 上使用 Python 3.13 x64：

```powershell
python -m venv var/plugin-build-env
var/plugin-build-env/Scripts/python.exe -m pip install -r tools/plugin-build-requirements.txt
var/plugin-build-env/Scripts/python.exe tools/build_utools.py
```

生成 `utools/runtime/`、`utools/runtime-manifest.json`、`dist/qiaoding-yigu-<版本>-<哈希>/` 和对应 ZIP。分发目录只包含插件、运行组件和许可证，不包含上游仓库、开发环境或个人记录。

在 uTools 开发者工具中导入发布目录里的 `plugin.json`，验收后打包为 `.upx` 发布。ZIP 是完整目录备份，不能改后缀当作 `.upx`。支持从插件归档释放组件到用户目录。

```powershell
python -m unittest discover -s tests -q
$env:ASTOCK_TEST_PACKAGE = (Resolve-Path utools).Path
node --test utools/tests/runner.test.js
python utools/tests/standalone_smoke.py dist/<实际发布目录>
```

Node 集成验证会清除 PATH 中的 Python 路径，检查独立组件并使用隔离数据目录。仅支持 Windows x64，行情源网络可用性独立于安装验证。
