import asyncio,json,os,sys
from pathlib import Path
import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

@pytest.mark.skipif(os.environ.get('KOMPAS_INTEGRATION')!='1',reason='Requires Windows KOMPAS integration environment')
def test_stdio_lifecycle(tmp_path):
    async def run():
        root=Path(__file__).resolve().parents[1]
        executable=os.environ.get('KOMPAS_MCP_EXE')
        params=StdioServerParameters(command=executable or sys.executable,args=[] if executable else [str(root/'run.py')],env={**os.environ,'KOMPAS_MCP_WORKSPACE':str(tmp_path)})
        async with Client(params) as client:
            tools=await client.list_tools()
            assert len(tools.tools)>=35
            response=await client.call_tool('capabilities',{})
            assert not response.is_error
            doc=await client.call_tool('document_create',{'name':'Protocol smoke'})
            assert not doc.is_error,doc
            payload=doc.structured_content or json.loads(doc.content[0].text)
            token=payload['data']['document_id']
            closed=await client.call_tool('document_close',{'document_id':token,'discard_changes':True})
            assert not closed.is_error
            bad=await client.call_tool('document_info',{'document_id':'nonexistent'})
            assert bad.is_error
            invalid=await client.call_tool('sketch_create',{'request':{'document_id':'a','name':'x','primitives':[{'kind':'circle','radius':-1}]}})
            assert invalid.is_error
            await client.list_resources()
    asyncio.run(run())
