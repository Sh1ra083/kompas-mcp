import hashlib
import math
from pathlib import Path
from .errors import CadError
from .engineering import EngineeringOperations

ADVANCED_ACTIONS=('variables_list','variable_set','feature_bind','material_set','mate_create','component_list','preview',
                  'drawing_views','drawing_dimension','drawing_stamp','drawing_section','specification_create','specification_update','document_import','drawing_position','component_pattern','thread_parameters','thread_list','drawing_batch','drawing_view_create','drawing_inspect','drawing_layout')

class AdvancedOperations(EngineeringOperations):
    def drawing_position(self,document_id,view_number,point,shelf,position):
        if position<1:raise CadError('invalid_parameters','Position must be positive')
        d=self.cast(self.doc(document_id)['com'],'IKompasDocument2D');view=d.ViewsAndLayersManager.Views.ViewByNumber(view_number)
        if view is None:raise CadError('no_match','View does not exist')
        leader=self.cast(view,'ISymbols2DContainer').Leaders.Add(self.c('ksDrPosLeader'))
        branch=self.cast(leader,'IBranchs');branch.X0,branch.Y0=shelf;branch.AddBranchByPoint(0,*point)
        self.cast(leader,'IPositionLeader').Positions.Str=str(position)
        if not leader.Update():raise CadError('drawing_failed','Position callout failed')
        associated=bool(self.cast(leader,'IDrawingObject1').Associate());self.sync(document_id,True)
        return {'reference':self.register(document_id,leader,'drawing'),'position':position,'associated':associated}

    def component_pattern(self,reference,count,step):
        from .backend import xyz
        if count<2 or count>1000 or not any(step):raise CadError('invalid_parameters','Use 2..1000 components and a nonzero step')
        source=self.cast(self.resolve(reference),'IPart7');token=reference['document_id']
        origin=xyz(source.Placement.GetOrigin());angles=xyz(source.Placement.GetEulerAngles());path=source.FileName;name=source.Name;fixed=source.Fixed
        checkpoint=self.checkpoint(token);created=[]
        try:
            for index in range(1,count):
                inserted=self.component_insert(token,path,name+' '+str(index+1),[a+index*b for a,b in zip(origin,step)],fixed)
                placed=self.component_place(inserted['reference'],[a+index*b for a,b in zip(origin,step)],angles,fixed)
                created.append(placed['reference'])
        except Exception as e:raise CadError('pattern_failed','Component pattern stopped',created=created,checkpoint=checkpoint,restored=False) from e
        revision=self.documents[token]['revision'];created=[{**ref,'revision':revision} for ref in created]
        return {'components':created,'checkpoint':checkpoint,'parametric_feature':False,'warnings':['Explicit component instances; repeat the pattern operation to change spacing.']}

    def variables_list(self,document_id,feature=None):
        from .backend import objects
        owner=self.resolve(feature,document_id) if feature else self.part(document_id)
        f=self.cast(owner,'IFeature7')
        return {'variables':[{'name':v.Name,'value':v.Value,'expression':v.Expression,'note':v.Note,'read_only':v.ReadOnly} for v in objects(f.Variables(False,False))]}

    def variable_set(self,document_id,name,value=None,expression=None):
        from .backend import objects
        p=self.part(document_id);variables=objects(self.cast(p,'IFeature7').Variables(False,False));found=[v for v in variables if v.Name==name]
        if not found:
            if value is None:raise CadError('invalid_parameters','New variable requires a value')
            v=p.AddVariable(name,value,'MCP parameter')
        else:v=found[0]
        if v.ReadOnly:raise CadError('read_only_variable',name)
        if expression is not None:v.Expression=expression
        elif value is not None:v.Value=value
        else:raise CadError('invalid_parameters','Supply value or expression')
        self.cast(self.doc(document_id)['com'],'IKompasDocument3D').RebuildDocument()
        return self.document_rebuild(document_id)

    def feature_bind(self,reference,variable_name,expression):
        from .backend import objects
        o=self.resolve(reference);f=self.cast(o,'IFeature7');vs=[v for v in objects(f.Variables(False,False)) if v.Name==variable_name]
        if len(vs)!=1:raise CadError('variable_not_found','Use variables_list for the feature first')
        vs[0].Expression=expression
        self.cast(self.doc(reference['document_id'])['com'],'IKompasDocument3D').RebuildDocument()
        return self.document_rebuild(reference['document_id'])

    def material_set(self,document_id,name,density_g_cm3):
        if density_g_cm3<=0:raise CadError('invalid_parameters','Density must be positive')
        p=self.part(document_id);p.SetMaterial(name,density_g_cm3);p.Update()
        return self.document_rebuild(document_id)

    def component_list(self,document_id):
        p=self.part(document_id)
        def walk(part):
            rows=[]
            for i in range(part.Parts.Count):
                c=self.cast(part.Parts.Item(i),'IPart7')
                rows.append({'name':c.Name,'source':c.FileName,'fixed':c.Fixed,'reference':self.register(document_id,c),
                             'origin':list(c.Placement.GetOrigin()[-3:]),'x_axis':list(c.Placement.GetVector(71)[-3:]),'z_axis':list(c.Placement.GetVector(73)[-3:]),'children':walk(c)})
            return rows
        return {'components':walk(p),'revision':self.documents[document_id]['revision']}

    def mate_create(self,document_id,first,second,kind='coincident',value=0,alignment=0,name='Mate'):
        kinds={'coincident':'mc_Coincidence','concentric':'mc_Concentric','parallel':'mc_Parallel','perpendicular':'mc_Perpendicular','distance':'mc_Distance','angle':'mc_Angle','tangent':'mc_Tangency'}
        if kind not in kinds:raise CadError('invalid_parameters','Unknown mate type')
        a=self.resolve(first,document_id);b=self.resolve(second,document_id)
        manager=self.cast(self.doc(document_id)['com'],'IKompasDocument3D1').MateConstraints
        m=manager.Add(self.c(kinds[kind]));m.Name=name;m.BaseObject1=a;m.BaseObject2=b;m.ParamValue=value;m.Alignment=alignment
        try:
            if not m.Update() or not m.Valid:raise CadError('constraint_conflict','Assembly mate rejected')
        finally:self.sync(document_id,True)
        return {'reference':self.register(document_id,m),'valid':bool(m.Valid)}

    def preview(self,document_id,path='preview.png',orientation=None):
        d=self.doc(document_id)['com'];dest=self.path(path,'.png')
        if orientation and d.DocumentType in (4,5):
            manager=self.cast(d,'IKompasDocument3D1').ViewProjectionManager
            found=False
            aliases={'isometric':'ksVPIsometric','front':'ksVPFront','back':'ksVPRear','top':'ksVPUp','bottom':'ksVPDown','left':'ksVPLeft','right':'ksVPRight'}
            target=self.c(aliases[orientation]) if orientation in aliases else None
            available=[]
            for i in range(manager.Count):
                v=self.cast(manager.ViewProjection(i),'IViewProjection7');available.append(v.Name)
                if v.Name==orientation or target is not None and v.ViewProjectonType==target:
                    v.Current=True;found=True;break
            if not found:raise CadError('no_match','Unknown projection name',available=available)
        d1=self.cast(d,'IKompasDocument1')
        param=self.cast(d1.GetInterface(self.c('ksObjectRasterConvertParameters')),'IRasterConvertParameters')
        param.RasterFormat=self.c('ksRasterFormatPNG');param.Resolution=150;param.ColorBPP=24;param.Scale=1
        if d.DocumentType in (4,5):
            raster_scale=param.CalcRasterScale(d,1600,1200)
            if not math.isfinite(raster_scale) or raster_scale<=0:
                raise CadError('preview_failed','Cannot calculate bounded raster scale')
            param.Scale=raster_scale
        if not d1.SaveAsToRasterFormat(str(dest),param) or not dest.exists():raise CadError('preview_failed','Raster export failed')
        return {'path':str(dest),'resource_uri':'kompas://image/'+dest.relative_to(self.root).as_posix(),'sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),'bytes':dest.stat().st_size}

    def drawing_views(self,document_id,source_document_id,orientation='',scale=1.0,origin=(80,180),spacing=(30,30)):
        import pythoncom
        from win32com.client import VARIANT
        source=self.doc(source_document_id)['com'];d=self.cast(self.doc(document_id)['com'],'IKompasDocument2D')
        if not source.PathName:raise CadError('save_required','Save model before projection')
        viewtypes=VARIANT(pythoncom.VT_ARRAY|pythoncom.VT_I4,(self.c('ksPtFront'),self.c('ksPtUp'),self.c('ksPtLeft')))
        if not d.ViewsAndLayersManager.Views.AddStandartViews(source.PathName,orientation,viewtypes,*origin,scale,*spacing):
            raise CadError('drawing_failed','Standard views failed')
        # Draft occlusion can omit completely enclosed assembly components even
        # from a later section. Engineering drawings need full projection.
        views=d.ViewsAndLayersManager.Views
        for i in range(views.Count):
            v=self.cast(views.Item(i),'IView')
            if v.ViewType in (2,3,4,5,6,7):
                a=self.cast(v,'IAssociationView');a.UseOcclusion=False
                if not a.Update() or not a.Rebuild() or a.UseOcclusion:
                    raise CadError('verification_failed','Full-quality projection could not be enabled',view=v.Number)
        self.sync(document_id,True)
        created=self._views(document_id)
        broken=[row for row in created if row.get('base_view') is not None and not row.get('projection_link')]
        if broken:raise CadError('verification_failed','Standard projection links were not created',views=broken)
        return {'views':created,'verification':{'projection_links_preserved':True,'full_projection':True}}

    def _views(self,document_id):
        views=self.cast(self.doc(document_id)['com'],'IKompasDocument2D').ViewsAndLayersManager.Views
        result=[]
        for v in (self.cast(views.Item(i),'IView') for i in range(views.Count)):
            row={'number':v.Number,'name':v.Name,'type':v.ViewType}
            if v.ViewType in (2,3,4,5,6,7):
                a=self.cast(v,'IAssociationView');row.update(projection_link=bool(a.ProjectionLink),base_view=a.BaseView.Number if a.BaseView is not None else None,draft_projection=bool(a.UseOcclusion))
            result.append(row)
        return result

    def drawing_dimension(self,document_id,view_number,first,second,position,orientation='horizontal'):
        d=self.cast(self.doc(document_id)['com'],'IKompasDocument2D');v=d.ViewsAndLayersManager.Views.ViewByNumber(view_number)
        if v is None:raise CadError('no_match','View does not exist')
        o=self.cast(v,'ISymbols2DContainer').LineDimensions.Add();o.X1,o.Y1=first;o.X2,o.Y2=second;o.X3,o.Y3=position
        o.Orientation=self.c({'horizontal':'ksLinDHorizontal','vertical':'ksLinDVertical','aligned':'ksLinDParallel'}[orientation])
        if not o.Update():raise CadError('drawing_failed','Dimension creation failed')
        # Ask KOMPAS to associate dimension endpoints to underlying geometry.
        associated=bool(self.cast(o,'IDrawingObject1').Associate())
        self.sync(document_id,True)
        return {'reference':self.register(document_id,o,'dimension'),'associated':associated}

    def drawing_stamp(self,document_id,fields,sheet=0):
        d=self.doc(document_id)['com'];stamp=d.LayoutSheets.Item(sheet).Stamp
        missing=[key for key in fields if not stamp.IsCellPresent(int(key))]
        if missing:raise CadError('invalid_stamp_cells','Stamp does not contain requested cells',cells=missing)
        for key,value in fields.items():stamp.Text(int(key)).Str=value
        if not stamp.Update():raise CadError('drawing_failed','Stamp update failed')
        self.sync(document_id,True);return {'updated_cells':list(fields)}

    def drawing_section(self,document_id,base_view_number,start,end,position,label='A'):
        import pythoncom
        from win32com.client import VARIANT
        d=self.cast(self.doc(document_id)['com'],'IKompasDocument2D');views=d.ViewsAndLayersManager.Views;base=views.ViewByNumber(base_view_number)
        cut=self.cast(base,'ISymbols2DContainer').CutLines.Add();cut.X1,cut.Y1=start;cut.X2,cut.Y2=end;cut.Text.Str=label
        cut.Points=VARIANT(pythoncom.VT_ARRAY|pythoncom.VT_R8,tuple(start)+tuple(end))
        if not cut.Update():raise CadError('drawing_failed','Cut line failed')
        view=self.cast(views.Add(self.c('vt_Section')),'IAssociationView');view.BaseView=base;view.BaseObject=cut;view.ProjectionLink=True;view.UseOcclusion=False;view.Scale=base.Scale;view.X,view.Y=position
        if not view.Update() or not view.Rebuild():raise CadError('drawing_failed','Section view failed')
        if not view.ProjectionLink or view.BaseView is None:raise CadError('verification_failed','Section projection link was not preserved')
        if view.UseOcclusion:raise CadError('verification_failed','Section still uses draft projection')
        self.sync(document_id,True);return {'view_number':view.Number,'reference':self.register(document_id,view,'view')}

    def specification_create(self,assembly_document_id):
        p=self.part(assembly_document_id);source=self.doc(assembly_document_id)['com']
        if source.DocumentType!=5:raise CadError('wrong_document_type','Assembly required')
        if not source.PathName:raise CadError('save_required','Save assembly first')
        created=self.document_create('specification');token=created['document_id'];d=self.doc(token)['com'];desc=d.SpecificationDescriptions.Active
        if desc is None:raise CadError('specification_unavailable','No default specification style')
        self.documents[token]['managed_specification']=True
        # Explicit managed rows: attaching the whole assembly also auto-imports a second BOM.
        self.documents[token]['source_assembly']=source.PathName
        return self._fill_specification(assembly_document_id,token)

    def specification_update(self,assembly_document_id,specification_document_id):
        state=self.doc(specification_document_id)
        if not state.get('managed_specification'):raise CadError('not_managed','Update supports specifications generated in this session')
        snapshot=self.checkpoint(specification_document_id)
        desc=state['com'].SpecificationDescriptions.Active
        for i in reversed(range(desc.BaseObjects.Count)):
            if not desc.BaseObjects.Item(i).Delete():raise CadError('specification_failed','Cannot replace generated row',checkpoint=snapshot)
        result=self._fill_specification(assembly_document_id,specification_document_id);result['checkpoint']=snapshot
        return result

    def _fill_specification(self,assembly_document_id,token):
        previous=self.app.ActiveDocument;target=self.doc(token)['com']
        try:
            target.Active=True
            return self._fill_specification_active(assembly_document_id,token)
        finally:
            if previous is not None and previous.Reference!=target.Reference:previous.Active=True

    def _fill_specification_active(self,assembly_document_id,token):
        p=self.part(assembly_document_id);d=self.doc(token)['com'];desc=d.SpecificationDescriptions.Active
        tuning=desc.SpecificationTuning;tuning.CalculatePosition=True
        for j in range(tuning.Sections.Count):
            section=tuning.Sections.Item(j);section.ReserveStringCount=0;section.SortObjects=False
        if not tuning.Update():raise CadError('specification_failed','Cannot apply managed numbering settings')
        sections=desc.SpecificationStyle.Sections
        section_ids={sections.Item(i).Name.lower():sections.Item(i).Number for i in range(sections.Count)}
        if 'детали' not in section_ids or 'сборочные единицы' not in section_ids:raise CadError('specification_unavailable','Required native sections missing',sections=section_ids)
        groups={}
        for i in range(p.Parts.Count):
            c=self.cast(p.Parts.Item(i),'IPart7');key=(c.FileName.lower(),c.Marking)
            if key not in groups:groups[key]={'component':c,'quantity':0,'instances':[]}
            groups[key]['quantity']+=1;groups[key]['instances'].append(c.Name)
        rows=[];native_rows=[]
        for i,group in enumerate(groups.values()):
            c=group['component'];o=desc.BaseObjects.Add(section_ids['сборочные единицы' if c.Parts.Count else 'детали'],0)
            if o is None:raise CadError('specification_failed','Cannot create specification row')
            o.AttachedDocuments.Add(c.FileName,False)
            for j in range(o.Columns.Count):
                column=o.Columns.Item(j);name=column.ColumnName.lower()
                if 'наимен' in name:column.Text.Str=c.Name
                elif 'обознач' in name:column.Text.Str=c.Marking
                elif 'кол' in name:column.Text.Str=str(group['quantity'])
                elif 'поз' in name:column.Text.Str=str(i+1)
            if not o.Update():raise CadError('specification_failed','Row update failed')
            rows.append({'position':i+1,'name':c.Name,'source':c.FileName,'quantity':group['quantity'],'instances':group['instances']})
            native_rows.append(o)
        self.cast(d,'ISpecificationDocument').RebuildDocument();self.sync(token,True)
        if desc.BaseObjects.Count!=len(rows):raise CadError('specification_failed','Unexpected native BOM row count',expected=len(rows),actual=desc.BaseObjects.Count)
        for row,native in zip(rows,native_rows):
            row['columns']={native.Columns.Item(j).ColumnName:native.Columns.Item(j).Text.Str for j in range(native.Columns.Count)}
            positions=[value for key,value in row['columns'].items() if 'поз' in key.lower()]
            if positions!=[str(row['position'])]:raise CadError('specification_failed','Native position differs',row=row)
            quantities=[value for key,value in row['columns'].items() if 'кол' in key.lower()]
            if quantities and str(row['quantity']) not in quantities:raise CadError('specification_failed','Native quantity differs from assembly count',row=row)
        return {'document':self.document_info(token),'rows':rows}

    def document_import(self,source,name='Imported'):
        src=Path(source).resolve(strict=True)
        if src.suffix.lower() not in ('.step','.stp'):raise CadError('invalid_parameters','STEP input required')
        created=self.document_create('part',name);token=created['document_id'];d=self.doc(token)['com']
        param=self.cast(self.cast(d,'IKompasDocument1').GetInterface(self.c('ksObjectAdditionConvertParameters')),'IAdditionConvertParameters');param.Format=self.c('ksConverterFromSTEP')
        if not self.cast(d,'IKompasDocument3D1').ConvertFromAdditionFormat(str(src),param):raise CadError('import_failed','STEP conversion failed')
        self.sync(token,True);return self.document_info(token)
