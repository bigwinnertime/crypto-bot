"""
Web 管理界面（长期规划-1）— Streamlit Dashboard。

功能模块：
  📊 实时仪表盘：账户净值、持仓、Regime状态、熔断状态
  📈 历史分析：交易明细、盈亏分布、退出原因统计
  ⚙️ 策略管理：参数查看、信号评分展示

启动：
  streamlit run dashboard.py
  或
  streamlit run dashboard.py --server.port 8501
"""
import json
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

# 项目根目录加入 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_state.json")


@st.cache_data(ttl=10)  # 10秒缓存
def load_state():
    """加载 bot_state.json。"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        st.error(f"加载状态文件失败: {e}")
    return {}


def main():
    st.set_page_config(
        page_title="Crypto Bot Dashboard",
        page_icon="🤖",
        layout="wide"
    )

    st.title("🤖 Crypto Bot 交易机器人仪表盘")

    state = load_state()
    if not state:
        st.warning("⚠️ 无法加载 bot_state.json，请确保机器人正在运行")
        st.info(f"状态文件路径: {STATE_FILE}")
        return

    # ═══ 顶部摘要 ═══
    col1, col2, col3, col4 = st.columns(4)

    virtual = state.get('virtual_account', {})
    balance = virtual.get('balance', 0)
    initial_balance = virtual.get('initial_balance', 10000)
    roi = ((balance / initial_balance) - 1) * 100 if initial_balance > 0 else 0

    with col1:
        st.metric("💰 账户净值", f"{balance:.2f} USDT", f"{roi:+.2f}%")

    with col2:
        positions = state.get('positions', {})
        st.metric("📊 当前持仓", f"{len(positions)} 个")

    with col3:
        fused = state.get('is_fused', False)
        fused_symbols = state.get('fused_symbols', {})
        fuse_status = "🔴 熔断中" if (fused or fused_symbols) else "🟢 正常"
        st.metric("🛡️ 熔断状态", fuse_status)

    with col4:
        trades = state.get('trade_history', [])
        st.metric("📝 历史交易", f"{len(trades)} 笔")

    st.divider()

    # ═══ 实时仪表盘 ═══
    st.header("📊 实时仪表盘")

    # 持仓详情
    if positions:
        st.subheader("当前持仓")
        pos_data = []
        for symbol, pos in positions.items():
            entry_price = pos.get('entry_price', 0)
            current_price = pos.get('current_price', entry_price)
            pnl_pct = ((current_price / entry_price) - 1) * 100 if entry_price > 0 else 0
            pos_data.append({
                '币种': symbol,
                '策略': '趋势跟踪' if pos.get('strategy_type') == 'trend' else '均值回归',
                '入场价': entry_price,
                '当前价': current_price,
                '数量': pos.get('amount', 0),
                '最高价': pos.get('highest_price', entry_price),
                '收益率(%)': round(pnl_pct, 2),
                '入场模式': pos.get('mode', '').replace('_', ' '),
                '持仓时长(h)': pos.get('holding_hours', 0),
            })
        df_pos = pd.DataFrame(pos_data)
        st.dataframe(df_pos, use_container_width=True)
    else:
        st.info("📭 当前无持仓")

    # 账户状态
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("💰 虚拟账户")
        peak = virtual.get('peak_balance', balance)
        drawdown = ((balance / peak) - 1) * 100 if peak > 0 else 0
        st.write(f"- 初始资金: **{initial_balance:.2f} USDT**")
        st.write(f"- 当前余额: **{balance:.2f} USDT**")
        st.write(f"- 历史峰值: **{peak:.2f} USDT**")
        st.write(f"- 当前回撤: **{drawdown:.2f}%**")
        st.write(f"- 最大允许回撤: **{config.MAX_DRAWDOWN_PCT * 100:.0f}%**")

        # 回撤进度条
        dd_progress = min(abs(drawdown) / (config.MAX_DRAWDOWN_PCT * 100), 1.0)
        st.progress(dd_progress, text=f"回撤风险 {dd_progress*100:.0f}%")

    with col_right:
        st.subheader("🛡️ 风控状态")
        if fused:
            st.error("🔴 全局熔断已触发！所有交易暂停")
        fused_symbols = state.get('fused_symbols', {})
        if fused_symbols:
            for sym, ts in fused_symbols.items():
                elapsed = (datetime.now().timestamp() - ts) / 3600
                remaining = (config.FUSE_DURATION / 3600) - elapsed
                st.warning(f"⏸️ {sym} 熔断中，剩余冷却约 {remaining:.1f}h")
        if not fused and not fused_symbols:
            st.success("🟢 所有币种正常交易")

    st.divider()

    # ═══ 历史分析 ═══
    st.header("📈 历史分析")

    if trades:
        df_trades = pd.DataFrame(trades)

        # 确保日期列
        if 'exit_time' in df_trades.columns:
            df_trades['exit_time'] = pd.to_datetime(df_trades['exit_time'])

        col_a, col_b, col_c = st.columns(3)

        with col_a:
            wins = df_trades[df_trades['pnl_pct'] > 0] if 'pnl_pct' in df_trades.columns else pd.DataFrame()
            losses = df_trades[df_trades['pnl_pct'] <= 0] if 'pnl_pct' in df_trades.columns else pd.DataFrame()
            win_rate = len(wins) / len(df_trades) * 100 if len(df_trades) > 0 else 0
            avg_win = wins['pnl_pct'].mean() if len(wins) > 0 else 0
            avg_loss = abs(losses['pnl_pct'].mean()) if len(losses) > 0 else 0
            profit_factor = avg_win / avg_loss if avg_loss > 0 else float('inf')
            st.metric("胜率", f"{win_rate:.1f}%")
            st.metric("盈亏比", f"{profit_factor:.2f}")

        with col_b:
            if 'pnl_pct' in df_trades.columns:
                total_pnl = df_trades['pnl_pct'].sum()
                st.metric("总盈亏(%)", f"{total_pnl:+.2f}%")
                avg_pnl = df_trades['pnl_pct'].mean()
                st.metric("平均盈亏(%)", f"{avg_pnl:+.2f}%")

        with col_c:
            if 'strategy_type' in df_trades.columns:
                trend_count = len(df_trades[df_trades['strategy_type'] == 'trend'])
                meanrev_count = len(df_trades[df_trades['strategy_type'] == 'meanrev'])
                st.metric("趋势交易", f"{trend_count} 笔")
                st.metric("均值回归", f"{meanrev_count} 笔")

        # 盈亏分布图
        if 'pnl_pct' in df_trades.columns:
            st.subheader("盈亏分布")
            st.bar_chart(df_trades['pnl_pct'])

        # 退出原因统计
        if 'reason' in df_trades.columns:
            st.subheader("退出原因统计")
            reason_counts = df_trades['reason'].value_counts()
            st.bar_chart(reason_counts)

        # 交易明细表
        st.subheader("交易明细")
        display_cols = [c for c in ['entry_time', 'exit_time', 'symbol', 'strategy_type',
                                     'entry_price', 'exit_price', 'pnl_pct', 'reason']
                        if c in df_trades.columns]
        st.dataframe(df_trades[display_cols], use_container_width=True)
    else:
        st.info("📭 暂无历史交易记录")

    st.divider()

    # ═══ 策略配置 ═══
    st.header("⚙️ 策略配置")

    st.subheader("交易币种")
    for symbol in config.SYMBOLS:
        spec = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)
        with st.expander(f"{symbol} 参数"):
            col1, col2, col3 = st.columns(3)
            with col1:
                st.write(f"- ADX阈值: {spec.get('adx_threshold', 'N/A')}")
                st.write(f"- RSI超卖/超买: {spec.get('rsi_oversold', 'N/A')}/{spec.get('rsi_overbought', 'N/A')}")
                st.write(f"- 固定止损: {spec.get('stop_loss_pct', 'N/A')*100:.1f}%")
                st.write(f"- ATR倍数: {spec.get('atr_multiplier', 'N/A')}")
            with col2:
                st.write(f"- 量能阈值: {spec.get('volume_threshold', 'N/A')}")
                st.write(f"- max_position_pct: {spec.get('max_position_pct', 'N/A')*100:.0f}%")
                st.write(f"- min_signal_score: {spec.get('min_signal_score', 'N/A')}")
                st.write(f"- profit_target_atr: {spec.get('profit_target_atr', 'N/A')}×ATR")
            with col3:
                st.write(f"- 保本止损: 盈利>{spec.get('breakeven_trigger', 0.02)*100:.1f}%后触发")
                mr_cfg = spec.get('meanrev_config', {})
                st.write(f"- 均值回归止损: {mr_cfg.get('stop_loss_pct', 'N/A')*100:.1f}%")
                st.write(f"- 超时退出: {mr_cfg.get('max_hold_hours', 'N/A')}h")

    st.subheader("全局风控参数")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.write(f"- 最大总仓位: {config.MAX_TOTAL_EXPOSURE*100:.0f}%")
        st.write(f"- 单根K线熔断: {config.DRAWDOWN_FUSE*100:.0f}%")
    with col2:
        st.write(f"- 多根累计熔断: {config.DRAWDOWN_FUSE_MULTI_BAR*100:.0f}% ({config.DRAWDOWN_FUSE_MULTI_BAR_COUNT}根)")
        st.write(f"- 熔断时长: {config.FUSE_DURATION/3600:.0f}h")
    with col3:
        st.write(f"- 最大回撤: {config.MAX_DRAWDOWN_PCT*100:.0f}%")
        st.write(f"- 回撤冷却: {config.DRAWDOWN_COOLDOWN/3600:.0f}h")

    # ═══ 自动刷新 ═══
    st.divider()
    auto_refresh = st.checkbox("🔄 自动刷新（10秒）", value=True)
    if auto_refresh:
        st.caption(f"最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        import time
        time.sleep(10)
        st.rerun()


if __name__ == "__main__":
    main()
