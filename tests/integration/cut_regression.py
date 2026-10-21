"""Public-MCP integration regression: cut directions, edits and no-op rejection."""
import argparse,asyncio,json,math,os,pathlib,sys
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

async def run(exe,folder):
    folder.mkdir(parents=True,exist_ok=True);checks=[]
    async with Client(StdioServerParameters(command=sys.executable if exe.suffix=='.py' else str(exe),args=[str(exe)] if exe.suffix=='.py' else [],env={**os.environ,'KOMPAS_MCP_WORKSPACE':str(folder)})) as client:
        async def raw(tool,**args):
            r=await client.call_tool(tool,args)
            return r.is_error,r.structured_content or json.loads(r.content[0].text)
        async def call(tool,**args):
            error,data=await raw(tool,**args);assert not error,data
            return data['data']
        baseline=(await call('connect'))['documents_open']
        try:
            for reverse in (False,True):
                d=(await call('document_create',name='Cut regression'))['document_id']
                s=await call('sketch_create',request=dict(document_id=d,name='base',primitives=[dict(kind='rectangle',width=20,height=20)]))
                await call('feature_create',request=dict(document_id=d,name='block',operation=dict(kind='extrude',sketch=s['reference'],depth=10)))
                s=await call('sketch_create',request=dict(document_id=d,name='hole',offset=10 if reverse else 0,primitives=[dict(kind='circle',center=[10,10],radius=2)]))
                cut=await call('feature_create',request=dict(document_id=d,name='cut',operation=dict(kind='extrude',sketch=s['reference'],depth=2,cut=True,reverse=reverse)))
                assert abs(cut['verification']['removed_volume_mm3']-math.pi*4*2)<1e-5,cut
                edited=await call('feature_edit',reference=cut['reference'],parameters={'depth':3})
                assert abs(edited['document']['volume_mm3']-(4000-math.pi*4*3))<1e-5,edited
                await call('model_check',request=dict(document_id=d,body_count=1,volume_mm3=4000-math.pi*4*3))
                error,data=await raw('model_check',request=dict(document_id=d,body_count=2))
                assert error and data['error']['code']=='verification_failed',data
                checks.append(dict(reverse=reverse,cut_volume_mm3=cut['verification']['removed_volume_mm3'],edited_volume_mm3=edited['document']['volume_mm3'],negative_check_rejected=True))
                await call('document_close',document_id=d,discard_changes=True)
            # A sketch on Z=0 directed downward merely touches the block.
            d=(await call('document_create',name='No-op rejection'))['document_id']
            s=await call('sketch_create',request=dict(document_id=d,name='base',primitives=[dict(kind='rectangle',width=20,height=20)]))
            await call('feature_create',request=dict(document_id=d,name='block',operation=dict(kind='extrude',sketch=s['reference'],depth=10)))
            s=await call('sketch_create',request=dict(document_id=d,name='wrong direction',primitives=[dict(kind='circle',center=[10,10],radius=2)]))
            error,data=await raw('feature_create',request=dict(document_id=d,name='no-op',operation=dict(kind='extrude',sketch=s['reference'],depth=2,cut=True,reverse=True)))
            assert error and data['error']['code']=='no_geometry_change',data
            assert abs((await call('document_info',document_id=d))['volume_mm3']-4000)<1e-5
            checks.append(dict(no_op_rejected=True,error=data['error']))
        finally:
            for d in reversed((await call('connect'))['owned_documents']):await call('document_close',document_id=d,discard_changes=True)
            after=(await call('connect'))['documents_open'];assert after==baseline
        report=dict(passed=True,checks=checks,documents_before=baseline,documents_after=after)
        (folder/'cut-regression.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('exe',type=pathlib.Path);p.add_argument('output',type=pathlib.Path);a=p.parse_args();asyncio.run(run(a.exe.resolve(),a.output.resolve()))
