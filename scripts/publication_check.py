"""Check tracked/staged publication content; never print matched secret values.

This is a bounded guard against accidental publication, not a security audit.
Run after git add and before committing. CI checks committed source content.
"""
from pathlib import Path, PurePosixPath
import json
import re
import subprocess
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]
ALLOWED={'.py','.json','.md','.toml','.txt','.ps1','.cmd','.spec','.yml','.yaml'}
SPECIAL={'.gitignore','.gitattributes','LICENSE'}
FORBIDDEN_DIRS={'__pycache__','.com-cache','.venv','cad-workspace','acceptance','work','tmp','final'}
PATTERNS={
    'GitHub token': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'AWS access key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'personal Windows path': re.compile(r'(?i)[A-Z]:(?:[\\/])+Users(?:[\\/])+[^\\/\s"\']+'),
    'credential URL': re.compile(r'https?://[^\s/@:]+:[^\s/@]+@'),
}


def main():
    listed=subprocess.run(['git','ls-files','-z','--cached'],cwd=ROOT,check=True,capture_output=True).stdout
    files=[x.decode('utf-8') for x in listed.split(b'\0') if x]
    if not files:raise SystemExit('No tracked/staged publication files. Run git add with an explicit file list first.')
    failures=[]
    for name in files:
        path=PurePosixPath(name)
        if len(path.parts)==1 and path.suffix=='.md':
            failures.append((name,'root Markdown belongs in docs/'))
        if any(p in FORBIDDEN_DIRS or p.startswith('build-') or p.startswith('dist-') or p.endswith('.egg-info') for p in path.parts) or path.parts[0] in {'build','dist'}:
            failures.append((name,'generated/private directory'))
        if path.suffix not in ALLOWED and path.name not in SPECIAL:
            failures.append((name,'unexpected file type'))
        content=subprocess.run(['git','show',':'+name],cwd=ROOT,check=True,capture_output=True).stdout
        if len(content)>1024*1024:failures.append((name,'file exceeds 1 MiB source limit'))
        try:text=content.decode('utf-8-sig')
        except UnicodeDecodeError:
            failures.append((name,'not UTF-8 source'));continue
        for label,pattern in PATTERNS.items():
            if pattern.search(text):failures.append((name,label))
    project=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))
    version=re.search(r'__version__\s*=\s*[\'"]([^\'"]+)',(ROOT/'src/kompas_mcp/__init__.py').read_text(encoding='utf-8')).group(1)
    if version!=project['project']['version']:failures.append(('pyproject.toml','package versions differ'))
    for file in (ROOT/'src/kompas_mcp/data').glob('*.json'):json.loads(file.read_text(encoding='utf-8'))
    if failures:
        for name,reason in failures:print(f'FAIL {name}: {reason}')
        raise SystemExit(1)
    print(f'Publication content passed: {len(files)} UTF-8 source files; version {version}. No secret values printed.')


if __name__=='__main__':main()
