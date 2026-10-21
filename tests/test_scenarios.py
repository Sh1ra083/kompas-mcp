import json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from kompas_mcp.models import Scenario
from kompas_mcp.scenarios import run,run_file
from kompas_mcp.errors import CadError

class Backend:
    def __init__(self,root):
        self.root=root;self.documents={'user':{'com':SimpleNamespace(PathName='user.m3d',Changed=False)}};self.registry={};self.actions={'create':None,'verify':None,'fail':None};self.checked=[]
    def execute(self,action,args):
        if action=='fail':raise CadError('build_failed','test failure')
        if action=='create':
            self.documents['new']={'com':SimpleNamespace(PathName='new.m3d',Changed=False)}
            return {'document_id':'new','large_evidence':['geometry']*1000}
        return {'passed':True}
    def checkpoint(self,d):self.checked.append(d);return {'path':d+'.bak'}
    def doc(self,d):return self.documents[d]
    def document_close(self,d,discard_changes):assert not discard_changes;del self.documents[d]
    def path(self,p):return self.root/p

def recipe():return dict(response_detail='compact',close_created=True,steps=[dict(name='model',action='create',arguments={}),dict(name='check',action='verify',arguments={'document_id':'$model.document_id'})],outputs={'check':'$check'})

def test_compact_retains_evidence_and_closes_only_new_saved_documents(tmp_path):
    b=Backend(tmp_path);r=run(b,recipe());full=json.loads(__import__('pathlib').Path(r['report']['path']).read_text(encoding='utf-8'))
    assert r['outputs']=={'check':{'passed':True}}
    assert len(full['steps']['model']['large_evidence'])==1000
    assert list(b.documents)==['user'] and b.checked==['new']
    assert len(json.dumps(r))<len(json.dumps(full))/10

def test_failure_keeps_state_and_report(tmp_path):
    b=Backend(tmp_path);s=recipe();s['steps'][1]['action']='fail'
    with pytest.raises(CadError) as e:run(b,s)
    r=e.value.result()['error'];assert 'scenario_failed' in json.dumps(r)
    assert 'new' in b.documents and len(list(tmp_path.glob('scenario-report-*.json')))==1

def test_forward_reference_rejected_before_execution(tmp_path):
    b=Backend(tmp_path);s=recipe();s['steps'][0]['arguments']={'x':'$check.passed'}
    with pytest.raises(ValidationError):run(b,s)
    assert list(b.documents)==['user']

def test_file_parameters_are_values_not_code(tmp_path):
    s=recipe();s['steps'][0]['arguments']={'size':'$params.size'}
    (tmp_path/'recipe.json').write_text(json.dumps(s))
    with pytest.raises(CadError):run_file(Backend(tmp_path),'recipe.json',{})
    assert run_file(Backend(tmp_path),'recipe.json',{'size':25})['status']=='completed'
