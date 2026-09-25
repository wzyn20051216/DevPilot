FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Git 用于仓库差异查询；Docker CLI 通过宿主机 socket 启动隔离测试容器。
RUN apt-get update \
    && apt-get install -y --no-install-recommends git docker-cli \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend ./backend

EXPOSE 8000

CMD ["uvicorn", "backend.src.main:app", "--host", "0.0.0.0", "--port", "8000"]
