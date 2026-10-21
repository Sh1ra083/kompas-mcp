"""Conservative sheet-space layout using native object bounds, including annotations."""
import math
import pythoncom
from .errors import CadError

from contextlib import contextmanager

@contextmanager
def drawing_context(b,document_id):
    target=b.doc(document_id)['com'];previous=b.app.ActiveDocument
    views=b.cast(target,'IKompasDocument2D').ViewsAndLayersManager.Views
    current=next((b.cast(views.Item(i),'IView').Number for i in range(views.Count) if b.cast(views.Item(i),'IView').Current),0)
    try:
        target.Active=True
        views.ViewByNumber(0).Current=True
        yield
    finally:
        views.ViewByNumber(current).Current=True
        if previous is not None and previous.Reference!=target.Reference:previous.Active=True

def audit(b,document_id,padding=3,gap=3):
    # API5 needs the target document active; active non-system view also reports
    # a zero Scale in API7. Isolate both and restore the user's context.
    with drawing_context(b,document_id):return _audit_active(b,document_id,padding,gap)

def _audit_active(b,document_id,padding=3,gap=3):
    d=b.cast(b.doc(document_id)['com'],'IKompasDocument2D')
    if d.LayoutSheets.Count!=1:raise CadError('unsupported_layout','Layout audit currently requires one sheet')
    free=d.LayoutSheets.Item(0).GetPlaceInsideFrames()
    if not free[0]:raise CadError('layout_unavailable','Cannot read free sheet area')
    _,left,top,right,bottom=free;area=[left+padding,bottom+padding,right-padding,top-padding]
    if area[2]<=area[0] or area[3]<=area[1]:raise CadError('invalid_parameters','Padding leaves no usable sheet area')
    l=b.legacy_mass;raw=l.app.ksGetDocumentByReference(d.Reference)
    doc5=l.api.ksDocument2D(raw._oleobj_.QueryInterface(l.api.ksDocument2D.CLSID,pythoncom.IID_IDispatch))
    rows=[];unknown=[]
    views=d.ViewsAndLayersManager.Views
    for i in range(views.Count):
        v=b.cast(views.Item(i),'IView')
        if not v.Visible:continue
        annotations=0
        objs=b.cast(v,'IDrawingContainer').Objects(b.c('ksAllObj'));boxes=[]
        for o in (objs if isinstance(objs,(tuple,list)) else [objs] if objs else []):
            o=b.cast(o,'IDrawingObject')
            if o.DrawingObjectType==b.c('ksDrCsPoint'):continue
            if o.DrawingObjectType==b.c('ksDrDrawText') and not b.cast(o,'IText').Str.strip():continue
            if o.DrawingObjectType in (4,9,10,13,14,15,19,21,22,23,24,25,26,38,39,40,43,46,47,50,51,52,53,54,57,62,71):annotations+=1
            p=l.api.ksRectParam(l.app.GetParamStruct(b.c('ko_RectParam')))
            if not doc5.ksGetObjGabaritRect(o.Reference,p):unknown.append({'view_number':v.Number,'type':o.DrawingObjectType});continue
            a=l.api.ksMathPointParam(p.GetpBot());z=l.api.ksMathPointParam(p.GetpTop());box=[a.x,a.y,z.x,z.y]
            if not all(math.isfinite(x) for x in box):unknown.append({'view_number':v.Number,'type':o.DrawingObjectType});continue
            boxes.append(box)
        if not boxes:continue
        box=[min(x[0] for x in boxes),min(x[1] for x in boxes),max(x[2] for x in boxes),max(x[3] for x in boxes)]
        overflow=[max(0,area[0]-box[0]),max(0,area[1]-box[1]),max(0,box[2]-area[2]),max(0,box[3]-area[3])]
        projection_link=None;base_view=None
        if v.ViewType in (2,3,4,5,6,7):
            a=b.cast(v,'IAssociationView');projection_link=bool(a.ProjectionLink);base_view=a.BaseView.Number if a.BaseView is not None else None
        rows.append({'view_number':v.Number,'bounds_mm':box,'origin':[v.X,v.Y],'scale':v.Scale,'angle':v.Angle,'annotation_count':annotations,'object_count':len(boxes),'overflow_mm':overflow,'projection_link':projection_link,'base_view':base_view})
    collisions=[]
    for i,a in enumerate(rows):
        for c in rows[i+1:]:
            x=a['bounds_mm'];y=c['bounds_mm']
            if x[0]<y[2]+gap and y[0]<x[2]+gap and x[1]<y[3]+gap and y[1]<x[3]+gap:collisions.append([a['view_number'],c['view_number']])
    broken_links=[{'view_number':r['view_number'],'base_view':r['base_view']} for r in rows if r['base_view'] is not None and r['projection_link'] is False]
    return {'passed':bool(rows) and not unknown and not collisions and not broken_links and all(max(r['overflow_mm'])<1e-6 for r in rows),'usable_area_mm':area,'views':rows,'overlapping_view_bounds':collisions,'unmeasured_objects':unknown,'broken_projection_links':broken_links,'projection_links_preserved':not broken_links,'padding_mm':padding,'gap_mm':gap,'limitation':'Conservative rectangular view bounds; does not detect overlaps between annotations inside one view.'}

def pack(rows,area,gap):
    for columns in range(1,len(rows)+1):
        x,y=area[0],area[3];row_height=0;placements=[];valid=True
        for i,row in enumerate(rows):
            box=row['bounds_mm'];w=box[2]-box[0];h=box[3]-box[1]
            if i and i%columns==0:x=area[0];y-=row_height+gap;row_height=0
            if x+w>area[2]+1e-6 or y-h<area[1]-1e-6:valid=False;break
            placements.append({'view_number':row['view_number'],'origin':[row['origin'][0]+x-box[0],row['origin'][1]+y-h-box[1]]})
            x+=w+gap;row_height=max(row_height,h)
        if valid:return placements
    return None

def fit(b,document_id,padding=3,gap=3,min_scale=.25,allow_annotation_scaling=False):
    initial=audit(b,document_id,padding,gap)
    if initial['passed']:return {'changed':False,'verification':initial}
    rows=initial['views']
    linked=[r['view_number'] for r in rows if r.get('base_view') is not None and r.get('projection_link')]
    if linked:raise CadError('layout_requires_manual_review','Automatic independent packing would violate projection alignment; move base views or choose a larger sheet',linked_views=linked,audit=initial)
    if initial['unmeasured_objects'] or not rows or any(r['view_number']==0 for r in rows):raise CadError('layout_requires_manual_review','Unmeasured or system-view objects cannot be automatically fitted',audit=initial)
    original=[{k:r[k] for k in ('view_number','origin','scale','angle')} for r in rows]
    checkpoint=b.checkpoint(document_id)
    # First move without changing scale; then measure each real scaled result anew.
    scales=[None]+[s for s in [10,5,4,2.5,2,1,.5,.4,.25,.2,.1,.05] if min_scale<=s<max(r['scale'] for r in rows)]
    if any(r['annotation_count'] for r in rows) and not allow_annotation_scaling:scales=[None]
    try:
        for scale in scales:
            if scale is not None:
                b.drawing_layout({'document_id':document_id,'views':[{'view_number':r['view_number'],'scale':min(scale,r['scale'])} for r in rows]})
            measured=audit(b,document_id,padding,gap)
            placements=pack(measured['views'],measured['usable_area_mm'],gap)
            if placements:
                b.drawing_layout({'document_id':document_id,'views':placements})
                checked=audit(b,document_id,padding,gap)
                if checked['passed']:return {'changed':True,'checkpoint':checkpoint,'verification':checked,'visual_review_required':True,'annotation_scaling_applied':scale is not None and any(r['annotation_count'] for r in rows)}
        raise CadError('layout_does_not_fit','Use a larger sheet, reposition annotations, or explicitly allow annotation scaling; no acceptable layout found')
    except Exception:
        b.drawing_layout({'document_id':document_id,'views':original})
        raise
