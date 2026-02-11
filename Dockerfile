# Dockerfile - 最小可用
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖（若需要）
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制并安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目
COPY . .

# 暴露端口（imcp.pro 等通常使用环境变量 PORT）
ENV PORT=8080
EXPOSE 8080

# 使用 gunicorn 生产启动
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8080", "app:app", "--timeout", "30"]
