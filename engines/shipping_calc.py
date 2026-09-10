"""FBM shipping cost estimator (what the seller pays to ship to the buyer).

Pure functions, no I/O. Phase 1 supports two paths:

  * Flat per-package cost (from the onboarding "average shipping cost"
    answer) -- the default and most reliable input.
  * A coarse weight-based worst-case lookup, used when no flat cost is
    given. This is intentionally a rough stub so the *interface* is right;
    real carrier zone math arrives in a later phase.

Packaging cost is added on top of whichever base is used.
"""

DEFAULT_PACKAGING_COST = 1.00

# Coarse worst-case (far-zone) ground rates by weight ceiling, in dollars.
# Ordered ascending by weight limit (lb); the last entry catches heavier items.
_WEIGHT_TABLE = [
    (1.0, 5.00),
    (2.0, 7.00),
    (3.0, 9.00),
    (5.0, 12.00),
    (10.0, 16.00),
    (20.0, 24.00),
    (float("inf"), 40.00),
]


def _weight_based_cost(weight_lb: float) -> float:
    for limit, cost in _WEIGHT_TABLE:
        if weight_lb <= limit:
            return cost
    return _WEIGHT_TABLE[-1][1]


def fbm_shipping(
    weight_lb: float = 0.0,
    dims=None,
    carrier: str = "USPS",
    flat_cost=None,
    packaging_cost: float = DEFAULT_PACKAGING_COST,
) -> float:
    """Estimate the FBM shipping cost to the buyer.

    Precedence:
      1. A known per-item ``weight_lb`` (> 0) -> weight-based estimate. This is the
         most accurate per-lead figure and corrects the flat guess in BOTH
         directions: it stops over-charging light items (a poly-mailer dog toy
         doesn't cost the flat ~$8) AND stops under-charging heavy items (a flat
         rate makes a 5 lb item look cheaper to ship than it is).
      2. Else the operator's measured ``flat_cost`` (good for personal sourcing
         where they know their average).
      3. Else the weight table at the given/zero weight.
    ``packaging_cost`` is always added on top. ``dims``/``carrier`` reserved for
    later zone math.
    """
    if weight_lb and weight_lb > 0:
        base = _weight_based_cost(weight_lb)
    elif flat_cost is not None:
        base = flat_cost
    else:
        base = _weight_based_cost(weight_lb)
    return base + packaging_cost
