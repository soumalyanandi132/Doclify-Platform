import json
import sqlite3
from pathlib import Path

class Store:
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.path = str(Path(directory) / 'doclify.sqlite3')
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS repositories (
              id TEXT PRIMARY KEY, name TEXT, url TEXT, description TEXT,
              branch TEXT, commit_sha TEXT, status TEXT, created_at TEXT,
              indexed_at TEXT, is_sample INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS files (
              repository_id TEXT, path TEXT, content TEXT, language TEXT,
              lines INTEGER, symbols TEXT, imports TEXT,
              PRIMARY KEY(repository_id,path));
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, repository_id TEXT, kind TEXT, status TEXT,
              stage TEXT, progress INTEGER, error TEXT, created_at TEXT, finished_at TEXT);
            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY, repository_id TEXT, title TEXT, markdown TEXT,
              kind TEXT, commit_sha TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY, repository_id TEXT, question TEXT, answer TEXT,
              sources TEXT, mode TEXT, created_at TEXT);
            PRAGMA user_version=1;
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        return db

    def execute(self, sql, args=()):
        with self.connect() as db:
            db.execute(sql, args)

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def files(self, repository_id, content=False):
        fields = '*' if content else 'path,language,lines,symbols,imports'
        rows = self.rows(f'SELECT {fields} FROM files WHERE repository_id=? ORDER BY path', (repository_id,))
        for row in rows:
            row['symbols'] = json.loads(row['symbols'])
            row['imports'] = json.loads(row['imports'])
        return rows
