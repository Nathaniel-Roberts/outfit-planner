"""Admin-only backup export and restore."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from app import backup
from app.auth import require_admin
from app.users import User

router = APIRouter()


@router.get("/backup/export")
def export_backup(
    request: Request, background: BackgroundTasks, user: User = Depends(require_admin)
):
    settings = request.app.state.settings
    from app.main import app_version

    stamp = datetime.now(ZoneInfo(settings.tz)).strftime("%Y%m%d-%H%M")
    tmp = Path(tempfile.mkdtemp(prefix="op-backup-"))
    out = tmp / f"outfit-planner-backup-{stamp}.zip"
    backup.export_zip(settings.data_dir, out, version=app_version())
    background.add_task(shutil.rmtree, tmp, True)
    return FileResponse(out, media_type="application/zip", filename=out.name)


@router.post("/backup/import")
async def import_backup(
    request: Request,
    user: User = Depends(require_admin),
    archive: UploadFile | None = None,
    confirm: str = Form(""),
):
    if confirm.strip().lower() != "replace":
        request.state.flash = {"kind": "error", "message": "Type REPLACE to confirm the restore."}
        return RedirectResponse("/settings?restore=unconfirmed#backup", status_code=303)
    if archive is None or not archive.filename:
        raise HTTPException(status_code=400, detail="No archive uploaded")
    settings = request.app.state.settings
    with tempfile.TemporaryDirectory(prefix="op-restore-") as tmp:
        path = Path(tmp) / "upload.zip"
        with open(path, "wb") as f:
            while chunk := await archive.read(1024 * 1024):
                f.write(chunk)
        try:
            result = backup.import_zip(path, settings.data_dir)
        except backup.BadBackup as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(f"/settings?restored={result['photos']}#backup", status_code=303)
