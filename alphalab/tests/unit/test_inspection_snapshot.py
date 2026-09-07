import pandas as pd
import pytest
from alphalab.research.inspection_snapshot import frame_fingerprint


def test_fingerprint_stable_for_row_order_but_detects_values_schema_and_duplicates():
    frame=pd.DataFrame({'symbol':['b','a'],'price':[2.,1.]})
    digest=frame_fingerprint(frame)
    assert frame_fingerprint(frame.iloc[::-1]) == digest
    assert frame_fingerprint(frame.assign(price=[2.,3.])) != digest
    assert frame_fingerprint(pd.concat([frame,frame.iloc[:1]])) != digest
    assert frame_fingerprint(frame.rename(columns={'price':'close'})) != digest


def test_fingerprint_can_cancel_between_chunks():
    with pytest.raises(InterruptedError):
        frame_fingerprint(pd.DataFrame({'x':range(5)}),cancelled=lambda:True)


def test_inspection_reuses_only_unchanged_source_and_sidecars(tmp_path, monkeypatch):
    from alphalab.research.wizard_backend import WizardResearchBackend
    backend = WizardResearchBackend(tmp_path/'market.duckdb', cache_dir=tmp_path/'cache')
    calls = []
    def inspect(scope, progress, cancelled):
        calls.append(scope)
        return {'status':'READY', 'issues':[], 'coverage':[], 'requirement_id':'one', 'data_identity':str(len(calls))}, pd.DataFrame(), pd.DataFrame()
    monkeypatch.setattr(backend, '_inspect_raw', inspect)
    first = backend.inspect({'scope':'one'})
    assert backend.inspect({'scope':'one'}) == first
    path = tmp_path/'cache'/'source_capabilities'/'limitation.json'
    path.parent.mkdir(parents=True)
    path.write_text('{}')
    assert backend.inspect({'scope':'one'})['data_identity'] == '2'
    path.unlink()
    assert backend.inspect({'scope':'one'})['data_identity'] == '3'
    (tmp_path/'market.duckdb').write_bytes(b'changed source')
    assert backend.inspect({'scope':'one'})['data_identity'] == '4'
