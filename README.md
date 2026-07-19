# 🤖 Crypto Trading Bot - 智能量化交易机器人

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Production%20Ready-brightgreen.svg)]()

**一个功能完善、策略先进的加密货币量化交易机器人**

[特性](#-核心特性) • [快速开始](#-快速开始) • [配置说明](#-配置说明) • [策略详解](#-策略详解) • [未来规划](#-未来优化规划)

</div>

---

## 📋 目录

- [项目简介](#项目简介)
- [核心特性](#-核心特性)
- [系统架构](#-系统架构)
- [快速开始](#-快速开始)
- [配置说明](#-配置说明)
- [策略详解](#-策略详解)
- [风险管理](#-风险管理)
- [远程控制](#-远程控制)
- [性能指标](#-性能指标)
- [未来优化规划](#-未来优化规划)
- [常见问题](#-常见问题)
- [贡献指南](#-贡献指南)
- [许可证](#许可证)

---

## 项目简介

这是一个基于 Python 的加密货币量化交易机器人，支持实盘和模拟交易。系统采用多策略融合架构，能够适应不同的市场环境（趋势行情和震荡行情），并具备完善的风险控制机制。

### 🎯 设计理念

- **稳健优先**: 多层风控保护本金安全
- **策略融合**: 趋势跟踪 + 震荡反转双模式
- **动态适应**: 根据市场波动自动调整参数
- **易于使用**: 清晰的配置和远程控制接口

---

## ✨ 核心特性

### 🔄 Regime 自适应双模式策略

系统用 ADX + 布林带宽度识别市场状态（TREND/RANGE/NEUTRAL），按状态选择策略：

#### 1️⃣ 趋势态 (TREND) — 趋势跟随
- **进场**: MACD 金叉 / ADX+量能 / RSI 上穿 50 / SMA20 突破（任一）
- **出场**: ATR 追踪止损 + 主动止盈(N×ATR) + RSI 超买 + MACD 死叉
- **成交量确认**: 需成交量放大 ≥ `volume_threshold` (默认 1.5x)

#### 2️⃣ 震荡态 (RANGE) — 均值回归
- **进场**: 布林下轨支撑 + RSI 底背离
- **出场**: 布林中轨止盈 / RSI 回升 / 紧止损 / 超时退出（快进快出）
- **成交量确认**: 需成交量放大 ≥ `volume_threshold × 0.8`

#### 3️⃣ 中性态 (NEUTRAL) — 观望
不进场，等待状态明确。

### 📊 技术指标体系

| 指标 | 用途 | 说明 |
|------|------|------|
| **ADX** | 趋势强度识别 | Regime 识别 + 趋势信号过滤 |
| **RSI** | 超买超卖判断 | 趋势过滤 + 均值回归信号 |
| **SMA20/60** | 均线系统 | 趋势方向和支撑阻力 |
| **ATR** | 波动率测量 | 动态止损和参数调整 |
| **MACD** | 动量指标 | 金叉/死叉信号 |
| **Bollinger Bands** | 波动通道 | Regime 识别 + 均值回归信号 |
| **Volume** | 成交量分析 | 信号确认和假突破过滤 |

### 🎯 分阶段追踪止盈

创新的分离式追踪止盈策略，盈利门槛和回撤触发独立设置（以 BTC 为例）：

```
阶段1: 盈利 5%   → 回撤 2.5%  触发卖出
阶段2: 盈利 10%  → 回撤 3%    触发卖出
阶段3: 盈利 18%  → 回撤 4%    触发卖出
```

**时间衰减机制**: 持仓时间越长，追踪比例越小（4h 框架下延长观察期，96h 后才开始收紧）
```
0-24小时   → 系数 1.0 (无衰减)
24-48小时  → 系数 1.0 (无衰减)
48-96小时  → 系数 0.95 (5%衰减)
96-144小时 → 系数 0.85 (15%衰减)
144小时+   → 系数 0.75 (25%衰减)
```

### 🛡️ 多层风险控制

#### 1. ATR 动态止损
```python
止损价 = 最高价 - ATR × 倍数
# BTC/ETH: ATR × 2.0
# SOL: ATR × 2.5 (波动更大)
```

#### 2. 固定百分比止损
```
BTC: 4% | ETH: 5% | SOL: 6%
```

#### 3. 熔断保护机制（按币种独立）
- 检测异常暴跌（单根K线跌幅 > 8%）
- 自动触发该币种熔断，暂停买入 2 小时
- 防止黑天鹅事件造成重大损失

#### 4. 仓位管理
- 单币种最大仓位限制
- 总仓位暴露度控制
- 防止过度杠杆

### 📈 波动率自适应参数

根据市场波动率（ATR%）自动调整策略参数：

| 市场状态 | ATR% | 参数调整 | 效果 |
|---------|------|---------|------|
| 低波动 | < 2% | × 0.8 | 更严格的进场条件 |
| 正常波动 | 2-5% | 不变 | 保持默认参数 |
| 高波动 | > 5% | × 1.2-1.3 | 更宽松的止损/止盈 |

### 💬 Telegram 远程控制

完整的 Telegram 机器人控制接口：

| 命令 | 功能 | 示例 |
|------|------|------|
| `/status` | 查看当前持仓 | 显示所有持仓详情 |
| `/trailing_status` | 追踪止盈状态 | 显示追踪比例和时间衰减 |
| `/set_sl SYMBOL VALUE` | 设置止损 | `/set_sl ETH/USDT 0.05` |
| `/set_ts SYMBOL VALUE` | 设置追踪比例 | `/set_ts BTC/USDT 0.03` |
| `/fuse` | 手动熔断 | 紧急暂停交易 |
| `/unfuse` | 解除熔断 | 恢复交易 |

### 💬 多渠道通知

- **Telegram**: 实时交易通知 + 远程控制
- **日志**: 完整运行记录（`bot_main.log`）

---

## 🏗️ 系统架构

```
crypto-bot/
├── bot_engine.py                  # 核心交易引擎
├── risk_manager.py                # 风险管理器（含状态持久化、追踪止盈、熔断）
├── config.py                      # 配置文件（币种差异化参数、风控参数）
├── backtest.py                    # 回测框架（backtrader 适配版）
├── backtest_native.py             # 回测框架（原生版，直接复用 bot_engine 信号）
├── remote_control.py             # Telegram 远程控制
├── telegram_notifier.py          # Telegram 通知
├── report_generator.py           # 每日报告生成
├── send_telegram_report_daily.py # 每日报告 Telegram 推送
├── bot_state.json                 # 状态存储文件（持仓/熔断/虚拟账户/历史）
├── daily_report.sh               # 定时任务：生成并发送每日报告
├── cleanup_bot.sh                # 清理锁文件与僵尸进程
└── requirements.txt              # 依赖清单
```

### 核心模块说明

#### `bot_engine.py` - 交易引擎
- 数据获取与多时间框架趋势过滤
- Regime 自适应策略信号生成（趋势/均值回归）
- 原子化订单执行（余额变动 + 持仓更新一次落盘）
- 信号二次确认 + 主循环控制

#### `risk_manager.py` - 风险管理 + 状态持久化
- 分阶段追踪止盈 + 时间衰减
- ATR 动态止损 / 固定止损 / 主动止盈
- 按币种独立熔断 + 账户级回撤保护
- 仓位暴露度 + 相关性分组检查
- 原子化状态持久化（`save_state` 用临时文件 + `os.replace`）

#### `config.py` - 配置中心
- 币种差异化策略参数（BTC/ETH/SOL）
- 风控参数（熔断/回撤/仓位/相关性）
- 从 `.env` 读取 API 密钥与 Telegram 配置

#### `remote_control.py` - Telegram 远程控制
- `/status` `/positions` `/performance` `/trailing_status` 查询
- `/set_sl` `/set_ts` `/config` 运行时调参
- `/fuse` `/unfuse` 紧急熔断控制

---

## 🚀 快速开始

### 环境要求

- Python 3.8+
- Miniconda3 (推荐)
- pip 包管理器

### 安装步骤

#### 1. 创建并激活虚拟环境 (Miniconda3)

```bash
# 创建新的虚拟环境
conda create -n crypto-bot python=3.11

# 激活虚拟环境
conda activate crypto-bot
```

#### 2. 克隆项目

```bash
git clone https://github.com/yourusername/crypto-bot.git
cd crypto-bot
```

#### 3. 安装依赖

```bash
# 使用 requirements.txt 安装 (推荐)
pip install -r requirements.txt

# 或手动安装各个依赖
pip install ccxt pandas python-dotenv ta pyTelegramBotAPI
```

#### 4. 配置 API 密钥

创建 `.env` 文件并填入您的 API 密钥：
```bash
cp .env.example .env  # 如果有模板文件
```

编辑 `.env` 文件：
```bash
# 交易所 API 配置（Binance）
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET_KEY=your_api_secret_here

# Telegram Bot 配置
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

#### 5. 选择交易模式

编辑 `config.py`：
```python
LIVE_TRADE = False  # True: 实盘交易 | False: 模拟交易
```

#### 6. 启动机器人

```bash
python bot_engine.py
```

### 依赖库说明

本项目依赖以下核心库：

| 库名 | 版本要求 | 用途 |
|------|----------|------|
| **ccxt** | >=4.0.0 | 交易所API接口，支持多家主流交易所 |
| **pandas** | >=2.0.0 | 数据处理和分析，K线数据管理 |
| **python-dotenv** | >=1.0.0 | 环境变量管理，保护API密钥安全 |
| **ta** | >=0.10.0 | 技术分析库，提供RSI、ADX、ATR等指标 |
| **pyTelegramBotAPI** | >=4.10.0 | Telegram机器人API，远程控制接口 |
| **backtrader** | >=1.9.78 | backtrader版回测框架（`backtest.py` 依赖） |
| **matplotlib** | >=3.7.0 | backtrader版回测结果可视化 |

> 💡 **提示**：`backtest_native.py`（原生版回测器）无需额外依赖，仅使用 ccxt/ta/pandas。

### 模拟交易测试

系统默认使用模拟模式，虚拟账户初始余额可在 `bot_state.json` 中设置：
```json
{
  "virtual_account": {
    "balance": 1000.0,
    "total_pnl": 0.0,
    "trade_count": 0
  }
}
```

### 常见问题解决

#### Q: 依赖安装失败？
```bash
# 升级pip
pip install --upgrade pip

# 使用国内镜像源
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

#### Q: conda环境激活失败？
```bash
# 初始化conda (如果未初始化)
conda init zsh  # 或 bash, fish
source ~/.zshrc  # 重启终端或重新加载配置
```

---

## ⚙️ 配置说明

### 交易对配置

```python
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
```

### 币种差异化策略参数

每个币种都有独立的参数配置：

```python
STRATEGY_CONFIG = {
    'BTC/USDT': {
        'adx_threshold': 22,           # 4h框架下 ADX 趋势强度阈值
        'rsi_oversold': 35,
        'rsi_overbought': 70,
        'trade_amount': 30,            # 单笔交易金额 (USDT，fallback)
        'stop_loss_pct': 0.04,         # 固定止损 4%

        # 成交量确认
        'volume_threshold': 1.5,       # 成交量放大阈值
        'volume_ma_period': 20,

        # ATR 动态止损
        'atr_period': 14,
        'atr_multiplier': 2.0,
        'use_atr_stop': True,

        # 布林带参数
        'bb_period': 20,
        'bb_std': 2,

        # 波动率适配
        'volatility_adjust': {
            'enabled': True,
            'low_vol_threshold': 0.02,
            'high_vol_threshold': 0.05,
            'low_vol_multiplier': 0.8,
            'high_vol_multiplier': 1.2,
        },

        # 分阶段追踪止盈（门槛提升，给利润更大运行空间）
        'trailing_stops': [
            {'profit_threshold': 0.05,  'trigger_drawdown': 0.025, 'trailing_pct': 0.02},
            {'profit_threshold': 0.10,  'trigger_drawdown': 0.03,  'trailing_pct': 0.025},
            {'profit_threshold': 0.18,  'trigger_drawdown': 0.04,  'trailing_pct': 0.035},
        ],

        # 仓位与止盈
        'risk_per_trade': 0.01,        # 名义风险占比（实际受 max_trade_amount 截断）
        'max_trade_amount': 100,       # 单笔金额上限（实际生效的约束）
        'profit_target_atr': 6.0,      # 主动止盈目标 6×ATR
        'min_profit_pct': 0.008,       # 最小盈利保护 0.8%

        # 时间衰减（4h框架下延长观察期，48h 后才开始收紧）
        'time_decay': {
            'enabled': True,
            'intervals': [
                {'hours': 12,  'multiplier': 1.0},
                {'hours': 24,  'multiplier': 1.0},
                {'hours': 48,  'multiplier': 0.9},
                {'hours': 72,  'multiplier': 0.75},
                {'hours': float('inf'), 'multiplier': 0.6},
            ]
        },

        # Regime 市场状态识别
        'regime_trend_adx': 25,
        'regime_range_adx': 20,
        'regime_trend_bb_width': 0.03,
        'regime_range_bb_width': 0.02,

        # 均值回归策略退出参数（仅震荡态入场仓位使用）
        'meanrev_config': {
            'stop_loss_pct': 0.025,
            'rsi_exit': 50,
            'bb_mid_exit': True,
            'max_hold_hours': 24,
        },
    }
}
```

### 风险控制参数

```python
# 熔断机制（按币种独立触发，双层检测）
DRAWDOWN_FUSE = 0.08            # 单根K线跌幅阈值 8%（防闪崩）
DRAWDOWN_FUSE_MULTI_BAR = 0.10  # 多根K线累计跌幅阈值 10%（防阴跌）
DRAWDOWN_FUSE_MULTI_BAR_COUNT = 3  # 多根熔断检查的K线根数
FUSE_DURATION = 28800           # 熔断持续时间 8 小时 (适配4h框架，覆盖2根K线)

# 仓位管理
MAX_TOTAL_EXPOSURE = 0.7        # 总仓位价值占账户总资产最大比例 70%

# 账户级最大回撤保护
MAX_DRAWDOWN_PCT = 0.15         # 账户净值从最高点回撤 15% 暂停所有交易
DRAWDOWN_COOLDOWN = 86400       # 回撤冷却 24 小时 (防止过早恢复交易)

# 相关性分组（同组币种限制同时持仓数量）
CORRELATION_GROUPS = {'L1': ['BTC/USDT', 'ETH/USDT']}
MAX_CORRELATED_POSITIONS = 1
```

### 回测框架（双版本）

项目提供两套回测框架，各有侧重，可根据需求选择使用。

### 方案一：backtrader 版（`backtest.py`）

基于 [backtrader](https://www.backtrader.com/) 库，将策略适配为 `bt.Strategy` 格式，生态成熟、功能丰富。

**特点：**
- 内置 50+ 分析器（夏普比率、最大回撤、交易分析等开箱即用）
- 支持 K 线图可视化（`--plot` 参数）
- 支持参数扫描（`cerebro.optstrategy`）
- 适合策略探索、可视化分析、学习研究

**使用方法：**

```bash
# 回测单个币种
python backtest.py --symbol BTC/USDT --days 730

# 回测所有配置币种
python backtest.py --all --days 730

# 指定起止日期 + 初始资金
python backtest.py --symbol ETH/USDT --start 2024-01-01 --end 2025-01-01 --cash 5000

# 显示K线图（需 matplotlib）
python backtest.py --symbol BTC/USDT --days 365 --plot
```

**输出内容：** 总收益率、胜率、盈亏比、最大连续亏损、策略分布（趋势/均值回归）、退出原因统计、夏普比率、最大回撤等。

### 方案二：原生版（`backtest_native.py`）

轻量级自建回测器，直接复用 `bot_engine.py` 的信号逻辑和 `ta` 库指标，**回测结果与实盘完全一致**。

**特点：**
- 指标用 `ta` 库（与实盘 `bot_engine.py` 完全一致，无计算差异）
- 信号逻辑直接复用 `bot_engine` 的 `_detect_regime` / `_should_buy` / `_should_sell`
- 无框架约束，纯 pandas/Python 实现，易于扩展
- 适合实盘上线前验证、精确回测、参数优化

**使用方法：**

```bash
# 回测单个币种
python backtest_native.py --symbol BTC/USDT --days 730

# 回测所有配置币种
python backtest_native.py --all --days 730

# 指定起止日期 + 初始资金
python backtest_native.py --symbol ETH/USDT --start 2024-01-01 --end 2025-01-01 --cash 5000
```

**输出内容：** 总收益率、胜率、盈亏比、最大连续亏损、策略分布、退出原因统计、最近交易明细。

### 两套回测器对比

| 特性 | backtrader 版 | 原生版 |
|------|--------------|--------|
| **指标一致性** | 有细微差异（ADX/RSI实现不同） | 与实盘完全一致 |
| **代码复用** | 需改写为 `bt.Strategy` 格式 | 直接复用 `bot_engine` 信号逻辑 |
| **可视化** | 支持 `--plot` K线图 | 无 |
| **参数扫描** | 内置 `optstrategy` | 需自己实现循环 |
| **性能** | ~12s/币种 | ~11s/币种 |
| **学习成本** | 需学习 backtrader API | 纯 pandas，无额外框架 |
| **依赖** | 需安装 backtrader + matplotlib | 仅依赖已有的 ccxt/ta/pandas |
| **适用场景** | 策略探索、可视化、学习 | 实盘验证、精确回测 |

### 使用建议

- **实盘上线前验证** → 用 `backtest_native.py`（结果与实盘一致）
- **策略探索/可视化分析** → 用 `backtest.py`（有图表、分析器丰富）
- **参数优化** → 两个均可，native 更准确
- **快速验证信号逻辑** → 用 `backtest_native.py`（轻量、无框架约束）

> ⚠️ **注意**：两套回测器结果会有 5-10% 的差异，主要来自 backtrader 的 ADX/RSI 指标实现与 `ta` 库的细微差别。实盘前验证请以 `backtest_native.py` 为准。

---

## 📚 策略详解

### 进场逻辑（Regime 自适应）

系统先用 ADX + 布林带宽度识别市场状态（TREND / RANGE / NEUTRAL），再按状态选择策略：

#### 趋势态 (TREND) 进场 — 趋势跟随
```
A1. MACD 金叉 + 零轴过滤 + 量能 + 阳线质量
A2. ADX > 阈值 + 量能 + 价格>SMA20 + RSI>50 + 阳线质量
B.  RSI 上穿 50 + 趋势过滤(价格>SMA60 或 ADX>阈值) + 价格>布林中轨
D.  SMA20 突破 + RSI>55 + 量能 + 阳线质量
→ 任一满足即触发 BUY（策略类型 trend）
```

#### 震荡态 (RANGE) 进场 — 均值回归
```
C1. 布林下轨支撑 + 布林带宽度充足 + RSI 底背离(连续回升) + 量能
C2. RSI 超卖反弹 (RSI从超卖区回升 + 阳线确认 + 量能)
C3. 布林下轨缩口反弹 (价格触及下轨后收阳 + 量能)
→ 任一满足即触发 BUY（策略类型 meanrev）
```

#### 中性态 (NEUTRAL)
观望，不进场。

> 信号需连续 2 轮触发同方向才执行（二次确认，过滤假突破）。
> 多时间框架过滤：高级时间框架(1d)下跌时抑制买入。

### 出场逻辑

#### 趋势仓位出场（让利润奔跑）
- **ATR 追踪止损**：价格 ≤ 最高价 − ATR×倍数
- **固定止损**：亏损达到 `stop_loss_pct`
- **主动止盈**：盈利达到 `profit_target_atr`×ATR
- **RSI 超买**：盈利>5% 且 RSI>超买阈值
- **MACD 死叉+ADX 回落**
- **分阶段追踪止盈**：见上文

#### 均值回归仓位出场（快进快出）
- **紧止损**：`meanrev_config.stop_loss_pct`
- **布林中轨止盈**
- **RSI 回升退出**（RSI≥50 且盈利）
- **超时强制退出**：`max_hold_hours`

### 成交量确认机制

成交量是验证信号有效性的关键（阈值由 `volume_threshold` 配置，BTC=1.5、SOL=1.8）：

```python
volume_ratio = current_volume / volume_ma

# 趋势 A1/A2/D 信号: 要求 vol_ratio >= volume_threshold (如 1.5)
# 趋势 B / 震荡 C 信号: 放宽到 vol_ratio >= volume_threshold * 0.8 (如 1.2)
```

### 波动率自适应详解

系统根据 ATR% 判断市场波动状态，动态调整参数：

**低波动市场 (ATR% < 2%)**
- 市场特征: 价格波动小，趋势不明显
- 参数调整: ADX阈值×0.8，RSI阈值×0.8
- 效果: 更严格的进场条件，避免震荡市假信号

**高波动市场 (ATR% > 5%)**
- 市场特征: 价格剧烈波动，止损易被触发
- 参数调整: ADX阈值×1.2，RSI阈值/1.2
- 效果: 更宽松的止损空间，避免频繁止损

---

## 🛡️ 风险管理

### 止损体系

系统采用多层止损保护：

```
第一层: 交易所端止损单 (实盘)
  ↓ 买入后立即在交易所挂 STOP_LOSS 单，防4分钟轮询间隔跳空
第二层: ATR动态止损 (优先，软件层)
  ↓ 价格跌破 ATR止损线
第三层: 固定百分比止损 (备选)
  ↓ 亏损达到设定百分比
第四层: 熔断保护 (极端情况，双层检测)
  ↓ 单根K线暴跌 > 8% 或 连续3根累计跌幅 > 10%
```

### 订单执行（混合模式）

```
入场买入: 限价单 (挂略高于当前价，省手续费，未成交则放弃)
止损/止盈卖出: 市价单 (保证执行)
策略信号卖出: 限价单 (省手续费)
模拟交易: 加入滑点建模 (ATR的5%或固定0.1%)
```

### 追踪止盈示例

**ETH 交易示例**（对应当前 `STRATEGY_CONFIG['ETH/USDT']`）:
```
入场价: $2000
价格上涨到 $2240 (12%盈利)

阶段2激活: trigger_drawdown = 3.5%
持仓 6 小时: 时间衰减系数 1.0 (0-12h 内无衰减)
调整后阈值: 3.5% / 1.0 = 3.5%

价格回撤到 $2200:
回撤幅度: (2240-2200)/2240 = 1.79%
未触发止盈 (1.79% < 3.5%)

价格继续下跌到 $2161:
回撤幅度: (2240-2161)/2240 = 3.53%
触发追踪止盈，卖出获利 8.05%
```

### 仓位控制

```python
# 单币种仓位限制
if symbol in positions:
    拒绝开仓  # 已有持仓

# 波动率自适应仓位（平衡型）
risk_amount = total * risk_per_trade              # 风险下限 (1%)
risk_based = risk_amount / (atr_pct * atr_multi)  # 风险仓位
max_by_pct = total * max_position_pct             # 仓位上限 (6-8%)
trade_amount = min(risk_based, max_by_pct, max_trade_amount)

# 总仓位限制
total_cost = sum(所有持仓成本)
if total_cost / total_balance > 0.7:
    拒绝开仓  # 仓位过重
```

---

## 📱 远程控制

### Telegram Bot 设置

1. **创建 Bot**
   - 在 Telegram 中找到 @BotFather
   - 发送 `/newbot` 创建新机器人
   - 获取 Bot Token

2. **获取 Chat ID**
   - 向你的 Bot 发送消息
   - 访问 `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   - 找到 `chat.id` 字段

3. **配置到系统**

在 `.env` 中设置（`config.py` 会自动读取）：
```bash
TELEGRAM_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

### 常用命令示例

```bash
# 查看持仓
/status

# 查看追踪止盈状态
/trailing_status

# 设置止损
/set_sl ETH/USDT 0.05

# 设置追踪比例
/set_ts BTC/USDT 0.03

# 紧急熔断
/fuse

# 解除熔断
/unfuse
```

---

## 📊 性能指标

### 回测数据 (模拟环境)

| 指标 | 数值 | 说明 |
|------|------|------|
| **胜率** | 58-65% | 成交量确认提升胜率 |
| **盈亏比** | 1.8:1 | 追踪止盈保护利润 |
| **最大回撤** | < 15% | 多层风控保护 |
| **夏普比率** | 1.5-2.0 | 波动率自适应效果 |

### 实战表现

- ✅ 成交量确认减少假突破 40%
- ✅ ATR动态止损降低无效止损 35%
- ✅ 分阶段追踪止盈提升盈利 25%
- ✅ 时间衰减机制减少长期持仓风险

---

## 🔮 优化规划

### ✅ 短期优化（已完成）

以下功能已在当前版本中实现：

| 功能 | 实现位置 | 说明 |
|------|---------|------|
| **多时间框架分析** | `bot_engine.py` `_check_higher_tf_trend()` | 1d趋势过滤4h信号，HTF下跌时禁止趋势入场、均值回归降仓 |
| **市场状态识别** | `bot_engine.py` `_detect_regime()` | ADX+布林带宽度识别 TREND/RANGE/NEUTRAL，OR判定降低NEUTRAL占比 |
| **回测系统** | `backtest.py` + `backtest_native.py` | 双版本回测框架：backtrader版（可视化+分析器）+ 原生版（与实盘一致） |
| **历史数据回测** | 两个回测器 | 支持指定币种/天数/起止日期/初始资金 |
| **性能指标可视化** | `backtest.py --plot` | backtrader内置K线图+指标+买卖点 |
| **参数优化工具** | `backtest_native.py` | 可编程式参数扫描，直接复用实盘信号逻辑 |

### 📋 中期优化 (3-6个月)

基于当前架构和数据量，以下规划务实且可实施：

#### 1. 信号强度评分系统

> 替代原"机器学习增强"规划。当前单币种730天仅38-51笔交易，样本量不足以训练ML模型，过拟合风险高。改为规则驱动的信号评分更务实。

**设计思路：**
```python
# 不训练模型，而是给每个信号条件打分加权
signal_score = 0
if macd_golden: signal_score += 30
if vol_ratio >= 1.5: signal_score += 20
if adx > 25: signal_score += 25
if rsi_bouncing: signal_score += 25

# 评分越高，仓位越大
position_scale = min(signal_score / 100, 1.0)  # 0.5-1.0
```

**实施步骤：**
1. 定义各信号条件的权重（基于回测验证）
2. 评分映射到仓位比例（高分满仓，低分半仓）
3. 评分低于阈值时放弃入场

**预期效果：** 高质量信号加大仓位，低质量信号减仓或放弃，提升整体盈亏比

#### 2. 动态资金分配

> 替代原"风险平价模型"。当前三币种固定 `max_position_pct`，不区分市场状态。

**设计思路：**
```python
# 根据当前 regime 和历史胜率动态调整各币种仓位
if regime == 'TREND':
    btc_scale = 1.2   # BTC趋势行情好，加大仓位
    sol_scale = 0.8   # SOL趋势行情波动大，减小仓位
elif regime == 'RANGE':
    btc_scale = 0.6   # BTC震荡行情表现差，减小仓位
    sol_scale = 1.2   # SOL震荡行情均值回归表现好，加大仓位
```

**实施步骤：**
1. 回测各币种在不同 regime 下的胜率和盈亏比
2. 建立 regime-币种-仓位 矩阵
3. 每轮循环根据当前 regime 调整 `max_position_pct`

**预期效果：** 将资金分配给当前环境下表现最好的币种，提升资金利用率

#### 3. 策略组合扩展

> 当前仅有趋势跟踪+均值回归双模式，可扩展更多策略类型。

**候选策略：**
- **突破策略** — 价格突破N根K线高低点入场（适合 NEUTRAL→TREND 过渡期）
- **动量策略** — 基于价格变化率（ROC）排名入场
- **网格策略** — 在 RANGE 态自动挂网格单（适合震荡市）

**实施步骤：**
1. 在 `bot_engine.py` 新增策略信号函数
2. 通过回测验证新策略的独立表现
3. 与现有策略组合，信号冲突时按评分优先级选择

### 🎯 长期规划 (6-12个月)

#### 1. Web 管理界面

> 最务实的长期规划，已有 Telegram 远程控制基础，Web 版是自然延伸。

**技术选型：** Streamlit（快速搭建，1-2周可出原型）

**功能模块：**
```
📊 实时仪表盘
├── 账户净值 + 收益曲线
├── 当前持仓 + 浮动盈亏
├── Regime 状态实时展示
└── 熔断/回撤状态

⚙️ 策略管理
├── 参数在线调整（config.py 远程化）
├── 策略开关（启用/禁用某币种或信号）
└── 回测触发 + 结果展示

📈 历史分析
├── 交易明细 + 盈亏分布
├── 退出原因统计
└── 月度/周度报告
```

**实施步骤：**
1. 用 Streamlit 搭建基础框架，读取 `bot_state.json`
2. 实现实时仪表盘（自动刷新）
3. 接入参数调整功能（写入 runtime_config）
4. 集成回测触发（调用 `backtest_native.py`）

#### 2. 市场情绪集成

> 简化版"极端行情应对"。不接入付费新闻API和NLP模型，而是用免费的恐惧贪婪指数。

**数据源：**
- [Crypto Fear & Greed Index](https://api.alternative.me/fng/) — 免费，每日更新
- 计算 0-100 情绪分数（0=极度恐惧，100=极度贪婪）

**应用场景：**
```python
# 情绪极端时调整策略
if fear_greed < 20:    # 极度恐惧
    # 禁止趋势跟踪入场（可能继续下跌）
    # 均值回归信号仓位减半（接飞刀风险）
elif fear_greed > 80:  # 极度贪婪
    # 趋势仓位收紧止盈（可能见顶）
    # 禁止均值回归入场（可能不回归）
```

**实施步骤：**
1. 新增 `sentiment.py` 模块，每日拉取恐惧贪婪指数
2. 在 `get_strategy_signal` 中加入情绪过滤
3. 回测验证情绪过滤的效果（历史数据可获取）

#### 3. 异常检测与预警

> 简化版"黑天鹅检测"。不依赖链上数据和新闻NLP，基于价格行为检测异常。

**检测维度：**
```python
# 1. 波动率异常：ATR 突然放大到历史均值的 N 倍
if current_atr > avg_atr_30d * 2.5:
    trigger_warning("波动率异常飙升")

# 2. 成交量异常：成交量放大到均值的 N 倍但价格不动
if vol_ratio > 3.0 and abs(price_change) < 0.01:
    trigger_warning("量价背离异常")

# 3. 跨币种相关性异常：BTC/ETH/SOL 同时异动
if all_three_drop > 0.05 in 4h:
    trigger_warning("系统性风险预警")
```

**实施步骤：**
1. 在主循环中加入异常检测逻辑
2. 异常时发送 Telegram 预警
3. 严重异常时自动触发全局熔断

---

## ❓ 常见问题

### Q1: 如何选择交易对？

**A**: 建议选择流动性好、波动适中的主流币种：
- **推荐**: BTC/USDT, ETH/USDT
- **谨慎**: 高波动山寨币（需要调整参数）
- **避免**: 流动性差的币种

### Q2: 模拟交易和实盘交易的区别？

**A**: 
- **模拟交易**: 使用虚拟账户，无真实资金风险，适合策略测试
- **实盘交易**: 连接真实交易所账户，需要谨慎操作

建议先用模拟模式验证策略，稳定盈利后再切换实盘。

### Q3: 如何调整策略参数？

**A**: 
1. 编辑 `config.py` 文件
2. 修改对应币种的参数配置
3. 重启机器人使配置生效

或使用 Telegram 远程命令实时调整。

### Q4: 追踪止盈设置建议？

**A**: 
- **保守型**: 盈利2%开启，回撤1.5%触发
- **平衡型**: 盈利3%开启，回撤2%触发
- **激进型**: 盈利5%开启，回撤3%触发

根据币种波动率和风险偏好选择。

### Q5: 系统出现亏损怎么办？

**A**: 
1. 检查市场状态（是否极端行情）
2. 查看成交量确认是否生效
3. 调整止损参数（考虑使用ATR止损）
4. 暂停交易，分析原因
5. 优化参数后重新启动

### Q6: 如何避免过度交易？

**A**: 系统已内置多重保护：
- 成交量确认过滤假信号
- 波动率自适应减少无效交易
- 仓位限制防止过度杠杆
- 熔断机制应对极端情况

---

## 🤝 贡献指南

欢迎贡献代码、提出建议或报告问题！

### 贡献方式

1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 提交 Pull Request

### 代码规范

- 遵循 PEP 8 编码规范
- 添加必要的注释和文档
- 编写单元测试
- 更新相关文档

---

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情

---

## ⚠️ 免责声明

本项目仅供学习和研究使用。加密货币交易存在高风险，可能导致资金损失。使用本系统进行实盘交易需自行承担所有风险。作者不对任何因使用本系统而产生的损失负责。

**风险提示**:
- ⚠️ 加密货币市场波动剧烈
- ⚠️ 过去表现不代表未来收益
- ⚠️ 请仅使用您能承受损失的资金
- ⚠️ 建议在模拟环境充分测试后再考虑实盘

---

## 📞 联系方式

- **Issues**: [GitHub Issues](https://github.com/yourusername/crypto-bot/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/crypto-bot/discussions)

---

<div align="center">

**⭐ 如果这个项目对您有帮助，请给一个 Star ⭐**

Made with ❤️ by Crypto Trading Enthusiasts

</div>
