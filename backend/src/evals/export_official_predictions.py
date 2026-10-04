"""! @brief 将真实评测补丁导出为 SWE-bench 官方 harness 的 JSONL 输入。"""

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


def export_predictions(
    report_path: Path,
    *,
    variant: str,
    repeat_index: int = 1,
    output_path: Path | None = None,
) -> Path:
    """! @brief 选择每题的一次运行，导出官方格式并保存来源清单。

    @param report_path 已完成的 DevPilot 真实评测 report.json。
    @param variant 要导出的 Agent/RAG 策略。
    @param repeat_index 同题重复运行的序号；官方格式每题只允许一份补丁。
    @param output_path JSONL 输出路径；缺省放在原报告目录。
    @return 实际写出的 JSONL 路径。
    @raise ValueError 报告未完成、选择为空、重复实例或补丁字节不符。
    """

    if repeat_index < 1:
        raise ValueError("repeat_index 必须 >= 1")
    report_bytes = report_path.read_bytes()
    report: dict[str, Any] = json.loads(report_bytes)
    if report.get("status") != "completed":
        raise ValueError("仅能导出已完成的真实评测报告")
    selected = [
        row
        for row in report.get("rows", [])
        if row.get("variant") == variant and row.get("repeat_index", 1) == repeat_index
    ]
    if not selected:
        raise ValueError(f"没有找到 variant={variant}、repeat_index={repeat_index} 的运行")

    model = str(report.get("model", "unknown"))
    run_id = str(report.get("run_id", "unknown"))
    model_name = re.sub(
        r"[^A-Za-z0-9._-]", "_", f"devpilot_{model}_{variant}_{run_id}_r{repeat_index}"
    )
    predictions: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in selected:
        instance_id = str(row["instance_id"])
        if instance_id in seen:
            raise ValueError(f"同一官方预测文件包含重复实例：{instance_id}")
        seen.add(instance_id)
        patch_bytes = Path(row["patch_path"]).read_bytes()
        # 旧报告在 Windows 上可能由 write_text 把 LF 转成 CRLF；报告记录的
        # patch_bytes 则是原始 LF 补丁长度。仅还原这一换行差异后校验。
        patch_text = patch_bytes.decode("utf-8").replace("\r\n", "\n")
        normalized_patch = patch_text.encode("utf-8")
        if len(normalized_patch) != row["patch_bytes"]:
            raise ValueError(f"补丁字节数与报告不符：{instance_id}")
        if row.get("patch_sha256") and (
            hashlib.sha256(normalized_patch).hexdigest() != row["patch_sha256"]
        ):
            raise ValueError(f"补丁哈希与报告不符：{instance_id}")
        predictions.append(
            {
                "instance_id": instance_id,
                "model_name_or_path": model_name,
                "model_patch": patch_text,
            }
        )

    output = output_path or report_path.parent / f"predictions__{variant}__r{repeat_index}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in predictions),
        encoding="utf-8",
        newline="\n",
    )
    manifest = {
        "source_report": str(report_path.resolve()),
        "source_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "source_run_id": run_id,
        "source_dataset": report.get("dataset"),
        "official_dataset": "princeton-nlp/SWE-bench_Lite",
        "model_name_or_path": model_name,
        "variant": variant,
        "repeat_index": repeat_index,
        "submitted_instance_ids": [item["instance_id"] for item in predictions],
        "skipped_after_calibration": [
            item["instance_id"]
            for item in report.get("audits", [])
            if not item.get("baseline_failed", False)
        ],
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> None:
    """! @brief 命令行入口。"""

    parser = argparse.ArgumentParser(description="导出 SWE-bench 官方 harness 预测文件")
    parser.add_argument("report", type=Path)
    parser.add_argument("--variant", default="single_no_rag")
    parser.add_argument("--repeat-index", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(
        export_predictions(
            args.report,
            variant=args.variant,
            repeat_index=args.repeat_index,
            output_path=args.output,
        )
    )


if __name__ == "__main__":
    main()
