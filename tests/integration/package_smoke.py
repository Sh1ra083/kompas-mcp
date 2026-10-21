import asyncio
import json
import os
from pathlib import Path
import sys
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


async def run(exe,folder):
    async with Client(StdioServerParameters(command=str(exe.resolve()),args=[],env={**os.environ,'KOMPAS_MCP_WORKSPACE':str(folder.resolve())})) as c:
        tools=await c.list_tools()
        assert {'building_preview','building_project_save','norm_search','building_furniture_catalog'} <= {t.name for t in tools.tools}
        for name,args in [('norm_search',{'request':{'query':'расстояние между перегородками'}}),('building_furniture_catalog',{})]:
            result=await c.call_tool(name,args)
            assert not result.is_error,result
            data=(result.structured_content or json.loads(result.content[0].text))['data']
            if name=='norm_search':assert data['state']=='insufficient_data'
            else:assert len(data['items'])==8
        resource=await c.read_resource('kompas://norm/gost-21-101-2026')
        assert resource.contents
        print('Packaged MCP: tools, normative ambiguity, catalog, resources passed')


if __name__=='__main__':asyncio.run(run(Path(sys.argv[1]),Path(sys.argv[2])))
