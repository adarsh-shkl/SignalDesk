"""Single-user local research dashboard. Binds to loopback (start script).
No orders or trading account modifications are implemented.
"""
from __future__ import annotations
import json
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from .shoonya import client, clean_symbol, MarketDataError
from .engine import fetch_frame, build_chart, frame_summary
from .research import research_source
from .rules import DEFAULT, NUMERIC, BOOL, TIMES, validate_config, evaluate_rules

BASE=Path(__file__).resolve().parent.parent
CONFIG_PATH=BASE/'local_config.json'
LOCK=Lock()
app=FastAPI(title='NSE Trading Agent • Local Shoonya Research',version='0.5.0')


def _load()->dict:
    if CONFIG_PATH.exists():
        try:return validate_config(json.loads(CONFIG_PATH.read_text(encoding='utf-8')))
        except (ValueError,OSError):pass
    return validate_config(DEFAULT)


def _save(c:dict):
    with LOCK:
        temp=CONFIG_PATH.with_suffix('.tmp')
        temp.write_text(json.dumps(c,indent=2),encoding='utf-8')
        temp.replace(CONFIG_PATH)

@app.get('/')
def root():return FileResponse(BASE/'app'/'static'/'index.html')

@app.get('/api/meta')
def meta():
    return {'provider':'Shoonya (read-only)','model':'local research; not order placement',
         'source_parity':'Partial native port; original Pine runtime not embedded.',
         'numeric_fields':NUMERIC,'boolean_fields':BOOL,'timeframes':TIMES,
         'auth':{'configured_client_id':bool(client.client_id),'configured_secret':bool(client.secret),
                  'authenticated':bool(client.token and client.user_id)},
         'data_policy':'DEMO uses actual historical NSE/Yahoo candles for education only; OFFLINE uses synthetic fixtures; LIVE requires fresh authorized Shoonya ticks.'}

@app.get('/api/config')
def read_config():return _load()

@app.put('/api/config')
def save_config(payload:dict[str,Any]):
    try:conf=validate_config(payload)
    except ValueError as exc:raise HTTPException(422,detail=str(exc)) from exc
    _save(conf)
    return {'saved':True,'config':conf}

@app.get('/api/auth/url')
def auth_url():
    try:return {'url':client.login_url()}
    except MarketDataError as exc:raise HTTPException(400,detail=str(exc)) from exc

@app.post('/api/auth/exchange')
def exchange(payload:dict[str,Any]):
    try:return client.exchange_code(str(payload.get('code','')))
    except MarketDataError as exc:raise HTTPException(400,detail=str(exc)) from exc

@app.post('/api/live/connect')
def start_feed():
    conf=_load()
    try:return client.connect(conf['watchlist'])
    except MarketDataError as exc:raise HTTPException(503,detail=str(exc)) from exc

@app.post('/api/live/disconnect')
def stop_feed():return client.disconnect()

@app.get('/api/live/status')
def live_status():return client.status()

@app.get('/api/chart')
def chart(symbol:str='RELIANCE',timeframe:str='15m',mode:str='demo'):
    if timeframe not in TIMES:raise HTTPException(400,detail='Invalid timeframe')
    if mode not in ('demo','offline','live'):raise HTTPException(400,detail='Mode must be demo, offline or live')
    try:
        sym=clean_symbol(symbol)
        frame,live=fetch_frame(sym,timeframe,demo=(mode=='demo'),offline=(mode=='offline'))
        result=build_chart(frame,timeframe)
        return {'symbol':sym,'mode':'DEMO / HISTORICAL' if mode=='demo' else 'OFFLINE / SYNTHETIC' if mode=='offline' else 'LIVE' if live else 'HISTORICAL / NOT LIVE',
                'source':research_source(timeframe) if mode=='demo' else 'Synthetic fixture' if mode=='offline' else 'Shoonya',
                'is_live':live, 'last_feed':client.latest(sym) if mode=='live' else None,
                'note':'Educational only; latest bar may be incomplete; corporate-action adjustments can differ.' if mode=='demo' else 'Synthetic fixture, not market prices.' if mode=='offline' else
                       'Latest candle may be provisional; candle calculation parity still needs validation.',
                **result}
    except (ValueError,MarketDataError) as exc:
        raise HTTPException(503,detail=str(exc)) from exc

@app.post('/api/scan')
def scan(payload:dict[str,Any]):
    mode=payload.get('mode','demo')
    if mode not in ('demo','offline','live'):raise HTTPException(400,detail='Mode must be demo, offline or live')
    try:conf=validate_config(payload.get('config') or _load())
    except ValueError as exc:raise HTTPException(422,detail=str(exc)) from exc
    rule_list=[r for r in conf['rules'] if r['enabled']]
    if not rule_list:raise HTTPException(422,detail='Enable at least one condition before scanning')
    required=sorted({r['timeframe'] for r in rule_list})
    results=[]
    for symbol in conf['watchlist']:
        tick=client.latest(symbol) if mode=='live' else None
        if mode=='live' and not tick['fresh']:
            results.append({'symbol':symbol,'status':'NOT_LIVE','checks':[], 'last_price':tick['price'],
                            'feed_time':tick['feed_time'],'error':'No fresh Shoonya trade tick. Live scan withheld.'})
            continue
        data={};errors=[];sources={};bar_times={}
        for tf in required:
            try:
                frame,updated=fetch_frame(symbol,tf,demo=mode=='demo',offline=mode=='offline')
                if mode=='live' and not updated:raise MarketDataError('Price feed became stale during scan.')
                data[tf]=frame_summary(frame,tf)
                sources[tf]=research_source(tf) if mode=='demo' else 'Synthetic fixture' if mode=='offline' else 'Shoonya'
                bar_times[tf]=data[tf]['last_bar_time']
            except (ValueError,MarketDataError) as exc:errors.append(f'{tf}: {exc}')
        evaluated=evaluate_rules(rule_list,conf['combine'],data)
        status= (mode.upper()+'_'+('MATCH' if evaluated['passed'] is True else 'NO_MATCH' if evaluated['passed'] is False else 'INCOMPLETE')) if mode in ('demo','offline') else evaluated['status']
        if errors and mode!='live':status=mode.upper()+'_INCOMPLETE'
        if errors and not data:status='UNAVAILABLE'
        if mode=='live':
            tick=client.latest(symbol)
            if not tick['fresh']:status='NOT_LIVE'
            elif errors:status='INCOMPLETE'
        results.append({'symbol':symbol,'status':status,'checks':evaluated['checks'],
              'last_price':tick['price'] if tick else data.get(required[0],{}).get('close'),
              'feed_time':tick['feed_time'] if tick else None,'sources':sources,'bar_times':bar_times,'price_timeframe':required[0] if required[0] in data else None,'error':' | '.join(errors) or None})
    return {'mode':mode.upper(),'condition_count':len(rule_list),'combine':conf['combine'],
         'checked':len(results),'matched':sum(r['status'] in ('MATCH','DEMO_MATCH','OFFLINE_MATCH') for r in results),
         'results':results,'disclaimer':'Research condition matches only. No automated buy/sell recommendations or orders.'}
