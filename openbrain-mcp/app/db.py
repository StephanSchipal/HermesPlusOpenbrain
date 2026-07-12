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
        register_vector(conn)          # registers Vector/ndarray dumpers + vector-column
                                        # loading; plain list params still need an explicit
                                        # ::vector cast at the call site (see store.py)
        yield conn
    finally:
        conn.close()
