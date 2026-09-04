FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
# pyproject.toml declares packages = ["app"] explicitly, so `pip install .`
# needs an `app/` dir at metadata time. Stub it for this layer only so the
# dependency layer stays cacheable independent of app source; the real
# `COPY app ./app` below overwrites it. (Same trick as openbrain-mcp.)
RUN mkdir app && touch app/__init__.py && \
    python -m pip install --no-cache-dir . && \
    rm -rf app

COPY app ./app

EXPOSE 8080
CMD ["python", "-m", "app.server"]
