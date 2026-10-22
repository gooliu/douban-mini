# 豆瓣小组查询 —— 后端 + 前端一体镜像
# 适用：Render / Koyeb / Northflank / Railway / Fly.io / Zeabur / 任意支持 Docker 的平台
#
# 构建： docker build -t douban-mini .
# 本地跑：docker run -p 8000:8000 -e DOUBAN_CLOUD=1 douban-mini
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    # 云端默认：登录凭证只存访问者浏览器，不落服务端，多人共用也不串号
    DOUBAN_CLOUD=1 \
    DOUBAN_DATA_DIR=/data

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY web/ ./web/

# 补充记录 / 设备号落在这里；平台支持持久卷时挂到 /data 就能跨重启保留
RUN mkdir -p /data && chmod 777 /data
VOLUME ["/data"]

WORKDIR /app/backend

EXPOSE 8000

# 平台（Render / Koyeb 等）会注入 $PORT，缺省回落 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
