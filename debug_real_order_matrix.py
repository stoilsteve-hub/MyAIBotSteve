import os
import sys
import time
from decimal import Decimal
from dotenv import load_dotenv
from binance.spot import Spot
from binance.error import ClientError

# 1. Explicit Env Load
ENV_PATH = os.path.join(os.getcwd(), ".env")
load_dotenv(dotenv_path=ENV_PATH)

# Config
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
BASE_URL = os.getenv("BASE_URL", "https://api.binance.com")
DRY_RUN = os.getenv("DRY_RUN")
LIVE_TRADING = os.getenv("LIVE_TRADING")

print(f"\nTime: {time.strftime('%H:%M:%S')}")
print(f"Base URL: {BASE_URL}")
if not API_KEY:
    print("❌ API_KEY missing")
    sys.exit(1)

client = Spot(API_KEY, API_SECRET, base_url=BASE_URL)

SYMBOLS = ["ETHUSDT", "BTCUSDT", "ETHEUR"]

def get_precision(step_str):
    s = step_str.rstrip('0')
    if '.' not in s: return 0
    return len(s.split('.')[1])

def get_params_for_symbol(symbol):
    try:
        # 1. Fetch Filters
        info = client.exchange_info(symbol=symbol)
        s_info = info['symbols'][0]
        if s_info['status'] != 'TRADING':
            return None, f"Status={s_info['status']}"

        filters = s_info['filters']
        
        # PRICE_FILTER
        f_price = next(f for f in filters if f['filterType'] == 'PRICE_FILTER')
        tick_size = Decimal(f_price['tickSize'])
        price_prec = get_precision(f_price['tickSize'])

        # LOT_SIZE
        f_lot = next(f for f in filters if f['filterType'] == 'LOT_SIZE')
        step_size = Decimal(f_lot['stepSize'])
        min_qty = Decimal(f_lot['minQty'])
        qty_prec = get_precision(f_lot['stepSize'])

        # NOTIONAL
        f_not = next((f for f in filters if f['filterType'] == 'NOTIONAL'), None)
        if not f_not:
             f_not = next((f for f in filters if f['filterType'] == 'MIN_NOTIONAL'), None)
        min_notional = Decimal(f_not['minNotional']) if f_not else Decimal("5.0")

        # 2. Get Market Price
        ticker = client.book_ticker(symbol)
        ask_price = Decimal(ticker['askPrice']) # Use Ask to set Sell Limit high
        
        # 3. Calculate Safe LIMIT Price (10x Ask)
        # Ensure it doesn't violate maxPrice if that filter exists (usually huge)
        limit_price_raw = ask_price * Decimal("10.0")
        # Round down to tickSize
        limit_price = (limit_price_raw // tick_size) * tick_size
        
        # 4. Calculate Safe Qty
        # Must meet minNotional: Qty * Price >= minNotional
        # Qty >= minNotional / Price
        # Buffer 10%
        req_by_notional = (min_notional * Decimal("1.1")) / limit_price
        
        safe_qty_raw = max(min_qty, req_by_notional)
        
        # Round up to stepSize to be safe? Or down?
        # Usually round down for sells to match wallet, but here we don't care about balance limits much (assuming we have dust)
        # But wait, we need to have this balance.
        # If user has 0.009 ETH, and we calculate 1000 ETH requirement -> fail.
        # But Price is HUGE (10x), so Qty will be TINY. 
        # e.g. ETH=$3000. Limit=$30,000. MinNotional=$5. Qty needed = 5/30000 = 0.00016
        # This should be fine for balance.
        
        # Round to stepSize
        safe_qty = (safe_qty_raw // step_size) * step_size
        if safe_qty < min_qty:
             # If rounding down made it < min_qty, add one step
             safe_qty += step_size

        # Format Strings
        p_str = "{:.{p}f}".format(limit_price, p=price_prec)
        q_str = "{:.{p}f}".format(safe_qty, p=qty_prec)
        
        return {
            "symbol": symbol,
            "side": "SELL",
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": q_str,
            "price": p_str
        }, None

    except Exception as e:
        return None, f"CalcError: {e}"

def run_matrix():
    print(f"{'SYMBOL':<10} | {'PRICE':<10} | {'QTY':<10} | {'TEST (/order/test)':<20} | {'REAL (/order)':<15} | {'CANCEL'}")
    print("-" * 90)

    for sym in SYMBOLS:
        params, err = get_params_for_symbol(sym)
        if not params:
            print(f"{sym:<10} | {'N/A':<10} | {'N/A':<10} | SKIP ({err})")
            continue

        p_disp = params['price']
        q_disp = params['quantity']
        
        # 1. TEST
        test_res = "FAIL"
        test_err = ""
        try:
            client.new_order_test(**params)
            test_res = "PASS"
        except ClientError as e:
            test_res = "FAIL"
            test_err = f"{e.error_code}: {e.error_message}"
        except Exception as e:
             test_res = "ERR"
             test_err = str(e)
             
        # 2. REAL
        real_res = "SKIP"
        cancel_res = "-"
        
        if test_res == "PASS":
            try:
                # PLACE
                order = client.new_order(**params)
                real_res = "PASS (ID:{})".format(str(order['orderId'])[-4:])
                
                # CANCEL
                try:
                    client.cancel_order(symbol=sym, orderId=order['orderId'])
                    cancel_res = "PASS"
                except Exception as c_e:
                    cancel_res = f"FAIL {c_e}"

            except ClientError as e:
                real_res = f"FAIL {e.error_code}"
                # If -2010, detailed message is key
                if e.error_code == -2010:
                    real_res = "FAIL -2010 (Perms)"
            except Exception as e:
                real_res = f"ERR {str(e)[:15]}"

        # Print Row
        # If failure, print error on next line
        print(f"{sym:<10} | {p_disp:<10} | {q_disp:<10} | {test_res:<20} | {real_res:<15} | {cancel_res}")
        if test_err:
            print(f"    >>> TEST ERROR: {test_err}")

if __name__ == "__main__":
    if str(DRY_RUN) != '0' or LIVE_TRADING != 'YES':
         print("⚠️  Set DRY_RUN=0 and LIVE_TRADING=YES to run.")
    else:
         run_matrix()
