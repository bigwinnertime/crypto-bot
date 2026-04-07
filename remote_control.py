import telebot
import json
import config
import os
import ccxt
import time
import logging
import threading
from dotenv import load_dotenv
from risk_manager import RiskManager
from state_manager import state_mgr

logger = logging.getLogger("TradingBot.Remote")
risk = RiskManager()

load_dotenv()

# 单例模式：确保只有一个 bot 实例
_bot_instance = None
_bot_lock = threading.Lock()

def get_bot_instance():
    global _bot_instance
    if _bot_instance is None:
        with _bot_lock:
            if _bot_instance is None:
                _bot_instance = telebot.TeleBot(os.getenv('TELEGRAM_TOKEN'))
    return _bot_instance

# 初始化 Bot
bot = get_bot_instance()
ADMIN_ID = int(os.getenv('TELEGRAM_CHAT_ID'))

# 提取私钥并处理换行符
raw_key = os.getenv('BINANCE_SECRET_KEY', '')

# 关键点：将字符串中的 "\n" 替换为真正的换行字符，并去掉可能误加的引号
formatted_key = raw_key.replace('\\n', '\n').strip('"').strip("'")


# 初始化交易所 (以币安为例，可根据需要更换)
exchange = ccxt.binance({
    'apiKey': os.getenv('BINANCE_API_KEY'),
    'secret': formatted_key,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot', 'secretType': 'ed25519'}
})

def auth(message):
    return message.from_user.id == ADMIN_ID

# --- 0. 帮助指令 (/help) ---
@bot.message_handler(commands=['help', 'start'])
def show_help(message):
    if not auth(message): return
    
    help_text = """🤖 **交易机器人远程控制指南**

📊 **状态查询**
/status - 查看账户状态（虚拟账户+真实账户）
/positions - 查看当前持仓详情
/performance - 查看收益率和交易统计
/trailing_status - 查看追踪止盈状态

⚙️ **参数设置**
/config - 查看当前配置参数
/config [参数名] [新值] - 修改全局配置（如: /config LIVE_TRADE True）
/set_sl [币种] [数值] - 设置止损比例（如: /set_sl BTC/USDT 0.03）
/set_ts [币种] [数值] - 设置追踪止盈比例（如: /set_ts BTC/USDT 0.02）

🚨 **风险控制**
/fuse - 紧急熔断（停止所有交易）
/unfuse - 解除熔断

💡 **提示**
• 所有数值比例使用小数表示（如 0.03 = 3%）
• 币种格式: BTC/USDT, ETH/USDT
• 只有管理员可使用这些命令"""
    
    bot.reply_to(message, help_text, parse_mode='Markdown')

# --- 1. 状态查询处理器 ---
@bot.message_handler(commands=['status'])
def get_status(message):
    if not auth(message): 
        bot.reply_to(message, "🚫 无权访问")
        return
    
    loading_msg = bot.reply_to(message, "⏳ 正在同步账户数据...")
    
    try:
        # A. 模拟/虚拟账户状态
        v_status = "📉 **虚拟账户 (Paper Trading)**\n"
        virtual_acc = risk.state.get('virtual_account', {})
        v_balance = virtual_acc.get('balance', 0)
        v_pnl = virtual_acc.get('total_pnl', 0)
        v_status += f"💰 余额: `{v_balance:.2f} USDT`\n"
        v_status += f"📊 累计盈亏: `{v_pnl:+.2f} USDT`\n\n"
        
        for symbol, cfg in config.STRATEGY_CONFIG.items():
            v_status += f"🔸 `{symbol}`: 止损`{cfg.get('stop_loss_pct')*100:.1f}%`, 追踪`{cfg.get('trailing_stops', [{}])[0].get('trigger_drawdown', 0)*100:.1f}%`\n"

        # B. 真实交易所账户状态
        r_status = "\n🏦 **真实账户 (Exchange)**\n"
        try:
            balance = exchange.fetch_balance()
            active_balances = {k: v for k, v in balance['total'].items() if v > 0}
            
            if not active_balances:
                r_status += "📭 空仓中"
            else:
                total_value = 0
                for coin, amt in active_balances.items():
                    r_status += f"🔹 `{coin}`: `{amt:.6f}`\n"
                    if coin != 'USDT' and f'{coin}/USDT' in config.SYMBOLS:
                        try:
                            ticker = exchange.fetch_ticker(f'{coin}/USDT')
                            total_value += amt * ticker['last']
                        except:
                            pass
                    elif coin == 'USDT':
                        total_value += amt
                r_status += f"\n💵 预估总价值: `≈{total_value:.2f} USDT`"
        except Exception as ex:
            r_status += f"⚠️ 获取失败: `{str(ex)[:50]}`"

        # C. 系统状态
        is_fused = risk.state.get('is_fused', False)
        fuse_status = "🚨 已熔断" if is_fused else "✅ 正常"
        positions_count = len(risk.state.get('positions', {}))
        
        report = (
            f"🤖 **交易机器人实时状态**\n"
            f"{'─' * 25}\n"
            f"🛡️ 系统状态: {fuse_status}\n"
            f"📈 当前持仓: `{positions_count}` 个\n"
            f"🔄 运行模式: `{'实盘' if config.LIVE_TRADE else '模拟'}`\n"
            f"{'─' * 25}\n"
            f"{v_status}{r_status}"
        )
        
        bot.edit_message_text(report, chat_id=loading_msg.chat.id, message_id=loading_msg.message_id, parse_mode='Markdown')
        
    except Exception as e:
        error_msg = f"⚠️ **获取状态失败**\n```\n{str(e)[:200]}\n```"
        try:
            bot.edit_message_text(error_msg, chat_id=loading_msg.chat.id, message_id=loading_msg.message_id, parse_mode='Markdown')
        except:
            bot.reply_to(message, error_msg, parse_mode='Markdown')

# --- 2. 止损设置处理器 ---
@bot.message_handler(commands=['set_sl'])
def update_stop_loss(message):
    if not auth(message): 
        bot.reply_to(message, "🚫 无权访问")
        return
    try:
        parts = message.text.split()
        if len(parts) != 3:
            bot.reply_to(message, 
                "⚠️ **格式错误**\n"
                "正确格式: `/set_sl [币种] [数值]`\n"
                "例如: `/set_sl BTC/USDT 0.03`\n\n"
                "📌 说明: 数值为百分比小数形式，0.03 = 3%", 
                parse_mode='Markdown')
            return
            
        _, symbol, value = parts
        symbol = symbol.upper()
        sl_val = float(value)

        def update_logic(state):
            if 'runtime_config' not in state:
                state['runtime_config'] = {}
            if symbol not in state['runtime_config']:
                state['runtime_config'][symbol] = {}
            state['runtime_config'][symbol]['stop_loss_pct'] = sl_val
            return state

        if state_mgr.update_state(func=update_logic):
            bot.reply_to(message, 
                f"✅ **止损设置成功**\n\n"
                f"🔸 币种: `{symbol}`\n"
                f"📉 止损比例: `{sl_val*100:.2f}%`\n"
                f"💾 设置已持久化", 
                parse_mode='Markdown')
        else:
            bot.reply_to(message, "❌ **设置失败**\n状态更新时发生错误", parse_mode='Markdown')
            
    except ValueError:
        bot.reply_to(message, 
            "⚠️ **参数错误**\n"
            "止损比例必须是数字\n"
            "例如: `/set_sl BTC/USDT 0.03`", 
            parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"⚠️ **操作失败**\n```\n{str(e)[:200]}\n```", parse_mode='Markdown')

# --- 3. 追踪止盈状态查询 ---
@bot.message_handler(commands=['trailing_status'])
def get_trailing_status(message):
    if not auth(message): 
        bot.reply_to(message, "🚫 无权访问")
        return
    
    try:
        positions = risk.state.get('positions', {})
        if not positions:
            bot.reply_to(message, "📭 **当前无持仓**\n\n机器人正在监控市场机会...", parse_mode='Markdown')
            return
        
        status_report = "📊 **追踪止盈状态报告**\n\n"
        
        for symbol in positions:
            try:
                ticker = exchange.fetch_ticker(symbol)
                current_price = ticker['last']
                
                status = risk.get_trailing_stop_status(symbol, current_price)
                if status:
                    # 盈亏颜色标记
                    profit_emoji = "🟢" if status['highest_profit_pct'] > 0 else "🔴"
                    active_emoji = "✅ 已激活" if status['active_trigger_drawdown'] else "⏳ 未激活"
                    
                    status_report += f"{profit_emoji} **{symbol}**\n"
                    status_report += f"├─ 入场价: `{status['entry_price']:.2f}` USDT\n"
                    status_report += f"├─ 当前价: `{status['current_price']:.2f}` USDT\n"
                    status_report += f"├─ 最高价: `{status['highest_price']:.2f}` USDT\n"
                    status_report += f"├─ 最高盈利: `{status['highest_profit_pct']:+.2%}`\n"
                    status_report += f"├─ 当前回撤: `{status['current_drawdown_pct']:.2%}`\n"
                    status_report += f"├─ 追踪止盈: {active_emoji}\n"
                    
                    if status['active_trigger_drawdown']:
                        status_report += f"├─ 回撤阈值: `{status['active_trigger_drawdown']:.2%}`\n"
                        if status['time_multiplier'] and status['time_multiplier'] != 1.0:
                            status_report += f"├─ 时间系数: `{status['time_multiplier']:.2f}`\n"
                            status_report += f"└─ 调整后阈值: `{status['adjusted_trigger_drawdown']:.2%}`\n"
                        else:
                            status_report += f"└─ 调整后阈值: `{status['adjusted_trigger_drawdown']:.2%}`\n"
                    
                    status_report += f"⏱ 持仓时间: `{status['holding_time_hours']:.1f}` 小时\n\n"
            except Exception as e:
                status_report += f"⚠️ **{symbol}** 数据获取失败: `{str(e)[:30]}`\n\n"
        
        bot.reply_to(message, status_report, parse_mode='Markdown')
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ **获取状态失败**\n```\n{str(e)[:200]}\n```", parse_mode='Markdown')

# --- 4. 追踪止盈设置处理器 ---
@bot.message_handler(commands=['set_ts'])
def update_trailing_stop(message):
    if not auth(message): 
        bot.reply_to(message, "🚫 无权访问")
        return
    try:
        parts = message.text.split()
        if len(parts) != 3:
            bot.reply_to(message, 
                "⚠️ **格式错误**\n"
                "正确格式: `/set_ts [币种] [数值]`\n"
                "例如: `/set_ts BTC/USDT 0.02`\n\n"
                "📌 说明: 数值为回撤比例的小数形式，0.02 = 2%", 
                parse_mode='Markdown')
            return
            
        _, symbol, value = parts
        symbol = symbol.upper()
        ts_val = float(value)

        risk.update_runtime_config(symbol, 'trailing_stop_pct', ts_val)
        
        bot.reply_to(message, 
            f"✅ **追踪止盈设置成功**\n\n"
            f"🔸 币种: `{symbol}`\n"
            f"📈 回撤比例: `{ts_val*100:.2f}%`\n"
            f"💾 设置已持久化，重启不丢失", 
            parse_mode='Markdown'
        )
        
    except ValueError:
        bot.reply_to(message, 
            "⚠️ **参数错误**\n"
            "追踪止盈比例必须是数字\n"
            "例如: `/set_ts BTC/USDT 0.02`", 
            parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"⚠️ **操作失败**\n```\n{str(e)[:200]}\n```", parse_mode='Markdown')

# --- 5. 紧急熔断指令 (/fuse) ---
@bot.message_handler(commands=['fuse'])
def handle_emergency_fuse(message):
    if not auth(message): 
        bot.reply_to(message, "🚫 无权访问")
        return
    
    try:
        is_already_fused = risk.state.get('is_fused', False)
        if is_already_fused:
            bot.reply_to(message, 
                "⚠️ **熔断已处于激活状态**\n\n"
                "系统已在熔断保护中。\n"
                "如需解除，请发送 `/unfuse`", 
                parse_mode='Markdown')
            return
            
        risk.remote_set_fuse(True)
        
        bot.reply_to(message, 
            "🚨 **紧急熔断已启动！**\n\n"
            "✅ 熔断状态已写入 `bot_state.json`\n"
            "⏸️ 主程序将在下一轮循环检测到并停止交易\n"
            "💡 如需恢复，请发送 `/unfuse`", 
            parse_mode='Markdown')
            
    except Exception as e:
        bot.reply_to(message, f"⚠️ **熔断操作失败**\n```\n{str(e)[:200]}\n```", parse_mode='Markdown')

# --- 6. 解除熔断指令 (/unfuse) ---
@bot.message_handler(commands=['unfuse'])
def handle_unfuse(message):
    if not auth(message): 
        bot.reply_to(message, "🚫 无权访问")
        return
    
    try:
        is_fused = risk.state.get('is_fused', False)
        if not is_fused:
            bot.reply_to(message, 
                "ℹ️ **熔断未激活**\n\n"
                "系统当前处于正常运行状态。\n"
                "如需熔断，请发送 `/fuse`", 
                parse_mode='Markdown')
            return
            
        risk.remote_set_fuse(False)
        
        bot.reply_to(message, 
            "✅ **熔断已解除**\n\n"
            "🔄 机器人将恢复正常扫描信号\n"
            "📊 系统已恢复交易监控", 
            parse_mode='Markdown')
            
    except Exception as e:
        bot.reply_to(message, f"⚠️ **解除熔断失败**\n```\n{str(e)[:200]}\n```", parse_mode='Markdown')

# --- 7. 持仓查询指令 (/positions) ---
@bot.message_handler(commands=['positions'])
def get_positions(message):
    if not auth(message): 
        bot.reply_to(message, "🚫 无权访问")
        return
    
    loading_msg = bot.reply_to(message, "⏳ 正在获取持仓数据...")
    
    try:
        positions = risk.state.get('positions', {})
        if not positions:
            bot.edit_message_text(
                "📭 **当前无持仓**\n\n机器人正在监控市场机会...",
                chat_id=loading_msg.chat.id,
                message_id=loading_msg.message_id,
                parse_mode='Markdown')
            return
        
        report = "📊 **当前持仓详情**\n\n"
        total_unrealized_pnl = 0
        total_cost = 0
        
        for symbol, pos in positions.items():
            try:
                ticker = exchange.fetch_ticker(symbol)
                current_price = ticker['last']
                
                entry_price = pos['entry_price']
                amount = pos['amount']
                highest_price = pos.get('highest_price', entry_price)
                cost = pos.get('cost', entry_price * amount)
                total_cost += cost
                
                unrealized_pnl = (current_price - entry_price) * amount
                unrealized_pnl_pct = (current_price / entry_price - 1) * 100
                total_unrealized_pnl += unrealized_pnl
                
                drawdown_from_high = (highest_price - current_price) / highest_price * 100 if highest_price > 0 else 0
                max_profit_pct = (highest_price / entry_price - 1) * 100
                
                holding_hours = risk._get_holding_hours(pos)
                
                # 根据盈亏选择 emoji
                pnl_emoji = "🟢" if unrealized_pnl >= 0 else "🔴"
                
                report += f"{pnl_emoji} **{symbol}**\n"
                report += f"├─ 持仓量: `{amount:.6f}`\n"
                report += f"├─ 成本: `{cost:.2f} USDT`\n"
                report += f"├─ 入场价: `{entry_price:.2f}` USDT\n"
                report += f"├─ 当前价: `{current_price:.2f}` USDT\n"
                report += f"├─ 最高价: `{highest_price:.2f}` USDT ({max_profit_pct:+.2f}%)\n"
                report += f"├─ 浮动盈亏: `{unrealized_pnl:+.2f} USDT` ({unrealized_pnl_pct:+.2f}%)\n"
                report += f"├─ 当前回撤: `{drawdown_from_high:.2f}%`\n"
                report += f"└─ 持仓时间: `{holding_hours:.1f}` 小时\n\n"
                
            except Exception as e:
                report += f"⚠️ **{symbol}**\n   价格获取失败: `{str(e)[:40]}`\n\n"
        
        # 总体统计
        total_pnl_pct = (total_unrealized_pnl / total_cost * 100) if total_cost > 0 else 0
        total_emoji = "🟢" if total_unrealized_pnl >= 0 else "🔴"
        
        report += f"{'─' * 30}\n"
        report += f"{total_emoji} **总体浮动盈亏**\n"
        report += f"💰 金额: `{total_unrealized_pnl:+.2f} USDT`\n"
        report += f"📊 收益率: `{total_pnl_pct:+.2f}%`"
        
        bot.edit_message_text(report, chat_id=loading_msg.chat.id, message_id=loading_msg.message_id, parse_mode='Markdown')
        
    except Exception as e:
        error_msg = f"⚠️ **获取持仓失败**\n```\n{str(e)[:200]}\n```"
        try:
            bot.edit_message_text(error_msg, chat_id=loading_msg.chat.id, message_id=loading_msg.message_id, parse_mode='Markdown')
        except:
            bot.reply_to(message, error_msg, parse_mode='Markdown')

# --- 8. 收益率统计指令 (/performance) ---
@bot.message_handler(commands=['performance'])
def get_performance(message):
    if not auth(message): 
        bot.reply_to(message, "🚫 无权访问")
        return
    
    loading_msg = bot.reply_to(message, "⏳ 正在生成绩效报告...")
    
    try:
        trade_history = risk.state.get('trade_history', [])
        virtual_acc = risk.state.get('virtual_account', {})
        
        # 虚拟账户数据
        initial_balance = virtual_acc.get('initial_balance', 10000.0)
        current_balance = virtual_acc.get('balance', initial_balance)
        total_pnl = virtual_acc.get('total_pnl', 0.0)
        total_fees = virtual_acc.get('total_fees', 0.0)
        trade_count = virtual_acc.get('trade_count', 0)
        
        # 计算总收益率
        total_return_pct = (total_pnl / initial_balance) * 100 if initial_balance > 0 else 0
        
        # 统计获胜/失败次数
        wins = sum(1 for t in trade_history if t.get('pnl_amount', 0) > 0)
        losses = sum(1 for t in trade_history if t.get('pnl_amount', 0) <= 0)
        total_closed = wins + losses
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0
        
        # 计算最大单笔盈亏
        if trade_history:
            pnl_list = [t.get('pnl_amount', 0) for t in trade_history]
            max_profit = max(pnl_list) if pnl_list else 0
            max_loss = min(pnl_list) if pnl_list else 0
            avg_trade = sum(pnl_list) / len(pnl_list) if pnl_list else 0
            
            # 计算盈亏比
            avg_win = sum(p for p in pnl_list if p > 0) / wins if wins > 0 else 0
            avg_loss = abs(sum(p for p in pnl_list if p <= 0) / losses) if losses > 0 else 1
            profit_factor = avg_win / avg_loss if avg_loss > 0 else float('inf')
        else:
            max_profit = max_loss = avg_trade = profit_factor = 0
        
        # 盈亏 emoji
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        
        report = f"{pnl_emoji} **交易绩效报告**\n\n"
        
        # 虚拟账户概览
        report += "💰 **模拟账户统计**\n"
        report += f"├─ 初始资金: `{initial_balance:.2f} USDT`\n"
        report += f"├─ 当前资金: `{current_balance:.2f} USDT`\n"
        report += f"├─ 累计盈亏: `{total_pnl:+.2f} USDT` ({total_return_pct:+.2f}%)\n"
        report += f"└─ 累计手续费: `{total_fees:.2f} USDT`\n\n"
        
        # 交易统计
        report += "📊 **交易统计**\n"
        report += f"├─ 总交易次数: `{trade_count}`\n"
        report += f"├─ 已平仓交易: `{total_closed}`\n"
        report += f"├─ 盈利次数: `{wins}` ✅\n"
        report += f"├─ 亏损次数: `{losses}` ❌\n"
        report += f"├─ 胜率: `{win_rate:.1f}%`\n"
        if profit_factor > 0:
            report += f"└─ 盈亏比: `{profit_factor:.2f}`\n\n"
        else:
            report += "\n"
        
        # 盈亏分析
        if trade_history:
            report += "📈 **盈亏分析**\n"
            report += f"├─ 最大单笔盈利: `{max_profit:+.2f} USDT`\n"
            report += f"├─ 最大单笔亏损: `{max_loss:+.2f} USDT`\n"
            report += f"└─ 平均单笔盈亏: `{avg_trade:+.2f} USDT`\n\n"
        
        # 当前持仓浮动盈亏
        positions = risk.state.get('positions', {})
        if positions:
            unrealized_pnl = 0
            for symbol, pos in positions.items():
                try:
                    ticker = exchange.fetch_ticker(symbol)
                    current_price = ticker['last']
                    unrealized_pnl += (current_price - pos['entry_price']) * pos['amount']
                except:
                    pass
            if unrealized_pnl != 0:
                unreal_emoji = "🟢" if unrealized_pnl >= 0 else "🔴"
                report += f"{unreal_emoji} **未实现盈亏**\n"
                report += f"└─ 当前持仓浮动: `{unrealized_pnl:+.2f} USDT`\n\n"
        
        # 熔断状态
        is_fused = risk.state.get('is_fused', False)
        fuse_status = "🚨 已熔断" if is_fused else "✅ 正常运行"
        report += f"🛡️ **系统状态: {fuse_status}**"
        
        bot.edit_message_text(report, chat_id=loading_msg.chat.id, message_id=loading_msg.message_id, parse_mode='Markdown')
        
    except Exception as e:
        error_msg = f"⚠️ **获取绩效报告失败**\n```\n{str(e)[:200]}\n```"
        try:
            bot.edit_message_text(error_msg, chat_id=loading_msg.chat.id, message_id=loading_msg.message_id, parse_mode='Markdown')
        except:
            bot.reply_to(message, error_msg, parse_mode='Markdown')

# --- 9. 配置管理指令 (/config) ---
@bot.message_handler(commands=['config'])
def manage_config(message):
    if not auth(message): 
        bot.reply_to(message, "🚫 无权访问")
        return
    
    try:
        parts = message.text.split()
        
        # 无参数：显示当前配置
        if len(parts) == 1:
            config_report = "⚙️ **当前配置参数**\n\n"
            
            # 全局设置
            config_report += "🔹 **全局设置**\n"
            config_report += f"├─ LIVE_TRADE: `{'实盘 ✅' if config.LIVE_TRADE else '模拟 📊'}`\n"
            config_report += f"├─ SYMBOLS: `{', '.join(config.SYMBOLS)}`\n"
            config_report += f"├─ TIMEFRAME: `{config.TIMEFRAME}`\n"
            config_report += f"├─ MAX_EXPOSURE: `{config.MAX_TOTAL_EXPOSURE*100:.0f}%`\n"
            config_report += f"├─ DRAWDOWN_FUSE: `{config.DRAWDOWN_FUSE*100:.0f}%`\n"
            config_report += f"└─ FUSE_DURATION: `{config.FUSE_DURATION/3600:.1f}小时`\n\n"
            
            # 币种特定配置
            config_report += "🔹 **币种策略配置**\n"
            for symbol, cfg in config.STRATEGY_CONFIG.items():
                config_report += f"\n📌 **{symbol}**\n"
                config_report += f"├─ 交易金额: `{cfg.get('trade_amount')} USDT`\n"
                config_report += f"├─ 止损比例: `{cfg.get('stop_loss_pct')*100:.1f}%`\n"
                config_report += f"├─ ADX阈值: `{cfg.get('adx_threshold')}`\n"
                config_report += f"└─ RSI超卖/超买: `{cfg.get('rsi_oversold')}` / `{cfg.get('rsi_overbought')}`\n"
            
            config_report += "\n💡 **修改配置**\n"
            config_report += "格式: `/config [参数名] [新值]`\n"
            config_report += "例如: `/config LIVE_TRADE True`"
            
            bot.reply_to(message, config_report, parse_mode='Markdown')
            return
        
        # 有参数：修改配置
        if len(parts) >= 3:
            param_name = parts[1].upper()
            param_value = ' '.join(parts[2:])
            
            # 可修改的全局配置项
            bool_params = ['LIVE_TRADE']
            float_params = ['MAX_TOTAL_EXPOSURE', 'DRAWDOWN_FUSE']
            int_params = ['FUSE_DURATION']
            
            # 处理布尔值
            if param_name in bool_params:
                if param_value.lower() in ['true', '1', 'yes', 'on']:
                    new_value = True
                elif param_value.lower() in ['false', '0', 'no', 'off']:
                    new_value = False
                else:
                    bot.reply_to(message, 
                        "⚠️ **格式错误**\n"
                        "布尔值参数请使用:\n"
                        "`True` / `False` / `yes` / `no` / `on` / `off` / `1` / `0`", 
                        parse_mode='Markdown')
                    return
                
                # 特殊处理LIVE_TRADE（需要更谨慎）
                if param_name == 'LIVE_TRADE':
                    if new_value and not config.LIVE_TRADE:
                        bot.reply_to(message, 
                            "🚨 **警告：即将切换到实盘模式！**\n\n"
                            "⚠️ 请确认：\n"
                            "• API密钥已正确配置\n"
                            "• 了解实盘交易风险\n\n"
                            "发送 `/config LIVE_TRADE CONFIRM` 确认切换。", 
                            parse_mode='Markdown')
                        return
                    elif not new_value and config.LIVE_TRADE:
                        config.LIVE_TRADE = False
                        bot.reply_to(message, 
                            "✅ **已切换回模拟交易模式**\n\n"
                            "📊 系统将使用虚拟资金进行测试", 
                            parse_mode='Markdown')
                        return
                    elif new_value and param_value.upper() == 'CONFIRM':
                        config.LIVE_TRADE = True
                        bot.reply_to(message, 
                            "✅ **已确认切换到实盘交易模式！**\n\n"
                            "⚠️ 请谨慎操作，建议先小额测试\n"
                            "📈 实盘交易已激活", 
                            parse_mode='Markdown')
                        return
                
                setattr(config, param_name, new_value)
                bot.reply_to(message, 
                    f"✅ **配置已更新**\n\n"
                    f"`{param_name} = {new_value}`", 
                    parse_mode='Markdown')
                return
            
            # 处理浮点参数
            elif param_name in float_params:
                try:
                    new_value = float(param_value)
                    if param_name in ['MAX_TOTAL_EXPOSURE', 'DRAWDOWN_FUSE']:
                        # 用户可能输入百分比，转换为小数
                        if new_value > 1:
                            new_value = new_value / 100
                    setattr(config, param_name, new_value)
                    bot.reply_to(message, 
                        f"✅ **配置已更新**\n\n"
                        f"`{param_name} = {new_value}`\n"
                        f"({'{:.1%}'.format(new_value) if new_value < 1 else new_value})", 
                        parse_mode='Markdown')
                except ValueError:
                    bot.reply_to(message, 
                        "⚠️ **格式错误**\n"
                        "参数值必须是数字", 
                        parse_mode='Markdown')
                return
            
            # 处理整数参数
            elif param_name in int_params:
                try:
                    new_value = int(param_value)
                    if param_name == 'FUSE_DURATION' and new_value < 3600:
                        new_value = new_value * 3600  # 用户可能输入小时
                    setattr(config, param_name, new_value)
                    bot.reply_to(message, 
                        f"✅ **配置已更新**\n\n"
                        f"`{param_name} = {new_value}`", 
                        parse_mode='Markdown')
                except ValueError:
                    bot.reply_to(message, 
                        "⚠️ **格式错误**\n"
                        "参数值必须是整数", 
                        parse_mode='Markdown')
                return
            
            else:
                bot.reply_to(message, 
                    f"⚠️ **未知参数: `{param_name}`**\n\n"
                    f"可修改的参数:\n"
                    f"• 布尔: `{', '.join(bool_params)}`\n"
                    f"• 小数: `{', '.join(float_params)}`\n"
                    f"• 整数: `{', '.join(int_params)}`", 
                    parse_mode='Markdown')
                return
        
        bot.reply_to(message, 
            "⚠️ **格式错误**\n\n"
            "使用 `/config` 查看配置\n"
            "或 `/config [参数名] [新值]` 修改", 
            parse_mode='Markdown')
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ **配置操作失败**\n```\n{str(e)[:200]}\n```", parse_mode='Markdown')

# 启动监听
def start_remote_listener():
    # 进程锁：防止多个实例同时运行
    lock_file = "telegram_bot.lock"
    
    try:
        # 检查锁文件
        if os.path.exists(lock_file):
            logger.warning("⚠️ 检测到其他 bot 实例正在运行，跳过启动")
            return
        
        # 创建锁文件
        with open(lock_file, 'w') as f:
            f.write(str(os.getpid()))
        
        logger.info("📡 远程调参监听器已启动...")
        
        # 使用 webhook 模式避免轮询冲突
        try:
            # 先停止任何现有的轮询
            bot.stop_polling()
            # 使用非阻塞轮询模式
            bot.polling(none_stop=True, interval=3, timeout=60)
        except Exception as e:
            logger.error(f"Telegram bot polling error: {e}")
            # 如果出错，等待后重试
            time.sleep(5)
            start_remote_listener()
            
    except KeyboardInterrupt:
        logger.info("👋 Bot 监听器已停止")
    finally:
        # 清理锁文件
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except:
                pass
