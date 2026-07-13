FROM python:3.11-slim

# Build deps for psycopg/torch wheels are not needed (binary wheels), keep slim.
WORKDIR /app
COPY pyproject.toml ./
# pyproject.toml declares packages = ["app"] explicitly (not auto-discovered), so
# `pip install .` fails at the metadata/egg_info step if `app/` doesn't exist yet --
# stub it out just for this layer so dependency resolution succeeds; the real
# `COPY app ./app` below overwrites it. This keeps the dependency-install layer
# cacheable independently of app source changes (the whole point of installing
# from pyproject.toml before COPY app ./app).
RUN mkdir app && touch app/__init__.py && \
    python -m pip install --no-cache-dir . && \
    rm -rf app

# Pre-download the embedding model at build time so first request is fast
ENV OPENBRAIN_MODEL=intfloat/multilingual-e5-small
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

COPY app ./app
COPY migrations ./migrations

EXPOSE 8080
CMD ["python", "-m", "app.server"]
