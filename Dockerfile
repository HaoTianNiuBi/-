FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai \
    QIYU_DB=/data/qiyu.sqlite3 QIYU_STATIC_DIR=/app/frontend QIYU_KEY_MODE=environment
WORKDIR /app
COPY backend/requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt \
    && useradd --system --uid 10001 qiyu \
    && install -d -o qiyu -g qiyu /data
COPY backend/*.py ./
COPY --from=frontend /build/dist ./frontend
USER qiyu
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765"]
