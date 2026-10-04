"""! @brief SWE-bench 官方预测文件导出的离线验收。"""

import json
import hashlib
from pathlib import Path

import pytest

from backend.src.evals.export_official_predictions import export_predictions


def _report(tmp_path: Path) -> Path:
    """! @brief 构造两次重复及一次无效校准的最小报告。"""

    patch_one = tmp_path / "r1.patch"
    patch_two = tmp_path / "r2.patch"
    patch_one.write_text("diff --git a/a.py b/a.py\n+print('新')\n", encoding="utf-8", newline="\n")
    patch_two.write_text("diff --git a/a.py b/a.py\n+print('二')\n", encoding="utf-8", newline="\n")
    report = {
        "run_id": "sample-run",
        "status": "completed",
        "dataset": "SWE-bench/SWE-bench_Lite:dev",
        "model": "deepseek-v4-flash",
        "audits": [
            {"instance_id": "owner__repo-1", "baseline_failed": True},
            {"instance_id": "owner__repo-2", "baseline_failed": False},
        ],
        "rows": [
            {
                "instance_id": "owner__repo-1",
                "variant": "single_no_rag",
                "repeat_index": 1,
                "patch_path": str(patch_one),
                "patch_bytes": len(patch_one.read_bytes()),
            },
            {
                "instance_id": "owner__repo-1",
                "variant": "single_no_rag",
                "repeat_index": 2,
                "patch_path": str(patch_two),
                "patch_bytes": len(patch_two.read_bytes()),
            },
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path


def test_export_selects_one_repeat_and_preserves_patch(tmp_path: Path) -> None:
    """! @brief 同题重复只能选一次，补丁 Unicode 与来源清单须原样保存。"""

    report_path = _report(tmp_path)
    output = export_predictions(report_path, variant="single_no_rag", repeat_index=2)
    lines = output.read_text(encoding="utf-8").splitlines()
    manifest = json.loads(output.with_suffix(".manifest.json").read_text(encoding="utf-8"))

    assert len(lines) == 1
    prediction = json.loads(lines[0])
    assert set(prediction) == {"instance_id", "model_name_or_path", "model_patch"}
    assert prediction["instance_id"] == "owner__repo-1"
    assert prediction["model_patch"] == (tmp_path / "r2.patch").read_text(encoding="utf-8")
    assert manifest["submitted_instance_ids"] == ["owner__repo-1"]
    assert manifest["skipped_after_calibration"] == ["owner__repo-2"]
    assert manifest["repeat_index"] == 2


def test_export_rejects_corrupted_patch_without_output(tmp_path: Path) -> None:
    """! @brief 报告字节数与补丁不一致时不得产出看似有效的预测。"""

    report_path = _report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["rows"][0]["patch_bytes"] += 1
    report_path.write_text(json.dumps(report), encoding="utf-8")
    output = tmp_path / "predictions.jsonl"

    with pytest.raises(ValueError, match="补丁字节数"):
        export_predictions(
            report_path,
            variant="single_no_rag",
            output_path=output,
        )

    assert not output.exists()


def test_export_normalizes_legacy_windows_patch(tmp_path: Path) -> None:
    """! @brief 兼容旧报告的 CRLF 磁盘补丁，仍按原始 LF 字节数核验。"""

    report_path = _report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    patch_path = Path(report["rows"][0]["patch_path"])
    original = patch_path.read_bytes()
    patch_path.write_bytes(original.replace(b"\n", b"\r\n"))

    output = export_predictions(report_path, variant="single_no_rag")
    prediction = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert prediction["model_patch"].encode("utf-8") == original
    assert b"\r\n" not in prediction["model_patch"].encode("utf-8")


def test_export_rejects_same_length_patch_tampering(tmp_path: Path) -> None:
    """! @brief 新报告的 SHA-256 校验可发现等长补丁篡改。"""

    report_path = _report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    patch_path = Path(report["rows"][0]["patch_path"])
    report["rows"][0]["patch_sha256"] = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report), encoding="utf-8")
    patch_path.write_bytes(patch_path.read_bytes().replace(b"a.py", b"b.py"))

    with pytest.raises(ValueError, match="补丁哈希"):
        export_predictions(report_path, variant="single_no_rag")
