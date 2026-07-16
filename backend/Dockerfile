FROM python:3.12-slim

WORKDIR /app

# 构建期系统依赖（P0 解析库多为 wheel，仅保留 build-essential 兜底）。
# P1 接 weasyprint 时在此追加 libpango 等。
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
