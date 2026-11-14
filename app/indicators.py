"""Native Python reimplementation of a *subset* of the uploaded Pine indicator.

This does NOT execute Pine syntax, nor claim parity with TradingView's entire
42-page script. Ported parameters: SMA(7,20), EMA(50,200), BB(20,2), ATR(50),
VWAP and confirmed pivot/Swing BOS/CHoCH (lengths 50 and 5).
Other features are research adaptations, explicitly labeled in the API.
"""
from __future__ import annotations
from typing import Any
import numpy as np
import pandas as pd


def sma(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(length, min_periods=length).mean()

def ema(s: pd.Series, length: int) -> pd.Series:
    # Matches standard exponential MA smoothing; first-value initialization may
    # differ slightly from Pine ta.ema when history is short.
    return s.ewm(span=length, adjust=False, min_periods=length).mean()

def rma(s: pd.Series, length: int) -> pd.Series:
    # Wilder smoothing seeded by the first complete SMA, as in Pine ATR.
    src = pd.to_numeric(s, errors='coerce').astype(float)
    result = np.full(len(src), np.nan)
    vals = src.to_numpy()
    if len(vals) < length: return pd.Series(result, index=src.index)
    for i in range(length - 1, len(vals)):
        if i == length - 1:
            if np.all(np.isfinite(vals[:length])): result[i] = np.mean(vals[:length])
        else:
            if np.isfinite(vals[i]) and np.isfinite(result[i-1]):
                result[i] = (result[i-1] * (length-1) + vals[i]) / length
    return pd.Series(result, index=src.index)

def atr(df: pd.DataFrame, length: int = 50) -> pd.Series:
    pclose = df['close'].shift(1)
    ranges = pd.concat([(df['high']-df['low']), (df['high']-pclose).abs(),
                        (df['low']-pclose).abs()], axis=1)
    return rma(ranges.max(axis=1), length)

def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    diff = close.diff()
    ups = diff.clip(lower=0)
    downs = (-diff).clip(lower=0)
    # Seed at first 14 changes (ignore first NaN) to avoid early bias.
    avg_u = rma(ups.iloc[1:].reset_index(drop=True), length)
    avg_d = rma(downs.iloc[1:].reset_index(drop=True), length)
    avg_u = pd.Series([np.nan] + avg_u.tolist(), index=close.index)
    avg_d = pd.Series([np.nan] + avg_d.tolist(), index=close.index)
    rs = avg_u / avg_d.replace(0, np.nan)
    out = 100 - (100/(1+rs))
    out[(avg_d==0) & (avg_u>0)] = 100
    out[(avg_u==0) & (avg_d>0)] = 0
    out[(avg_u==0) & (avg_d==0)] = 50
    return out

def confirmed_pivots(high: pd.Series, low: pd.Series, length: int):
    """Pivots only become usable length bars after the extreme (no lookahead)."""
    n = len(high)
    highs, lows = {}, {}
    h, l = high.to_numpy(), low.to_numpy()
    for mid in range(length, n - length):
        start = mid-length; end = mid+length+1
        if np.isfinite(h[mid]) and h[mid] >= np.max(h[start:end]) and np.sum(h[start:end] == h[mid]) == 1:
            highs[mid + length] = (mid, float(h[mid]))
        if np.isfinite(l[mid]) and l[mid] <= np.min(l[start:end]) and np.sum(l[start:end] == l[mid]) == 1:
            lows[mid + length] = (mid, float(l[mid]))
    return highs, lows

def structure_events(df: pd.DataFrame, length: int):
    """Events analogous to Pine's confirmed swing BOS and CHoCH alerts.

    Pine's LuxAlgo-based `swings` method is not identical to this strict
    pivot-confirmation adaptation. All uses of this detector are labeled.
    """
    highs, lows = confirmed_pivots(df['high'],df['low'],length)
    latest_high, latest_low = None, None
    high_active = low_active = False
    trend = 0
    events = []
    close = df['close'].to_numpy()
    for i in range(len(df)):
        if i in highs:
            latest_high = highs[i][1]; high_active=True
        if i in lows:
            latest_low = lows[i][1]; low_active=True
        if i == 0: continue
        if high_active and latest_high is not None and close[i] > latest_high and close[i-1] <= latest_high:
            kind = 'Bullish CHoCH' if trend<0 else 'Bullish BOS'
            events.append({'bar':i,'type':kind,'level':latest_high})
            high_active=False; trend=1
        if low_active and latest_low is not None and close[i] < latest_low and close[i-1] >= latest_low:
            kind = 'Bearish CHoCH' if trend>0 else 'Bearish BOS'
            events.append({'bar':i,'type':kind,'level':latest_low})
            low_active=False;trend=-1
    return events, {'last_swing_high':latest_high,'last_swing_low':latest_low,'trend':trend}

def supply_demand_zones(df:pd.DataFrame, length:int=10, width_atr:float=0.5):
    """Adapted Fluid-Trade zone geometry from the source:

    Confirm pivot swing_length=10, ATR50 buffer=box_width/10=0.5,
    reject overlapping POIs within ATR*2, invalidate when close breaks zone.
    The source uses TradingView box-array state with additional nuances.
    """
    highs, lows = confirmed_pivots(df['high'], df['low'], length)
    atr_ = atr(df, 50)
    zones=[]
    broken=[]
    for i in range(len(df)):
        price = float(df['close'].iat[i])
        for z in zones[:]:
            if (z['type']=='supply' and price >= z['top']) or (z['type']=='demand' and price <= z['bottom']):
                broken.append({'bar':i,'type':f"{z['type'].title()} zone broken",'level':z['poi']})
                zones.remove(z)
        for side, event in [('supply',highs.get(i)),('demand',lows.get(i))]:
            if not event or pd.isna(atr_.iat[i]): continue
            mid, level=event
            buffer = float(atr_.iat[i]) * width_atr
            top = level if side=='supply' else level+buffer
            bottom = level-buffer if side=='supply' else level
            poi=(top+bottom)/2
            if any(abs(poi-z['poi'])<=float(atr_.iat[i])*2 for z in zones):continue
            zones.insert(0,{'type':side,'top':top,'bottom':bottom,'poi':poi,'bar':mid})
            zones=zones[:20]
    return zones,broken

def fvg_events(df:pd.DataFrame):
    """Simple same-timeframe fair-value gap geometry.

    The uploaded Pine indicator defaults show_fvg=false and has additional
    multi-timeframe/auto-threshold logic, which this port does not implement.
    """
    events=[]
    for i in range(2,len(df)):
        if df['low'].iat[i]>df['high'].iat[i-2] and df['close'].iat[i-1]>df['high'].iat[i-2]:
            events.append({'bar':i,'type':'Bullish FVG','low':float(df['high'].iat[i-2]),'high':float(df['low'].iat[i])})
        elif df['high'].iat[i]<df['low'].iat[i-2] and df['close'].iat[i-1]<df['low'].iat[i-2]:
            events.append({'bar':i,'type':'Bearish FVG','low':float(df['high'].iat[i]),'high':float(df['low'].iat[i-2])})
    return events

def add_indicators(original:pd.DataFrame, interval:str|None=None) -> pd.DataFrame:
    df = original.copy().reset_index(drop=True)
    for field in ('open','high','low','close','volume'):
        df[field] = pd.to_numeric(df[field],errors='coerce').astype(float)
    c = df['close']
    df['sma7'] = sma(c,7)
    df['sma20'] = sma(c,20)
    df['ema50'] = ema(c,50)
    df['ema200'] = ema(c,200)
    df['bb_middle'] = sma(c,20)
    # Pine ta.stdev uses population SD by default (biased=true).
    dev = c.rolling(20,min_periods=20).std(ddof=0)*2
    df['bb_upper'] = df['bb_middle'] + dev
    df['bb_lower'] = df['bb_middle'] - dev
    df['bb_width_pct'] = (df['bb_upper']-df['bb_lower']) / df['bb_middle'] * 100
    df['atr50'] = atr(df,50)
    df['rsi14'] = rsi(c,14)  # Requested earlier; not in uploaded Pine indicator's executable rules.
    if 'time' in df.columns and interval not in ('1d','1mo'):
        # Session reset VWAP: only meaningful for intraday candles.
        typical = (df['high']+df['low']+c)/3
        session = pd.to_datetime(df['time']).dt.date
        vol = df['volume'].clip(lower=0)
        cv = vol.groupby(session).cumsum()
        df['vwap'] = ((typical*vol).groupby(session).cumsum()/cv.replace(0,np.nan))
    if 'vwap' not in df.columns:
        df['vwap'] = np.nan
    return df

def _n(v: Any):
    if v is None: return None
    try:
        if pd.isna(v) or not np.isfinite(float(v)):return None
        return round(float(v),4)
    except (TypeError,ValueError): return None

def snapshot(df:pd.DataFrame, interval:str) -> dict:
    d=add_indicators(df,interval)
    last=d.iloc[-1]
    swing_events, state = structure_events(d,50)
    internal_events, istate= structure_events(d,5)
    fvgs = fvg_events(d)
    zones,zone_breaks=supply_demand_zones(d)
    active_demand=[z for z in zones if z["type"]=="demand"]
    inside_demand=bool(any(z["bottom"]<=last["close"]<=z["top"] for z in active_demand))
    lookback= min(60,max(0,len(d)-21))
    bw=d['bb_width_pct']
    current_bw=bw.iloc[-1]
    prior=bw.iloc[-(lookback+1):-1].dropna() if lookback else pd.Series(dtype=float)
    squeeze= bool(len(prior)>=24 and np.isfinite(current_bw) and current_bw<=prior.quantile(0.20))
    up=bool(pd.notna(last['bb_upper']) and last['close']>last['bb_upper'])
    down=bool(pd.notna(last['bb_lower']) and last['close']<last['bb_lower'])
    bullish_smc=[e for e in (swing_events + internal_events) if e['bar']>=len(d)-3 and e['type'] in ['Bullish BOS','Bullish CHoCH']]
    bearish_smc=[e for e in (swing_events + internal_events) if e['bar']>=len(d)-3 and e['type'] in ['Bearish BOS','Bearish CHoCH']]
    recent_fvgs=[e for e in fvgs if e['bar']>=len(d)-3]
    monthly_condition = bool(interval=='1mo' and squeeze and pd.notna(last['sma20']) and last['close']>last['sma20'] and pd.notna(last['rsi14']) and last['rsi14']>40)
    indicators={key:_n(last[key]) for key in ('sma7','sma20','ema50','ema200','rsi14','atr50','vwap','bb_middle','bb_upper','bb_lower','bb_width_pct')}
    events=[]
    for e in (swing_events[-6:]+internal_events[-6:]+fvgs[-6:]):
        if e['bar']>=len(d)-12:
            events.append({'type':e['type'],'time':str(d['time'].iat[e['bar']]),'level':_n(e.get('level'))})
    return {
        'interval':interval,'close':_n(last['close']),
        'conditions':{
            'bb_above':up,'bb_below':down,'monthly_squeeze_candidate':monthly_condition,
            'squeeze_bandwidth_low':squeeze,
            'bullish_structure_recent':bool(bullish_smc),'bearish_structure_recent':bool(bearish_smc),
            'inside_demand_zone':inside_demand,
            'bullish_fvg_recent':any(x['type']=='Bullish FVG' for x in recent_fvgs),
            'bearish_fvg_recent':any(x['type']=='Bearish FVG' for x in recent_fvgs),
        },'indicators':indicators,'events':sorted(events,key=lambda x:x['time'],reverse=True)[:10],
        'smc_state':{'swing':state,'internal':istate},
        'zones':[{k:_n(v) if k in ('top','bottom','poi') else v for k,v in z.items()} for z in zones[:8]],
        'candle_count':len(d),
        'last_bar_time':pd.Timestamp(last['time']).isoformat(),
        'data_staleness_warning':'Last candle may be incomplete; scanner conditions are preliminary until bar close.'
    }

def chart_payload(df:pd.DataFrame,interval:str,limit:int=220)->dict:
    d=add_indicators(df,interval)
    d=d.tail(limit)
    def out_time(v):return int(pd.Timestamp(v).timestamp())
    candles=[{'time':out_time(r.time),'open':_n(r.open),'high':_n(r.high),'low':_n(r.low),'close':_n(r.close)} for r in d.itertuples()]
    def line(key):return [{'time':out_time(r.time),'value':_n(getattr(r,key))} for r in d.itertuples() if _n(getattr(r,key)) is not None]
    return {'interval':interval,'candles':candles,'series':{k:line(k) for k in ['bb_upper','bb_middle','bb_lower','sma20','ema50','ema200']},'snapshot':snapshot(df,interval)}
