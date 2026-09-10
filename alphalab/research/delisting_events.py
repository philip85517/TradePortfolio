"""Offline, source-backed formal termination decisions; never a complete market feed.

The caller supplies actual trading sessions and checks executable bars separately.
Date-only disclosures become known on a later session, never on their printed day.
Risk warnings, ST designations and a last available bar are not decision evidence.
"""
from __future__ import annotations

from datetime import date, datetime, time
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo('Asia/Shanghai')
BUNDLED_PATH = Path(__file__).with_name('data') / 'delisting_events.json'
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')


def _date(value: object, field: str) -> date:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError(f'{field} must be an ISO date (YYYY-MM-DD)')
    return date.fromisoformat(value)


def _published(value: object) -> date | datetime:
    if not isinstance(value, str):
        raise ValueError('published_at must be an ISO date or timezone-aware datetime')
    if _DATE.fullmatch(value):
        return _date(value, 'published_at')
    if not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', value):
        raise ValueError('published_at must be an ISO date or timezone-aware datetime')
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('published_at datetime must include an explicit timezone')
    return result.astimezone(SHANGHAI)


def _validate(record: object, source_dataset: str, coverage: str) -> dict:
    if not isinstance(record, dict):
        raise ValueError('each delisting event must be an object')
    event = dict(record)
    symbol = event.get('symbol')
    if not isinstance(symbol, str) or not re.fullmatch(r'[036489]\d{5}', symbol):
        raise ValueError('symbol must be a six-digit mainland stock code')
    if event.get('event_type') != 'termination_decision':
        raise ValueError('event_type must be termination_decision')
    if not isinstance(event.get('event_id'), str) or not event['event_id'].strip():
        raise ValueError('event_id must be a nonempty string')
    published = _published(event.get('published_at'))
    url = event.get('source_url')
    if not isinstance(url, str):
        raise ValueError('source_url must be an HTTPS URL')
    parsed_url = urlsplit(url)
    if parsed_url.scheme != 'https' or not parsed_url.hostname or any(c.isspace() for c in url):
        raise ValueError('source_url must be an HTTPS URL')
    published_date = published.date() if isinstance(published, datetime) else published
    for field in ('trading_resumes_on', 'delisted_date'):
        if event.get(field) is not None:
            parsed = _date(event[field], field)
            if parsed < published_date:
                raise ValueError(f'{field} precedes disclosure')
    if event.get('trading_resumes_on') and event.get('delisted_date'):
        if event['trading_resumes_on'] >= event['delisted_date']:
            raise ValueError('trading_resumes_on must precede delisted_date')
    event['published_precision'] = 'timestamp' if isinstance(published, datetime) else 'date'
    event['source_dataset'] = source_dataset
    event['coverage'] = coverage
    return event


def _read(path: Path, *, bundled: bool) -> list[dict]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    records = payload.get('events') if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f'{path}: expected an event array or object containing events')
    dataset = 'builtin:delisting_events' if bundled else str(path.resolve())
    coverage = 'fixed_source_bundle_not_complete' if bundled else 'user_supplied_not_complete'
    return [_validate(record, dataset, coverage) for record in records]


def _order(event: dict) -> tuple[datetime, str]:
    published = _published(event['published_at'])
    # Unknown intraday precision cannot outrank verified earlier same-day timing.
    if not isinstance(published, datetime):
        published = datetime.combine(published, time.max, SHANGHAI)
    return published, event['event_id']


def load_events(path: str | Path | None = None) -> list[dict]:
    """Merge bundled evidence with an optional JSON file, earliest decision per stock.

An absent optional file means no local additions. A present malformed file fails
closed. Conflicting reuse of an event ID is rejected even if a later decision
would otherwise be discarded. No network calls or implicit warning inference.
"""
    events = _read(BUNDLED_PATH, bundled=True)
    if path is not None:
        optional = Path(path)
        if optional.exists() and optional.resolve() != BUNDLED_PATH.resolve():
            events.extend(_read(optional, bundled=False))
    by_id: dict[str, dict] = {}
    derived = {'source_dataset', 'coverage', 'published_precision'}
    for event in events:
        previous = by_id.get(event['event_id'])
        if previous is not None:
            evidence = {k: v for k, v in event.items() if k not in derived}
            previous_evidence = {k: v for k, v in previous.items() if k not in derived}
            if evidence != previous_evidence:
                raise ValueError(f"conflicting delisting evidence for event_id {event['event_id']}")
        else:
            by_id[event['event_id']] = event
    by_symbol: dict[str, dict] = {}
    for event in sorted(by_id.values(), key=_order):
        by_symbol.setdefault(event['symbol'], event)
    return [by_symbol[symbol] for symbol in sorted(by_symbol)]


def eligible_on(event: dict, session: str | date | datetime) -> bool:
    """Whether the disclosure was public by this session's Shanghai 09:30 open.

This is public availability, not tradability. Suspension, delisting and the
exchange calendar belong to the execution caller. A date-only disclosure needs
a strictly later local date; explicit timestamps may qualify on the same day.
"""
    if isinstance(session, datetime):
        session_date = session.astimezone(SHANGHAI).date() if session.tzinfo else session.date()
    elif isinstance(session, date):
        session_date = session
    else:
        session_date = _date(session, 'session')
    published = _published(event.get('published_at'))
    if isinstance(published, datetime):
        return published <= datetime.combine(session_date, time(9, 30), SHANGHAI)
    return published < session_date
