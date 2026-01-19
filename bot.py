import os
import sys
import json
import time
import math
import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
import pytz
from dotenv import load_dotenv
from binance.spot import Spot as Client
from binance.error import ClientError

# ==================================================================================
# CONFIGURATION
# ==================================================================================
# ==================================================================================
# CONFIGURATION
# ==================================================================================
# Explicit load to handle non-interactive shells or here-docs correctly
ENV_PATH = os.path.join(os.getcwd(), ".env")
load_dotenv(dotenv_path=ENV_PATH)

def get_env(key, default, cast_func=str):
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return cast_func(val)
    except ValueError:
        print(f"INVALID CONFIG: {key}={val}. USING DEFAULT: {default}")
        return default

# Trading Config
TRADE_VALUE_QUOTE = get_env("TRADE_VALUE_USDT", 10.0, float)
BUY_DROP_PCT = get_env("BUY_DROP_PCT", 0.01, float)
TAKE_PROFIT_PCT = get_env("TAKE_PROFIT_PCT", 0.012, float)
STOP_LOSS_PCT = get_env("STOP_LOSS_PCT", 0.02, float)

# Safety Config
# Backward compatibility for MAX_DAILY_LOSS_USDT
MAX_DAILY_LOSS_QUOTE = get_env("MAX_DAILY_LOSS_QUOTE", get_env("MAX_DAILY_LOSS_USDT", 2.0, float), float)
MAX_TRADES_PER_DAY = get_env("MAX_TRADES_PER_DAY", 10, int)
LOOP_INTERVAL_SECONDS = get_env("LOOP_INTERVAL_SECONDS", 15, int)
COOLDOWN_SECONDS = get_env("COOLDOWN_SECONDS", 120, int)
ERROR_LIMIT = get_env("ERROR_LIMIT", 5, int)
DRY_RUN = get_env("DRY_RUN", 1, int)
REQUIRE_START_CONFIRM = get_env("REQUIRE_START_CONFIRM", 1, int)
LIVE_TRADING = get_env("LIVE_TRADING", "NO")
ERROR_WINDOW_SECONDS = get_env("ERROR_WINDOW_SECONDS", 600, int)
MAX_SPREAD_PCT = get_env("MAX_SPREAD_PCT", 0.003, float)
MAX_SLIPPAGE_PCT = get_env("MAX_SLIPPAGE_PCT", 0.005, float)
MIN_NOTIONAL_BUFFER = get_env("MIN_NOTIONAL_BUFFER", 1.05, float)

# Execution Config (Option 2)
LIMIT_OFFSET_PCT = get_env("LIMIT_OFFSET_PCT", 0.001, float) # 0.1% offset
ORDER_TIMEOUT_SECONDS = get_env("ORDER_TIMEOUT_SECONDS", 60, int)
TREND_WINDOW_SAMPLES = get_env("TREND_WINDOW_SAMPLES", 60, int) # 60 * 15s = 15 mins
TREND_MIN_SAMPLES = get_env("TREND_MIN_SAMPLES", 30, int) # Need 30 samples to start trading
MIN_FILL_QUOTE = get_env("MIN_FILL_QUOTE", 5.0, float) # Min executed quote value to flip state

# Option 2.1: Reversal Gate
TREND_MODE = get_env("TREND_MODE", "REVERSAL") # STRICT or REVERSAL
REVERSAL_MODE = get_env("REVERSAL_MODE", "BOUNCE3") # CROSSUP or BOUNCE3
REVERSAL_SAMPLES = get_env("REVERSAL_SAMPLES", 3, int)
TREND_BLOCK_COOLDOWN_SECONDS = get_env("TREND_BLOCK_COOLDOWN_SECONDS", 300, int)
MIN_TREND_SPREAD_PCT = get_env("MIN_TREND_SPREAD_PCT", 0.0, float) # e.g. 0.001 for 0.1% buffer

TIMEZONE_STR = get_env("TIMEZONE", "Europe/Stockholm")
BASE_URL = get_env("BASE_URL", "https://api.binance.com")

SYMBOL = get_env("SYMBOL", "ETHUSDT")
QUOTE_ASSET = "USDT" # Default, updated dynamically in main
STATE_FILE = "bot_state.json"

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_activity.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ==================================================================================
# STATE MANAGEMENT
# ==================================================================================
class BotState:
    def __init__(self):
        self.state = "HOLDING_QUOTE" # or "HOLDING_ETH"
        self.entry_price = 0.0
        self.last_sell_price = 0.0
        self.pot_quote = 0.0
        self.pot_eth = 0.0
        self.daily_loss_quote = 0.0
        self.trade_count = 0
        self.day_key = ""
        self.last_trade_time = 0
        self.last_trade_time = 0
        self.error_timestamps = []
        self.error_timestamps = []
        self.price_history = [] # Now persistent
        
        # Option 2.1 Fields
        self.trend_block_until = 0
        self.last_sma = 0.0
        self.last_mid = 0.0

    def load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    
                    # Migration Logic: Map old USDT keys to Quote keys
                    self.state = data.get("state", "HOLDING_QUOTE")
                    if self.state == "HOLDING_USDT": self.state = "HOLDING_QUOTE"
                    
                    self.entry_price = data.get("entry_price", 0.0)
                    self.last_sell_price = data.get("last_sell_price", 0.0)
                    
                    # Pot Migration
                    if "pot_usdt" in data:
                        self.pot_quote = data.get("pot_usdt", 0.0)
                    else:
                        self.pot_quote = data.get("pot_quote", 0.0)
                        
                    self.pot_eth = data.get("pot_eth", 0.0)
                    
                    # Daily Loss Migration
                    if "daily_loss_usdt" in data:
                        self.daily_loss_quote = data.get("daily_loss_usdt", 0.0)
                    else:
                         self.daily_loss_quote = data.get("daily_loss_quote", 0.0)

                    self.trade_count = data.get("trade_count", 0)
                    self.day_key = data.get("day_key", "")
                    self.last_trade_time = data.get("last_trade_time", 0)
                    self.error_timestamps = data.get("error_timestamps", [])
                    
                    # Trend Data persistence (Cap to window size)
                    ph = data.get("price_history", [])
                    if len(ph) > TREND_WINDOW_SAMPLES:
                        ph = ph[-TREND_WINDOW_SAMPLES:]
                    self.price_history = ph
                    
                    # Option 2.1 State persistence
                    self.trend_block_until = data.get("trend_block_until", 0)
                    self.last_sma = data.get("last_sma", 0.0)
                    self.last_mid = data.get("last_mid", 0.0)
                    
                    logger.info("State loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")

    def save(self):
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.__dict__, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def check_daily_reset(self):
        tz = pytz.timezone(TIMEZONE_STR)
        now_date = datetime.now(tz).strftime("%Y-%m-%d")
        if now_date != self.day_key:
            logger.info(f"Daily Reset: New day {now_date}. Resetting daily counters.")
            self.daily_loss_quote = 0.0
            self.trade_count = 0
            self.day_key = now_date
            self.save()

# ==================================================================================
# HELPER FUNCTIONS
# ==================================================================================
def api_call(fn, *args, **kwargs):
    retries = 3
    delays = [1, 2, 4]
    for i in range(retries):
        try:
            return fn(*args, **kwargs)
        except ClientError as e:
            # FAIL FAST on Client Errors (4xx) - Do not retry permission/param errors
            # Codes: -2010 (Symbol Invalid), -1013 (Filter), -2011 (Unknown Order), etc.
            # HTTP 4XX usually means "Configuration Error" or "Account Restriction"
            if 400 <= e.status_code < 500:
                logger.error(f"API ClientError (Fatal - Not Retrying): {e}")
                # Provide Contextual Help
                if e.error_code == -2010:
                     logger.critical("FATAL: Account is restricted from trading this symbol (-2010).")
                raise e
            
            if i == retries - 1:
                logger.error(f"API Call Failed after {retries} attempts: {e}")
                raise e
            logger.warning(f"API Call retry {i+1}/{retries} ({e}). Waiting {delays[i]}s...")
            time.sleep(delays[i])
        except Exception as e:
            if i == retries - 1:
                logger.error(f"API Call Failed after {retries} attempts: {e}")
                raise e
            logger.warning(f"API Call retry {i+1}/{retries} ({e}). Waiting {delays[i]}s...")
            time.sleep(delays[i])

def get_mid_price_and_spread(client):
    try:
        ticker = api_call(client.book_ticker, SYMBOL)
        bid = float(ticker['bidPrice'])
        ask = float(ticker['askPrice'])
        if bid <= 0: return 0.0, 0.0
        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / bid
        return mid, spread_pct
    except Exception as e:
        logger.error(f"Error fetching ticker: {e}")
        return 0.0, 0.0

def print_pre_flight_check(client, bot):
    print("\n" + "="*60)
    print(" PRE-FLIGHT SAFETY CHECKLIST")
    print("="*60)
    
    # 1. Config
    print(f"[-] CONFIGURATION:")
    print(f"    DRY_RUN: {DRY_RUN}")
    print(f"    LIVE_TRADING: {LIVE_TRADING}")
    print(f"    REQUIRE_START_CONFIRM: {REQUIRE_START_CONFIRM}")
    print(f"    SYMBOL: {SYMBOL}")
    print(f"    QUOTE_ASSET: {QUOTE_ASSET}")
    print(f"    TRADE_VALUE_QUOTE: {TRADE_VALUE_QUOTE}")
    print(f"    MAX_DAILY_LOSS_QUOTE: {MAX_DAILY_LOSS_QUOTE}")
    print(f"    MAX_TRADES_PER_DAY: {MAX_TRADES_PER_DAY}")
    print(f"    COOLDOWN_SECONDS: {COOLDOWN_SECONDS}")

    # 2. Permissions & Balances
    print(f"[-] ACCOUNT:")
    try:
        acc = api_call(client.account)
        print(f"    Permissions: {acc.get('permissions')}")
        print(f"    Can Trade: {acc.get('canTrade')}")
        
        # Determine Base (Usually ETH) from Symbol?
        # Assuming Standard Pair Base+Quote
        base_asset = SYMBOL.replace(QUOTE_ASSET, "")
        
        base_bal = next((b for b in acc['balances'] if b['asset'] == base_asset), {'free': '0.0'})
        quote_bal = next((b for b in acc['balances'] if b['asset'] == QUOTE_ASSET), {'free': '0.0'})
        print(f"    {base_asset} Balance: {base_bal['free']} (Free)")
        print(f"    {QUOTE_ASSET} Balance: {quote_bal['free']} (Free)")
    except Exception as e:
        print(f"    ERROR FETCHING ACCOUNT INFO: {e}")
        sys.exit(1)

    # 3. State
    print(f"[-] BOT STATE:")
    print(f"    State: {bot.state}")
    print(f"    Pot {QUOTE_ASSET}: {bot.pot_quote}")
    print(f"    Pot ETH: {bot.pot_eth}")
    print(f"    Last Sell Price: {bot.last_sell_price}")
    print(f"    Entry Price: {bot.entry_price}")
    print(f"    Day Key: {bot.day_key}")
    print(f"    Last Trade Time: {bot.last_trade_time}")
    
    # 4. Phase Verification
    print("-" * 60)
    if DRY_RUN != 0:
        print("!!! DRY_RUN IS ACTIVE (non-zero). ABORTING PHASE 2 START !!!")
        print("To go live, set DRY_RUN=0 in .env")
        sys.exit(1)
        
    if LIVE_TRADING != "YES":
        print("!!! LIVE_TRADING IS NOT 'YES'. EXITING !!!")
        sys.exit(1)
        
    print("✅ PHASE 2: LIVE TRADING MODE VERIFIED.")
    print("="*60 + "\n")

def probe_symbol_permission(client):
    """
    Probes permissions by placing (and cancelling) a REAL LIMIT order.
    This is necessary because /order/test does not check Account Permissions (-2010).
    """
    logger.info(f"PROBING REAL ORDER PERMISSION for {SYMBOL}...")
    try:
        # 1. Fetch Filters & Price
        info = api_call(client.exchange_info, symbol=SYMBOL)
        filters = info['symbols'][0]['filters']
        
        f_price = next(f for f in filters if f['filterType'] == 'PRICE_FILTER')
        tick_size = Decimal(f_price['tickSize'])
        
        f_lot = next(f for f in filters if f['filterType'] == 'LOT_SIZE')
        step_size = Decimal(f_lot['stepSize'])
        min_qty = Decimal(f_lot['minQty'])
        
        f_not = next((f for f in filters if f['filterType'] == 'NOTIONAL'), None)
        if not f_not: f_not = next((f for f in filters if f['filterType'] == 'MIN_NOTIONAL'), None)
        min_notional = Decimal(f_not['minNotional'])
        
        ticker = api_call(client.book_ticker, SYMBOL)
        ask_price = Decimal(ticker['askPrice'])
        
        # 2. Construct Safe Params (LIMIT SELL PROBE)
        # Using a "Realistic" price to avoid PERCENT_PRICE filter errors
        # Sell @ Bid * 1.002 (0.2% above bid)
        ticker = api_call(client.book_ticker, SYMBOL)
        bid = Decimal(ticker['bidPrice'])
        limit_price_raw = bid * Decimal("1.002")
        limit_price = (limit_price_raw // tick_size) * tick_size
        
        # Qty > Min Notional with buffer
        target_val = max(min_notional * Decimal("1.2"), Decimal("11.0"))
        qty_raw = target_val / limit_price
        qty = (qty_raw // step_size) * step_size
        if qty < min_qty: qty = min_qty
        
        # Precision Formatting
        p_str = "{:f}".format(limit_price)
        q_str = "{:f}".format(qty)
        
        params = {
            "symbol": SYMBOL,
            "side": "SELL",
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": q_str,
            "price": p_str
        }
        
        logger.info(f"Probe Params: {params}")

        # 3. Test Order First
        api_call(client.new_order_test, **params)
        
        # 4. Real Order
        logger.info("Placing REAL PROBE order (will cancel immediately)...")
        order = api_call(client.new_order, **params)
        oid = order['orderId']
        logger.info(f"REAL PROBE PLACED (ID: {oid}). Cancelling...")
        
        # 5. Cancel
        api_call(client.cancel_order, symbol=SYMBOL, orderId=oid)
        logger.info(f"✅ REAL ORDER PROBE PASSED: Account can trade {SYMBOL}.")
        
    except ClientError as e:
        logger.critical(f"❌ PROBE FAILED: {e}")
        if e.error_code == -2010:
            print("\n" + "!"*60) 
            print(f"FATAL ERROR: Your account is RESTRICTED from trading {SYMBOL} (Error -2010).")
            print(f"SUGGESTION: Edit .env and change SYMBOL to a permitted pair (e.g. ETHEUR).")
            print("!"*60 + "\n")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ PROBE UNEXPECTED ERROR: {e}")
        sys.exit(1)

def get_filters(client):
    try:
        info = api_call(client.exchange_info, symbol=SYMBOL)
        f = info['symbols'][0]['filters']
        
        # LOT_SIZE -> stepSize
        lot_filter = next((x for x in f if x['filterType'] == 'LOT_SIZE'), None)
        if not lot_filter:
            logger.critical("CRITICAL: LOT_SIZE filter not found for symbol.")
            sys.exit(1)
        step_size_str = lot_filter['stepSize']
        step_size_decimal = Decimal(step_size_str)
        
        # MIN_NOTIONAL
        notional_filter = next((x for x in f if x['filterType'] in ['NOTIONAL', 'MIN_NOTIONAL']), None)
        if not notional_filter:
            logger.critical("CRITICAL: MIN_NOTIONAL filter not found.")
            sys.exit(1)
        min_notional_decimal = Decimal(notional_filter['minNotional'])
        
        # PRICE_FILTER -> tickSize
        price_filter = next((x for x in f if x['filterType'] == 'PRICE_FILTER'), None)
        if not price_filter:
            logger.critical("CRITICAL: PRICE_FILTER not found.")
            sys.exit(1)
        tick_size_decimal = Decimal(price_filter['tickSize'])
        
        # PERCENT_PRICE_BY_SIDE (or PERCENT_PRICE)
        # We want multiplierUp and multiplierDown
        # If not present, default to wide range
        pct_filter = next((x for x in f if x['filterType'] == 'PERCENT_PRICE_BY_SIDE'), None)
        if not pct_filter:
             pct_filter = next((x for x in f if x['filterType'] == 'PERCENT_PRICE'), None)
             
        if pct_filter:
            mul_up = Decimal(pct_filter.get('multiplierUp', '5')) # Default 5x if missing?
            mul_down = Decimal(pct_filter.get('multiplierDown', '0.2'))
        else:
            mul_up = Decimal("5.0")
            mul_down = Decimal("0.2")

        # logger.info(f"Filters Loaded: step={step_size_str}, tick={tick_size_decimal}, notional={min_notional_decimal}, mulUp={mul_up}, mulDown={mul_down}")
        return step_size_decimal, step_size_str, tick_size_decimal, min_notional_decimal, mul_up, mul_down

    except Exception as e:
        logger.critical(f"Error fetching filters: {e}")
        sys.exit(1)

def round_down_step(quantity, step_size_decimal):
    d_qty = Decimal(str(quantity))
    if step_size_decimal == 0: return Decimal("0")
    # quantize logic: derive precision or just use floor div?
    # Floor div is safest for step size alignment: (qty // step) * step
    return (d_qty // step_size_decimal) * step_size_decimal

def get_precision_from_step_str(step_size_str):
    # Examples:
    # "0.00010000" -> s="0.0001" -> 4
    # "1.00000000" -> s="1." -> 0
    # "0.01000000" -> s="0.01" -> 2
    # "0.00500000" -> s="0.005" -> 3
    s = step_size_str.rstrip('0')
    if s.endswith('.'): s = s[:-1]
    if "." not in s: return 0
    return len(s.split(".")[1])

def get_avg_exec_price(fills):
    if not fills: return 0.0
    total_qty = 0.0
    total_cost = 0.0
    for fill in fills:
        qty = float(fill['qty'])
        price = float(fill['price'])
        total_qty += qty
        total_cost += (qty * price)
    if total_qty == 0: return 0.0
    return total_cost / total_qty

def check_errors(bot_state):
    now = time.time()
    # Remove old errors
    original_len = len(bot_state.error_timestamps)
    bot_state.error_timestamps = [t for t in bot_state.error_timestamps if now - t < ERROR_WINDOW_SECONDS]
    current_len = len(bot_state.error_timestamps)
    
    if current_len > 0:
        logger.warning(f"Error Count: {current_len}/{ERROR_LIMIT} in last {ERROR_WINDOW_SECONDS}s")

    if current_len >= ERROR_LIMIT:
        logger.critical(f"STOPPING: Error limit reached ({current_len} in {ERROR_WINDOW_SECONDS}s).")
        sys.exit(1)
        
    # Only save if list changed
    if current_len != original_len:
        bot_state.save()

def normalize_pots(bot_state, step_size_decimal):
    # Normalize ETH
    d_eth = Decimal(str(bot_state.pot_eth))
    # Using small dust threshold
    if abs(d_eth) < step_size_decimal and d_eth != 0:
        logger.info(f"Normalizing Dust Pot ETH: {bot_state.pot_eth:.8f} -> 0.0")
        bot_state.pot_eth = 0.0

    if bot_state.pot_eth < 0:
        logger.warning(f"Clamping negative Pot ETH: {bot_state.pot_eth:.8f} -> 0.0")
        bot_state.pot_eth = 0.0

    # Normalize QUOTE
    d_quote = Decimal(str(bot_state.pot_quote))
    # Using 0.01 as a safe lower bound for generic fiat/stablecoin dust
    if abs(d_quote) < Decimal("0.01") and d_quote != 0:
        logger.info(f"Normalizing Dust Pot {QUOTE_ASSET}: {bot_state.pot_quote:.8f} -> 0.0")
        bot_state.pot_quote = 0.0

    if bot_state.pot_quote < 0:
        logger.warning(f"Clamping negative Pot {QUOTE_ASSET}: {bot_state.pot_quote:.8f} -> 0.0")
        bot_state.pot_quote = 0.0

def safe_execution_checks(quantity_decimal, mid_decimal, min_notional_decimal, target_notional_decimal):
    # Notional Check
    notional_val = quantity_decimal * mid_decimal
    
    # Check 1: Absolute Min Notional
    if notional_val < min_notional_decimal:
         logger.warning(f"SKIPPING: Notional {notional_val:.2f} < MinNotional {min_notional_decimal:.2f}")
         return False

    # Check 2: Target Notional Buffer
    if notional_val < target_notional_decimal * Decimal("0.95"):
        logger.warning(f"SKIPPING: Notional {notional_val:.2f} < TargetNotional Buffer {target_notional_decimal * Decimal('0.95'):.2f}")
        return False

    return True

def calculate_sma(prices):
    if not prices: return 0.0
    return sum(prices) / len(prices)

def should_apply_trend_block(bot_state, now_time):
    if now_time < bot_state.trend_block_until:
        return True
    return False

def set_trend_block(bot_state, now_time):
    bot_state.trend_block_until = now_time + TREND_BLOCK_COOLDOWN_SECONDS
    bot_state.save()

def is_reversal_confirmed(price_history, current_price, current_sma, prev_price, prev_sma):
    # Returns (bool, reason_string)
    
    # 1. Warmup Check (Should be handled by caller but safe to check)
    if not price_history or len(price_history) < TREND_MIN_SAMPLES:
        return False, "WARMUP"
        
    # 2. Strict Mode
    if TREND_MODE == "STRICT":
        # Price must be > SMA
        required_price = current_sma * (1 + MIN_TREND_SPREAD_PCT)
        if current_price > required_price:
            return True, "STRICT_OK"
        else:
            return False, "STRICT_BLOCKED"
            
    # 3. Reversal Mode
    if TREND_MODE == "REVERSAL":
        if REVERSAL_MODE == "CROSSUP":
            # Crossed above SMA from below?
            # Or just currently above? Usually "CrossUp" implies event, but for gating "currently above" is safer?
            # User Prompt: "Confirm only if: prev_price <= prev_sma AND current_price > current_sma"
            # This is a strict crossover event.
            req_curr = current_sma * (1 + MIN_TREND_SPREAD_PCT)
            if prev_price <= prev_sma and current_price > req_curr:
                return True, "REVERSAL_CROSSUP_OK"
            return False, "REVERSAL_CROSSUP_BLOCKED"

        elif REVERSAL_MODE == "BOUNCE3":
            # Confirm if last N samples are rising
            N = REVERSAL_SAMPLES
            if len(price_history) < N:
                 return False, "WARMUP_BOUNCE"
            
            # Check last N samples rising: p[-1] > p[-2] > ...
            # price_history[-1] is current_price (if appended already? Yes, usually appended before check)
            # Let's assume price_history[-1] is the LATEST
            subset = price_history[-N:]
            
            # Check strictly increasing
            is_rising = all(subset[i] < subset[i+1] for i in range(len(subset)-1))
            
            if is_rising:
                return True, "REVERSAL_BOUNCE3_OK"
            else:
                return False, "REVERSAL_BOUNCE3_BLOCKED"
                
    return False, "UNKNOWN_MODE"

# ==================================================================================
# TRADING ACTIONS
# ==================================================================================
def place_limit_order_with_timeout(client, side, quantity_decimal, step_size_decimal, step_size_str, tick_size_decimal, estimated_price_decimal, avg_price_cap_min=None, avg_price_cap_max=None, timeout=ORDER_TIMEOUT_SECONDS):
    try:
        # 1. Quantize Quantity
        if step_size_decimal == 0:
            final_qty = quantity_decimal
        else:
            final_qty = (quantity_decimal // step_size_decimal) * step_size_decimal

        if final_qty <= 0:
             logger.warning(f"Quantity rounded to 0. Order aborted.")
             return None

        # Format Qty
        precision_q = get_precision_from_step_str(step_size_str)
        if precision_q == 0:
            quant_exp_q = Decimal("1")
        else:
            quant_exp_q = Decimal("1e-" + str(precision_q))
        final_qty_quant = final_qty.quantize(quant_exp_q, rounding=ROUND_DOWN)
        qty_str = "{:f}".format(final_qty_quant)
        
        # 2. Calculate LIMIT Price
        # BUY: price = mid * (1 - offset)
        # SELL: price = mid * (1 + offset)
        offset = Decimal(str(LIMIT_OFFSET_PCT))
        if side == 'BUY':
             final_price = estimated_price_decimal * (Decimal("1") - offset)
        else:
             final_price = estimated_price_decimal * (Decimal("1") + offset)
             
        # CLAMP Price (Percent Price Filter)
        if avg_price_cap_min and final_price < avg_price_cap_min:
             logger.warning(f"Limit Price {final_price:.2f} clamped to Min {avg_price_cap_min:.2f}")
             final_price = avg_price_cap_min
        if avg_price_cap_max and final_price > avg_price_cap_max:
             logger.warning(f"Limit Price {final_price:.2f} clamped to Max {avg_price_cap_max:.2f}")
             final_price = avg_price_cap_max
             
        # Round to tickSize
        if tick_size_decimal > 0:
            final_price = (final_price // tick_size_decimal) * tick_size_decimal
            
        p_str = "{:f}".format(final_price)

        # Log Intent
        log_msg = f"{side} LIMIT {qty_str} @ {p_str} (Offset {LIMIT_OFFSET_PCT*100:.2f}%)"
        logger.info(f"PREPARING: {log_msg}")

        if DRY_RUN == 1:
            logger.info(f"DRY_RUN: Simulated {log_msg}")
            return {
                "status": "FILLED",
                "cummulativeQuoteQty": str(final_qty_quant * final_price),
                "executedQty": str(final_qty_quant),
                "fills": [{"qty": str(final_qty_quant), "price": str(final_price)}]
            }

        # LIVE ORDER
        if DRY_RUN == 0:
             logger.critical(f"🚀 SENDING LIVE {log_msg}")
        
        # Place LIMIT Order
        order = api_call(
            client.new_order,
            symbol=SYMBOL,
            side=side,
            type='LIMIT',
            timeInForce='GTC',
            quantity=qty_str,
            price=p_str
        )
        oid = order['orderId']
        logger.info(f"ORDER PLACED (ID: {oid}). Waiting {timeout}s...")
        
        # POLL LOOP
        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(1) # Poll every 1s (User requested 1s)
            
            # Fetch status
            o_stat = api_call(client.get_order, symbol=SYMBOL, orderId=oid)
            status = o_stat['status']
            
            if status == 'FILLED':
                logger.info(f"ORDER FILLED (ID: {oid}).")
                return o_stat
            
            if status in ['CANCELED', 'REJECTED', 'EXPIRED']:
                 logger.warning(f"Order {status} externally (ID: {oid}).")
                 # Check executedQty just in case
                 exec_qty = float(o_stat.get('executedQty', 0.0))
                 if exec_qty > 0: return o_stat
                 return None 
        
        # TIMEOUT REACHED
        logger.warning(f"TIMEOUT reached ({timeout}s). Cancelling order {oid}...")
        try:
            api_call(client.cancel_order, symbol=SYMBOL, orderId=oid)
            # Check final status
            final_stat = api_call(client.get_order, symbol=SYMBOL, orderId=oid)
            exec_qty = float(final_stat.get('executedQty', 0.0))
            if exec_qty > 0:
                logger.info(f"Partial fill detected on cancel: {exec_qty}")
                return final_stat
            return None
            
        except Exception as e:
            logger.error(f"Error cancelling timed-out order: {e}")
            return None # Ambiguous
            
    except ClientError as e:
        logger.error(f"Binance API Error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected Error placing limit order: {e}")
        return None

def fund_pot_if_needed(client, bot_state, target_notional, step_size_decimal, step_size_str, min_notional_decimal):
    # If pot is already sufficient (with small buffer 0.95 to avoid tiny top-ups)
    if bot_state.pot_quote >= float(target_notional * Decimal("0.95")):
        return

    logger.info(f"Funding Pot: Current Pot {bot_state.pot_quote:.2f} < Target {target_notional:.2f}")
    
    # Need to buy QUOTE (Sell ETH)
    needed_quote = target_notional - Decimal(str(bot_state.pot_quote))
    mid, _ = get_mid_price_and_spread(client)
    if mid == 0: return
    mid_decimal = Decimal(str(mid))

    est_eth_needed_raw = needed_quote / mid_decimal
    # Round UP slightly for fee buffer and step size
    est_eth_needed_raw *= Decimal("1.01")
    
    # Check available ETH balance
    try:
        acc = api_call(client.account)
        eth_bal = next((b for b in acc['balances'] if b['asset'] == 'ETH'), {'free': '0.0'})
        free_eth = Decimal(eth_bal['free'])
    except Exception as e:
        logger.error(f"Failed to fetch account balance: {e}")
        return

    qty_to_sell = round_down_step(est_eth_needed_raw, step_size_decimal)
    
    # Check checks
    if qty_to_sell <= 0:
        logger.warning("Funding quantity rounds to 0. Skipping.")
        return

    # Check Min Notional for this specific funding trade
    notional_val = qty_to_sell * mid_decimal
    
    if notional_val < min_notional_decimal:
        logger.info(f"Funding sell ({notional_val:.2f}) < min_notional ({min_notional_decimal:.2f}). Boosting to target_notional.")
        qty_to_sell = round_down_step(target_notional / mid_decimal, step_size_decimal)

    if qty_to_sell > free_eth:
        logger.critical(f"STOPPING: Insufficient ETH to fund pot. Need {qty_to_sell}, Have {free_eth}")
        sys.exit(1)

    # EXECUTE FUNDING SELL
    logger.info(f"Executing FUNDING SELL: {qty_to_sell} ETH")
    # Ticker needed for tick_size
    info = api_call(client.exchange_info, symbol=SYMBOL)
    filters = info['symbols'][0]['filters']
    f_price = next(f for f in filters if f['filterType'] == 'PRICE_FILTER')
    tick_size = Decimal(f_price['tickSize'])
    
    order = place_limit_order_with_timeout(client, 'SELL', qty_to_sell, step_size_decimal, step_size_str, tick_size, mid_decimal)
    
    if order:
        cumm_quote_qty = float(order.get('cummulativeQuoteQty', 0.0))
        executed_qty = float(order.get('executedQty', 0.0))
        fills = order.get('fills', [])
        avg_price = get_avg_exec_price(fills)
        
        if avg_price == 0 and executed_qty > 0:
            avg_price = cumm_quote_qty / executed_qty

        # Pot Update
        bot_state.pot_quote += cumm_quote_qty
        
        bot_state.last_sell_price = avg_price # Set reference price
        bot_state.state = "HOLDING_QUOTE" # Ensure state matches the funding action
        
        if bot_state.last_sell_price <= 0:
            logger.critical("CRITICAL: Funding executed but last_sell_price is 0/invalid.")
            sys.exit(1)

        bot_state.last_trade_time = time.time()
        bot_state.save()
        logger.info(f"Pot Funded. New Pot: {bot_state.pot_quote:.2f} {QUOTE_ASSET}. Last Sell Price: {bot_state.last_sell_price}")
    else:
        bot_state.error_timestamps.append(time.time())
        check_errors(bot_state)

# ==================================================================================
# MAIN LOOP
# ==================================================================================
def main():
    print("=========================================================")
    print(f"    BINANCE SPOT TRADING BOT - {SYMBOL}")
    print(" SAFETY WARNING: SPOT ONLY. NO WITHDRAWALS. USE AT OWN RISK.")
    print(" LOGS: bot_activity.log")
    print("=========================================================")

    # SAFETY: LIVE TRADING GUARD
    if DRY_RUN == 0 and LIVE_TRADING != "YES":
        msg = "Live trading blocked. To enable: set DRY_RUN=0 and LIVE_TRADING=YES in .env"
        logger.critical(msg)
        print(msg)
        sys.exit(1)

    # API KEYS
    api_key = os.getenv("API_KEY")
    api_secret = os.getenv("API_SECRET")
    if not api_key or not api_secret:
        print("ERROR: API_KEY or API_SECRET missing in .env")
        sys.exit(1)

    # Allow custom base_url for Binance.US or Testnet
    client = Client(api_key, api_secret, base_url=BASE_URL)
    logger.info(f"Connected to Binance Node: {BASE_URL}")
    
    # DYNAMIC QUOTE ASSET RETRIEVAL
    # We must fetch this before printing pre-flight check
    try:
        info = api_call(client.exchange_info, symbol=SYMBOL)
        global QUOTE_ASSET
        QUOTE_ASSET = info['symbols'][0]['quoteAsset']
    except Exception as e:
        logger.critical(f"Failed to fetch Symbol Info for {SYMBOL}: {e}")
        sys.exit(1)
    
    bot = BotState()
    bot.load()
    
    # PRE-FLIGHT PRE-CHECK
    if LIVE_TRADING == "YES" and DRY_RUN == 0:
         print_pre_flight_check(client, bot)
         probe_symbol_permission(client)

    # STARTUP CONFIRMATION
    if REQUIRE_START_CONFIRM == 1:
        try:
            # Check basic conditions. We need filters to know min_notional.
            # Just do a quick loose check or fetch filters once.
            logger.info("Startup Safety Check...")
            _, _, _, min_notional_decimal, _, _ = get_filters(client)
            
            needs_funding = False
            if bot.state == "HOLDING_QUOTE" and Decimal(str(bot.pot_quote)) < min_notional_decimal:
                # Check ETH/BASE Asset
                acc = api_call(client.account)
                base_asset = SYMBOL.replace(QUOTE_ASSET, "")
                base_bal = next((b for b in acc['balances'] if b['asset'] == base_asset), {'free': '0.0'})
                if Decimal(base_bal['free']) > 0:
                    needs_funding = True

            if needs_funding:
                print("\n" + "!"*60)
                print(f"WARNING: Bot pot {QUOTE_ASSET} is low. Funding SELL of your {base_asset} may occur immediately.")
                print(f"Current Pot {QUOTE_ASSET}: {bot.pot_quote}")
                print("!"*60)
                user_in = input("Type 'I UNDERSTAND' to proceed, anything else to abort: ")
                if user_in.strip() != "I UNDERSTAND":
                    print("Aborted by user.")
                    sys.exit(0)
        except Exception as e:
            logger.error(f"Startup check failed: {e}")
            sys.exit(1)

    while True:
        try:
            # 0. Global Check
            bot.check_daily_reset()
            check_errors(bot)

            if bot.daily_loss_quote >= MAX_DAILY_LOSS_QUOTE:
                logger.critical(f"STOPPING: Max Daily Loss Reached ({bot.daily_loss_quote:.2f} >= {MAX_DAILY_LOSS_QUOTE})")
                sys.exit(0)

            if bot.trade_count >= MAX_TRADES_PER_DAY:
                logger.critical(f"STOPPING: Max Trades Reached ({bot.trade_count})")
                sys.exit(0)

            time.sleep(1) # Tiny pause

            # 1. Cooldown Check
            if time.time() - bot.last_trade_time < COOLDOWN_SECONDS:
                logging.debug("Cooldown active...")
                time.sleep(LOOP_INTERVAL_SECONDS)
                continue

            # 2. Get Exchange Filters
            step_size_decimal, step_size_str, tick_size_decimal, min_notional_decimal, mul_up, mul_down = get_filters(client)
            d_trade_val = Decimal(str(TRADE_VALUE_QUOTE))
            target_notional_decimal = max(d_trade_val, min_notional_decimal * Decimal(str(MIN_NOTIONAL_BUFFER)))

            # 3. Pot Check & Funding
            if bot.state == "HOLDING_QUOTE":
                if Decimal(str(bot.pot_quote)) < min_notional_decimal: 
                    # Try to fund it logic
                    fund_pot_if_needed(client, bot, target_notional_decimal, step_size_decimal, step_size_str, min_notional_decimal)
                    if Decimal(str(bot.pot_quote)) < min_notional_decimal:
                        logger.critical(f"STOPPING: Pot {QUOTE_ASSET} {bot.pot_quote:.2f} too small for min_notional {min_notional_decimal}. Funding failed or insufficient.")
                        sys.exit(1)

            # 4. Market Data
            current_price, spread = get_mid_price_and_spread(client)
            if current_price == 0:
                time.sleep(5)
                continue
            
            # 4b. Percent Price Limits (Clamp Calculation)
            # Up = Current * mulUp, Down = Current * mulDown
            # Technically should use AvgPrice, but Mid is close enough for Clamp Estimate
            avg_price_cap_max = Decimal(str(current_price)) * mul_up
            avg_price_cap_min = Decimal(str(current_price)) * mul_down

            # 4. Market Data
            current_price, spread = get_mid_price_and_spread(client)
            if current_price == 0:
                time.sleep(5)
                continue
            
            # 4b. Percent Price Limits (Clamp Calculation)
            avg_price_cap_max = Decimal(str(current_price)) * mul_up
            avg_price_cap_min = Decimal(str(current_price)) * mul_down

            # 4c. Trend Data Collection & Persistence
            # Store previous values for CrossUp logic
            prev_price = bot.price_history[-1] if bot.price_history else current_price
            prev_sma = calculate_sma(bot.price_history) # SMA before new price
            
            bot.price_history.append(current_price)
            if len(bot.price_history) > TREND_WINDOW_SAMPLES:
                 bot.price_history.pop(0) 
            
            # Option 2.1: Save last mid/sma for context
            bot.last_mid = current_price
            sma = calculate_sma(bot.price_history)
            bot.last_sma = sma
            bot.save()
            
            trend_ready = len(bot.price_history) >= TREND_MIN_SAMPLES
            
            # Spread Check
            if spread > MAX_SPREAD_PCT:
                logger.warning(f"Spread {spread*100:.3f}% > Limit {MAX_SPREAD_PCT*100:.3f}%. Waiting...")
                time.sleep(LOOP_INTERVAL_SECONDS)
                continue

            if not trend_ready:
                 logger.info(f"Collecting trend data... ({len(bot.price_history)}/{TREND_MIN_SAMPLES})")
            else:
                 # Enhanced Logging
                 if bot.state == "HOLDING_QUOTE":
                      disp_target = bot.last_sell_price * (1 - BUY_DROP_PCT)
                      logger.info(f"State: {bot.state} | Price: {current_price:.2f} | SMA: {sma:.2f} | DipTarget: {disp_target:.2f} | Pot: {bot.pot_quote:.2f} {QUOTE_ASSET}")
                 else:
                      logger.info(f"State: {bot.state} | Price: {current_price:.2f} | SMA: {sma:.2f} | Pot: {bot.pot_eth:.4f} ETH")

            # 5. Logic
            if bot.state == "HOLDING_QUOTE":
                # Signal: Price Drop
                target_buy_price = bot.last_sell_price * (1 - BUY_DROP_PCT)
                
                if bot.last_sell_price > 0:
                    # ONLY check logic if Dip Triggered
                    if current_price <= target_buy_price:
                        
                        # 1. Check Trend Block Cooldown
                        if should_apply_trend_block(bot, time.time()):
                             logger.info(f"BUY SKIPPED (Trend cooldown active until {bot.trend_block_until:.0f})")
                             time.sleep(LOOP_INTERVAL_SECONDS)
                             continue

                        # 2. Check Warmup
                        if not trend_ready:
                             logger.info(f"BUY SKIPPED (Trend warmup {len(bot.price_history)}/{TREND_MIN_SAMPLES})")
                             time.sleep(LOOP_INTERVAL_SECONDS)
                             continue
                        
                        # 3. Reversal Gate
                        is_ok, reason = is_reversal_confirmed(bot.price_history, current_price, sma, prev_price, prev_sma)
                        
                        if not is_ok:
                             logger.info(f"BUY SKIPPED (Trend Gate: {reason} | Price {current_price:.2f} | SMA {sma:.2f})")
                             set_trend_block(bot, time.time())
                             time.sleep(LOOP_INTERVAL_SECONDS)
                             continue

                        logger.info(f"BUY APPROVED ({reason} | Price {current_price:.2f} | SMA {sma:.2f} | DipTarget {target_buy_price:.2f})")
                        
                        # Calculate Qty
                        qty_raw = Decimal(str(bot.pot_quote)) / Decimal(str(current_price))
                        qty_to_buy = round_down_step(qty_raw * Decimal("0.99"), step_size_decimal)

                        if qty_to_buy <= 0:
                            logger.critical(f"STOPPING: BUY Quantity rounds to 0. Pot {QUOTE_ASSET} too small.")
                            sys.exit(1)
                            
                        # Notional check
                        d_mid = Decimal(str(current_price))
                        if qty_to_buy * d_mid < min_notional_decimal:
                            logger.critical(f"STOPPING: BUY Value < MinNotional. Pot too small.")
                            sys.exit(1)
                        
                        # Pre-Trade Safety
                        valid = safe_execution_checks(qty_to_buy, d_mid, min_notional_decimal, target_notional_decimal)
                        if not valid:
                            time.sleep(LOOP_INTERVAL_SECONDS)
                            continue

                        # Execute LIMIT BUY
                        order = place_limit_order_with_timeout(
                            client, 'BUY', qty_to_buy, step_size_decimal, step_size_str, 
                            tick_size_decimal, d_mid, avg_price_cap_min, avg_price_cap_max
                        )
                        
                        if order:
                            cumm_quote = float(order.get('cummulativeQuoteQty', 0.0))
                            exec_qty = float(order.get('executedQty', 0.0))
                            fills = order.get('fills', [])
                            avg_price = get_avg_exec_price(fills)
                            if avg_price == 0: avg_price = cumm_quote / exec_qty if exec_qty else current_price
                            
                            # State Update Logic (Partial Fill Handling)
                            bot.pot_quote -= cumm_quote
                            if bot.pot_quote < 0: bot.pot_quote = 0.0
                            bot.pot_eth += exec_qty
                            
                            normalize_pots(bot, step_size_decimal)
                            
                            # Flip State Check (Strict User Rule)
                            # BUY: if executed quote >= MIN_FILL_QUOTE AND executedQty >= step_size
                            if cumm_quote >= MIN_FILL_QUOTE and exec_qty >= float(step_size_decimal):
                                bot.entry_price = avg_price
                                bot.state = "HOLDING_ETH"
                                logger.info(f"BUY SUCCESS. Entry: {bot.entry_price:.2f}. New Pot: {bot.pot_eth:.4f} ETH")
                            else:
                                logger.warning(f"BUY PARTIAL/TINY ({cumm_quote:.2f} {QUOTE_ASSET}, Qty {exec_qty}). Staying in HOLDING_QUOTE.")

                            bot.last_trade_time = time.time()
                            bot.save()
                        else:
                            bot.error_timestamps.append(time.time())
                            check_errors(bot)

                else:
                    # Inconsistent state (last_sell_price == 0)
                    logger.warning("No last sell price found. Resetting anchor.")
                    bot.last_sell_price = current_price
                    bot.save()
                    time.sleep(LOOP_INTERVAL_SECONDS)
                    continue

            elif bot.state == "HOLDING_ETH":
                take_profit_price = bot.entry_price * (1 + TAKE_PROFIT_PCT)
                stop_loss_price = bot.entry_price * (1 - STOP_LOSS_PCT)
                
                signal_sell = False
                sell_reason = ""

                if current_price >= take_profit_price:
                    signal_sell = True
                    sell_reason = "TAKE_PROFIT"
                elif current_price <= stop_loss_price:
                    signal_sell = True
                    sell_reason = "STOP_LOSS"

                if signal_sell:
                    logger.info(f"SIGNAL: SELL ({sell_reason}) Price {current_price:.2f}")
                    
                    qty_to_sell = round_down_step(bot.pot_eth, step_size_decimal)
                    
                    if qty_to_sell <= 0:
                        logger.critical("STOPPING: SELL Quantity rounds to 0.")
                        sys.exit(1)

                    d_mid = Decimal(str(current_price))
                    if qty_to_sell * d_mid < min_notional_decimal:
                        logger.critical(f"STOPPING: SELL Value < MinNotional.")
                        sys.exit(1)

                    # Pre-Trade Safety
                    valid = safe_execution_checks(qty_to_sell, d_mid, min_notional_decimal, target_notional_decimal)
                    if not valid:
                         time.sleep(LOOP_INTERVAL_SECONDS)
                         continue
                    
                    # Execute LIMIT SELL
                    order = place_limit_order_with_timeout(
                        client, 'SELL', qty_to_sell, step_size_decimal, step_size_str, 
                        tick_size_decimal, d_mid, avg_price_cap_min, avg_price_cap_max
                    )
                    
                    if order:
                        cumm_quote = float(order.get('cummulativeQuoteQty', 0.0))
                        exec_qty = float(order.get('executedQty', 0.0))
                        fills = order.get('fills', [])
                        avg_price = get_avg_exec_price(fills)
                        if avg_price == 0: avg_price = cumm_quote / exec_qty if exec_qty else current_price
                        
                        # PnL
                        pnl_per_unit = avg_price - bot.entry_price
                        realized_pnl = pnl_per_unit * exec_qty
                        
                        if realized_pnl < 0:
                            bot.daily_loss_quote += abs(realized_pnl)
                            logger.info(f"LOSS REALIZED: {realized_pnl:.4f} {QUOTE_ASSET}.")

                        # State Update Logic
                        bot.pot_eth -= exec_qty
                        bot.pot_quote += cumm_quote
                        bot.last_sell_price = avg_price
                        
                        normalize_pots(bot, step_size_decimal)
                        
                        # Flip State Check (Strict User Rule)
                        # Only flip if BOTH: (exec_quote >= MIN_FILL_QUOTE) AND (remaining pot_eth is effectively 0)
                        
                        is_dust = False
                        if bot.pot_eth == 0:
                            is_dust = True
                        else:
                            # Check if remaining Notional is < minNotional
                            rem_val = float(bot.pot_eth) * float(current_price)
                            if rem_val < float(min_notional_decimal):
                                is_dust = True

                        if cumm_quote >= MIN_FILL_QUOTE and is_dust:
                             bot.state = "HOLDING_QUOTE"
                             bot.trade_count += 1
                             logger.info(f"SELL SUCCESS ({sell_reason}). Price: {avg_price:.2f}. PnL: {realized_pnl:.4f}. Trades Today: {bot.trade_count}")
                        else:
                             # We either didn't fill enough OR we still have a bag left.
                             logger.warning(f"SELL PARTIAL ({cumm_quote:.2f} {QUOTE_ASSET}). Dust/Rem: {is_dust}. Remain: {bot.pot_eth:.4f}. Staying in HOLDING_ETH.")

                        bot.last_trade_time = time.time()
                        bot.save()
                    else:
                        bot.error_timestamps.append(time.time())
                        check_errors(bot)

            time.sleep(LOOP_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print("\nBot stopped by user.")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Main Loop Error: {e}")
            bot.error_timestamps.append(time.time())
            check_errors(bot)
            time.sleep(5)

if __name__ == "__main__":
    main()
