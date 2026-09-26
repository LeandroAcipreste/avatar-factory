import sqlite3
from contextlib import contextmanager
from .config import DATABASE_PATH


def init_db() -> None:
    with connect() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                consent_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                file_size INTEGER NOT NULL,
                duration_seconds REAL,
                width INTEGER,
                height INTEGER,
                codec TEXT,
                thumbnail_path TEXT,
                sample_frames_json TEXT,
                recommendations_json TEXT
            )
        ''')


@contextmanager
def connect():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def create_job(job: dict) -> None:
    columns = ', '.join(job.keys())
    values = ', '.join('?' for _ in job)
    with connect() as conn:
        conn.execute(f'INSERT INTO jobs ({columns}) VALUES ({values})', tuple(job.values()))


def update_job(job_id: str, **changes) -> None:
    if not changes:
        return
    clause = ', '.join(f'{key} = ?' for key in changes)
    with connect() as conn:
        conn.execute(f'UPDATE jobs SET {clause} WHERE id = ?', (*changes.values(), job_id))


def get_job(job_id: str):
    with connect() as conn:
        return conn.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()


def list_jobs(limit: int = 30):
    with connect() as conn:
        return conn.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
