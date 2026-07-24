FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY backend/pyproject.toml ./
# Same stub-package trick as openbrain-mcp/Dockerfile: pyproject.toml
# declares packages = ["app"], so `pip install .` needs app/ to exist for
# the metadata step even though the real source is copied in afterward --
# this keeps the dependency layer cacheable independently of app changes.
RUN mkdir app && touch app/__init__.py && \
    python -m pip install --no-cache-dir . && \
    rm -rf app

COPY backend/app ./app
COPY --from=frontend-build /frontend/dist ./static

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
