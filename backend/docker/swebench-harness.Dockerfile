# 与本项目 SWE-bench Lite dev 实例格式兼容的官方 Linux harness。
FROM python:3.11-slim

RUN pip install --no-cache-dir swebench==4.0.5

WORKDIR /workspace
