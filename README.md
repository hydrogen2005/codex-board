# Codex 多电脑 token 统计

离线读取每台电脑的 Codex 会话日志，导出电脑名、日期和 token 用量，再汇总电脑占比。Windows、macOS、Linux 均可运行，要求 Python 3.9 或更新版本；只使用标准库，不安装依赖、不访问外部网络。

## 中文可视化界面（推荐）

### Windows 免安装版

直接运行 `Codex用量看板.exe` 即可，内置 Python 运行环境及全部网页资源。只需把这个 EXE 复制到其他 Windows 64 位电脑，无需复制源码文件夹，也无需另装 Python。

启动后自动打开本机浏览器。请保留运行窗口；关闭该窗口或按 Ctrl+C 退出。默认使用 8765 端口，占用时自动选择空闲端口。首次启动需要解包，可能稍等片刻。

每台电脑运行 EXE 后点击「导出本机数据」，在汇总电脑运行同一个 EXE 并点击「导入其他电脑」。导出文件与之前的 Python 脚本版本兼容。程序不含任何电脑的实际日志或登录凭据。

### Python 源码版

Windows 双击 `start-ui.cmd`，或在本文件夹打开终端运行：

```powershell
python codex_usage_ui.py
```

macOS / Linux 可用 `python3 codex_usage_ui.py`。程序会打开本机浏览器；保持终端窗口打开，按 Ctrl+C 停止。默认访问 `http://127.0.0.1:8765`，占用时自动选择空闲端口，只监听本机，不连接外部服务。也可用 `--port 8766` 指定端口；不自动打开浏览器用 `--no-browser`。

界面全中文，启动后自动读取本机日志，默认展示近 30 天：

1. 查看总用量、输入/输出 token、每日趋势和电脑占比。
2. 点击「导出本机数据」，得到 `.usage.jsonl`，复制到用于汇总的电脑。
3. 在汇总电脑点击「导入其他电脑」或将统计文件拖入页面，支持多选及自动去重。
4. 按今天、近 7 天、近 30 天、全部或自定义日期筛选；也能只查看某台电脑。
5. 点击「下载占比」或「下载明细」保存中文 CSV，可用 Excel 打开。

「本机设置」可修改唯一电脑名与 Codex 数据目录。数据只保存在当前页面内，刷新或关闭页面后需要重新导入。读取本机始终导出完整可用历史，页面的日期筛选只影响展示和 CSV 报表。诊断面板解释日志缺口、计数重置与归属不明。

界面与命令行共享日志解析、去重和汇总逻辑。所有设备使用固定北京时间分日。超长日期范围的趋势图按多天合计绘制，提示中会显示聚合粒度；每日明细仍保留原始每日值。

## 每台电脑导出

将本文件夹复制到每台电脑，在这个文件夹打开终端：

```powershell
python codex_token_usage.py export
```

macOS / Linux 如无 `python` 命令，改用 `python3`。Windows 如无 `python`，可改用 `py -3`。

默认使用系统电脑名，读取环境变量 `CODEX_HOME` 或用户目录下的 `.codex`，扫描其中 `sessions` 和 `archived_sessions`，结果放在当前目录的 `exports`。只读原始日志。

电脑名必须能区分设备。如果几台电脑重名，或你想固定一个统计名称：

```powershell
python codex_token_usage.py export --machine OFFICE-PC
python codex_token_usage.py export --machine HOME-PC
```

上面的两条命令分别在对应电脑上执行。不要在同一台电脑切换名称来重复导出同一批日志。

只导出指定日期（包含起止两天）：

```powershell
python codex_token_usage.py export --from 2026-11-01 --to 2026-11-30
```

指定 Codex 数据目录或额外日志根目录：

```powershell
python codex_token_usage.py export --codex-home "D:\CodexData"
python codex_token_usage.py export --sessions-dir "D:\CodexData\sessions" --sessions-dir "D:\CodexData\archived_sessions"
```

导出文件：

| 文件 | 用途 |
| --- | --- |
| `电脑名-标识.usage.jsonl` | 供汇总使用的去重明细；只需要收集这个文件 |
| `电脑名-标识.daily.csv` | 每日 token 用量，Excel 可直接打开 |
| `电脑名-标识.machines.csv` | 本机期间总量 |
| `电脑名-标识.summary.json` | 汇总与异常计数、最多 50 条定位信息 |

同一输出目录再次导出会覆盖本机上一次结果。若要保留每日快照，使用不同输出目录，例如 `--out exports/2026-11-02`。日志增长后重复导出不影响汇总去重。

## 集中汇总

把每台电脑导出的 `.usage.jsonl` 复制到同一个文件夹，例如 `collected`，然后在任意一台电脑运行：

```powershell
python codex_token_usage.py merge collected --out report
```

也可以传多个文件或目录；目录会递归查找 `.usage.jsonl`：

```powershell
python codex_token_usage.py merge "D:\office-exports" "D:\home-exports" --from 2026-11-01 --to 2026-11-30 --out report
```

生成：

| 文件 | 内容 |
| --- | --- |
| `report/machines.csv` | 每台电脑的期间总量、`share_percent`（期间占比） |
| `report/daily.csv` | 每台电脑每天的用量、`day_share_percent`（当天占比） |
| `report/summary.json` | 总量、已导出电脑列表、日期口径、去重及异常信息 |

`share_percent = 该电脑期间 total_tokens / 所有采集记录去重后的期间 total_tokens × 100`。

没有用量的电脑可在 `summary.json` 的 `exported_machines` 中看到；CSV 只列出有实际用量的行。占比保留六位小数，舍入后总和可能与 100 有细微差异。

## 日期和 token 口径

- 默认所有电脑都按固定 UTC+08:00（北京时间）分日，不依赖电脑系统时区。若要改口径，导出和汇总均传 `--utc-offset +00:00`。负偏移写成 `--utc-offset=-05:00`。固定偏移不自动处理夏令时。
- `total_tokens = input_tokens + output_tokens`。缓存输入和推理输出是其中的子项，不再次加到总量。`cache_write_input_tokens` 只按日志原值单独展示，也不额外叠加。
- 累计计数相邻做差；重复计数不累加。日志不完整或计数重置时，有 `last_token_usage` 则只计可确认的最近一次用量并记录诊断，无法定位日期的历史差额不补算。缺少累计计数的事件跳过并记录诊断。
- 若首条计数没有单次值，只能将首个累计值记在该事件日期；会标记 `initial_cumulative_assigned_to_first_timestamp`，该日期归属可能不精确。运行中的未完成尾行会跳过，下次完整导出再计。
- 按事件时间记录用量，跨午夜的请求记在用量事件报告的日期。日期筛选前仍扫描历史日志以正确计算差额。
- 使用日志中的事件标识去重，重复导出、活动/归档目录中的同一记录不会重复计入。冲突的同一事件计数会报错，要求重新导出。
- 同一事件同时出现在不同电脑的导出中，无法证明原始执行设备，归入 `__SHARED_UNATTRIBUTED__`（设备归属不明），只计一次，并参与分母和占比。不会把它任意分给一台电脑。

## 统计边界

这是**本地可观察 token 用量**，不是订阅额度消耗比例、账单金额或官方全账号总量。未保留日志、云端执行、未收集设备以及仅存在于其他远程主机的记录不会自动补齐。需在实际执行 Codex 的主机导出。

本地日志通常不提供可信的执行电脑标识，因此默认把记录归到导出电脑。不要把整份 `.codex` 数据目录在设备之间同步再依赖它判断执行设备：只有重复记录同时收集到时才能识别为“归属不明”；只收集到一份复制记录时无法识别。改变 session ID 的历史复制/分叉也可能无法去重。

同一个数据目录若曾切换多个账号，历史会一起统计；脚本不读取登录凭据，也不按当前登录账号筛选。导出和汇总需使用相同日期范围；汇总筛选不能恢复导出时已经排除的记录。

导出不保存对话正文、代码、提示词或登录凭据。为去重保留会话 ID、事件哈希和 UTC 时间；诊断可能包含本机日志路径。只收集 `.usage.jsonl` 即可，无需复制原始会话日志。

## 验证

```powershell
python -m unittest -v test_codex_token_usage.py test_codex_usage_ui.py
```

测试覆盖累计去重、缓存/推理子项、计数重置、日志缺口、损坏尾行、归档扫描、日期边界、重复导出、跨电脑归属不明、继续会话和冲突导出。

## 重新构建 Windows EXE

仅开发者重新构建时需要本机 Python 和 PyInstaller。用户运行已生成的 EXE 不需要这些工具。

```powershell
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
python verify_windows_exe.py .\dist\Codex用量看板.exe
```

构建产物在 `dist`，只打包程序、标准库运行环境和 `ui` 资源，不会打包 `exports`、原始日志、界面截图或本机配置。验证脚本在无 Python PATH、无源码的临时工作目录中启动 EXE，检查资源、扫描导出、导入去重和占比计算。
