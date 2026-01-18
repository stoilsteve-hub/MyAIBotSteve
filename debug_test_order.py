from binance.spot import Spot
from binance.error import ClientError
from dotenv import load_dotenv
import os
import sys

load_dotenv()

client = Spot(os.getenv("API_KEY"), os.getenv("API_SECRET"))
SYMBOL = "ETHUSDT"

def debug_test_order():
    try:
        # Get Price
        ticker = client.book_ticker(SYMBOL)
        bid = float(ticker['bidPrice'])
        print(f"Current Bid: {bid}")

        # Construct VALID params
        # We'll test a SELL of 0.003 ETH (approx $10). 
        # This mirrors the funding sell the bot was trying.
        qty = "0.0030" # String format
        
        method_name = "client.new_order_test (POST /api/v3/order/test)"
        params = {
            "symbol": SYMBOL,
            "side": "SELL",
            "type": "MARKET",
            "quantity": qty
        }
        
        print(f"\n[DEBUG] Invoking Method: {method_name}")
        print(f"[DEBUG] Params: {params}")
        
        # This endpoint validates parameters but DOES NOT place order
        response = client.new_order_test(**params)
        print(f"[DEBUG] Raw Response: {response}")
        print("\n✅ TEST ORDER ACCEPTED by Binance API")
        print("This confirms keys, permissions, and parameters are valid.")
        
    except ClientError as e:
        print(f"\n❌ TEST ORDER REJECTED: {e}")
        print(f"Error Code: {e.error_code}")
        print(f"Error Message: {e.error_message}")
        print(f"Full Header: {e.header}")
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")

if __name__ == "__main__":
    debug_test_order()
