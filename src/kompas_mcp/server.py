import atexit
import anyio
from mcp import types
import json
import logging
import multiprocessing
import os
from pathlib import Path
from typing import Literal
from mcp.server import MCPServer
from .models import SketchRequest, FeatureRequest, Selector, Scenario, Ref, ModelCheck, DrawingBatch, DrawingLayout, Positive
from .worker import CadWorker
from .errors import CadError
from .models import AssemblyCompose
from .building import BuildingProject, ProjectStore, inspect_project
from .building_norms import NormStore, NormQuery

ROOT=Path(os.environ.get('KOMPAS_MCP_WORKSPACE',str(Path.cwd()/'cad-workspace'))).resolve()
_worker=None
server=MCPServer('kompas-local',instructions='Work on owned documents only. Units: mm and degrees. Re-select topology after each change. Inspect geometry and feature errors after building. Never repeat a timed-out write; call operation_status. For buildings, persist explicit dimensions and assumptions in building_project_save. Query norm_search with applicability context before selecting normative dimensions. Metadata-only cards are not verified rules. Never invent a universal distance between partitions. Normative checks are advisory; do not silently correct projects or change pinned editions. Building semantic tools currently do not generate CAD deliverables.')
_building_store=ProjectStore(ROOT)

@server.tool()
async def building_project_save(project: BuildingProject, expected_revision: int | None = None) -> dict:
    """Save semantic building project atomically with revision check and history. No CAD mutation. None revision only creates a new project. Dimensions must be explicit, not inferred from an image scale."""
    try:
        if project.rule_pack != NormStore(ROOT).pack['id']:
            raise ValueError('Unknown normative pack')
        return {'ok':True,'data':_building_store.save(project,expected_revision)}
    except ValueError as exc:
        result=CadError('invalid_parameters',str(exc)).result()
        return types.CallToolResult(isError=True,content=[types.TextContent(type='text',text=json.dumps(result))],structuredContent=result)

@server.tool()
async def building_furniture_catalog() -> dict:
    """Parametric furniture envelope examples. Dimensions are product assumptions, not regulatory sizes; usage clearances must be supplied separately."""
    return {'ok':True,'data':json.loads((Path(__file__).parent/'data'/'furniture.json').read_text(encoding='utf-8'))}

@server.tool()
async def building_preview(project_id: str) -> dict:
    """EXPERIMENTAL: new native M3D and AEC-template CDW plan from a saved single-storey project. Furniture envelopes only; overlapping wall joints, no associative elevations or managed updates. Leaves results open. Not a compliant documentation release."""
    return await call('building_preview',project_id=project_id)

@server.tool()
async def building_project_check(project_id: str) -> dict:
    """Check saved semantic geometry: hosted openings, convex closed rooms and furniture/wall collisions. Not a normative compliance certificate; no CAD mutation."""
    return {'ok':True,'data':inspect_project(_building_store.load(project_id))}

@server.tool()
async def norm_search(request: NormQuery) -> dict:
    """Offline FTS5 search with explicit metadata verification status. Ambiguous partition-distance requests return missing context instead of a number. Full source information is in resources."""
    return {'ok':True,'data':NormStore(ROOT).search(request)}

@server.resource('kompas://building/{project_id}',mime_type='application/json')
def building_resource(project_id: str) -> str:
    return _building_store.load(project_id).model_dump_json(indent=2)

@server.resource('kompas://norm/{rule_id}',mime_type='application/json')
def norm_resource(rule_id: str) -> str:
    return NormStore(ROOT).cards[rule_id].model_dump_json(indent=2)

async def call(action,**arguments):
    global _worker
    if _worker is None:
        _worker=CadWorker(ROOT)
        atexit.register(_worker.close)
    try:result=await anyio.to_thread.run_sync(lambda:_worker.call(action,arguments))
    except CadError as e:result=e.result()
    if not result.get('ok',False):
        return types.CallToolResult(isError=True,content=[types.TextContent(type='text',text=json.dumps(result))],structuredContent=result)
    return result

@server.tool()
async def assembly_compose(request: AssemblyCompose) -> dict:
    """Insert named native components in one batch. Explicit orthonormal axes, read-back poses and component counts; checkpoint and partial-failure report. Fixed placement is not a kinematic mate."""
    return await call('assembly_compose',request=request.model_dump())

@server.tool()
async def drawing_sheet_set(document_id: str,format: Literal['A0','A1','A2','A3','A4','A5']='A3',portrait: bool=False) -> dict:
    """Set native paper format and orientation before placing views. Returns measured usable area; single sheet with checkpoint."""
    return await call('drawing_sheet_set',document_id=document_id,format=format,portrait=portrait)

@server.tool()
async def drawing_layout_check(document_id: str,padding: Positive=3,gap: Positive=3) -> dict:
    """Measure all view geometry, dimensions and text against native free sheet area. Detect overflow and overlapping view bounds. Single sheet; no intra-view text collision detection."""
    return await call('drawing_layout_check',document_id=document_id,padding=padding,gap=gap)

@server.tool()
async def drawing_fit(document_id: str,padding: Positive=3,gap: Positive=3,min_scale: Positive=.25,allow_annotation_scaling: bool=False) -> dict:
    """Fit independent visible views into the native free sheet area. Linked projections require a manual base-view layout so their alignment is preserved."""
    return await call('drawing_fit',document_id=document_id,padding=padding,gap=gap,min_scale=min_scale,allow_annotation_scaling=allow_annotation_scaling)

@server.tool()
async def drawing_layout(request: DrawingLayout) -> dict:
    """Position, scale or rotate views with a checkpoint while preserving projection links. Conflicting linked-view placement is rejected."""
    return await call('drawing_layout',request=request.model_dump())

@server.tool()
async def drawing_view_create(document_id: str,name: str='View',origin: tuple[float,float]=(0,0),scale: Positive=1.0,angle: float=0) -> dict:
    """Create a normal 2D view with explicit origin, scale and rotation. Dimension values remain in view/model units."""
    return await call('drawing_view_create',document_id=document_id,name=name,origin=origin,scale=scale,angle=angle)

@server.tool()
async def drawing_inspect(document_id: str,view_number: int=0) -> dict:
    """Read native view counts, dimension nominal values and text after saving/reopening. Up to 200 entries per type."""
    return await call('drawing_inspect',document_id=document_id,view_number=view_number)

@server.tool()
async def thread_parameters(standard: str='cil0_24705-2004 coarse', diameter: Positive|None=None) -> dict:
    """Read installed thread-table sizes/pitches before choosing a standard thread. Never silently infer missing pitch."""
    return await call('thread_parameters',standard=standard,diameter=diameter)

@server.tool()
async def thread_list(document_id: str) -> dict:
    """Inspect native symbolic threads, handedness, standard, pitch and axial limits, including reopened models."""
    return await call('thread_list',document_id=document_id)

@server.tool()
async def drawing_batch(request: DrawingBatch) -> dict:
    """Create native 2D outlines, hatches, text and dimensions in one checkpointed call. Coordinates are in the explicitly selected view."""
    return await call('drawing_batch',request=request.model_dump())

@server.tool()
async def connect() -> dict:
    """Connect to installed KOMPAS and report actual version."""
    return await call('connect')

@server.tool()
async def capabilities() -> dict:
    """List implemented actions and successful feature calls in this session."""
    return await call('capabilities')

@server.tool()
async def operation_status() -> dict:
    """Resolve a timed-out operation before submitting another CAD command."""
    return _worker.status() if _worker else {'ok':True,'pending':None}

@server.tool()
async def document_create(kind: Literal['part','assembly','drawing','specification']='part', name: str='Model') -> dict:
    """Create a separate owned CAD document."""
    return await call('document_create',kind=kind,name=name)

@server.tool()
async def document_open_copy(source: str) -> dict:
    """Open a working copy of an existing CAD file."""
    return await call('document_open_copy',source=source)

@server.tool()
async def document_info(document_id: str) -> dict:
    """Read revision, path, bounds, components and feature status."""
    return await call('document_info',document_id=document_id)

@server.tool()
async def model_check(request: ModelCheck) -> dict:
    """Verify body count, feature errors, envelope, volume and geometric selection counts. Mismatch is an MCP error; usable as a scenario acceptance step."""
    return await call('model_check',request=request.model_dump())

@server.tool()
async def document_save(document_id: str, path: str|None=None) -> dict:
    """Save a native document inside the configured workspace."""
    return await call('document_save',document_id=document_id,path=path)

@server.tool()
async def document_close(document_id: str, discard_changes: bool=False) -> dict:
    """Close an owned document. Unsaved work requires explicit discard_changes=true."""
    return await call('document_close',document_id=document_id,discard_changes=discard_changes)

@server.tool()
async def sketch_create(request: SketchRequest) -> dict:
    """Create native sketch geometry on an explicit plane or face."""
    return await call('sketch_create',request=request.model_dump())

@server.tool()
async def feature_create(request: FeatureRequest) -> dict:
    """Build a typed native feature and verify its update and error state."""
    return await call('feature_create',request=request.model_dump())

@server.tool()
async def geometry_select(query: Selector) -> dict:
    """Find features, faces, edges or bodies. Returns revision-scoped references."""
    return await call('geometry_select',query=query.model_dump())

@server.tool()
async def feature_edit(reference: Ref, parameters: dict[str,float|str|bool]) -> dict:
    """Edit depth, angle, radius, thickness, name or hidden visibility and rebuild."""
    return await call('feature_edit',reference=reference.model_dump(),parameters=parameters)

@server.tool()
async def feature_suppress(reference: Ref, suppressed: bool=True) -> dict:
    """Suppress or restore a native operation."""
    return await call('feature_suppress',reference=reference.model_dump(),suppressed=suppressed)

@server.tool()
async def feature_delete(reference: Ref) -> dict:
    """Delete a feature only when it has no dependent operations."""
    return await call('feature_delete',reference=reference.model_dump())

@server.tool()
async def document_rebuild(document_id: str) -> dict:
    """Rebuild a model and invalidate old geometry references."""
    return await call('document_rebuild',document_id=document_id)

@server.tool()
async def checkpoint(document_id: str) -> dict:
    """Save a checkpoint before a sequence of changes."""
    return await call('checkpoint',document_id=document_id)

@server.tool()
async def checkpoint_restore(path: str) -> dict:
    """Open a complete checkpoint as a new working copy; preserve failed document for inspection."""
    return await call('checkpoint_restore',path=path)

@server.tool()
async def scenario_run_file(path: str, parameters: dict | None = None) -> dict:
    """Run a workspace JSON recipe once. Substitute $params.name values; return selected outputs and a full report resource. Prefer this for repeated builds; never retry timed-out writes."""
    return await call('scenario_run_file',path=path,parameters=parameters)

@server.tool()
async def scenario_run(scenario: Scenario) -> dict:
    """Run ordered named actions. $step.path references earlier outputs. Stops on failure."""
    return await call('scenario_run',scenario=scenario.model_dump())

@server.tool()
async def component_insert(document_id: str, source: str, name: str='', origin: tuple[float,float,float]=(0,0,0), fixed: bool=False) -> dict:
    """Insert a component in an owned assembly."""
    return await call('component_insert',document_id=document_id,source=source,name=name,origin=origin,fixed=fixed)

@server.tool()
async def component_place(reference: Ref, origin: tuple[float,float,float], angles: tuple[float,float,float]=(0,0,0), fixed: bool=False) -> dict:
    """Set component placement using origin and SDK Euler angles in degrees."""
    return await call('component_place',reference=reference.model_dump(),origin=origin,angles=angles,fixed=fixed)

@server.tool()
async def component_replace(reference: Ref, source: str) -> dict:
    """Replace a component source with a file inside the working package."""
    return await call('component_replace',reference=reference.model_dump(),source=source)

@server.tool()
async def assembly_intersections(document_id: str) -> dict:
    """Check component bodies for intersections."""
    return await call('assembly_intersections',document_id=document_id)

@server.tool()
async def drawing_create(source_document_id: str, name: str='Drawing', scale: float=1.0) -> dict:
    """Create a drawing with three standard associative views of a saved model."""
    return await call('drawing_create',source_document_id=source_document_id,name=name,scale=scale)

@server.tool()
async def drawing_rebuild(document_id: str) -> dict:
    """Rebuild associative drawing views."""
    return await call('drawing_rebuild',document_id=document_id)

@server.tool()
async def document_export(document_id: str, path: str) -> dict:
    """Export STEP or STL, reporting converter failure explicitly."""
    return await call('document_export',document_id=document_id,path=path)

@server.tool()
async def measure(first: Ref, second: Ref) -> dict:
    """Measure distances and angle between two objects in one document."""
    return await call('measure',first=first.model_dump(),second=second.model_dump())

@server.resource('kompas://operations')
def operations() -> str:
    """Recent operation results including errors and durations."""
    path=ROOT/'operations.jsonl'
    return '\n'.join(path.read_text(encoding='utf-8').splitlines()[-100:]) if path.exists() else ''

@server.tool()
async def variables_list(document_id: str, feature: Ref|None=None) -> dict:
    """List native variables with names suitable for feature_bind."""
    return await call('variables_list',document_id=document_id,feature=feature.model_dump() if feature else None)

@server.tool()
async def variable_set(document_id: str, name: str, value: float|None=None, expression: str|None=None) -> dict:
    """Set a document variable or expression, then rebuild dependent features."""
    return await call('variable_set',document_id=document_id,name=name,value=value,expression=expression)

@server.tool()
async def feature_bind(reference: Ref, variable_name: str, expression: str) -> dict:
    """Bind a feature's native variable to a KOMPAS expression."""
    return await call('feature_bind',reference=reference.model_dump(),variable_name=variable_name,expression=expression)

@server.tool()
async def material_set(document_id: str, name: str, density_g_cm3: float) -> dict:
    """Set explicit material and density."""
    return await call('material_set',document_id=document_id,name=name,density_g_cm3=density_g_cm3)

@server.tool()
async def component_list(document_id: str) -> dict:
    """Read assembly hierarchy and current component references."""
    return await call('component_list',document_id=document_id)

@server.tool()
async def mate_create(document_id: str, first: Ref, second: Ref, kind: Literal['coincident','concentric','parallel','perpendicular','distance','angle','tangent']='coincident', value: float=0, alignment: Literal[-1,0,1]=0, name: str='Mate') -> dict:
    """Create and validate native assembly constraints."""
    return await call('mate_create',document_id=document_id,first=first.model_dump(),second=second.model_dump(),kind=kind,value=value,alignment=alignment,name=name)

@server.tool()
async def preview(document_id: str, path: str='preview.png', orientation: str|None=None) -> dict:
    """Export a native raster preview as a MCP resource."""
    return await call('preview',document_id=document_id,path=path,orientation=orientation)

@server.tool()
async def drawing_views(document_id: str, source_document_id: str, orientation: str='', scale: float=1, origin: tuple[float,float]=(80,180), spacing: tuple[float,float]=(30,30)) -> dict:
    """Add standard associative projection views."""
    return await call('drawing_views',document_id=document_id,source_document_id=source_document_id,orientation=orientation,scale=scale,origin=origin,spacing=spacing)

@server.tool()
async def drawing_dimension(document_id: str, view_number: int, first: tuple[float,float], second: tuple[float,float], position: tuple[float,float], orientation: Literal['horizontal','vertical','aligned']='horizontal') -> dict:
    """Add a linear dimension and report whether KOMPAS associated it to geometry."""
    return await call('drawing_dimension',document_id=document_id,view_number=view_number,first=first,second=second,position=position,orientation=orientation)

@server.tool()
async def drawing_stamp(document_id: str, fields: dict[str,str], sheet: int=0) -> dict:
    """Fill existing stamp cells using the installed drawing layout."""
    return await call('drawing_stamp',document_id=document_id,fields=fields,sheet=sheet)

@server.tool()
async def drawing_section(document_id: str, base_view_number: int, start: tuple[float,float], end: tuple[float,float], position: tuple[float,float], label: str='A') -> dict:
    """Create cut line and associative section view."""
    return await call('drawing_section',document_id=document_id,base_view_number=base_view_number,start=start,end=end,position=position,label=label)

@server.tool()
async def specification_create(assembly_document_id: str) -> dict:
    """Create a native specification with attached component documents."""
    return await call('specification_create',assembly_document_id=assembly_document_id)

@server.tool()
async def specification_update(assembly_document_id: str,specification_document_id: str) -> dict:
    """Regenerate this session's managed specification after composition changes, with a checkpoint."""
    return await call('specification_update',assembly_document_id=assembly_document_id,specification_document_id=specification_document_id)

@server.tool()
async def drawing_position(document_id: str,view_number: int,point: tuple[float,float],shelf: tuple[float,float],position: int) -> dict:
    """Create a native position leader in an explicit drawing view."""
    return await call('drawing_position',document_id=document_id,view_number=view_number,point=point,shelf=shelf,position=position)

@server.tool()
async def component_pattern(reference: Ref,count: int,step: tuple[float,float,float]) -> dict:
    """Create explicit linearly spaced component instances with a checkpoint; not a parametric feature."""
    return await call('component_pattern',reference=reference.model_dump(),count=count,step=step)

@server.tool()
async def document_import(source: str, name: str='Imported') -> dict:
    """Import a STEP file into a separate owned native document."""
    return await call('document_import',source=source,name=name)

@server.resource('kompas://artifact/{name}',mime_type='application/octet-stream')
def artifact(name: str) -> bytes:
    """Read a generated artifact inside the server workspace."""
    path=(ROOT/name).resolve()
    if not path.is_relative_to(ROOT) or path.suffix.lower() not in ('.png','.json','.txt'):
        raise ValueError('Artifact unavailable')
    if path.stat().st_size>20*1024*1024:raise ValueError('Artifact exceeds 20 MiB resource limit')
    return path.read_bytes()

@server.resource('kompas://image/{name}',mime_type='image/png')
def image_resource(name: str) -> bytes:
    """Generated PNG preview."""
    if Path(name).suffix.lower()!='.png':raise ValueError('PNG required')
    return artifact(name)

def main():
    multiprocessing.freeze_support()
    import sys,struct
    if sys.platform!='win32' or struct.calcsize('P')!=8:
        raise SystemExit('KOMPAS MCP requires Windows x64')
    logging.basicConfig(level=logging.INFO)
    server.run()

if __name__=='__main__':main()
