"""Dependency-light local web server for the Doclify workspace."""
import json
import logging
import mimetypes
import os
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from doclify.core import generate_documentation
from doclify.schema.schema import LLMConfig
from .repository import fetch_snapshot, sample_snapshot, parse_url
from .store import Store

log = logging.getLogger(__name__)


def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid4().hex


class Workspace:
    def __init__(self, data_dir=None, snapshot_loader=None, ai_generate=None):
        self.store = Store(data_dir or os.environ.get("DOCLIFY_DATA_DIR", "work/doclify-data"))
        self.snapshot_loader = snapshot_loader or fetch_snapshot
        self.ai_generate = ai_generate
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="doclify")
        self.gate = threading.Lock()
        self.store.execute(
            "UPDATE jobs SET status='failed',stage='Interrupted',error='Server restarted. Please retry.',finished_at=? "
            "WHERE status IN ('queued','running')",
            (now(),),
        )
        self.store.execute("UPDATE repositories SET status='failed' WHERE status='indexing'")

    def repository(self, repo_id):
        repo = self.store.one("SELECT * FROM repositories WHERE id=?", (repo_id,))
        if not repo:
            raise ValueError("Repository not found.")
        return repo

    def ready(self, repo_id):
        repo = self.repository(repo_id)
        if repo["status"] != "ready":
            raise RuntimeError("Wait for repository indexing to finish.")
        return repo

    def progress(self, job_id, stage, value):
        self.store.execute("UPDATE jobs SET status='running',stage=?,progress=? WHERE id=?", (stage, value, job_id))

    def enqueue(self, repo_id, kind, task):
        with self.gate:
            if self.store.one("SELECT id FROM jobs WHERE repository_id=? AND status IN ('queued','running')", (repo_id,)):
                raise RuntimeError("A job is already running for this repository.")
            job_id = uid()
            self.store.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, repo_id, kind, "queued", "Queued", 0, None, now(), None),
            )

        def run():
            try:
                self.progress(job_id, "Starting", 2)
                task(job_id)
                self.store.execute(
                    "UPDATE jobs SET status='completed',stage='Completed',progress=100,finished_at=? WHERE id=?",
                    (now(), job_id),
                )
            except Exception as error:
                message = str(error) if isinstance(error, ValueError) and kind == "index" else "Processing failed. Check server configuration and retry."
                log.warning("Job %s failed: %s", job_id, error)
                self.store.execute(
                    "UPDATE jobs SET status='failed',stage='Failed',error=?,finished_at=? WHERE id=?",
                    (message, now(), job_id),
                )
                if kind == "index":
                    self.store.execute("UPDATE repositories SET status='failed' WHERE id=?", (repo_id,))

        self.executor.submit(run)
        return {"job_id": job_id, "repository_id": repo_id}

    def index(self, repo_id, sample=False):
        repo = self.repository(repo_id)

        def task(job_id):
            self.store.execute("UPDATE repositories SET status='indexing' WHERE id=?", (repo_id,))
            snapshot = sample_snapshot() if sample else self.snapshot_loader(repo["url"], lambda s, p: self.progress(job_id, s, p))
            self.progress(job_id, "Indexing symbols", 80)
            with self.store.connect() as db:
                db.execute("DELETE FROM files WHERE repository_id=?", (repo_id,))
                for file in snapshot["files"]:
                    db.execute(
                        "INSERT INTO files VALUES (?,?,?,?,?,?,?)",
                        (
                            repo_id,
                            file["path"],
                            file["content"],
                            file["language"],
                            file["lines"],
                            json.dumps(file["symbols"]),
                            json.dumps(file["imports"]),
                        ),
                    )
                db.execute(
                    "UPDATE repositories SET name=?,description=?,branch=?,commit_sha=?,status='ready',indexed_at=?,skipped=? WHERE id=?",
                    (snapshot["name"], snapshot["description"], snapshot["branch"], snapshot["commit_sha"], now(), snapshot["skipped"], repo_id),
                )

        return self.enqueue(repo_id, "index", task)

    def list_repositories(self):
        return self.store.rows(
            "SELECT r.*, (SELECT count(*) FROM files f WHERE f.repository_id=r.id) file_count "
            "FROM repositories r ORDER BY created_at DESC"
        )

    def repository_detail(self, repo_id):
        repo = self.repository(repo_id)
        files = self.store.files(repo_id)
        repo.update(
            files=files,
            file_count=len(files),
            languages=dict(Counter(f["language"] for f in files)),
            total_lines=sum(f["lines"] for f in files),
            symbol_count=sum(len(f["symbols"]) for f in files),
        )
        repo["jobs"] = self.store.rows("SELECT * FROM jobs WHERE repository_id=? ORDER BY created_at DESC LIMIT 20", (repo_id,))
        repo["documents"] = self.store.rows(
            "SELECT id,title,kind,commit_sha,created_at FROM documents WHERE repository_id=? ORDER BY created_at DESC",
            (repo_id,),
        )
        return repo

    def add_repository(self, url):
        owner, name = parse_url(url)
        clean_url = f"https://github.com/{owner}/{name}"
        existing = self.store.one("SELECT id FROM repositories WHERE lower(url)=lower(?)", (clean_url,))
        if existing:
            return {"repository_id": existing["id"], "existing": True}
        repo_id = uid()
        self.store.execute(
            "INSERT INTO repositories (id,name,url,description,status,created_at) VALUES (?,?,?,?,?,?)",
            (repo_id, f"{owner}/{name}", clean_url, "", "new", now()),
        )
        return self.index(repo_id)

    def add_sample(self):
        existing = self.store.one("SELECT id FROM repositories WHERE is_sample=1")
        if existing:
            return {"repository_id": existing["id"], "existing": True}
        repo_id = uid()
        self.store.execute(
            "INSERT INTO repositories (id,name,url,description,status,created_at,is_sample) VALUES (?,?,?,?,?,?,1)",
            (repo_id, "sample/orbit-api", "", "Sample workspace", "new", now()),
        )
        return self.index(repo_id, True)

    def create_document(self, repo_id, mode):
        repo = self.ready(repo_id)
        files = self.store.files(repo_id, True)

        def task(job_id):
            if mode == "ai":
                import tempfile
                with tempfile.TemporaryDirectory(prefix="doclify-") as tmp:
                    for file in files:
                        target = Path(tmp) / file["path"]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(file["content"], encoding="utf-8")
                    result = generate_documentation(
                        tmp,
                        [f["path"] for f in files],
                        project_name=repo["name"],
                        llm_config=LLMConfig(model=os.environ.get("DOCLIFY_MODEL", "llama-3.3-70b-versatile")),
                        generate=self.ai_generate,
                        progress=lambda s, d, t, p: self.progress(job_id, "Writing " + (p or "README"), min(95, int(d / t * 90))),
                    )
                    title, markdown = "AI README", result.markdown
            else:
                self.progress(job_id, "Building source report", 60)
                title = "Repository source report"
                sections = [
                    f"# {repo['name']}\n",
                    "> Generated from indexed source metadata. This is a source report, not an AI explanation.\n",
                    f"Snapshot: `{repo['commit_sha']}` | Branch: `{repo['branch']}`\n",
                    f"## Repository overview\n\n{repo['description']}\n\n{len(files)} indexed files | {sum(f['lines'] for f in files)} lines\n",
                    "## Source files\n",
                ]
                for file in files:
                    sections.append(f"### {file['path']}\n\n{file['language']} | {file['lines']} lines\n")
                    if file["symbols"]:
                        sections.extend(f"- {s['kind']} `{s['name']}` - line {s['line']}" for s in file["symbols"])
                    if file["imports"]:
                        sections.append("\nImports: " + ", ".join("`" + x + "`" for x in file["imports"]))
                    sections.append("")
                markdown = "\n".join(sections)
            self.store.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?)",
                (uid(), repo_id, title, markdown, mode, repo["commit_sha"], now()),
            )

        return self.enqueue(repo_id, "documentation", task)

    def chat(self, repo_id, question):
        self.ready(repo_id)
        terms = set(re.findall(r"[a-zA-Z_][a-zA-Z_0-9]+", question.lower())) - {
            "the", "how", "does", "what", "this", "work", "where", "are", "and", "with", "for", "can", "you"
        }
        chunks = []
        for file in self.store.files(repo_id, True):
            lines = file["content"].splitlines()
            for start in range(0, len(lines), 40):
                content = "\n".join(lines[start:start + 50])
                hay = (file["path"] + " " + content).lower()
                score = sum(min(hay.count(t), 5) for t in terms)
                if score:
                    chunks.append((score, {"path": file["path"], "line": start + 1, "end_line": min(start + 50, len(lines)), "content": content[:5000]}))
        sources = [c for _, c in sorted(chunks, key=lambda pair: pair[0], reverse=True)[:4]]
        answer = "Found matching source excerpts below. AI explanations are not enabled in this workspace; these results come from keyword search."
        mode = "search"
        if not sources:
            answer = "No matching source was found. Try a function name, file name, or a more specific question."
        row = {"id": uid(), "repository_id": repo_id, "question": question, "answer": answer, "sources": sources, "mode": mode, "created_at": now()}
        self.store.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
            (row["id"], repo_id, question, answer, json.dumps(sources), mode, row["created_at"]),
        )
        return row


class Handler(BaseHTTPRequestHandler):
    workspace = None
    static = Path(__file__).parent / "static"

    def log_message(self, fmt, *args):
        log.info(fmt, *args)

    def send_json(self, payload, status=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def serve_file(self, path):
        target = (self.static / path).resolve()
        if not target.is_file() or not target.is_relative_to(self.static.resolve()):
            self.send_error(404)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            url = urlparse(self.path)
            path = url.path
            query = parse_qs(url.query)
            if path == "/":
                return self.serve_file("index.html")
            if path.startswith("/static/"):
                return self.serve_file(unquote(path.removeprefix("/static/")))
            if path == "/api/session":
                return self.send_json({"authenticated": True, "password_required": False, "ai_available": False, "model": os.environ.get("DOCLIFY_MODEL", "llama-3.3-70b-versatile"), "mode": "Single workspace"})
            if path == "/api/repositories":
                return self.send_json(self.workspace.list_repositories())
            if match := re.fullmatch(r"/api/repositories/([0-9a-f]+)", path):
                return self.send_json(self.workspace.repository_detail(match.group(1)))
            if match := re.fullmatch(r"/api/jobs/([0-9a-f]+)", path):
                row = self.workspace.store.one("SELECT * FROM jobs WHERE id=?", (match.group(1),))
                return self.send_json(row or {"detail": "Job not found."}, 200 if row else 404)
            if match := re.fullmatch(r"/api/repositories/([0-9a-f]+)/file", path):
                file = self.workspace.store.one("SELECT path,content,language,lines,symbols FROM files WHERE repository_id=? AND path=?", (match.group(1), query.get("path", [""])[0]))
                if not file:
                    return self.send_json({"detail": "File not found."}, 404)
                file["symbols"] = json.loads(file["symbols"])
                return self.send_json(file)
            if match := re.fullmatch(r"/api/documents/([0-9a-f]+)", path):
                doc = self.workspace.store.one("SELECT * FROM documents WHERE id=?", (match.group(1),))
                return self.send_json(doc or {"detail": "Document not found."}, 200 if doc else 404)
            if match := re.fullmatch(r"/api/repositories/([0-9a-f]+)/messages", path):
                rows = self.workspace.store.rows("SELECT * FROM messages WHERE repository_id=? ORDER BY created_at", (match.group(1),))
                for row in rows:
                    row["sources"] = json.loads(row["sources"])
                return self.send_json(rows)
            self.send_error(404)
        except Exception as error:
            self.send_json({"detail": str(error)}, 500)

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            body = self.body()
            if path == "/api/sample":
                return self.send_json(self.workspace.add_sample(), 202)
            if path == "/api/repositories":
                return self.send_json(self.workspace.add_repository(body.get("url", "")), 202)
            if match := re.fullmatch(r"/api/repositories/([0-9a-f]+)/index", path):
                return self.send_json(self.workspace.index(match.group(1), bool(self.workspace.repository(match.group(1))["is_sample"])), 202)
            if match := re.fullmatch(r"/api/repositories/([0-9a-f]+)/documents", path):
                return self.send_json(self.workspace.create_document(match.group(1), body.get("mode", "report")), 202)
            if match := re.fullmatch(r"/api/repositories/([0-9a-f]+)/chat", path):
                return self.send_json(self.workspace.chat(match.group(1), body.get("question", "")))
            self.send_error(404)
        except ValueError as error:
            self.send_json({"detail": str(error)}, 422)
        except RuntimeError as error:
            self.send_json({"detail": str(error)}, 409)
        except Exception as error:
            self.send_json({"detail": str(error)}, 500)


def run(host="127.0.0.1", port=8000, data_dir=None):
    Handler.workspace = Workspace(data_dir=data_dir)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Doclify web workspace running at http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        Handler.workspace.executor.shutdown(wait=False)


def main():
    run(os.environ.get("DOCLIFY_HOST", "127.0.0.1"), int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
