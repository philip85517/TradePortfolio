"""Durable evidence of source adjustment limitations, scoped to an exact query.

Callers should include provider configuration/version in source_identity and the
original bars fingerprint in data_identity. Any identity or range change makes
old evidence inapplicable; no market data is stored or modified here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .data_repair import _encoded, _sync_directory


def _scope(source_identity, symbol, start, end, requested, data_identity):
    return dict(version=1, source_identity=source_identity, data_identity=data_identity,
                symbol=symbol, start=str(start), end=str(end), requested_adjustment=requested)


def _path(cache_dir, scope):
    return Path(cache_dir) / 'source_capabilities' / (hashlib.sha256(_encoded(scope)).hexdigest() + '.json')


def record_adjustment_limitation(cache_dir, source_identity, symbol, start, end, requested, returned,
                                 *, data_identity='', response_evidence=None):
    """Atomically save validated mismatch evidence and return the record.

    This records an observed limitation, not a claim that the provider can never
    serve this adjustment. Only validated nonempty responses belong here.
    """
    scope = _scope(source_identity, symbol, start, end, requested, data_identity)
    returned = sorted(set([returned] if isinstance(returned, str) else returned))
    if not returned or not set(returned) <= {'hfq', 'qfq', 'none'} or returned == [requested]:
        raise ValueError('An adjustment limitation requires a known mismatched response')
    payload = dict(scope, classification='source_adjustment_unavailable',
                   returned_adjustment=returned, response_evidence=response_evidence or [],
                   recorded_at=datetime.now(timezone.utc).isoformat())
    target = _path(cache_dir, scope)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = _encoded(payload)
    fd, name = tempfile.mkstemp(prefix='.limitation-', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _sync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def load_adjustment_limitation(cache_dir, source_identity, symbol, start, end, requested,
                               *, data_identity=''):
    """Return matching durable evidence; absent or malformed records allow retry."""
    scope = _scope(source_identity, symbol, start, end, requested, data_identity)
    try:
        payload = json.loads(_path(cache_dir, scope).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in scope.items()):
        return None
    returned = payload.get('returned_adjustment')
    if (payload.get('classification') != 'source_adjustment_unavailable'
            or not isinstance(returned, list) or not returned
            or any(value not in ('hfq', 'qfq', 'none') for value in returned)
            or all(value == requested for value in returned)):
        return None
    return payload


def clear_adjustment_limitation(cache_dir, source_identity, symbol, start, end, requested,
                                *, data_identity=''):
    """Explicitly clear this scope so a user-requested recheck may query again."""
    target = _path(cache_dir, _scope(source_identity, symbol, start, end, requested, data_identity))
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    _sync_directory(target.parent)
    return True
