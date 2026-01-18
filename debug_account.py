from binance.spot import Spot
from dotenv import load_dotenv
import os
import json

load_dotenv()
client = Spot(os.getenv("API_KEY"), os.getenv("API_SECRET"))

try:
    print("Checking Account & Symbol Permissions...")
    
    # 1. Account Info
    acc = client.account()
    print("\n--- Account Status ---")
    print(f"Can Trade: {acc.get('canTrade')}")
    print(f"Can Withdraw: {acc.get('canWithdraw')}")
    print(f"Can Deposit: {acc.get('canDeposit')}")
    print(f"Account Type: {acc.get('accountType')}")
    print(f"Permissions: {acc.get('permissions')}")
    
    print(f"\n--- Balances ---")
    for b in acc['balances']:
        if b['asset'] in ['ETH', 'USDT']:
            print(f"{b['asset']}: Free={b['free']}, Locked={b['locked']}")
            
    # 2. Symbol Info
    info = client.exchange_info(symbol="ETHUSDT")
    s = info['symbols'][0]
    print("\n--- Symbol Info (ETHUSDT) ---")
    print(f"Status: {s['status']}")
    print(f"Is Spot Trading Allowed: {s['isSpotTradingAllowed']}")
    print(f"Is Margin Trading Allowed: {s['isMarginTradingAllowed']}")
    
except Exception as e:
    print(f"\nERROR: {e}")
