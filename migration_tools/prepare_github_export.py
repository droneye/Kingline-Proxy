"""Prepare a source-only public snapshot without reading production credentials.

Optional --redactions is a private JSON string-to-string map for replacing local
operator paths and installation-specific values with documentation examples.
The working source is never modified. Only a dedicated fresh clone may be used
as the output; its Git history is retained while its source tree is replaced.
"""
import argparse
import ast
import json
import re
import shutil
from pathlib import Path


SKIP = {'private', '.git', '.venv', 'venv', 'node_modules', '__pycache__',
        'dist', '.pytest_cache', '.mypy_cache', 'logs', 'uploads',
        'support_uploads', 'secrets', 'screenshots'}
SUFFIXES = {'.py', '.js', '.jsx', '.mjs', '.ts', '.tsx', '.css', '.html',
            '.md', '.txt', '.json', '.yml', '.yaml', '.lock', '.svg',
            '.png', '.jpg', '.jpeg', '.webp', '.ico', '.woff', '.woff2',
            '.sh', '.command', '.service', '.timer', '.example'}
ROOT_SUFFIXES = {'.py', '.sh', '.command', '.service', '.md'}
PRIVATE_NAMES = {'traffic_data.json', 'DOMAIN-TRANSFER-STATUS.md'}
SENSITIVE = {
    'bot_token': re.compile(r'\b\d{7,12}:[A-Za-z0-9_-]{30,50}\b'),
    'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s*\n[A-Za-z0-9+/=\r\n]{80,}'),
    'jwt': re.compile(r'\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{20,}\b'),
}


def selected(root):
    for path in root.iterdir():
        if path.is_file() and not path.is_symlink() and (path.suffix in ROOT_SUFFIXES or path.name == '.gitignore'):
            yield path
    for folder in ('node_manager', 'migration_tools', 'staging/bot',
                   'staging/site', 'staging/ops', 'staging/docs'):
        for path in (root/folder).rglob('*'):
            relative=path.relative_to(root)
            if not path.is_file() or path.is_symlink() or SKIP.intersection(relative.parts):
                continue
            if path.name in PRIVATE_NAMES or (path.name.startswith('.') and path.name != '.env.example'):
                continue
            if path.suffix in SUFFIXES:
                yield path
    for name in ('Dockerfile', 'compose.yaml', 'requirements.txt', 'requirements.lock',
                 'patch_legacy.py', 'test_provisioning.py', '.env.example'):
        path=root/'staging'/name
        if path.is_file():yield path


def prepare(root, output, redactions):
    if output == root or root.is_relative_to(output) or not (output/'.git').exists():
        raise SystemExit('Output must be a dedicated fresh Git clone, not the working project')
    changes={}
    for path in sorted(set(selected(root))):
        relative=path.relative_to(root)
        content=path.read_bytes()
        if b'\0' not in content and path.suffix not in {'.png','.jpg','.jpeg','.webp','.ico','.woff','.woff2'}:
            text=content.decode('utf-8')
            for old,new in sorted(redactions.items(),key=lambda item:len(item[0]),reverse=True):
                text=text.replace(old,new)
            # A public checkout never grants access to the deployment owner's ID.
            text=re.sub(r"(os\.environ\.get\('SANDBOX_ALLOWED_TG_IDS',)'[^']*'",r"\1''",text)
            for kind,pattern in SENSITIVE.items():
                if pattern.search(text):
                    raise SystemExit(f'Public export refused: {kind} in {relative}')
            if path.suffix=='.py':ast.parse(text,filename=str(relative))
            content=text.encode()
        changes[str(relative)]=content
    # The clone belongs to this export workflow; leave all history under .git.
    for path in output.iterdir():
        if path.name=='.git':continue
        if path.is_dir():shutil.rmtree(path)
        else:path.unlink()
    for name,content in changes.items():
        path=output/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o755 if (root/name).stat().st_mode & 0o111 else 0o644)
    print(json.dumps({'files':len(changes),'bytes':sum(map(len,changes.values())),
                      'credentials_and_runtime_data_excluded':True}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--redactions',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    redactions=json.loads(args.redactions.read_text()) if args.redactions else {}
    assert isinstance(redactions,dict) and all(isinstance(k,str) and k and isinstance(v,str) for k,v in redactions.items())
    prepare(root,args.output.resolve(),redactions)
