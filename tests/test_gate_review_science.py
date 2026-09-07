"""Lead acceptance gate with known answers; executors must not edit."""
import numpy as np
import pandas as pd
import pytest
import xarray as xr


def cube():
    a=np.arange(3*2*8*8,dtype=np.float32).reshape(3,2,8,8)+1
    return xr.Dataset({'image':(('time','band','y','x'),a),
                       'valid':(('time','y','x'),np.ones((3,8,8),bool))},
        coords={'time':pd.date_range('2017-02-02',periods=3,freq='300s'),
                'band':['RED','GREEN'],'product_id':('time',['A_V01','B_V01','C_V01'])},
        attrs={'instrument':'JunoCam','km_per_px':15.,'region':'test','level':'frame'})


def test_explicit_band_and_true_cadence():
    from jiram_catalog.science import prepare_stack, stack_readiness
    d=cube();d.image.attrs['units']='DN'
    with pytest.raises(ValueError): prepare_stack(d)
    one=prepare_stack(d,band='GREEN')
    assert one.image.dims == ('time','y','x')
    np.testing.assert_array_equal(one.image,d.image.sel(band='GREEN'))
    assert one.image.attrs['units']=='DN'
    good=stack_readiness(d,band='GREEN')
    assert good['ready'] and good['n_observations']==3 and good['n_realizations']>=1
    bad=d.assign_coords(time=pd.to_datetime(['2017-02-02','2017-02-02 00:05:00','2017-02-02 00:15:00'],format='mixed'))
    assert not stack_readiness(bad,band='RED')['ready']
    empty=d.copy();empty['valid']=xr.zeros_like(empty.valid)
    assert not stack_readiness(empty,band='RED')['ready']


def test_power_law_fit_and_fragmented_mask_are_measured():
    from jiram_catalog.science import fit_spectrum, mask_diagnostics
    k=np.geomspace(1e-6,1e-3,50)
    result=fit_spectrum(k,7*k**-3)
    assert result['slope']==pytest.approx(-3,abs=1e-8)
    yy,xx=np.mgrid[:32,:32];m=(xx%2==0)&(yy%2==0)
    d=mask_diagnostics(xx.astype(float)+yy,m,1000.)
    assert d['valid_frac']==pytest.approx(.25)
    assert d['components']>1
