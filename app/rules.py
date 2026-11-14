"""Strict, JSON-serializable AND/OR condition builder. No user Python execution."""
from __future__ import annotations
from math import isfinite
from typing import Any

TIMES = ('15m','1h','1d','1mo')
NUMERIC = ('close','open','high','low','sma7','sma20','ema50','ema200',
           'bb_upper','bb_middle','bb_lower','bb_width_pct','rsi14','atr50','vwap')
BOOL = ('bb_above','bb_below','monthly_squeeze_candidate','bullish_structure_recent',
        'bearish_structure_recent','inside_demand_zone','bullish_fvg_recent',
        'bearish_fvg_recent','squeeze_bandwidth_low')
OPS = ('>','>=','<','<=','==','!=')
DEFAULT = {
 'watchlist':['RELIANCE','TCS','INFY','HDFCBANK','SBIN'],
 'combine':'AND',
 'rules':[
   {'id':'r1','timeframe':'15m','left':'close','op':'>','right':'bb_upper','enabled':True},
   {'id':'r2','timeframe':'15m','left':'rsi14','op':'>','right':'40','enabled':True},
 ],
 'overlays':{'bb':True,'sma7':True,'sma20':True,'ema50':True,
             'ema200':False,'vwap':False,'supertrend':False,'rsi':True,'zones':True,'structure':True,'fvg':False},
}


def validate_config(config: Any) -> dict:
    if not isinstance(config,dict):raise ValueError('Configuration must be a JSON object')
    combine=config.get('combine','AND')
    if combine not in ('AND','OR'):raise ValueError('combine must be AND or OR')
    symbols=config.get('watchlist',[])
    if not isinstance(symbols,list) or not 1<=len(symbols)<=50:
        raise ValueError('Watchlist must contain 1–50 NSE stocks for the local pilot. 500 subscriptions are a future expansion.')
    from .shoonya import clean_symbol
    cleaned=list(dict.fromkeys(clean_symbol(str(s)) for s in symbols))
    rules=config.get('rules',[])
    if not isinstance(rules,list) or len(rules)>25:raise ValueError('Up to 25 conditions are supported')
    checked=[]; ids=set()
    for i,r in enumerate(rules):
        if not isinstance(r,dict):raise ValueError('Each condition must be an object')
        key=str(r.get('id',f'r{i}'))
        if not key.isalnum() or len(key)>32 or key in ids:raise ValueError('Invalid or duplicate condition ID')
        ids.add(key)
        tf=r.get('timeframe');field=r.get('left');op=r.get('op');right=str(r.get('right','')).strip()
        if tf not in TIMES or field not in (*NUMERIC,*BOOL):raise ValueError('Invalid timeframe or indicator in condition '+key)
        if field in BOOL:
            if op!='is' or right.lower() not in ('true','false'):
                raise ValueError('Boolean condition requires operator "is" and true/false')
        else:
            if op not in OPS:raise ValueError('Invalid comparator in '+key)
            if right not in NUMERIC:
                try:
                    if not isfinite(float(right)):raise ValueError
                except ValueError:raise ValueError('Condition right side must be number or indicator')
        checked.append({'id':key,'timeframe':tf,'left':field,'op':op,'right':right,'enabled':bool(r.get('enabled',True))})
    overlays=config.get('overlays',{})
    if not isinstance(overlays,dict):raise ValueError('Overlays must be a map')
    allowed=('bb','sma7','sma20','ema50','ema200','vwap','supertrend','rsi','zones','structure','fvg')
    return {'watchlist':cleaned,'combine':combine,'rules':checked,
            'overlays':{k:bool(overlays.get(k,DEFAULT['overlays'].get(k,False))) for k in allowed}}


def evaluate_rules(rule_list:list[dict], combine:str, frames:dict[str,dict]) -> dict:
    enabled=[r for r in rule_list if r['enabled']]
    checks=[]
    for r in enabled:
        frame=frames.get(r['timeframe'])
        left=None;right=None;passed=None
        if frame:
            indicators=frame.get('indicators',{})
            special=frame.get('conditions',{})
            if r['left'] in BOOL:
                left=special.get(r['left'])
                right=r['right']=='true'
                passed=left == right if isinstance(left,bool) else None
            else:
                left=frame.get(r['left']) if r['left'] in ('close','open','high','low') else indicators.get(r['left'])
                raw=r['right']
                right=frame.get(raw) if raw in ('close','open','high','low') else indicators.get(raw) if raw in NUMERIC else float(raw)
                if left is not None and right is not None:
                    operators={'>':lambda a,b:a>b,'>=':lambda a,b:a>=b,'<':lambda a,b:a<b,'<=':lambda a,b:a<=b,
                               '==':lambda a,b:a==b,'!=':lambda a,b:a!=b}
                    passed=bool(operators[r['op']](float(left),float(right)))
        checks.append({'id':r['id'],'timeframe':r['timeframe'],'expression':f"{r['left']} {r['op']} {r['right']}",
                       'passed':passed,'left_value':left,'right_value':right})
    if not enabled: return {'status':'NO_RULES','passed':False,'checks':[]}
    vals=[c['passed'] for c in checks]
    if combine=='AND':
        passed=False if False in vals else None if None in vals else True
    else: passed=True if True in vals else None if None in vals else False
    return {'status':'MATCH' if passed is True else 'INCOMPLETE' if passed is None else 'NO_MATCH',
            'passed':passed,'checks':checks}
