from datetime import datetime, timezone
from app.shoonya import parse_bars,monthly_bars,blend_live,parse_feed_time,ShoonyaMarket,clean_symbol

def test_bar_parsing_and_reordering():
    bars=parse_bars([{'time':'06-DEC-2022','into':'10','inth':'12','intl':'9','intc':'11','v':'300'},
                    {'time':'05-DEC-2022','into':'9','inth':'11','intl':'8','intc':'10','v':'200'}])
    assert len(bars)==2 and bars[0]['close']==10 and bars[-1]['close']==11
    assert monthly_bars(bars)[0]['high']==12

def test_websocket_incremental_merges_without_erasing_fields():
    client=ShoonyaMarket()
    client.token_to_symbol={'22':'RELIANCE'}
    client._handle_message({'t':'tk','tk':'22','lp':'103','ft':'1670231374','v':'42'})
    client._handle_message({'t':'tf','tk':'22','lp':'104','ft':'1670231375'})
    assert client.ticks['RELIANCE']['v']=='42'
    assert client.ticks['RELIANCE']['lp']=='104'

def test_unauthenticated_not_live():
    s=ShoonyaMarket()
    assert s.latest('RELIANCE')['fresh'] is False
    assert parse_feed_time('abc') is None

def test_live_blend_updates_partial_candle():
    bars=[{'time':'2026-11-12T10:00:00+05:30','open':100,'high':103,'low':99,'close':102,'volume':40}]
    tick={'fresh':True,'feed_time':'2026-11-12T04:32:00+00:00','price':106}
    res=blend_live(bars,tick,'15m')
    assert len(res)==1 and res[0]['close']==106 and res[0]['high']==106 and res[0]['provisional']

def test_symbol_validation():
    assert clean_symbol('nse:RELIANCE-EQ')=='RELIANCE'

def test_intraday_volume_uses_interval_not_accumulated():
    rows=[{'time':'06-11-2026 09:15:00','into':'10','inth':'12','intl':'9','intc':'11','intv':'36','v':'300'}]
    assert parse_bars(rows,intraday=True)[0]['volume']==36

def test_book_update_does_not_refresh_price_timestamp():
    s=ShoonyaMarket();s.token_to_symbol={'22':'RELIANCE'}
    s._handle_message({'t':'tk','e':'NSE','tk':'22','ft':'1670231374','lp':'103'})
    s._handle_message({'t':'tf','e':'NSE','tk':'22','ft':'1670231375','bp1':'102.95'})
    assert s.ticks['RELIANCE']['price_ft']=='1670231374'

def test_native_live_candle_retains_high_and_low():
    s=ShoonyaMarket();s.token_to_symbol={'22':'RELIANCE'}
    dt=datetime.fromisoformat('2026-11-12T10:02:00+05:30');ts=int(dt.timestamp())
    for i,p in enumerate([103,108,99,104]):
        s._handle_message({'t':'tf','e':'NSE','tk':'22','ft':str(ts+i),'lp':str(p)})
    row=s.current_bar('RELIANCE','15m')
    assert (row['open'],row['high'],row['low'],row['close'])==(103,108,99,104)
    history=[{'time':'2026-11-12T09:45:00+05:30','open':100,'high':103,'low':99,'close':103,'volume':100}]
    out=blend_live(history,{'fresh':True,'feed_time':datetime.fromtimestamp(ts+3,timezone.utc).isoformat(),'price':104},'15m',row)
    assert len(out)==2 and out[-1]['high']==108 and out[-1]['low']==99

def test_oauth_code_from_redirect_and_checksum(monkeypatch):
    import json,hashlib
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'stat':'Ok','susertoken':'test-token','uid':'AB123','actid':'AB123'}
    class Session:
        def post(self,url,**kwargs):
            assert url.endswith('/GenAcsTok')
            body=json.loads(kwargs['data'].split('=',1)[1]);assert body['code']=='abc123'
            assert body['checksum']==hashlib.sha256(b'clientsecretabc123').hexdigest()
            return Response()
    s=ShoonyaMarket(Session());s.client_id='client';s.secret='secret';s.token=''
    assert s.exchange_code('http://127.0.0.1/callback?code=abc123')['authenticated']
    assert s.token=='test-token'

def test_ws_handshake_subscribes_after_ack():
    import json
    class Sock:
        def __init__(self):self.sent=[]
        def send(self,msg):self.sent.append(json.loads(msg))
    s=ShoonyaMarket();s.ws=Sock();s.wanted=['RELIANCE','TCS'];s.symbol_to_token={'RELIANCE':'2885','TCS':'11536'}
    s._handle_message({'t':'ak','s':'Ok'})
    assert s.connected and s.ws.sent==[{'t':'t','k':'NSE|2885#NSE|11536'}]

def test_late_packet_cannot_overwrite_latest():
    s=ShoonyaMarket();s.token_to_symbol={'22':'RELIANCE'}
    s._handle_message({'t':'tf','tk':'22','ft':'1670231375','lp':'104'})
    s._handle_message({'t':'tf','tk':'22','ft':'1670231374','lp':'90'})
    assert s.ticks['RELIANCE']['lp']=='104'

def test_read_only_http_resolve_and_history(monkeypatch):
    import json
    import app.shoonya as mod
    monkeypatch.setattr(mod.time,'sleep',lambda _:None)
    class Response:
        status_code=200;ok=True
        def __init__(self,payload):self.payload=payload
        def json(self):return self.payload
    class Session:
        def post(self,url,**kwargs):
            assert kwargs['headers']['Authorization']=='Bearer test-session'
            if url.endswith('SearchScrip'):
                assert json.loads(kwargs['data']['jData'])['stext']=='RELIANCE'
                return Response({'stat':'Ok','values':[{'exch':'NSE','tsym':'RELIANCE-EQ','token':'2885'}]})
            assert url.endswith('TPSeries') and kwargs['json']['token']=='2885' and kwargs['json']['intrv']=='15'
            return Response([{'time':'12-11-2026 09:15:00','into':'100','inth':'103','intl':'98','intc':'102','intv':'50','v':'500'}])
    s=ShoonyaMarket(Session());s.token='test-session';s.user_id='TEST'
    assert s.load_history('RELIANCE','15m')[0]['volume']==50
    assert s.load_history('RELIANCE','15m')[0]['close']==102
