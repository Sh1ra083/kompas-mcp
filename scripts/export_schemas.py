from pathlib import Path
import sys,json
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
from kompas_mcp.server import server
schemas={tool.name:tool.parameters for tool in server._tool_manager.list_tools()}
(root/'src/kompas_mcp/schemas.json').write_text(json.dumps(schemas,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
print(f'Exported {len(schemas)} tool schemas')
