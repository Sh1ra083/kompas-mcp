"""Version-pinned offline references. Metadata is never a verified numerical rule."""
import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal
from pydantic import Field, model_validator
from .models import Strict


class RuleCard(Strict):
    id: str
    document: str
    edition: str
    clause: str | None = None
    title: str
    source: str
    checked_on: date
    effective_from: date | None = None
    effective_until: date | None = None
    verification: Literal['metadata_only','text_reviewed','verified'] = 'metadata_only'
    source_sha256: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')
    amendments_reviewed: bool = False
    mandatory_basis: str | None = None
    conditions: dict[str, str | int | float | bool] = Field(default_factory=dict)
    exceptions: list[str] = Field(default_factory=list)
    measurement: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    units: str | None = None

    @model_validator(mode='after')
    def evidence(self):
        if self.verification=='verified' and (not self.clause or not self.source_sha256 or not self.amendments_reviewed or not self.effective_from):
            raise ValueError('Verified rules require clause, source hash, dates and amendment review')
        if self.minimum is not None and self.maximum is not None and self.minimum>self.maximum:
            raise ValueError('Invalid numeric interval')
        if (self.minimum is not None or self.maximum is not None) and (not self.units or not self.measurement):
            raise ValueError('Numeric rules require measurement and units')
        return self


class NormQuery(Strict):
    query: str = Field(min_length=1,max_length=1000)
    context: dict[str,str|int|float|bool] = Field(default_factory=dict)
    on_date: date = Field(default_factory=date.today)
    limit: int = Field(default=10,ge=1,le=50)
    offset: int = Field(default=0,ge=0)


def evaluate(card, context, measured, units, on_date):
    result={'rule_id':card.id,'state':'insufficient_data','measured':measured,
            'units':units,'source':card.source,'clause':card.clause}
    if card.verification!='verified':
        return {**result,'reason':'Full text and applicable amendments have not been verified'}
    if on_date<card.effective_from or card.effective_until and on_date>=card.effective_until:
        return {**result,'state':'not_applicable','reason':'Outside effective period'}
    missing=[k for k in card.conditions if k not in context]
    # A known exclusion is decisive even when other fields are missing.
    if any(k in context and context[k]!=v for k,v in card.conditions.items()):
        return {**result,'state':'not_applicable','reason':'Applicability conditions differ'}
    if missing:
        return {**result,'reason':'Missing applicability context','missing':missing}
    if card.exceptions:
        return {**result,'reason':'Exceptions require explicit review','exceptions':card.exceptions}
    if measured is None or units!=card.units or card.minimum is None and card.maximum is None:
        return {**result,'reason':'No compatible numerical measurement or automatic evaluator'}
    passed=(card.minimum is None or measured>=card.minimum) and (card.maximum is None or measured<=card.maximum)
    return {**result,'state':'passed' if passed else 'violated','minimum':card.minimum,'maximum':card.maximum}


class NormStore:
    def __init__(self, root):
        self.root=Path(root)
        raw=(Path(__file__).parent/'data'/'office_norms.json').read_bytes()
        self.pack=json.loads(raw)
        self.cards={c.id:c for c in (RuleCard.model_validate(x) for x in self.pack['cards'])}
        # Content-addressed derived index: no changes to pinned source cards.
        self.index=self.root/'norms'/(hashlib.sha256(raw).hexdigest()+'.sqlite')

    def search(self, request):
        text=request.query.lower()
        if ('перегород' in text or 'partition' in text) and ('расстояни' in text or 'distance' in text):
            needed=['measurement_kind','reference_surfaces','building_use','occupancy','accessible_route']
            missing=[k for k in needed if k not in request.context]
            if missing:
                return {'state':'insufficient_data','questions':missing,
                        'message':'Уточните: ширина коридора, размер помещения или проход между мебелью; какие чистовые поверхности ограничивают расстояние.',
                        'rules':[],'pack':self.pack['id']}
        self.index.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.index) as db:
            db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS cards USING fts5(id UNINDEXED, text, tokenize="unicode61")')
            if db.execute('SELECT count(*) FROM cards').fetchone()[0]!=len(self.cards):
                db.execute('DELETE FROM cards')
                db.executemany('INSERT INTO cards VALUES (?,?)',[(c.id,c.document+' '+c.title) for c in self.cards.values()])
            tokens=re.findall(r'\w+',request.query,flags=re.UNICODE)
            expression=' OR '.join('"'+t+'"*' for t in tokens)
            ids=[] if not expression else [r[0] for r in db.execute('SELECT id FROM cards WHERE cards MATCH ? ORDER BY rank LIMIT ? OFFSET ?',(expression,request.limit,request.offset))]
        def period(card):
            if card.effective_from is None:return 'unknown'
            return 'effective' if request.on_date>=card.effective_from and (card.effective_until is None or request.on_date<card.effective_until) else 'outside_effective_period'
        return {'pack':self.pack['id'],'checked_on':self.pack['checked_on'],'requested_date':request.on_date.isoformat(),
                'rules':[{'id':k,'document':self.cards[k].document,'title':self.cards[k].title,
                          'verification':self.cards[k].verification,'period':period(self.cards[k]),'resource':f'kompas://norm/{k}'} for k in ids],
                'offset':request.offset,'limit':request.limit,
                'warning':'Search includes document metadata. Only verified rule cards may be used for checks.'}
