from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

Point2 = tuple[float, float]
Point3 = tuple[float, float, float]
Positive = Annotated[float, Field(gt=0)]

class Ref(Strict):
    document_id: str
    object_id: str
    revision: int = Field(ge=0)

class Line(Strict):
    kind: Literal['line']
    start: Point2
    end: Point2
    construction: bool = False

    @model_validator(mode='after')
    def length(self):
        if self.start == self.end: raise ValueError('Zero length line')
        return self

class Circle(Strict):
    kind: Literal['circle']
    center: Point2 = (0, 0)
    radius: Positive

class Rectangle(Strict):
    kind: Literal['rectangle']
    origin: Point2 = (0, 0)
    width: Positive
    height: Positive

class Arc(Strict):
    kind: Literal['arc']
    center: Point2
    radius: Positive
    start_angle: float
    end_angle: float
    counterclockwise: bool = True

class Spline(Strict):
    kind: Literal['spline']
    points: list[Point2] = Field(min_length=2)
    closed: bool = False

class Polygon(Strict):
    kind: Literal['polygon']
    points: list[Point2] = Field(min_length=3,max_length=10000)

    @model_validator(mode='after')
    def nondegenerate(self):
        if any(a==b for a,b in zip(self.points,self.points[1:]+self.points[:1])):raise ValueError('Repeated polygon vertex')
        area=sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(self.points,self.points[1:]+self.points[:1]))
        if abs(area)<1e-12:raise ValueError('Degenerate polygon')
        return self

Primitive = Annotated[Line | Circle | Rectangle | Arc | Spline | Polygon, Field(discriminator='kind')]

class Constraint(Strict):
    kind: Literal['horizontal','vertical','coincident','parallel','perpendicular','equal_length','equal_radius','tangent','concentric','fixed_point','length','angle']
    entity: int = Field(ge=0, description='Index in flattened geometry; rectangle produces four lines')
    partner: int | None = Field(default=None,ge=0)
    point_index: int = 0
    partner_point_index: int = 0
    value: float | None = None
    variable: str = ''

class SketchRequest(Strict):
    document_id: str
    name: str = Field(min_length=1)
    plane: Literal['XY','XZ','YZ'] | Ref = 'XY'
    offset: float = 0
    primitives: list[Primitive] = Field(default_factory=list, max_length=10000)
    projections: list[Ref] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)

class Extrude(Strict):
    kind: Literal['extrude']
    sketch: Ref
    depth: Positive
    cut: bool = False
    new_body: bool = False
    reverse: bool = False
    draft: float = Field(default=0, ge=-89, le=89)

class Revolve(Strict):
    kind: Literal['revolve']
    sketch: Ref
    axis: Literal['X','Y','Z'] | Ref = 'Z'
    angle: float = Field(default=360, gt=0, le=360)
    cut: bool = False

class Loft(Strict):
    kind: Literal['loft','surface_loft']
    sketches: list[Ref] = Field(min_length=2)
    guides: list[Ref] = Field(default_factory=list)
    closed: bool = False

class Sweep(Strict):
    kind: Literal['sweep','surface_sweep']
    sketch: Ref
    path: list[Ref] = Field(min_length=1)
    cut: bool = False

    @model_validator(mode='after')
    def solid_cut(self):
        if self.kind=='surface_sweep' and self.cut:raise ValueError('Surface sweep cannot cut a solid')
        return self

class ThreadFeature(Strict):
    kind: Literal['thread']
    face: Ref
    standard: str = 'cil0_24705-2004 coarse'
    diameter: Positive
    pitch: Positive
    length: Positive | None = None
    initial_border: Ref | None = None
    reverse: bool = False
    left_handed: bool = False

class Fillet(Strict):
    kind: Literal['fillet']
    edges: list[Ref] = Field(min_length=1)
    radius: Positive
    tangent: bool = True

class Chamfer(Strict):
    kind: Literal['chamfer']
    edges: list[Ref] = Field(min_length=1)
    distance: Positive
    angle: float = Field(default=45, gt=0, lt=90)

class Shell(Strict):
    kind: Literal['shell']
    faces: list[Ref] = Field(min_length=1)
    thickness: Positive
    outward: bool = False

class Thicken(Strict):
    kind: Literal['thicken']
    surfaces: list[Ref] = Field(min_length=1)
    thickness: Positive

class Sew(Strict):
    kind: Literal['sew']
    surfaces: list[Ref] = Field(min_length=1)
    solid: bool = False

class SurfaceOffset(Strict):
    kind: Literal['surface_offset']
    surfaces: list[Ref] = Field(min_length=1)
    distance: float

class Sheet(Strict):
    kind: Literal['sheet']
    sketch: Ref
    thickness: Positive

class Pattern(Strict):
    kind: Literal['linear_pattern','circular_pattern']
    features: list[Ref] = Field(min_length=1)
    axis: Literal['X','Y','Z'] | Ref = 'X'
    count: int = Field(ge=2,le=10000)
    step: Positive

class Mirror(Strict):
    kind: Literal['mirror']
    features: list[Ref] = Field(min_length=1)
    plane: Literal['XY','XZ','YZ'] | Ref = 'YZ'

class Boolean(Strict):
    kind: Literal['boolean']
    base: Ref
    bodies: list[Ref] = Field(min_length=1)
    mode: Literal['union','subtract','intersect']

class Rib(Strict):
    kind: Literal['rib']
    sketch: Ref
    thickness: Positive
    reverse: bool = False

class Draft(Strict):
    kind: Literal['draft']
    faces: list[Ref] = Field(min_length=1)
    plane: Literal['XY','XZ','YZ'] | Ref = 'XY'
    angle: float = Field(gt=-89,lt=89)

class TrimSurface(Strict):
    kind: Literal['surface_trim']
    surface: Ref
    cutter: Ref
    reverse: bool = False

class Bend(Strict):
    kind: Literal['bend']
    edges: list[Ref] = Field(min_length=1)
    length: Positive
    radius: Positive
    angle: float = Field(default=90,gt=0,lt=180)
    reverse: bool = False

class Unfold(Strict):
    kind: Literal['unfold']
    fixed_face: Ref
    bends: list[Ref] = Field(min_length=1)

class PlaneOffset(Strict):
    kind: Literal['plane_offset']
    plane: Literal['XY','XZ','YZ'] | Ref = 'XY'
    offset: float

class Spiral(Strict):
    kind: Literal['spiral']
    diameter: Positive
    pitch: Positive
    turns: Positive
    plane: Literal['XY','XZ','YZ'] | Ref = 'XY'

class AxisEdge(Strict):
    kind: Literal['axis_edge']
    edge: Ref

class CoordinateSystem(Strict):
    kind: Literal['coordinate_system']
    origin: Point3 = (0,0,0)
    euler_angles: Point3 = (0,0,0)

Feature = Annotated[Extrude | Revolve | Loft | Sweep | ThreadFeature | Fillet | Chamfer | Shell | Thicken | Sew | SurfaceOffset | Sheet | Pattern | Mirror | Boolean | Rib | Draft | TrimSurface | Bend | Unfold | PlaneOffset | Spiral | AxisEdge | CoordinateSystem, Field(discriminator='kind')]

class FeatureRequest(Strict):
    document_id: str
    name: str = Field(min_length=1)
    operation: Feature

class Selector(Strict):
    document_id: str
    kind: Literal['face','edge','body','feature']
    name: str | None = None
    geometry: Literal['plane','cylinder','sphere','cone','torus','line','circle','arc','spline'] | None = None
    near: Point3 | None = None
    normal: Point3 | None = None
    radius: Positive | None = None
    measure: Positive | None = None
    tolerance: Positive = 0.001
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=500)
    require_unique: bool = False
    component: Ref | None = None
    adjacent_to: Ref | None = None
    feature: Ref | None = None

class GeometryCount(Strict):
    query: Selector
    expected_count: int = Field(ge=0)

class ModelCheck(Strict):
    document_id: str
    body_count: int = Field(default=1,ge=0)
    bounds_mm: tuple[float,float,float,float,float,float] | None = None
    bounds_tolerance_mm: Positive = 0.001
    volume_mm3: Positive | None = None
    volume_tolerance_mm3: Positive = 0.001
    geometry_counts: list[GeometryCount] = Field(default_factory=list,max_length=100)

class DrawingGeometry(Strict):
    geometry: Primitive
    style: int = Field(default=1,ge=1,le=9)

class HatchRegion(Strict):
    boundaries: list[Primitive] = Field(min_length=1,max_length=1000)
    angle: float = 45
    step: Positive = 3

class DrawingLabel(Strict):
    text: str = Field(min_length=1,max_length=2000)
    position: Point2
    height: Positive = 3.5
    angle: float = 0

class LinearDrawingDimension(Strict):
    kind: Literal['linear']
    first: Point2
    second: Point2
    position: Point2
    orientation: Literal['horizontal','vertical','aligned'] = 'horizontal'
    prefix: str = ''
    suffix: str = ''

class RoundDrawingDimension(Strict):
    kind: Literal['diameter','radius']
    center: Point2
    radius: Positive
    angle: float = 45
    prefix: str = ''
    suffix: str = ''

class DrawingBatch(Strict):
    document_id: str
    view_number: int = Field(default=0,ge=0)
    geometry: list[DrawingGeometry] = Field(default_factory=list,max_length=10000)
    hatches: list[HatchRegion] = Field(default_factory=list,max_length=100)
    labels: list[DrawingLabel] = Field(default_factory=list,max_length=200)
    dimensions: list[Annotated[LinearDrawingDimension | RoundDrawingDimension,Field(discriminator='kind')]] = Field(default_factory=list,max_length=200)

class ViewPlacement(Strict):
    view_number: int = Field(ge=1)
    origin: Point2 | None = None
    scale: Positive | None = None
    angle: float | None = None
    detach_projection_alignment: bool = Field(default=False,description='Deprecated safety guard. True is rejected because MCP preserves projection alignment.')

class DrawingLayout(Strict):
    document_id: str
    views: list[ViewPlacement] = Field(min_length=1,max_length=100)

class ComponentInstance(Strict):
    name: str = Field(min_length=1,max_length=200)
    source: str
    origin: Point3 = (0,0,0)
    x_axis: Point3 = (1,0,0)
    z_axis: Point3 = (0,0,1)
    fixed: bool = True

    @model_validator(mode='after')
    def orthonormal(self):
        if any(abs(sum(v*v for v in a)-1)>1e-8 for a in (self.x_axis,self.z_axis)) or abs(sum(x*z for x,z in zip(self.x_axis,self.z_axis)))>1e-8:
            raise ValueError('Placement axes must be unit length and perpendicular')
        return self

class AssemblyCompose(Strict):
    document_id: str
    components: list[ComponentInstance] = Field(min_length=1,max_length=1000)

    @model_validator(mode='after')
    def unique_names(self):
        if len({c.name for c in self.components})!=len(self.components):raise ValueError('Component instance names must be unique')
        return self

class ScenarioStep(Strict):
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]*$')
    action: str
    arguments: dict
    depends_on: list[str] = Field(default_factory=list)

class Scenario(Strict):
    steps: list[ScenarioStep] = Field(min_length=1, max_length=500)
    checkpoint: bool = True
    response_detail: Literal['full','compact'] = 'full'
    outputs: dict[str,str] = Field(default_factory=dict)
    close_created: bool = False

    @model_validator(mode='after')
    def dependencies(self):
        seen=set()
        def references(value):
            if isinstance(value,str) and value.startswith('$'): yield value[1:].split('.')[0]
            elif isinstance(value,dict):
                for v in value.values(): yield from references(v)
            elif isinstance(value,list):
                for v in value: yield from references(v)
        for step in self.steps:
            if step.name in seen: raise ValueError('Duplicate step name')
            if not set(step.depends_on) <= seen: raise ValueError('Dependencies must refer to earlier steps')
            if not set(references(step.arguments)) <= seen: raise ValueError('Argument references must refer to earlier steps')
            seen.add(step.name)
        for value in self.outputs.values():
            if not value.startswith('$') or value[1:].split('.')[0] not in seen:
                raise ValueError('Outputs must reference an existing step')
        return self
