import pathlib,sys,math,argparse
sys.path.insert(0,str(pathlib.Path(__file__).parent))
from acceptance import Acceptance

class Extended(Acceptance):
    def convenience(self):
        d=self.doc('Convenience');s=self.sketch(d,'Base',self.rect(20,20));self.feature(d,'Block','extrude',sketch=s,depth=10)
        face=self.find(d,'face',normal=[0,0,1],require_unique=True)[0]['reference']
        assert len(self.find(d,'edge',adjacent_to=face))==4
        assert len(self.find(d,'face',adjacent_to=face))==4
        self.feature(d,'Rotated CS','coordinate_system',origin=[10,20,30],euler_angles=[90,0,0])
        path=self.save(d,'component.m3d');a=self.doc('Pattern','assembly');part=self.call('component_insert',document_id=a,source=path,name='Cube',fixed=True)
        self.call('component_pattern',reference=part['reference'],count=3,step=[30,0,0]);assert len(self.call('component_list',document_id=a)['components'])==3
        self.save(a,'pattern.a3d')
        spec=self.call('specification_create',assembly_document_id=a);assert len(spec['rows'])==1 and spec['rows'][0]['quantity']==3
        self.call('component_insert',document_id=a,source=path,name='Fourth',origin=[90,0,0],fixed=True);self.call('document_save',document_id=a)
        updated=self.call('specification_update',assembly_document_id=a,specification_document_id=spec['document']['document_id']);assert len(updated['rows'])==1 and updated['rows'][0]['quantity']==4
        self.save(spec['document']['document_id'],'pattern.spw')
        dr=self.call('drawing_create',source_document_id=a,scale=0.5)['document']['document_id']
        self.call('drawing_position',document_id=dr,view_number=1,point=[10,10],shelf=[-10,30],position=1)
        self.save(dr,'positions.cdw');self.call('preview',document_id=dr,path='positions.png')
        return {'checks':['adjacent_faces','adjacent_edges','rotated_coordinate_system','three_component_instances','position_leader','grouped_specification','updated_quantity']}
    def datums(self):
        d=self.doc('Datums');s=self.sketch(d,'Base',self.rect(20,20));self.feature(d,'Block','extrude',sketch=s,depth=20)
        edge=self.find(d,'edge',near=[10,0,20],require_unique=True)[0]['reference'];self.feature(d,'Axis','axis_edge',edge=edge)
        self.feature(d,'CS','coordinate_system',origin=[10,20,30]);return self.save(d,'datums.m3d')
    def trim(self):
        d=self.doc('Trimmed')
        for name,z in [('A',0),('B',40)]:self.sketch(d,name,self.circle(20),offset=z)
        self.feature(d,'Surface','surface_loft',sketches=[self.ref(d,n) for n in ('A','B')])
        self.feature(d,'Cutting plane','plane_offset',offset=20)
        body=self.find(d,'body',require_unique=True)[0]['reference']
        self.feature(d,'Trim','surface_trim',surface=body,cutter=self.ref(d,'Cutting plane'))
        return self.save(d,'trimmed.m3d')
    def boolean(self):
        out=[]
        for mode,expected in [('union',6000),('subtract',2000),('intersect',2000)]:
            d=self.doc(mode)
            for name,x in [('A',0),('B',10)]:
                s=self.sketch(d,name,self.rect(20,20,x));self.feature(d,name+'body','extrude',sketch=s,depth=10,new_body=True)
            bodies=self.find(d,'body');assert len(bodies)==2
            self.feature(d,mode,'boolean',base=bodies[0]['reference'],bodies=[bodies[1]['reference']],mode=mode)
            assert math.isclose(self.verify(d)['volume_mm3'],expected,abs_tol=1e-5)
            out.append(self.reopened(d,mode+'.m3d'))
        return out
    def sew(self):
        d=self.doc('Sewn')
        for name,z in [('A',0),('B',20),('C',40)]:self.sketch(d,name,self.circle(20),offset=z)
        self.feature(d,'First','surface_loft',sketches=[self.ref(d,n) for n in ('A','B')])
        self.feature(d,'Second','surface_loft',sketches=[self.ref(d,n) for n in ('B','C')])
        bodies=self.find(d,'body');assert len(bodies)==2
        self.feature(d,'Sewn','sew',surfaces=[b['reference'] for b in bodies])
        return self.save(d,'sewn.m3d')
    def rib(self):
        d=self.doc('Rib');s=self.sketch(d,'Base',self.rect(80,60,y=-30));self.feature(d,'Plate','extrude',sketch=s,depth=5)
        s=self.sketch(d,'Wall profile',self.rect(5,60,x=60,y=-30));self.feature(d,'Wall','extrude',sketch=s,depth=35)
        s=self.sketch(d,'Rib profile',[{'kind':'line','start':[10,-5],'end':[60,-30]}],plane='XZ')
        self.feature(d,'Rib','rib',sketch=s,thickness=3)
        assert math.isclose(self.verify(d)['volume_mm3'],24000+9000+50*25*3/2,abs_tol=1e-5)
        return self.reopened(d,'rib.m3d')
    def section(self):
        d=self.doc('Source');s=self.sketch(d,'Base',self.rect(60,40));self.feature(d,'Plate','extrude',sketch=s,depth=10);self.save(d,'section_source.m3d')
        dr=self.doc('Section','drawing');self.call('drawing_views',document_id=dr,source_document_id=d)
        result=self.call('drawing_section',document_id=dr,base_view_number=1,start=[30,-10],end=[30,50],position=[120,80])
        self.save(dr,'section.cdw');return result
    def exchange(self):
        d=self.doc('Export');s=self.sketch(d,'Base',self.circle(10));self.feature(d,'Cylinder','extrude',sketch=s,depth=30);before=self.verify(d);self.save(d,'exchange.m3d')
        step=self.call('document_export',document_id=d,path='exchange.step');self.call('document_export',document_id=d,path='exchange.stl')
        imported=self.call('document_import',source=step['path']);assert math.isclose(imported['volume_mm3'],before['volume_mm3'],rel_tol=1e-5)
        return self.reopened(imported['document_id'],'imported.m3d')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('cases',nargs='*',default=['boolean','sew','rib','section','exchange']);p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();sys.exit(0 if Extended(a.output.resolve()).run(a.cases) else 1)
