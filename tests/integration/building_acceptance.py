"""Native preview smoke test, not full architectural acceptance."""
import asyncio
import json
import os
from pathlib import Path
import sys
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


async def run(folder):
    folder=folder.resolve();folder.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parents[2]
    project={'id':'office-fixture','name':'Building adapter fixture','storeys':[{'id':'floor','height':3000}],
             'walls':[dict(id=f'w{i}',storey_id='floor',start=a,end=b,thickness=200) for i,(a,b) in enumerate([((0,0),(6000,0)),((6000,0),(6000,4000)),((6000,4000),(0,4000)),((0,4000),(0,0))])],
             'openings':[dict(id='d1',wall_id='w0',kind='door',offset=1000,width=900,height=2100)],
             'rooms':[dict(id='r1',storey_id='floor',number='1',name='Office',use='office',boundary_walls=['w0','w1','w2','w3'])],
             'furniture':[dict(id='desk1',storey_id='floor',catalog_id='desk',position=[3000,2000],width=1400,depth=700,height=750)],
             'assumptions':['Technical fixture dimensions chosen for testing only; regulatory suitability has not been checked']}
    async with Client(StdioServerParameters(command=sys.executable,args=[str(root/'run.py')],env={**os.environ,'KOMPAS_MCP_WORKSPACE':str(folder)})) as client:
        async def call(name,**args):
            result=await client.call_tool(name,args)
            data=result.structured_content or json.loads(result.content[0].text)
            if result.is_error:raise RuntimeError(data)
            return data['data']
        saved=await call('building_project_save',project=project)
        assert saved['revision']==1
        ambiguous=await call('norm_search',request={'query':'расстояние между перегородками'})
        assert ambiguous['state']=='insufficient_data'
        resources=await client.read_resource('kompas://building/office-fixture')
        assert resources.contents
        preview=await call('building_preview',project_id=project['id'])
        (folder/'native-preview.json').write_text(json.dumps(preview,ensure_ascii=False,indent=2),encoding='utf-8')
        assert preview['checks']['model']['passed'],preview['checks']['model']
        assert preview['checks']['layout']['passed'],preview['checks']['layout']
        for key in ('model','drawing'):
            reopened=await call('document_open_copy',source=preview[key]['path'])
            try:
                if key=='model':
                    check=await call('model_check',request={'document_id':reopened['document_id'],'body_count':7,'bounds_mm':[-100,-100,0,6100,4100,3000],'volume_mm3':12357000000,'volume_tolerance_mm3':1})
                    assert check['passed'],check
                else:
                    check=await call('drawing_layout_check',document_id=reopened['document_id'])
                    assert check['passed'],check
            finally:
                await call('document_close',document_id=reopened['document_id'],discard_changes=True)
        print(json.dumps({'status':'passed','path':str(folder/'native-preview.json'),'final_documents_left_open':preview['documents']}))


if __name__=='__main__':asyncio.run(run(Path(sys.argv[1])))
