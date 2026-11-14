"""Read-only Shoonya market-data adapter. Never imports or calls order endpoints.

API request forms are based on Shoonya's public OAuth / WebSocket documentation.
A live account is needed to validate transport-specific behavior; no credentials
are included in the project or its tests.
"""
from __future__ import annotations

import hashlib
import math
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse, parse_qs
from zoneinfo import ZoneInfo
from typing import Any

import requests
import websocket
from dotenv import load_dotenv

load_dotenv()
IST = ZoneInfo('Asia/Kolkata')
REST = 'https://api.shoonya.com/NorenWClientAPI/'
WS = 'wss://api.shoonya.com/NorenWSAPI/'

class MarketDataError(Exception):
    pass


def clean_symbol(value: str) -> str:
    s = value.upper().strip().replace('NSE:', '')
    if s.endswith('-EQ'): s = s[:-3]
    if not re.fullmatch(r'[A-Z0-9_&.-]{1,32}', s):
        raise ValueError('Invalid NSE equity symbol: ' + str(value)[:40])
    return s


def live_hours_ist(now: datetime | None = None) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    # This is only an approximate weekday window; exchange holidays must be
    # inferred from ACTUAL live trades, not the calendar alone.
    return now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) < (15, 30)


def parse_feed_time(raw: Any) -> datetime | None:
    if raw in (None, ''): return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


class ShoonyaMarket:
    def __init__(self, session: requests.Session | None = None):
        self.client_id = os.getenv('SHOONYA_CLIENT_ID', '').strip()
        self.secret = os.getenv('SHOONYA_SECRET_CODE', '').strip()
        self.user_id = os.getenv('SHOONYA_USER_ID', '').strip()
        self.token = os.getenv('SHOONYA_ACCESS_TOKEN', '').strip()
        self.account_id = os.getenv('SHOONYA_ACCOUNT_ID', '').strip()
        self.oauth_url = os.getenv('SHOONYA_OAUTH_URL', 'https://api.shoonya.com/OAuthlogin/authorize/oauth')
        self.session = session or requests.Session()
        self.lock = threading.RLock()
        self.ws: websocket.WebSocketApp | None = None
        self.ws_thread: threading.Thread | None = None
        self.connected = False
        self.error = ''
        self.wanted: list[str] = []
        self.symbol_to_token: dict[str, str] = {}
        self.token_to_symbol: dict[str, str] = {}
        self.ticks: dict[str, dict] = {}
        self.live_bars: dict[tuple[str,str], dict] = {}
        self.rest_lock=threading.Lock()
        self.hist_cache: dict[tuple, tuple[float, list[dict]]] = {}
        self.last_rest = 0.0
        self.connecting = False
        self.stop_flag = False

    def login_url(self) -> str:
        if not self.client_id:
            raise MarketDataError('Add SHOONYA_CLIENT_ID to .env, restart, and try again.')
        return self.oauth_url + ('&' if '?' in self.oauth_url else '?') + urlencode({'client_id': self.client_id})

    def exchange_code(self, code: str) -> dict:
        if not self.client_id or not self.secret:
            raise MarketDataError('Add SHOONYA_CLIENT_ID and SHOONYA_SECRET_CODE to .env and restart.')
        code = code.strip()
        if code.startswith(("https://", "http://")):
            code = parse_qs(urlparse(code).query).get("code", [""])[0]
        if not code or len(code) > 1200: raise MarketDataError('Enter the short-lived authorization code from Shoonya.')
        checksum = hashlib.sha256((self.client_id+self.secret+code).encode()).hexdigest()
        try:
            response = self.session.post(REST+'GenAcsTok', data='jData='+json.dumps({'code':code,'checksum':checksum},separators=(',',':')),
                                         headers={'Content-Type':'text/plain'}, timeout=15)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise MarketDataError('Shoonya OAuth exchange failed. Check IP whitelist, code expiry, and callback configuration.') from exc
        if not isinstance(data,dict) or data.get('stat') != 'Ok' or not data.get('susertoken'):
            raise MarketDataError('Shoonya login rejected. Check authorization code, credentials and IP permissions.')
        with self.lock:
            self.token = str(data['susertoken'])
            self.user_id = str(data.get('uid') or self.user_id or self.client_id)
            self.account_id = str(data.get('actid') or self.user_id)
            self.connected = False
            self.error = ''
            self.hist_cache.clear()
            self.ticks.clear(); self.live_bars.clear()
            sock = self.ws
        if sock: sock.close()  # Avoid holding the callback lock while closing
        return {'authenticated':True,'user_id':self.user_id,'note':'Token stored in process memory only. Shoonya token expires after about 24 hours.'}

    def post(self, endpoint: str, data: dict) -> Any:
        if not self.token: raise MarketDataError('Shoonya not authenticated. Sign in on the Connection tab.')
        if endpoint not in ('SearchScrip','TPSeries','EODChartData','GetQuotes'):
            raise MarketDataError('Unsupported read-only endpoint')
        body=json.dumps(data,separators=(',',':'))
        header={'Authorization':'Bearer '+self.token}
        raw={'data':'jData='+body,'headers':{**header,'Content-Type':'text/plain'}}
        form={'data':{'jData':body,'jKey':self.token},'headers':header}
        js={'json':data,'headers':header}
        # Endpoint examples and the common API guide use different encodings.
        # Only retry alternate shapes for read-only, explicitly format-related failures.
        variants=[form,raw,js] if endpoint in ('SearchScrip','GetQuotes') else [js,raw,form]
        with self.rest_lock:
            for i,kwargs in enumerate(variants):
                wait=1.0-(time.monotonic()-self.last_rest)
                if wait>0:time.sleep(wait)
                self.last_rest=time.monotonic()
                try:
                    res=self.session.post(REST+endpoint,timeout=20,**kwargs)
                    payload=res.json()
                except (requests.RequestException,ValueError) as exc:
                    raise MarketDataError('Shoonya '+endpoint+' network or invalid response.') from exc
                msg=str(payload.get('emsg','')) if isinstance(payload,dict) else ''
                if res.status_code in (401,403) or 'session' in msg.lower() and 'expir' in msg.lower():
                    raise MarketDataError('Shoonya session or IP authorization rejected. Sign in again and verify IP permissions.')
                if res.status_code==429 or 'rate_limited' in msg.lower():
                    self.last_rest=time.monotonic()+2
                    raise MarketDataError('Shoonya rate limit reached; wait before retrying.')
                if res.status_code in (400,415,422) and i+1<len(variants):continue
                if not res.ok or isinstance(payload,dict) and str(payload.get('stat','')).lower()=='not_ok':
                    # Do not echo possibly sensitive broker response data.
                    raise MarketDataError(f'Shoonya {endpoint} rejected request (HTTP {res.status_code}). Check symbol, session and permissions.')
                return payload
        raise MarketDataError('Shoonya '+endpoint+' request format rejected.')

    def resolve(self, symbol: str) -> str:
        symbol = clean_symbol(symbol)
        with self.lock:
            if symbol in self.symbol_to_token: return self.symbol_to_token[symbol]
        data = self.post('SearchScrip',{'uid':self.user_id,'exch':'NSE','stext':symbol})
        values = data.get('values',[]) if isinstance(data,dict) else []
        wanted = {symbol, symbol+'-EQ'}
        matches = [r for r in values if str(r.get('tsym','')).upper() in wanted and str(r.get('exch','NSE')).upper()=='NSE']
        if not matches: raise MarketDataError('NSE equity '+symbol+' not found using Shoonya SearchScrip')
        token = str(matches[0].get('token',''))
        if not token.isdigit(): raise MarketDataError('No numeric Shoonya token returned for '+symbol)
        with self.lock:
            self.symbol_to_token[symbol]=token
            self.token_to_symbol[token]=symbol
        return token

    def load_history(self, symbol: str, interval: str) -> list[dict]:
        symbol = clean_symbol(symbol)
        key=(symbol,interval)
        ttl = 60 if interval in ('15m','1h') else 900
        with self.lock:
            cached=self.hist_cache.get(key)
            if cached and time.monotonic()-cached[0] < ttl: return [dict(x) for x in cached[1]]
        token=self.resolve(symbol)
        now=int(time.time())
        if interval in ('15m','1h'):
            days=45 if interval=='15m' else 120
            raw=self.post('TPSeries', {'uid':self.user_id,'exch':'NSE','token':token,'st':str(now-days*86400),'et':str(now),'intrv':'15' if interval=='15m' else '60'})
        elif interval in ('1d','1mo'):
            years=6 if interval=='1mo' else 3
            raw=self.post('EODChartData', {'uid':self.user_id,'sym':'NSE:'+symbol+'-EQ','from':str(now-years*366*86400),'to':str(now)})
        else: raise ValueError('Unsupported timeframe')
        if isinstance(raw,dict) and isinstance(raw.get('data'),list): raw=raw['data']
        if not isinstance(raw,list): raise MarketDataError('Shoonya '+interval+' historical endpoint returned an unexpected format; inspect broker account permission.')
        candles=parse_bars(raw, intraday=interval in ('15m','1h'))
        if interval=='1mo': candles=monthly_bars(candles)
        if not candles: raise MarketDataError('Shoonya returned no '+interval+' candles for '+symbol)
        with self.lock: self.hist_cache[key] = (time.monotonic(),candles)
        return [dict(x) for x in candles]

    def _handle_message(self, data: dict) -> None:
        event = data.get('t')
        if event == 'ak':
            if data.get('s') != 'Ok':
                with self.lock:
                    self.error='Shoonya WebSocket login rejected; sign in again or check account/IP whitelist.'
                    self.connected=False;self.stop_flag=True
                    sock=self.ws
                if sock:sock.close()
                return
            with self.lock:
                self.connected=True; self.error=''
                tokens=[self.symbol_to_token[s] for s in self.wanted if s in self.symbol_to_token]
                sock=self.ws
            if sock and tokens:
                sock.send(json.dumps({'t':'t','k':'#'.join('NSE|'+t for t in tokens)}))
            return
        if event not in ('tk','tf'): return
        token = str(data.get('tk',''))
        with self.lock:
            sym=self.token_to_symbol.get(token)
            if not sym:return
            if data.get('e','NSE')!='NSE':return
            state=self.ticks.setdefault(sym,{'symbol':sym,'token':token})
            previous_time=parse_feed_time(state.get('ft'))
            feed=parse_feed_time(data.get('ft'))
            if feed and previous_time and feed<previous_time:return
            # Snapshot/book-only packets must not refresh the age of the last price.
            old_volume=safe_number(state.get('v'))
            for key in ('lp','ft','v','o','h','l','c','pc','ts'):
                if key in data:state[key]=data[key]
            if feed and 'lp' in data and safe_number(data['lp']) is not None:
                state['price_ft']=data['ft'];state['timed_price']=data['lp']
                state['received_at']=datetime.now(timezone.utc).timestamp()
                price=safe_number(data['lp'])
                if price<=0 or not live_hours_ist(feed):return
                day=feed.astimezone(IST).date()
                volume=safe_number(state.get('v'))
                delta=max(0,volume-old_volume) if volume is not None and old_volume is not None and previous_time and previous_time.astimezone(IST).date()==day else 0
                for tf in ('15m','1h','1d','1mo'):
                    start=bucket_start(feed,tf).isoformat();key=(sym,tf)
                    row=self.live_bars.get(key)
                    if not row or row['time']!=start:
                        row={'time':start,'open':price,'high':price,'low':price,'close':price,'volume':0,'provisional':True,'last_date':str(day)}
                        self.live_bars[key]=row
                    row['high']=max(row['high'],price);row['low']=min(row['low'],price);row['close']=price;row['volume']+=delta
                    if tf=='1d':
                        for name,wire in [('open','o'),('high','h'),('low','l'),('volume','v')]:
                            val=safe_number(state.get(wire))
                            if val is not None and (val>0 or name=='volume'):row[name]=val
                        row['high']=max(row['high'],row['open'],price);row['low']=min(row['low'],row['open'],price)
                    if tf=='1mo':
                        hi=safe_number(state.get('h'));lo=safe_number(state.get('l'))
                        if hi is not None and hi>0:row['high']=max(row['high'],hi)
                        if lo is not None and lo>0:row['low']=min(row['low'],lo)

    def current_bar(self,symbol,interval):
        with self.lock:return dict(self.live_bars.get((clean_symbol(symbol),interval),{}))

    def disconnect(self):
        with self.lock:
            self.stop_flag=True;self.connected=False;self.wanted=[];self.ticks.clear();self.live_bars.clear();sock=self.ws
        if sock:sock.close()
        return {'connection':'DISCONNECTED'}

    def _run(self) -> None:
        backoff=1
        while not self.stop_flag and self.token:
            def opened(sock):
                sock.send(json.dumps({'t':'a','uid':self.user_id,'actid':self.account_id or self.user_id,
                    'source':'API','accesstoken':self.token}))
            def message(sock, payload):
                try:self._handle_message(json.loads(payload))
                except (ValueError, TypeError):pass
            def error(sock, exc):
                with self.lock:self.error='Shoonya WebSocket error: '+type(exc).__name__
            def closed(sock, status, reason):
                with self.lock:self.connected=False
            ws=websocket.WebSocketApp(WS,on_open=opened,on_message=message,on_error=error,on_close=closed)
            with self.lock:self.ws=ws
            try:ws.run_forever(ping_interval=25,ping_timeout=10)
            except Exception:
                with self.lock:self.connected=False;self.error='Shoonya socket disconnected'
            with self.lock:self.connected=False
            if self.stop_flag: break
            for _ in range(backoff*10):
                if self.stop_flag:break
                time.sleep(.1)
            backoff=min(backoff*2,30)

    def connect(self, symbols:list[str]) -> dict:
        if not self.token or not self.user_id:
            raise MarketDataError('Sign in to Shoonya first; Client ID and API key alone are not an access token.')
        if len(symbols)>500:raise MarketDataError('Shoonya limit is 500 WebSocket symbols per session.')
        symbols=list(dict.fromkeys(clean_symbol(s) for s in symbols))
        mapped={s:self.resolve(s) for s in symbols}
        with self.lock:
            old=set(self.wanted); self.wanted=list(symbols)
            self.stop_flag=False
            if self.connected and self.ws:
                remove=old-set(symbols);add=set(symbols)-old
                if remove:self.ws.send(json.dumps({'t':'u','k':'#'.join('NSE|'+mapped.get(s,self.symbol_to_token.get(s,'')) for s in remove)}))
                if add:self.ws.send(json.dumps({'t':'t','k':'#'.join('NSE|'+mapped[s] for s in add)}))
                return {'connection':'CONNECTED','watchlist':list(symbols)}
            if not self.ws_thread or not self.ws_thread.is_alive():
                self.stop_flag=False
                self.ws_thread=threading.Thread(target=self._run,daemon=True,name='shoonya-feed')
                self.ws_thread.start()
        return {'connection':'CONNECTING','watchlist':list(symbols)}

    def latest(self,symbol:str) -> dict:
        with self.lock:
            tick=dict(self.ticks.get(clean_symbol(symbol),{}))
            connected=self.connected
        feed=parse_feed_time(tick.get('price_ft'))
        age=(datetime.now(timezone.utc)-feed).total_seconds() if feed else None
        fresh=bool(connected and live_hours_ist() and age is not None and -10 <= age < 120 and safe_number(tick.get('timed_price')) is not None and safe_number(tick.get('timed_price'))>0)
        return {'symbol':symbol,'fresh':fresh,'feed_time':feed.isoformat() if feed else None,
                'price':safe_number(tick.get('timed_price')),'age_seconds':round(age) if age is not None else None,
                'received_at':tick.get('received_at')}

    def status(self) -> dict:
        with self.lock:
            return {'connection':'CONNECTED' if self.connected else 'CONNECTING' if self.ws_thread and self.ws_thread.is_alive() and not self.stop_flag else 'DISCONNECTED',
                    'authenticated':bool(self.token and self.user_id), 'error':self.error,
                    'market_hours_ist':live_hours_ist(),'symbols':list(self.wanted),
                    'ticks':{s:self.latest(s) for s in self.wanted}}


def safe_number(value):
    try:
        number=float(value)
        return number if math.isfinite(number) else None
    except (TypeError,ValueError):return None


def parse_datetime(s: Any):
    if s is None:return None
    if isinstance(s,(int,float)):
        return datetime.fromtimestamp(int(s),tz=timezone.utc).astimezone(IST)
    value=str(s).strip()
    if value.isdigit() and len(value)>=10:
        return datetime.fromtimestamp(int(value[:10]),tz=timezone.utc).astimezone(IST)
    for fmt in ('%d-%m-%Y %H:%M:%S','%d-%m-%Y %H:%M','%d-%b-%Y %H:%M:%S','%d-%b-%Y','%d-%m-%Y','%Y-%m-%d %H:%M:%S','%Y-%m-%d'):
        try:return datetime.strptime(value,fmt).replace(tzinfo=IST)
        except ValueError:pass
    return None


def parse_bars(rows:list[dict], intraday:bool=False) -> list[dict]:
    out={}
    for r in rows:
        if isinstance(r,str):
            try:r=json.loads(r)
            except ValueError:continue
        if not isinstance(r,dict):continue
        dt=parse_datetime(r.get('time') or r.get('ssboe') or r.get('ft'))
        if dt is None:continue
        c={k:safe_number(r.get(field)) for k,field in [('open','into'),('high','inth'),('low','intl'),('close','intc'),('volume','v')]}
        if c['close'] is None or any(c[k] is None for k in ('open','high','low')):continue
        if not (c['low']<=c['open']<=c['high'] and c['low']<=c['close']<=c['high']):continue
        c['volume']=(safe_number(r.get('intv')) if intraday else c['volume']) or 0
        c['time']=dt.isoformat()
        out[c['time']]=c
    return sorted(out.values(),key=lambda x:x['time'])


def monthly_bars(daily:list[dict]) -> list[dict]:
    grouped={}
    for bar in daily:
        key=bar['time'][:7]
        if key not in grouped:grouped[key]=dict(bar)
        else:
            x=grouped[key];x['high']=max(x['high'],bar['high']);x['low']=min(x['low'],bar['low'])
            x['close']=bar['close'];x['volume']+=bar['volume']
        grouped[key]['last_date']=bar['time'][:10]
    return list(grouped.values())


def bucket_start(dt:datetime,interval:str)->datetime:
    dt=dt.astimezone(IST)
    if interval=='15m':return dt.replace(minute=(dt.minute//15)*15,second=0,microsecond=0)
    if interval=='1h':
        offset=(dt.hour*60+dt.minute)-(9*60+15)
        return dt.replace(hour=9,minute=15,second=0,microsecond=0)+timedelta(minutes=max(0,offset//60)*60)
    if interval=='1d':return dt.replace(hour=0,minute=0,second=0,microsecond=0)
    return dt.replace(day=1,hour=0,minute=0,second=0,microsecond=0)


def blend_live(candles:list[dict],tick:dict,interval:str,observed:dict|None=None) -> list[dict]:
    if not tick.get('fresh') or not candles:return candles
    dt=datetime.fromisoformat(tick['feed_time']);price=tick['price']
    if price is None:return candles
    start=bucket_start(dt,interval)
    observed=observed or {}
    if observed.get('time')!=start.isoformat():observed={}
    last_dt=datetime.fromisoformat(candles[-1]['time']).astimezone(IST)
    last_bucket=bucket_start(last_dt,interval)==start
    result=list(candles)
    high=max(price,observed.get('high',price));low=min(price,observed.get('low',price))
    if last_bucket:
        row=dict(result[-1]);row['close']=price;row['high']=max(row['high'],high);row['low']=min(row['low'],low);row['provisional']=True
        # REST and stream overlap; never add overlapping volumes.
        if interval=='1d' and observed:row['volume']=max(row['volume'],observed['volume'])
        result[-1]=row
    elif last_dt<start:
        result.append({'time':start.isoformat(),'open':observed.get('open',price),'high':high,'low':low,'close':price,'volume':observed.get('volume',0),'provisional':True})
    return result

client = ShoonyaMarket()
