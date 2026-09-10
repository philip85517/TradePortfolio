"""Announcement evidence must not introduce intraday look-ahead or invent coverage."""
import importlib
import json
from datetime import date

import pytest


def api():
    return importlib.import_module('alphalab.research.delisting_events')


def decision(**changes):
    return dict(symbol='600000', event_type='termination_decision',
                event_id='fixture-1', published_at='2025-06-06',
                source_url='https://example.org/decision.pdf', **changes)


def write_events(tmp_path, records):
    path = tmp_path / 'events.json'
    path.write_text(json.dumps(records), encoding='utf-8')
    return path


def test_builtin_is_auditable_and_optional_file_extends_it(tmp_path):
    module = api()
    events = module.load_events(write_events(tmp_path, [decision()]))
    assert {e['symbol'] for e in events} >= {'002336', '600000'}
    bundled = next(e for e in events if e['symbol'] == '002336')
    assert bundled['source_url'] == 'https://static.cninfo.com.cn/finalpage/2025-06-06/1223789415.PDF'
    assert bundled['published_at'] == '2025-06-06'
    assert bundled['published_precision'] == 'date'
    assert bundled['trading_resumes_on'] == '2025-06-13'
    assert bundled['delisted_date'] == '2025-07-04'
    assert bundled['coverage'] == 'fixed_source_bundle_not_complete'
    assert module.load_events(tmp_path / 'missing.json') == module.load_events()


@pytest.mark.parametrize(('published_at', 'session', 'expected'), [
    ('2025-06-06', '2025-06-06', False),
    ('2025-06-06', '2025-06-09', True),
    ('2025-06-06T09:29:59+08:00', '2025-06-06', True),
    ('2025-06-06T09:30:00+08:00', '2025-06-06', True),
    ('2025-06-06T09:30:01+08:00', '2025-06-06', False),
    ('2025-06-06T10:00:00+08:00', '2025-06-09', True),
    ('2025-06-06T01:29:59Z', '2025-06-06', True),
    ('2025-06-05T20:00:00-07:00', '2025-06-06', False),
])
def test_disclosure_known_at_shanghai_session_open(published_at, session, expected):
    event = decision()
    event['published_at'] = published_at
    assert api().eligible_on(event, session) is expected


def test_public_availability_is_independent_of_trading_halt_or_delisting():
    event = next(e for e in api().load_events() if e['symbol'] == '002336')
    assert api().eligible_on(event, date(2025, 6, 9))
    assert api().eligible_on(event, '2025-07-07')


def test_earliest_decision_per_symbol_wins_and_exact_duplicates_are_idempotent(tmp_path):
    early = decision()
    late = dict(early, event_id='fixture-repeat', published_at='2025-06-10')
    events = api().load_events(write_events(tmp_path, [late, early, early]))
    selected = [e for e in events if e['symbol'] == '600000']
    assert len(selected) == 1
    assert selected[0]['event_id'] == 'fixture-1'


def test_repeated_event_id_with_conflicting_evidence_is_rejected(tmp_path):
    event = decision()
    with pytest.raises(ValueError, match='conflict'):
        api().load_events(write_events(tmp_path, [event, dict(event, published_at='2025-06-10')]))


@pytest.mark.parametrize(('field', 'value'), [
    ('symbol', '60000'), ('symbol', 600000),
    ('event_id', ''), ('event_type', 'risk_warning'),
    ('published_at', '2025-06-06T09:00:00'),
    ('published_at', '2025-02-30'), ('published_at', '20250606'),
    ('source_url', 'http://example.org/decision.pdf'), ('source_url', 'https:///x'),
    ('trading_resumes_on', 'tomorrow'), ('delisted_date', '2025-06-05'),
])
def test_invalid_evidence_fails_closed(tmp_path, field, value):
    event = dict(decision(), **{field: value})
    with pytest.raises(ValueError):
        api().load_events(write_events(tmp_path, [event]))


def test_bad_file_shape_does_not_silently_remove_evidence(tmp_path):
    with pytest.raises(ValueError):
        api().load_events(write_events(tmp_path, {'symbol': '600000'}))


def test_builtin_conflict_is_rejected_when_optional_file_reuses_event_id(tmp_path):
    event = next(e for e in api().load_events() if e['symbol'] == '002336')
    with pytest.raises(ValueError, match='conflict'):
        api().load_events(write_events(tmp_path, [dict(event, published_at='2025-06-05')]))
