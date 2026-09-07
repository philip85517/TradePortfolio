"""Source limitations must never suppress a different source or data revision."""
import importlib
import json


def capabilities():
    return importlib.import_module('alphalab.research.source_capabilities')


def scope(tmp_path, **changes):
    return dict(cache_dir=tmp_path, source_identity={'provider': 'baostock', 'version': 'v1'},
                symbol='302132.SZ', start='2022-07-08', end='2022-12-30', requested='hfq',
                data_identity='original-bars-v1') | changes


def test_limitation_roundtrip_is_scoped_to_source_data_symbol_range_and_adjustment(tmp_path):
    api = capabilities()
    evidence = [{'date': '2022-12-30', 'returned_adjustment': 'none'}]
    api.record_adjustment_limitation(**scope(tmp_path), returned=['none'], response_evidence=evidence)
    record = api.load_adjustment_limitation(**scope(tmp_path))
    assert record['requested_adjustment'] == 'hfq'
    assert record['returned_adjustment'] == ['none']
    assert record['response_evidence'] == evidence
    for changes in [dict(source_identity={'provider': 'other'}), dict(data_identity='new-bars'),
                    dict(symbol='000001.SZ'), dict(start='2022-01-01'), dict(end='2023-01-01'),
                    dict(requested='qfq')]:
        assert api.load_adjustment_limitation(**scope(tmp_path, **changes)) is None
    assert len(list(tmp_path.rglob('*.json'))) == 1


def test_repeated_evidence_keeps_classification_and_explicit_clear_allows_retry(tmp_path):
    api = capabilities()
    for returned in ['none', ['qfq']]:
        api.record_adjustment_limitation(**scope(tmp_path), returned=returned)
    assert api.load_adjustment_limitation(**scope(tmp_path))['classification'] == 'source_adjustment_unavailable'
    assert api.clear_adjustment_limitation(**scope(tmp_path)) is True
    assert api.load_adjustment_limitation(**scope(tmp_path)) is None
    assert api.clear_adjustment_limitation(**scope(tmp_path)) is False


def test_damaged_evidence_does_not_suppress_retry(tmp_path):
    api = capabilities()
    api.record_adjustment_limitation(**scope(tmp_path), returned=['none'])
    path = next(tmp_path.rglob('*.json'))
    path.write_text('{broken')
    assert api.load_adjustment_limitation(**scope(tmp_path)) is None
    path.write_text(json.dumps({'classification': 'source_adjustment_unavailable'}))
    assert api.load_adjustment_limitation(**scope(tmp_path)) is None


def test_failed_atomic_publication_preserves_prior_evidence(tmp_path, monkeypatch):
    import pytest
    api = capabilities()
    api.record_adjustment_limitation(**scope(tmp_path), returned=['none'])
    original = api.load_adjustment_limitation(**scope(tmp_path))
    def fail_replace(*args):
        raise OSError('disk failure')
    monkeypatch.setattr(api.os, 'replace', fail_replace)
    with pytest.raises(OSError, match='disk failure'):
        api.record_adjustment_limitation(**scope(tmp_path), returned=['qfq'])
    assert api.load_adjustment_limitation(**scope(tmp_path)) == original
    assert not list(tmp_path.rglob('.limitation-*'))
