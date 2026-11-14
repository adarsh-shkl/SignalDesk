from fastapi.testclient import TestClient
from app.server import app

client=TestClient(app)

def test_metadata_and_demo_scan():
    meta=client.get('/api/meta')
    assert meta.status_code==200
    assert meta.json()['provider'].startswith('Shoonya')
    c=client.get('/api/config').json();c['watchlist']=['RELIANCE','TCS']
    scan=client.post('/api/scan',json={'mode':'offline','config':c})
    assert scan.status_code==200,scan.text
    x=scan.json()
    assert x['mode']=='OFFLINE' and x['checked']==2
    assert all(r['status'].startswith('OFFLINE_') for r in x['results'])

def test_demo_chart():
    d=client.get('/api/chart?symbol=INFY&timeframe=15m&mode=offline')
    assert d.status_code==200,d.text
    assert d.json()['mode']=='OFFLINE / SYNTHETIC' and len(d.json()['candles'])>200

def test_invalid_config_rejected():
    c=client.get('/api/config').json();c['watchlist']=['../evil']
    assert client.put('/api/config',json=c).status_code==422

def test_no_live_scan_without_tick():
    c=client.get('/api/config').json();c['watchlist']=['RELIANCE']
    r=client.post('/api/scan',json={'mode':'live','config':c})
    assert r.status_code==200
    assert r.json()['results'][0]['status']=='NOT_LIVE'
