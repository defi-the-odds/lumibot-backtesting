import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from pytz import utc
from lumibot.backtesting import YahooDataBacktesting
from lumibot.strategies.strategy import Strategy

# =============================================================================
# CONFIGURATION
# =============================================================================
API_KEY = "dfo_xxxxxxxxxxxxxxxx" # Use your API KEY :)
BASE_URL = "https://api.defitheodds.xyz/v1"
SYMBOL = "BTC-USD"
CANDLES = 3000

class DefiTheOddsStrategy(Strategy):
    """
    Continuous Rebalancing Regime Strategy.
    Instead of binary Buy/Sell, this strategy adjusts position size daily 
    to match the 'confidence' of the Market Regime Score.
    """
    
    def initialize(self, enriched_df=None):
        self.sleeptime = "1D"
        self.enriched_df = enriched_df
        # Risk Settings
        self.min_regime_to_hold = 35 # Absolute floor to be in the market
        self.target_weights = {
            'bull': 0.95,      # Score > 75
            'neutral': 0.40,   # Score 50-75
            'caution': 0.15,   # Score 35-50
            'exit': 0.00       # Score < 35
        }

    def on_trading_iteration(self):
        current_dt = self.get_datetime()
        lookup_dt = current_dt.astimezone(utc).replace(hour=0, minute=0, second=0, microsecond=0)
        
        if self.enriched_df is None:
            return

        try:
            # We look at a 3-day window to smooth out "one-day" regime flickers
            window = self.enriched_df.loc[:lookup_dt].tail(3)
            if len(window) < 3:
                return
            
            data = window.iloc[-1]
            avg_regime = window['market_regime_score'].mean()
        except KeyError:
            return

        # Extract values
        close = data.get("close")
        sma50 = data.get("sma_50")
        sma200 = data.get("sma_200")
        
        if pd.isna(close):
            return

        # ---------------------------------------------------------
        # 1. CALCULATE TARGET EXPOSURE
        # We determine how much of our portfolio SHOULD be in BTC
        # ---------------------------------------------------------
        if avg_regime > 75:
            target_pct = self.target_weights['bull']
        elif avg_regime > 50:
            target_pct = self.target_weights['neutral']
        elif avg_regime > 35:
            # Only stay in "Caution" if the trend (SMA) is still technically positive
            target_pct = self.target_weights['caution'] if sma50 > sma200 else 0.0
        else:
            target_pct = 0.0

        # ---------------------------------------------------------
        # 2. CONTINUOUS REBALANCING LOGIC
        # ---------------------------------------------------------
        current_pos = self.get_position(SYMBOL)
        current_shares = current_pos.quantity if current_pos else 0
        
        # Calculate what our share count should be
        portfolio_value = self.portfolio_value
        target_value = portfolio_value * target_pct
        target_shares = target_value // close
        
        share_diff = target_shares - current_shares

        # Only execute if the change is significant (> 5% of portfolio) to avoid over-trading
        if abs(share_diff * close) > (portfolio_value * 0.05):
            if share_diff > 0:
                # Buying to reach target
                order = self.create_order(SYMBOL, share_diff, "buy")
                self.submit_order(order)
                self.log_message(f"REBALANCE: Increasing exposure to {target_pct*100}% (Regime: {avg_regime:.2f})")
            elif share_diff < 0:
                # Selling to reach target
                order = self.create_order(SYMBOL, abs(share_diff), "sell")
                self.submit_order(order)
                self.log_message(f"REBALANCE: Reducing exposure to {target_pct*100}% (Regime: {avg_regime:.2f})")

    def on_abrupt_closing(self):
        self.sell_all()

def get_defitheodds_data():
    """Fetches data from the API and formats it for Lumibot."""
    url = f"{BASE_URL}/daily/{SYMBOL}/{CANDLES}"
    headers = {"X-API-KEY": API_KEY}
    
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"API Error: {response.text}")
        
    raw_data = response.json()["data"]
    df = pd.DataFrame(raw_data)
    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)
    
    cols_to_convert = df.columns.drop(['ticker', 'candle']) if 'candle' in df.columns else df.columns.drop(['ticker'])
    df[cols_to_convert] = df[cols_to_convert].apply(pd.to_numeric, errors='coerce')
    
    if df.index.tz is None:
        df.index = df.index.tz_localize(utc)
    
    return df

if __name__ == "__main__":
    try:
        data_df = get_defitheodds_data()
        start_date = data_df.index.min()
        end_date = data_df.index.max()
        
        print(f"Backtesting {SYMBOL} with Continuous Rebalancing...")

        result = DefiTheOddsStrategy.backtest(
            YahooDataBacktesting, 
            start_date,
            end_date,
            pandas_data=data_df, 
            name="ContinuousRegimeRebalance",
            enriched_df=data_df,
            benchmark_asset=SYMBOL 
        )
    except Exception as e:
        print(f"Error: {e}")
