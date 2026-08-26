# 用户数据（自选股与开关）

本目录是**用户输入**，与机械选股结果 `data/screening/`、点金术结果 `data/dianjin/` 完全独立。

## 自选股 `watchlist.txt`

- 每行一个 6 位 A 股代码；`#` 开头为注释，空行忽略
- 也可写成 `代码,名称` 或 `代码 名称`
- 成长股工具的 download / finance / policies 可用 `--watchlist userdata/watchlist.txt`
- 技术面默认也读这一份，不要再维护 `tech_analysis/watchlist.txt`（那份只作初次复制的种子）
- 点金术**不读**这份名单当筛选池；若命中股也在自选股中，个股目录加 `_自选` 后缀，总表「自选」列打标。自选股为空时不标注、不报错；日报自选股章节写「今日无符合」，点金术仍全市场扫描

## 开关 `settings.yaml`

| 字段 | 含义 |
| --- | --- |
| `autostart` | 开机自启（登录时启动 `--daemon` 托盘，不弹主窗口） |
| `daily_update` | 每日更新：到点生成自选股详细基本面+技术面日报，并附点金术全市场结果 |
| `daily_time` | 日报时刻，默认 `16:00` |
| `report_dir` | 日报根目录；空则默认「桌面/日报集」 |
| `earnings_season_months` | 财报季月份；仅这些月份会联网刷新财务，否则用本地缓存 |

改 YAML 后：下次打开 GUI 会按文件刷新勾选；`--daily-report` 每次启动都重新读文件。  
`daily_update: false` 时 `--daily-report` 只打日志并退出，**不写桌面**。

## 源优先级 `source_preference.yaml`

程序自动维护，**不是密钥**。新安装没有此文件时，默认：

- K 线 / 日线：`tencent`
- 成长股全 A 快照：`tencent` → `datacenter`

旧文件中的 `sina`、`eastmoney`、`clist` 会被忽略。某主源对一次请求全部失败、备用成功时，会对调并写回。不要把开发机上的这份文件打进安装包。

也可在统一 GUI「定时任务」页勾选，会立刻写回本文件并同步 Windows 计划任务：

- `StockAnalysisAutostart`（登录启动）
- `StockAnalysisDaily`（每日定时日报）
