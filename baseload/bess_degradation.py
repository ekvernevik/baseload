"""BESS chemistry degradation model — LFP / NMC.

IMPORTANT — these are placeholder parameters, not vendor or TSO/GO figures
-----------------------------------------------------------------------------
We do not have a cell/system datasheet or a TSO/grid-operator published
standard to calibrate against. Every number below is an order-of-magnitude
estimate drawn from public literature on grid-scale Li-ion storage, chosen to
be directionally correct (LFP degrades slower and cycles harder than NMC,
cold weather hurts both) rather than precise. Swap `CHEMISTRY_PRESETS` for
real datasheet values the moment a vendor or TSO figure is available — every
field below is where that number goes.

Model, deliberately kept simple
-----------------------------------------------------------------------------
Capacity fade = calendar fade (linear in elapsed years) + cycle fade (linear
in cumulative equivalent full cycles, "EFC"), summed and floored at zero.
This additive-linear form is the standard simplification used in early-stage
BESS pre-feasibility studies — it is not a rainflow / Arrhenius / Wöhler
stress model, and does not vary fade rate with depth-of-discharge, average
SOC, or temperature history beyond the single ambient-temperature derate
below. Good enough to show degradation matters and rank chemistries; not a
substitute for a warranty-grade lifetime model.

Round-trip efficiency is treated as constant over the asset's life (only
capacity fades); the only temperature effect modeled is a one-time derate of
that efficiency for cold ambient conditions (Li-ion charge acceptance and
internal resistance both worsen below ~0 degC). No seasonal/monthly
temperature variation — one ambient_temp_c figure per run.

Cycling limits are a flat warranty-style ceiling (max equivalent full cycles
per day), enforced as an LP constraint so the optimizer cannot dispatch the
battery harder than the chemistry would tolerate.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChemistryParams:
    """One chemistry's degradation/derating parameters. All placeholders — see module docstring."""

    name: str
    roundtrip_efficiency_bol: float    # beginning-of-life round-trip efficiency (fraction)
    calendar_fade_pct_per_yr: float    # % capacity lost per elapsed year, reference ambient
    cycle_fade_pct_per_1000_efc: float  # % capacity lost per 1000 equivalent full cycles
    eol_retention: float               # capacity fraction defining end-of-life (industry-standard ~0.80)
    max_efc_per_day: float             # warranty-style cycling limit, equivalent full cycles/day
    cold_derate_temp_c: float          # ambient temp below which efficiency derates
    cold_derate_pct_per_c: float       # % efficiency lost per degC below cold_derate_temp_c


#: LFP (lithium iron phosphate) — slower calendar/cycle fade, tolerates deeper/more frequent
#: cycling, marginally lower round-trip efficiency than NMC. Dominant chemistry for new-build
#: grid-scale storage as of 2026.
LFP = ChemistryParams(
    name="LFP",
    roundtrip_efficiency_bol=0.92,
    calendar_fade_pct_per_yr=2.0,
    cycle_fade_pct_per_1000_efc=2.5,
    eol_retention=0.80,
    max_efc_per_day=1.5,
    cold_derate_temp_c=0.0,
    cold_derate_pct_per_c=0.3,
)

#: NMC (nickel manganese cobalt) — higher energy density, slightly higher round-trip
#: efficiency, but faster calendar and cycle fade and a tighter cycling ceiling.
NMC = ChemistryParams(
    name="NMC",
    roundtrip_efficiency_bol=0.93,
    calendar_fade_pct_per_yr=3.0,
    cycle_fade_pct_per_1000_efc=5.0,
    eol_retention=0.80,
    max_efc_per_day=1.0,
    cold_derate_temp_c=0.0,
    cold_derate_pct_per_c=0.4,
)

CHEMISTRY_PRESETS: dict[str, ChemistryParams] = {"LFP": LFP, "NMC": NMC}


def get_chemistry(chemistry: str | ChemistryParams) -> ChemistryParams:
    """Resolve a chemistry name ("LFP"/"NMC") or pass an existing ChemistryParams through."""
    if isinstance(chemistry, ChemistryParams):
        return chemistry
    try:
        return CHEMISTRY_PRESETS[chemistry.upper()]
    except KeyError as exc:
        raise ValueError(
            f"Unknown chemistry '{chemistry}'. Available: {sorted(CHEMISTRY_PRESETS)}."
        ) from exc


def thermal_efficiency_multiplier(ambient_temp_c: float, chem: ChemistryParams) -> float:
    """Fractional derate applied to round-trip efficiency for cold ambient conditions.

    1.0 at/above ``cold_derate_temp_c``; declines linearly below it. Floored at 0.5 so an
    extreme input can't produce a negative or absurd multiplier.
    """
    if ambient_temp_c >= chem.cold_derate_temp_c:
        return 1.0
    delta_c = chem.cold_derate_temp_c - ambient_temp_c
    multiplier = 1.0 - (chem.cold_derate_pct_per_c / 100.0) * delta_c
    return max(multiplier, 0.5)


def capacity_retention(age_years: float, cumulative_efc: float, chem: ChemistryParams) -> float:
    """Fraction of nameplate energy capacity remaining, given elapsed age and cumulative cycling.

    Additive linear calendar + cycle fade (see module docstring for why this is a
    deliberately simple placeholder model). Floored at 0.0.
    """
    calendar_loss = (chem.calendar_fade_pct_per_yr / 100.0) * age_years
    cycle_loss = (chem.cycle_fade_pct_per_1000_efc / 100.0) * (cumulative_efc / 1000.0)
    return max(1.0 - calendar_loss - cycle_loss, 0.0)


def reached_eol(retention: float, chem: ChemistryParams) -> bool:
    """True once capacity retention has fallen to/below the chemistry's end-of-life threshold."""
    return retention <= chem.eol_retention


def max_efc_for_horizon(chem: ChemistryParams, horizon_years: float) -> float:
    """Warranty-style cycling ceiling (equivalent full cycles) for a horizon of *horizon_years*."""
    return chem.max_efc_per_day * 365.0 * horizon_years
