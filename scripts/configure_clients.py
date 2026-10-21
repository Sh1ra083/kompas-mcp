"""Generate configurations; optionally append one Codex server without rewriting other settings."""
import argparse,datetime,json,pathlib,tomllib

def main():
    p=argparse.ArgumentParser();p.add_argument('--install-codex',action='store_true');a=p.parse_args()
    root=pathlib.Path(__file__).resolve().parents[1];exe=root/'dist/kompas-mcp/kompas-mcp.exe';workspace=root/'cad-workspace'
    if not exe.is_file():raise SystemExit('Build and verify executable first')
    config={'command':str(exe),'env':{'KOMPAS_MCP_WORKSPACE':str(workspace)}}
    dest=root/'configs';dest.mkdir(exist_ok=True)
    (dest/'mcp.json').write_text(json.dumps({'mcpServers':{'kompas-local':config}},ensure_ascii=False,indent=2),encoding='utf-8')
    text='\n[mcp_servers.kompas-local]\ncommand = '+json.dumps(str(exe))+'\nstartup_timeout_sec = 30\ntool_timeout_sec = 150\n\n[mcp_servers.kompas-local.env]\nKOMPAS_MCP_WORKSPACE = '+json.dumps(str(workspace))+'\n'
    tomllib.loads(text);(dest/'codex.toml').write_text(text,encoding='utf-8')
    if a.install_codex:
        import os
        codex_dir=pathlib.Path(os.environ.get('CODEX_HOME',str(pathlib.Path.home()/'.codex')))
        target=codex_dir/'config.toml';original=target.read_bytes() if target.exists() else b''
        parsed=tomllib.loads(original.decode('utf-8-sig'))
        if 'kompas-local' in parsed.get('mcp_servers',{}):
            existing=parsed['mcp_servers']['kompas-local']
            if existing.get('command')==str(exe):print('Already configured');return
            raise SystemExit('Existing kompas-local differs; refusing to overwrite it')
        combined=original+b'\n'+text.encode('utf-8');tomllib.loads(combined.decode('utf-8-sig'))
        backup=target.with_name('config.toml.kompas-backup-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
        if original:backup.write_bytes(original)
        if target.exists() and target.read_bytes()!=original:raise SystemExit('Config changed concurrently; retry after inspection')
        target.write_bytes(combined)
        assert target.read_bytes().startswith(original)
        print('Configured kompas-local; existing config preserved; backup:',backup)
    else:print('Configuration examples generated')

if __name__=='__main__':main()
