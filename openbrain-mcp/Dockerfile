FROM python:3.11-slim

# Build deps for psycopg/torch wheels are not needed (binary wheels), keep slim.
WORKDIR /app
COPY pyproject.toml ./
RUN python -m pip install --no-cache-dir .

# Pre-download the embedding model at build time so first request is fast
ENV OPENBRAIN_MODEL=intfloat/multilingual-e5-small
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

COPY app ./app
COPY migrations ./migrations

EXPOSE 8080
CMD ["python", "-m", "app.server"]
