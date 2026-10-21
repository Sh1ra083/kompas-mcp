"""Windows COM adapter, used exclusively by the STA worker."""
import hashlib
import json
import math
import re
import shutil
import uuid
from pathlib import Path
import pythoncom
import win32com.client as com
from pydantic import ValidationError
from .errors import CadError
from .models import SketchRequest, FeatureRequest, Selector, Scenario, ModelCheck
from .advanced import AdvancedOperations, ADVANCED_ACTIONS

def objects(value):
    if value is None: return []
    return list(value) if isinstance(value,(tuple,list)) else [value]

def xyz(value):
    return list(value[-3:])

class KompasBackend(AdvancedOperations):
    def __init__(self, root: Path):
        self.root=root.resolve()
        self.app=None
        self.api=None
        self.constants={}
        self.documents={}
        self.registry={}
        self.evidence={}
        self.schemas=json.loads((Path(__file__).parent/'schemas.json').read_text(encoding='utf-8'))
        self._validators={}
        self.actions={name:getattr(self,name) for name in (
            'connect','document_create','document_open_copy','document_save','document_close',
            'document_info','model_check','sketch_create','feature_create','geometry_select','feature_edit',
            'feature_suppress','feature_delete','document_rebuild','checkpoint','scenario_run','scenario_run_file',
            'component_insert','component_place','component_replace','assembly_intersections',
            'drawing_create','drawing_rebuild','document_export','capabilities','measure')}
        self.actions.update({name:getattr(self,name) for name in ADVANCED_ACTIONS})
        self.actions['checkpoint_restore']=self.checkpoint_restore
        self.actions['building_preview']=self.building_preview
        self.actions.update({name:getattr(self,name) for name in ('drawing_layout_check','drawing_fit','drawing_sheet_set','assembly_compose')})

    def building_preview(self, project_id):
        from .building_cad import preview
        return preview(self,project_id)

    def execute(self, action, arguments):
        if action not in self.actions: raise CadError('unknown_action',action)
        try:arguments=json.loads(json.dumps(arguments,allow_nan=False))
        except (ValueError,TypeError) as e:raise CadError('invalid_parameters','Arguments must be finite JSON values') from e
        import jsonschema
        try:
            if action not in self._validators:
                cls=jsonschema.validators.validator_for(self.schemas[action])
                cls.check_schema(self.schemas[action])
                self._validators[action]=cls(self.schemas[action])
            self._validators[action].validate(arguments)
        except jsonschema.ValidationError as e:raise CadError('invalid_parameters',e.message) from e
        try: return self.actions[action](**arguments)
        except ValidationError as e: raise CadError('invalid_parameters',str(e)) from e
        except Exception:
            if action not in ('connect','document_info','geometry_select','capabilities','variables_list','component_list','measure','assembly_intersections'):
                for token in self.documents:
                    try:self.sync(token,True)
                    except pythoncom.com_error:pass
            raise

    def connect(self):
        if self.app is None:
            import win32com
            import win32com.gen_py
            cache=self.root/'.com-cache';cache.mkdir(parents=True,exist_ok=True)
            win32com.__gen_path__=str(cache)
            win32com.gen_py.__path__=[str(cache)]
            com.gencache.is_readonly=False
            com.gencache.is_zip=False
            com.gencache.GetGeneratePath()
            com.gencache.Rebuild(verbose=0)
            import winreg
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,r'KOMPAS.Application.7\CLSID') as k:
                clsid=winreg.QueryValue(k,None)
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,'CLSID\\'+clsid+r'\LocalServer32') as k:
                exe=winreg.QueryValue(k,None).strip('"')
            bindir=Path(exe).parent
            self.bindir=bindir
            for file in ('kAPI7.tlb','ksConstants.tlb','ksConstants3D.tlb'):
                attr=pythoncom.LoadTypeLib(str(bindir/file)).GetLibAttr()
                module=com.gencache.EnsureModule(attr[0],attr[1],attr[3],attr[4])
                if file=='kAPI7.tlb': self.api=module
                else: self.constants.update(vars(module.constants))
            self.app=com.Dispatch('KOMPAS.Application.7')
            self.app.Visible=True
            from .legacy import LegacyMass
            self.legacy_mass=LegacyMass(bindir,self.constants)
        return {'application':self.app.ApplicationName(True),'version':self.app.GetSystemVersion(),
                'documents_open':self.app.Documents.Count,'owned_documents':list(self.documents)}

    def c(self,name):
        if name not in self.constants: raise CadError('sdk_mismatch',f'Constant unavailable: {name}')
        return self.constants[name]

    def cast(self,obj,interface):
        klass=getattr(self.api,interface)
        return klass(obj._oleobj_.QueryInterface(klass.CLSID,pythoncom.IID_IDispatch))

    def release(self):
        self.registry.clear(); self.documents.clear(); self.app=None

    def path(self,value,extension=None):
        p=Path(value)
        if not p.is_absolute(): p=self.root/p
        p=p.resolve()
        if not p.is_relative_to(self.root): raise CadError('path_outside_workspace','Output path must be inside server workspace')
        if extension and p.suffix.lower()!=extension: raise CadError('invalid_parameters',f'Expected extension {extension}')
        p.parent.mkdir(parents=True,exist_ok=True)
        return p

    def adopt(self,doc):
        token=uuid.uuid4().hex
        self.documents[token]={'com':doc,'revision':0,'stamp':None}
        self.sync(token)
        return token

    def doc(self,token):
        if token not in self.documents: raise CadError('document_not_owned','Unknown document; create or open a working copy')
        state=self.documents[token]
        try: _=state['com'].Name
        except pythoncom.com_error as e: raise CadError('document_closed','Document is no longer open') from e
        self.sync(token)
        return state

    def sync(self,token,bump=False):
        state=self.documents[token]; doc=state['com']
        if doc.DocumentType in (4,5):
            part=self.cast(doc,'IKompasDocument3D').TopPart
            stamp=self.cast(part,'IFeature7').UpdateStamp
        else: stamp=None
        if bump or (state['stamp'] is not None and stamp!=state['stamp']): state['revision']+=1
        state['stamp']=stamp

    def part(self,token):
        d=self.doc(token)['com']
        if d.DocumentType not in (4,5): raise CadError('wrong_document_type','A 3D document is required')
        return self.cast(d,'IKompasDocument3D').TopPart

    def register(self,token,obj,kind='feature'):
        key=uuid.uuid4().hex
        self.registry[key]={'document':token,'com':obj,'kind':kind}
        return {'document_id':token,'object_id':key,'revision':self.documents[token]['revision']}

    def resolve(self,ref,token=None):
        if hasattr(ref,'model_dump'): ref=ref.model_dump()
        token=token or ref['document_id']
        if token!=ref['document_id']: raise CadError('cross_document_reference','Reference belongs to another document')
        state=self.doc(token)
        if ref['revision']!=state['revision']: raise CadError('stale_reference','Re-select the object after document changes',current_revision=state['revision'])
        item=self.registry.get(ref['object_id'])
        if not item or item['document']!=token: raise CadError('unknown_reference','Object reference is unavailable')
        return item['com']

    def document_create(self,kind='part',name='Model'):
        self.connect()
        types={'part':4,'assembly':5,'drawing':1,'specification':3}
        if kind not in types: raise CadError('invalid_parameters','Unknown document kind')
        doc=self.app.Documents.Add(types[kind],True)
        if doc is None: raise CadError('creation_failed','KOMPAS did not create a document')
        token=self.adopt(doc)
        if kind in ('part','assembly'):
            p=self.part(token); p.Name=name; p.Update(); self.sync(token,True)
            self.cast(doc,'IKompasDocument3D1').ViewProjectionManager.ViewProjectionScheme=self.c('ksVPSZAxonometric')
            for index in (1,2,3):
                default_plane=p.DefaultObject(index);default_plane.Hidden=True;default_plane.Update()
        return self.document_info(token)

    def document_open_copy(self,source):
        self.connect()
        src=Path(source).resolve(strict=True)
        folder=self.root/'copies'/uuid.uuid4().hex
        folder.mkdir(parents=True)
        target=self._copy_package(src,folder)
        doc=self.app.Documents.Open(str(target),True,False)
        if doc is None: raise CadError('open_failed','Unable to open working copy')
        token=self.adopt(doc)
        return self.document_info(token)

    def _copy_package(self,source,folder):
        mapping={};order=[];missing=[]
        def discover(src):
            src=src.resolve()
            if src in mapping:return
            mapping[src]=folder/(hashlib.sha256(str(src).lower().encode()).hexdigest()[:16]+src.suffix.lower())
            existing={item.Reference for i in range(self.app.Documents.Count) if (item:=self.app.Documents.Item(i)) is not None}
            d=self.app.Documents.Open(str(src),False,True)
            if d is None:raise CadError('open_failed','Cannot inspect dependency',path=str(src))
            try:
                names=objects(self.cast(d,'IKompasDocument1').ExternalFilesNames(True))
                for name in names:
                    dep=Path(name)
                    if not dep.is_absolute():dep=src.parent/dep
                    if dep.suffix.lower() not in ('.m3d','.a3d','.cdw','.spw','.frw'):continue
                    if dep.is_file():discover(dep)
                    else:missing.append(str(dep))
            finally:
                if d.Reference not in existing:d.Close(self.c('kdDoNotSaveChanges'))
            order.append(src)
        discover(source)
        if missing:raise CadError('missing_dependencies','Cannot make complete working package',paths=missing)
        for src,dest in mapping.items():shutil.copy2(src,dest)
        for src in order:
            d=self.app.Documents.Open(str(mapping[src]),False,False)
            if d is None:raise CadError('open_failed','Cannot relink copied dependency',path=str(mapping[src]),source=str(src))
            try:
                d1=self.cast(d,'IKompasDocument1')
                for old,new in mapping.items():d1.ReplaceExternalFilesNames(False,str(old),str(new))
                d.Save()
            finally:d.Close(self.c('kdDoNotSaveChanges'))
        (folder/'manifest.json').write_text(json.dumps({str(k):str(v) for k,v in mapping.items()},indent=2),encoding='utf-8')
        return mapping[source.resolve()]

    def document_save(self,document_id,path=None):
        doc=self.doc(document_id)['com']
        if path:
            ext={4:'.m3d',5:'.a3d',1:'.cdw',2:'.frw',3:'.spw'}[doc.DocumentType]
            p=self.path(path,ext)
            if p.exists() and str(p).lower()!=str(doc.PathName).lower():
                raise CadError('file_exists','Choose a new file name')
            doc.SaveAs(str(p))
        else:
            if not doc.PathName: raise CadError('path_required','First save needs a path')
            p=self.path(doc.PathName)
            doc.Save()
        if not p.is_file() or p.stat().st_size==0: raise CadError('save_failed','Saved file was not produced')
        return {'document_id':document_id,'path':str(p),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

    def document_close(self,document_id,discard_changes=False):
        state=self.doc(document_id)
        if state['com'].Changed and not discard_changes: raise CadError('unsaved_changes','Save the working document or explicitly discard changes before closing')
        state['com'].Close(self.c('kdDoNotSaveChanges'))
        del self.documents[document_id]
        self.registry={k:v for k,v in self.registry.items() if v['document']!=document_id}
        return {'closed':document_id}

    def document_info(self,document_id):
        state=self.doc(document_id); d=state['com']
        result={'document_id':document_id,'revision':state['revision'],'name':d.Name,'path':d.PathName,'type':d.DocumentType,'changed':bool(d.Changed)}
        if d.DocumentType in (4,5):
            p=self.part(document_id); f=self.cast(p,'IFeature7')
            bounds=p.GetGabarit(True,True)
            raw_mass=p.Mass
            result.update(bounds_mm=list(bounds[1:]) if bounds[0] and all(abs(v)<1e100 for v in bounds[1:]) else None,component_count=p.Parts.Count,
                          body_count=len(objects(f.ResultBodies)),mass_kg=raw_mass/1000,feature_error=f.ObjectError)
            if result['body_count'] or result['component_count']:
                result.update(self.legacy_mass.calculate(d))
        return result

    def _primitive(self,dc,item):
        kind=item.kind
        if kind=='polygon':
            from .models import Line
            return [self._primitive(dc,Line(kind='line',start=a,end=b))[0] for a,b in zip(item.points,item.points[1:]+item.points[:1])]
        if kind=='rectangle':
            from .models import Line
            x,y=item.origin; w,h=item.width,item.height
            points=[(x,y),(x+w,y),(x+w,y+h),(x,y+h)]
            return [self._primitive(dc,Line(kind='line',start=points[i],end=points[(i+1)%4]))[0] for i in range(4)]
        if kind=='line':
            o=dc.LineSegments.Add(); o.X1,o.Y1=item.start; o.X2,o.Y2=item.end; o.Style=3 if item.construction else 1
        elif kind=='circle':
            o=dc.Circles.Add(); o.Xc,o.Yc=item.center; o.Radius=item.radius; o.Style=1
        elif kind=='arc':
            o=dc.Arcs.Add(); o.Xc,o.Yc=item.center; o.Radius=item.radius
            o.Angle1=item.start_angle; o.Angle2=item.end_angle; o.Direction=not item.counterclockwise; o.Style=1
        elif kind=='spline':
            o=dc.NurbsesByPoints.Add(); o.Degree=min(3,len(item.points)-1);o.Closed=item.closed;o.Style=1
            for i,(x,y) in enumerate(item.points): o.AddPoint(i,x,y,1.0)
        if not o.Update(): raise CadError('geometry_failed',f'Cannot create {kind}')
        return [o]

    def sketch_create(self,request):
        req=SketchRequest.model_validate(request); token=req.document_id; p=self.part(token)
        plane=p.DefaultObject({'XY':1,'XZ':2,'YZ':3}[req.plane]) if isinstance(req.plane,str) else self.resolve(req.plane,token)
        projections=[self.resolve(r,token) for r in req.projections]
        if req.offset:
            offset_plane=self.cast(self.cast(p,'IAuxiliaryGeomContainer').Planes3D.Add(self.c('o3d_planeOffset')),'IPlane3DByOffset')
            offset_plane.Name=req.name+' plane';offset_plane.BasePlane=plane;offset_plane.Offset=abs(req.offset);offset_plane.Direction=req.offset>=0
            offset_plane.Hidden=True
            if not offset_plane.Update():raise CadError('plane_failed','Offset plane failed')
            plane=offset_plane
        s=self.cast(p,'IModelContainer').Sketchs.Add(); s.Name=req.name;s.Plane=plane
        if not s.Update(): raise CadError('sketch_failed','Cannot create sketch on selected plane')
        try:
            sd=s.BeginEdit()
            if sd is None: raise CadError('sketch_edit_failed','Cannot enter sketch')
            try:
                view=self.cast(sd,'IKompasDocument2D').ViewsAndLayersManager.Views.ActiveView
                dc=self.cast(view,'IDrawingContainer')
                geometry=[]
                for primitive in req.primitives: geometry.extend(self._primitive(dc,primitive))
                names={'horizontal':'ksCHorizontal','vertical':'ksCVertical','coincident':'ksCMergePoints','parallel':'ksCParallel',
                       'perpendicular':'ksCPerpendicular','equal_length':'ksCEqualLength','equal_radius':'ksCEqualRadius',
                       'tangent':'ksCTangentTwoCurves','concentric':'ksCConcentricity','fixed_point':'ksCFixedPoint','length':'ksCFixedLenght','angle':'ksCFixedAngle'}
                for constraint in req.constraints:
                    if constraint.entity>=len(geometry) or (constraint.partner is not None and constraint.partner>=len(geometry)):
                        raise CadError('invalid_parameters','Constraint geometry index outside range')
                    c=self.cast(geometry[constraint.entity],'IDrawingObject1').NewConstraint();c.ConstraintType=self.c(names[constraint.kind]);c.Index=constraint.point_index
                    if constraint.partner is not None:c.Partner=geometry[constraint.partner];c.PartnerIndex=constraint.partner_point_index
                    if constraint.value is not None:c.Value=constraint.value
                    if constraint.variable:c.Variable=constraint.variable
                    if not c.Create():raise CadError('constraint_conflict','KOMPAS rejected sketch constraint',constraint=constraint.model_dump())
            finally: s.EndEdit()
            for obj in projections:
                if not s.AddProjectionOf(obj): raise CadError('projection_failed','Projection could not be created')
        finally: self.sync(token,True)
        return {'reference':self.register(token,s),'name':s.Name,'constraints_state':s.ConstraintsState,'valid':bool(s.Valid)}

    def feature_create(self,request):
        req=FeatureRequest.model_validate(request); token=req.document_id; p=self.part(token); op=req.operation
        before_volume=self.document_info(token).get('volume_mm3',0) if op.kind in ('extrude','revolve','sweep') and op.cut else None
        mc=self.cast(p,'IModelContainer'); r=lambda ref:self.resolve(ref,token)
        refs=lambda values:tuple(r(x) for x in values)
        plane=lambda value:p.DefaultObject({'XY':1,'XZ':2,'YZ':3}[value]) if isinstance(value,str) else r(value)
        axis=lambda value:p.DefaultObject({'X':71,'Y':72,'Z':73}[value]) if isinstance(value,str) else r(value)
        if op.kind=='thread':return self._thread_create(req)
        if op.kind=='axis_edge':
            edge=r(op.edge);o=self.cast(self.cast(p,'IAuxiliaryGeomContainer').Axes3D.Add(self.c('o3d_axisEdge')),'IAxis3DByEdge');o.Edge=edge
        elif op.kind=='coordinate_system':
            o=self.cast(p,'IAuxiliaryGeomContainer').LocalCoordinateSystems.Add();o.X,o.Y,o.Z=op.origin
            o.OrientationType=self.c('ksEulerCorners')
            angles=self.cast(o.LocalCSParameters,'ILocalCSEulerParam');angles.PrecessionAngle,angles.NutationAngle,angles.RotationAngle=op.euler_angles
        elif op.kind=='extrude':
            if op.cut and op.new_body:raise CadError('invalid_parameters','A cut cannot create a new body')
            sketch=r(op.sketch)
            o=mc.Extrusions.Add(self.c('o3d_cutExtrusion' if op.cut else 'o3d_bossExtrusion'))
            # KOMPAS cut operations invert the default extrusion direction.
            # Public contract is uniform: reverse=False follows the sketch normal.
            cad_reverse=op.reverse != op.cut
            o.Sketch=sketch;o.Direction=self.c('dtReverse' if cad_reverse else 'dtNormal')
            o.SetSideParameters(not cad_reverse,self.c('etBlind'),op.depth,op.draft,False,None)
            if op.new_body:self.cast(o,'IExtrusion1').OperationResult=self.c('ksOperationNewBody')
        elif op.kind=='revolve':
            sketch=r(op.sketch);axis=p.DefaultObject({'X':71,'Y':72,'Z':73}[op.axis]) if isinstance(op.axis,str) else r(op.axis)
            o=mc.Rotateds.Add(self.c('o3d_cutRotated' if op.cut else 'o3d_bossRotated'))
            o.Profile=sketch;o.Axis=axis;o.Direction=0;o.SetAngle(True,op.angle)
        elif op.kind in ('loft','surface_loft'):
            sketches=refs(op.sketches);guides=refs(op.guides)
            collection=mc.Lofts if op.kind=='loft' else self.cast(p,'ISurfaceContainer').LoftSurfaces
            o=collection.Add(self.c('o3d_bossLoft' if op.kind=='loft' else 'o3d_LoftSurface'));o.Sketchs=sketches;o.Closed=op.closed
            if guides:o.DirectionalLines=guides
        elif op.kind in ('sweep','surface_sweep'):
            sketch=r(op.sketch);path=refs(op.path)
            collection=mc.Evolutions if op.kind=='sweep' else self.cast(p,'ISurfaceContainer').EvolutionSurfaces
            o=collection.Add(self.c(('o3d_cutEvolution' if op.cut else 'o3d_bossEvolution') if op.kind=='sweep' else 'o3d_EvolutionSurface'));o.Sketch=sketch;o.Edges=path
        elif op.kind=='fillet':
            edges=refs(op.edges);o=mc.Fillets.Add();o.BaseObjects=edges;o.Radius1=op.radius;o.Tangent=op.tangent
        elif op.kind=='chamfer':
            edges=refs(op.edges);o=mc.Chamfers.Add();o.BaseObjects=edges;o.Distance1=op.distance;o.Angle=op.angle
        elif op.kind=='shell':
            faces=refs(op.faces);o=mc.Shells.Add();o.DeletedFaces=faces;o.Thickness=op.thickness;o.ThinType=self.c('dtNormal' if op.outward else 'dtReverse')
        elif op.kind=='thicken':
            faces=refs(op.surfaces);o=mc.SurfaceThickenings.Add();o.Faces=faces
            thin=self.cast(o,'IThinParameters');thin.SetThinParameters(True,self.c('dtNormal'),op.thickness,0)
        elif op.kind=='sew':
            surfaces=refs(op.surfaces);shells=[]
            for surface in surfaces:
                try:body=self.cast(surface,'IBody7')
                except pythoncom.com_error:body=self.cast(surface,'IFace').Body
                if not any(body.BodyId==b.BodyId for b in shells):shells.append(body)
            o=self.cast(p,'ISurfaceContainer').SurfaceSewers.Add();o.Shells=com.VARIANT(pythoncom.VT_ARRAY|pythoncom.VT_DISPATCH,tuple(shells));o.CreateBody=op.solid;o.Precision=0.001
        elif op.kind=='surface_offset':
            surfaces=refs(op.surfaces);o=self.cast(p,'ISurfaceContainer').EquidistantSurfaces.Add();o.BaseSurface=surfaces;o.Distance=abs(op.distance);o.Direction=op.distance>=0
        elif op.kind=='sheet':
            sketch=r(op.sketch);o=self.cast(p,'ISheetMetalContainer').SheetMetalBodies.Add();o.Sketch=sketch;o.Thickness=op.thickness
        elif op.kind in ('linear_pattern','circular_pattern'):
            initial=refs(op.features);ax=axis(op.axis)
            o=mc.FeaturePatterns.Add(self.c('o3d_meshCopy' if op.kind=='linear_pattern' else 'o3d_circularCopy'))
            o=self.cast(o,'ILinearPattern' if op.kind=='linear_pattern' else 'ICircularPattern')
            o.InitialObjects=initial;o.Count1=op.count;o.Count2=1;o.Step1=op.step
            if op.kind=='linear_pattern':o.Axis1=ax
            else:o.Axis=ax;o.Count1=1;o.Count2=op.count;o.Step1=0;o.Step2=op.step
        elif op.kind=='mirror':
            initial=refs(op.features);pl=plane(op.plane);o=self.cast(mc.FeaturePatterns.Add(self.c('o3d_mirrorOperation')),'IMirrorPattern');o.InitialObjects=initial;o.Plane=pl;o.SaveInitialObjects=True
        elif op.kind=='boolean':
            base=r(op.base);bodies=refs(op.bodies);o=mc.Booleans.Add();o.BaseObject=base;o.ModifyObjects=bodies;o.BooleanType=self.c({'union':'ksUnion','subtract':'ksDifference','intersect':'ksIntersect'}[op.mode])
        elif op.kind=='rib':
            sk=r(op.sketch);bodies=objects(self.cast(p,'IFeature7').ResultBodies)
            if len(bodies)!=1:raise CadError('ambiguous_selection','Rib requires exactly one target body',count=len(bodies))
            o=mc.Ribs.Add();o.Sketch=sk;o.Body=bodies[0];o.SegmentIndex=0;o.Side=int(op.reverse);self.cast(o,'IThinParameters').SetThinParameters(True,self.c('dtBoth'),op.thickness/2,op.thickness/2)
        elif op.kind=='draft':
            faces=refs(op.faces);pl=plane(op.plane);o=mc.Inclines.Add();o.Faces=faces;o.Plane=pl;o.Angle=abs(op.angle);o.Direction=op.angle>=0
        elif op.kind=='surface_trim':
            surface=r(op.surface);cutter=r(op.cutter);o=self.cast(p,'ISurfaceContainer').TrimmedSurfaces.Add();o.Surface=surface;o.CutObject=cutter;o.Sense=not op.reverse
        elif op.kind=='bend':
            edges=refs(op.edges);o=self.cast(p,'ISheetMetalContainer').SheetMetalBends.Add();o.BendObjects=edges;o.Length=op.length;o.Radius=op.radius;o.Angle=op.angle;o.Direction=not op.reverse
        elif op.kind=='unfold':
            face=self.cast(r(op.fixed_face),'IFace')
            if not face.IsPlanar:raise CadError('invalid_parameters','Unfold reference face must be planar')
            bends=[self.cast(r(bend),'ISheetMetalBend') for bend in op.bends]
            try:
                for bend in bends:
                    bend.Straighten=True
                    if not bend.Update():raise CadError('build_failed','Cannot straighten native bend',name=bend.Name)
                p.RebuildModel(True)
            finally:self.sync(token,True)
            return {'document':self.document_info(token),'native_unfold':True,'method':'ISheetMetalBend.Straighten','name':req.name,'valid':True}
        elif op.kind=='plane_offset':
            pl=plane(op.plane);o=self.cast(self.cast(p,'IAuxiliaryGeomContainer').Planes3D.Add(self.c('o3d_planeOffset')),'IPlane3DByOffset');o.BasePlane=pl;o.Offset=abs(op.offset);o.Direction=op.offset>=0
        elif op.kind=='spiral':
            pl=plane(op.plane);o=self.cast(self.cast(p,'IAuxiliaryGeomContainer').Spirals3D.Add(self.c('o3d_cylindricSpiral')),'ICylindricSpiral3D');o.BasePlane=pl;o.Diameter=op.diameter;o.Step=op.pitch;o.TurnsCount=op.turns;o.Height=op.pitch*op.turns
        else: raise CadError('unsupported_feature',op.kind)
        o.Name=req.name
        try:
            updated=o.Update();f=self.cast(o,'IFeature7')
            if not updated or not o.Valid or f.ObjectError:
                raise CadError('build_failed',f'KOMPAS rejected {op.kind}',feature_error=f.ObjectError,name=req.name)
        finally:self.sync(token,True)
        info=self.document_info(token);reference=self.register(token,o)
        verification={}
        if before_volume is not None:
            after_volume=info.get('volume_mm3',0)
            removed=before_volume-after_volume
            verification={'volume_before_mm3':before_volume,'volume_after_mm3':after_volume,'removed_volume_mm3':removed}
            if removed<=max(1e-6,abs(before_volume)*1e-10):
                raise CadError('no_geometry_change','Cut did not remove measurable material; check sketch position and direction',reference=reference,verification=verification,state_changed=True,restored=False)
        self.evidence[op.kind]=self.evidence.get(op.kind,0)+1
        return {'reference':reference,'name':o.Name,'valid':bool(o.Valid),'feature_error':f.ObjectError,'document':info,'verification':verification}

    def geometry_select(self,query):
        q=Selector.model_validate(query);p=self.part(q.document_id)
        if q.component:p=self.cast(self.resolve(q.component,q.document_id),'IPart7')
        f=self.cast(p,'IFeature7')
        if q.feature:f=self.cast(self.resolve(q.feature,q.document_id),'IFeature7')
        if q.kind=='body': items=objects(f.ResultBodies)
        elif q.kind=='feature': items=objects(self.cast(p,'IModelContainer').Objects(0))
        else: items=objects(f.ModelObjects(6 if q.kind=='face' else 7))
        if q.adjacent_to:
            adjacent=self.resolve(q.adjacent_to,q.document_id)
            if q.kind=='face':
                try:items=objects(self.cast(adjacent,'IFace').ConnectedFaces)
                except pythoncom.com_error:
                    edge=self.cast(adjacent,'IEdge');items=[face for side in (True,False) if (face:=edge.AdjacentFace(side)) is not None]
            elif q.kind=='edge':items=objects(self.cast(adjacent,'IFace').LimitingEdges)
            else:raise CadError('invalid_parameters','Adjacency supports face or edge results')
        matches=[]
        for obj in items:
            row={'name':obj.Name,'kind':q.kind}
            if q.name is not None and row['name']!=q.name: continue
            if q.kind=='face':
                obj=self.cast(obj,'IFace');u=(obj.ParamUMin+obj.ParamUMax)/2;v=(obj.ParamVMin+obj.ParamVMax)/2
                row.update(point=xyz(obj.GetPoint(u,v)),normal=xyz(obj.GetNormal(u,v)),measure=obj.GetArea(self.c('ksLUnMM')),radius=obj.Radius)
                row['geometry']=next((n for n,a in [('plane','IsPlanar'),('cylinder','IsCylinder'),('sphere','IsSphere'),('cone','IsCone'),('torus','IsTorus'),('spline','IsNurbsSurface')] if getattr(obj,a)), 'other')
            elif q.kind=='edge':
                obj=self.cast(obj,'IEdge');a=xyz(obj.GetPoint(True));b=xyz(obj.GetPoint(False))
                row.update(start=a,end=b,point=[(x+y)/2 for x,y in zip(a,b)],measure=obj.GetLength(self.c('ksLUnMM')))
                row['geometry']=next((n for n,a in [('line','IsStraight'),('circle','IsCircle'),('arc','IsArc'),('spline','IsNurbs')] if getattr(obj,a)),'other')
            elif q.kind=='body': row.update(bounds=list(self.cast(obj,'IBody7').GetGabarit()),solid=bool(self.cast(obj,'IBody7').IsSolid))
            elif q.kind=='feature':
                try:feature=self.cast(obj,'IFeature7')
                except pythoncom.com_error as e:
                    if (e.hresult & 0xffffffff)!=0x80004002:raise
                    row.update(diagnostic_available=False,feature_error=None,object_type=obj.ModelObjectType)
                else:row.update(diagnostic_available=True,valid=bool(obj.Valid),feature_error=feature.ObjectError,state=feature.State,object_type=obj.ModelObjectType)
            if q.geometry and row.get('geometry')!=q.geometry: continue
            if q.radius is not None and abs(row.get('radius',float('inf'))-q.radius)>q.tolerance:continue
            if q.measure is not None and abs(row.get('measure',float('inf'))-q.measure)>q.tolerance:continue
            if q.near:
                distance=obj.GetDistanceToPoint(*q.near) if q.kind=='face' else math.dist(q.near,row.get('point',[float('inf')]*3))
                if distance>q.tolerance:continue
                row['distance']=distance
            if q.normal:
                norm=math.sqrt(sum(v*v for v in q.normal))
                if norm==0:raise CadError('invalid_parameters','Normal cannot be zero')
                if 'normal' not in row or math.dist([v/norm for v in q.normal],row['normal'])>q.tolerance:continue
            row['reference']=self.register(q.document_id,obj,q.kind);matches.append(row)
        if q.require_unique and len(matches)!=1:raise CadError('ambiguous_selection' if matches else 'no_match','Selection must identify exactly one object',candidates=matches[:q.limit],count=len(matches))
        return {'revision':self.documents[q.document_id]['revision'],'total':len(matches),'items':matches[q.offset:q.offset+q.limit]}

    def model_check(self,request):
        req=ModelCheck.model_validate(request)
        if any(x.query.document_id!=req.document_id for x in req.geometry_counts):
            raise CadError('cross_document_reference','All checks must target the requested document')
        info=self.document_info(req.document_id);checks=[]
        def record(name,expected,actual,passed):checks.append(dict(name=name,expected=expected,actual=actual,passed=passed))
        record('body_count',req.body_count,info.get('body_count'),info.get('body_count')==req.body_count)
        record('feature_error',0,info.get('feature_error'),info.get('feature_error')==0)
        if req.bounds_mm is not None:
            actual=info.get('bounds_mm')
            record('bounds_mm',req.bounds_mm,actual,actual is not None and all(abs(a-b)<=req.bounds_tolerance_mm for a,b in zip(req.bounds_mm,actual)))
        if req.volume_mm3 is not None:
            actual=info.get('volume_mm3')
            record('volume_mm3',req.volume_mm3,actual,actual is not None and abs(actual-req.volume_mm3)<=req.volume_tolerance_mm3)
        for i,item in enumerate(req.geometry_counts):
            query=item.query.model_copy(update={'require_unique':False,'offset':0,'limit':1})
            actual=self.geometry_select(query)['total']
            record(f'geometry_counts[{i}]',item.expected_count,actual,actual==item.expected_count)
        result={'passed':all(x['passed'] for x in checks),'document':info,'checks':checks}
        if not result['passed']:raise CadError('verification_failed','Geometry does not meet the declared expectations',report=result)
        return result

    def feature_edit(self,reference,parameters):
        obj=self.resolve(reference);token=reference['document_id']
        allowed={'depth','angle','radius','thickness','name','hidden'}
        if set(parameters)-allowed:raise CadError('invalid_parameters','Unsupported editable parameter')
        for key,value in parameters.items():
            if key=='name':
                if not isinstance(value,str) or not value:raise CadError('invalid_parameters','Name must be nonempty text')
            elif key=='hidden':
                if not isinstance(value,bool):raise CadError('invalid_parameters','Hidden must be boolean')
            elif isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise CadError('invalid_parameters','Dimensions must be positive finite numbers',parameter=key)
        for key,value in parameters.items():
            if key=='depth':
                extrusion=self.cast(obj,'IExtrusion');direction=extrusion.Direction
                if direction not in (self.c('dtNormal'),self.c('dtReverse')):
                    raise CadError('invalid_parameters','Depth edit requires a one-sided extrusion')
                extrusion.SetDepth(direction==self.c('dtNormal'),float(value))
            elif key=='angle':obj.SetAngle(True,float(value))
            else:setattr(obj,{'radius':'Radius1','thickness':'Thickness','name':'Name','hidden':'Hidden'}[key],value)
        try:
            if not obj.Update():raise CadError('build_failed','Parameter update failed')
            self.part(token).RebuildModel(True)
            f=self.cast(obj,'IFeature7')
            if f.ObjectError:raise CadError('build_failed','Updated feature has errors',feature_error=f.ObjectError)
        finally:self.sync(token,True)
        return {'reference':self.register(token,obj),'document':self.document_info(token)}

    def feature_suppress(self,reference,suppressed=True):
        o=self.resolve(reference);token=reference['document_id'];self.cast(o,'IFeature7').Excluded=suppressed
        return self.document_rebuild(token)

    def feature_delete(self,reference):
        obj=self.resolve(reference);token=reference['document_id']
        children=objects(self.cast(obj,'IModelObject1').Childrens(0))
        if children:raise CadError('dependent_features','Remove dependent operations first',children=[x.Name for x in children])
        if not self.cast(obj,'IFeature7').Delete():raise CadError('delete_failed','Cannot delete feature')
        return self.document_rebuild(token)

    def document_rebuild(self,document_id):
        p=self.part(document_id)
        try:
            if not p.RebuildModel(True):raise CadError('rebuild_failed','Model rebuild failed')
        finally:self.sync(document_id,True)
        return self.document_info(document_id)

    def checkpoint(self,document_id):
        d=self.doc(document_id)['com']
        if not d.PathName: self.document_save(document_id,'documents/'+document_id+{4:'.m3d',5:'.a3d',1:'.cdw',3:'.spw'}[d.DocumentType])
        else:self.document_save(document_id)
        source=Path(d.PathName);folder=self.root/'checkpoints'/uuid.uuid4().hex
        folder.mkdir(parents=True);dest=self._copy_package(source,folder)
        return {'document_id':document_id,'path':str(dest),'restored':False}

    def checkpoint_restore(self,path):
        p=self.path(path)
        if not p.is_relative_to(self.root/'checkpoints'):raise CadError('invalid_checkpoint','Use a generated checkpoint path')
        result=self.document_open_copy(str(p))
        return {'restored':True,'document':result,'original_document_preserved':True}

    def scenario_run(self,scenario):
        from .scenarios import run
        return run(self,scenario)

    def scenario_run_file(self,path,parameters=None):
        from .scenarios import run_file
        return run_file(self,path,parameters or {})

    def component_insert(self,document_id,source,name='',origin=(0,0,0),fixed=False):
        p=self.part(document_id)
        if self.doc(document_id)['com'].DocumentType!=5:raise CadError('wrong_document_type','Assembly required')
        src=Path(source).resolve(strict=True)
        if not src.is_relative_to(self.root):
            folder=self.root/'components'/uuid.uuid4().hex;folder.mkdir(parents=True);src=self._copy_package(src,folder)
        o=p.Parts.AddFromFile(str(src),True,True)
        if o is None:raise CadError('component_failed','Insert failed')
        o=self.cast(o,'IPart7')
        if Path(o.FileName).resolve()!=src:
            o.FileName=str(src)
            if not o.Update() or Path(o.FileName).resolve()!=src:
                raise CadError('component_source_mismatch','KOMPAS resolved a different source file',requested=str(src),actual=o.FileName)
        if name:o.Name=name
        o.Placement.SetOrigin(*origin);o.UpdatePlacement(True);o.Fixed=fixed;o.Update();self.sync(document_id,True)
        if math.dist(o.Placement.GetOrigin()[-3:],origin)>1e-7:raise CadError('placement_failed','Inserted component origin differs from request')
        return {'reference':self.register(document_id,o),'name':o.Name,'source':str(src)}

    def component_place(self,reference,origin,angles=(0,0,0),fixed=False):
        o=self.cast(self.resolve(reference),'IPart7');o.Fixed=False
        o.Placement.SetOrigin(*origin);o.Placement.SetEulerAngles(*angles);o.UpdatePlacement(True);o.Fixed=fixed;o.Update()
        if math.dist(o.Placement.GetOrigin()[-3:],origin)>1e-7:raise CadError('placement_failed','Component origin differs from request')
        self.sync(reference['document_id'],True)
        return {'reference':self.register(reference['document_id'],o)}

    def component_replace(self,reference,source):
        o=self.cast(self.resolve(reference),'IPart7');src=Path(source).resolve(strict=True)
        if not src.is_relative_to(self.root):raise CadError('path_outside_workspace','Import a component working copy first')
        o.FileName=str(src)
        if not o.Update():raise CadError('component_failed','Replacement failed')
        return self.document_rebuild(reference['document_id'])

    def assembly_intersections(self,document_id):
        p=self.part(document_id);parts=[self.cast(p.Parts.Item(i),'IPart7') for i in range(p.Parts.Count)];hits=[]
        for i,a in enumerate(parts):
            for b in parts[i+1:]:
                for ab in objects(self.cast(a,'IFeature7').ResultBodies):
                    for bb in objects(self.cast(b,'IFeature7').ResultBodies):
                        result=self.cast(ab,'IBody7').CheckIntersectionWithBody(self.cast(bb,'IBody7'),False)
                        if result:hits.append({'first':a.Name,'second':b.Name,'result':result})
        return {'intersections':hits,'component_count':len(parts)}

    def drawing_create(self,source_document_id,name='Drawing',scale=1.0):
        source=self.doc(source_document_id)['com']
        if not source.PathName:raise CadError('save_required','Save model before making associative views')
        if source.Changed:self.document_save(source_document_id)
        created=self.document_create('drawing',name);token=created['document_id']
        result=self.drawing_views(token,source_document_id,scale=scale)
        return {'document':self.document_info(token),'views':result['views']}

    def drawing_rebuild(self,document_id):
        d=self.cast(self.doc(document_id)['com'],'IKompasDocument2D');views=d.ViewsAndLayersManager.Views;count=0
        for i in range(views.Count):
            v=self.cast(views.Item(i),'IView')
            if v.ViewType in (2,3,4,5,6,7):
                if not self.cast(v,'IAssociationView').Rebuild():raise CadError('drawing_failed','View rebuild failed')
                count+=1
        self.sync(document_id,True);return {'views_rebuilt':count,'document':self.document_info(document_id)}

    def document_export(self,document_id,path):
        d=self.doc(document_id)['com'];dest=self.path(path)
        if dest.exists():raise CadError('file_exists','Choose a new export path')
        if dest.suffix.lower() in ('.pdf','.dxf'):
            if d.DocumentType not in (1,2,3):raise CadError('wrong_document_type','2D document required for PDF/DXF')
            self.document_save(document_id)
            library=self.bindir/'Pdf2d.dll' if dest.suffix.lower()=='.pdf' else self.bindir.parent/'Libs'/'ImpExp'/'dwgdxfExp.rtw'
            converter=self.app.Converter(str(library))
            if converter is None:raise CadError('export_unavailable','Converter unavailable',library=str(library))
            if converter.Convert(d.PathName,str(dest),1,False)!=1 or not dest.exists():raise CadError('export_unavailable','2D converter failed')
            return {'path':str(dest),'bytes':dest.stat().st_size}
        if dest.suffix.lower() not in ('.step','.stp','.stl'):raise CadError('unsupported_export','Supported: STEP, STL, PDF, DXF')
        if d.DocumentType not in (4,5):raise CadError('wrong_document_type','3D document required')
        param=self.cast(d,'IKompasDocument1').GetInterface(self.c('ksObjectAdditionConvertParameters'))
        param=self.cast(param,'IAdditionConvertParameters')
        param.Format=self.c('ksConverterToSTL' if dest.suffix.lower()=='.stl' else 'ksConverterToSTEP')
        param.LengthUnits=self.c('ksLUnMM')
        result=self.cast(d,'IKompasDocument3D1').ConvertToAdditionFormat(str(dest),param)
        if not result or not dest.is_file():raise CadError('export_unavailable','Converter did not create a file',format=dest.suffix)
        return {'path':str(dest),'bytes':dest.stat().st_size}

    def measure(self,first,second):
        token=first['document_id'];a=self.resolve(first);b=self.resolve(second,token);m=self.part(token).Measurement3D
        m.Object1=a;m.Object2=b
        if not m.Calculate():raise CadError('measurement_failed','Cannot measure selected objects')
        return {'minimum_mm':m.Lmin,'maximum_mm':m.Lmax,'angle_degrees':m.Angle if m.IsAngleValid else None,'first_point':xyz(m.GetMinPoint1()),'second_point':xyz(m.GetMinPoint2())}

    def capabilities(self):
        return {'target':'KOMPAS-3D v25 x64','installation':self.connect(),'compatibility':json.loads((Path(__file__).parent/'compatibility.json').read_text(encoding='utf-8')),'transport':'stdio','actions':list(self.actions),'successful_features_this_session':self.evidence,
                'limitations':['Evidence records successful operations, not universal geometric coverage.','Educational edition/converter support is determined by actual calls.']}
