FROM python:3.12-slim AS python
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

# ---------------------------------------------------------------------------
# polyglot 阶段：在 Python 沙箱最终镜像基础上追加 Node 22 + tsx + Temurin JDK 22，
# 用于 TypeScript / Java 的跨文件 benchmark（技术手册 10.2.7）。
#
# 关键约束：沙箱运行期仍然是 --network none + --read-only + 只读挂载仓库，
# 所以 Node/JDK/tsx 必须在【构建期】全部安装进镜像，运行期不需要任何网络，
# 风险面与 Python 沙箱完全一致。
# ---------------------------------------------------------------------------
FROM python AS polyglot

USER root

# Node 22 使用官方 tarball（比 NodeSource 少依赖 gnupg，且版本可复现）。
# 采用 .tar.gz 而非 .tar.xz，避免 slim 基础镜像缺 xz-utils 解压失败。
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ARCH=$(dpkg --print-architecture) \
    && case "${ARCH}" in \
         amd64) NODE_ARCH=x64 ;; \
         arm64) NODE_ARCH=arm64 ;; \
         *) echo "unsupported arch: ${ARCH}" && exit 1 ;; \
       esac \
    && curl -fsSL "https://nodejs.org/dist/v22.11.0/node-v22.11.0-linux-${NODE_ARCH}.tar.gz" -o /tmp/node.tar.gz \
    && mkdir -p /opt/node \
    && tar -xzf /tmp/node.tar.gz -C /opt/node --strip-components=1 \
    && rm /tmp/node.tar.gz

ENV PATH="/opt/node/bin:${PATH}"

# tsx 全局安装：构建期联网装好，运行期沙箱禁网也能用 npx tsx 跑 TS 测试。
RUN npm install -g tsx

# Temurin JDK 22（Adoptium 官方 tarball）：JEP 458 需要 JDK 22+ 才能
# `java Main.java` 直接启动多文件源码程序。
RUN ARCH=$(dpkg --print-architecture) \
    && case "${ARCH}" in \
         amd64) JAVA_ARCH=x64 ;; \
         arm64) JAVA_ARCH=aarch64 ;; \
         *) echo "unsupported arch: ${ARCH}" && exit 1 ;; \
       esac \
    && curl -fsSL "https://api.adoptium.net/v3/binary/latest/22/ga/linux/${JAVA_ARCH}/jdk/hotspot/normal/eclipse" -o /tmp/jdk.tar.gz \
    && mkdir -p /opt/java \
    && tar -xzf /tmp/jdk.tar.gz -C /opt/java --strip-components=1 \
    && rm /tmp/jdk.tar.gz

ENV JAVA_HOME=/opt/java
ENV PATH="/opt/java/bin:${PATH}"

# npx 默认把缓存写到 $HOME/.npm；沙箱运行期根文件系统只读、HOME 不可写，
# 因此把 npm 缓存重定向到运行期唯一可写的 /tmp，避免 npx tsx 因建缓存失败。
ENV npm_config_cache=/tmp/npm-cache

USER sandbox

CMD ["node", "--version"]
