import importlib

import pandas as pd
import pytest


def api():
    return importlib.import_module('alphalab.research.price_corrections')


def rows():
    return pd.DataFrame([
        dict(symbol='600321', date='2022-10-21', open=10.99742644, high=11.05785186,
             low=10.93700102, close=10.93700102, amount=10685888., volume=5879100., adjustment='hfq', source='baostock'),
        dict(symbol='600321', date='2022-10-24', open=14.2966278, high=14.2966278,
             low=13.7467575, close=13.7467575, amount=24603663., volume=13772500., adjustment='hfq', source='baostock'),
    ])


def test_verified_correction_restores_return_without_changing_original_or_turnover():
    original = rows()
    preserved = original.copy(deep=True)
    result = api().apply_verified_price_corrections(original)
    assert result.loc[1, 'close'] == pytest.approx(10.5744485)
    assert result.loc[1, 'open'] == pytest.approx(10.99742644)
    assert result.loc[1, 'close'] / result.loc[0, 'close'] - 1 == pytest.approx(-0.03314917127071826)
    pd.testing.assert_frame_equal(original, preserved)
    pd.testing.assert_frame_equal(result[['amount', 'volume']], original[['amount', 'volume']])
    assert result.loc[1, 'source_correction_id']
    assert pd.isna(result.loc[0, 'source_correction_id'])


@pytest.mark.parametrize(('field', 'value'), [
    ('symbol', '600320'), ('source', 'akshare'), ('source', None),
    ('date', '2022-10-23'), ('adjustment', 'qfq'), ('adjustment', 'none'),
])
def test_unrelated_symbol_source_date_and_basis_remain_untouched(field, value):
    original = rows().iloc[[1]].copy()
    original[field] = value
    result = api().apply_verified_price_corrections(original)
    pd.testing.assert_frame_equal(result[original.columns], original)


def test_correction_is_idempotent_after_concatenating_corrected_and_raw_rows():
    once = api().apply_verified_price_corrections(rows())
    twice = api().apply_verified_price_corrections(once)
    pd.testing.assert_frame_equal(once, twice)
    mixed = pd.concat([once, rows().iloc[[1]]], ignore_index=True)
    result = api().apply_verified_price_corrections(mixed)
    assert result.loc[1, 'close'] == result.loc[2, 'close']


def test_explicit_provider_can_identify_source_less_response_with_custom_date_column():
    raw = rows().drop(columns='source').rename(columns={'date': 'ts'})
    result = api().apply_verified_price_corrections(raw, date_column='ts', provider='baostock')
    assert result.loc[1, 'close'] == pytest.approx(10.5744485)
    raw['source'] = 'akshare'
    untouched = api().apply_verified_price_corrections(raw, date_column='ts', provider='baostock')
    assert untouched.loc[1, 'close'] == 13.7467575


def test_source_marker_survives_standard_market_schema_and_provider_fallback():
    module = api()
    corrected = module.apply_verified_price_corrections(rows().drop(columns='source'), provider='baostock')
    correction_id = corrected.loc[1, 'source_correction_id']
    assert corrected.loc[1, 'source'] == 'baostock+correction:' + correction_id
    reloaded = corrected.drop(columns='source_correction_id')
    result = module.apply_verified_price_corrections(reloaded, provider='baostock')
    assert result.loc[1, 'close'] == pytest.approx(10.5744485)
    assert result.loc[0, 'close'] == 10.93700102
    assert result.loc[1, 'source_correction_id'] == correction_id
    assert pd.isna(result.loc[0, 'source_correction_id'])
    pd.testing.assert_frame_equal(result, module.apply_verified_price_corrections(result))


def test_active_manifest_correction_requires_symbol_and_overlapping_dates():
    module = api()
    assert module.active_price_corrections(['000936'], '2022-01-01', '2025-12-03') == []
    assert module.active_price_corrections(['600321'], '2022-01-01', '2022-10-23') == []
    active = module.active_price_corrections(['600321'], '2022-07-08', '2025-12-03')
    assert len(active) == 1
    evidence = active[0]
    assert evidence['multiplier'] == 6.042542 / 7.855290
    assert evidence['source'] == 'baostock'
    assert evidence['effective_from'] == '2022-10-24'
    assert evidence['source_correction_id'] == module.apply_verified_price_corrections(rows()).loc[1, 'source_correction_id']
    assert evidence['evidence']['annual_report']['source_url'].startswith('https://')
    evidence['evidence']['annual_report']['source_url'] = 'mutated'
    assert module.active_price_corrections(['600321'], '2022-10-24', '2022-10-24')[0]['evidence']['annual_report']['source_url'].startswith('https://')
