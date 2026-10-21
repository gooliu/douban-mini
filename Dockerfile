# 豆瓣小组查询 —— 后端 + 前端一体镜像
# 部署到 Render / Railway / Koyeb / Fly.io / 任意支持 Docker 的平台
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY web/ ./web/

WORKDIR /app/backend

EXPOSE 8000

# 平台（Render / Railway 等）会注入 $PORT，缺省回落 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
