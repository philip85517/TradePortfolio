"""Known market events have phase-specific meaning without data repair actions."""
import pandas as pd
import pytest
from alphalab.research.data_readiness import liquidity_issues, make_issue, finalize_readiness

DATES = dict(signal_date='2023-01-02', entry_date='2023-01-03', exit_date='2023-01-05')


def rows(status='0', volume=None, amount=None):
    return pd.DataFrame(dict(date=pd.date_range('2023-01-01', periods=5),
                             tradestatus=status, volume=volume, amount=amount))


def test_phase_aware_suspensions_are_explained_without_download_actions():
    issues = liquidity_issues(rows(), '600000.SH', True, DATES, policy='phase-aware-v1')
    assert {issue['phase'] for issue in issues} == {'warmup', 'signal', 'entry', 'holding', 'exit'}
    expected = dict(warmup='零成交', signal='不具备', entry='现金', holding='估值', exit='未实现')
    for issue in issues:
        assert issue['code'] == 'SUSPENDED'
        assert issue['resolution'] == 'none'
        assert issue['severity'] == 'info'
        assert issue['category'] == 'market_event'
        assert issue['blocking_scope'] == 'none'
        assert expected[issue['phase']] in issue['action']
        assert issue['evidence']['rows'][0]['volume'] is None
    result = finalize_readiness(dict(issues=issues, requirement_id='r', dates=DATES))
    assert result['repair_plan']['actions'] == []


def test_phase_aware_unknown_status_still_blocks_and_legacy_suspension_unchanged():
    issues = liquidity_issues(rows(status=''), '600000.SH', True, DATES, policy='phase-aware-v1')
    assert all(issue['severity'] == 'blocking' and issue['resolution'] == 'verify' for issue in issues)
    legacy = liquidity_issues(rows(), '600000.SH', True, DATES)
    assert all(issue['severity'] == 'blocking' and issue['resolution'] == 'unsupported' for issue in legacy)


def test_nonblocking_download_issue_does_not_create_repair_action():
    issue = make_issue('MISSING_BARS', 'example', '', symbol='600000.SH')
    issue['severity'] = 'info'
    result = finalize_readiness(dict(issues=[issue], requirement_id='r', dates=DATES))
    assert result['repair_plan']['actions'] == []


@pytest.mark.parametrize('volume,amount,rule', [(1., 1., True), (1., None, False)])
def test_clean_rows_skip_groupby_entirely(monkeypatch, volume, amount, rule):
    def unexpected_groupby(*args, **kwargs):
        raise AssertionError('clean rows should skip groupby')
    monkeypatch.setattr(pd.DataFrame, 'groupby', unexpected_groupby)
    assert liquidity_issues(rows(status='1', volume=volume, amount=amount), '600000.SH', rule, DATES) == []


@pytest.mark.parametrize('bad', [None, float('inf'), -float('inf'), 0., -1.])
def test_nonfinite_and_nonpositive_volume_remains_blocking(bad):
    issues = liquidity_issues(rows(status='1', volume=bad, amount=1.), '600000.SH', True, DATES,
                              policy='phase-aware-v1')
    assert len(issues) == 5
    assert all(issue['code'] == 'UNTRADABLE' and issue['severity'] == 'blocking' for issue in issues)


def test_nullable_quantities_preserve_unknown_status_requirement():
    part = rows(status='')
    part['volume'] = pd.Series([pd.NA] * 5, dtype='Float64')
    part['amount'] = pd.Series([pd.NA] * 5, dtype='Float64')
    issues = liquidity_issues(part, '600000.SH', True, DATES, policy='phase-aware-v1')
    assert len(issues) == 5
    assert all(issue['code'] == 'TRADING_STATUS_UNKNOWN' for issue in issues)
