"""Batch native component placement with explicit axes and count verification."""
import math
import pythoncom
from win32com.client import VARIANT
from pathlib import Path
from .models import AssemblyCompose
from .errors import CadError

def pose(b,reference,origin,x_axis,z_axis,fixed):
    o=b.cast(b.resolve(reference),'IPart7');o.Fixed=False;p=o.Placement
    y_axis=[z_axis[1]*x_axis[2]-z_axis[2]*x_axis[1],z_axis[2]*x_axis[0]-z_axis[0]*x_axis[2],z_axis[0]*x_axis[1]-z_axis[1]*x_axis[0]]
    matrix=[*x_axis,0,*y_axis,0,*z_axis,0,*origin,1]
    if not p.InitByMatrix3D(VARIANT(pythoncom.VT_ARRAY|pythoncom.VT_R8,matrix)):raise CadError('placement_failed','Cannot set component frame')
    # KOMPAS can return False on an unchanged placement. Verify actual state below.
    placement_status=o.UpdatePlacement(True);o.Fixed=fixed;update_status=o.Update()
    actual_origin=list(p.GetOrigin()[-3:]);actual_x=list(p.GetVector(71)[-3:]);actual_z=list(p.GetVector(73)[-3:])
    if not o.Valid or any(math.dist(a,e)>1e-7 for a,e in [(actual_origin,origin),(actual_x,x_axis),(actual_z,z_axis)]):raise CadError('placement_failed','Read-back component frame differs')
    return {'origin':actual_origin,'x_axis':actual_x,'z_axis':actual_z,'fixed':bool(o.Fixed),'native_update_status':[placement_status,update_status]}

def compose(b,request):
    req=AssemblyCompose.model_validate(request);p=b.part(req.document_id)
    if b.doc(req.document_id)['com'].DocumentType!=5:raise CadError('wrong_document_type','Assembly required')
    existing={str(b.cast(p.Parts.Item(i),'IPart7').Name) for i in range(p.Parts.Count)}
    for c in req.components:
        if c.name in existing:raise CadError('invalid_parameters','Component name already exists',name=c.name)
        src=Path(c.source).resolve()
        if not src.is_file():raise CadError('invalid_parameters','Component file does not exist',source=c.source)
        if src.suffix.lower() not in ('.m3d','.a3d'):raise CadError('invalid_parameters','Native component file required',source=c.source)
    checkpoint=b.checkpoint(req.document_id);before=p.Parts.Count;rows=[];failed=None
    try:
        for c in req.components:
            failed=c.name;r=b.component_insert(req.document_id,c.source,c.name,c.origin,False)
            placement=pose(b,r['reference'],c.origin,c.x_axis,c.z_axis,c.fixed)
            rows.append({'name':c.name,'source':r['source'],'reference':r['reference'],**placement})
        if p.Parts.Count!=before+len(rows):raise CadError('verification_failed','Unexpected assembly component count')
        b.sync(req.document_id,True)
        for row in rows:row['reference']['revision']=b.documents[req.document_id]['revision']
        return {'components':rows,'added_count':len(rows),'component_count':p.Parts.Count,'checkpoint':checkpoint,'verification':{'count_passed':True,'placement_passed':True},'revision':b.documents[req.document_id]['revision']}
    except Exception as exc:
        raise CadError('assembly_batch_failed','Stopped; inserted instances retained for diagnosis',failed_component=failed,completed_names=[r['name'] for r in rows],checkpoint=checkpoint,restored=False,cause=str(exc)) from exc
