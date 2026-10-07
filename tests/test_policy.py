import pytest

from engineering.policy import transition_allowed, validate_policy


@pytest.mark.parametrize('old,new,allowed', [
    ('created','approved',True), ('approved','processing',True),
    ('processing','created',False), ('invoiced','created',False),
    ('processing','approved',False), ('invoiced','approved',False),
    ('processing','invoiced',True), ('invoiced','processing',True),
    ('shipped','delivered',True), ('delivered','shipped',False),
    ('canceled','delivered',False), ('unavailable','delivered',False),
    ('canceled','unavailable',False), ('approved','canceled',True),
    ('delivered','delivered',True), ('created','delivered',True),
])
def test_transitions(old,new,allowed):
    assert transition_allowed(old,new) is allowed


def test_snapshot_policy():
    assert validate_policy() is None
    assert validate_policy('upsert','2026-10-01T12:00:00+08:00')=='2026-10-01T04:00:00+00:00'
    for value in (None,'bad','2026-10-01T12:00:00','2999-01-01T00:00:00Z'):
        with pytest.raises(ValueError): validate_policy('upsert',value)
    for value in ('2026-09-30T00:00:00Z','2026-10-01T04:00:00Z'):
        with pytest.raises(ValueError,match='旧快照'): validate_policy('upsert',value,'2026-10-01T04:00:00+00:00')
    with pytest.raises(ValueError,match='删除'): validate_policy('delete')
