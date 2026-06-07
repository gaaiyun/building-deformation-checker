"""PaddleOCR-VL-1.6 异步 API 参数对照实验。

回答用户的核心疑问："是不是没深入调试利用好 PaddleOCR？"

实测：异步 `/api/v2/ocr/jobs` 的 optionalPayload 到底接受多少参数？
当前代码只透传 3 个键（ASYNC_OPTIONAL_PAYLOAD_KEYS），把 useLayoutDetection /
mergeTables / minPixels / maxPixels / layoutNms / promptLabel 全 strip 了。
本脚本用同一个 3 页切片（鱼珠乐天 p6-8 密集数据表），对照三档 optionalPayload，
看 API 接受性 + 表格提取质量（表数 / 表字符数 / 总字符数 / 耗时）。

用法：
    $env:BDC_ALLOW_REAL_PADDLE_DEBUG = "1"
    $env:BDC_OCR_SLICE_PDF = "output/_ocr_slice.pdf"
    python baseline/debug_paddle_params.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from src.utils.dotenv_loader import load_dotenv  # noqa: E402

load_dotenv()  # 必须在 import config 前：config 在导入时即读取 os.getenv

from src import config  # noqa: E402
from src.tools.pdf_extractor import (  # noqa: E402
    PADDLE_TABLE_PROFILE,
    TABLE_RE,
    _collect_layout_results_from_jsonl,
    _decode_paddle_response,
)

PDF = Path(os.environ.get("BDC_OCR_SLICE_PDF", ROOT / "output" / "_ocr_slice.pdf"))
TOKEN = config.PADDLE_OCR_TOKEN
MODEL = config.PADDLE_OCR_MODEL
JOB_URL = config.PADDLE_OCR_ASYNC_JOB_URL


def submit_and_wait(optional_payload: dict, label: str, model: str | None = None) -> dict:
    headers = {"Authorization": f"bearer {TOKEN}"}
    data = {
        "model": model or MODEL,
        "optionalPayload": json.dumps(optional_payload, ensure_ascii=False),
    }
    t0 = time.monotonic()
    try:
        with open(PDF, "rb") as f:
            resp = requests.post(
                JOB_URL, headers=headers, data=data, files={"file": f}, timeout=300
            )
        body = _decode_paddle_response(resp, "submit")
        job_id = (body.get("data") or {}).get("jobId")
        if not job_id:
            return {"label": label, "error": f"no jobId: {list(body.keys())}"}
        while True:
            if time.monotonic() - t0 > 600:
                return {"label": label, "error": "poll timeout"}
            pr = requests.get(f"{JOB_URL}/{job_id}", headers=headers, timeout=60)
            pb = _decode_paddle_response(pr, "poll", allow_failed_job=True)
            jd = pb.get("data") or {}
            st = jd.get("state")
            if st == "done":
                break
            if st == "failed":
                return {"label": label, "error": f"job failed: {jd.get('errorMsg')}",
                        "sec": round(time.monotonic() - t0, 1)}
            time.sleep(4)
        jsonl_url = (jd.get("resultUrl") or {}).get("jsonUrl")
        if not jsonl_url:
            return {"label": label, "error": "no jsonUrl"}
        jr = requests.get(jsonl_url, timeout=300)
        jr.raise_for_status()
        results = _collect_layout_results_from_jsonl(jr.text)
        md = "\n".join((r.get("markdown") or {}).get("text", "") for r in results)
        tables = TABLE_RE.findall(md)
        # dump markdown 供逐表人工对比
        safe = label.split("_")[0]
        (ROOT / "output" / f"_ocr_md_{safe}.md").write_text(md, encoding="utf-8")
        return {
            "label": label,
            "state": "done",
            "pages": len(results),
            "tables": len(tables),
            "table_chars": sum(len(t) for t in tables),
            "total_chars": len(md),
            "sec": round(time.monotonic() - t0, 1),
        }
    except Exception as exc:  # noqa: BLE001 - 实验脚本，要看全部异常
        return {
            "label": label,
            "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            "sec": round(time.monotonic() - t0, 1),
        }


VARIANTS = {
    "A_当前3键白名单": {
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
    },
    "B_完整table_profile": dict(PADDLE_TABLE_PROFILE),
    "C_调优_低阈值大像素union": {
        **PADDLE_TABLE_PROFILE,
        "layoutThreshold": 0.3,
        "maxPixels": 4000000,
        "layoutMergeBboxesMode": "union",
    },
}


MIN_PAYLOAD = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}
MODEL_VARIANTS = ["PaddleOCR-VL-1.5", "PaddleOCR-VL-1.6"]


def main() -> None:
    if os.environ.get("BDC_ALLOW_REAL_PADDLE_DEBUG") != "1":
        print("[SAFE] 本脚本会把指定 PDF 上传到 PaddleOCR 异步 API。")
        print("       确认要运行时，请先设置 BDC_ALLOW_REAL_PADDLE_DEBUG=1。")
        return
    if not TOKEN:
        print("[FAIL] 缺 PADDLE_OCR_TOKEN，请检查 .env")
        return
    if not PDF.exists():
        print(f"[FAIL] 切片不存在: {PDF}")
        return

    mode = sys.argv[1] if len(sys.argv) > 1 else "params"
    rows = []

    if mode == "models":
        # 模型版本对照：固定最小 payload，比 VL-1.5 vs 1.6（验证升级提交 c5ca17f）
        print(f"PDF={PDF.name}  模式=模型版本对照  端点={JOB_URL}\n")
        for m in MODEL_VARIANTS:
            print(f">>> 提交 model={m} (最小 payload) ...", flush=True)
            r = submit_and_wait(MIN_PAYLOAD, m, model=m)
            rows.append(r)
            print("    ", json.dumps(r, ensure_ascii=False), "\n", flush=True)
    else:
        print(f"PDF={PDF.name}  MODEL={MODEL}  端点={JOB_URL}\n")
        for label, op in VARIANTS.items():
            print(f">>> 提交 {label}  (optionalPayload {len(op)} 键) ...", flush=True)
            r = submit_and_wait(op, label)
            rows.append(r)
            print("    ", json.dumps(r, ensure_ascii=False), "\n", flush=True)

    print("=" * 70)
    print(f"{'变体':28} {'状态':8} {'页':>3} {'表数':>4} {'表字符':>7} {'耗时s':>6}")
    print("-" * 70)
    for r in rows:
        if "error" in r:
            print(f"{r['label']:28} {'ERROR':8} {r.get('error','')[:40]}")
        else:
            print(f"{r['label']:28} {r['state']:8} {r['pages']:>3} {r['tables']:>4} "
                  f"{r['table_chars']:>7} {r['sec']:>6}")


if __name__ == "__main__":
    main()
