"""Exercise the delivered executable over MCP, including geometry and image resources."""
import asyncio,json,os,pathlib,sys,argparse
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

async def run(executable,folder):
    root=pathlib.Path(__file__).resolve().parents[2];folder.mkdir(parents=True,exist_ok=True)
    async with Client(StdioServerParameters(command=str(executable),env={**os.environ,'KOMPAS_MCP_WORKSPACE':str(folder)})) as c:
        async def call(name,args):
            r=await c.call_tool(name,args)
            if r.is_error:raise AssertionError(r)
            data=r.structured_content
            if data is None:data=json.loads(r.content[0].text)
            return data['data']
        tools=await c.list_tools();assert len(tools.tools)>=39
        baseline=(await call('connect',{}))['documents_open']
        scenario=json.loads((root/'examples/plate.json').read_text(encoding='utf-8'))
        result=await call('scenario_run',{'scenario':scenario});d=result['steps']['document']['document_id']
        info=await call('document_info',{'document_id':d});assert abs(info['volume_mm3']-48000)<1e-5
        dr=await call('drawing_create',{'source_document_id':d});drid=dr['document_id'] if 'document_id' in dr else dr['document']['document_id']
        png=await call('preview',{'document_id':drid,'path':'preview.png'})
        image=await c.read_resource(png['resource_uri']);assert image.contents[0].mime_type=='image/png'
        await c.read_resource('kompas://operations')
        bad=await c.call_tool('feature_edit',{'reference':result['steps']['body']['reference'],'parameters':{'depth':-1}});assert bad.is_error
        await call('document_close',{'document_id':drid,'discard_changes':True})
        await call('document_close',{'document_id':d,'discard_changes':True})
        assert (await call('connect',{}))['documents_open']==baseline,'Test leaked a CAD document'
        report={'passed':True,'tool_count':len(tools.tools),'volume_mm3':info['volume_mm3'],'image_resource':png['resource_uri'],'checks':['stdio','schemas','scenario','checkpoint','native_geometry','STEP','PNG_resource','error']}
        (folder/'protocol.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('executable',type=pathlib.Path);p.add_argument('output',type=pathlib.Path);a=p.parse_args();asyncio.run(run(a.executable.resolve(),a.output.resolve()))
