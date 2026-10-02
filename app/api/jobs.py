"""Jobs API: upload, status, SSE progress, review, export, download."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.deps import CSRF, CurrentUser, DB, client_ip
from app.config import get_settings
from app.db.models import Job, JobEvent, JobFile, Transaction, utcnow
from app.services import export_service, job_service, settings_service

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

MAX_FILES = 12
ALLOWED_SUFFIXES = {".zip", ".csv", ".xlsx", ".pdf"}


def _job_or_404(db, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


class JobOut(BaseModel):
    id: str
    title: str
    status: str
    error: str
    created_at: str
    stats: dict = {}
    summary: dict = {}


def _job_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        title=job.title,
        status=job.status,
        error=job.error,
        created_at=job.created_at.isoformat(),
        stats=job.stats or {},
        summary=job.summary or {},
    )


@router.post("", dependencies=[CSRF])
async def create_job(
    request: Request,
    db: DB,
    user: CurrentUser,
    files: list[UploadFile] = File(...),
    passwords: str = Form(default=""),
    title: str = Form(default=""),
) -> dict:
    """Create a job from uploaded bills.

    ``passwords`` is a JSON array of candidate passwords (order-free trial,
    legacy behaviour).  Passwords stay in memory for this run only.
    """

    settings = get_settings()
    try:
        password_list = json.loads(passwords) if passwords else []
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="passwords 必须是 JSON 数组") from exc
    if not isinstance(password_list, list) or len(password_list) > 20:
        raise HTTPException(status_code=400, detail="passwords 非法")
    password_list = [str(p)[:256] for p in password_list if p]

    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个账单文件")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"一次最多上传 {MAX_FILES} 个文件")

    total_bytes = 0
    payloads: list[tuple[str, bytes]] = []
    for upload in files:
        name = Path(upload.filename or "").name
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型：{name or '(未命名)'}")
        data = await upload.read()
        total_bytes += len(data)
        if total_bytes > settings.max_request_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail="请求总大小超过限制")
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"{name} 超过单文件大小限制")
        payloads.append((name, data))
        await upload.close()

    job_id = job_service.new_job_id()
    fingerprint_seen: set[str] = set()
    db.add(
        Job(
            id=job_id,
            title=title[:128] or f"导入 {len(payloads)} 份账单",
            status="queued",
        )
    )
    for name, data in payloads:
        fingerprint = _sha256(data)
        duplicate = fingerprint in fingerprint_seen
        fingerprint_seen.add(fingerprint)
        stored = job_service.stage_upload(job_id, name, data)
        db.add(
            JobFile(
                job_id=job_id,
                display_name=name[:256],
                stored_name=stored,
                state="duplicate" if duplicate else "pending",
                error="重复上传的相同文件" if duplicate else "",
                fingerprint=fingerprint,
            )
        )
    job_service._add_event(db, job_id, "created", f"上传 {len(payloads)} 个文件")  # noqa: SLF001
    # Store passwords before the job row becomes visible to the worker,
    # then commit and wake so the worker can never see the job first.
    job_service.get_worker().store_passwords(job_id, password_list)
    db.commit()
    job_service.get_worker().wake()
    job = _job_or_404(db, job_id)
    return _job_out(job).model_dump()


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


@router.get("")
def list_jobs(db: DB, user: CurrentUser) -> list[dict]:
    rows = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(100)).all()
    return [_job_out(j).model_dump() for j in rows]


@router.get("/{job_id}")
def get_job(job_id: str, db: DB, user: CurrentUser) -> dict:
    job = _job_or_404(db, job_id)
    files = db.scalars(select(JobFile).where(JobFile.job_id == job_id)).all()
    out = _job_out(job).model_dump()
    out["files"] = [
        {
            "id": f.id,
            "name": f.display_name,
            "state": f.state,
            "source": f.detected_source,
            "password_ok": f.password_ok,
            "error": f.error,
        }
        for f in files
    ]
    return out


@router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request, db: DB, user: CurrentUser) -> StreamingResponse:
    """SSE progress stream for a job.  Uses its own DB sessions because the
    stream outlives the request-scoped session."""

    _job_or_404(db, job_id)

    async def stream():
        from app.db.database import db_session

        last_event_id = 0
        idle = 0.0
        while True:
            if await request.is_disconnected():
                return
            poll = db_session()
            try:
                rows = (
                    poll.query(JobEvent)
                    .filter(JobEvent.job_id == job_id, JobEvent.id > last_event_id)
                    .order_by(JobEvent.id)
                    .all()
                )
                for row in rows:
                    last_event_id = row.id
                    payload = json.dumps(
                        {"event": row.event, "message": row.message, "level": row.level, "at": row.created_at.isoformat()},
                        ensure_ascii=False,
                    )
                    yield f"id: {row.id}\ndata: {payload}\n\n"
                job = poll.get(Job, job_id)
                terminal = job is not None and job.status in {"needs_review", "done", "failed", "interrupted"}
                status_now = job.status if job is not None else ""
            finally:
                poll.close()
            if terminal and not rows:
                yield f"data: {json.dumps({'event': 'terminal', 'status': status_now}, ensure_ascii=False)}\n\n"
                return
            await asyncio.sleep(0.8)
            idle += 0.8
            if idle > 600:
                yield f"data: {json.dumps({'event': 'timeout'})}\n\n"
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{job_id}/rerun", dependencies=[CSRF])
def rerun(job_id: str, db: DB, user: CurrentUser) -> dict:
    job_service.rerun_job(db, job_id)
    return _job_out(_job_or_404(db, job_id)).model_dump()


@router.delete("/{job_id}", dependencies=[CSRF])
def delete_job(job_id: str, db: DB, user: CurrentUser) -> dict:
    job = _job_or_404(db, job_id)
    if job.status in {"queued", "extracting", "reconciling", "classifying", "exporting"}:
        raise HTTPException(status_code=409, detail="任务正在处理中，无法删除")
    import shutil

    shutil.rmtree(job_service.job_dir(job_id), ignore_errors=True)
    for f in db.scalars(select(JobFile).where(JobFile.job_id == job_id)):
        db.delete(f)
    for e in db.scalars(select(JobEvent).where(JobEvent.job_id == job_id)):
        db.delete(e)
    for t in db.scalars(select(Transaction).where(Transaction.job_id == job_id)):
        db.delete(t)
    from app.db.models import Match as MatchRow

    for m in db.scalars(select(MatchRow).where(MatchRow.job_id == job_id)):
        db.delete(m)
    db.delete(job)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


class ClassificationUpdate(BaseModel):
    record_uid: str = ""  # empty + merchant => apply to every matching pending row
    category: str
    subcategory: str
    direction: str = "支出"
    merchant: str = ""
    save_rule: bool = False  # “以后相同商户都这样分类”


class ReviewFilter(BaseModel):
    state: str = "all"
    search: str = ""


@router.get("/{job_id}/transactions")
def list_transactions(
    job_id: str,
    db: DB,
    user: CurrentUser,
    state: str = "all",
    disposition: str = "",
    search: str = "",
    offset: int = 0,
    limit: int = 200,
) -> dict:
    _job_or_404(db, job_id)
    query = db.query(Transaction).filter(Transaction.job_id == job_id)
    if state == "pending":
        query = query.filter(Transaction.classification_state == "pending")
    elif state in {"rule", "manual", "ai"}:
        query = query.filter(Transaction.classification_state == state)
    elif state == "review":
        query = query.filter(Transaction.review_reasons != "[]")
    elif state == "expense":
        query = query.filter(Transaction.flow == "支出")
    elif state == "income":
        query = query.filter(Transaction.flow == "收入")
    elif state == "refund":
        query = query.filter(Transaction.refund_original_uid.isnot(None))
    if disposition:
        query = query.filter(Transaction.disposition == disposition)
    if search:
        like = f"%{search}%"
        query = query.filter(Transaction.merchant.like(like) | Transaction.item.like(like))
    total = query.count()
    rows = query.order_by(Transaction.datetime, Transaction.source).offset(max(0, offset)).limit(min(1000, limit)).all()
    return {
        "total": total,
        "items": [
            {
                "record_uid": t.record_uid,
                "source": t.source,
                "datetime": t.datetime,
                "flow": t.flow,
                "amount": t.amount_cents / 100,
                "net_amount": t.net_amount_cents / 100,
                "disposition": t.disposition,
                "disposition_reason": t.disposition_reason,
                "merchant": t.merchant,
                "item": t.item,
                "account": t.account,
                "status": t.status,
                "refund_original_uid": t.refund_original_uid,
                "refund_uids": t.refund_uids,
                "matched_uids": t.matched_uids,
                "review_reasons": t.review_reasons,
                "category": t.category,
                "subcategory": t.subcategory,
                "tags": t.tags,
                "basis": t.basis,
                "classification_state": t.classification_state,
            }
            for t in rows
        ],
    }


@router.post("/{job_id}/classify", dependencies=[CSRF])
def classify_transaction(job_id: str, payload: ClassificationUpdate, db: DB, user: CurrentUser) -> dict:
    _job_or_404(db, job_id)
    taxonomy = settings_service.load_taxonomy()
    if payload.direction == "收入":
        from app.core.classification.rules import INCOME_CATEGORIES

        if payload.category != payload.subcategory or payload.category not in INCOME_CATEGORIES:
            raise HTTPException(status_code=400, detail="收入分类不合法")
    else:
        subs = taxonomy.get(payload.category, [])
        if payload.subcategory not in subs:
            raise HTTPException(status_code=400, detail="支出分类不合法")

    if payload.record_uid:
        tx = (
            db.query(Transaction)
            .filter(Transaction.job_id == job_id, Transaction.record_uid == payload.record_uid)
            .first()
        )
        if tx is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        tx.category = payload.category
        tx.subcategory = payload.subcategory
        tx.classification_state = "manual"
        tx.basis = "人工分类"
        target_merchant = tx.merchant
        changed = 1
    else:
        if not payload.merchant:
            raise HTTPException(status_code=400, detail="需要 record_uid 或 merchant")
        target_merchant = payload.merchant
        changed = 0

    # Apply to remaining pending rows with the same merchant in the same
    # export direction (disposition-based: a withdrawal-fee row is an
    # expense even though its raw flow is 不计收支).
    if target_merchant:
        direction = payload.direction
        if direction == "支出":
            dispositions = ["导入-支出", "导入-提现手续费", "导入-部分退款净额", "导入-银行直连支出", "排除-全额退款原单"]
        else:
            dispositions = ["排除-收入", "排除-退款入账", "保留-跨期退款收入"]
        siblings = (
            db.query(Transaction)
            .filter(
                Transaction.job_id == job_id,
                Transaction.merchant == target_merchant,
                Transaction.category == "",
                Transaction.disposition.in_(dispositions),
            )
            .all()
        )
        for sib in siblings:
            sib.category = payload.category
            sib.subcategory = payload.subcategory
            sib.classification_state = "manual"
            sib.basis = "人工分类"
            changed += 1
    if changed == 0:
        raise HTTPException(status_code=404, detail="没有匹配的待分类记录")
    if payload.save_rule and target_merchant:
        settings_service.upsert_rule(
            db,
            {
                "match_key": target_merchant,
                "direction": payload.direction,
                "category": payload.category,
                "subcategory": payload.subcategory,
                "origin": "user",
            },
        )
    db.commit()
    return {"ok": True, "changed": changed}


@router.get("/{job_id}/unmatched")
def unmatched_summary(job_id: str, db: DB, user: CurrentUser) -> list[dict]:
    _job_or_404(db, job_id)
    rows = db.query(Transaction).filter(Transaction.job_id == job_id, Transaction.category == "").all()
    EXPENSE_OK = {"导入-支出", "导入-提现手续费", "导入-部分退款净额", "导入-银行直连支出", "排除-全额退款原单"}
    INCOME_OK = {"排除-收入", "排除-退款入账", "保留-跨期退款收入"}
    grouped: dict[tuple[str, str], dict] = {}
    for t in rows:
        # Direction follows the export semantics, not the raw bill flow
        # (a withdrawal-fee row is an expense even though its source flow
        # is 不计收支).
        if t.disposition in EXPENSE_OK:
            direction = "支出"
        elif t.disposition in INCOME_OK:
            direction = "收入"
        else:
            continue
        key = (direction, t.merchant or t.item)
        slot = grouped.setdefault(key, {"merchant": key[1], "direction": direction, "count": 0, "amount": 0.0, "items": []})
        slot["count"] += 1
        slot["amount"] = round(slot["amount"] + t.amount_cents / 100, 2)
        if t.item and t.item not in slot["items"] and len(slot["items"]) < 3:
            slot["items"].append(t.item)
    return list(grouped.values())


@router.get("/{job_id}/matches")
def matches(job_id: str, db: DB, user: CurrentUser) -> list[dict]:
    _job_or_404(db, job_id)
    return export_service.match_explanations(db, job_id)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@router.post("/{job_id}/export", dependencies=[CSRF])
def run_export(job_id: str, db: DB, user: CurrentUser) -> dict:
    _job_or_404(db, job_id)
    try:
        return export_service.export_job(db, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{job_id}/artifacts/{name}")
def download_artifact(job_id: str, name: str, db: DB, user: CurrentUser) -> FileResponse:
    _job_or_404(db, job_id)
    # name is validated against the artifacts directory; no traversal.
    safe_name = Path(name).name
    if safe_name != name or ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = job_service.job_dir(job_id) / "artifacts" / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path, filename=safe_name)


@router.get("/{job_id}/review-pack")
def review_pack(job_id: str, db: DB, user: CurrentUser) -> StreamingResponse:
    _job_or_404(db, job_id)
    data = export_service.build_review_workbook(db, job_id)
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="review_{job_id}.zip"'},
    )
