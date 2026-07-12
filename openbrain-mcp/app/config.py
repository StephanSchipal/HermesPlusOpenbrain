import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENBRAIN_TOKEN = os.environ.get("OPENBRAIN_TOKEN", "")
MODEL_NAME = os.environ.get("OPENBRAIN_MODEL", "intfloat/multilingual-e5-small")
EMBED_DIM = 384
