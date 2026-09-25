FROM python:3.12-slim
#不生成`.pyc`字节码缓存文件，容器里不需要缓存，减少垃圾文件。
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
#执行命令安装依赖、创建用户，打包进镜像里面
RUN python -m pip install \
    pytest \
    ruff \
    mypy

# ④创建普通非root用户 sandbox，uid=10001，防止容器内root权限高危
RUN useradd \
    --create-home \
    --uid 10001 \
    sandbox
# ⑤设置容器默认工作目录，容器启动后就在这个文件夹
WORKDIR /workspace

USER sandbox
# ⑦容器启动时默认执行命令：打印python版本
CMD ["python", "--version"]
