"""Native thread annotations and typed 2D drafting; called only on the CAD STA."""
import math
import pythoncom
from win32com.client import VARIANT
from .errors import CadError
from .models import DrawingBatch,DrawingLayout

class EngineeringOperations:
    def assembly_compose(self,request):
        from .assemblies import compose
        return compose(self,request)

    def drawing_sheet_set(self,document_id,format='A3',portrait=False):
        doc=self.cast(self.doc(document_id)['com'],'IKompasDocument2D')
        if doc.LayoutSheets.Count!=1:raise CadError('unsupported_layout','Single-sheet document required')
        checkpoint=self.checkpoint(document_id);sheet=doc.LayoutSheets.Item(0);f=sheet.Format
        f.Format=self.c('ksFormat'+format);f.FormatMultiplicity=1;f.VerticalOrientation=portrait
        if not sheet.Update():raise CadError('drawing_failed','Sheet format update failed',checkpoint=checkpoint)
        actual=sheet.Format
        if actual.Format!=self.c('ksFormat'+format) or bool(actual.VerticalOrientation)!=portrait:raise CadError('verification_failed','Sheet format differs from requested')
        self.sync(document_id,True)
        return {'format':format,'portrait':portrait,'width_mm':actual.FormatWidth,'height_mm':actual.FormatHeight,'free_area':list(sheet.GetPlaceInsideFrames()),'checkpoint':checkpoint,'revision':self.documents[document_id]['revision']}

    def drawing_layout_check(self,document_id,padding=3,gap=3):
        from .layout import audit
        return audit(self,document_id,padding,gap)

    def drawing_fit(self,document_id,padding=3,gap=3,min_scale=.25,allow_annotation_scaling=False):
        from .layout import fit
        return fit(self,document_id,padding,gap,min_scale,allow_annotation_scaling)

    def drawing_layout(self,request):
        from .layout import drawing_context
        DrawingLayout.model_validate(request)
        snapshot=self.checkpoint(request['document_id'])
        with drawing_context(self,request["document_id"]):return self._drawing_layout_active(request,snapshot)

    def _drawing_layout_active(self,request,snapshot):
        req=DrawingLayout.model_validate(request);doc=self.cast(self.doc(req.document_id)['com'],'IKompasDocument2D');views=doc.ViewsAndLayersManager.Views
        selected=[]
        for item in req.views:
            view=views.ViewByNumber(item.view_number)
            if view is None:raise CadError('no_match','Drawing view does not exist',view_number=item.view_number)
            if view.ViewType in (2,3,4,5,6,7):view=self.cast(view,'IAssociationView')
            selected.append((view,item))
        result=[]
        try:
            for view,item in selected:
                changed=False
                is_associative=view.ViewType in (2,3,4,5,6,7)
                linked=is_associative and bool(view.ProjectionLink)
                base=view.BaseView if linked else None
                if item.detach_projection_alignment:
                    raise CadError('projection_link_required','MCP preserves drawing projection links; create an independent view explicitly instead',view_number=item.view_number,checkpoint=snapshot)
                if linked and base is not None:
                    if item.origin is not None:
                        if abs(view.X-base.X)<1e-7 and abs(item.origin[0]-base.X)>1e-7:
                            raise CadError('projection_constraint_conflict','Requested X would break vertical projection alignment',view_number=item.view_number,base_view=base.Number,required_x=base.X,checkpoint=snapshot)
                        if abs(view.Y-base.Y)<1e-7 and abs(item.origin[1]-base.Y)>1e-7:
                            raise CadError('projection_constraint_conflict','Requested Y would break horizontal projection alignment',view_number=item.view_number,base_view=base.Number,required_y=base.Y,checkpoint=snapshot)
                    if item.scale is not None and abs(item.scale-base.Scale)>1e-9:
                        raise CadError('projection_constraint_conflict','Linked view scale must equal its base view scale',view_number=item.view_number,base_view=base.Number,required_scale=base.Scale,checkpoint=snapshot)
                if item.origin is not None and math.dist(item.origin,[view.X,view.Y])>1e-9:view.X,view.Y=item.origin;changed=True
                if item.scale is not None and abs(item.scale-view.Scale)>1e-9:view.Scale=item.scale;changed=True
                if item.angle is not None and abs(item.angle-view.Angle)>1e-9:view.Angle=item.angle;changed=True
                if changed and not view.Update():raise CadError('drawing_failed','View layout update failed',view_number=item.view_number,requested=item.model_dump(),actual={'origin':[view.X,view.Y],'scale':view.Scale,'visible':view.Visible},checkpoint=snapshot)
                if item.origin is not None and math.dist(item.origin,[view.X,view.Y])>1e-7:raise CadError('verification_failed','View did not reach requested position',checkpoint=snapshot)
                if linked and not view.ProjectionLink:raise CadError('verification_failed','Projection link was lost during layout update',view_number=item.view_number,checkpoint=snapshot)
                result.append({'view_number':view.Number,'origin':[view.X,view.Y],'scale':view.Scale,'angle':view.Angle,'visible':bool(view.Visible),'projection_link':bool(view.ProjectionLink) if is_associative else None,'base_view':view.BaseView.Number if is_associative and view.BaseView is not None else None})
        finally:self.sync(req.document_id,True)
        return {'views':result,'checkpoint':snapshot,'revision':self.doc(req.document_id)['revision']}

    def drawing_view_create(self,document_id,name='View',origin=(0,0),scale=1.0,angle=0):
        doc=self.cast(self.doc(document_id)['com'],'IKompasDocument2D')
        v=doc.ViewsAndLayersManager.Views.Add(self.c('vt_Normal'));v.Name=name;v.X,v.Y=origin;v.Scale=scale;v.Angle=angle
        try:
            if not v.Update():raise CadError('drawing_failed','Cannot create drawing view')
        finally:self.sync(document_id,True)
        return {'view_number':v.Number,'name':v.Name,'scale':v.Scale,'origin':[v.X,v.Y],'reference':self.register(document_id,v,'view')}

    def drawing_inspect(self,document_id,view_number=0):
        doc=self.cast(self.doc(document_id)['com'],'IKompasDocument2D')
        v=doc.ViewsAndLayersManager.Views.ViewByNumber(view_number)
        if v is None:raise CadError('no_match','View does not exist')
        dc=self.cast(v,'IDrawingContainer');symbols=self.cast(v,'ISymbols2DContainer');dimensions=[]
        for kind,collection in [('linear',symbols.LineDimensions),('diameter',symbols.DiametralDimensions),('radius',symbols.RadialDimensions)]:
            for i in range(min(collection.Count,200)):
                o=collection.Item(i);text=self.cast(o,'IDimensionText')
                dimensions.append({'kind':kind,'nominal_value':text.NominalValue,'prefix':text.Prefix.Str,'suffix':text.Suffix.Str})
        return {'revision':self.doc(document_id)['revision'],'view_number':v.Number,'name':v.Name,'origin':[v.X,v.Y],'scale':v.Scale,
                'object_count':v.ObjectCount,'hatch_count':dc.Hatches.Count,'line_count':dc.LineSegments.Count,'circle_count':dc.Circles.Count,
                'dimensions':dimensions,'labels':[self.cast(dc.DrawingTexts.Item(i),'IText').Str for i in range(min(dc.DrawingTexts.Count,200))]}

    def thread_parameters(self,standard='cil0_24705-2004 coarse',diameter=None):
        self.connect()
        pattern=self.app.SystemSettings.ThreadPattern(standard)
        if pattern is None:raise CadError('unsupported_standard','Thread table is not installed',standard=standard)
        result={'standard':pattern.Standart,'table':pattern.TableName,'internal_diameter_coefficient':pattern.HoleKoeff}
        if diameter is None:result['diameters_mm']=list(pattern.SelectDiameters() or ())
        else:
            pitches=list(pattern.SelectP(diameter) or ())
            result.update(diameter_mm=diameter,pitches_mm=pitches)
            if not pitches:raise CadError('unsupported_thread_size','Diameter is absent from selected table; do not infer a pitch',table=standard,diameter=diameter)
            result['internal_minor_diameters_mm']=[diameter-pattern.HoleKoeff*p for p in pitches]
        return result

    def _thread_row(self,token,o):
        p=self.cast(o,'IThreadsParameters');limits=o.GetLimitPoints()
        return {'reference':self.register(token,o,'thread'),'name':o.Name,'valid':bool(o.Valid),'standard':p.Standard,'table':p.ThreadStandardFileName,
                'designation':p.Designation,'diameter_mm':p.Diameter,'pitch_mm':p.Pitch,'internal':bool(p.Internal),'left_handed':bool(o.LeftThread),
                'limits_mm':list(limits[1:]) if limits[0] else None,'length_mm':math.dist(limits[1:4],limits[4:7]) if limits[0] else None}

    def thread_list(self,document_id):
        collection=self.cast(self.part(document_id),'ISymbols3DContainer').Threads
        return {'revision':self.doc(document_id)['revision'],'threads':[self._thread_row(document_id,collection.Thread(i)) for i in range(collection.Count)]}

    def _thread_create(self,req):
        token=req.document_id;op=req.operation
        catalog=self.thread_parameters(op.standard,op.diameter)
        if not any(abs(op.pitch-p)<1e-8 for p in catalog['pitches_mm']):
            raise CadError('unsupported_thread_size','Pitch is absent from the selected standard',available=catalog)
        face=self.cast(self.resolve(op.face,token),'IFace')
        if not face.IsCylinder:raise CadError('invalid_parameters','This adapter supports cylindrical thread faces')
        expected_radius=(op.diameter-catalog['internal_diameter_coefficient']*op.pitch)/2
        # Do not reshape a user's hole implicitly: require either standard core or nominal bore.
        if min(abs(face.Radius-op.diameter/2),abs(face.Radius-expected_radius))>0.01:
            raise CadError('thread_base_mismatch','Face radius does not match nominal or basic internal diameter',actual_radius=face.Radius,nominal_radius=op.diameter/2,internal_minor_radius=expected_radius)
        border=self.resolve(op.initial_border,token) if op.initial_border else None
        collection=self.cast(self.part(token),'ISymbols3DContainer').Threads
        if collection.FindThread(face) is not None:raise CadError('duplicate_thread','A thread already exists on this face')
        o=collection.Add();o.Name=req.name;o.BaseObject=face;o.AutoDiameter=False;o.AutoLenght=op.length is None
        o.BaseObjectAdjustment=False;o.LeftThread=op.left_handed;o.Direction=not op.reverse
        if border is not None:o.InitialBorder=border
        if op.length is not None:o.Lenght=op.length
        try:
            if not self.cast(o,'IThreadsParameters').Init(op.standard,op.diameter,op.pitch):raise CadError('unsupported_standard','Cannot initialize native thread')
            if not o.Update() or not o.Valid:raise CadError('build_failed','Native thread was rejected')
        finally:self.sync(token,True)
        row=self._thread_row(token,o)
        if abs(row['diameter_mm']-op.diameter)>1e-8 or abs(row['pitch_mm']-op.pitch)>1e-8 or not row['designation']:
            raise CadError('verification_failed','Native thread parameters differ from request',actual=row)
        self.evidence['thread']=self.evidence.get('thread',0)+1
        return {**row,'document':self.document_info(token),'warnings':['Native symbolic thread: no helical groove is removed; STEP contains the support geometry only.']}

    def drawing_batch(self,request):
        req=DrawingBatch.model_validate(request);token=req.document_id
        document=self.doc(token)['com']
        if document.DocumentType not in (1,2):raise CadError('wrong_document_type','Drawing or fragment required')
        view=self.cast(document,'IKompasDocument2D').ViewsAndLayersManager.Views.ViewByNumber(req.view_number)
        if view is None:raise CadError('no_match','Drawing view does not exist')
        dc=self.cast(view,'IDrawingContainer');symbols=self.cast(view,'ISymbols2DContainer');made=[];dimensions=[]
        snapshot=self.checkpoint(token)
        try:
            for item in req.geometry:
                for o in self._primitive(dc,item.geometry):
                    o.Style=item.style
                    if not o.Update():raise CadError('drawing_failed','Geometry style update failed')
                    made.append(o)
            for item in req.hatches:
                boundaries=[o for primitive in item.boundaries for o in self._primitive(dc,primitive)]
                hatch=dc.Hatches.Add();param=self.cast(hatch,'IHatchParam');param.HatchAngle=item.angle;param.Step=item.step;param.Style=0
                if not self.cast(hatch,'IBoundariesObject').AddBoundaries(VARIANT(pythoncom.VT_ARRAY|pythoncom.VT_DISPATCH,tuple(boundaries)),True):
                    raise CadError('drawing_failed','Hatch boundaries rejected')
                if not hatch.Update():raise CadError('drawing_failed','Hatch update rejected')
                made.append(hatch)
            for item in req.labels:
                o=dc.DrawingTexts.Add();o.X,o.Y=item.position;o.Angle=item.angle
                text=self.cast(o,'IText');text.Str=item.text
                font=self.cast(text.TextLine(0).TextItem(0),'ITextFont');font.Height=item.height
                if not o.Update():raise CadError('drawing_failed','Text update failed')
                made.append(o)
            for item in req.dimensions:
                if item.kind=='linear':
                    o=symbols.LineDimensions.Add();o.X1,o.Y1=item.first;o.X2,o.Y2=item.second;o.X3,o.Y3=item.position
                    o.Orientation=self.c({'horizontal':'ksLinDHorizontal','vertical':'ksLinDVertical','aligned':'ksLinDParallel'}[item.orientation])
                else:
                    o=(symbols.DiametralDimensions if item.kind=='diameter' else symbols.RadialDimensions).Add()
                    o.Xc,o.Yc=item.center;o.Radius=item.radius;o.Angle=item.angle
                txt=self.cast(o,'IDimensionText');txt.Prefix.Str=item.prefix;txt.Suffix.Str=item.suffix
                if not o.Update():raise CadError('drawing_failed','Dimension rejected')
                associated=bool(self.cast(o,'IDrawingObject1').Associate())
                expected=(abs(item.second[0]-item.first[0]) if item.orientation=='horizontal' else abs(item.second[1]-item.first[1]) if item.orientation=='vertical' else math.dist(item.first,item.second)) if item.kind=='linear' else item.radius*(2 if item.kind=='diameter' else 1)
                if abs(txt.NominalValue-expected)>1e-6:raise CadError('verification_failed','Native dimension differs from requested geometry',expected=expected,actual=txt.NominalValue)
                dimensions.append({'kind':item.kind,'nominal_value':txt.NominalValue,'associated':associated})
                made.append(o)
        except Exception as e:
            raise CadError('drawing_batch_failed','Drafting stopped; restore checkpoint if needed',checkpoint=snapshot,created_count=len(made),restored=False,cause=str(e)) from e
        finally:self.sync(token,True)
        return {'revision':self.doc(token)['revision'],'created_count':len(made),'dimensions':dimensions,'checkpoint':snapshot,
                'counts':{'geometry':len(req.geometry),'hatches':len(req.hatches),'labels':len(req.labels),'dimensions':len(req.dimensions)}}
