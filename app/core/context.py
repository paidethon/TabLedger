"""Engine configuration.

The legacy pipeline hard-coded the statement owner's name and bank card
tails.  In TabLedger everything user-specific lives in user configuration
(database settings, or a private config file for local regression) and is
passed into the engine through :class:`EngineConfig`.  No personal data
exists in this repository.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BankCard:
    """A personal bank card the user reconciles against.

    ``source`` is the bank statement source name (e.g. a bank name);
    ``tail`` is the last digits of the card number, used only as a match
    marker inside platform payment-method strings;
    ``display_name`` is the canonical account name used in records and
    exports; ``yimu_account`` is the target account name in the Yimu
    ledger (defaults to ``display_name``).
    """

    source: str
    tail: str
    display_name: str
    yimu_account: str = ""
    id_prefix: str = ""

    def resolved_yimu_account(self) -> str:
        return self.yimu_account or self.display_name


@dataclass(frozen=True)
class EngineConfig:
    """Everything user-specific the deterministic engine needs."""

    owner_names: tuple[str, ...] = ()
    bank_cards: tuple[BankCard, ...] = ()
    ledger_name: str = "日常账本"
    extra_internal_tokens: tuple[str, ...] = field(default=())

    def card_for_source(self, source: str) -> BankCard | None:
        for card in self.bank_cards:
            if card.source == source:
                return card
        return None

    def source_account(self, source: str) -> str:
        card = self.card_for_source(source)
        return card.display_name if card else source

    def source_id_prefix(self, source: str) -> str:
        card = self.card_for_source(source)
        if not card:
            return source
        if card.id_prefix:
            return card.id_prefix
        letters = "".join(ch for ch in card.tail if ch.isalnum())
        return f"{source}{letters}"

    def canonical_bank_account(self, value: object) -> str:
        """Map any spelling of a configured bank card to its canonical name.

        Mirrors the legacy behaviour: the card tail must appear in the text
        together with the bank name (or its short alias).  Returns "" for
        anything that is not a configured card.
        """

        text = str(value or "")
        if not text:
            return ""
        for card in self.bank_cards:
            aliases = _bank_aliases(card.source)
            if card.tail and card.tail in text and any(alias in text for alias in aliases):
                return card.display_name
        return ""

    def is_owner_text(self, text: object) -> bool:
        value = str(text or "")
        return bool(value) and any(name and name in value for name in self.owner_names)


_BANK_ALIAS_REGISTRY: dict[str, tuple[str, ...]] = {
    "工商银行": ("工商银行", "工行"),
    "中国银行": ("中国银行", "中行"),
}


def _bank_aliases(source: str) -> tuple[str, ...]:
    return _BANK_ALIAS_REGISTRY.get(source, (source,))


def default_engine_config() -> EngineConfig:
    """Configuration with no personal data: no owner names, no cards.

    With this config the engine still parses and reconciles platform bills,
    but cross-source matching against bank cards is disabled until the user
    configures their cards in settings.
    """

    return EngineConfig(owner_names=(), bank_cards=(), ledger_name="日常账本")
