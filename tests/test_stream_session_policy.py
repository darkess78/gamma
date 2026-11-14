"""Budget boundaries independent of model wording or the stream implementation."""
import math

import pytest

from gamma.coordination.stream_session_policy import speech_budget


@pytest.mark.parametrize('records,expected', [([], 'eligible'),
    ([{'started_at':39,'duration_seconds':35}], 'eligible'),
    ([{'started_at':40,'duration_seconds':35}], 'speech_budget'),
    ([{'started_at':94,'duration_seconds':2}], 'minimum_gap'),
    ([{'started_at':92,'duration_seconds':2}], 'eligible'),
    ([{'started_at':80,'duration_seconds':32}], 'speech_budget'),
    ([{'started_at':80,'duration_seconds':31}], 'eligible')])
def test_budget_boundaries(records,expected):
    assert speech_budget(records,now=100,proposed_seconds=4)==expected


@pytest.mark.parametrize('field,value', [('now', True),('now',math.nan),
    ('proposed_seconds',-1),('proposed_seconds',math.inf),('minimum_gap_seconds',0),
    ('maximum_seconds_per_minute',61),('maximum_seconds_per_minute',True)])
def test_budget_rejects_invalid_options(field,value):
    args={'now':100,'proposed_seconds':4};args[field]=value
    with pytest.raises(ValueError): speech_budget([],**args)


@pytest.mark.parametrize('records', [None,{},[{'started_at':101,'duration_seconds':4}],
    [{'started_at':80,'duration_seconds':True}],[{'started_at':80,'duration_seconds':-1}],
    [{'started_at':80,'duration_seconds':4,'authority':True}],
    [{'started_at':80,'duration_seconds':4}]*129])
def test_budget_rejects_invalid_records(records):
    with pytest.raises(ValueError): speech_budget(records,now=100,proposed_seconds=4)
