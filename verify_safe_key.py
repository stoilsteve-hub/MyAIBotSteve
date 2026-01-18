from binance.spot import Spot
from dotenv import load_dotenv
import os
import sys

load_dotenv()

try:
    client = Spot(os.getenv("API_KEY"), os.getenv("API_SECRET"))
    acc = client.account()
    
    can_trade = acc.get('canTrade')
    can_withdraw = acc.get('canWithdraw')
    
    print("\n--- Key Security Audit ---")
    print(f"Can Trade: {can_trade}")
    print(f"Can Withdraw: {can_withdraw}")
    
    if can_withdraw is True:
        print("\n❌ CRITICAL FAIL: Withdrawals are ENABLED. Do not use this key.")
        sys.exit(1)
    
    if can_trade is False:
        print("\n❌ TRADING FAIL: Spot Trading is DISABLED. Enable it.")
        sys.exit(1)
        
    print("\n✅ KEY IS SAFE (Trade: Yes, Withdraw: No)")
    
except Exception as e:
    print(f"Error checking key: {e}")
    sys.exit(1)
