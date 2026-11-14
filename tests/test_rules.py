from app.rules import validate_config,evaluate_rules,DEFAULT
from app.engine import demo_frame,build_chart,frame_summary

def test_rule_builder_default():
    conf=validate_config(DEFAULT)
    assert conf['combine']=='AND' and len(conf['rules'])==2

def test_rule_evaluation_and_or():
    rules=[{'id':'a','timeframe':'15m','left':'close','op':'>','right':'sma20','enabled':True},
           {'id':'b','timeframe':'15m','left':'rsi14','op':'>','right':'80','enabled':True}]
    frame={'15m':{'close':105,'indicators':{'sma20':100,'rsi14':60}}}
    assert evaluate_rules(rules,'AND',frame)['status']=='NO_MATCH'
    assert evaluate_rules(rules,'OR',frame)['status']=='MATCH'
    assert evaluate_rules(rules,'AND',{})['status']=='INCOMPLETE'

def test_bool_rules_and_wrong_operator():
    c=dict(DEFAULT)
    c['rules']=[{'id':'x','timeframe':'1d','left':'bullish_structure_recent','op':'is','right':'true','enabled':True}]
    good=validate_config(c)
    assert evaluate_rules(good['rules'],'AND',{'1d':{'conditions':{'bullish_structure_recent':True}}})['passed']
    c['rules'][0]['op']='>'
    try:validate_config(c);assert False
    except ValueError:pass

def test_native_chart_has_candles_and_rsi_series():
    p=build_chart(demo_frame('RELIANCE','15m'),'15m')
    assert len(p['candles'])==270 and len(p['series']['rsi14'])>1 and 'markers' in p
