"""Deterministic ranking, selection, compliance and totals (REQ 1.3-1.6, 1.9, 1.16)."""

from dataclasses import dataclass, field

from pmr import flash as fl
from pmr.ic_log import Activity
from pmr.model import Issue
from pmr.prior_pmr import Guideline

PER_ROLE = 2  # REQ 1.4: two contributors, two detractors, two largest positions
# Role prefixes by rank.
_ORDINAL = ["largest", "second-largest"]


# A fund called out in the report, with every role it earned.
@dataclass
class Featured:
    name: str
    sleeve: str
    roles: list[str] = field(default_factory=list)  # e.g. ["largest contributor", "largest individual position"]


def attribution(flash: fl.Flash) -> list[dict]:
    """Every fund's dollar contribution, most positive first; ties broken by name."""
    rows = []
    for f in flash.funds:
        rows.append({
            "fund": f.name,
            "sleeve": f.sleeve,
            "income": fl.num(f.cash.get(fl.INCOME)),
            "appreciation": fl.num(f.cash.get(fl.APPREC)),
            "fees": fl.num(f.cash.get(fl.FEES)),
            "contribution": f.contribution(),
            # Locators of the three inputs.
            "locs": [f.cash[k].loc for k in (fl.INCOME, fl.APPREC, fl.FEES) if k in f.cash],
        })
    return sorted(rows, key=lambda r: (-r["contribution"], r["fund"]))


def select_featured(flash: fl.Flash) -> tuple[list[Featured], list[str]]:
    """Funds that earn a role callout, each listed once with all of its roles (REQ 1.4-1.5)."""
    ranked = attribution(flash)
    # Top positive contributions.
    contributors = [r for r in ranked if r["contribution"] > 0][:PER_ROLE]
    # Most negative contributions.
    detractors = sorted((r for r in ranked if r["contribution"] < 0), key=lambda r: (r["contribution"], r["fund"]))[:PER_ROLE]
    # Largest market values.
    positions = sorted(flash.funds, key=lambda f: (-fl.num(f.funding.get("Market Value ($)")), f.name))[:PER_ROLE]

    # Insertion order: contributors, then detractors, then positions.
    featured: dict[str, Featured] = {}

    # Add a role, merging into the fund's entry if already featured.
    def add(name, sleeve, role):
        featured.setdefault(name, Featured(name, sleeve)).roles.append(role)

    for i, r in enumerate(contributors):
        add(r["fund"], r["sleeve"], f"{_ORDINAL[i]} contributor")
    for i, r in enumerate(detractors):
        add(r["fund"], r["sleeve"], f"{_ORDINAL[i]} detractor")
    for i, f in enumerate(positions):
        add(f.name, f.sleeve, f"{_ORDINAL[i]} individual position")

    # Explain a short detractor list instead of padding it.
    notes = []
    if len(detractors) < PER_ROLE:
        notes.append(f"Only {len(detractors)} fund(s) had a negative dollar contribution; "
                     "every other fund contributed positively.")
    return list(featured.values()), notes


def compliance(flash: fl.Flash, guidelines: list[Guideline], benchmark: str) -> tuple[list[dict], list[Issue]]:
    """Evaluate each guideline from the prior PMR's table against this quarter's flash (REQ 1.9)."""
    results, issues = [], []
    # Route each guideline to its rule by keywords in its name or limit.
    for g in guidelines:
        name, limit = g.name.value.lower(), g.limit.value.lower()
        if "sector" in name or "sector" in limit:
            results.append(_sector(flash, g))
        elif "ex-us" in limit:
            results.append(_ex_us(flash, g))
        elif "leverage" in name or "loan-to-value" in name:
            results.append(_leverage(flash, g, issues))
        elif "benchmark" in limit:
            results.append(_return_objective(flash, g, benchmark))
        else:
            issues.append(Issue("blocker", "guideline_unknown",
                                f"No rule to evaluate guideline {g.name.value!r} ({g.limit.value!r})", g.name.loc))
    # A rule that could not compute a status is a blocker.
    for r in results:
        if r["status"] is None:
            issues.append(Issue("blocker", "compliance_unavailable", f"{r['guideline']}: {r['actual_text']}",
                                r["limit_loc"]))
        # A breach is reported as found, never hidden, but a reviewer must see it before sign-off.
        elif r["status"] == "Not in compliance":
            issues.append(Issue("warning", "compliance_breach",
                                f"{r['guideline']}: {r['actual_text']} breaches {r['limit']}",
                                (r["actual_locs"] or [r["limit_loc"]])[0]))
    return results, issues


# One compliance row; ok=None means the rule could not be evaluated.
def _result(g: Guideline, actual_text: str, ok: bool | None, actual_locs: list[str]) -> dict:
    status = None if ok is None else ("In compliance" if ok else "Not in compliance")
    return {"guideline": g.name.value, "limit": g.limit.value, "limit_loc": g.limit.loc,
            "actual_text": actual_text, "status": status, "actual_locs": actual_locs}


# Largest property sector must be at or under the limit.
def _sector(flash: fl.Flash, g: Guideline) -> dict:
    mix = {k: v for k, v in flash.property_mix.items() if isinstance(v.value, (int, float))}
    if not mix or not g.limit_values:
        return _result(g, "property mix or limit not found", None, [])
    sector, fact = max(mix.items(), key=lambda kv: kv[1].value)
    return _result(g, f"{fact.value:.1f}% ({sector})", fact.value <= g.limit_values[0], [fact.loc])


# Ex-US share of market value must be at or under the limit.
def _ex_us(flash: fl.Flash, g: Guideline) -> dict:
    fact = flash.funding_rows.get(fl.EX_US_LABEL, {}).get(fl.FUNDING_PCT)
    if fact is None or not g.limit_values:
        return _result(g, f"{fl.EX_US_LABEL!r} row or limit not found", None, [])
    return _result(g, f"{fact.value:.1f}% Ex-US", fact.value <= g.limit_values[0], [fact.loc])


def _leverage(flash: fl.Flash, g: Guideline, issues: list[Issue]) -> dict:
    """Limits are listed in the order the prior PMR's actual column names the sleeves."""
    actual = g.actual.value.lower()
    # Sleeves ordered by where their first word appears in last quarter's actual text.
    order = sorted((s for s in flash.sleeves if s.split()[0].lower() in actual),
                   key=lambda s: actual.index(s.split()[0].lower()))
    # Need one limit per sleeve, every sleeve mapped.
    if len(order) != len(g.limit_values) or len(order) != len(flash.sleeves):
        return _result(g, f"cannot map limits {g.limit_values} to sleeves {flash.sleeves}", None, [])
    parts, oks, locs = [], [], []
    # Each sleeve's LTV subtotal against its own limit.
    for sleeve, lim in zip(order, g.limit_values):
        fact = flash.cash_rows.get(sleeve, {}).get(fl.LTV)
        if fact is None or not isinstance(fact.value, (int, float)):
            return _result(g, f"no {fl.LTV} subtotal for {sleeve!r}", None, [])
        parts.append(f"{fact.value:.1f}% {sleeve.split()[0].lower()}")
        oks.append(fact.value <= lim)
        locs.append(fact.loc)
    # Compliant only if every sleeve is.
    return _result(g, " / ".join(parts), all(oks), locs)


# 1-year net portfolio return must beat the benchmark.
def _return_objective(flash: fl.Flash, g: Guideline, benchmark: str) -> dict:
    port = flash.performance.get("1 Year (%) TNET")
    bench = flash.annual_rows.get(benchmark, {}).get("1 Year Net")
    if port is None or bench is None:
        return _result(g, "1-year net portfolio or benchmark return not found", None, [])
    return _result(g, f"{port.value:.2f}% vs {bench.value:.2f}% (1-Yr)", port.value > bench.value,
                   [port.loc, bench.loc])


def find_benchmark(flash: fl.Flash, prior_text: str) -> tuple[str | None, list[Issue]]:
    """The client's benchmark: the AnnReturns row the prior PMR names (REQ 1.2, 1.18)."""
    # Every AnnReturns row except the client's own.
    labels = [lab for lab in flash.annual_rows if fl.strip_portfolio(lab) != flash.client_name]
    named = [lab for lab in labels if lab.lower() in prior_text.lower()]
    # Exactly one must be named; otherwise a reviewer picks.
    if len(named) == 1:
        return named[0], []
    return None, [Issue("blocker", "benchmark_unresolved",
                        f"Expected exactly one {fl.ANNUAL} row named in the prior PMR; found {named or 'none'} "
                        f"among {labels}", flash.file)]


def committed_totals(flash: fl.Flash, activity: Activity) -> dict:
    """REQ 1.6: flash commitments plus every open approval, and the matching position count."""
    funded = flash.funding_rows.get(fl.TOTAL_LABEL, {}).get("Commitment Amount")
    # IC amounts are in millions.
    open_musd = sum(a.amount_musd or 0 for a in activity.open_approvals)
    return {
        "funded_commitments": funded.to_json() if funded else None,
        "open_approvals_usd": open_musd * 1_000_000,
        "total_committed_or_approved_usd": fl.num(funded) + open_musd * 1_000_000,
        "funded_positions": len(flash.funds),
        "open_positions": len(activity.open_approvals),
        "total_positions": len(flash.funds) + len(activity.open_approvals),
        "open_approval_locs": [a.row.loc for a in activity.open_approvals],
        "open_approval_amount_locs": [a.row.amount_loc for a in activity.open_approvals],
        "funded_position_locs": [f.name_loc for f in flash.funds],
    }
