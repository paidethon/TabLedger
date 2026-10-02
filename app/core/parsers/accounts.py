"""Flow / payment-method / funding-account normalisation for platform bills.

Card-specific funding accounts are resolved through the user's
:class:`EngineConfig`; nothing is hard-coded.
"""

from __future__ import annotations

from app.core.context import EngineConfig

GENERIC_INTERNAL_TOKENS = (
    "支付宝小荷包-自动攒",
    "微信零钱充值账户",
    "财付通-零钱通",
)


def clean_text(value) -> str:
    return "" if value is None else str(value).strip()


def clean_placeholder(value) -> str:
    text = clean_text(value)
    return "" if text in {"/", "-", "--"} else text


def normalize_flow(source: str, raw_flow: str) -> tuple[str, list[str]]:
    value = clean_text(raw_flow)
    if value in {"支出", "收入", "不计收支"}:
        return value, []
    if source == "wx" and value in {"/", "", "中性交易"}:
        return "不计收支", ["flow_slash_to_neutral"]
    raise ValueError(f"Unknown {source} flow value: {raw_flow!r}")


def normalize_payment_method(value) -> tuple[str, list[str]]:
    raw = clean_text(value)
    if raw in {"", "/", "-", "--"}:
        return "", ["payment_method_placeholder"]
    return raw, []


def normalize_platform_account(
    config: EngineConfig,
    source: str,
    payment_method: str,
    *,
    status: str,
    raw_type: str,
    flow: str,
) -> tuple[str, list[str]]:
    """Normalize the funding account while retaining the full raw method."""

    flags: list[str] = []
    method = clean_text(payment_method)
    if "&" in method:
        flags.append("composite_payment_method")

    # A configured personal bank card used directly as the funding source.
    canonical = config.canonical_bank_account(method)
    if canonical:
        return canonical, flags

    if source == "wx":
        if method == "零钱通":
            return "微信零钱通", flags
        if method == "零钱":
            return "微信零钱", flags
        # WeChat omits the payment method for received transfers/red packets.
        flags.append("account_inferred_from_blank_payment_method")
        if "零钱通" in status:
            return "微信零钱通", flags
        if any(token in status for token in ("零钱", "收钱", "到账")):
            return "微信零钱", flags
        if "零钱通" in raw_type:
            return "微信零钱通", flags
        return "微信账户", flags

    if "亲情卡" in method:
        return "支付宝亲情卡", flags
    if "余额宝" in method:
        return "余额宝", flags
    if method in {"账户余额", "余额"}:
        return "支付宝余额", flags
    if "支付宝小荷包" in method:
        return "支付宝小荷包", flags
    if not method:
        flags.append("account_inferred_from_blank_payment_method")
        # Incoming transfers and receipts normally settle to the Alipay balance.
        return "支付宝余额" if flow == "收入" else "支付宝账户", flags

    # A few tiny transactions are completely covered by a promotion and have
    # no cash funding component.  Keep them visible and explicitly identified.
    flags.append("discount_only_payment_method")
    return "支付宝优惠", flags
