import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENBRAIN_TOKEN = os.environ.get("OPENBRAIN_TOKEN", "")
MODEL_NAME = os.environ.get("OPENBRAIN_MODEL", "intfloat/multilingual-e5-small")
EMBED_DIM = 384
# IANA timezone name search's date_from/date_to filters interpret "calendar
# day" in. created_at is stored as timestamptz (a real UTC instant); casting
# it straight to ::date uses the Postgres session's timezone (UTC unless
# configured), which silently misattributes captures made near local
# midnight to the wrong day for any non-UTC deployment. Defaults to UTC
# (safe/neutral); a single-user deployment in a specific timezone should set
# this to its own IANA name (e.g. "Europe/Vienna").
CAPTURE_TIMEZONE = os.environ.get("OPENBRAIN_TIMEZONE", "UTC")
