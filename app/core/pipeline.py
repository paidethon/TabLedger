"""End-to-end deterministic pipeline: extract → reconcile → classify.

The export step is separate because the review UI may add classification
overrides (manual fixes, accepted AI suggestions) before rows are written.
"""

from __future__ import annotations

from typing import Any

from app.core.classification.apply import build_import_rows
from app.core.context import EngineConfig
from app.core.extraction import BillInput, detect_and_extract, extract_bank_all, reconcile_payapps
from app.core.reconciliation.ledger import LedgerBuilder
from app.core.reconciliation.predicates import SOURCE_TAG
from app.core.reconciliation.refunds import auto_direct_bank_refund_groups


def run_pipeline(
    inputs: list[BillInput],
    config: EngineConfig,
    rules: list[dict[str, str]],
    categories: dict[str, Any],
    overrides: dict[tuple[str, str], dict[str, str]] | None = None,
    direct_refund_groups: list | None = None,
    passwords: list[str] | None = None,
) -> dict[str, Any]:
    records_by_source, file_meta, errors = detect_and_extract(inputs, config, passwords)
    bank_records, bank_qa = extract_bank_all(records_by_source)
    errors.extend(bank_qa.get("errors", []))

    payapps: list[dict] = []
    for source in ("wx", "zfb"):
        payapps.extend(records_by_source.get(source, []))
    payapps.sort(key=lambda r: r["datetime"])
    declared_report = reconcile_payapps(payapps, file_meta)

    if direct_refund_groups is None:
        direct_refund_groups = auto_direct_bank_refund_groups(bank_records)
        refund_mode = "auto"
    else:
        refund_mode = "override"

    builder = LedgerBuilder(config, payapps, bank_records, direct_refund_groups=direct_refund_groups)
    ledger = builder.run()

    rows, unmatched = build_import_rows(config, ledger, categories, rules, overrides)

    return {
        "ledger": ledger,
        "import_rows": rows,
        "unmatched": {f"{direction}|{key}": slot for (direction, key), slot in unmatched.items()},
        "extraction": {
            "files": file_meta,
            "banks": bank_qa["banks"],
            "declared_reconciliation": declared_report,
            "source_tags": SOURCE_TAG,
        },
        "errors": errors,
        "refund_mode": refund_mode,
        "direct_refund_groups": [
            [
                [original[0], original[1]],
                [[refund[0], refund[1]] for refund in refunds],
            ]
            for original, refunds in builder.direct_bank_refund_groups_input
        ],
    }
