"""State sales tax base rates (all 50 states + DC).

Pure data + a single helper. Rates are *state base* rates expressed as
fractions (0.0625 == 6.25%); local/county add-ons are out of scope for
Phase 1. Per PLAN.md: AK, DE, MT, NH, OR have no sales tax; IL (operator's
state) is 6.25%; IN/MS/TN/RI are 7.0%.
"""

# State base sales tax rates as fractions.
STATE_TAX_RATES = {
    "AL": 0.0400,
    "AK": 0.0000,
    "AZ": 0.0560,
    "AR": 0.0650,
    "CA": 0.0725,
    "CO": 0.0290,
    "CT": 0.0635,
    "DE": 0.0000,
    "DC": 0.0600,
    "FL": 0.0600,
    "GA": 0.0400,
    "HI": 0.0400,
    "ID": 0.0600,
    "IL": 0.0625,
    "IN": 0.0700,
    "IA": 0.0600,
    "KS": 0.0650,
    "KY": 0.0600,
    "LA": 0.0445,
    "ME": 0.0550,
    "MD": 0.0600,
    "MA": 0.0625,
    "MI": 0.0600,
    "MN": 0.06875,
    "MS": 0.0700,
    "MO": 0.04225,
    "MT": 0.0000,
    "NE": 0.0550,
    "NV": 0.0685,
    "NH": 0.0000,
    "NJ": 0.06625,
    "NM": 0.04875,
    "NY": 0.0400,
    "NC": 0.0475,
    "ND": 0.0500,
    "OH": 0.0575,
    "OK": 0.0450,
    "OR": 0.0000,
    "PA": 0.0600,
    "RI": 0.0700,
    "SC": 0.0600,
    "SD": 0.0420,
    "TN": 0.0700,
    "TX": 0.0625,
    "UT": 0.0610,
    "VT": 0.0600,
    "VA": 0.0530,
    "WA": 0.0650,
    "WV": 0.0600,
    "WI": 0.0500,
    "WY": 0.0400,
}


def state_rate(state: str) -> float:
    """Return the base tax rate fraction for a 2-letter state code."""
    return STATE_TAX_RATES.get((state or "").strip().upper(), 0.0)


def sales_tax(subtotal: float, state: str, resale_exempt_states=None) -> float:
    """Compute sales tax on a subtotal.

    Returns 0 if the state is in the operator's resale-certificate exemption
    list (i.e. the operator buys tax-free for resale there), otherwise applies
    the state's base rate.
    """
    code = (state or "").strip().upper()
    if resale_exempt_states:
        exempt = {s.strip().upper() for s in resale_exempt_states}
        if code in exempt:
            return 0.0
    return subtotal * state_rate(code)
