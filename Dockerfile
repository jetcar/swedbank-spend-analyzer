FROM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY scripts/build-frontend.mjs ./scripts/build-frontend.mjs
COPY app/static ./app/static
RUN npm run build

FROM python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN apk upgrade --no-cache \
    && python -m pip install --no-cache-dir --upgrade pip==26.3 setuptools==84.2.1 \
    && python -m pip install --no-cache-dir --only-binary=:all: -r requirements.txt \
    && python -m pip check \
    # Installation tools are not needed by the running services. Pip bundles an old setuptools.
    && python -m pip uninstall -y pip setuptools \
    && rm -rf /usr/local/lib/python3.12/ensurepip

COPY app ./app
COPY --from=frontend /build/frontend-dist ./frontend-dist

CMD ["python", "-m", "app.server"]
