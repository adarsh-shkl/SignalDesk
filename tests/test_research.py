import sys
from types import SimpleNamespace
from datetime import datetime
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from app import research, engine
from app.server import app
from app.shoonya import MarketDataError


def test_normalize_sort_deduplicate_and_monthly():
    rows=pd.DataFrame({'time':['2026-02-03','2026-01-30','2026-02-02','2026-02-03'], 'open':[15,10,12,15],'high':[18,12,17,20],'low':[13,9,11,13],'close':[16,11,16,19],'volume':[100,20,30,50]})
    m=research.monthly_from_daily(research.normalize(rows))
    assert len(m)==2 and m.iloc[1].open==12 and m.iloc[1].close==19
    assert m.iloc[1].high==20 and m.iloc[1].low==11 and m.iloc[1].volume==80
    assert str(m.time.dt.tz)=='Asia/Kolkata'


def test_nse_history_pagination(monkeypatch):
    now=pd.Timestamp.now(tz=research.IST).normalize().tz_localize(None)
    dates=[(now-pd.Timedelta(days=d)).strftime('%Y-%m-%d') for d in (2,3,100)]
    called=[]
    def history(self,symbol,end):
        called.append(end)
        dat=[dates[0]] if len(called)==1 else [dates[2]]
        return {'symbol':symbol,'data':[dict(date=d,open=10,high=12,low=9,close=11,volume=100) for d in dat], 'next_end_date':dates[1] if len(called)==1 else None}
    monkeypatch.setattr(research.NSESession,'__enter__',lambda s:s)
    monkeypatch.setattr(research.NSESession,'history',history)
    frame=research.nse_history('RELIANCE','1d')
    assert len(frame)==2 and called==['today',dates[1]]
    assert frame.time.is_monotonic_increasing


def test_mcp_tool_parse(monkeypatch):
    page={'symbol':'INFY','data':[{'date':'2026-10-30'}]}
    import json
    def rpc(self,method,params):
        assert method=='tools/call' and params['name']=='get_stock_history'
        assert params['arguments']=={'symbol':'INFY','months':3,'endDate':'today'}
        return {'content':[{'type':'text','text':json.dumps(page)}]}
    monkeypatch.setattr(research.NSESession,'rpc',rpc)
    assert research.NSESession().history('INFY','today')==page


def test_yahoo_interval_and_symbol(monkeypatch):
    def ticker(symbol):
        assert symbol=='TCS.NS'
        def history(**kwargs):
            assert kwargs['interval']=='1h' and kwargs['auto_adjust'] is False
            return pd.DataFrame({'Open':[10],'High':[12],'Low':[9],'Close':[11],'Volume':[55]},index=pd.DatetimeIndex(['2026-10-30 09:15'],tz=research.IST,name='Datetime'))
        return SimpleNamespace(history=history)
    monkeypatch.setitem(sys.modules,'yfinance',SimpleNamespace(Ticker=ticker))
    frame=research.yahoo_history('TCS','1h')
    assert frame.iloc[0].close==11 and frame.iloc[0].time.hour==9


def test_demo_real_adapter_and_source_metadata(monkeypatch):
    calls=[]
    def fake(sym,tf):
        calls.append((sym,tf));return engine.demo_frame(sym,tf)
    monkeypatch.setattr(engine,'fetch_research',fake)
    cli=TestClient(app)
    config=cli.get('/api/config').json();config['watchlist']=['INFY'];config['rules'][0]['timeframe']='1d'
    chart=cli.get('/api/chart?symbol=INFY&timeframe=1d&mode=demo').json()
    assert chart['source'].startswith('NSE Bhavcopy') and chart['is_live'] is False
    res=cli.post('/api/scan',json={'mode':'demo','config':config}).json()['results'][0]
    assert res['sources']['15m'].startswith('Yahoo Finance') and '1d' in res['bar_times']
    assert ('INFY','1d') in calls and ('INFY','15m') in calls


def test_provider_unavailable_never_synthetic(monkeypatch):
    def fail(*args):raise MarketDataError('Provider unavailable')
    monkeypatch.setattr(engine,'fetch_research',fail)
    cli=TestClient(app)
    assert cli.get('/api/chart?symbol=TCS&mode=demo').status_code==503
    config=cli.get('/api/config').json();config['watchlist']=['TCS']
    res=cli.post('/api/scan',json={'mode':'demo','config':config}).json()
    assert res['matched']==0 and res['results'][0]['status']=='UNAVAILABLE'
    assert cli.get('/api/chart?symbol=TCS&mode=offline').status_code==200


def test_mcp_transport_initialization_and_sse(monkeypatch):
    import json
    calls=[]
    class Resp:
        def __init__(self,body,headers):self.body=body;self.headers=headers
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):pass
        def json(self):return self.body
        def iter_lines(self,**kwargs):
            yield 'event: message';yield 'data: '+json.dumps(self.body);yield ''
    class Http:
        def post(self,url,json,headers,**kwargs):
            assert url==research.NSE_URL
            calls.append((json.copy(),headers.copy()))
            if json['method']=='initialize':
                return Resp({'jsonrpc':'2.0','id':json['id'],'result':{'protocolVersion':'2025-03-26'}},{'Mcp-Session-Id':'test-id','Content-Type':'application/json'})
            assert headers['Mcp-Session-Id']=='test-id'
            assert headers['MCP-Protocol-Version']=='2025-03-26'
            if json['method']=='notifications/initialized':return Resp({}, {})
            return Resp({'jsonrpc':'2.0','id':json['id'],'result':{'structuredContent':{'data':[]}}},{'Content-Type':'text/event-stream'})
        def close(self):pass
    monkeypatch.setattr(research.requests,'Session',Http)
    with research.NSESession() as api:
        assert api.history('TCS','today')=={'data':[]}
    assert [c[0]['method'] for c in calls]==['initialize','notifications/initialized','tools/call']


def test_cache_reuses_research_data_without_live_feed(monkeypatch):
    count=[]
    monkeypatch.setattr(research,'_CACHE',{})
    def fetch(symbol,tf):
        count.append((symbol,tf));return engine.demo_frame(symbol,tf)
    monkeypatch.setattr(research,'nse_history',fetch)
    x=research.fetch_research('TCS','1d');y=research.fetch_research('TCS','1d')
    assert count==[('TCS','1d')] and x.equals(y) and x is not y
