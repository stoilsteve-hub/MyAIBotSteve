from binance.spot import Spot
from binance.error import ClientError
from decimal import Decimal, ROUND_DOWN
from dotenv import load_dotenv
import os

load_dotenv()
client = Spot(os.getenv("API_KEY"), os.getenv("API_SECRET"))

SYMBOLS_TO_TEST = ["ETHUSDT", "BTCUSDT", "ETHBTC", "ETHEUR"]

def get_valid_qty(symbol):
    try:
        info = client.exchange_info(symbol=symbol)
        f = info['symbols'][0]['filters']
        
        lot = next(x for x in f if x['filterType'] == 'LOT_SIZE')
        notional = next((x for x in f if x['filterType'] == 'NOTIONAL'), None)
        if not notional:
            notional = next((x for x in f if x['filterType'] == 'MIN_NOTIONAL'), None)
            
        step_size = Decimal(lot['stepSize'])
        min_notional = Decimal(notional['minNotional'])
        
        # Calculate safer qty (approx $15 value or 0.005 ETH)
        ticker = client.book_ticker(symbol)
        price = Decimal(ticker['bidPrice'])
        
        target_val = max(min_notional * Decimal("1.2"), Decimal("12.0")) # Safe buffer
        
        raw_qty = target_val / price
        
        # Quantize
        qty = (raw_qty // step_size) * step_size
        return "{:f}".format(qty)
    except Exception as e:
        print(f"  [!] Skipped {symbol}: Could not calculate valid qty ({e})")
        return None

def test_symbol_permission():
    print("====================================================")
    print(" SYMBOL PERMISSION DIAGNOSTIC (using /order/test)")
    print("====================================================")
    
    for s in SYMBOLS_TO_TEST:
        print(f"\nTesting {s}...")
        
        qty = get_valid_qty(s)
        if not qty: continue
        
        try:
            params = {
                "symbol": s,
                "side": "SELL",
                "type": "MARKET",
                "quantity": qty
            }
            client.new_order_test(**params)
            print(f"  ✅ PASS: Account CAN trade {s}")
        except ClientError as e:
            print(f"  ❌ FAIL: {e}")
            if e.error_code == -2010:
                print("     -> ACCOUNT RESTRICTED from this symbol.")
        except Exception as e:
            print(f"  ❌ ERROR: {e}")

if __name__ == "__main__":
    test_symbol_permission()
