"""Offline semantic building projects. Millimetres, degrees, explicit revisions.

This module deliberately does not import COM. Drawing output is a separate adapter.
"""
import hashlib
import json
import math
import os
import re
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator
from .models import Strict, Point2, Positive

ID = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')]


class Storey(Strict):
    id: ID
    elevation: float = 0
    height: Positive = 3000


class Wall(Strict):
    id: ID
    storey_id: ID
    start: Point2
    end: Point2
    thickness: Positive
    kind: Literal['wall', 'partition'] = 'wall'

    @model_validator(mode='after')
    def nonzero(self):
        if math.dist(self.start, self.end) < 1e-6:
            raise ValueError('Wall must have nonzero length')
        return self


class Opening(Strict):
    id: ID
    wall_id: ID
    kind: Literal['door', 'window', 'opening']
    offset: float = Field(ge=0, description='Distance from wall start to first jamb')
    width: Positive
    height: Positive
    sill: float = Field(default=0, ge=0)
    hinge: Literal['start', 'end'] = 'start'
    swing_side: Literal[-1, 1] = 1


class Room(Strict):
    id: ID
    storey_id: ID
    number: str
    name: str
    use: Literal['office', 'open_office', 'meeting', 'corridor', 'toilet']
    boundary_walls: list[ID] = Field(min_length=3)


class Furniture(Strict):
    id: ID
    storey_id: ID
    catalog_id: Literal['desk', 'meeting_table', 'office_chair', 'visitor_chair', 'cabinet', 'pedestal', 'toilet', 'basin']
    position: Point2
    angle: float = 0
    width: Positive
    depth: Positive
    height: Positive
    use_clearance: float = Field(default=0, ge=0, description='User-specified clearance; not a normative default')


class BuildingProject(Strict):
    id: ID
    name: str
    revision: int = Field(default=0, ge=0)
    schema_version: Literal[1] = 1
    units: Literal['mm'] = 'mm'
    rule_pack: str = 'ru-office-2026-09-15-draft'
    storeys: list[Storey] = Field(default_factory=list)
    walls: list[Wall] = Field(default_factory=list)
    openings: list[Opening] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    furniture: list[Furniture] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def relations(self):
        all_objects = self.storeys + self.walls + self.openings + self.rooms + self.furniture
        if len({o.id for o in all_objects}) != len(all_objects):
            raise ValueError('Object IDs must be globally unique')
        levels = {s.id: s for s in self.storeys}
        walls = {w.id: w for w in self.walls}
        for obj in self.walls + self.rooms + self.furniture:
            if obj.storey_id not in levels:
                raise ValueError(f'{obj.id}: unknown storey')
        for opening in self.openings:
            wall = walls.get(opening.wall_id)
            if wall is None:
                raise ValueError(f'{opening.id}: unknown host wall')
            if opening.offset + opening.width > math.dist(wall.start, wall.end) + 1e-6:
                raise ValueError(f'{opening.id}: opening exceeds host wall')
            if opening.sill + opening.height > levels[wall.storey_id].height + 1e-6:
                raise ValueError(f'{opening.id}: opening exceeds storey height')
        for i, a in enumerate(self.openings):
            for b in self.openings[i+1:]:
                if a.wall_id == b.wall_id and min(a.offset+a.width,b.offset+b.width) > max(a.offset,b.offset) and min(a.sill+a.height,b.sill+b.height) > max(a.sill,b.sill):
                    raise ValueError(f'Overlapping openings: {a.id}, {b.id}')
        for room in self.rooms:
            if len(set(room.boundary_walls)) != len(room.boundary_walls):
                raise ValueError(f'{room.id}: repeated boundary wall')
            if any(w not in walls or walls[w].storey_id != room.storey_id for w in room.boundary_walls):
                raise ValueError(f'{room.id}: invalid boundary wall')
        return self


def wall_point(wall, offset, normal=0):
    dx, dy = wall.end[0]-wall.start[0], wall.end[1]-wall.start[1]
    length = math.hypot(dx, dy)
    return (wall.start[0]+dx/length*offset-dy/length*normal,
            wall.start[1]+dy/length*offset+dx/length*normal)


def footprint(item, clearance=0):
    a = math.radians(item.angle)
    c, s = math.cos(a), math.sin(a)
    w, d = item.width/2+clearance, item.depth/2+clearance
    return [(item.position[0]+x*c-y*s,item.position[1]+x*s+y*c)
            for x,y in [(-w,-d),(w,-d),(w,d),(-w,d)]]


def convex_overlap(a, b):
    """Positive area intersection; touching boundaries do not collide."""
    for poly in (a,b):
        for p,q in zip(poly,poly[1:]+poly[:1]):
            axis = (p[1]-q[1],q[0]-p[0])
            pa=[x*axis[0]+y*axis[1] for x,y in a]
            pb=[x*axis[0]+y*axis[1] for x,y in b]
            if min(max(pa),max(pb))-max(min(pa),min(pb)) <= 1e-7:
                return False
    return True


def room_geometry(project, room):
    """Interior intersections for a simple convex wall loop. Unsupported loops fail explicitly."""
    walls={w.id:w for w in project.walls}
    pending=[walls[k] for k in room.boundary_walls]
    first=pending.pop(0)
    ordered=[(first.start,first.end,first.thickness)]
    while pending:
        end=ordered[-1][1]
        matches=[(i,w,False) for i,w in enumerate(pending) if math.dist(w.start,end)<1e-5]
        matches += [(i,w,True) for i,w in enumerate(pending) if math.dist(w.end,end)<1e-5]
        if len(matches)!=1:
            return {'state':'insufficient_data','reason':'Boundary walls must form one unambiguous closed loop'}
        i,w,reverse=matches[0];pending.pop(i)
        ordered.append((w.end if reverse else w.start,w.start if reverse else w.end,w.thickness))
    if math.dist(ordered[-1][1],ordered[0][0])>1e-5:
        return {'state':'violated','reason':'Room boundary is not closed'}
    signed=sum(a[0]*b[1]-b[0]*a[1] for a,b,_ in ordered)
    if abs(signed)<1e-6:
        return {'state':'violated','reason':'Degenerate room'}
    sign=1 if signed>0 else -1
    shifted=[]
    for a,b,t in ordered:
        dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
        shifted.append(((a[0]-sign*dy/length*t/2,a[1]+sign*dx/length*t/2),(dx,dy)))
    vertices=[]
    for i,(p,u) in enumerate(shifted):
        q,v=shifted[i-1];cross=u[0]*v[1]-u[1]*v[0]
        if abs(cross)<1e-8:
            return {'state':'insufficient_data','reason':'Collinear room boundaries require merging'}
        if sign*cross>=0:
            return {'state':'insufficient_data','reason':'Only convex room loops supported by this adapter'}
        t=((q[0]-p[0])*v[1]-(q[1]-p[1])*v[0])/cross
        vertices.append((p[0]+t*u[0],p[1]+t*u[1]))
    for v in vertices:
        for a,b,t in ordered:
            dx,dy=b[0]-a[0],b[1]-a[1]
            if sign*(dx*(v[1]-a[1])-dy*(v[0]-a[0])) < t/2*math.hypot(dx,dy)-1e-5:
                return {'state':'violated','reason':'Wall thickness consumes room interior'}
    area=abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(vertices,vertices[1:]+vertices[:1])))/2e6
    return {'state':'passed','area_m2':area,'interior_polygon':vertices,'basis':'Clear interior polygon; no deductions for columns or finishes'}


def inspect_project(project):
    findings=[]
    for i,a in enumerate(project.furniture):
        for b in project.furniture[i+1:]:
            if a.storey_id==b.storey_id and convex_overlap(footprint(a),footprint(b)):
                findings.append({'state':'violated','kind':'furniture_collision','objects':[a.id,b.id]})
            elif a.storey_id==b.storey_id and (a.use_clearance or b.use_clearance) and convex_overlap(footprint(a,a.use_clearance),footprint(b,b.use_clearance)):
                findings.append({'state':'violated','kind':'specified_usage_zones_overlap','objects':[a.id,b.id],'basis':'User-specified clearance, not a regulatory limit'})
    for item in project.furniture:
        for wall in project.walls:
            if item.storey_id!=wall.storey_id: continue
            # Test solid wall intervals, retaining openings at furniture height.
            cuts=sorted((o.offset,o.offset+o.width) for o in project.openings if o.wall_id==wall.id and o.sill==0 and o.height>=item.height)
            start=0
            for lo,hi in cuts+[(math.dist(wall.start,wall.end),math.dist(wall.start,wall.end))]:
                if lo>start:
                    poly=[wall_point(wall,start,-wall.thickness/2),wall_point(wall,lo,-wall.thickness/2),wall_point(wall,lo,wall.thickness/2),wall_point(wall,start,wall.thickness/2)]
                    if convex_overlap(footprint(item),poly):
                        findings.append({'state':'violated','kind':'wall_collision','objects':[item.id,wall.id]})
                start=max(start,hi)
    walls={w.id:w for w in project.walls}
    for opening in project.openings:
        if opening.kind!='door':continue
        wall=walls[opening.wall_id]
        hinge=wall_point(wall,opening.offset if opening.hinge=='start' else opening.offset+opening.width)
        length=math.dist(wall.start,wall.end)
        sign=1 if opening.hinge=='start' else -1
        along=((wall.end[0]-wall.start[0])/length*sign,(wall.end[1]-wall.start[1])/length*sign)
        normal=(-(wall.end[1]-wall.start[1])/length*opening.swing_side,(wall.end[0]-wall.start[0])/length*opening.swing_side)
        for item in project.furniture:
            if item.storey_id!=wall.storey_id:continue
            # Exact circular-sector intersection: clip to positive hinge quadrant,
            # then compare the nearest point of the clipped convex polygon to radius.
            poly=[((x-hinge[0])*along[0]+(y-hinge[1])*along[1],(x-hinge[0])*normal[0]+(y-hinge[1])*normal[1]) for x,y in footprint(item)]
            for axis in (0,1):
                clipped=[]
                for a,b in zip(poly,poly[1:]+poly[:1]):
                    inside_a=a[axis]>=0;inside_b=b[axis]>=0
                    if inside_a:clipped.append(a)
                    if inside_a!=inside_b:
                        t=-a[axis]/(b[axis]-a[axis]);clipped.append((a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])))
                poly=clipped
            if len(poly)<3:continue
            distance=float('inf')
            for a,b in zip(poly,poly[1:]+poly[:1]):
                dx,dy=b[0]-a[0],b[1]-a[1];den=dx*dx+dy*dy
                t=max(0,min(1,-(a[0]*dx+a[1]*dy)/den)) if den else 0
                distance=min(distance,math.hypot(a[0]+t*dx,a[1]+t*dy))
            if distance<opening.width-1e-6:
                findings.append({'state':'violated','kind':'door_swing_blocked','objects':[opening.id,item.id],'basis':'90-degree single-leaf swing, no frame allowances'})
    return {'project_id':project.id,'revision':project.revision,
            'rooms':{r.id:room_geometry(project,r) for r in project.rooms},
            'findings':findings,'unchecked':['door_to_wall_swing','egress_paths','accessibility','structural_safety'],
            'normative_compliance':'insufficient_data'}


class ProjectStore:
    def __init__(self, root):
        self.root=Path(root)/'buildings'
        self.lock=threading.RLock()

    def path(self, project_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',project_id):
            raise ValueError('Invalid project ID')
        path=self.root/(project_id+'.json')
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('Project path escapes storage')
        return path

    @contextmanager
    def exclusive(self):
        self.root.mkdir(parents=True,exist_ok=True)
        path=self.root/'.write.lock'
        try:
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except FileExistsError as exc:
            raise ValueError('Project store busy; inspect lock owner before retry') from exc
        try:
            os.write(fd,str(os.getpid()).encode())
            yield
        finally:
            os.close(fd);path.unlink()

    def load(self, project_id):
        return BuildingProject.model_validate_json(self.path(project_id).read_text(encoding='utf-8'))

    def save(self, project, expected_revision):
        project=BuildingProject.model_validate(project.model_dump())
        with self.lock, self.exclusive():
            path=self.path(project.id)
            if path.exists():
                old=self.load(project.id)
                if expected_revision!=old.revision:
                    raise ValueError(f'Stale revision: current={old.revision}')
                if old.rule_pack!=project.rule_pack:
                    raise ValueError('Normative pack migration requires an explicit migration workflow')
                revision=old.revision+1
            else:
                if expected_revision is not None:
                    raise ValueError('Project does not exist')
                revision=1
            saved=project.model_copy(update={'revision':revision})
            self.root.mkdir(parents=True,exist_ok=True)
            payload=saved.model_dump_json(indent=2)
            # Keep previous revision before replacing the canonical file.
            if path.exists():
                history=self.root/'history';history.mkdir(exist_ok=True)
                (history/f'{project.id}.{revision-1}.json').write_bytes(path.read_bytes())
            temp=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
            try:
                with temp.open('w',encoding='utf-8') as stream:
                    stream.write(payload);stream.flush();os.fsync(stream.fileno())
                os.replace(temp,path)
            finally:
                if temp.exists():temp.unlink()
            return {'project_id':saved.id,'revision':revision,'sha256':hashlib.sha256(payload.encode()).hexdigest(),
                    'resource':f'kompas://building/{saved.id}','checks':inspect_project(saved)}
