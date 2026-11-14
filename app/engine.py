from __future__ import annotations
import hashlib
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .indicators import add_indicators, snapshot, chart_payload, structure_events, fvg_events
from .shoonya import blend_live, client, MarketDataError, clean_symbol
from .research import fetch_research

IST=ZoneInfo('Asia/Kolkata')


def demo_frame(symbol:str,tf:str) -> pd.DataFrame:
    """Deterministic synthetic TEST data — never represent as a market quote."""
    seed=int(hashlib.sha256((symbol+'/'+tf).encode()).hexdigest()[:8],16)
    rng=np.random.default_rng(seed)
    n=270 if tf!='1mo' else 140
    freq={'15m':'15min','1h':'1h','1d':'B','1mo':'MS'}[tf]
    # All sample points firmly in the past; NOT a fake current-live bar.
    times=pd.date_range(end='2026-10-30',periods=n,freq=freq,tz=IST)
    trend=np.linspace(0,17,n)
    raw=130+trend+np.cumsum(rng.normal(0,1.1,n))
    opening=raw+rng.normal(0,.7,n)
    high=np.maximum(raw,opening)+rng.uniform(.15,1.2,n)
    low=np.minimum(raw,opening)-rng.uniform(.15,1.2,n)
    volume=rng.integers(4000,90000,n)
    return pd.DataFrame({'time':times,'open':opening,'high':high,'low':low,'close':raw,'volume':volume})


def fetch_frame(symbol:str,tf:str,demo:bool=False,offline:bool=False) -> tuple[pd.DataFrame,bool]:
    if offline:return demo_frame(symbol,tf),False
    if demo:return fetch_research(symbol,tf),False
    if not client.token:raise MarketDataError('Sign in to Shoonya to load real market candles.')
    raw=client.load_history(symbol,tf)
    tick=client.latest(symbol)
    rows=blend_live(raw,tick,tf,client.current_bar(symbol,tf))
    return pd.DataFrame(rows),bool(tick['fresh'])


def frame_summary(df:pd.DataFrame,tf:str) -> dict:
    s=snapshot(df,tf)
    row=df.iloc[-1]
    for key in ('open','high','low','close'):
        s[key]=float(row[key])
    return s


def build_chart(df:pd.DataFrame, tf:str) -> dict:
    p=chart_payload(df,tf,limit=320)
    d=add_indicators(df,tf)
    d['supertrend']=supertrend_approx(d)
    last=d.tail(320)
    series = p['series']
    for key in ('sma7','vwap','supertrend','rsi14'):
        items=[]
        for row in last.itertuples():
            raw=getattr(row,key)
            if pd.notna(raw):items.append({'time':int(pd.Timestamp(row.time).timestamp()),'value':round(float(raw),4)})
        series[key]=items
    swing,_=structure_events(d,5)
    markers=[]
    for e in swing[-25:]:
        if e['bar']>=len(d)-320:
            markers.append({'time':int(pd.Timestamp(d.time.iat[e['bar']]).timestamp()),
               'position':'belowBar' if e['type'].startswith('Bullish') else 'aboveBar',
               'color':'#43c59e' if e['type'].startswith('Bullish') else '#f17487',
               'shape':'arrowUp' if e['type'].startswith('Bullish') else 'arrowDown',
               'text':e['type']})
    fvgs=fvg_events(d)
    for e in fvgs[-10:]:
        if e['bar']>=len(d)-320:
            markers.append({'time':int(pd.Timestamp(d.time.iat[e['bar']]).timestamp()),'position':'belowBar' if 'Bullish' in e['type'] else 'aboveBar',
                 'color':'#88aaf0','shape':'circle','text':'FVG'})
    markers.sort(key=lambda x:x['time'])
    p['markers']=markers
    p['candles']=[dict(c,volume=None) if False else c for c in p['candles']]
    return p


def supertrend_approx(df:pd.DataFrame,period:int=10,multiplier:float=3.0) -> pd.Series:
    """Standard ATR supertrend visual approximation; NOT the uploaded custom channel."""
    from .indicators import atr
    atr10=atr(df,period)
    hl2=(df['high']+df['low'])/2
    ub=(hl2+multiplier*atr10).to_numpy()
    lb=(hl2-multiplier*atr10).to_numpy()
    cl=df['close'].to_numpy()
    result=np.full(len(df),np.nan)
    upper=lower=float('nan');direction=1
    for i in range(period-1,len(df)):
        if not np.isfinite(ub[i]):continue
        if i==period-1:upper=ub[i];lower=lb[i];direction=1
        else:
            prevclose=cl[i-1]
            upper=ub[i] if ub[i]<upper or prevclose>upper else upper
            lower=lb[i] if lb[i]>lower or prevclose<lower else lower
            if direction==1 and cl[i]<lower:direction=-1
            elif direction==-1 and cl[i]>upper:direction=1
        result[i]=lower if direction==1 else upper
    return pd.Series(result,index=df.index)
