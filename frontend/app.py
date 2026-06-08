from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel


FRONTEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = FRONTEND_DIR.parent
STATIC_DIR = FRONTEND_DIR / "static"
RUNS_DIR = FRONTEND_DIR / "runs"
WORKER_PATH = FRONTEND_DIR / "worker.py"
load_dotenv(FRONTEND_DIR / ".env", override=True)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
MAX_LOG_LINES = 400
AUTH_COOKIE_NAME = "mip_frontend_auth"
AUTH_COOKIE_MAX_AGE_SECONDS = 12 * 60 * 60
AUTH_PUBLIC_PATHS = {"/login", "/api/auth", "/api/health", "/api/login", "/api/logout"}
TERMINAL_JOB_STATUSES = {"complete", "failed", "cancelled"}

WORKFLOW_LOCK = threading.Lock()
STATUS_LOCK = threading.Lock()
PROCESS_LOCK = threading.Lock()
RUNNING_PROCESSES: dict[str, subprocess.Popen[str]] = {}
JOB_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$")

app = FastAPI(title="GD Symbol MIP Workflow")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class LoginRequest(BaseModel):
    password: str


@app.middleware("http")
async def _password_gate(request: Request, call_next):
    if not _auth_enabled() or _is_public_path(request.url.path):
        return await call_next(request)
    if _request_is_authenticated(request):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Authentication required."}, status_code=401)
    return _no_store_file(STATIC_DIR / "login.html")


@app.on_event("startup")
def _startup() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    _mark_interrupted_jobs()


@app.get("/")
def index() -> FileResponse:
    return _no_store_file(STATIC_DIR / "index.html")


@app.get("/login")
def login_page() -> FileResponse:
    return _no_store_file(STATIC_DIR / "login.html")


@app.get("/api/auth")
def auth_state(request: Request) -> dict[str, Any]:
    enabled = _auth_enabled()
    return {
        "enabled": enabled,
        "authenticated": (not enabled) or _request_is_authenticated(request),
        "session_seconds": AUTH_COOKIE_MAX_AGE_SECONDS,
    }


@app.post("/api/login")
def login(payload: LoginRequest) -> JSONResponse:
    password = _access_password()
    if not password:
        return JSONResponse({"status": "disabled"})
    if not hmac.compare_digest(payload.password.encode("utf-8"), password.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid password.")

    response = JSONResponse({"status": "ok"})
    response.set_cookie(
        AUTH_COOKIE_NAME,
        _make_auth_cookie(),
        max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/api/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return response


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/jobs")
def list_jobs() -> dict[str, list[dict[str, Any]]]:
    jobs = []
    for job_file in RUNS_DIR.glob("*/job.json"):
        try:
            jobs.append(_public_job(_read_json(job_file)))
        except (OSError, json.JSONDecodeError):
            continue
    jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {"jobs": jobs[:25]}


@app.post("/api/jobs")
async def create_job(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    uploads = [upload for upload in files if upload.filename]
    if not uploads:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    invalid = [
        upload.filename
        for upload in uploads
        if Path(upload.filename or "").suffix.lower() not in IMAGE_EXTENSIONS
    ]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type: {', '.join(invalid)}",
        )

    job_id = _new_job_id()
    job_dir = RUNS_DIR / job_id
    input_dir = job_dir / "input"
    ocr_output_dir = job_dir / "ocr-output"
    workflow_output_dir = job_dir / "excel-output"
    input_dir.mkdir(parents=True, exist_ok=False)
    ocr_output_dir.mkdir(parents=True, exist_ok=True)
    workflow_output_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for index, upload in enumerate(uploads, start=1):
        original_name = Path(upload.filename or f"table-{index}.png").name
        filename = f"{index:02d}-{_safe_filename(original_name)}"
        target = input_dir / filename
        with target.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                handle.write(chunk)
        saved_files.append(
            {
                "original_name": original_name,
                "stored_name": filename,
                "stored_path": _relative_to_job(job_dir, target),
                "size": target.stat().st_size,
            }
        )

    job = {
        "id": job_id,
        "status": "queued",
        "progress": 5,
        "step": "檔案已接收",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "files": saved_files,
        "results": [],
        "errors": [],
        "warnings": [],
        "log": [],
        "paths": {
            "input_dir": _relative_to_job(job_dir, input_dir),
            "ocr_output_dir": _relative_to_job(job_dir, ocr_output_dir),
            "workflow_output_dir": _relative_to_job(job_dir, workflow_output_dir),
        },
    }
    _write_job(job_dir, job)

    thread = threading.Thread(target=_run_job, args=(job_id,), name=f"mip-job-{job_id}", daemon=True)
    thread.start()
    return _public_job(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return _public_job(_load_job(job_id))


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    job = _load_job(job_id)
    if job.get("status") in TERMINAL_JOB_STATUSES:
        return _public_job(job)

    job_dir = _job_dir(job_id)
    if not job_dir:
        raise HTTPException(status_code=404, detail="Job not found.")

    _update_job(
        job_dir,
        status="cancelled",
        progress=100,
        step="已取消",
        warnings=job.get("warnings", []) + ["Job cancelled by browser refresh or close."],
    )
    _terminate_registered_process(job_id)
    return _public_job(_read_job(job_dir))


@app.get("/api/jobs/{job_id}/artifacts/{result_index}/workbook")
def download_workbook(job_id: str, result_index: int) -> FileResponse:
    job, path = _artifact_path(job_id, result_index, "workbook_path")
    source_name = job["results"][result_index].get("source_name") or f"table-{result_index + 1}"
    download_name = f"{Path(source_name).stem}_MIP_filled.xls"
    return FileResponse(path, media_type="application/vnd.ms-excel", filename=download_name)


@app.get("/api/jobs/{job_id}/artifacts/{result_index}/preview")
def preview_image(job_id: str, result_index: int) -> FileResponse:
    _, path = _artifact_path(job_id, result_index, "preview_path")
    return FileResponse(path, media_type="image/png")


def _run_job(job_id: str) -> None:
    job_dir = _job_dir(job_id)
    if not job_dir:
        return
    if _job_status(job_dir) in TERMINAL_JOB_STATUSES:
        return

    _update_job(job_dir, status="queued", progress=8, step="等待工作流程")
    with WORKFLOW_LOCK:
        if _job_status(job_dir) in TERMINAL_JOB_STATUSES:
            return
        _update_job(job_dir, status="running", progress=10, step="啟動 OCR workflow")
        command = [
            sys.executable,
            str(WORKER_PATH),
            "--job-dir",
            str(job_dir),
            "--input-dir",
            str(job_dir / "input"),
            "--ocr-output-dir",
            str(job_dir / "ocr-output"),
            "--workflow-output-dir",
            str(job_dir / "excel-output"),
        ]
        _append_log(job_dir, "$ " + " ".join(command))
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        _register_process(job_id, process)
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                if _job_status(job_dir) == "cancelled":
                    _terminate_process(process)
                    break
                line = raw_line.rstrip()
                if not line:
                    continue
                if line.startswith("FRONTEND_PROGRESS "):
                    _apply_progress_message(job_dir, line.removeprefix("FRONTEND_PROGRESS "))
                    continue
                _append_log(job_dir, line)
                _apply_log_progress(job_dir, line)

            return_code = process.wait()
        finally:
            _unregister_process(job_id, process)

        if _job_status(job_dir) == "cancelled":
            return
        summary = _load_worker_summary(job_dir)
        if return_code != 0:
            errors = summary.get("errors") or [f"worker exited with code {return_code}"]
            warnings = summary.get("warnings") or []
            _update_job(
                job_dir,
                status="failed",
                step="處理失敗",
                errors=errors,
                warnings=warnings,
            )
            return

        _finalize_job(job_dir, summary)


def _apply_progress_message(job_dir: Path, payload: str) -> None:
    try:
        message = json.loads(payload)
    except json.JSONDecodeError:
        _append_log(job_dir, payload)
        return
    progress = message.get("progress")
    if isinstance(progress, int | float):
        progress = min(99, max(0, int(progress)))
    else:
        progress = None
    changes = {
        "status": message.get("status") or "running",
        "step": message.get("step") or "處理中",
    }
    if progress is not None:
        changes["progress"] = progress
    _update_job(job_dir, **changes)


def _apply_log_progress(job_dir: Path, line: str) -> None:
    if "Loading GD symbol classifier" in line:
        _update_job(job_dir, progress=16, step="載入 GD 符號分類器")
        return
    if "Loading base model" in line:
        _update_job(job_dir, progress=22, step="載入 GLM-OCR")
        return
    match = re.search(r"\[(\d+)/(\d+)\]\s+Processing:\s+(.+)$", line)
    if match:
        index = int(match.group(1))
        total = max(1, int(match.group(2)))
        progress = 30 + round(((index - 1) / total) * 38)
        _update_job(job_dir, progress=progress, step=f"OCR 辨識中 {index}/{total}")
        return
    if line.startswith("Saved:") and line.endswith(".json"):
        _update_job(job_dir, progress=68, step="OCR JSON 已產生")


def _finalize_job(job_dir: Path, summary: dict[str, Any]) -> None:
    results = []
    workflow = summary.get("workflow") or {}
    source_names = _source_name_map(_read_job(job_dir))

    for index, result in enumerate(workflow.get("results", [])):
        workbook_path = _relative_to_job(job_dir, Path(result.get("workbook", "")))
        preview = result.get("frontend_preview") or {}
        preview_path = _relative_to_job(job_dir, Path(preview.get("output_path", "")))
        input_path = Path(result.get("input", ""))
        source_name = source_names.get(input_path.stem, input_path.name or f"table-{index + 1}")
        results.append(
            {
                "source_name": source_name,
                "row_count": result.get("row_count"),
                "validation_status": (result.get("workbook_validation") or {}).get("status"),
                "snapshot_status": (result.get("snapshot") or {}).get("status"),
                "preview_status": preview.get("status"),
                "preview_source": preview.get("source"),
                "workbook_path": workbook_path,
                "preview_path": preview_path,
            }
        )

    status = "complete" if summary.get("status") == "success" else "failed"
    _update_job(
        job_dir,
        status=status,
        progress=100 if status == "complete" else 99,
        step="完成" if status == "complete" else "處理失敗",
        results=results,
        errors=summary.get("errors", []),
        warnings=summary.get("warnings", []),
    )


def _source_name_map(job: dict[str, Any]) -> dict[str, str]:
    mapping = {}
    for item in job.get("files", []):
        stored_stem = Path(item.get("stored_name", "")).stem
        if stored_stem:
            mapping[stored_stem] = item.get("original_name") or item.get("stored_name")
    return mapping


def _no_store_file(path: Path) -> FileResponse:
    return FileResponse(path, headers={"Cache-Control": "no-store"})


def _auth_enabled() -> bool:
    return bool(_access_password())


def _access_password() -> str:
    return os.environ.get("FRONTEND_ACCESS_PASSWORD", "").strip()


def _is_public_path(path: str) -> bool:
    return path.startswith("/static/") or path in AUTH_PUBLIC_PATHS


def _request_is_authenticated(request: Request) -> bool:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    return bool(token and _valid_auth_cookie(token))


def _make_auth_cookie() -> str:
    issued_at = str(int(time.time()))
    nonce = secrets.token_hex(8)
    payload = f"{issued_at}.{nonce}"
    return f"{payload}.{_auth_signature(payload)}"


def _valid_auth_cookie(token: str) -> bool:
    parts = token.split(".")
    if len(parts) != 3:
        return False
    issued_at, nonce, signature = parts
    if not issued_at.isdigit() or not nonce:
        return False
    age = int(time.time()) - int(issued_at)
    if age < 0 or age > AUTH_COOKIE_MAX_AGE_SECONDS:
        return False
    payload = f"{issued_at}.{nonce}"
    return hmac.compare_digest(signature, _auth_signature(payload))


def _auth_signature(payload: str) -> str:
    secret = os.environ.get("FRONTEND_SESSION_SECRET", "").strip() or _access_password()
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _load_worker_summary(job_dir: Path) -> dict[str, Any]:
    summary_path = job_dir / "worker-summary.json"
    if not summary_path.is_file():
        return {"status": "errors_found", "errors": ["worker-summary.json was not created"], "warnings": []}
    try:
        return _read_json(summary_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "errors_found", "errors": [str(exc)], "warnings": []}


def _new_job_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{secrets.token_hex(4)}"


def _safe_filename(filename: str) -> str:
    path = Path(filename)
    suffix = path.suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip(".-_")
    return f"{(stem or 'table')[:80]}{suffix}"


def _load_job(job_id: str) -> dict[str, Any]:
    job_dir = _job_dir(job_id)
    if not job_dir:
        raise HTTPException(status_code=404, detail="Job not found.")
    try:
        return _read_job(job_dir)
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=404, detail="Job metadata is unavailable.") from None


def _job_dir(job_id: str) -> Path | None:
    if not JOB_ID_PATTERN.match(job_id):
        return None
    job_dir = (RUNS_DIR / job_id).resolve()
    try:
        job_dir.relative_to(RUNS_DIR.resolve())
    except ValueError:
        return None
    return job_dir if job_dir.is_dir() else None


def _artifact_path(job_id: str, result_index: int, key: str) -> tuple[dict[str, Any], Path]:
    job = _load_job(job_id)
    results = job.get("results") or []
    if result_index < 0 or result_index >= len(results):
        raise HTTPException(status_code=404, detail="Result not found.")
    relative_path = results[result_index].get(key)
    if not relative_path:
        raise HTTPException(status_code=404, detail="Artifact not available.")
    job_dir = _job_dir(job_id)
    if not job_dir:
        raise HTTPException(status_code=404, detail="Job not found.")
    path = (job_dir / relative_path).resolve()
    try:
        path.relative_to(job_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Artifact path is invalid.") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file not found.")
    return job, path


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    public = dict(job)
    for index, result in enumerate(public.get("results", [])):
        if result.get("workbook_path"):
            result["download_url"] = f"/api/jobs/{public['id']}/artifacts/{index}/workbook"
        if result.get("preview_path"):
            result["preview_url"] = f"/api/jobs/{public['id']}/artifacts/{index}/preview"
    return public


def _job_status(job_dir: Path) -> str | None:
    try:
        return _read_job(job_dir).get("status")
    except (OSError, json.JSONDecodeError):
        return None


def _register_process(job_id: str, process: subprocess.Popen[str]) -> None:
    with PROCESS_LOCK:
        RUNNING_PROCESSES[job_id] = process


def _unregister_process(job_id: str, process: subprocess.Popen[str]) -> None:
    with PROCESS_LOCK:
        if RUNNING_PROCESSES.get(job_id) is process:
            RUNNING_PROCESSES.pop(job_id, None)


def _terminate_registered_process(job_id: str) -> None:
    with PROCESS_LOCK:
        process = RUNNING_PROCESSES.get(job_id)
    if process is not None:
        _terminate_process(process)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        pass


def _update_job(job_dir: Path, **changes: Any) -> None:
    with STATUS_LOCK:
        job = _read_job(job_dir)
        if job.get("status") == "cancelled" and changes.get("status") != "cancelled":
            return
        if "progress" in changes and isinstance(changes["progress"], int):
            changes["progress"] = max(job.get("progress", 0), changes["progress"])
        job.update(changes)
        job["updated_at"] = _utc_now()
        _write_job(job_dir, job)


def _append_log(job_dir: Path, line: str) -> None:
    with STATUS_LOCK:
        job = _read_job(job_dir)
        log = job.setdefault("log", [])
        log.append(line)
        if len(log) > MAX_LOG_LINES:
            del log[: len(log) - MAX_LOG_LINES]
        job["updated_at"] = _utc_now()
        _write_job(job_dir, job)


def _read_job(job_dir: Path) -> dict[str, Any]:
    return _read_json(job_dir / "job.json")


def _write_job(job_dir: Path, job: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    target = job_dir / "job.json"
    tmp = job_dir / "job.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _relative_to_job(job_dir: Path, path: Path) -> str | None:
    if not str(path):
        return None
    try:
        return str(path.resolve().relative_to(job_dir.resolve())).replace("\\", "/")
    except (OSError, ValueError):
        return None


def _mark_interrupted_jobs() -> None:
    for job_file in RUNS_DIR.glob("*/job.json"):
        try:
            job = _read_json(job_file)
        except (OSError, json.JSONDecodeError):
            continue
        if job.get("status") in {"queued", "running"}:
            job["status"] = "failed"
            job["step"] = "服務重啟，工作已中斷"
            job.setdefault("errors", []).append("The server restarted before this job finished.")
            job["updated_at"] = _utc_now()
            _write_job(job_file.parent, job)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
