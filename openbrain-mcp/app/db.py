# app/db.py
from contextlib import contextmanager
from collections.abc import Iterator
import psycopg
from pgvector.psycopg import register_vector
from app.config import DATABASE_URL

@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(DATABASE_URL)
    try:
        register_vector(conn)          # lets us pass/return Python lists as vectors
        yield conn
    finally:
        conn.close()
