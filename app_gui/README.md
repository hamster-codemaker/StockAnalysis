# 成长股文档工具 — 图形界面

独立窗口，用子进程调用项目根目录的 `main.py`，**不导入** `stock_screener` 或其它业务模块。

与技术面分析界面（`python tech_analysis/gui.py`）是两套程序，不要混在一个窗口里。

## 用法

在项目根或任意目录：

```bash
python app_gui/main.py
```

或在本文件夹内：

```bash
python main.py
python app_gui.py
```

工作目录会被设为项目根，因此 `config.yaml` 与 `data/` 路径与命令行一致。

## 覆盖的功能

与当前 `main.py` 子命令对齐：

| 界面任务 | CLI |
| --- | --- |
| 筛选 | `python main.py screen` |
| 下载 | `python main.py download [--codes] [--screening-file] [--limit]` |
| 政策 | `python main.py policies [--industries] [--screening-file] [--max] [--limit]` |
| 更新政策 | `python main.py update-policies`（参数同上） |
| 财务分析 | `python main.py finance [--codes] [--screening-file] [--limit]` |
| 全流程 | `python main.py run [--limit]` |

可选：配置文件路径、详细日志 `-v`。开始后日志实时显示；停止会 terminate 子进程。可打开 `data/docs`、`data/policies`、`data/screening`。

长任务在后台线程里跑 subprocess，界面不会卡住。
