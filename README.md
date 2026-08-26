# A股成长股分析套件

面向自己做功课的 A 股研究者：量化筛选成长股、自动攒齐法定披露材料、每日自选股基本面+技术面日报、点金术全市场高股息折价扫描。所有功能耦合为**一个统一 GUI / 一个 exe**。

详细文档：[产品说明](docs/产品说明.md) · [功能介绍](docs/功能介绍.md) · [安装包说明](docs/安装包说明.md) · [数据源与合规](docs/数据源与合规.md)

## 统一入口

```bash
python entry.py                     # 统一图形界面（自选股 / 成长股工具 / 技术面 / 定时任务）
python -m launcher                  # 同上
python entry.py --help              # 命令说明
python entry.py --daily-report      # 无窗口日报（先读 userdata/settings.yaml；daily_update=false 时只打日志）
python entry.py --watchlist-analyze # 一键分析全部自选股（技术面+基本面+综合分析，不跑点金术）
python entry.py --daemon            # 右下角托盘常驻；到点自动出日报
python main.py dianjin              # 点金术全市场扫描（CLI）
```

打包后：`dist\StockAnalysis\StockAnalysis.exe`（双击开 GUI，无黑框）。旧窗口仍可用：`python app_gui/main.py`、`python tech_analysis/gui.py`。

## 三套名单，永远分开

| 名单 | 路径 | 用途 |
| --- | --- | --- |
| 用户自选股 | `userdata/watchlist.txt` | 你自己盯的股票。download / finance / policies / 技术面 / 日报自选股章节 / 一键分析都读它 |
| 机械选股 | `data/screening/` | 成长股量化筛选结果，不要和自选股混写 |
| 点金术 | `data/dianjin/` | 全市场高股息折价名单。**不把自选股当筛选池**；命中且在自选股中的目录标 `_自选`，总表有「自选」列 |

自选股格式：每行 6 位代码，`#` 注释，空行忽略；可写 `代码,名称`（有名称时日报不再出网查简称）。

## 自选股一键分析

界面「自选股」页点 **分析全部自选股**，或命令行 `--watchlist-analyze`。对名单里每只股票做技术面 + 基本面 + `综合分析.md`（两侧要点对照），写入：

- `data/watchlist/`（固定位置，随时可看）
- 当日 `日报集/<日期>/自选股/`（若当天日报目录已存在）

自选股为空时写「今日无符合」，不报错。此入口**不跑**点金术。

## 开机自启与每日日报

单一真相：`userdata/settings.yaml`（每次运行都重新读取；GUI「定时任务」页勾选会立刻写回）。

- `autostart`：登录后托盘静默运行（计划任务 `StockAnalysisAutostart`，参数 `--daemon`）
- `daily_update`：每日自动生成日报（任务 `StockAnalysisDaily`）；`false` 时 `--daily-report` 只打日志
- `daily_time`：默认 `16:00`（A股 15:00 收盘后一小时）
- `report_dir`：日报根目录；空则默认「桌面/日报集」，其下按日期建子目录

### 日报目录结构（桌面/日报集/YYYYMMDD/）

```
总览.md                            # 自选股 / 点金术 分节目录
点金术技术信号汇总.md|.csv          # 点金术命中股技术信号一页看完
点金术extra技术信号汇总.md|.csv
自选股/                            # 整份自选股结果（与点金术并列，互不混放）
├── 总表.md / 总表.csv
├── 技术面汇总.md / 基本面汇总.md
└── 个股/<代码>_<名称>/
    ├── 综合分析.md                # 基本面 + 技术面要点对照
    ├── 技术面/  分析报告.md / 技术分析.png / signals.csv / 日线指标.csv
    └── 基本面/  财务分析.md / 财务数据.csv / 五张趋势图（含单季拆分）
点金术/                            # 全市场筛选，独立文件夹
├── 总表.md（含最近技术指标列） / 点金术.md|.csv / 技术信号汇总.md|.csv
└── 个股/<代码>_<名称>[_自选]/
点金术extra/                       # 加严子集，独立文件夹与个股副本
```

副本另存 `data/daily_reports/<日期>/`。日报每天自动分析全部自选股（技术面+基本面+综合分析），再跑点金术全市场。

## 点金术规则（纯量化）

- 全部 A 股，剔除 ST/*ST/退市，默认不含北交所
- **股息率 > 3%**：口径为**同花顺「股息率TTM」**。全市场快照预填东财数据中心 **DV_TTM**（与原 f133 同列；锚点：思维列控 2.43、中油资本 1.50）；对已过市盈率关的股票再用同花顺 F10 分红表（`basic.10jqka.com.cn/new/{code}/bonus.html`）覆盖：只计已实施派现、剔除股利支付率 >200% 的特别分红。**不用 f183**（资金流占比）、**不用**同花顺 526792（振幅）、**不用**腾讯字段 64（年度/含特别分红口径）。跨日核对时用 DPS/现价，不要直接比百分数。
- 三档市盈率（动态 f9 / 静态 f114 / TTM f115）均 **0 < PE < 20**，缺数、非数字、负值一律不通过
- 收盘 **< MA120 × 88%**；extra 为加严子集：股息 > 4% 且收盘 < MA120 × 82%，同一套 PE
- MA120 用腾讯前复权日线自算（公开接口没有现成 MA120 字段），只对股息+PE 幸存者拉 K 线
- 名单为空写「今日无符合」，不中断日报

`--hist-limit` / `--limit` / `--screen-only` 仅测试用，日报入口不会带这些参数。

## 技术面口径

最近 **120 个交易日** K 线（预热约 250 根），叠加 MA8 / MA24 / MA120；分图 BOLL(20,2)、MACD(12,26,9)、RSI 通达信 RSI1/2/3 = 6/12/24。信号带日期和数值；点金术个股高亮近 5 个交易日信号。

**指标日 = 该股日线最后一根 K 的日期。** 当日 K 线缓存（`data/cache/kline/`）在收盘后（≥15:15）发现最后一根仍是昨天、且缓存写于当日收盘前时会自动重拉，保证收盘后跑出的指标日统一为当天；停牌股保持在其最后交易日。周末沿用周五、盘中允许昨收，都不算过期。

## 成长股筛选规则

程序只实现三条可量化标准（阈值在 `config.yaml` 可调）：

| 标准 | 实现方式 | 默认阈值 |
| --- | --- | --- |
| ① 业绩增速 | 最新报告期营收同比与净利润同比均不低于阈值（东方财富业绩报表） | 均 > 40% |
| ④ 小盘股 | 总市值低于上限，剔除ST/*ST/退市整理股（默认不含北交所） | < 200亿元 |
| ⑤ 机构持仓适中 | 持有机构家数不少于下限，且机构持股占流通股比例不超上限 | ≥ 1家 且 ≤ 20% |

估值高低（②）、增长成色（③）、产业趋势（⑥）由使用者阅读下载的材料自行判断，程序不做处理。

## 数据源与合规

| 内容 | 来源 | 说明 |
| --- | --- | --- |
| 招股说明书、定期财报 PDF | [巨潮资讯网](https://www.cninfo.com.cn) | 证监会指定信息披露平台，公开接口直链下载 |
| 个股/行业研报 | [东方财富研报中心](https://data.eastmoney.com/report/) | 公开列表接口；PDF 走普通 HTTPS |
| 行业政策文件 | [国务院政策文件库](https://www.gov.cn/zhengce/zhengceku/) | 公开搜索接口 |
| 历年财务数据 | akshare 封装的东财 F10 公开财报 | 与披露定期报告同一口径，不解析扫描版 PDF |
| K 线 / 日线 / 行情快照 | 腾讯 | 不再请求东财 push2his / 新浪；顺序在 `userdata/source_preference.yaml` |
| 点金术估值快照 | 东财数据中心 `RPT_VALUEANALYSIS_DET` + 腾讯下标 52 | 静态/TTM PE 与预填股息；动态 PE 由腾讯补齐 |
| 点金术筛选股息率 | 同花顺 F10 分红表（股息率TTM 口径） | 只对过了 PE 关的股票拉页覆盖 |
| 证券简称 | 本地 → 腾讯 | 不再请求东财个股资料 / 新浪 |

精确 URL、robots 结论与「DPS/现价」验算见 [数据源与合规](docs/数据源与合规.md)。

合规措施：仅访问公开披露内容；文档/政策下载默认间隔 1.5 秒，行情分页 0.12～0.3 秒；不登录、不付费、不伪造 TLS、不请求 `push2`。请仅用于个人研究，遵守各数据源使用条款。

## 安装与使用

需要 Python 3.10+：

```bash
pip install -r requirements.txt
```

```bash
# 筛选：输出 data/screening/成长股筛选_YYYYMMDD.csv|.md
python main.py screen

# 下载（建议先 --limit 试跑；重复运行增量跳过，记录在 data/manifest.json）
python main.py download --limit 3
python main.py download --codes 300750,688111
python main.py download --watchlist userdata/watchlist.txt

# 政策库（增量：只入库总清单里没有的新文件）
python main.py policies --industries 集成电路,人工智能 --max 30
python main.py update-policies

# 财务提取与出图（本地已最新则不出网）
python main.py finance --codes 688308
python main.py finance --watchlist userdata/watchlist.txt

# 全流程 / 点金术 / 技术面
python main.py run [--limit N]
python main.py dianjin
python entry.py tech --watchlist userdata/watchlist.txt
```

**体量提示**：财报默认全量下载（上市以来所有定期报告），入选股票较多时可达数 GB。建议先 `--limit` 小批量试跑，或在 `config.yaml` 缩小 `financial_reports.types`。

## 输出结构（data/）

```
data/
├── screening/成长股筛选_YYYYMMDD.csv|.md    # 机械筛选清单与报告
├── dianjin/YYYYMMDD/点金术|点金术extra/     # 点金术当日结果（总表、名单、技术信号汇总、个股/）
├── watchlist/                               # 一键分析全部自选股的固定输出（结构同日报自选股/）
├── daily_reports/YYYYMMDD/                  # 日报副本（桌面日报集的完整镜像）
├── tech_analysis/                           # 技术面独立输出（信号汇总 + 个股）
├── docs/<代码>_<名称>/                      # 招股说明书/ 财报/ 研报/ 政策文件索引.md / 财务分析/
├── policies/文件库/ + 政策总清单.csv        # 政策中央库（原文链接全局去重）
├── cache/                                   # kline 日线缓存、简称缓存、巨潮映射等
└── manifest.json                            # 增量下载记录
```

## 配置（config.yaml）

- `screening.growth.revenue_yoy_min` / `profit_yoy_min`：增速阈值（%）
- `screening.market_cap_max_yi`：总市值上限（亿元）
- `screening.institution.min_institutions` / `max_float_ratio_pct`：机构门槛
- `dianjin.*`：点金术股息/PE/MA 门槛（默认 3% / 20 / 0.88；extra 4% / 0.82）
- `downloads.financial_reports.types`：财报类型（默认全量）
- `downloads.financial_analysis.enabled`：download/run 后自动财务分析（默认 true）
- `network.rate_limit_seconds`：请求限速间隔
- `network.use_system_proxy`：默认 false（国内站点直连）

技术面参数在 `tech_analysis/config.yaml`（均线/BOLL/MACD/RSI 参数、回看 120 交易日、预热根数）。

## 开发：测试、打包、备份

```bash
python -m unittest discover -s tests    # 全部单元测试
python pack.py                          # PyInstaller onedir + Inno 安装包 → dist/installer/StockAnalysisSetup.exe
```

源码快照备份在 `backup/<时间戳>/`（只含代码与文档，不含 data/ 与 dist/）。

## 实现说明与已知限制

- **行情源顺序**：K 线走腾讯；全 A 排行腾讯优先、数据中心备用。持久化在 `userdata/source_preference.yaml`；不再请求东财 push2 / push2his，也不再请求新浪。
- **机构持仓**：东方财富基金持仓（只统计公募家数，占比按流通股本估算）。按季度披露有滞后，属数据源固有特性。
- **研报 PDF**：`pdf.dfcfw.com` 走普通 HTTPS；CDN 可能拒绝部分客户端，失败则跳过，不伪造 TLS。
- **招股说明书**：取最新正式版（排除摘要/意向书）；2000 年前上市的公司可能未电子化披露。
- **财务分析**：指标含营收、归母/扣非净利、毛利率、净利率、ROE、经营现金流、资产负债率、流动比率、EPS。同比=上年同期；环比=相邻报告期（累计口径）。另有单季拆分（Q2=半年报−一季报……）。低基数极端百分比在图上截断标注，CSV 保留原值。
- **文件校验**：所有下载校验 PDF 文件头，非 PDF 内容丢弃并计为失败。
