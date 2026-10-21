import pytest
from pydantic import ValidationError
from kompas_mcp.models import SketchRequest,FeatureRequest,Scenario,Selector

def test_invalid_geometry_rejected():
    for primitive in [{'kind':'circle','radius':-1},{'kind':'line','start':[0,0],'end':[0,0]}, {'kind':'circle','radius':float('nan')}]:
        with pytest.raises(ValidationError):SketchRequest(document_id='a',name='s',primitives=[primitive])

def test_cross_step_dependencies_are_ordered():
    with pytest.raises(ValidationError):Scenario(steps=[{'name':'a','action':'document_create','arguments':{},'depends_on':['b']}])
    with pytest.raises(ValidationError):Scenario(steps=[{'name':'a','action':'document_create','arguments':{}},{'name':'a','action':'document_create','arguments':{}}])

def test_unknown_feature_and_extra_properties_rejected():
    with pytest.raises(ValidationError):FeatureRequest(document_id='a',name='x',operation={'kind':'execute_code','code':'unsafe'})
    with pytest.raises(ValidationError):Selector(document_id='a',kind='face',tolerance=0)


def test_assembly_frames_and_names():
    from kompas_mcp.models import AssemblyCompose
    good={'name':'pin','source':'pin.m3d','x_axis':[1,0,0],'z_axis':[0,1,0]}
    AssemblyCompose(document_id='d',components=[good])
    for bad in [{**good,'x_axis':[2,0,0]}, {**good,'z_axis':[1,0,0]}, {**good,'origin':[float('inf'),0,0]}]:
        with pytest.raises(ValidationError):AssemblyCompose(document_id='d',components=[bad])
    with pytest.raises(ValidationError):AssemblyCompose(document_id='d',components=[good,good])
