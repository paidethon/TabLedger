"""Application settings service: AI config (encrypted key), account mappings, taxonomy."""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.config import get_settings
from app.core.context import BankCard, EngineConfig
from app.db.models import AccountMapping, ClassificationRule, Setting

AI_SETTINGS_KEY = "ai"
PRIVACY_SETTINGS_KEY = "privacy"
APP_SETTINGS_KEY = "app"

AI_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "provider": "",
    "base_url": "",
    "api_key_encrypted": "",
    "api_key_hint": "",
    "model": "",
    "timeout_seconds": 60,
    "retry": 1,
    "batch_size": 60,
    "max_output_tokens": 800,
}

PRIVACY_DEFAULTS: dict[str, Any] = {
    # 处理完成后原始上传文件的处理策略: immediate | 1h | 24h | 7d
    "raw_file_retention": "immediate",
}


def get_setting(db: DBSession, key: str, defaults: dict[str, Any]) -> dict[str, Any]:
    row = db.get(Setting, key)
    value = dict(defaults)
    if row is not None and isinstance(row.value, dict):
        value.update(row.value)
    return value


def put_setting(db: DBSession, key: str, value: dict[str, Any]) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = utcnow_fallback()


def utcnow_fallback():
    from app.db.models import utcnow

    return utcnow()


# ---------------------------------------------------------------------------
# API key encryption (authenticated encryption via Fernet, key = TAB_SECRET_KEY)
# ---------------------------------------------------------------------------


def _fernet() -> Fernet:
    secret = get_settings().ensure_secret_key()
    digest = __import__("hashlib").sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return ""


def secret_hint(plaintext: str) -> str:
    if not plaintext:
        return ""
    tail = plaintext[-4:] if len(plaintext) >= 8 else "••••"
    return f"{plaintext[:2]}••••••{tail}" if len(plaintext) > 8 else f"••••••{tail}"


# ---------------------------------------------------------------------------
# AI settings API surface (key never returned to the browser)
# ---------------------------------------------------------------------------


def get_ai_settings(db: DBSession) -> dict[str, Any]:
    value = get_setting(db, AI_SETTINGS_KEY, AI_DEFAULTS)
    return {k: v for k, v in value.items() if k != "api_key_encrypted"}


def update_ai_settings(db: DBSession, patch: dict[str, Any]) -> dict[str, Any]:
    current = get_setting(db, AI_SETTINGS_KEY, AI_DEFAULTS)
    for field in ("enabled", "provider", "base_url", "model", "timeout_seconds", "retry", "batch_size", "max_output_tokens"):
        if field in patch:
            current[field] = patch[field]
    if "api_key" in patch:
        plaintext = str(patch["api_key"] or "")
        current["api_key_encrypted"] = encrypt_secret(plaintext)
        current["api_key_hint"] = secret_hint(plaintext)
    put_setting(db, AI_SETTINGS_KEY, current)
    db.commit()
    return get_ai_settings(db)


def ai_api_key(db: DBSession) -> str:
    value = get_setting(db, AI_SETTINGS_KEY, AI_DEFAULTS)
    return decrypt_secret(str(value.get("api_key_encrypted", "")))


# ---------------------------------------------------------------------------
# Account mappings → EngineConfig
# ---------------------------------------------------------------------------


def list_account_mappings(db: DBSession) -> list[dict[str, str]]:
    rows = db.scalars(select(AccountMapping)).all()
    return [
        {
            "id": str(row.id),
            "source": row.source,
            "tail": row.tail,
            "display_name": row.display_name,
            "yimu_account": row.yimu_account,
            "id_prefix": row.id_prefix,
        }
        for row in rows
    ]


def replace_account_mappings(db: DBSession, mappings: list[dict[str, str]]) -> None:
    for existing in db.scalars(select(AccountMapping)).all():
        db.delete(existing)
    for item in mappings:
        db.add(
            AccountMapping(
                source=str(item.get("source", ""))[:32],
                tail=str(item.get("tail", ""))[:16],
                display_name=str(item.get("display_name", ""))[:64],
                yimu_account=str(item.get("yimu_account", ""))[:64],
                id_prefix=str(item.get("id_prefix", ""))[:32],
            )
        )
    db.commit()


def owner_names_setting(db: DBSession) -> list[str]:
    app_cfg = get_setting(db, APP_SETTINGS_KEY, {})
    names = app_cfg.get("owner_names", [])
    return [str(n) for n in names if n]


def update_owner_names(db: DBSession, names: list[str]) -> None:
    app_cfg = get_setting(db, APP_SETTINGS_KEY, {})
    app_cfg["owner_names"] = [str(n).strip() for n in names if str(n).strip()]
    put_setting(db, APP_SETTINGS_KEY, app_cfg)
    db.commit()


def engine_config(db: DBSession) -> EngineConfig:
    mappings = list_account_mappings(db)
    cards = tuple(
        BankCard(
            source=m["source"],
            tail=m["tail"],
            display_name=m["display_name"],
            yimu_account=m["yimu_account"],
            id_prefix=m["id_prefix"],
        )
        for m in mappings
        if m["source"] and m["tail"] and m["display_name"]
    )
    app_cfg = get_setting(db, APP_SETTINGS_KEY, {})
    ledger_name = str(app_cfg.get("ledger_name", "日常账本")) or "日常账本"
    extra_tokens = tuple(str(t) for t in app_cfg.get("extra_internal_tokens", []))
    return EngineConfig(
        owner_names=tuple(owner_names_setting(db)),
        bank_cards=cards,
        ledger_name=ledger_name,
        extra_internal_tokens=extra_tokens,
    )


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


def load_taxonomy() -> dict[str, Any]:
    import importlib.resources as resources

    data_pkg = resources.files("app.core.data")
    taxonomy_path = data_pkg.joinpath("taxonomy.json")
    return json.loads(taxonomy_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------


def rules_for_engine(db: DBSession) -> list[dict[str, str]]:
    rows = db.scalars(select(ClassificationRule).where(ClassificationRule.enabled.is_(True))).all()
    rules = [
        {
            "键": row.match_key,
            "收支": row.direction,
            "类别": row.category,
            "子类": row.subcategory,
            "标签": row.tags,
            "来源": row.origin,
        }
        for row in rows
    ]
    rules.sort(key=lambda r: len(r["键"]), reverse=True)
    return rules


def list_rules(db: DBSession) -> list[dict[str, Any]]:
    rows = db.scalars(select(ClassificationRule)).all()
    return [
        {
            "id": row.id,
            "match_key": row.match_key,
            "direction": row.direction,
            "category": row.category,
            "subcategory": row.subcategory,
            "tags": row.tags,
            "origin": row.origin,
            "enabled": row.enabled,
        }
        for row in rows
    ]


def upsert_rule(db: DBSession, data: dict[str, Any]) -> int:
    row_id = data.get("id")
    if row_id:
        row = db.get(ClassificationRule, int(row_id))
        if row is None:
            raise ValueError("规则不存在")
        row.match_key = str(data.get("match_key", row.match_key))
        row.direction = str(data.get("direction", row.direction))
        row.category = str(data.get("category", row.category))
        row.subcategory = str(data.get("subcategory", row.subcategory))
        row.tags = str(data.get("tags", row.tags))
        row.enabled = bool(data.get("enabled", row.enabled))
    else:
        row = ClassificationRule(
            match_key=str(data.get("match_key", "")),
            direction=str(data.get("direction", "")),
            category=str(data.get("category", "")),
            subcategory=str(data.get("subcategory", "")),
            tags=str(data.get("tags", "")),
            origin=str(data.get("origin", "user")),
        )
        db.add(row)
    db.commit()
    return row.id


def delete_rule(db: DBSession, rule_id: int) -> None:
    row = db.get(ClassificationRule, rule_id)
    if row is not None:
        db.delete(row)
        db.commit()


def import_rules_csv(db: DBSession, rows: list[dict[str, str]], origin: str = "import") -> int:
    """Import legacy 分类词典 CSV rows (匹配键,收支,类别,子类,标签)."""

    added = 0
    existing = {
        (r.match_key, r.direction, r.category, r.subcategory)
        for r in db.scalars(select(ClassificationRule)).all()
    }
    for row in rows:
        key = (row.get("匹配键") or row.get("match_key") or "").strip()
        if not key:
            continue
        direction = (row.get("收支") or row.get("direction") or "").strip()
        category = (row.get("类别") or row.get("category") or "").strip()
        subcategory = (row.get("子类") or row.get("subcategory") or "").strip()
        tags = (row.get("标签") or row.get("tags") or "").strip()
        identity = (key, direction, category, subcategory)
        if identity in existing:
            continue
        existing.add(identity)
        db.add(
            ClassificationRule(
                match_key=key, direction=direction, category=category, subcategory=subcategory, tags=tags, origin=origin
            )
        )
        added += 1
    db.commit()
    return added


# ---------------------------------------------------------------------------
# Privacy settings & backup
# ---------------------------------------------------------------------------


def get_privacy_settings(db: DBSession) -> dict[str, Any]:
    return get_setting(db, PRIVACY_SETTINGS_KEY, PRIVACY_DEFAULTS)


def update_privacy_settings(db: DBSession, patch: dict[str, Any]) -> dict[str, Any]:
    current = get_privacy_settings(db)
    if "raw_file_retention" in patch:
        value = patch["raw_file_retention"]
        if value not in {"immediate", "1h", "24h", "7d"}:
            raise ValueError("非法的保留周期")
        current["raw_file_retention"] = value
    put_setting(db, PRIVACY_SETTINGS_KEY, current)
    db.commit()
    return current


def export_backup(db: DBSession) -> dict[str, Any]:
    """Config backup: rules, account mappings, app settings. No API key, no bills."""

    return {
        "version": 1,
        "exported_at": utcnow_fallback().isoformat(),
        "rules": list_rules(db),
        "account_mappings": list_account_mappings(db),
        "app": get_setting(db, APP_SETTINGS_KEY, {}),
        "privacy": get_privacy_settings(db),
        "ai": {k: v for k, v in get_ai_settings(db).items() if k != "api_key_hint"},
    }
