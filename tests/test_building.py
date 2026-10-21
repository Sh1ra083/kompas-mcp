from datetime import date
import pytest
from kompas_mcp.building import BuildingProject, ProjectStore, inspect_project
from kompas_mcp.building_norms import NormStore, NormQuery, RuleCard, evaluate


def project():
    return BuildingProject.model_validate({'id':'office','name':'Office','storeys':[{'id':'level'}],
        'walls':[{'id':str(i),'storey_id':'level','start':a,'end':b,'thickness':200}
                 for i,(a,b) in enumerate([((0,0),(6000,0)),((6000,0),(6000,4000)),((6000,4000),(0,4000)),((0,4000),(0,0))])],
        'rooms':[{'id':'room','storey_id':'level','number':'1','name':'Office','use':'office','boundary_walls':['0','1','2','3']}],
        'openings':[{'id':'door','wall_id':'0','kind':'door','offset':1000,'width':900,'height':2100}]})


def test_clear_area_and_moved_wall(tmp_path):
    store=ProjectStore(tmp_path);p=project()
    assert store.save(p,None)['checks']['rooms']['room']['area_m2']==pytest.approx(5.8*3.8)
    p=store.load('office')
    p.walls[0].end=(7000,0);p.walls[1].start=(7000,0);p.walls[1].end=(7000,4000);p.walls[2].start=(7000,4000)
    result=store.save(BuildingProject.model_validate(p.model_dump()),1)
    assert result['checks']['rooms']['room']['area_m2']==pytest.approx(6.8*3.8)
    assert store.load('office').openings[0].offset==1000
    assert (tmp_path/'buildings/history/office.1.json').exists()
    with pytest.raises(ValueError,match='Stale'):store.save(p,1)
    assert store.load('office').revision==2


def test_opening_validation_and_room_gap():
    raw=project().model_dump();raw['openings'][0]['offset']=5900
    with pytest.raises(ValueError,match='exceeds host'):BuildingProject.model_validate(raw)
    p=project();p.walls[2].end=(1,4000)
    assert inspect_project(p)['rooms']['room']['state']!='passed'


def test_rotated_collision_and_storey_isolation():
    raw=project().model_dump()
    raw['furniture']=[dict(id=k,storey_id='level',catalog_id='desk',position=(3000,2000),angle=a,width=1400,depth=700,height=750) for k,a in [('a',0),('b',45)]]
    p=BuildingProject.model_validate(raw)
    assert any(f['kind']=='furniture_collision' for f in inspect_project(p)['findings'])
    raw['storeys'].append({'id':'upper'});raw['furniture'][1]['storey_id']='upper'
    assert not inspect_project(BuildingProject.model_validate(raw))['findings']


def test_ambiguous_offline_search_and_metadata(tmp_path):
    store=NormStore(tmp_path)
    result=store.search(NormQuery(query='расстояние между перегородками'))
    assert result['state']=='insufficient_data' and not result['rules']
    result=store.search(NormQuery(query='офисы'))
    assert result['rules'] and all(r['verification']=='metadata_only' for r in result['rules'])
    assert store.index.exists()
    card=next(iter(store.cards.values()))
    assert evaluate(card,{},1000,'mm',date(2026,9,15))['state']=='insufficient_data'


def test_synthetic_rule_boundaries_dates_exceptions():
    # Synthetic evaluator fixture: never shipped as a regulatory rule.
    card=RuleCard(id='synthetic',document='TEST',edition='test',clause='1',title='test',source='https://example.org',checked_on=date(2026,1,1),effective_from=date(2026,2,1),effective_until=date(2027,1,1),verification='verified',source_sha256='0'*64,amendments_reviewed=True,conditions={'kind':'corridor'},measurement='width',minimum=1000,units='mm')
    def check(context,value,day=date(2026,9,15)):
        return evaluate(card,context,value,'mm',day)['state']
    assert check({},1100)=='insufficient_data'
    assert check({'kind':'room'},900)=='not_applicable'
    assert check({'kind':'corridor'},999)=='violated'
    assert check({'kind':'corridor'},1000)=='passed'
    assert check({'kind':'corridor'},1001)=='passed'
    assert check({'kind':'corridor'},1000,date(2026,1,1))=='not_applicable'
    assert check({'kind':'corridor'},1000,date(2027,1,1))=='not_applicable'
    card.exceptions=['unresolved exception']
    assert check({'kind':'corridor'},1000)=='insufficient_data'


def test_source_evidence_guard():
    with pytest.raises(ValueError,match='Verified rules require'):
        RuleCard(id='bad',document='x',edition='x',title='x',source='x',checked_on=date.today(),verification='verified')


def test_door_swing_blocked():
    raw=project().model_dump()
    raw['furniture']=[dict(id='chair',storey_id='level',catalog_id='office_chair',position=[1500,300],width=200,depth=200,height=800)]
    assert any(f['kind']=='door_swing_blocked' for f in inspect_project(BuildingProject.model_validate(raw))['findings'])
    raw['furniture'][0]['position']=[3000,2000]
    assert not inspect_project(BuildingProject.model_validate(raw))['findings']


def test_lock_conflict_and_invalid_mutation(tmp_path):
    store=ProjectStore(tmp_path);p=project();store.save(p,None)
    with store.exclusive():
        with pytest.raises(ValueError,match='busy'):ProjectStore(tmp_path).save(p,1)
    p.openings[0].width=99999
    with pytest.raises(ValueError,match='exceeds host'):store.save(p,1)
    assert store.load('office').revision==1
