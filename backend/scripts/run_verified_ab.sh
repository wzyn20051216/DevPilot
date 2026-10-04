#!/usr/bin/env bash
# 新题配对 A/B：镜像就绪一题就评一题，避免等待全部镜像下载完成。
# 每题单独一个 run_id；汇总时按 report.json 合并。日志写入 backend/data/verified_ab/。
set -u
cd "$(dirname "$0")/../.."
LOG_DIR=backend/data/verified_ab
mkdir -p "$LOG_DIR"
REPEATS="${REPEATS:-2}"
# 名单来源（可复现）：Verified test，排除 NON_PYTEST_REPOS 后
# select_stratified_instances(count=12, seed=20261004)。
PENDING=(
  astropy__astropy-13579 matplotlib__matplotlib-20676 mwaskom__seaborn-3187
  pallets__flask-5014 psf__requests-2317 pydata__xarray-6744
  pylint-dev__pylint-4970 pytest-dev__pytest-5631 scikit-learn__scikit-learn-26323
  sphinx-doc__sphinx-7454 astropy__astropy-13977 matplotlib__matplotlib-20826
)

image_of() {
  local lower
  lower=$(echo "$1" | tr 'A-Z' 'a-z')
  echo "swebench/sweb.eval.x86_64.${lower/__/_1776_}:latest"
}

while ((${#PENDING[@]})); do
  remaining=()
  for id in "${PENDING[@]}"; do
    if [[ -f "$LOG_DIR/$id.done" ]]; then
      continue
    fi
    if docker image inspect "$(image_of "$id")" >/dev/null 2>&1; then
      echo "[$(date +%H:%M:%S)] 开始 $id"
      .venv/Scripts/python.exe -m backend.src.evals.real_world \
        --dataset verified --instance "$id" --repeats "$REPEATS" \
        --variant single_no_rag --variant single_enhanced \
        >"$LOG_DIR/$id.log" 2>&1
      echo "$?" >"$LOG_DIR/$id.done"
      echo "[$(date +%H:%M:%S)] 完成 $id: $(tail -1 "$LOG_DIR/$id.log")"
    else
      remaining+=("$id")
    fi
  done
  PENDING=("${remaining[@]+"${remaining[@]}"}")
  ((${#PENDING[@]})) && sleep 60
done
echo "[$(date +%H:%M:%S)] 全部完成"
