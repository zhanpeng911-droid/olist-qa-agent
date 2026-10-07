import pytest
from engineering.reports import where,ratio,enrich


def test_parameterized_month_and_state_filters():
    sql,args=where('2017-01','2018-08','SP','o')
    assert args==['2017-01-01','2018-08-01','SP']
    assert 'DATE_ADD(%s,INTERVAL 1 MONTH)' in sql
    assert 'SP' not in sql
    with pytest.raises(ValueError): where('2018-08','2017-01',None)
    with pytest.raises(ValueError): where(None,None,"SP' OR 1=1")


def test_empty_samples_are_null_not_zero():
    assert ratio(0,0) is None
    assert ratio(0,10)==0
    row=enrich({'product_value':None,'delivered_orders':0,'customers':0,'items':None,'late_orders':0,'delivery_sample':0,'low_score_orders':0,'review_sample':0,'canceled_orders':0,'all_orders':0})
    assert row['aov'] is None and row['late_rate'] is None
