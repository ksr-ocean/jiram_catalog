"""Lead-owned synthetic oracle for empty optional footprint arrays."""
import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jiram_catalog.api import science as api


def corners(**values):
    return dict(min_lat=-10.,max_lat=10.,c1_lon=350.,c2_lon=10.,
                c3_lon=10.,c4_lon=350.,**values)


@pytest.mark.parametrize('empty',[[],(),np.array([],dtype=float)])
def test_empty_optional_footprint_uses_known_corners(empty):
    assert api._bbox(corners(fp_lon=empty))==pytest.approx((-10.,10.,350.,20.))


def test_real_footprint_keeps_priority_over_corners():
    assert api._longitude_arc(corners(fp_lon=[40.,50.,45.]))==pytest.approx((40.,10.))


def test_unknown_longitudes_do_not_invent_a_box():
    assert api._bbox(dict(min_lat=-10.,max_lat=10.,fp_lon=[])) is None


def test_full_longitude_extent_is_preserved():
    assert api._longitude_arc(dict(fp_lon=[],lon_span_deg=360.))==(0.,360.)


def test_api_matches_real_catalog_shape_and_preserves_filters(tmp_path,monkeypatch):
    from jiram_catalog.api import catalog
    stamp=pd.Timestamp('2017-03-27T09:00:00')
    rows=[dict(product_id='JC_V01',instrument='JunoCam',band='RED;GREEN;BLUE',
               start_time=stamp,min_lat=-5.,max_lat=5.,fp_lon=np.array([355.,5.,5.,355.]))]
    for pid,band,dt,lo,hi in [('IR_V01','M',30.,350.,10.),('IR_V01','L',30.,350.,10.),
                             ('late_V01','M',400.,350.,10.),('distant_V01','M',10.,90.,110.)]:
        rows.append(dict(product_id=pid,instrument='JIRAM',band=band,
            start_time=stamp+pd.Timedelta(seconds=dt),min_lat=-10.,max_lat=10.,fp_lon=[],
            c1_lon=lo,c2_lon=hi,c3_lon=hi,c4_lon=lo,median_pixel_km=15.))
    monkeypatch.setattr(catalog,'catalog_frame',lambda root:pd.DataFrame(rows))
    app=FastAPI();app.state.mirror=tmp_path;app.include_router(api.router)
    with TestClient(app) as client:
        response=client.post('/api/science/matches',json=dict(product_id='JC_V01',max_dt_s=300,min_overlap=.25,limit=100))
    assert response.status_code==200,response.text
    result=response.json()
    assert len(result['items'])==1
    item=result['items'][0]
    assert item['product_id']=='IR_V01' and item['bands']==['L','M']
    assert item['dt_s']==30. and item['overlap_fraction']==pytest.approx(1.)
    assert 'approximate' in item['overlap_method']


@pytest.mark.parametrize('l_overlaps',[False,True])
def test_combined_product_reports_only_qualifying_physical_halves(tmp_path,monkeypatch,l_overlaps):
    from jiram_catalog.api import catalog
    stamp=pd.Timestamp('2017-03-27T09:00:00')
    rows=[dict(product_id='JC_V01',instrument='JunoCam',band='RED;GREEN;BLUE',
        start_time=stamp,min_lat=-5.,max_lat=5.,fp_lon=[355.,5.,5.,355.])]
    for half in ['M','L']:
        lo,hi=(350.,10.) if half=='M' or l_overlaps else (90.,110.)
        rows.append(dict(product_id='paired_V01',instrument='JIRAM',band='LM',half=half,
            start_time=stamp,min_lat=-10.,max_lat=10.,fp_lon=[],
            c1_lon=lo,c2_lon=hi,c3_lon=hi,c4_lon=lo,median_pixel_km=15.))
    monkeypatch.setattr(catalog,'catalog_frame',lambda root:pd.DataFrame(rows))
    app=FastAPI();app.state.mirror=tmp_path;app.include_router(api.router)
    with TestClient(app) as client:
        response=client.post('/api/science/matches',json=dict(product_id='JC_V01',max_dt_s=300,min_overlap=.25,limit=100))
    assert response.status_code==200,response.text
    items=response.json()['items']
    assert len(items)==1 and items[0]['product_id']=='paired_V01'
    assert items[0]['bands']==(['L','M'] if l_overlaps else ['M'])
