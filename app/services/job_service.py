"""Job pipeline: upload staging, background worker, persistence.

Single-process background worker (thread) — no Celery.  Job state lives in
SQLite so a restart marks running jobs as ``interrupted`` and the user can
re-run them; nothing can get stuck in ``processing`` forever.

Bill passwords are submitted with the job and kept ONLY in the worker's
in-memory map for the duration of the run; they are never written to the
database, disk or logs.
"""

from __future__ import annotations

import logging
import shutil
import threading
import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.ai import service as ai_service
from app.ai.sanitizer import normalize_key
from app.config import get_settings
from app.core.classification.apply import build_import_rows
from app.core.classification.rules import INCOME_CATEGORIES
from app.core.extraction import BillInput, detect_and_extract, extract_bank_all, reconcile_payapps
from app.core.reconciliation.ledger import LedgerBuilder
from app.core.reconciliation.refunds import auto_direct_bank_refund_groups
from app.db.models import AIUsage, Job, JobEvent, JobFile, Match, Transaction, utcnow
from app.services import settings_service

logger = logging.getLogger("tabledger.jobs")


def new_job_id() -> str:
    return uuid.uuid4().hex[:16]


def job_dir(job_id: str) -> Path:
    return get_settings().data_dir / "jobs" / job_id


def uploads_dir(job_id: str) -> Path:
    return job_dir(job_id) / "uploads"


def _add_event(db: DBSession, job_id: str, event: str, message: str = "", level: str = "info") -> None:
    db.add(JobEvent(job_id=job_id, event=event, message=message, level=level))


def _set_status(db: DBSession, job_id: str, status: str, message: str = "") -> None:
    job = db.get(Job, job_id)
    if job is None:
        return
    job.status = status
    if message:
        _add_event(db, job_id, "status", message)


def cents_of(value: Any) -> int:
    if value is None:
        return 0
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def stage_upload(job_id: str, display_name: str, data: bytes) -> str:
    """Store an upload under a server-generated random name."""

    directory = uploads_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = Path(display_name).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    (directory / stored_name).write_bytes(data)
    return stored_name


def mark_interrupted_jobs(db: DBSession) -> int:
    """On startup: any job left running is marked interrupted (re-runnable)."""

    active = ["queued", "extracting", "reconciling", "classifying", "exporting"]
    rows = db.scalars(select(Job).where(Job.status.in_(active))).all()
    for job in rows:
        job.status = "interrupted"
        _add_event(db, job.id, "interrupted", "应用重启，任务已中断；可重新运行")
    db.commit()
    return len(rows)


def delete_raw_files(db: DBSession, job_id: str) -> None:
    """Privacy default: raw uploads are removed once processing completes."""

    directory = uploads_dir(job_id)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    for row in db.scalars(select(JobFile).where(JobFile.job_id == job_id)):
        row.state = "deleted"


def retention_hours(privacy: dict[str, Any]) -> int | None:
    value = privacy.get("raw_file_retention", "immediate")
    return {"immediate": None, "1h": 1, "24h": 24, "7d": 24 * 7}.get(value)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

_worker: JobWorker | None = None


def get_worker() -> JobWorker:
    global _worker
    if _worker is None:
        _worker = JobWorker()
    return _worker


class JobWorker:
    """Single background thread processing queued jobs sequentially."""

    def __init__(self) -> None:
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # job_id -> [passwords]; entries removed after the run.
        self._passwords: dict[str, list[str]] = {}

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="tabledger-worker", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def store_passwords(self, job_id: str, passwords: list[str] | None) -> None:
        """Keep job passwords in memory only (never DB/disk/logs)."""

        if passwords:
            self._passwords[job_id] = [p for p in passwords if p]

    def wake(self) -> None:
        self._wake.set()

    def submit(self, job_id: str, passwords: list[str] | None = None) -> None:
        """Queue a job with in-memory-only passwords (single-call convenience)."""

        self.store_passwords(job_id, passwords)
        self._wake.set()

    def _loop(self) -> None:
        from app.db.database import db_session

        while not self._stop.is_set():
            self._wake.wait(timeout=2.0)
            self._wake.clear()
            while not self._stop.is_set():
                db = db_session()
                try:
                    job_id = db.scalar(select(Job.id).where(Job.status == "queued").order_by(Job.created_at))
                    if job_id is None:
                        break
                    try:
                        self._run_job(db, job_id)
                    except Exception:
                        logger.exception("job %s failed", job_id)
                        db.rollback()
                        job = db.get(Job, job_id)
                        if job is not None:
                            job.status = "failed"
                            job.error = "内部处理错误"
                            _add_event(db, job_id, "error", "内部处理错误", "error")
                            db.commit()
                        self._passwords.pop(job_id, None)
                finally:
                    db.close()

    # -- job phases ------------------------------------------------------

    def _run_job(self, db: DBSession, job_id: str) -> None:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.started_at = utcnow()
        _set_status(db, job_id, "extracting", "开始提取账单")
        db.commit()

        passwords = self._passwords.pop(job_id, [])
        files = [
            f
            for f in db.scalars(select(JobFile).where(JobFile.job_id == job_id)).all()
            if f.state in {"pending", "needs_password"}
        ]
        inputs: list[BillInput] = []
        for f in files:
            path = uploads_dir(job_id) / f.stored_name
            if not path.exists():
                f.state = "failed"
                f.error = "文件缺失"
                continue
            inputs.append(BillInput(path=path, name=f.display_name))

        config = settings_service.engine_config(db)
        taxonomy = settings_service.load_taxonomy()

        # -- extract -----------------------------------------------------
        records_by_source, file_meta, errors = detect_and_extract(inputs, config, passwords)
        self._record_file_states(db, job_id, file_meta)
        bank_records, bank_qa = extract_bank_all(records_by_source)
        errors.extend(bank_qa.get("errors", []))
        db.commit()
        if errors:
            job = db.get(Job, job_id)
            job.status = "failed"
            job.error = "；".join(errors[:5])
            _add_event(db, job_id, "error", job.error, "error")
            db.commit()
            self._after_processing(db, job)
            return
        _add_event(
            db,
            job_id,
            "extract",
            "；".join(f"{source}: {len(rows)} 条" for source, rows in records_by_source.items()),
        )
        db.commit()

        # -- reconcile -----------------------------------------------------
        _set_status(db, job_id, "reconciling", "跨源对账与去重")
        db.commit()
        payapps: list[dict] = []
        for source in ("wx", "zfb"):
            payapps.extend(records_by_source.get(source, []))
        payapps.sort(key=lambda r: r["datetime"])
        declared_report = reconcile_payapps(payapps, file_meta)

        direct_groups = auto_direct_bank_refund_groups(bank_records)
        builder = LedgerBuilder(config, payapps, bank_records, direct_refund_groups=direct_groups)
        ledger = builder.run()

        # -- classify -----------------------------------------------------
        _set_status(db, job_id, "classifying", "规则分类")
        db.commit()
        rules = settings_service.rules_for_engine(db)
        rows, unmatched = build_import_rows(config, ledger, taxonomy, rules)

        ai_stats = self._ai_fallback(db, job_id, ledger, taxonomy, rules, unmatched, ai_cfg=settings_service.get_ai_settings(db))
        if ai_stats["overrides"]:
            rows, unmatched = build_import_rows(config, ledger, taxonomy, rules, ai_stats["overrides"])

        # -- persist -----------------------------------------------------
        self._persist_results(db, job_id, ledger, rows)

        summary = dict(ledger["summary"])
        summary["declared_reconciliation"] = declared_report
        summary["ai"] = ai_stats["stats"]
        pending_keys = len(unmatched)
        job = db.get(Job, job_id)
        job.summary = summary
        job.stats = {
            "original_records": summary.get("original_record_count", 0),
            "import_rows": len(rows),
            "unmatched_keys": pending_keys,
        }
        _set_status(db, job_id, "needs_review", f"处理完成，{pending_keys} 个待分类键")
        db.commit()
        self._after_processing(db, job)

    def _after_processing(self, db: DBSession, job: Job) -> None:
        """Apply the raw-file retention policy (default: delete now)."""

        privacy = settings_service.get_privacy_settings(db)
        if retention_hours(privacy) is None:
            delete_raw_files(db, job.id)
            db.commit()

    def _record_file_states(self, db: DBSession, job_id: str, file_meta: dict) -> None:
        rows = {f.display_name: f for f in db.scalars(select(JobFile).where(JobFile.job_id == job_id))}
        for meta in file_meta.get("files", []):
            row = rows.get(str(meta.get("name", "")))
            if row is None or row.state not in {"pending", "parsed", "failed"}:
                continue
            if meta.get("status") == "ok":
                row.state = "parsed"
                row.detected_source = str(meta.get("source", ""))
                row.password_ok = bool(meta.get("password_matched"))
                row.error = ""
            else:
                row.state = "failed"
                row.error = str(meta.get("reason", ""))[:500]

    def _ai_fallback(
        self,
        db: DBSession,
        job_id: str,
        ledger: dict,
        taxonomy: dict,
        rules: list[dict[str, str]],
        unmatched: dict,
        ai_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        """Cache-first AI classification for unmatched unique keys."""

        empty = {"stats": {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cache_hits": 0, "ai_hits": 0}, "overrides": {}}
        if not ai_cfg.get("enabled") or not unmatched:
            return empty

        provider = str(ai_cfg.get("provider", ""))
        model = str(ai_cfg.get("model", ""))
        unknown: list[dict[str, Any]] = []
        overrides: dict[tuple[str, str], dict[str, str]] = {}

        for (direction, key), _slot in unmatched.items():
            merchant, _, item = key.partition(" ")
            normalized = normalize_key(merchant, item)
            ckey = ai_service.cache_key(normalized, direction, provider, model)
            cached = ai_service.cache_lookup_many(db, [ckey]).get(ckey)
            if cached is not None:
                empty["stats"]["cache_hits"] += 1
                overrides[(direction, key)] = {"类别": cached.category, "子类": cached.subcategory, "标签": cached.tags}
                continue
            unknown.append(
                {
                    "id": f"u{len(unknown) + 1}",
                    "merchant": merchant,
                    "item": item,
                    "flow": direction,
                    "_direction": direction,
                    "_key": key,
                }
            )

        if not unknown:
            return empty

        _add_event(db, job_id, "ai", f"AI 待分类 {len(unknown)} 个唯一键")
        payload = ai_service.build_ai_payload(unknown)
        results = ai_service.classify_batch(db, payload, taxonomy, INCOME_CATEGORIES)

        latest_usage = None
        for row in db.scalars(select(AIUsage).order_by(AIUsage.id.desc()).limit(1)):
            latest_usage = row
        if latest_usage is not None:
            empty["stats"]["requests"] = latest_usage.requests
            empty["stats"]["input_tokens"] = latest_usage.input_tokens
            empty["stats"]["output_tokens"] = latest_usage.output_tokens

        threshold = float(ai_cfg.get("confidence_threshold", 0.9))
        for entry in unknown:
            result = results.get(entry["id"])
            if not result:
                continue
            normalized = normalize_key(entry["merchant"], entry["item"])
            ckey = ai_service.cache_key(normalized, entry["_direction"], provider, model)
            ai_service.cache_store(
                db,
                key=ckey,
                merchant=entry["merchant"],
                item=entry["item"],
                flow=entry["_direction"],
                category=result["category"],
                subcategory=result["subcategory"],
                tags=result["tags"],
                confidence=result["confidence"],
                provider=provider,
                model=model,
                input_tokens=empty["stats"]["input_tokens"],
                output_tokens=empty["stats"]["output_tokens"],
            )
            empty["stats"]["ai_hits"] += 1
            if float(result.get("confidence", 0.0)) >= threshold:
                overrides[(entry["_direction"], entry["_key"])] = {
                    "类别": result["category"],
                    "子类": result["subcategory"],
                    "标签": result["tags"],
                }
        empty["overrides"] = overrides
        db.commit()
        return empty

    def _persist_results(self, db: DBSession, job_id: str, ledger: dict, rows: list[dict]) -> None:
        classification_by_uid = {r.get("_record_uid", ""): r for r in rows}
        for tx in db.scalars(select(Transaction).where(Transaction.job_id == job_id)):
            db.delete(tx)
        for m in db.scalars(select(Match).where(Match.job_id == job_id)):
            db.delete(m)
        db.flush()

        for record in ledger["all_records"]:
            row = classification_by_uid.get(record["record_uid"], {})
            basis = str(row.get("_依据", ""))
            if str(row.get("类别", "")):
                if basis.startswith("分类词典"):
                    state = "rule"
                elif basis == "当期分类":
                    state = "manual"
                else:
                    state = "ai"
            else:
                state = "pending"
            db.add(
                Transaction(
                    job_id=job_id,
                    record_uid=record["record_uid"],
                    source=record["source"],
                    source_row=int(record.get("source_row", 0) or 0),
                    source_id=str(record.get("source_id", "")),
                    datetime=record["datetime"],
                    flow=record["flow"],
                    amount_cents=cents_of(record["amount"]),
                    signed_amount_cents=cents_of(record.get("signed_amount", 0)),
                    net_amount_cents=cents_of(record.get("net_amount", 0)),
                    status=str(record.get("status", "")),
                    disposition=str(record.get("disposition", "")),
                    disposition_reason=str(record.get("disposition_reason", "")),
                    merchant=str(record.get("merchant", "")),
                    item=str(record.get("item", "")),
                    account=str(record.get("account", "")),
                    payment_method=str(record.get("payment_method", "")),
                    raw_type=str(record.get("raw_type", "")),
                    refund_original_uid=record.get("refund_original_uid"),
                    refund_uids=list(record.get("refund_record_uids", [])),
                    refund_total_cents=cents_of(record.get("refund_total", 0)),
                    matched_uids=list(record.get("matched_record_uids", [])),
                    match_ids=list(record.get("match_ids", [])),
                    review_reasons=list(record.get("review_reasons", [])),
                    category=str(row.get("类别", "")),
                    subcategory=str(row.get("子类", "")),
                    tags=str(row.get("标签", "")),
                    basis=basis,
                    classification_state=state,
                    import_index=row.get("import_index"),
                )
            )
        for match in ledger["matches"]:
            db.add(
                Match(
                    job_id=job_id,
                    match_id=match["match_id"],
                    match_type=match["match_type"],
                    confidence=match["confidence"],
                    decision=match["decision"],
                    reason=match["reason"],
                    platform_uids=match["platform_record_uids"],
                    bank_uids=match["bank_record_uids"],
                    original_uids=match["original_record_uids"],
                    refund_uids=match["refund_record_uids"],
                    account=match["account"],
                    amount_cents=cents_of(match["amount"]) if match["amount"] is not None else None,
                    time_delta_seconds=match["time_delta_seconds"],
                )
            )
        db.flush()


def rerun_job(db: DBSession, job_id: str) -> None:
    """Reset a failed/interrupted job back to the queue (raw files must exist)."""

    job = db.get(Job, job_id)
    if job is None:
        raise ValueError("任务不存在")
    if job.status not in {"failed", "interrupted"}:
        raise ValueError("仅失败或中断的任务可以重新运行")
    if not uploads_dir(job_id).exists() or not any(uploads_dir(job_id).iterdir()):
        raise ValueError("原始文件已被删除，无法重新运行；请重新上传")
    job.status = "queued"
    job.error = ""
    _add_event(db, job_id, "queued", "重新运行")
    db.commit()
    get_worker().submit(job_id)


def pending_retention_cleanup(db: DBSession) -> None:
    """Delete raw files whose retention window has elapsed."""

    privacy = settings_service.get_privacy_settings(db)
    hours = retention_hours(privacy)
    if hours is None:
        return
    now = utcnow()
    for job in db.scalars(select(Job).where(Job.status.in_(["needs_review", "done", "failed"]))):
        finished = job.finished_at
        if finished is None:
            continue
        deadline = finished + timedelta(hours=hours)
        if isinstance(deadline, datetime) and deadline <= now:
            delete_raw_files(db, job.id)
    db.commit()
