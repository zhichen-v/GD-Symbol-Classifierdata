# AGENTS.md

## Scope

- Only add or edit files under `frontend/` for frontend work.
- Do not write uploaded user files into the project root `final-table/` directories.
- Runtime job data belongs under `frontend/runs/<job-id>/`.

## Workflow

- The FastAPI app is `frontend/app.py`.
- The background worker is `frontend/worker.py`.
- The static UI lives in `frontend/static/`.
- Reuse the root project OCR and Excel modules instead of copying pipeline logic.
- `worker.py` may import `ocr_final.py` and temporarily set `ocr_final.INPUT_DIR` / `ocr_final.OUTPUT_DIR` to the current job folders.
- Optional shared-password access control is loaded from `frontend/.env` via `FRONTEND_ACCESS_PASSWORD`.
- The browser UI is session-scoped: do not auto-load prior `frontend/runs/` jobs on page load.
- Refreshing or closing a page with active jobs should best-effort cancel those jobs, not delete their run folders.
- Do not hardcode passwords or secrets in tracked files.

## Verification

Use the fine-tune environment for this app:

```powershell
uv pip install --python .\.venv-finetune\Scripts\python.exe -r .\frontend\requirements.txt
.\.venv-finetune\Scripts\python.exe -m py_compile .\frontend\app.py .\frontend\worker.py
.\.venv-finetune\Scripts\python.exe -m uvicorn frontend.app:app --host 127.0.0.1 --port 8000
```

Check `http://127.0.0.1:8000/api/health` before using the page.

To verify password mode:

```powershell
Copy-Item .\frontend\.env.example .\frontend\.env
# Set FRONTEND_ACCESS_PASSWORD in .\frontend\.env before starting.
.\.venv-finetune\Scripts\python.exe -m uvicorn frontend.app:app --host 127.0.0.1 --port 8000
```

## Data Safety

- Do not delete `frontend/runs/` unless the user explicitly asks for cleanup.
- Do not print or commit the local value in `frontend/.env`.
- Do not start training from this frontend.
- OCR jobs should remain serial unless GPU memory behavior is intentionally revisited.
