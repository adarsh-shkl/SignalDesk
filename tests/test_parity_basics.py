import pandas as pd
from app.indicators import add_indicators, sma, ema
from app.engine import demo_frame,supertrend_approx


def test_pine_bb_defaults_20_2_population_std():
    data=demo_frame('TCS','15m')
    actual=add_indicators(data)
    expected=data.close.rolling(20).mean()+2*data.close.rolling(20).std(ddof=0)
    assert abs(actual.bb_upper.iat[-1]-expected.iat[-1])<1e-8
    assert abs(actual.sma7.iat[-1]-data.close.tail(7).mean())<1e-8


def test_supertrend_approx_is_explicitly_separate():
    d=add_indicators(demo_frame('INFY','1d'))
    values=supertrend_approx(d)
    assert values.notna().sum()>200
