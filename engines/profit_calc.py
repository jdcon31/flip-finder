"""Core FBM profit calculation engine.

Pure functions + dataclasses, no I/O. This is the mathematical core every
downstream component consumes (Telegram deal card, pre-filter, lead list).

Convention: every ``*_pct`` field is a *whole-number percent* (2.0 == 2%),
matching the store-config schema in PLAN.md (e.g. ``portal_cashback_pct: 1.0``).
Percents are divided by 100 internally.

Cost side (PLAN.md "Profit Calculation Engine"):
    taxable_base   = sale_price - coupon_savings
    sales_tax      = tax(taxable_base, state)             # 0 if resale-exempt
    amount_charged = taxable_base + sales_tax + source_shipping
    cc_cashback    = cc_pct%   * amount_charged           # card rebates the charge
    portal_cashback= portal_pct% * taxable_base           # portals pay on merch
    effective_cost = amount_charged - cc_cashback - portal_cashback

Credit-card cashback has two modes (``ProfitSettings.cashback_mode``):
  * "lead_list" (default) -- use a flat generic rate (``LEAD_LIST_CASHBACK_PCT``,
    2%) regardless of the deal, because subscribers all hold different cards.
    The lead list should carry CASHBACK_DISCLAIMER so buyers know actual ROI
    varies with their own card setup.
  * "personal" -- use ``Deal.cc_cashback_pct`` (the operator's auto-selected
    best card) for personal-use sourcing.

Revenue side:
    net_profit = sell_price - effective_cost - referral_fee - closing_fee
                 - fbm_shipping - return_reserve - misc_buffer
    roi_pct    = net_profit / effective_cost * 100
    margin_pct = net_profit / sell_price * 100
"""

from dataclasses import dataclass, field
from typing import Optional

from engines import fba_fees, shipping_calc
from utils import tax_tables

# Generic cashback rate (%) assumed for lead-list profit math, since
# subscribers all hold different cards. Personal-use mode uses the operator's
# actual best-card rate instead (Deal.cc_cashback_pct).
LEAD_LIST_CASHBACK_PCT = 2.0

CASHBACK_DISCLAIMER = (
    f"ROI assumes a generic {LEAD_LIST_CASHBACK_PCT:.0f}% card cashback; "
    "your actual return varies with your own card and portal setup."
)


@dataclass
class ProfitSettings:
    """Operator-level settings that affect every profit calc."""

    state: str = "IL"
    resale_exempt_states: tuple = ()
    return_reserve_pct: float = 3.0
    misc_buffer_pct: float = 1.0
    default_packaging_cost: float = shipping_calc.DEFAULT_PACKAGING_COST
    default_shipping_cost: Optional[float] = None  # operator's measured avg flat cost
    carrier: str = "USPS"
    # "lead_list" -> generic flat cashback; "personal" -> deal's best-card rate.
    cashback_mode: str = "lead_list"
    lead_list_cashback_pct: float = LEAD_LIST_CASHBACK_PCT

    @classmethod
    def from_dict(cls, d: dict) -> "ProfitSettings":
        """Build from an onboarding settings.json dict, ignoring extra keys."""
        d = d or {}
        return cls(
            state=d.get("state", "IL"),
            resale_exempt_states=tuple(d.get("resale_exempt_states", ())),
            return_reserve_pct=float(d.get("return_reserve_pct", 3.0)),
            misc_buffer_pct=float(d.get("misc_buffer_pct", 1.0)),
            default_packaging_cost=float(
                d.get("default_packaging_cost", shipping_calc.DEFAULT_PACKAGING_COST)
            ),
            default_shipping_cost=d.get("avg_shipping_cost", d.get("default_shipping_cost")),
            carrier=d.get("shipping_carrier", d.get("carrier", "USPS")),
            cashback_mode=d.get("cashback_mode", "lead_list"),
            lead_list_cashback_pct=float(
                d.get("lead_list_cashback_pct", LEAD_LIST_CASHBACK_PCT)
            ),
        )


@dataclass
class Deal:
    """A single sourcing opportunity to evaluate."""

    sale_price: float
    sell_price: float  # Amazon Buy Box (or Keepa 90-day avg)
    category: str = "default"
    list_price: Optional[float] = None  # retailer list/original price (informational)
    coupon_savings: float = 0.0
    source_shipping: float = 0.0
    cc_cashback_pct: float = 0.0
    portal_cashback_pct: float = 0.0
    weight: float = 0.0  # lb, for weight-based FBM shipping fallback
    dims: Optional[tuple] = None
    fbm_shipping_flat: Optional[float] = None  # per-deal override of settings.default_shipping_cost


@dataclass
class ProfitResult:
    net_profit: float
    roi_pct: float
    margin_pct: float
    breakdown: dict = field(default_factory=dict)
    # Set when cashback used a generic lead-list rate; None in personal mode.
    cashback_note: Optional[str] = None

    def rounded(self, ndigits: int = 2) -> "ProfitResult":
        """Return a copy with all money/percent values rounded for display."""
        return ProfitResult(
            net_profit=round(self.net_profit, ndigits),
            roi_pct=round(self.roi_pct, ndigits),
            margin_pct=round(self.margin_pct, ndigits),
            breakdown={k: round(v, ndigits) for k, v in self.breakdown.items()},
            cashback_note=self.cashback_note,
        )


def calculate(deal: Deal, settings: ProfitSettings = None) -> ProfitResult:
    """Compute net profit, ROI, and margin for a deal. Full precision kept;
    round only at presentation via ``ProfitResult.rounded()``.
    """
    settings = settings or ProfitSettings()

    # --- Cost side ---
    taxable_base = deal.sale_price - deal.coupon_savings
    tax = tax_tables.sales_tax(taxable_base, settings.state, settings.resale_exempt_states)
    amount_charged = taxable_base + tax + deal.source_shipping
    # Lead-list mode uses a flat generic cashback (subscribers' cards differ);
    # personal mode uses the deal's auto-selected best-card rate.
    cc_pct = (
        settings.lead_list_cashback_pct
        if settings.cashback_mode == "lead_list"
        else deal.cc_cashback_pct
    )
    cc_cashback = (cc_pct / 100.0) * amount_charged
    portal_cashback = (deal.portal_cashback_pct / 100.0) * taxable_base
    effective_cost = amount_charged - cc_cashback - portal_cashback

    # --- Revenue / Amazon fee side ---
    referral = fba_fees.referral_fee(deal.category, deal.sell_price)
    closing = fba_fees.closing_fee(deal.category)
    flat = deal.fbm_shipping_flat if deal.fbm_shipping_flat is not None else settings.default_shipping_cost
    fbm_ship = shipping_calc.fbm_shipping(
        weight_lb=deal.weight,
        dims=deal.dims,
        carrier=settings.carrier,
        flat_cost=flat,
        packaging_cost=settings.default_packaging_cost,
    )
    return_reserve = (settings.return_reserve_pct / 100.0) * deal.sell_price
    misc_buffer = (settings.misc_buffer_pct / 100.0) * deal.sell_price

    net_profit = (
        deal.sell_price
        - effective_cost
        - referral
        - closing
        - fbm_ship
        - return_reserve
        - misc_buffer
    )
    roi_pct = (net_profit / effective_cost * 100.0) if effective_cost else 0.0
    margin_pct = (net_profit / deal.sell_price * 100.0) if deal.sell_price else 0.0

    breakdown = {
        "sale_price": deal.sale_price,
        "coupon_savings": deal.coupon_savings,
        "taxable_base": taxable_base,
        "sales_tax": tax,
        "source_shipping": deal.source_shipping,
        "amount_charged": amount_charged,
        "cc_cashback_pct": cc_pct,
        "cc_cashback": cc_cashback,
        "portal_cashback": portal_cashback,
        "effective_cost": effective_cost,
        "sell_price": deal.sell_price,
        "referral_fee": referral,
        "closing_fee": closing,
        "fbm_shipping": fbm_ship,
        "return_reserve": return_reserve,
        "misc_buffer": misc_buffer,
        "net_profit": net_profit,
    }

    note = CASHBACK_DISCLAIMER if settings.cashback_mode == "lead_list" else None
    return ProfitResult(
        net_profit=net_profit,
        roi_pct=roi_pct,
        margin_pct=margin_pct,
        breakdown=breakdown,
        cashback_note=note,
    )
