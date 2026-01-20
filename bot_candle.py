import os
import sys
import json
import time
import math
import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import pytz
from dotenv import load_dotenv
from binance.spot import Spot as Client
from binance.error import ClientError

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
TRADE_VALUE_QUOTE = get_env("TRADE_VALUE_QUOTE", get_env("TRADE_VALUE_USDT", 10.0, float), float)
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
TREND_WINDOW_SAMPLES = get_env("TREND_WINDOW_SAMPLES", 60, int) # Candles for Reversal Logic
TREND_MIN_SAMPLES = get_env("TREND_MIN_SAMPLES", 30, int) # Need 30 samples to start trading
MIN_FILL_QUOTE = get_env("MIN_FILL_QUOTE", 5.0, float) # Min executed quote value to flip state

# Option 2.1: Reversal Gate
TREND_MODE = get_env("TREND_MODE", "REVERSAL") # STRICT or REVERSAL
REVERSAL_MODE = get_env("REVERSAL_MODE", "BOUNCE3") # CROSSUP or BOUNCE3
REVERSAL_SAMPLES = get_env("REVERSAL_SAMPLES", 3, int)
TREND_BLOCK_COOLDOWN_SECONDS = get_env("TREND_BLOCK_COOLDOWN_SECONDS", 300, int)
MIN_TREND_SPREAD_PCT = get_env("MIN_TREND_SPREAD_PCT", 0.0, float) # e.g. 0.001 for 0.1% buffer

# Walk-The-Limit Config
WALK_ENABLED = get_env("WALK_ENABLED", "YES") == "YES"
WALK_SLICE_SECONDS = get_env("WALK_SLICE_SECONDS", 15, int)
WALK_MAX_TOTAL_SECONDS = get_env("WALK_MAX_TOTAL_SECONDS", 180, int)
WALK_MAX_ATTEMPTS = get_env("WALK_MAX_ATTEMPTS", 6, int)
# Default offsets: Start = 0.1% (LIMIT_OFFSET_PCT), End = 0.0% (Mid)
WALK_OFFSET_START_PCT = get_env("WALK_OFFSET_START_PCT", LIMIT_OFFSET_PCT, float)
WALK_OFFSET_END_PCT = get_env("WALK_OFFSET_END_PCT", 0.0, float)
WALK_MODE = get_env("WALK_MODE", "LINEAR") # LINEAR or EXPONENTIAL
WALK_MAX_SPREAD_CROSS_PCT = get_env("WALK_MAX_SPREAD_CROSS_PCT", 0.0002, float) # 0.02% crossover cap

# Reserve Watcher (Monitors NON-POT ETH)
ENABLE_RESERVE_WATCHER = get_env("ENABLE_RESERVE_WATCHER", "YES") == "YES"
ENABLE_RESERVE_AUTOSALE = get_env("ENABLE_RESERVE_AUTOSALE", "NO") == "YES"
RESERVE_MIN_ETH = get_env("RESERVE_MIN_ETH", 0.0010, float)
RESERVE_MIN_ETH_DEC = Decimal(str(RESERVE_MIN_ETH))
RESERVE_TRAIL_PCT = get_env("RESERVE_TRAIL_PCT", 0.03, float) # 3% Trailing Stop
RESERVE_BLOCK_COOLDOWN_SECONDS = get_env("RESERVE_BLOCK_COOLDOWN_SECONDS", 300, int)
RESERVE_MAX_SELL_ETH = get_env("RESERVE_MAX_SELL_ETH", 0.01, float)

# Dynamic Dip Anchor
DIP_ANCHOR_MODE = get_env("DIP_ANCHOR_MODE", "BLEND") # BLEND, SMA_ONLY, LAST_SELL_ONLY
DIP_BLEND_SMA_WEIGHT = get_env("DIP_BLEND_SMA_WEIGHT", 0.7, float)
MAX_UNDER_SMA_PCT = get_env("MAX_UNDER_SMA_PCT", 0.03, float) # 3% limit below SMA
DIP_TARGET_DEBUG = get_env("DIP_TARGET_DEBUG", 1, int) == 1

TIMEZONE_STR = get_env("TIMEZONE", "Europe/Stockholm")
BASE_URL = get_env("BASE_URL", "https://api.binance.com")

SYMBOL = get_env("SYMBOL", "ETHUSDT")
QUOTE_ASSET = "USDT" # Default, updated dynamically in main
BASE_ASSET = "ETH" # Default, updated dynamically in main
STATE_FILE = "bot_state.json"

# Derived Execution Config (Safety)
# If WALK is enabled, we MUST ignore LIMIT_OFFSET_PCT to avoid double-offsetting.
LIMIT_OFFSET_PCT_USED = 0.0 if WALK_ENABLED else LIMIT_OFFSET_PCT

# Candle Adapter Config
CANDLE_INTERVAL = get_env("CANDLE_INTERVAL", "5m")
CANDLE_LIMIT = get_env("CANDLE_LIMIT", 200, int)
CANDLE_POLL_SECONDS = get_env("CANDLE_POLL_SECONDS", 10, int)
SMA_WINDOW_CANDLES = get_env("SMA_WINDOW_CANDLES", 30, int)
MAX_CANDLE_STALENESS_SECONDS = get_env("MAX_CANDLE_STALENESS_SECONDS", 1200, int)
FILTERS_REFRESH_SECONDS = get_env("FILTERS_REFRESH_SECONDS", 21600, int)


# Logging Setup
# Logging Config
LOG_LEVEL = get_env("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
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
        self.error_timestamps = []
        self.price_history = [] # Now persistent
        
        # Option 2.1 Fields
        self.trend_block_until = 0
        self.last_sma = 0.0
        self.last_mid = 0.0
        
        
        # Reserve Watcher Fields
        self.reserve_high_watermark_quote = 0.0
        self.reserve_last_value_quote = 0.0
        self.reserve_last_action_ts = 0
        self.reserve_last_seen_eth = 0.0
        
        # Candle Adapter State
        self.last_candle_close_time = 0 # ms int
        self.candle_closes = [] # List of floats

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
                    
                    
                    # Reserve Watcher State
                    self.reserve_high_watermark_quote = data.get("reserve_high_watermark_quote", 0.0)
                    self.reserve_last_value_quote = data.get("reserve_last_value_quote", 0.0)
                    self.reserve_last_action_ts = data.get("reserve_last_action_ts", 0)
                    self.reserve_last_seen_eth = data.get("reserve_last_seen_eth", 0.0)
                    
                    # Candle State
                    self.last_candle_close_time = data.get("last_candle_close_time", 0)
                    self.candle_closes = data.get("candle_closes", [])
                    # Truncate on load just in case
                    if len(self.candle_closes) > SMA_WINDOW_CANDLES:
                         self.candle_closes = self.candle_closes[-SMA_WINDOW_CANDLES:]
                    
                    logger.info("State loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")

    def save(self):
        try:
            # Cap lists before saving to prevent JSON growth
            if len(self.price_history) > TREND_WINDOW_SAMPLES:
                self.price_history = self.price_history[-TREND_WINDOW_SAMPLES:]
            if len(self.candle_closes) > SMA_WINDOW_CANDLES:
                self.candle_closes = self.candle_closes[-SMA_WINDOW_CANDLES:]

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
            status = getattr(e, "status_code", None)
            if status is None:
                 status = getattr(e, "http_status", None)
            
            if status is not None and 400 <= status < 500:
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

def msg_to_precision(val_decimal, step_size_str):
    """
    Formats decimal to string respecting step_size precision string (e.g. "0.00100").
    """
    try:
        if "." not in step_size_str:
            precision = 0
            quant_exp = Decimal("1")
        else:
            s = step_size_str.rstrip('0')
            if s.endswith('.'): s = s[:-1]
            precision = len(s.split(".")[1]) if "." in s else 0
            if precision == 0:
                quant_exp = Decimal("1")
            else:
                quant_exp = Decimal("1e-" + str(precision))
                
        # Quantize
        val_quant = val_decimal.quantize(quant_exp, rounding=ROUND_DOWN)
        return "{:f}".format(val_quant)
    except:
        return "{:f}".format(val_decimal)

def fmt_price_side(val, tick, side):
    val = Decimal(str(val))
    if tick <= 0: return "{:f}".format(val)
    ticks = (val / tick).to_integral_value(rounding=ROUND_UP if side == "BUY" else ROUND_DOWN)
    return "{:f}".format(ticks * tick)

def execute_limit(client, side, qty_decimal, step_size_decimal, step_size_str, tick_size_decimal,
                  mid_decimal, avg_price_cap_min=None, avg_price_cap_max=None):
    """
    Returns:
      - order dict with executedQty > 0 on fill/partial
      - {"status":"CANCELED_TIMEOUT_NOFILL"} on clean nofill timeout
      - None on hard failure (rejected, api fail, cancel fail, etc.)
    """
    if WALK_ENABLED:
        order = place_limit_order_walked(
            client, side, qty_decimal, step_size_decimal, step_size_str, tick_size_decimal,
            mid_decimal, avg_price_cap_min, avg_price_cap_max
        )
    else:
        order = place_limit_order_with_timeout(
            client, side, qty_decimal, step_size_decimal, step_size_str, tick_size_decimal,
            mid_decimal, avg_price_cap_min, avg_price_cap_max
        )

    # Clean timeout is not an error
    if order and order.get("status") == "CANCELED_TIMEOUT_NOFILL":
        return order

    # Hard fail
    if order is None:
        return None

    # If rejected ever leaks through, treat as hard fail
    if order.get("status") == "REJECTED":
        return None

    # Must have execution to be considered success (Partial or Full)
    if float(order.get("executedQty", 0.0)) > 0:
        return order

    # Anything else is treated as hard fail
    return None

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

def get_bid_ask(client):
    bt = api_call(client.book_ticker, SYMBOL)
    bid = Decimal(bt["bidPrice"])
    ask = Decimal(bt["askPrice"])
    return bid, ask

def get_recent_closed_candles(client, limit=None):
    if limit is None: limit = CANDLE_LIMIT
    try:
        result = api_call(client.klines, symbol=SYMBOL, interval=CANDLE_INTERVAL, limit=limit)
        # Parse: [Open Time, Open, High, Low, Close, Volume, Close Time, ...]
        # We only want CLOSED candles: close_time < now_ms
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - 1000 # 1s safety margin
        
        closed = []
        for k in result:
             c_time = k[6]
             if c_time < cutoff:
                 closed.append({
                     'close_time': c_time,
                     'close': float(k[4]),
                     'high': float(k[2]),
                     'low': float(k[3]),
                     'vol': float(k[5])
                 })
                 
        # Sort by time just in case
        closed.sort(key=lambda x: x['close_time'])
        return closed
    except Exception as e:
        logger.error(f"Error fetching candles: {e}")
        return []

def print_pre_flight_check(client, bot):
    print("\n" + "="*60)
    print(" PRE-FLIGHT SAFETY CHECKLIST")
    print("="*60)
    
    # 1. Config
    print("[-] RESERVE WATCHER:")
    print(f"    ENABLED: {ENABLE_RESERVE_WATCHER}")
    print(f"    AUTOSALE: {ENABLE_RESERVE_AUTOSALE}")
    print(f"    TRAIL: {RESERVE_TRAIL_PCT*100}%")
    
    print(f"[-] CONFIGURATION:")
    print(f"    DRY_RUN: {DRY_RUN}")
    print(f"    LIVE_TRADING: {LIVE_TRADING}")
    print(f"    REQUIRE_START_CONFIRM: {REQUIRE_START_CONFIRM}")
    print(f"    COOLDOWN_SECONDS: {COOLDOWN_SECONDS}")
    
    if WALK_ENABLED and LIMIT_OFFSET_PCT != 0:
        print("\n" + "!" * 60)
        print(" WARNING: WALK_ENABLED uses WALK offsets.")
        print(" LIMIT_OFFSET_PCT must be 0 to avoid double offsets.")
        print("!" * 60)

    # 2. Permissions & Balances
    print(f"[-] ACCOUNT:")
    try:
        acc = api_call(client.account)
        print(f"    Permissions: {acc.get('permissions')}")
        print(f"    Can Trade: {acc.get('canTrade')}")
        
        # Use the global BASE_ASSET
        print(f"    {BASE_ASSET} Balance: {next((b for b in acc['balances'] if b['asset'] == BASE_ASSET), {'free': '0.0'})['free']} (Free)")
        print(f"    {QUOTE_ASSET} Balance: {next((b for b in acc['balances'] if b['asset'] == QUOTE_ASSET), {'free': '0.0'})['free']} (Free)")
    except Exception as e:
        print(f"    ERROR FETCHING ACCOUNT INFO: {e}")
        sys.exit(1)

    # 3. State
    print(f"[-] BOT STATE:")
    print(f"    State: {bot.state}")
    print(f"    Pot {QUOTE_ASSET}: {bot.pot_quote}")
    print(f"    Pot {BASE_ASSET}: {bot.pot_eth}")
    print(f"    Last Sell Price: {bot.last_sell_price}")
    print(f"    Entry Price: {bot.entry_price}")
    print(f"    Day Key: {bot.day_key}")
    print(f"    Last Trade Time: {bot.last_trade_time}")
    
    # 4. Phase Verification
    print("-" * 60)
    
    if DRY_RUN != 0:
        print("✅ DRY_RUN mode (safe). Live phase verification skipped.")
        print("="*60 + "\n")
        return

    if LIVE_TRADING != "YES":
        print("!!! LIVE_TRADING is not 'YES'. Exiting !!!")
        sys.exit(1)

    print("✅ LIVE TRADING MODE VERIFIED.")
    print("="*60 + "\n")

def verify_live_readiness(client, bot_state):
    """
    Performs SAFE pre-flight checks without placing real orders.
    Verifies:
      1. Account 'canTrade' status.
      2. Symbol permissions and status (TRADING).
      3. Filters (LOT_SIZE, MIN_NOTIONAL) presence.
      4. 'new_order_test' validation for BUY and SELL.
    Returns: (bool, reason_message)
    """
    logger.info("VERIFYING LIVE READINESS (Safe Mode - No Real Orders)...")
    
    try:
        # A1) ACCOUNT CHECK
        acc = api_call(client.account)
        if not acc.get('canTrade', False):
            return False, "Account 'canTrade' is False. Check API Key permissions or Account Status."
        
        # Log permissions if visible
        perms = acc.get('permissions', [])
        logger.info(f"Account Permissions: {perms}")
        
        # A2) SYMBOL CHECK
        info = api_call(client.exchange_info, symbol=SYMBOL)
        s_info = info['symbols'][0]
        
        if s_info['status'] != 'TRADING':
            return False, f"Symbol {SYMBOL} status is {s_info['status']}, not TRADING."
            
        if s_info['quoteAsset'] != QUOTE_ASSET:
            return False, f"Symbol Quote Asset {s_info['quoteAsset']} mismatch with Config {QUOTE_ASSET}."

        # A3) FILTER + VIABILITY
        step_str = "0.0001" # fallback
        try:
             step_size_decimal, step_size_str, tick_size_decimal, min_notional_decimal, mul_up, mul_down, min_qty_decimal = get_filters(client)
             step_str = step_size_str
        except Exception as filter_e:
             return False, f"Filter Retrieval Failed: {filter_e}"
             
        # Construct Safe Test Params (Bid/Ask)
        ticker = api_call(client.book_ticker, SYMBOL)
        bid = Decimal(ticker['bidPrice'])
        ask = Decimal(ticker['askPrice'])
        
        # Valid Price (Tick Size) - Using Floor Logic
        # Use Ask for Buy, Bid for Sell (Realistic Limit Orders)
        buy_price_str = fmt_price_side(ask, tick_size_decimal, "BUY")
        sell_price_str = fmt_price_side(bid, tick_size_decimal, "SELL")
        
        # Valid Qty (Min Notional + Buffer)
        # Conservative: use higher price (ask) to estimate required qty? 
        # Actually lower price -> higher qty needed. Use Bid for conservative Notional check?
        # User snippet: "price_for_qty = ask if ask > 0 else (bid + ask) / 2"
        # Let's stick to user request.
        price_for_qty = ask if ask > 0 else (bid + ask) / 2
        
        target_notional = min_notional_decimal * Decimal("1.2")
        qty_needed = target_notional / price_for_qty
        qty_safe = round_down_step(qty_needed, step_size_decimal)
        
        # CLAMP Qty >= minQty
        if qty_safe < min_qty_decimal:
            qty_safe = min_qty_decimal

        # Step Size formatting
        qty_str = msg_to_precision(qty_safe, step_size_str)
        
        # A4) TEST ORDER CHECK (BUY & SELL)
        params_base = {
            "symbol": SYMBOL,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": qty_str
        }

        # BUY (at Ask)
        params_buy = params_base.copy()
        params_buy["side"] = "BUY"
        params_buy["price"] = buy_price_str
        api_call(client.new_order_test, **params_buy)
        
        # SELL (at Bid)
        params_sell = params_base.copy()
        params_sell["side"] = "SELL"
        params_sell["price"] = sell_price_str
        api_call(client.new_order_test, **params_sell)
        
        logger.info("✅ LIVE READY: Preflight checks passed (Account, Symbol, Filters, Test Orders).")
        return True, "OK"

    except ClientError as e:
        return False, f"Binance API Error: {e}"
    except Exception as e:
        return False, f"Unexpected Error: {e}"

def get_filters(client):
    # Check Cache
    if not hasattr(get_filters, 'cache'):
        get_filters.cache = None
    if not hasattr(get_filters, 'last_ts'):
        get_filters.last_ts = 0
        
    now = time.time()
    if get_filters.cache and (now - get_filters.last_ts < FILTERS_REFRESH_SECONDS):
        return get_filters.cache

    try:
        info = api_call(client.exchange_info, symbol=SYMBOL)
        f = info['symbols'][0]['filters']
        
        # LOT_SIZE -> stepSize, minQty
        lot_filter = next((x for x in f if x['filterType'] == 'LOT_SIZE'), None)
        if not lot_filter:
            logger.critical("CRITICAL: LOT_SIZE filter not found for symbol.")
            sys.exit(1)
        step_size_str = lot_filter['stepSize']
        step_size_decimal = Decimal(step_size_str)
        min_qty_decimal = Decimal(lot_filter['minQty'])
        
        # MIN_NOTIONAL
        notional_filter = next((x for x in f if x['filterType'] in ['NOTIONAL', 'MIN_NOTIONAL']), None)
        if not notional_filter:
            logger.critical("CRITICAL: MIN_NOTIONAL filter not found.")
            sys.exit(1)
            
        mn = (
            notional_filter.get("minNotional")
            or notional_filter.get("notional")
            or notional_filter.get("minNotionalValue")
        )
        if mn is None:
            logger.critical(f"CRITICAL: NOTIONAL/MIN_NOTIONAL filter missing min notional field: {notional_filter}")
            sys.exit(1)
        min_notional_decimal = Decimal(str(mn))
        
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
        
        # Cache Result
        get_filters.cache = (step_size_decimal, step_size_str, tick_size_decimal, min_notional_decimal, mul_up, mul_down, min_qty_decimal)
        get_filters.last_ts = time.time()
        
        return step_size_decimal, step_size_str, tick_size_decimal, min_notional_decimal, mul_up, mul_down, min_qty_decimal

    except Exception as e:
        logger.error(f"Error fetching filters: {e}")
        # Retries handled by loop flow usually, but we need defaults? 
        # For now, propagate or return defaults. 
        # Raising ensures we don't trade on bad data.
        raise e

def round_down_step(quantity, step_size_decimal):
    d_qty = Decimal(str(quantity))
    if step_size_decimal == 0: return Decimal("0")
    # quantize logic: derive precision or just use floor div?
    # Floor div is safest for step size alignment: (qty // step) * step
    return (d_qty // step_size_decimal) * step_size_decimal

def get_free_balance(c_client, asset_code):
    try:
        # Use api_call for robustness
        account = api_call(c_client.account)
        balances = account.get('balances', [])
        for b in balances:
            if b['asset'] == asset_code:
                return Decimal(b['free'])
        return Decimal("0.0")
    except Exception as e:
        logger.error(f"Error fetching balance for {asset_code}: {e}")
        return Decimal("0.0")

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
    if not prices:
        return 0.0
    # Safe float sum to handle mixed Float/Decimal types
    return float(sum(float(x) for x in prices)) / float(len(prices))

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



def compute_reserve_base(account_free_base, pot_base):
    """Calculates reserve base asset (Free - Pot), clamped to 0 if < MIN."""
    reserve = account_free_base - pot_base
    if reserve < RESERVE_MIN_ETH_DEC:
        return Decimal("0.0")
    return reserve

def reserve_watcher(client, bot_state, current_mid, step_size_decimal, step_size_str, tick_size_decimal, min_notional_decimal, avg_price_cap_min, avg_price_cap_max):
    """
    Monitors ETH outside the pot. Trailing stop logic (Value-based) to sell reserve ETH.
    """
    if not ENABLE_RESERVE_WATCHER:
        # Log occasionally? Or just ignore.
        return

    now_ts = time.time()
    
    # 1. Calculate Reserve
    try:
        total_free_base = get_free_balance(client, BASE_ASSET)
    except Exception as e:
        logger.error(f"Reserve error fetching {BASE_ASSET}: {e}")
        return

    pot_base_dec = Decimal(str(bot_state.pot_eth))
    reserve_base = compute_reserve_base(total_free_base, pot_base_dec)
    reserve_base_float = float(reserve_base)
    
    changed = False

    # HANDLE ZERO RESERVE
    # HANDLE ZERO RESERVE
    if reserve_base_float == 0:
        if bot_state.reserve_high_watermark_quote != 0.0:
            bot_state.reserve_high_watermark_quote = 0.0
            changed = True
        if bot_state.reserve_last_seen_eth != 0.0:
            bot_state.reserve_last_seen_eth = 0.0
            changed = True
        if bot_state.reserve_last_value_quote != 0.0:
            bot_state.reserve_last_value_quote = 0.0
            changed = True
        
        if changed:
            bot_state.save()
        return

    # 2. Value Calculation
    # Fix: Round/Quantize to 2 decimals to avoid float jitter
    mid_dec = Decimal(str(current_mid))
    val_dec = (reserve_base * mid_dec).quantize(Decimal("0.01"))
    reserve_value_quote = float(val_dec)
    
    if bot_state.reserve_last_value_quote != reserve_value_quote:
        bot_state.reserve_last_value_quote = reserve_value_quote
        # Do not save just for value update (too frequent), rely on periodic save or significant change?
        # Requirement: "Ensure reserve_last_seen_eth is persisted even on quiet ticks"
        # Since we save at 'changed' below, we might need to flag this if strict persistence is needed.
        # But saving on every price tick is bad I/O. 
        # User constraint: "Save at most once per reserve_watcher() call." 
        # Typically we only save if structure changes or watermark changes.
        pass 

    # --- WATERMARK RESET LOGIC (Size Change Detection) ---
    prev_base = bot_state.reserve_last_seen_eth
    delta_base = abs(reserve_base_float - prev_base)
    
    # Reset if change > step_size OR > 5% change
    threshold_step = float(step_size_decimal)
    threshold_pct = 0.05 * prev_base if prev_base > 0 else 0.0
    
    if prev_base > 0 and (delta_base > threshold_step or (threshold_pct > 0 and delta_base > threshold_pct)):
        logger.info(f"RESERVE: Reserve size changed ({prev_base:.4f} -> {reserve_base_float:.4f}). Resetting High Watermark to Current Val ({reserve_value_quote:.2f}).")
        bot_state.reserve_high_watermark_quote = reserve_value_quote
        changed = True
    
    # Always update last seen if different (so we catch up to new size)
    if bot_state.reserve_last_seen_eth != reserve_base_float:
        bot_state.reserve_last_seen_eth = reserve_base_float
        changed = True # Save new size persistence

    # 3. Update High Watermark (Value Based)
    if bot_state.reserve_high_watermark_quote == 0 or reserve_value_quote > bot_state.reserve_high_watermark_quote:
        bot_state.reserve_high_watermark_quote = reserve_value_quote
        # logger.info(f"RESERVE: High Watermark Updated: {bot_state.reserve_high_watermark_quote:.2f}") # Too spammy if price drift?
        # Only log if update is significant? Or keeping it is fine for now.
        changed = True
        
    if changed:
        bot_state.save()

    # Heartbeat Logging (Log every ~60s or if status changes significantly?)

    # 4. Check Cooldown
    if now_ts - bot_state.reserve_last_action_ts < RESERVE_BLOCK_COOLDOWN_SECONDS:
        return

    # 5. Check Triggers
    signal = False
    reason = ""
    
    # Value Trailing Stop: Value < High * (1 - trail)
    trail_val = bot_state.reserve_high_watermark_quote * (1 - RESERVE_TRAIL_PCT)
    if reserve_value_quote <= trail_val and bot_state.reserve_high_watermark_quote > 0:
        signal = True
        reason = f"TRAIL_STOP (Value {reserve_value_quote:.2f} < {trail_val:.2f})"



    if not signal:
        return

    # 6. Execute
    if signal:
        if not ENABLE_RESERVE_AUTOSALE:
            if int(now_ts) % 60 < LOOP_INTERVAL_SECONDS: # Log warning sparingly
                logger.warning(f"RESERVE SELL SIGNAL ({reason}) - AutoSale DISABLED.")
            return

        # SELL LOGIC
        logger.info(f"RESERVE SELL TRIGGERED: {reason}. Reserve: {reserve_base_float:.4f} {BASE_ASSET}")
        
        # Cap size
        qty_to_sell = min(reserve_base, Decimal(str(RESERVE_MAX_SELL_ETH)))
        qty_to_sell = round_down_step(qty_to_sell, step_size_decimal)
        
        d_mid = Decimal(str(current_mid))
        if qty_to_sell * d_mid < min_notional_decimal:
            logger.warning("RESERVE: Sell quantity too small for MinNotional. Skipping.")
            return

        order = execute_limit(client, 'SELL', qty_to_sell, step_size_decimal, step_size_str, tick_size_decimal, d_mid, avg_price_cap_min, avg_price_cap_max)
        
        if order and order.get("status") == "CANCELED_TIMEOUT_NOFILL":
            logger.info("RESERVE: Sell order timed out and canceled cleanly.")
            bot_state.reserve_last_action_ts = now_ts
            bot_state.save()
            return

        if order:
            cumm_quote = float(order.get('cummulativeQuoteQty', 0.0))
            exec_qty = float(order.get('executedQty', 0.0))
            
            logger.info(f"RESERVE: SELL EXECUTED. Qty: {exec_qty:.4f}. Returns: {cumm_quote:.2f} {QUOTE_ASSET}. (Not added to Pot)")
            
            bot_state.reserve_last_action_ts = now_ts
            
            # Reset / Update High Watermark Logic
            # If we sold PART of "Value", the new "Value" (remaining) is naturally lower.
            # Triggers might loop if we don't reset watermark to "Current".
            # Simplest: Reset Watermark to 0 (Fresh start) or Set to Current Remaining Value.
            # User Prompt: "After sell, set reserve_high_watermark_quote = 0"
            bot_state.reserve_high_watermark_quote = 0.0 
                
            bot_state.save()
        else:
            logger.error("RESERVE: Order Failed/Timeout.")
            bot_state.reserve_last_action_ts = now_ts # Cooldown
            bot_state.save()

# ==================================================================================
# TRADING ACTIONS
# ==================================================================================
def place_limit_order_once_with_poll(client, side, qty_str, price_str, poll_seconds):
    """
    Helper for Walk Strategy: Places one limit order, polls it for `poll_seconds`.
    Returns:
      - Order Dict (if FILLED or Partially Filled)
      - {"status": "CANCELED_TIMEOUT_NOFILL"} (if timed out and clean cancel)
      - None (if API failure)
    """
    try:
        # logger.info(f"WALK STEP: Placing {side} Order | Qty: {qty_str} | Price: {price_str} | Timeout: {poll_seconds}s")
        
        # 0. DRY RUN SAFETY
        if DRY_RUN == 1:
            logger.info(f"DRY_RUN: WALK STEP simulated {side} qty={qty_str} price={price_str}")
            # Calculate Quote Qty for state update logic
            price_dec = Decimal(price_str)
            qty_dec = Decimal(qty_str)
            quote_qty_str = str((qty_dec * price_dec).quantize(Decimal("0.00000001")))
            
            return {
                "status": "FILLED",
                "cummulativeQuoteQty": quote_qty_str,
                "executedQty": qty_str,
                "fills": [{"qty": qty_str, "price": price_str}]
            }

        # 1. Place Order
        order = api_call(
            client.new_order,
            symbol=SYMBOL,
            side=side,
            type="LIMIT",
            timeInForce="GTC",
            quantity=qty_str,
            price=price_str
        )
        
        order_id = order['orderId']
        
        # 2. Poll Loop
        elapsed = 0
        poll_interval = 2 # 2s polling
        
        while elapsed < poll_seconds:
            time.sleep(poll_interval)
            elapsed += poll_interval
            
            try:
                latest = api_call(client.get_order, symbol=SYMBOL, orderId=order_id)
                status = latest['status']
                
                if status == 'FILLED':
                    return latest
                
                if status in ['CANCELED', 'EXPIRED']:
                    if float(latest.get('executedQty', 0)) > 0:
                        return latest
                    return {"status": "CANCELED_TIMEOUT_NOFILL"}
                
                if status == 'REJECTED':
                    return latest
                        
            except Exception as e:
                logger.warning(f"WALK STEP Poll Error: {e}")
                
        # 3. Timeout Reached -> Cancel
        logger.info(f"WALK STEP Timeout ({poll_seconds}s). Cancelling order {order_id}...")
        try:
            cancel_res = api_call(client.cancel_order, symbol=SYMBOL, orderId=order_id)
            
            # Strict verify via get_order (Invariant 3)
            final_stat = api_call(client.get_order, symbol=SYMBOL, orderId=order_id)
            if float(final_stat.get('executedQty', 0)) > 0:
                 return final_stat
            
            return {"status": "CANCELED_TIMEOUT_NOFILL"}
            
        except ClientError as e:
            if e.error_code == -2011: # Unknown order (already filled/canceled)
                try:
                    final = api_call(client.get_order, symbol=SYMBOL, orderId=order_id)
                    if float(final['executedQty']) > 0:
                        return final
                    return {"status": "CANCELED_TIMEOUT_NOFILL"}
                except Exception:
                    return {"status": "CANCELED_TIMEOUT_NOFILL"}
            
            logger.error(f"WALK STEP Cancel Failed: {e}. Attempting best-effort final cancel...")
            try:
                api_call(client.cancel_order, symbol=SYMBOL, orderId=order_id)
            except Exception:
                pass
            return None
            
    except Exception as e:
        logger.error(f"WALK STEP Failed: {e}")
        return None

def place_limit_order_walked(client, side, qty_decimal, step_size_decimal, step_size_str, tick_size_decimal, mid_decimal, avg_price_cap_min=None, avg_price_cap_max=None):
    """
    Orchestrates a 'Walk-The-Limit' execution.
    Attempts multiple limit orders starting passive, moving to aggressive.
    """
    start_time = time.time()
    
    # 1. Quantize Logic (Same as standard)
    if step_size_decimal == 0:
        final_qty = qty_decimal
    else:
        final_qty = (qty_decimal // step_size_decimal) * step_size_decimal
        
    # Precise Qty Formatting
    qty_str = msg_to_precision(final_qty, step_size_str)
    
    logger.info(f"WALK START: {side} {qty_str} | MaxTime: {WALK_MAX_TOTAL_SECONDS}s | MaxAttempts: {WALK_MAX_ATTEMPTS}")
    
    for i in range(WALK_MAX_ATTEMPTS):
        # Time Check
        if time.time() - start_time > WALK_MAX_TOTAL_SECONDS:
            logger.warning("WALK ENDED: Total time limit reached.")
            break
            
        # Use passed reference price as base; do not fetch mid repeatedly (saves calls + matches spec)
        mid_dec = Decimal(str(mid_decimal))
        
        # Linear Offset Calc
        if WALK_MAX_ATTEMPTS > 1:
            progress = i / (WALK_MAX_ATTEMPTS - 1)
        else:
            progress = 0.0
            
        current_offset_pct = WALK_OFFSET_START_PCT + (WALK_OFFSET_END_PCT - WALK_OFFSET_START_PCT) * progress
        offset_dec = Decimal(str(current_offset_pct))
        cross_cap_dec = Decimal(str(WALK_MAX_SPREAD_CROSS_PCT))
        
        if side == "BUY":
            target_price = mid_dec * (Decimal("1.0") - offset_dec)
            max_limit = mid_dec * (Decimal("1.0") + cross_cap_dec)
            if target_price > max_limit: target_price = max_limit
            
            if avg_price_cap_max:
                if target_price > avg_price_cap_max:
                    target_price = avg_price_cap_max
        else: # SELL
            target_price = mid_dec * (Decimal("1.0") + offset_dec)
            min_limit = mid_dec * (Decimal("1.0") - cross_cap_dec)
            if target_price < min_limit: target_price = min_limit
            
            if avg_price_cap_min:
                if target_price < avg_price_cap_min:
                    target_price = avg_price_cap_min

        # Round Price
        # Helper fmt_price_side handles direction
        price_str = fmt_price_side(target_price, tick_size_decimal, side)
        
        # Slice Time
        remaining_total = WALK_MAX_TOTAL_SECONDS - (time.time() - start_time)
        if remaining_total <= 0:
            logger.warning("WALK ENDED: Time limit reached.")
            break

        this_slice = min(WALK_SLICE_SECONDS, remaining_total)
        this_slice = int(max(5, this_slice))
        
        # Log Book Context
        try:
            bt = api_call(client.book_ticker, SYMBOL)
            logger.info(f"WALK BOOK: bid={bt['bidPrice']} ask={bt['askPrice']} limit={price_str}")
        except: pass

        logger.info(f"WALK STEP {i+1}/{WALK_MAX_ATTEMPTS}: {side} @ {price_str} (Off: {current_offset_pct*100:.3f}%). Ref: {float(mid_dec):.2f}")
        
        res = place_limit_order_once_with_poll(client, side, qty_str, price_str, this_slice)
        
        if res:
            status = res.get('status')
            if status == "CANCELED_TIMEOUT_NOFILL":
                continue # Retry next step
            
            if status == "REJECTED":
                logger.error(f"WALK FAILED: Order rejected: {res}")
                return None

            # FILLED or Partial
            if float(res.get('executedQty', 0)) > 0:
                logger.info(f"WALK SUCCESS: Filled at step {i+1}")
                return res
            
            # If CANCELED/REJECTED with 0 exec, it usually comes as status sentinel, 
            # but if raw order dict returned with 0 exec, treat as retry?
            # Our helper returns sentinel for clean 0-fill cancel. 
            # So here we probably got a real fill or real error.
            if status in ['CANCELED', 'EXPIRED'] and float(res.get('executedQty', 0)) == 0:
                continue

    logger.info("WALK ENDED: No fill after all attempts.")
    return {"status": "CANCELED_TIMEOUT_NOFILL"}

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
        offset = Decimal(str(LIMIT_OFFSET_PCT_USED))
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
             
        # Round to tickSize (Side Aware)
        p_str = fmt_price_side(final_price, tick_size_decimal, side)
        p_dec = Decimal(p_str)

        # Log Intent
        log_msg = f"{side} LIMIT {qty_str} @ {p_str} (Offset {LIMIT_OFFSET_PCT_USED*100:.2f}%)"
        logger.info(f"PREPARING: {log_msg}")

        if DRY_RUN == 1:
            logger.info(f"DRY_RUN: Simulated {log_msg}")
            quote_qty = (final_qty_quant * p_dec).quantize(Decimal("0.00000001"))
            return {
                "status": "FILLED",
                "cummulativeQuoteQty": str(quote_qty),
                "executedQty": str(final_qty_quant),
                "fills": [{"qty": str(final_qty_quant), "price": str(p_dec)}]
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
            
            # Clean timeout (no fill)
            logger.info(f"Order {oid} canceled cleanly (no fill).")
            return {"status": "CANCELED_TIMEOUT_NOFILL"}
            
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
    # Fund Pot Logic
    mid_decimal = Decimal(str(mid))
    est_eth_needed_raw = needed_quote / mid_decimal
    # Round UP slightly for fee buffer and step size
    est_eth_needed = round_down_step(est_eth_needed_raw * Decimal("1.02"), step_size_decimal)
    
    # Available Balance Check
    acc = api_call(client.account)
    base_bal = next((b for b in acc['balances'] if b['asset'] == BASE_ASSET), {'free': '0.0'})
    free_base = Decimal(base_bal['free'])
    
    # Use cached filters and compute caps
    step_size_decimal_fund, step_size_str_fund, tick_size_fund, min_notional_decimal_fund, mul_up_fund, mul_down_fund, min_qty_decimal_fund = get_filters(client)

    if est_eth_needed > free_base:
         logger.critical(f"STOPPING: Insufficient {BASE_ASSET} to fund pot. Need {est_eth_needed}, Have {free_base}")
         sys.exit(1)

    if est_eth_needed < min_qty_decimal_fund:
        est_eth_needed = min_qty_decimal_fund
    
    # Ensure minQty is also step-aligned to prevent API rejection
    est_eth_needed = round_down_step(est_eth_needed, step_size_decimal_fund)

    # EXECUTE FUNDING SELL
    logger.info(f"SIGNAL: FUND POT (SELL {BASE_ASSET}) | Qty:{est_eth_needed}")
    
    # Consistently use bid/ask for funding reference too
    bid_f, ask_f = get_bid_ask(client)
    ref_price_f = max(bid_f, mid_decimal) 
    
    avg_price_cap_max = mid_decimal * mul_up_fund
    avg_price_cap_min = mid_decimal * mul_down_fund

    order = execute_limit(client, 'SELL', est_eth_needed, step_size_decimal_fund, step_size_str_fund, tick_size_fund, ref_price_f, avg_price_cap_min, avg_price_cap_max)
    
    if order and order.get("status") == "CANCELED_TIMEOUT_NOFILL":
        logger.info("FUNDING order timed out and canceled cleanly. Not counting as error.")
        return

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
        global QUOTE_ASSET, BASE_ASSET
        QUOTE_ASSET = info['symbols'][0]['quoteAsset']
        BASE_ASSET = info['symbols'][0]['baseAsset']
    except Exception as e:
        logger.critical(f"Failed to fetch Symbol Info for {SYMBOL}: {e}")
        sys.exit(1)
    
    bot = BotState()
    bot.load()
    
    # PRE-FLIGHT PRE-CHECK
    if LIVE_TRADING == "YES" and DRY_RUN == 0:
         print_pre_flight_check(client, bot)

         # Safe readiness check
         is_ready, msg = verify_live_readiness(client, bot)
         if not is_ready:
             logger.critical(f"❌ LIVE TRADING NOT READY: {msg}")
             print(f"CRITICAL: {msg}")
             sys.exit(1)

    # STARTUP CONFIRMATION
    if REQUIRE_START_CONFIRM == 1:
        try:
            # Check basic conditions. We need filters to know min_notional.
            # Just do a quick loose check or fetch filters once.
            logger.info("Startup Safety Check...")
            _, _, _, min_notional_decimal, _, _, min_qty_decimal = get_filters(client)
            
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

    last_reserve_ts = 0
    
    # ---------------- CANDLE LOOP START ----------------
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

            # 1. THROTTLED RESERVE WATCHER (Top Level)
            if ENABLE_RESERVE_WATCHER and (time.time() - last_reserve_ts > 60):
                 last_reserve_ts = time.time()  # Throttle regardless of success
                 # Fetch ticker just for reserve watcher
                 current_mid, _ = get_mid_price_and_spread(client)
                 if current_mid > 0:
                     step_size_decimal, step_size_str, tick_size_decimal, min_notional_decimal, mul_up, mul_down, min_qty_decimal = get_filters(client)
                     avg_price_cap_max = Decimal(str(current_mid)) * mul_up
                     avg_price_cap_min = Decimal(str(current_mid)) * mul_down
                     
                     reserve_watcher(client, bot, current_mid, step_size_decimal, step_size_str, tick_size_decimal, min_notional_decimal, avg_price_cap_min, avg_price_cap_max)

            # 2. POLL CANDLES (Always)
            new_candles = get_recent_closed_candles(client)
            new_closed = [c for c in new_candles if c['close_time'] > bot.last_candle_close_time]
            new_closed.sort(key=lambda x: x['close_time'])
            
            # If no new candles -> Wait -> Continue
            if not new_closed:
                # Log heartbeat if enabled (or every minute implicitly via debug)
                logger.debug("No new closed candle yet.")
                time.sleep(CANDLE_POLL_SECONDS)
                continue

            # ----------------------------------------
            # PROCESS NEW CANDLES (State Update First)
            # ----------------------------------------
            for candle in new_closed:
                c_close = candle['close']
                c_time = candle['close_time']
                
                bot.last_candle_close_time = c_time
                bot.candle_closes.append(c_close)
                if len(bot.candle_closes) > SMA_WINDOW_CANDLES:
                    bot.candle_closes.pop(0)
                    
                bot.price_history.append(c_close)
                if len(bot.price_history) > TREND_WINDOW_SAMPLES:
                    bot.price_history.pop(0)

            # Save state once after batch update
            bot.save()


            # ----------------------------------------
            # TRADING LOGIC (Latest Candle Only)
            # ----------------------------------------
            
            # Cooldown Gate (User Req: Check after candle update)
            if time.time() - bot.last_trade_time < COOLDOWN_SECONDS:
                logger.debug("Cooldown active (State updated, skipping logic)...")
                time.sleep(CANDLE_POLL_SECONDS)
                continue

            latest_candle = new_closed[-1]
            c_close = latest_candle['close']
            latest_c_time = latest_candle['close_time']

            # Staleness Check
            now_ms = int(time.time() * 1000)
            if now_ms - latest_c_time > MAX_CANDLE_STALENESS_SECONDS * 1000:
                logger.warning(f"Skipping stale candle batch (Latest: {latest_c_time}). Age: {(now_ms - latest_c_time)/1000:.1f}s")
                time.sleep(CANDLE_POLL_SECONDS)
                continue

            # Prepare Logic Vars
            current_price = c_close
            bot.last_mid = c_close 
            
            # SMA from Candles
            sma = calculate_sma(bot.candle_closes)
            bot.last_sma = sma
            current_sma = sma
            
            # Filters
            step_size_decimal, step_size_str, tick_size_decimal, min_notional_decimal, mul_up, mul_down, min_qty_decimal = get_filters(client)
            d_trade_val = Decimal(str(TRADE_VALUE_QUOTE))
            target_notional_decimal = max(d_trade_val, min_notional_decimal * Decimal(str(MIN_NOTIONAL_BUFFER)))
            
            # Snapshot Logging
            # Trend context (Use price_history for trend readiness and reversal source)
            trend_ready = len(bot.price_history) >= TREND_MIN_SAMPLES
            snap_trend_reason = "WARMUP"
            prev_price = bot.price_history[-2] if len(bot.price_history) >= 2 else current_price
            # Fix: Compute prev_sma from candle_closes to match current_sma logic
            prev_sma = calculate_sma(bot.candle_closes[:-1]) if len(bot.candle_closes) > 1 else current_sma
 
            if trend_ready:
                    _, snap_trend_reason = is_reversal_confirmed(bot.price_history, current_price, sma, prev_price, prev_sma)
            
            snap_cooldown = max(0, int(bot.trend_block_until - time.time()))
            
            # Timezone Format
            tz = pytz.timezone(TIMEZONE_STR)
            c_dt = datetime.fromtimestamp(latest_c_time/1000, tz=tz)
            c_time_str = c_dt.strftime("%Y-%m-%d %H:%M:%S")

            # Buy Data (Snapshot)
            snap_anchor = bot.last_sell_price
            if snap_anchor <= 0: snap_anchor = current_price
            if trend_ready and DIP_ANCHOR_MODE != "LAST_SELL_ONLY":
                snap_sma = sma
                if DIP_ANCHOR_MODE == "SMA_ONLY": snap_anchor = snap_sma
                elif DIP_ANCHOR_MODE == "BLEND":
                    w = DIP_BLEND_SMA_WEIGHT
                    snap_anchor = (Decimal(str(snap_sma)) * Decimal(str(w))) + (Decimal(str(snap_anchor)) * Decimal(str(1-w)))
                    snap_anchor = float(snap_anchor)
            snap_dip_target = snap_anchor * (1 - BUY_DROP_PCT)
            snap_dip_ok = current_price <= snap_dip_target
            
            # Log Snapshot
            pot_eur_fmt = f"{bot.pot_quote:.2f}"
            pot_eth_fmt = f"{bot.pot_eth:.4f}"
            
            trend_cd_s = max(0, int(bot.trend_block_until - time.time()))
            trade_cd_s = max(0, int(COOLDOWN_SECONDS - (time.time() - bot.last_trade_time)))
            
            buy_part = f"LastSell:{bot.last_sell_price:.2f} DipTarget:{snap_dip_target:.2f} DipOK:{snap_dip_ok} Trend:{snap_trend_reason} TrendCD:{trend_cd_s}s TradeCD:{trade_cd_s}s"
            
            logger.info(f"CANDLE [{c_time_str}] | Price:{current_price:.2f} | SMA:{sma:.2f} | Pot({pot_eur_fmt}/{pot_eth_fmt}) | {buy_part}")

            # ----------------------------------------
            # EXECUTION LOGIC
            # ----------------------------------------
            
            # Fetch REAL LIVE BID/ASK for execution (Single snapshot)
            bid, ask = get_bid_ask(client)
            if bid <= 0:
                 time.sleep(CANDLE_POLL_SECONDS)
                 continue
            exec_mid = float((bid + ask) / 2)
            exec_spread = float((ask - bid) / bid)
            
            # Spread Block (Safety)
            if MAX_SPREAD_PCT > 0 and exec_spread > MAX_SPREAD_PCT:
                 logger.info(f"SPREAD BLOCK: {exec_spread:.4f} > {MAX_SPREAD_PCT:.4f}")
                 time.sleep(CANDLE_POLL_SECONDS)
                 continue
            
            avg_price_cap_max = Decimal(str(exec_mid)) * mul_up
            avg_price_cap_min = Decimal(str(exec_mid)) * mul_down


            # Pot Funding Logic (Mid-based, but triggered here)
            if bot.state == "HOLDING_QUOTE":
                if Decimal(str(bot.pot_quote)) < min_notional_decimal: 
                     fund_pot_if_needed(client, bot, target_notional_decimal, step_size_decimal, step_size_str, min_notional_decimal)
                     if Decimal(str(bot.pot_quote)) < min_notional_decimal:
                        logger.critical(f"STOPPING: Pot {QUOTE_ASSET} {bot.pot_quote:.2f} too small. Funding failed.")
                        sys.exit(1)

            # BUY / SELL LOGIC
            if bot.state == "HOLDING_QUOTE":
                # ... [Insert BUY Logic from bot.py, adapting variables] ...
                # BUY Re-implementation with candle vars
                anchor_price = bot.last_sell_price
                if anchor_price <= 0: anchor_price = current_price
                
                is_falling_knife = False
                if trend_ready:
                    if DIP_ANCHOR_MODE == "SMA_ONLY": anchor_price = current_sma
                    elif DIP_ANCHOR_MODE == "BLEND":
                            w = DIP_BLEND_SMA_WEIGHT
                            anchor_price = (Decimal(str(current_sma)) * Decimal(str(w))) + (Decimal(str(anchor_price)) * Decimal(str(1-w)))
                            anchor_price = float(anchor_price)
                    
                    if current_price < current_sma * (1 - MAX_UNDER_SMA_PCT):
                            is_falling_knife = True

                target_buy_price = anchor_price * (1 - BUY_DROP_PCT)
                
                # Eval
                if current_price <= target_buy_price:
                    if is_falling_knife:
                            logger.info(f"KNIFE GUARD: {current_price} < {MAX_UNDER_SMA_PCT*100}% below SMA")
                            time.sleep(CANDLE_POLL_SECONDS)
                            continue
                    
                    if should_apply_trend_block(bot, time.time()):
                            logger.info(f"TREND COOLDOWN ACTIVE")
                            time.sleep(CANDLE_POLL_SECONDS)
                            continue
                            
                    if not trend_ready:
                            logger.info("WARMUP: Not enough trend samples.")
                            time.sleep(CANDLE_POLL_SECONDS)
                            continue
                            
                    confirmed, reason = is_reversal_confirmed(bot.price_history, current_price, current_sma, prev_price, prev_sma)
                    if not confirmed:
                            logger.info(f"TREND GATE: {reason}")
                            set_trend_block(bot, time.time())
                            time.sleep(CANDLE_POLL_SECONDS)
                            continue
                            
                    # EXECUTE BUY
                    logger.info(f"SIGNAL: BUY! Price:{current_price} <= Target:{target_buy_price}")
                    
                    qty_raw = Decimal(str(bot.pot_quote)) / Decimal(str(current_price))
                    qty_to_buy = round_down_step(qty_raw * Decimal("0.99"), step_size_decimal)
                    
                    if qty_to_buy <= 0: 
                        time.sleep(CANDLE_POLL_SECONDS)
                        continue
                        
                    ref_price = min(bid, Decimal(str(current_price)))
                    
                    if qty_to_buy * ref_price < min_notional_decimal:
                        logger.critical("STOPPING: BUY value < MinNotional at ref price. Pot too small.")
                        sys.exit(1)
                      
                    valid = safe_execution_checks(qty_to_buy, Decimal(str(exec_mid)), min_notional_decimal, target_notional_decimal)
                    if not valid: 
                         time.sleep(CANDLE_POLL_SECONDS)
                         continue
                    
                    order = execute_limit(client, 'BUY', qty_to_buy, step_size_decimal, step_size_str, tick_size_decimal, ref_price, avg_price_cap_min, avg_price_cap_max)
                    
                    if order and order.get('status') == 'CANCELED_TIMEOUT_NOFILL':
                        logger.info("BUY TIMEOUT")
                        time.sleep(CANDLE_POLL_SECONDS)
                        continue
                        
                    if order:
                        # Filled
                        cumm_quote = float(order.get('cummulativeQuoteQty', 0.0))
                        exec_qty = float(order.get('executedQty', 0.0))
                        fills = order.get('fills', [])
                        avg_price = get_avg_exec_price(fills)
                        if avg_price == 0: avg_price = cumm_quote / exec_qty if exec_qty else current_price
                        
                        bot.pot_quote -= cumm_quote
                        if bot.pot_quote < 0: bot.pot_quote = 0.0
                        bot.pot_eth += exec_qty
                        normalize_pots(bot, step_size_decimal)
                        
                        if cumm_quote >= MIN_FILL_QUOTE and exec_qty >= float(step_size_decimal):
                            bot.entry_price = avg_price
                            bot.state = "HOLDING_ETH"
                            logger.info(f"BUY SUCCESS. Entry: {bot.entry_price:.2f}")
                        
                        bot.last_trade_time = time.time()
                        bot.save()

            elif bot.state == "HOLDING_ETH":
                take_profit_price = bot.entry_price * (1 + TAKE_PROFIT_PCT)
                stop_loss_price = bot.entry_price * (1 - STOP_LOSS_PCT)
                
                # SELL EVAL Log
                sig_log = "NONE"
                if current_price >= take_profit_price: sig_log = "TAKE_PROFIT"
                elif current_price <= stop_loss_price: sig_log = "STOP_LOSS"
                logger.info(f"SELL_EVAL | Price:{current_price:.2f} | Entry:{bot.entry_price:.2f} | TP:{take_profit_price:.2f} | SL:{stop_loss_price:.2f} | Signal:{sig_log}")
                
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
                    d_exec_mid = Decimal(str(exec_mid))
                    
                    if qty_to_sell <= 0:
                        logger.critical("STOPPING: SELL Quantity rounds to 0.")
                        sys.exit(1)

                    if qty_to_sell * d_exec_mid < min_notional_decimal:
                        logger.critical(f"STOPPING: SELL Value < MinNotional.")
                        sys.exit(1)
                    
                    valid = safe_execution_checks(qty_to_sell, d_exec_mid, min_notional_decimal, target_notional_decimal)
                    if not valid:
                         time.sleep(CANDLE_POLL_SECONDS)
                         continue
                    
                    ref_price = max(ask, Decimal(str(current_price)))
                    
                    order = execute_limit(client, 'SELL', qty_to_sell, step_size_decimal, step_size_str, tick_size_decimal, ref_price, avg_price_cap_min, avg_price_cap_max)
                    
                    if order and order.get('status') == 'CANCELED_TIMEOUT_NOFILL':
                        logger.info("SELL TIMEOUT")
                        time.sleep(CANDLE_POLL_SECONDS)
                        continue

                    if order is None:
                        bot.error_timestamps.append(time.time())
                        check_errors(bot)
                        time.sleep(CANDLE_POLL_SECONDS)
                        continue

                    # SUCCESS
                    cumm_quote = float(order.get('cummulativeQuoteQty', 0.0))
                    exec_qty = float(order.get('executedQty', 0.0))
                    fills = order.get('fills', [])
                    avg_price = get_avg_exec_price(fills)
                    if avg_price == 0: avg_price = cumm_quote / exec_qty if exec_qty else current_price
                    
                    pnl = (avg_price - bot.entry_price) * exec_qty
                    if pnl < 0: bot.daily_loss_quote += abs(pnl)
                    
                    bot.pot_eth -= exec_qty
                    bot.pot_quote += cumm_quote
                    bot.last_sell_price = avg_price
                    normalize_pots(bot, step_size_decimal)
                    
                    is_dust = (bot.pot_eth == 0) or (float(bot.pot_eth) * float(exec_mid) < float(min_notional_decimal))
                    
                    if cumm_quote >= MIN_FILL_QUOTE and is_dust:
                            bot.state = "HOLDING_QUOTE"
                            bot.trade_count += 1
                            logger.info(f"SELL SUCCESS ({sell_reason}). PnL: {pnl:.4f}. Trades Today: {bot.trade_count}")
                    else:
                            logger.warning(f"SELL PARTIAL ({cumm_quote:.2f} {QUOTE_ASSET}). Dust/Rem: {is_dust}. Stay in HOLDING_ETH.")

                    bot.last_trade_time = time.time()
                    bot.save()
            
            # End of cycle logic sleep
            time.sleep(CANDLE_POLL_SECONDS)

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
