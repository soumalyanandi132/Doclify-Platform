"""Bounded, read-only GitHub snapshots. Repository code is never executed."""
import ast
import io
import json
import re
import zipfile
from pathlib import PurePosixPath
import httpx

MAX_ARCHIVE = 15_000_000
MAX_CONTENT = 2_000_000
MAX_FILE = 150_000
MAX_FILES = 300
LANGUAGES = {'.py':'Python', '.js':'JavaScript', '.jsx':'JavaScript', '.ts':'TypeScript',
             '.tsx':'TypeScript', '.md':'Markdown', '.toml':'TOML', '.json':'JSON',
             '.yaml':'YAML', '.yml':'YAML', '.css':'CSS', '.html':'HTML', '.txt':'Text'}
SKIP = {'.git','node_modules','vendor','dist','build','__pycache__','.venv','venv',
        '.next','coverage','.doclify','.idea'}

def parse_url(url):
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?', url.strip())
    if not match or any(x in {'.','..'} for x in match.groups()):
        raise ValueError('Use a public repository URL such as https://github.com/owner/repository')
    return match.group(1), match.group(2)

def describe(path, content):
    symbols, imports = [], []
    if path.endswith('.py'):
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbols.append({'name':node.name, 'kind':'class' if isinstance(node, ast.ClassDef) else 'function',
                                    'line':node.lineno, 'end_line':node.end_lineno})
                elif isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append('.' * node.level + (node.module or ''))
        except SyntaxError:
            pass
    return {'path':path, 'content':content, 'language':LANGUAGES.get(PurePosixPath(path).suffix,'Text'),
            'lines':len(content.splitlines()), 'symbols':symbols, 'imports':sorted(set(imports))}

def read_archive(data):
    files, skipped, total = [], 0, 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            parts = PurePosixPath(entry.filename).parts[1:]
            if entry.is_dir() or not parts:
                continue
            path = '/'.join(parts)
            name = parts[-1]
            if ('..' in parts or any(p in SKIP for p in parts) or name.startswith('.')
                or name in {'package-lock.json','uv.lock','yarn.lock','pnpm-lock.yaml'}
                or PurePosixPath(name).suffix not in LANGUAGES
                or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                skipped += 1
                continue
            if entry.file_size > MAX_FILE or len(files) >= MAX_FILES or total + entry.file_size > MAX_CONTENT:
                skipped += 1
                continue
            try:
                with archive.open(entry) as source:
                    raw = source.read(MAX_FILE + 1)
                if len(raw) > MAX_FILE or b'\x00' in raw:
                    skipped += 1
                    continue
                content = raw.decode('utf-8')
            except (UnicodeError, RuntimeError):
                skipped += 1
                continue
            total += len(raw)
            files.append(describe(path, content))
    if not files:
        raise ValueError('No supported text files found within the import limits.')
    return files, skipped

def fetch_snapshot(url, progress):
    owner, name = parse_url(url)
    headers = {'Accept':'application/vnd.github+json','User-Agent':'Doclify-Platform'}
    with httpx.Client(timeout=40, follow_redirects=False, headers=headers) as client:
        response = client.get(f'https://api.github.com/repos/{owner}/{name}')
        if response.status_code == 404:
            raise ValueError('Repository not found. This release supports public GitHub repositories.')
        if response.status_code in (403,429):
            raise ValueError('GitHub rate limit reached. Please retry later.')
        response.raise_for_status()
        metadata = response.json()
        if metadata.get('size',0) > 100_000:
            raise ValueError('This repository is too large. Choose a repository below 100 MB.')
        branch = metadata['default_branch']
        from urllib.parse import quote
        commit = client.get(f'https://api.github.com/repos/{owner}/{name}/commits/{quote(branch,safe="")}')
        commit.raise_for_status()
        sha = commit.json()['sha']
        progress('Downloading snapshot', 20)
        data = bytearray()
        with client.stream('GET',f'https://codeload.github.com/{owner}/{name}/zip/{sha}') as download:
            download.raise_for_status()
            for chunk in download.iter_bytes(65536):
                data.extend(chunk)
                if len(data) > MAX_ARCHIVE:
                    raise ValueError('Repository archive exceeds the 15 MB import limit.')
        progress('Reading source files',55)
        files, skipped = read_archive(data)
        return {'name':metadata['full_name'], 'description':metadata.get('description') or '',
                'branch':branch,'commit_sha':sha,'files':files,'skipped':skipped}

SAMPLE = {
 'README.md': '# Orbit API\n\nA sample task API for trying Doclify.\n\n## Run locally\n\npip install -r requirements.txt\nuvicorn app.main:app --reload\n\n## Authentication\nSend an API token in the Authorization header.\n',
 'app/main.py': 'from fastapi import FastAPI\nfrom app.routes import router\n\napp = FastAPI(title="Orbit API")\napp.include_router(router, prefix="/api")\n\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n',
 'app/auth.py': 'import os\nimport secrets\nfrom fastapi import Header, HTTPException\n\ndef require_token(authorization: str = Header(default="")):\n    expected = os.environ.get("ORBIT_API_TOKEN", "")\n    if not expected or not secrets.compare_digest(authorization, "Bearer " + expected):\n        raise HTTPException(status_code=401, detail="Invalid API token")\n    return True\n',
 'app/routes.py': 'from fastapi import APIRouter, Depends\nfrom app.auth import require_token\nfrom app.service import create_task, list_tasks\n\nrouter = APIRouter(dependencies=[Depends(require_token)])\n\n@router.get("/tasks")\ndef get_tasks():\n    return list_tasks()\n\n@router.post("/tasks")\ndef post_task(title: str):\n    return create_task(title)\n',
 'app/service.py': 'from uuid import uuid4\nfrom app.store import tasks\n\ndef create_task(title: str):\n    if not title.strip():\n        raise ValueError("Task title cannot be empty")\n    task = {"id": str(uuid4()), "title": title.strip(), "done": False}\n    tasks.append(task)\n    return task\n\ndef list_tasks():\n    return list(tasks)\n',
 'app/store.py': '# Sample in-memory store. Data is lost when the process restarts.\ntasks: list[dict] = []\n',
 'tests/test_service.py': 'from app.service import create_task\n\ndef test_create_task():\n    task = create_task("Write documentation")\n    assert task["title"] == "Write documentation"\n    assert task["done"] is False\n',
 'requirements.txt': 'fastapi\nuvicorn\npytest\n',
 '.github/workflows/test.yml': 'name: Test\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - run: pip install -r requirements.txt\n      - run: pytest\n',
}

def sample_snapshot():
    return {'name':'sample/orbit-api','description':'A small task API. Sample code for exploring the workspace.',
            'branch':'main','commit_sha':'sample-v1','files':[describe(p,c) for p,c in SAMPLE.items()], 'skipped':0}
