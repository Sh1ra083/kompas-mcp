"""Real CAD acceptance. No simulator: every CAD operation runs in installed KOMPAS."""
import argparse,json,math,pathlib,sys,time,traceback
ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from kompas_mcp.worker import CadWorker

class Acceptance:
    def __init__(self,folder):
        self.folder=folder;self.worker=CadWorker(folder,120);self.results={};self.owned={}
    def call(self,action,**arguments):
        r=self.worker.call(action,arguments)
        if not r['ok']:raise AssertionError(r)
        data=r['data']
        def remember(value):
            if isinstance(value,dict):
                if 'document_id' in value:self.owned[value['document_id']]=True
                for child in value.values():remember(child)
            elif isinstance(value,list):
                for child in value:remember(child)
        remember(data)
        return data
    def doc(self,name,kind='part'):return self.call('document_create',kind=kind,name=name)['document_id']
    def sketch(self,d,name,primitives,**kw):return self.call('sketch_create',request=dict(document_id=d,name=name,primitives=primitives,**kw))['reference']
    def feature(self,d,name,kind,**kw):return self.call('feature_create',request=dict(document_id=d,name=name,operation=dict(kind=kind,**kw)))
    def find(self,d,kind='feature',**kw):return self.call('geometry_select',query=dict(document_id=d,kind=kind,**kw))['items']
    def ref(self,d,name):return self.find(d,name=name,require_unique=True)[0]['reference']
    def save(self,d,name):return self.call('document_save',document_id=d,path=name)['path']
    def rect(self,w,h,x=0,y=0):return [{'kind':'rectangle','width':w,'height':h,'origin':[x,y]}]
    def circle(self,r,x=0,y=0):return [{'kind':'circle','radius':r,'center':[x,y]}]
    def info(self,d):return self.call('document_info',document_id=d)
    def verify(self,d):
        info=self.info(d);assert info['feature_error']==0,info
        assert info.get('volume_mm3',0)>0,info
        return info
    def reopened(self,d,name):
        before=self.verify(d);path=self.save(d,name);self.call('document_close',document_id=d)
        copy=self.call('document_open_copy',source=path);after=self.verify(copy['document_id'])
        assert math.isclose(before['volume_mm3'],after['volume_mm3'],rel_tol=1e-7)
        return path
    def housing(self):
        d=self.doc('Корпус');s=self.sketch(d,'Основание',self.rect(100,80));self.feature(d,'Габарит','extrude',sketch=s,depth=40)
        assert math.isclose(self.verify(d)['volume_mm3'],320000,abs_tol=1e-5)
        face=self.find(d,'face',normal=[0,0,1],require_unique=True)[0]['reference'];self.feature(d,'Оболочка','shell',faces=[face],thickness=3)
        s=self.sketch(d,'Контур ребра',self.rect(3,60,48.5,10));self.feature(d,'Ребро жесткости','extrude',sketch=s,depth=20)
        primitives=[]
        for x in (15,85):
            for y in (15,65):primitives+=self.circle(3,x,y)
        s=self.sketch(d,'Отверстия',primitives);before=self.verify(d)['volume_mm3']
        self.feature(d,'Вырезы','extrude',sketch=s,depth=5,cut=True)
        assert math.isclose(before-self.verify(d)['volume_mm3'],4*math.pi*3**2*3,abs_tol=0.001)
        assert len(self.find(d,'face',geometry='cylinder',radius=3))==4
        edge=self.find(d,'edge',near=[50,0,40],require_unique=True)[0]['reference'];self.feature(d,'Скругление','fillet',edges=[edge],radius=1)
        initial=self.verify(d)['volume_mm3'];r=self.ref(d,'Габарит');self.call('feature_edit',reference=r,parameters={'depth':45});assert self.verify(d)['volume_mm3']>initial
        path=self.reopened(d,'housing.m3d');return {'file':path,'checks':['volume','shell','rib','four_holes','fillet','parameter_rebuild','reopen']}
    def shaft(self):
        d=self.doc('Ступенчатый вал')
        s=self.sketch(d,'Диаметр 30',self.circle(15));self.feature(d,'Ступень 1','extrude',sketch=s,depth=30)
        s=self.sketch(d,'Диаметр 20',self.circle(10),offset=30);self.feature(d,'Ступень 2','extrude',sketch=s,depth=40)
        expected=math.pi*(225*30+100*40);assert math.isclose(self.verify(d)['volume_mm3'],expected,rel_tol=1e-7)
        vars=self.call('variables_list',document_id=d,feature=self.ref(d,'Ступень 2'));assert vars['variables']
        self.call('variable_set',document_id=d,name='L',value=45)
        vs=self.call('variables_list',document_id=d,feature=self.ref(d,'Ступень 2'))['variables']
        depth=[v for v in vs if math.isclose(v['value'],40)]
        assert depth,vs
        self.call('feature_bind',reference=self.ref(d,'Ступень 2'),variable_name=depth[0]['name'],expression='L')
        assert math.isclose(self.verify(d)['bounds_mm'][5],75,abs_tol=1e-6)
        self.call('variable_set',document_id=d,name='L',value=50)
        assert math.isclose(self.verify(d)['bounds_mm'][5],80,abs_tol=1e-6)
        return {'file':self.reopened(d,'shaft.m3d'),'checks':['analytic_volume','native_variable_expression','reopen']}
    def sheet(self):
        d=self.doc('Листовой кронштейн');s=self.sketch(d,'Контур',self.rect(80,60));self.feature(d,'Лист','sheet',sketch=s,thickness=2)
        edge=self.find(d,'edge',near=[40,0,2],require_unique=True)[0]['reference'];self.feature(d,'Сгиб','bend',edges=[edge],length=20,radius=2)
        folded=self.verify(d);assert folded['bounds_mm'][5]>20;self.save(d,'sheet_folded.m3d')
        face=self.find(d,'face',near=[40,30,2],normal=[0,0,1],require_unique=True)[0]['reference'];self.feature(d,'Развертка','unfold',fixed_face=face,bends=[self.ref(d,'Сгиб')])
        unfolded=self.verify(d);assert math.isclose(unfolded['bounds_mm'][5]-unfolded['bounds_mm'][2],2,abs_tol=1e-6)
        assert unfolded['bounds_mm'][1]<-20
        return {'file':self.reopened(d,'sheet_flat.m3d'),'checks':['native_sheet','bend','flat_thickness','bend_allowance','reopen']}
    def surface(self):
        d=self.doc('Криволинейный переход')
        for name,r,z,x in [('A',20,0,0),('B',15,25,6),('C',12,50,0)]:self.sketch(d,name,self.circle(r,x),offset=z)
        self.feature(d,'Поверхность','surface_loft',sketches=[self.ref(d,n) for n in ('A','B','C')])
        bounds=self.info(d)['bounds_mm'];assert math.isclose(bounds[5],50,abs_tol=1e-6),bounds
        faces=self.find(d,'face');assert faces
        self.feature(d,'Толщина','thicken',surfaces=[f['reference'] for f in faces],thickness=2)
        return {'file':self.reopened(d,'surface.m3d'),'checks':['three_offset_sections','noncoaxial_loft','thickening','reopen']}
    def assembly(self):
        a=self.doc('Узел',kind='assembly')
        for name,size,pos in [('Корпус',[50,40,10],[0,0,0]),('Вал',[10,10,30],[20,15,10]),('Втулка',[18,18,8],[16,11,35]),('Крепёж',[5,5,10],[5,5,10])]:
            d=self.doc(name)
            profile=self.rect(*size[:2]) if name=='Корпус' else self.circle(size[0]/2)
            if name=='Втулка':profile+=self.circle(5.2)
            s=self.sketch(d,'Контур',profile);self.feature(d,'Тело','extrude',sketch=s,depth=size[2]);path=self.save(d,name+'.m3d')
            self.call('component_insert',document_id=a,source=path,name=name,origin=pos,fixed=name=='Корпус')
        cs=self.call('component_list',document_id=a)['components'];assert len(cs)==4
        fa=self.find(a,'face',component=cs[0]['reference'],normal=[0,0,1],require_unique=True)[0]['reference']
        fb=self.find(a,'face',component=cs[1]['reference'],normal=[0,0,-1],require_unique=True)[0]['reference']
        self.call('mate_create',document_id=a,first=fa,second=fb,kind='coincident',name='Опора вала')
        replacement=self.doc('Запасной крепеж');s=self.sketch(replacement,'Контур',self.circle(2.5));self.feature(replacement,'Тело','extrude',sketch=s,depth=12);replacement_path=self.save(replacement,'replacement.m3d')
        fresh=self.call('component_list',document_id=a)['components'];self.call('component_replace',reference=fresh[3]['reference'],source=replacement_path)
        path=self.save(a,'assembly.a3d');sp=self.call('specification_create',assembly_document_id=a);assert len(sp['rows'])==4;self.save(sp['document']['document_id'],'assembly.spw')
        snapshot=self.call('checkpoint',document_id=a);copy=self.call('checkpoint_restore',path=snapshot['path'])['document'];assert copy['component_count']==4
        return {'file':path,'checks':['four_components','mate','replacement','specification','dependency_checkpoint_restore'],'intersections':self.call('assembly_intersections',document_id=a)}
    def drawing(self):
        d=self.doc('Пластина');s=self.sketch(d,'Контур',self.rect(60,40));self.feature(d,'Толщина','extrude',sketch=s,depth=10);self.save(d,'drawing_source.m3d')
        dr=self.doc('Чертеж','drawing');views=self.call('drawing_views',document_id=dr,source_document_id=d,origin=[75,190],spacing=[35,35]);assert len(views['views'])==4
        dim=self.call('drawing_dimension',document_id=dr,view_number=1,first=[0,0],second=[60,0],position=[0,-15]);assert dim['associated']
        self.call('drawing_stamp',document_id=dr,fields={'1':'Пластина','2':'MCP.001'})
        self.save(dr,'drawing.cdw')
        exports=[self.call('document_export',document_id=dr,path='drawing'+ext) for ext in ('.pdf','.dxf')]
        preview=self.call('preview',document_id=dr,path='drawing.png')
        self.call('feature_edit',reference=self.ref(d,'Толщина'),parameters={'depth':15});self.call('document_save',document_id=d)
        self.call('drawing_rebuild',document_id=dr);self.call('document_save',document_id=dr)
        return {'file':str(self.folder/'drawing.cdw'),'exports':exports,'preview':preview,'checks':['three_views','associative_dimension','stamp','pdf','dxf','model_update']}
    def negative(self):
        d=self.doc('Negative');s=self.sketch(d,'Open',[{'kind':'line','start':[0,0],'end':[20,0]}]);r=self.worker.call('feature_create',{'request':{'document_id':d,'name':'Invalid','operation':{'kind':'extrude','sketch':s,'depth':5}}});assert not r['ok']
        d=self.doc('Stale');s=self.sketch(d,'Base',self.rect(20,20));self.feature(d,'Block','extrude',sketch=s,depth=10)
        r=self.worker.call('feature_create',{'request':{'document_id':d,'name':'Stale','operation':{'kind':'extrude','sketch':s,'depth':5}}});assert r['error']['code']=='stale_reference'
        r=self.worker.call('geometry_select',{'query':{'document_id':d,'kind':'face','require_unique':True}});assert r['error']['code']=='ambiguous_selection'
        edge=self.find(d,'edge')[0]['reference'];r=self.worker.call('feature_create',{'request':{'document_id':d,'name':'Huge fillet','operation':{'kind':'fillet','edges':[edge],'radius':1000}}});assert not r['ok']
        sc={'steps':[{'name':'bad','action':'unknown','arguments':{}},{'name':'later','action':'document_create','arguments':{}}]};r=self.worker.call('scenario_run',{'scenario':sc});assert not r['ok']
        closed=self.doc('Closed');self.call('document_close',document_id=closed,discard_changes=True)
        assert not self.worker.call('document_info',{'document_id':closed})['ok']
        conflict=self.doc('Conflict');r=self.worker.call('sketch_create',{'request':{'document_id':conflict,'name':'Conflicting','primitives':[{'kind':'line','start':[0,0],'end':[20,0]}],'constraints':[{'kind':'horizontal','entity':0},{'kind':'vertical','entity':0},{'kind':'length','entity':0,'value':20}]}})
        assert not r['ok'],r
        sc={'steps':[{'name':'document','action':'document_create','arguments':{'name':'Interrupted'}},{'name':'bad','action':'feature_create','depends_on':['document'],'arguments':{'request':{'document_id':'$document.document_id','name':'Missing sketch','operation':{'kind':'extrude','sketch':{'document_id':'$document.document_id','object_id':'missing','revision':1},'depth':10}}}},{'name':'later','action':'document_create','arguments':{'name':'Must not exist'}}]}
        r=self.worker.call('scenario_run',{'scenario':sc});assert r['error']['code']=='scenario_failed'
        details=r['error']['details'];assert details['checkpoints'] and not details['restored'] and 'later' not in details['completed']
        return {'checks':['open_profile_rejected','stale_reference','ambiguous_face','impossible_fillet','invalid_scenario','closed_document','constraint_conflict','interrupted_batch_checkpoint']}
    def run(self,names):
        for name in names:
            started=time.time()
            try:self.results[name]={'passed':True,'details':getattr(self,name)()}
            except Exception as e:self.results[name]={'passed':False,'error':str(e),'traceback':traceback.format_exc()}
            finally:
                # Only documents returned by this test's worker, never unrelated user tabs.
                try:
                    connected=self.worker.call('connect',{})
                    if connected['ok']:self.owned.update(dict.fromkeys(connected['data']['owned_documents'],True))
                except Exception:pass
                for token in reversed(self.owned):
                    try:self.worker.call('document_close',{'document_id':token,'discard_changes':True})
                    except Exception:pass
                self.owned.clear()
            self.results[name]['seconds']=time.time()-started
            print(name,'PASS' if self.results[name]['passed'] else 'FAIL '+self.results[name]['error'],flush=True)
            (self.folder/'acceptance.json').write_text(json.dumps(self.results,ensure_ascii=False,indent=2),encoding='utf-8')
        self.worker.close()
        return all(r['passed'] for r in self.results.values())

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('cases',nargs='*',default=['housing','shaft','sheet','surface','assembly','drawing','negative']);parser.add_argument('--output',type=pathlib.Path,required=True);args=parser.parse_args()
    sys.exit(0 if Acceptance(args.output.resolve()).run(args.cases) else 1)
