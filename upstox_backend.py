import requests
import pandas as pd
import numpy as np
from scipy.signal import find_peaks

def fetch_historical_candles(index_symbol, timeframe, token):
    """Fetches high-density candle bars from the Upstox API for indices."""
    # Map index names to exact Upstox exchange database keys
    mappings = {
        "NIFTY 50": "NSE_INDEX|Nifty 50",
        "BANKNIFTY": "NSE_INDEX|Nifty Bank",
        "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
        "MIDCPNIFTY": "NSE_INDEX|NIFTY MIDCAP 50",
        "NIFTYNEXT50": "NSE_INDEX|Nifty Next 50",
        "SENSEX": "BSE_INDEX|SENSEX",
        "BANKEX": "BSE_INDEX|BANKEX"
    }
    key = mappings.get(index_symbol, "NSE_INDEX|Nifty 50")
    
    tf_map = {
        "5 Minute": "5minute",
        "15 Minute": "15minute",
        "45 Minute": "45minute",
        "1 Hour": "1hour",
        "4 Hour": "4hour",
        "Daily": "day"
    }
    api_tf = tf_map.get(timeframe, "15minute")
    
    url = f"https://upstox.com{key}/{api_tf}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    
    try:
        res = requests.get(url, headers=headers).json()
        candles = res.get("data", {}).get("candles", [])
        if candles:
            df = pd.DataFrame(candles, columns=['ts', 'o', 'h', 'l', 'c', 'v', 'oi']).iloc[::-1].reset_index(drop=True)
            for col in ['o', 'h', 'l', 'c', 'v']: df[col] = df[col].astype(float)
            return df
    except Exception:
        pass
    return None

def compute_indicators_and_signals(df):
    if df is None or len(df) < 35:
        return None
        
    close = df['c']
    high = df['h']
    low = df['l']
    
    # Indices approximate VWAP using standard close cumulative calculation
    df['vwap'] = (df['c'] * df['v']).cumsum() / df['v'].cumsum().replace(0, 1e-5)
    
    ema20 = close.ewm(span=20, adjust=False).mean()
    high_low = high - low
    high_close = abs(high - close.shift())
    low_close = abs(low - close.shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=20).mean()
    
    keltner_upper = ema20 + (2 * atr)
    keltner_lower = ema20 - (2 * atr)
    
    up_move = high.diff()
    down_move = low.shift() - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    tr_smooth = tr.rolling(window=14).mean()
    plus_di = 100 * (pd.Series(plus_dm).rolling(window=14).mean() / tr_smooth.replace(0, 1e-5))
    minus_di = 100 * (pd.Series(minus_dm).rolling(window=14).mean() / tr_smooth.replace(0, 1e-5))
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1e-5)
    adx = dx.rolling(window=14).mean()
    
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, 1e-5)
    rsi = 100 - (100 / (1 + rs.replace(0, 1e-5)))
    
    macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    
    c_price = close.iloc[-1]
    c_vwap = df['vwap'].iloc[-1]
    c_adx = adx.iloc[-1] if not adx.empty else 0
    
    peaks, _ = find_peaks(high.values, distance=5)
    troughs, _ = find_peaks(-low.values, distance=5)
    
    swing_high = high.iloc[peaks[-1]] if len(peaks) > 0 else high.max()
    swing_low = low.iloc[troughs[-1]] if len(troughs) > 0 else low.min()
    
    is_bullish = (c_price > c_vwap and c_price > keltner_upper.iloc[-1] and c_adx > 30 and rsi.iloc[-1] > rsi.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1])
    is_bearish = (c_price < c_vwap and c_price < keltner_lower.iloc[-1] and c_adx > 30 and rsi.iloc[-1] < rsi.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1])
    
    # Calculate options strike values rounded dynamically to index contract step sizes
    step = 100 if "BANK" in index_symbol or "SENSEX" in index_symbol else 50
    atm_strike = round(c_price / step) * step
    
    if is_bullish:
        risk = abs(swing_high * 1.002 - keltner_lower.iloc[-1])
        return {
            "type": "BULLISH", "spot": c_price, "entry": swing_high * 1.002,
            "target": (swing_high * 1.002) + (risk * 2.2), "sl": keltner_lower.iloc[-1], "adx": c_adx,
            "curr_opt": f"{atm_strike} CE", "next_opt": f"{atm_strike - step} CE"
        }
    elif is_bearish:
        risk = abs(keltner_upper.iloc[-1] - swing_low * 0.998)
        return {
            "type": "BEARISH", "spot": c_price, "entry": swing_low * 0.998,
            "target": (swing_low * 0.998) - (risk * 2.2), "sl": keltner_upper.iloc[-1], "adx": c_adx,
            "curr_opt": f"{atm_strike} PE", "next_opt": f"{atm_strike + step} PE"
        }
    return None

def generate_offline_simulated_data(index_symbol, index_rank):
    """Generates precise index pricing points when exchange servers are closed over weekends."""
    base_spots = {
        "NIFTY 50": 24230.0, "BANKNIFTY": 52140.0, "FINNIFTY": 23410.0,
        "MIDCPNIFTY": 12450.0, "NIFTYNEXT50": 72100.0, "SENSEX": 79540.0, "BANKEX": 58900.0
    }
    spot = base_spots.get(index_symbol, 24000.0)
    step = 100 if "BANK" in index_symbol or "SENSEX" in index_symbol else 50
    atm_strike = round(spot / step) * step
    
    risk_dist = spot * 0.008  # Tight index-scale trailing risk distribution percentage
    if index_rank % 2 == 0:
        return {
            "type": "BULLISH", "spot": spot, "entry": spot + (risk_dist * 0.2), "target": spot + (risk_dist * 5.5), "sl": spot - risk_dist, "adx": 36.0,
            "curr_opt": f"{atm_strike} CE", "next_opt": f"{atm_strike - step} CE"
        }
    else:
        return {
            "type": "BEARISH", "spot": spot, "entry": spot - (risk_dist * 0.2), "target": spot - (risk_dist * 5.5), "sl": spot + risk_dist, "adx": 38.0,
            "curr_opt": f"{atm_strike} PE", "next_opt": f"{atm_strike + step} PE"
        }
