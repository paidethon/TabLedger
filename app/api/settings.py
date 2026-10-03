"""Settings API: AI config, account mappings, rules, taxonomy, privacy, backup."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.ai import service as ai_service
from app.auth.deps import CSRF, DB, CurrentUser
from app.services import settings_service

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


# -- AI ------------------------------------------------------------------


class AISettingsPatch(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = Field(default=None, max_length=512)
    model: str | None = None
    timeout_seconds: int | None = None
    retry: int | None = None
    batch_size: int | None = None
    max_output_tokens: int | None = None
    confidence_threshold: float | None = None


@router.get("/ai")
def get_ai_settings_api(db: DB, user: CurrentUser) -> dict:
    return settings_service.get_ai_settings(db)


@router.put("/ai", dependencies=[CSRF])
def update_ai_settings_api(payload: AISettingsPatch, db: DB, user: CurrentUser) -> dict:
    patch = payload.model_dump(exclude_none=True)
    return settings_service.update_ai_settings(db, patch)


@router.post("/ai/test", dependencies=[CSRF])
def test_ai_connection(db: DB, user: CurrentUser) -> dict:
    """User-initiated minimal connectivity test (tiny prompt)."""

    return ai_service.test_connection(db)


@router.get("/ai/models")
def list_ai_models(db: DB, user: CurrentUser) -> dict:
    return ai_service.list_models(db)


# -- Account mappings & owner names ---------------------------------------


class MappingIn(BaseModel):
    source: str
    tail: str
    display_name: str
    yimu_account: str = ""
    id_prefix: str = ""


class MappingsPatch(BaseModel):
    mappings: list[MappingIn]
    owner_names: list[str] = Field(default_factory=list)
    ledger_name: str | None = None


@router.get("/accounts")
def get_accounts(db: DB, user: CurrentUser) -> dict:
    app_cfg = settings_service.get_setting(db, settings_service.APP_SETTINGS_KEY, {})
    return {
        "mappings": settings_service.list_account_mappings(db),
        "owner_names": settings_service.owner_names_setting(db),
        "ledger_name": str(app_cfg.get("ledger_name", "日常账本")),
    }


@router.put("/accounts", dependencies=[CSRF])
def put_accounts(payload: MappingsPatch, db: DB, user: CurrentUser) -> dict:
    settings_service.replace_account_mappings(db, [m.model_dump() for m in payload.mappings])
    settings_service.update_owner_names(db, payload.owner_names)
    if payload.ledger_name is not None:
        app_cfg = settings_service.get_setting(db, settings_service.APP_SETTINGS_KEY, {})
        app_cfg["ledger_name"] = payload.ledger_name[:64] or "日常账本"
        settings_service.put_setting(db, settings_service.APP_SETTINGS_KEY, app_cfg)
        db.commit()
    return get_accounts_value(db)


def get_accounts_value(db) -> dict:
    app_cfg = settings_service.get_setting(db, settings_service.APP_SETTINGS_KEY, {})
    return {
        "mappings": settings_service.list_account_mappings(db),
        "owner_names": settings_service.owner_names_setting(db),
        "ledger_name": str(app_cfg.get("ledger_name", "日常账本")),
    }


# -- Rules -----------------------------------------------------------------


class RuleIn(BaseModel):
    id: int | None = None
    match_key: str = Field(min_length=1, max_length=256)
    direction: str = ""
    category: str = Field(min_length=1, max_length=64)
    subcategory: str = ""
    tags: str = ""
    enabled: bool = True


@router.get("/rules")
def get_rules(db: DB, user: CurrentUser) -> dict:
    return {"rules": settings_service.list_rules(db)}


@router.put("/rules", dependencies=[CSRF])
def upsert_rule_api(payload: RuleIn, db: DB, user: CurrentUser) -> dict:
    rule_id = settings_service.upsert_rule(db, payload.model_dump())
    return {"ok": True, "id": rule_id}


@router.delete("/rules/{rule_id}", dependencies=[CSRF])
def delete_rule_api(rule_id: int, db: DB, user: CurrentUser) -> dict:
    settings_service.delete_rule(db, rule_id)
    return {"ok": True}


@router.post("/rules/import", dependencies=[CSRF])
async def import_rules(file: UploadFile, db: DB, user: CurrentUser) -> dict:
    """Import a legacy 分类词典 CSV (匹配键,收支,类别,子类,标签)."""

    import csv
    import io

    data = await file.read()
    if len(data) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大")
    await file.close()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="需要 UTF-8 编码的 CSV") from exc
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    added = settings_service.import_rules_csv(db, rows)
    return {"ok": True, "added": added}


@router.get("/rules/export")
def export_rules(db: DB, user: CurrentUser) -> dict:
    """Export rules as CSV rows (for backup)."""

    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["匹配键", "收支", "类别", "子类", "标签"])
    for rule in settings_service.list_rules(db):
        writer.writerow([rule["match_key"], rule["direction"], rule["category"], rule["subcategory"], rule["tags"]])
    from fastapi.responses import Response

    return Response(
        content=out.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="tabledger_rules.csv"'},
    )


# -- Taxonomy / privacy / backup -------------------------------------------


@router.get("/taxonomy")
def get_taxonomy(db: DB, user: CurrentUser) -> dict:
    from app.core.classification.rules import INCOME_CATEGORIES

    return {"expense": settings_service.load_taxonomy(), "income": INCOME_CATEGORIES}


class PrivacyPatch(BaseModel):
    raw_file_retention: str | None = None


@router.get("/privacy")
def get_privacy(db: DB, user: CurrentUser) -> dict:
    from sqlalchemy import func, select

    from app.db.models import AIClassificationCache

    cache_count = int(db.scalar(select(func.count(AIClassificationCache.id))) or 0)
    return {
        **settings_service.get_privacy_settings(db),
        "ai_cache_count": cache_count,
        "ai_enabled": settings_service.get_ai_settings(db).get("enabled", False),
        "ai_key_saved": bool(settings_service.get_setting(db, settings_service.AI_SETTINGS_KEY, {}).get("api_key_encrypted")),
        "ai_fields_sent": ["merchant（脱敏后）", "item（脱敏后）", "flow", "金额档(small/medium/large)"],
    }


@router.put("/privacy", dependencies=[CSRF])
def put_privacy(payload: PrivacyPatch, db: DB, user: CurrentUser) -> dict:
    return settings_service.update_privacy_settings(db, payload.model_dump(exclude_none=True))


@router.delete("/ai-cache", dependencies=[CSRF])
def clear_ai_cache(db: DB, user: CurrentUser) -> dict:
    from sqlalchemy import select as sa_select

    from app.db.models import AIClassificationCache

    count = 0
    for row in db.scalars(sa_select(AIClassificationCache)).all():
        db.delete(row)
        count += 1
    db.commit()
    return {"ok": True, "deleted": count}


@router.get("/backup")
def backup(db: DB, user: CurrentUser) -> dict:
    return settings_service.export_backup(db)
