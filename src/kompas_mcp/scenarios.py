"""Small protocol responses without losing the complete CAD execution evidence."""
import hashlib
import json
import time
import uuid
from .errors import CadError
from .models import Scenario


def run_file(backend,path,parameters):
    source=backend.path(path)
    if source.suffix.lower()!='.json' or source.stat().st_size>4*1024*1024:
        raise CadError('invalid_scenario','Expected a JSON recipe below 4 MiB')
    raw=source.read_bytes()
    def substitute(value):
        if isinstance(value,str) and value.startswith('$params.'):
            key=value[len('$params.'):]
            if key not in parameters: raise CadError('invalid_parameters','Missing recipe parameter',parameter=key)
            return parameters[key]
        if isinstance(value,dict):return {k:substitute(v) for k,v in value.items()}
        if isinstance(value,list):return [substitute(v) for v in value]
        return value
    spec=substitute(json.loads(raw))
    spec['response_detail']='compact'
    result=run(backend,spec)
    result['recipe_sha256']=hashlib.sha256(raw).hexdigest()
    return result


def run(backend,scenario):
    spec=Scenario.model_validate(scenario)
    results={};checkpoints={};baseline=set(backend.documents);started=time.monotonic()
    allowed=set(backend.actions)-{'scenario_run','scenario_run_file','document_close','checkpoint_restore'}
    for step in spec.steps:
        if step.action not in allowed:raise CadError('invalid_scenario','Action not allowed',action=step.action)
    def expand(value):
        if isinstance(value,str) and value.startswith('$'):
            bits=value[1:].split('.');v=results[bits[0]]
            for bit in bits[1:]:v=v[int(bit)] if isinstance(v,list) else v[bit]
            if isinstance(v,dict) and set(v)=={'document_id','object_id','revision'}:
                entry=backend.registry.get(v['object_id'])
                if entry and entry['kind']=='feature':
                    if not entry['com'].Valid:raise CadError('stale_reference','Scenario feature is no longer valid')
                    v={**v,'revision':backend.doc(v['document_id'])['revision']}
            return v
        if isinstance(value,dict):return {k:expand(v) for k,v in value.items()}
        if isinstance(value,list):return [expand(v) for v in value]
        return value
    def report(payload):
        name='scenario-report-'+uuid.uuid4().hex+'.json';path=backend.root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'scenario':spec.model_dump(),**payload},ensure_ascii=False,indent=2),encoding='utf-8')
        return {'path':str(path),'resource':'kompas://artifact/'+name}
    failed_step=None
    def document_tokens(value):
        if isinstance(value,str) and value in backend.documents:yield value
        elif isinstance(value,dict):
            for v in value.values():yield from document_tokens(v)
        elif isinstance(value,list):
            for v in value:yield from document_tokens(v)
    try:
        for step in spec.steps:
            failed_step=step.name
            args=expand(step.arguments)
            if spec.checkpoint:
                for token in set(document_tokens(args)):
                    if token not in checkpoints:checkpoints[token]=backend.checkpoint(token)
            results[step.name]=backend.execute(step.action,args)
        failed_step='outputs'
        selected={k:expand(v) for k,v in spec.outputs.items()}
        closed=[];kept=[]
        if spec.close_created:
            for token in reversed(list(backend.documents)):
                if token in baseline:continue
                doc=backend.doc(token)['com']
                # Never discard an unsaved result just to reduce open tabs.
                if not doc.PathName or doc.Changed:kept.append(token);continue
                backend.document_close(token,discard_changes=False);closed.append(token)
        payload={'steps':results,'checkpoints':checkpoints,'outputs':selected,'closed_documents':closed,'kept_unsaved_documents':kept,'elapsed_seconds':round(time.monotonic()-started,3)}
        if spec.response_detail=='full':return payload
        artifact=report(payload)
        return {'status':'completed','completed_steps':len(results),'outputs':selected,'closed_documents':closed,'kept_unsaved_documents':kept,'elapsed_seconds':payload['elapsed_seconds'],'report':artifact}
    except Exception as exc:
        error=exc.result()['error'] if isinstance(exc,CadError) else {'code':'scenario_error','message':str(exc)}
        detail={'failed_step':failed_step,'error':error,'completed':results,'checkpoints':checkpoints,'restored':False}
        if spec.response_detail=='compact':
            artifact=report(detail)
            detail={'failed_step':failed_step,'error':error,'completed_steps':len(results),'report':artifact,'restored':False,'kept_created_documents':list(set(backend.documents)-baseline)}
        raise CadError('scenario_failed','Stopped; inspect the report before retrying. No automatic rollback.',**detail) from exc
