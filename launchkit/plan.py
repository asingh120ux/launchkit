"""Load and validate plan.yaml, do the budget math, print a founder-readable tree."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

DAYS_PER_MONTH = 30

META_PRIMARY_TEXT_MAX = 125
META_HEADLINE_MAX = 40

GOOGLE_HEADLINE_MAX = 30
GOOGLE_DESCRIPTION_MAX = 90
GOOGLE_LONG_HEADLINE_MAX = 90
GOOGLE_MIN_HEADLINES = 3
GOOGLE_MIN_DESCRIPTIONS = 2

# The plan names the country and language; the API wants constant ids.
GOOGLE_GEO_TARGETS = {"US": 2840}
GOOGLE_LANGUAGES = {"en": 1000}

GOOGLE_CAMPAIGN_TYPES = ("SEARCH", "PERFORMANCE_MAX")
GOOGLE_BIDDING = ("MAXIMIZE_CONVERSIONS", "TARGET_IMPRESSION_SHARE")
GOOGLE_MATCH_TYPES = ("EXACT", "PHRASE", "BROAD")
META_AUDIENCE_TYPES = ("website_visitors", "website_event")


@dataclass
class Problem:
    scope: str  # "account", "meta" or "google"
    message: str

    def __str__(self) -> str:
        return f"[{self.scope}] {self.message}"


@dataclass
class Result:
    problems: list = field(default_factory=list)
    tree: str = ""
    created_files: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def problems_for(self, platform: str) -> list:
        return [p for p in self.problems if p.scope in ("account", platform)]


def load_plan(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a YAML mapping")
    return data


def is_cut(campaign: dict) -> bool:
    if str(campaign.get("status", "")).upper() == "CUT":
        return True
    budget = campaign.get("daily_budget")
    return isinstance(budget, (int, float)) and budget == 0


def active_campaigns(platform_block: dict) -> list:
    return [c for c in (platform_block or {}).get("campaigns") or [] if not is_cut(c)]


def daily_total(platform_block: dict) -> float:
    total = 0
    for c in active_campaigns(platform_block):
        b = c.get("daily_budget")
        if isinstance(b, (int, float)) and not isinstance(b, bool):
            total += b
    return total


def money(x: float) -> str:
    if float(x).is_integer():
        return f"${int(x):,}"
    return f"${x:,.2f}"


# ---------------------------------------------------------------------------
# small schema helpers
# ---------------------------------------------------------------------------

def _num(d: dict, key: str, where: str, scope: str, problems: list) -> bool:
    v = (d or {}).get(key)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
        problems.append(Problem(scope, f"{where}: '{key}' must be a number >= 0 (got {v!r})"))
        return False
    return True


def _str(d: dict, key: str, where: str, scope: str, problems: list) -> bool:
    v = (d or {}).get(key)
    if not isinstance(v, str) or not v.strip():
        problems.append(Problem(scope, f"{where}: '{key}' is missing or empty"))
        return False
    return True


def _list(d: dict, key: str, where: str, scope: str, problems: list) -> bool:
    v = (d or {}).get(key)
    if not isinstance(v, list) or not v:
        problems.append(Problem(scope, f"{where}: '{key}' must be a non-empty list"))
        return False
    return True


def _len(text, limit: int, what: str, scope: str, problems: list) -> None:
    if not isinstance(text, str):
        problems.append(Problem(scope, f"{what}: expected text, got {text!r}"))
    elif len(text) > limit:
        problems.append(Problem(scope, f"{what} is {len(text)} characters, limit {limit}: \"{text}\""))


def _dupes(names: list, where: str, scope: str, problems: list) -> None:
    seen = set()
    for n in names:
        if n in seen:
            problems.append(Problem(scope, f"{where}: duplicate name \"{n}\" (names must be unique, re-runs match on name)"))
        seen.add(n)


# ---------------------------------------------------------------------------
# platform checks
# ---------------------------------------------------------------------------

def _check_account(plan: dict, problems: list) -> None:
    acct = plan.get("account")
    if not isinstance(acct, dict):
        problems.append(Problem("account", "'account' section is missing"))
        return
    _str(acct, "landing_url", "account", "account", problems)
    url = acct.get("landing_url")
    if isinstance(url, str) and ("example.com" in url or "PLACEHOLDER" in url.upper()):
        # Learned on the first live run: Google rejects an ad at creation (PROHIBITED policy topic)
        # when the final URL is a placeholder domain, even on a PAUSED campaign.
        problems.append(Problem("account", f"landing_url is a placeholder ({url}); use the real page"))
    _num(acct, "total_monthly_budget", "account", "account", problems)


def _check_meta_targeting(t, aw: str, s: str, problems: list) -> None:
    if not isinstance(t, dict):
        problems.append(Problem(s, f"{aw}: 'targeting' is missing"))
        return
    age = t.get("age")
    if not (isinstance(age, list) and len(age) == 2 and all(isinstance(x, int) for x in age) and age[0] <= age[1]):
        problems.append(Problem(s, f"{aw}: targeting.age must be [min, max] (got {age!r})"))
    if not isinstance(t.get("advantage_audience"), bool):
        problems.append(Problem(s, f"{aw}: targeting.advantage_audience must be true or false"))
    if "interests" in t and not (isinstance(t["interests"], list) and t["interests"]):
        problems.append(Problem(s, f"{aw}: targeting.interests must be a non-empty list"))
    for key in ("include_audiences", "exclude_audiences"):
        for aud in t.get(key) or []:
            if not isinstance(aud, dict) or not aud.get("name"):
                problems.append(Problem(s, f"{aw}: {key} entry needs a name"))
                continue
            name = aud["name"]
            if aud.get("type") not in META_AUDIENCE_TYPES:
                problems.append(Problem(s, f"{aw}: audience \"{name}\" type must be one of {', '.join(META_AUDIENCE_TYPES)}"))
            days = aud.get("retention_days")
            if not isinstance(days, int) or days <= 0:
                problems.append(Problem(s, f"{aw}: audience \"{name}\" needs retention_days > 0"))
            if aud.get("type") == "website_event" and not aud.get("event"):
                problems.append(Problem(s, f"{aw}: audience \"{name}\" (website_event) needs an event"))


def _check_meta(plan: dict, base_dir: str, problems: list) -> None:
    s = "meta"
    meta = plan.get("meta")
    if not isinstance(meta, dict):
        problems.append(Problem(s, "'meta' section is missing"))
        return
    _num(meta, "monthly_budget", "meta", s, problems)
    _str(meta, "pixel_event", "meta", s, problems)
    _list(meta, "countries", "meta", s, problems)
    ads = meta.get("ads")
    if not isinstance(ads, dict) or not ads:
        problems.append(Problem(s, "meta: 'ads' library is missing or empty"))
        ads = {}

    for name, ad in ads.items():
        where = f"meta ad {name}"
        if not isinstance(ad, dict):
            problems.append(Problem(s, f"{where}: must be a mapping"))
            continue
        if ad.get("format", "image") != "image":
            problems.append(Problem(s, f"{where}: format '{ad.get('format')}' not supported, only 'image'"))
        for key in ("file", "primary_text", "headline", "cta"):
            _str(ad, key, where, s, problems)
        if isinstance(ad.get("primary_text"), str):
            _len(ad["primary_text"], META_PRIMARY_TEXT_MAX, f"{where} primary_text", s, problems)
        if isinstance(ad.get("headline"), str):
            _len(ad["headline"], META_HEADLINE_MAX, f"{where} headline", s, problems)
        f = ad.get("file")
        if isinstance(f, str) and f and not os.path.isfile(os.path.join(base_dir, f)):
            problems.append(Problem(s, f"{where}: file {f} does not exist"))

    campaigns = meta.get("campaigns")
    if not isinstance(campaigns, list) or not campaigns:
        problems.append(Problem(s, "meta: 'campaigns' must be a non-empty list"))
        return
    _dupes([c.get("name") for c in campaigns if isinstance(c, dict)], "meta campaigns", s, problems)

    for c in campaigns:
        if not _str(c, "name", "meta campaign", s, problems):
            continue
        where = f"meta campaign \"{c['name']}\""
        _num(c, "daily_budget", where, s, problems)
        if is_cut(c):
            continue
        _str(c, "objective", where, s, problems)
        if not _list(c, "ad_sets", where, s, problems):
            continue
        _dupes([a.get("name") for a in c["ad_sets"] if isinstance(a, dict)], f"{where} ad sets", s, problems)
        for a in c["ad_sets"]:
            if not _str(a, "name", f"{where} ad set", s, problems):
                continue
            aw = f"{where} > ad set \"{a['name']}\""
            _str(a, "optimization_goal", aw, s, problems)
            _str(a, "bid_strategy", aw, s, problems)
            _check_meta_targeting(a.get("targeting"), aw, s, problems)
            if _list(a, "ads", aw, s, problems):
                for ad_name in a["ads"]:
                    if ad_name not in ads:
                        problems.append(Problem(s, f"{aw}: ad \"{ad_name}\" is not in meta.ads"))


def _check_google_bidding(c: dict, where: str, s: str, problems: list) -> None:
    bidding = c.get("bidding") or {}
    strategy = bidding.get("strategy")
    if strategy not in GOOGLE_BIDDING:
        problems.append(Problem(s, f"{where}: bidding.strategy must be one of {', '.join(GOOGLE_BIDDING)}"))
        return
    if strategy == "TARGET_IMPRESSION_SHARE":
        if c.get("type") == "PERFORMANCE_MAX":
            problems.append(Problem(s, f"{where}: Performance Max cannot use TARGET_IMPRESSION_SHARE"))
        _str(bidding, "location", f"{where} bidding", s, problems)
        share = bidding.get("impression_share")
        if not isinstance(share, (int, float)) or not 0 < share <= 1:
            problems.append(Problem(s, f"{where}: bidding.impression_share must be between 0 and 1"))
        _num(bidding, "max_cpc", f"{where} bidding", s, problems)
    else:
        tcpa = bidding.get("target_cpa")
        if tcpa is not None and (not isinstance(tcpa, (int, float)) or tcpa <= 0):
            problems.append(Problem(s, f"{where}: bidding.target_cpa must be a number or null"))


def _check_search(c: dict, where: str, s: str, problems: list) -> None:
    if not _list(c, "ad_groups", where, s, problems):
        return
    _dupes([a.get("name") for a in c["ad_groups"] if isinstance(a, dict)], f"{where} ad groups", s, problems)
    for ag in c["ad_groups"]:
        if not _str(ag, "name", f"{where} ad group", s, problems):
            continue
        aw = f"{where} > ad group \"{ag['name']}\""
        if _list(ag, "keywords", aw, s, problems):
            for kw in ag["keywords"]:
                if not isinstance(kw, dict) or not kw.get("text"):
                    problems.append(Problem(s, f"{aw}: keyword entry needs text"))
                elif kw.get("match") not in GOOGLE_MATCH_TYPES:
                    problems.append(Problem(s, f"{aw}: keyword \"{kw['text']}\" match must be one of {', '.join(GOOGLE_MATCH_TYPES)}"))
        if _list(ag, "ads", aw, s, problems):
            for i, ad in enumerate(ag["ads"], 1):
                heads = ad.get("headlines") or []
                descs = ad.get("descriptions") or []
                if len(heads) < GOOGLE_MIN_HEADLINES:
                    problems.append(Problem(s, f"{aw} ad {i}: needs at least {GOOGLE_MIN_HEADLINES} headlines (has {len(heads)})"))
                if len(descs) < GOOGLE_MIN_DESCRIPTIONS:
                    problems.append(Problem(s, f"{aw} ad {i}: needs at least {GOOGLE_MIN_DESCRIPTIONS} descriptions (has {len(descs)})"))
                for h in heads:
                    _len(h, GOOGLE_HEADLINE_MAX, f"{aw} ad {i} headline", s, problems)
                for d in descs:
                    _len(d, GOOGLE_DESCRIPTION_MAX, f"{aw} ad {i} description", s, problems)


def _check_pmax(c: dict, where: str, base_dir: str, s: str, problems: list) -> None:
    if not _list(c, "asset_groups", where, s, problems):
        return
    _dupes([a.get("name") for a in c["asset_groups"] if isinstance(a, dict)], f"{where} asset groups", s, problems)
    for ag in c["asset_groups"]:
        if not _str(ag, "name", f"{where} asset group", s, problems):
            continue
        aw = f"{where} > asset group \"{ag['name']}\""
        _str(ag, "final_url", aw, s, problems)
        _str(ag, "business_name", aw, s, problems)
        if _str(ag, "long_headline", aw, s, problems):
            _len(ag["long_headline"], GOOGLE_LONG_HEADLINE_MAX, f"{aw} long_headline", s, problems)
        if _list(ag, "headlines", aw, s, problems):
            for h in ag["headlines"]:
                _len(h, GOOGLE_HEADLINE_MAX, f"{aw} headline", s, problems)
        if _list(ag, "descriptions", aw, s, problems):
            for d in ag["descriptions"]:
                _len(d, GOOGLE_DESCRIPTION_MAX, f"{aw} description", s, problems)
        images = ag.get("images") or {}
        files = {"images.landscape": images.get("landscape"), "images.square": images.get("square"), "logo": ag.get("logo")}
        for key, f in files.items():
            if not isinstance(f, str) or not f:
                problems.append(Problem(s, f"{aw}: '{key}' is missing"))
            elif not os.path.isfile(os.path.join(base_dir, f)):
                problems.append(Problem(s, f"{aw}: file {f} does not exist"))


def _check_google(plan: dict, base_dir: str, problems: list) -> None:
    s = "google"
    g = plan.get("google")
    if not isinstance(g, dict):
        problems.append(Problem(s, "'google' section is missing"))
        return
    _num(g, "monthly_budget", "google", s, problems)
    if g.get("country") not in GOOGLE_GEO_TARGETS:
        problems.append(Problem(s, f"google: country {g.get('country')!r} not mapped (known: {', '.join(GOOGLE_GEO_TARGETS)})"))
    if g.get("language") not in GOOGLE_LANGUAGES:
        problems.append(Problem(s, f"google: language {g.get('language')!r} not mapped (known: {', '.join(GOOGLE_LANGUAGES)})"))

    campaigns = g.get("campaigns")
    if not isinstance(campaigns, list) or not campaigns:
        problems.append(Problem(s, "google: 'campaigns' must be a non-empty list"))
        return
    _dupes([c.get("name") for c in campaigns if isinstance(c, dict)], "google campaigns", s, problems)

    for c in campaigns:
        if not _str(c, "name", "google campaign", s, problems):
            continue
        where = f"google campaign \"{c['name']}\""
        _num(c, "daily_budget", where, s, problems)
        if is_cut(c):
            continue
        ctype = c.get("type")
        if ctype not in GOOGLE_CAMPAIGN_TYPES:
            problems.append(Problem(s, f"{where}: type must be one of {', '.join(GOOGLE_CAMPAIGN_TYPES)}"))
            continue
        _check_google_bidding(c, where, s, problems)
        if ctype == "SEARCH":
            _check_search(c, where, s, problems)
        else:
            _check_pmax(c, where, base_dir, s, problems)


def _check_budgets(plan: dict, problems: list) -> list:
    """Returns printable budget lines and appends a problem for any overage."""
    lines = []
    total_daily = 0
    for platform in ("meta", "google"):
        block = plan.get(platform) or {}
        daily = daily_total(block)
        total_daily += daily
        monthly = daily * DAYS_PER_MONTH
        cap = block.get("monthly_budget")
        label = platform.capitalize() + ":"
        if isinstance(cap, (int, float)):
            if monthly <= cap:
                verdict = f"OK ({money(cap - monthly)} headroom)"
            else:
                verdict = f"OVER by {money(monthly - cap)}"
                problems.append(Problem(platform, f"{platform.capitalize()} plans {money(monthly)}/month ({money(daily)}/day x {DAYS_PER_MONTH}), over its monthly_budget {money(cap)} by {money(monthly - cap)}"))
            lines.append(f"  {label:<8}{money(daily)}/day x {DAYS_PER_MONTH} = {money(monthly)}/month, cap {money(cap)}: {verdict}")
        else:
            lines.append(f"  {label:<8}{money(daily)}/day x {DAYS_PER_MONTH} = {money(monthly)}/month, cap missing")
    monthly = total_daily * DAYS_PER_MONTH
    cap = (plan.get("account") or {}).get("total_monthly_budget")
    if isinstance(cap, (int, float)):
        if monthly <= cap:
            verdict = f"OK ({money(cap - monthly)} headroom)"
        else:
            verdict = f"OVER by {money(monthly - cap)}"
            problems.append(Problem("account", f"Meta + Google plan {money(monthly)}/month, over total_monthly_budget {money(cap)} by {money(monthly - cap)}"))
        lines.append(f"  {'Total:':<8}{money(total_daily)}/day x {DAYS_PER_MONTH} = {money(monthly)}/month, cap {money(cap)}: {verdict}")
    return lines


# ---------------------------------------------------------------------------
# tree
# ---------------------------------------------------------------------------

def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _camp_line(c: dict, extra: str = "") -> str:
    name = c.get("name", "?")
    if is_cut(c):
        return f"{name}{extra}  CUT (skipped, not built)"
    b = c.get("daily_budget") or 0
    return f"{name}{extra}  {money(b)}/day, {money(b * DAYS_PER_MONTH)}/month"


def _branch(items: list, prefix: str, out: list) -> None:
    """items: list of (text, children)."""
    for i, (text, children) in enumerate(items):
        last = i == len(items) - 1
        out.append(f"{prefix}{'`-- ' if last else '|-- '}{text}")
        if children:
            _branch(children, prefix + ("    " if last else "|   "), out)


def _meta_items(meta: dict) -> list:
    items = []
    for c in meta.get("campaigns") or []:
        children = []
        if not is_cut(c):
            sets = c.get("ad_sets") or []
            per_set = (c.get("daily_budget") or 0) / max(len(sets), 1)
            for a in sets:
                t = a.get("targeting") or {}
                bits = []
                if t.get("advantage_audience"):
                    bits.append("Advantage+ audience")
                if t.get("interests"):
                    bits.append(_plural(len(t["interests"]), "interest"))
                if t.get("include_audiences"):
                    bits.append("incl. " + ", ".join(x.get("name", "?") for x in t["include_audiences"]))
                if t.get("exclude_audiences"):
                    bits.append("excl. " + ", ".join(x.get("name", "?") for x in t["exclude_audiences"]))
                detail = f" [{'; '.join(bits)}]" if bits else ""
                n = len(a.get("ads") or [])
                children.append((f"Ad set: {a.get('name')}  {money(per_set)}/day, {_plural(n, 'ad')}{detail}", []))
        items.append((_camp_line(c), children))
    return items


def _google_items(g: dict) -> list:
    items = []
    for c in g.get("campaigns") or []:
        children = []
        if not is_cut(c):
            neg = c.get("negatives") or []
            if neg:
                children.append((f"Negatives: {_plural(len(neg), 'campaign-level keyword')}", []))
            for ag in c.get("ad_groups") or []:
                nk = len(ag.get("keywords") or [])
                na = len(ag.get("ads") or [])
                children.append((f"Ad group: {ag.get('name')}  {_plural(nk, 'keyword')}, {_plural(na, 'ad')}", []))
            for ag in c.get("asset_groups") or []:
                nh = len(ag.get("headlines") or [])
                nd = len(ag.get("descriptions") or [])
                children.append((f"Asset group: {ag.get('name')}  {_plural(nh, 'headline')}, {_plural(nd, 'description')}, 3 images", []))
            if c.get("brand_exclusions"):
                children.append(("Brand exclusions: set by hand in the UI (not built)", []))
        ctype = f" [{c.get('type')}]" if c.get("type") else ""
        items.append((_camp_line(c, ctype), children))
    return items


def print_tree(plan: dict) -> str:
    out = []
    acct = plan.get("account") or {}
    out.append(f"PLAN: {acct.get('label', '')} (month {acct.get('month', '?')})")
    out.append(f"Landing page: {acct.get('landing_url', '?')}")
    out.append("")
    meta = plan.get("meta") or {}
    md = daily_total(meta)
    out.append(f"META  {money(md)}/day, {money(md * DAYS_PER_MONTH)}/month (cap {money(meta.get('monthly_budget') or 0)})")
    _branch(_meta_items(meta), "", out)
    out.append("")
    g = plan.get("google") or {}
    gd = daily_total(g)
    out.append(f"GOOGLE  {money(gd)}/day, {money(gd * DAYS_PER_MONTH)}/month (cap {money(g.get('monthly_budget') or 0)})")
    _branch(_google_items(g), "", out)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def validate(plan: dict, base_dir: str = ".", make_files: bool = True) -> Result:
    """Run every check. Creates missing placeholder images first (make_files=True)."""
    from .creative import make_placeholders

    result = Result()
    if make_files:
        result.created_files = make_placeholders(plan, base_dir)
    problems = result.problems
    _check_account(plan, problems)
    _check_meta(plan, base_dir, problems)
    _check_google(plan, base_dir, problems)
    budget_lines = _check_budgets(plan, problems)
    result.tree = print_tree(plan) + "\n\nBUDGET CHECK (monthly = daily x 30)\n" + "\n".join(budget_lines)
    return result
