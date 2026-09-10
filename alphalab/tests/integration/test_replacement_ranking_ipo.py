from types import SimpleNamespace
import pandas as pd
import pytest

from alphalab.research.replacement_inputs import attach_replacement_inputs
from alphalab.tests.integration.test_replacement_normalization import _ranking_fixture


def fixture(tmp_path, *, history_present=True, missing=False):
    bars,backend=_ranking_fixture(tmp_path)
    ipo=bars.iloc[-20:].copy()
    ipo['symbol']='600001'
    ipo['listed_date']=str(ipo.date.min().date())
    if missing:
        ipo=ipo.drop(ipo.index[5])
    bars=pd.concat([bars,ipo],ignore_index=True)
    history=pd.DataFrame([dict(symbol=s,listed_date=listed,delisted_date=None,snapshot_id='fixture',source='fixture',industry_level1='银行')
                          for s,listed in [('600000','2000-01-01'),('600001',str(ipo.date.min().date()))]]) if history_present else pd.DataFrame()
    frozen=SimpleNamespace(bars=bars)
    attach_replacement_inputs(frozen,bars,history,backend,{})
    return frozen,bars.date.max()


@pytest.mark.parametrize('history_present',[True,False])
def test_ipo_complete_since_listing_is_ineligible_not_global_missing_error(tmp_path,history_present):
    frozen,day=fixture(tmp_path,history_present=history_present)
    assert frozen.replacement_rank(day,set()) == ['600000']


def test_ipo_missing_post_listing_session_still_fails(tmp_path):
    frozen,day=fixture(tmp_path,missing=True)
    with pytest.raises(ValueError,match='REPLACEMENT_DATA_MISSING.*600001'):
        frozen.replacement_rank(day,set())
