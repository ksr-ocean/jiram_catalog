"""Lead acceptance gate for the accepted review; executors must not edit."""
import numpy as np
import pandas as pd


def test_preferred_observation_versions_are_not_time_samples():
    from jiram_catalog.junocam.policy import observation_id, preferred_rows
    assert observation_id('JNCR_2017033_04C00099_V02') == 'JNCR_2017033_04C00099'
    frame = pd.DataFrame({'product_id': ['X_V02', 'X_V10', 'Y_V01'],
                          'start_time': pd.to_datetime(['2017-01-01', '2017-01-01 00:00:00.015', '2017-01-01 00:05:00'], format='mixed')})
    preferred = preferred_rows(frame)
    assert list(preferred.product_id) == ['X_V10', 'Y_V01']


def test_quality_requires_evidence_and_rejects_no_signal():
    from jiram_catalog.junocam.policy import assess_observation
    nominal = dict(product_id='JNCR_2017033_04C00099_V02', orbit=4,
                   quality_epoch='nominal', metrics_ok=True, geo_ok=True,
                   streak_index=.01, saturation_frac=0., zero_frac=.2,
                   max_dn=5000., bloom_flag=False, quality_tier='A')
    assert assess_observation(nominal)['status'] == 'eligible'
    assert assess_observation(dict(nominal, zero_frac=1., max_dn=0.))['status'] == 'excluded'
    assert assess_observation(dict(nominal, saturation_frac=.5))['status'] == 'excluded'
    assert assess_observation(dict(nominal, rationale_desc='Image is content free'))['status'] == 'excluded'
    assert assess_observation(dict(product_id='UNKNOWN',orbit=99))['status'] != 'eligible'
    assert assess_observation(dict(nominal,metrics_ok=False))['status'] != 'eligible'
    assert assess_observation(nominal)['policy_version'] == 'failure-exclusion-v1'
