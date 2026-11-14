"""Credential-free, local educational market history. Never a live trading feed."""
from __future__ import annotations
import json
import time
from datetime import datetime, timedelta
from threading import Lock
from zoneinfo import ZoneInfo
import pandas as pd
import requests
from .shoonya import MarketDataError, clean_symbol

IST = ZoneInfo('Asia/Kolkata')
NSE_URL = 'https://mcp.nseindia.in/bhavcopy/cm/mcp'
FIELDS = ['time','open','high','low','close','volume']
_CACHE: dict = {}
_LOCK = Lock()


def research_source(tf: str) -> str:
    return 'Yahoo Finance (NSE, historical intraday)' if tf in ('15m','1h') else 'NSE Bhavcopy MCP (end-of-day)' if tf == '1d' else 'NSE Bhavcopy MCP (monthly aggregation)'


class NSESession:
    """Minimal streamable HTTP MCP client; no login, no API keys or scraping."""
    def __init__(self):
        self.http = requests.Session()
        self.headers = {'Accept':'application/json, text/event-stream','Content-Type':'application/json'}
        self.seq = 0

    def rpc(self, method, params=None, notification=False):
        payload = {'jsonrpc':'2.0','method':method,'params':params or {}}
        if not notification:
            self.seq += 1
            payload['id'] = self.seq
        with self.http.post(NSE_URL, json=payload, headers=self.headers, timeout=(8,20), stream=True) as response:
            response.raise_for_status()
            if response.headers.get('Mcp-Session-Id'):
                self.headers['Mcp-Session-Id'] = response.headers['Mcp-Session-Id']
            if notification:
                return None
            if 'text/event-stream' in response.headers.get('Content-Type','').lower():
                messages = []
                for line in response.iter_lines(decode_unicode=True):
                    if isinstance(line,bytes): line=line.decode('utf-8')
                    if line.startswith('data:'): messages.append(line[5:].lstrip())
                    elif not line and messages:
                        item=json.loads('\n'.join(messages)); messages=[]
                        if item.get('id')==self.seq: break
                else:
                    raise MarketDataError('NSE MCP did not return a complete response.')
            else:
                item=response.json()
            if item.get('id')!=self.seq or 'error' in item or 'result' not in item:
                raise MarketDataError('NSE MCP returned an unexpected or error response.')
            return item['result']

    def __enter__(self):
        try:
            result=self.rpc('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'signaldesk-research','version':'0.5.0'}})
            self.headers['MCP-Protocol-Version']=result['protocolVersion']
            self.rpc('notifications/initialized',notification=True)
            return self
        except Exception:
            self.http.close(); raise

    def __exit__(self,*args): self.http.close()

    def history(self,symbol,end):
        result=self.rpc('tools/call',{'name':'get_stock_history','arguments':{'symbol':symbol,'months':3,'endDate':end}})
        if result.get('isError'): raise MarketDataError('NSE MCP history tool reported an error.')
        structured=result.get('structuredContent')
        if isinstance(structured,dict) and 'data' in structured: return structured
        for block in result.get('content',[]):
            if block.get('type')=='text':
                try: value=json.loads(block['text'])
                except (ValueError,KeyError): continue
                if isinstance(value,dict) and 'data' in value: return value
        raise MarketDataError('NSE MCP returned no OHLCV history for '+symbol+'.')


def normalize(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty or not set(FIELDS).issubset(rows.columns):
        raise MarketDataError('Provider returned no valid OHLCV candles.')
    rows=rows[FIELDS].copy()
    # Normalize exchange calendar labels and intraday timestamps to IST.
    def local_time(value):
        try:
            t=pd.Timestamp(value)
            if pd.isna(t): return pd.NaT
            return t.tz_localize(IST) if t.tzinfo is None else t.tz_convert(IST)
        except (ValueError,TypeError): return pd.NaT
    rows['time']=rows['time'].map(local_time)
    for col in FIELDS[1:]:
        rows[col]=pd.to_numeric(rows[col],errors='coerce')
    rows=rows.replace([float('inf'),float('-inf')],float('nan')).dropna()
    rows=rows[(rows['low']>0)&(rows['high']>=rows[['open','close','low']].max(axis=1))&(rows['low']<=rows[['open','close']].min(axis=1))&(rows['volume']>=0)]
    rows=rows.sort_values('time').drop_duplicates('time',keep='last').reset_index(drop=True)
    if rows.empty: raise MarketDataError('Provider returned no usable OHLCV candles.')
    return rows


def monthly_from_daily(rows: pd.DataFrame) -> pd.DataFrame:
    d=rows.copy()
    d['time']=d['time'].map(lambda t: pd.Timestamp(year=t.year,month=t.month,day=1,tz=IST))
    return d.groupby('time',as_index=False).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'))


def nse_history(symbol,tf):
    now=datetime.now(IST).date()
    cutoff=pd.Timestamp(now)-pd.DateOffset(years=5 if tf=='1mo' else 2)
    end='today'; seen=set(); records=[]
    with NSESession() as api:
        # 3 months per history request; cap at 21 for five years, 9 for two.
        for _ in range(21 if tf=='1mo' else 9):
            page=api.history(symbol,end)
            if page.get('symbol') and str(page['symbol']).upper()!=symbol:
                raise MarketDataError('NSE MCP returned a different stock symbol.')
            data=page.get('data')
            if not isinstance(data,list): raise MarketDataError('Invalid NSE history response.')
            if not data: break
            records.extend(data)
            next_end=page.get('next_end_date')
            if not next_end or next_end in seen: break
            try:
                next_date=pd.Timestamp(next_end)
            except (ValueError,TypeError) as exc: raise MarketDataError('Unexpected NSE history pagination date.') from exc
            if next_date < cutoff: break
            if end!='today' and next_date>=pd.Timestamp(end): raise MarketDataError('NSE history pagination did not advance.')
            seen.add(next_end);end=next_end
    rows=normalize(pd.DataFrame(records).rename(columns={'date':'time'}))
    rows=rows[rows.time>=cutoff.tz_localize(IST)].reset_index(drop=True)
    if rows.empty: raise MarketDataError('NSE returned no candles in the requested history window.')
    # A newly listed security may have fewer than five years of candles.
    return monthly_from_daily(rows) if tf=='1mo' else rows


def yahoo_history(symbol,tf):
    try: import yfinance as yf
    except ImportError as exc: raise MarketDataError('Install updated requirements.txt to enable Yahoo Finance research data.') from exc
    # Use 55 days rather than the 60-day intraday service boundary.
    now=datetime.now(IST)
    rows=yf.Ticker(symbol+'.NS').history(start=(now-timedelta(days=55)).date().isoformat(),
                interval=tf,auto_adjust=False,actions=False,prepost=False,timeout=12,raise_errors=True)
    if rows is None or rows.empty: raise MarketDataError('Yahoo Finance returned no intraday candles for '+symbol+'.')
    rows=rows.reset_index(); index_col=rows.columns[0]
    return normalize(rows.rename(columns={index_col:'time','Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume'}))


def fetch_research(symbol,tf):
    symbol=clean_symbol(symbol)
    if tf not in ('15m','1h','1d','1mo'): raise ValueError('Unsupported research timeframe.')
    key=(symbol,tf); ttl=300 if tf in ('15m','1h') else 900
    with _LOCK:
        saved=_CACHE.get(key)
        if saved and time.monotonic()-saved[0]<ttl: return saved[1].copy()
    try:
        frame=yahoo_history(symbol,tf) if tf in ('15m','1h') else nse_history(symbol,tf)
    except MarketDataError: raise
    except Exception as exc:
        # Do not silently replace genuine candles with synthetic data.
        raise MarketDataError(f'{research_source(tf)} unavailable for {symbol} ({type(exc).__name__}). Check internet access or try later.') from exc
    with _LOCK:
        if len(_CACHE)>=200: _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key]=(time.monotonic(),frame.copy())
    return frame
