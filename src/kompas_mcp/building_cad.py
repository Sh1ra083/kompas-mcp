"""Experimental native preview adapter, not an architectural documentation release.

Always creates a new revision directory. No existing CDW or user layers are edited.
"""
import json
import math
import os
import uuid
from pathlib import Path
from .building import ProjectStore, wall_point, footprint
from .errors import CadError


def solids(project):
    levels={s.id:s for s in project.storeys}
    for wall in project.walls:
        level=levels[wall.storey_id]
        openings=[o for o in project.openings if o.wall_id==wall.id]
        heights=sorted({0,level.height,*[z for o in openings for z in (o.sill,o.sill+o.height)]})
        length=math.dist(wall.start,wall.end)
        for band,(bottom,top) in enumerate(zip(heights,heights[1:])):
            gaps=sorted((o.offset,o.offset+o.width) for o in openings if o.sill<(bottom+top)/2<o.sill+o.height)
            start=0
            for segment,(a,b) in enumerate(gaps+[(length,length)]):
                if a>start:
                    polygon=[wall_point(wall,start,-wall.thickness/2),wall_point(wall,a,-wall.thickness/2),wall_point(wall,a,wall.thickness/2),wall_point(wall,start,wall.thickness/2)]
                    yield wall.id,f'{wall.id}-b{band}-s{segment}',polygon,level.elevation+bottom,top-bottom
                start=max(start,b)
    for item in project.furniture:
        yield item.id,item.id,footprint(item),levels[item.storey_id].elevation,item.height


def preview(backend, project_id):
    project=ProjectStore(backend.root).load(project_id)
    if len(project.storeys)!=1:
        raise CadError('unsupported_capability','Preview currently requires exactly one storey')
    pieces=list(solids(project))
    if not pieces or len(pieces)>100:
        raise CadError('unsupported_capability','Preview supports 1..100 solid pieces')
    backend.connect()
    template=Path(os.environ.get('KOMPAS_MCP_AEC_TEMPLATE',r'D:\KOMPAS\Bin\Resources\docTemplates\defaultTemplates\AEC.cdt'))
    if not template.is_file():
        raise CadError('unsupported_capability','Installed AEC.cdt template not found')
    folder=backend.root/'buildings'/'previews'/f'{project.id}-r{project.revision}-{uuid.uuid4().hex[:8]}'
    folder.mkdir(parents=True)
    created=[]
    report={'project_id':project.id,'revision':project.revision,'status':'started','documents':created,
            'limitations':['Experimental preview, not a normative architectural drawing','Furniture represented by envelopes','Wall joints overlap as independent bodies','No managed CDW update or projection associations'],
            'object_map':{}}
    report_path=folder/'report.json'
    def record():report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    record()
    try:
        model=backend.document_create('part',project.name)['document_id'];created.append(model)
        for object_id,name,polygon,z,height in pieces:
            sketch=backend.sketch_create({'document_id':model,'name':name,'plane':'XY','offset':z,'primitives':[{'kind':'polygon','points':polygon}]})
            feature=backend.feature_create({'document_id':model,'name':name,'operation':{'kind':'extrude','sketch':sketch['reference'],'depth':height,'new_body':True}})
            report['object_map'].setdefault(object_id,[]).append({'feature_name':name})
        report['model']=backend.document_save(model,str(folder/'building.m3d'))
        report['model_check']=backend.model_check({'document_id':model,'body_count':len(pieces)})
        native=backend.app.Documents.AddNewDocumentFromTemplate(str(template))
        if native is None:raise CadError('creation_failed','AEC template creation failed')
        drawing=backend.adopt(native);created.append(drawing)
        backend.document_save(drawing,str(folder/'plan.cdw'))
        sheet=backend.drawing_sheet_set(drawing,'A3',False)
        vertices=[p for _,_,poly,_,_ in pieces for p in poly]
        lo=[min(p[i] for p in vertices) for i in (0,1)];hi=[max(p[i] for p in vertices) for i in (0,1)]
        # Reserve paper area for labels and frame; native audit remains authoritative.
        scale=next((s for s in (1/20,1/25,1/50,1/100,1/200,1/500) if (hi[0]-lo[0])*s<=280 and (hi[1]-lo[1])*s<=170),None)
        if scale is None:raise CadError('unsupported_layout','Model too large for preview on A3')
        view=backend.drawing_view_create(drawing,'MCP '+project.id,(45-lo[0]*scale,65-lo[1]*scale),scale)['view_number']
        geometry=[]
        cut=project.storeys[0].elevation+project.storeys[0].height/3
        for object_id,name,poly,z,height in pieces:
            if z<=cut<z+height or any(f.id==object_id for f in project.furniture):
                geometry.append({'geometry':{'kind':'polygon','points':poly},'style':1})
        backend.drawing_batch({'document_id':drawing,'view_number':view,'geometry':geometry})
        report['layout']=backend.drawing_layout_check(drawing)
        report['drawing']=backend.document_save(drawing)
        report['status']='preview_created'
        record()
        return {'project_id':project.id,'revision':project.revision,'model':report['model'],'drawing':report['drawing'],
                'checks':{'model':report['model_check'],'layout':report['layout']},'limitations':report['limitations'],
                'report':str(report_path),'documents':created}
    except Exception:
        report['status']='failed_partial_documents_retained';record()
        raise
