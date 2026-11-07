"""Meta Marketing API builder. Plain Graph API calls with requests, no SDK.

Everything is created with status PAUSED. Dry run prints every call and sends nothing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlencode

import requests

from .plan import is_cut, money

GRAPH = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v22.0"

PLACEHOLDERS = {
    "META_AD_ACCOUNT_ID": "act_PLACEHOLDER",
    "META_PAGE_ID": "PAGE_ID_PLACEHOLDER",
    "META_PIXEL_ID": "PIXEL_ID_PLACEHOLDER",
}


class MetaAPIError(RuntimeError):
    pass


class ConfigError(RuntimeError):
    pass


@dataclass
class MetaConfig:
    token: str
    version: str
    ad_account: str
    page_id: str
    pixel_id: str


def load_config(dry_run: bool) -> tuple:
    """Read META_* env vars. Dry run fills gaps with placeholders and says so; live refuses."""
    notes = []
    values = {}
    missing = []
    for key in ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_PAGE_ID", "META_PIXEL_ID"):
        v = (os.environ.get(key) or "").strip()
        if not v:
            missing.append(key)
            if dry_run and key in PLACEHOLDERS:
                v = PLACEHOLDERS[key]
                notes.append(f"{key} not set, using placeholder {v} (dry run only)")
            elif dry_run:
                notes.append(f"{key} not set (not needed for a dry run)")
        values[key] = v
    version = (os.environ.get("META_API_VERSION") or "").strip()
    if not version:
        version = DEFAULT_API_VERSION
        notes.append(f"META_API_VERSION not set, using {version}")
    if missing and not dry_run:
        raise ConfigError("missing in .env: " + ", ".join(missing))
    account = values["META_AD_ACCOUNT_ID"]
    if account and not account.startswith("act_"):
        account = "act_" + account
    return MetaConfig(values["META_ACCESS_TOKEN"], version, account, values["META_PAGE_ID"], values["META_PIXEL_ID"]), notes


def _encode(payload: dict) -> dict:
    """Graph API form encoding: objects and lists as JSON strings, booleans as true/false."""
    out = {}
    for k, v in payload.items():
        if isinstance(v, bool):
            out[k] = "true" if v else "false"
        elif isinstance(v, (dict, list)):
            out[k] = json.dumps(v, separators=(",", ":"))
        else:
            out[k] = v
    return out


def _error_text(err: dict, where: str) -> str:
    msg = err.get("error_user_msg") or err.get("message") or "unknown error"
    bits = [f"code {err.get('code')}"]
    if err.get("error_subcode"):
        bits.append(f"subcode {err['error_subcode']}")
    if err.get("fbtrace_id"):
        bits.append(f"fbtrace_id {err['fbtrace_id']}")
    detail = err.get("message") if err.get("error_user_msg") else None
    text = f"{where}: {msg} ({', '.join(bits)})"
    return text + (f" [{detail}]" if detail else "")


class MetaClient:
    """Prints every call. Sends it only when dry_run is False. The token is never printed."""

    def __init__(self, cfg: MetaConfig, dry_run: bool, out=print):
        self.cfg = cfg
        self.dry_run = dry_run
        self.out = out
        self.calls = 0
        self.base = f"{GRAPH}/{cfg.version}"
        self.session = None if dry_run else requests.Session()
        if self.session is not None:
            self.session.headers["Authorization"] = f"Bearer {cfg.token}"

    def _handle(self, resp, where: str) -> dict:
        try:
            data = resp.json()
        except ValueError:
            raise MetaAPIError(f"{where}: HTTP {resp.status_code}, response was not JSON: {resp.text[:300]}")
        if isinstance(data, dict) and "error" in data:
            raise MetaAPIError(_error_text(data["error"], where))
        if resp.status_code >= 400:
            raise MetaAPIError(f"{where}: HTTP {resp.status_code}: {str(data)[:300]}")
        return data

    def post(self, path: str, payload: dict, fake: dict, file_path: str | None = None,
             file_label: str | None = None) -> dict:
        self.calls += 1
        shown = dict(payload)
        if file_path:
            shown["filename"] = f"<file {file_label or file_path}>"
        self.out(f"POST /{path} {json.dumps(shown, ensure_ascii=False)}")
        if self.dry_run:
            return fake
        where = f"POST /{path}"
        if file_path:
            with open(file_path, "rb") as fh:
                resp = self.session.post(f"{self.base}/{path}", data=_encode(payload),
                                         files={"filename": (os.path.basename(file_path), fh, "image/png")}, timeout=120)
        else:
            resp = self.session.post(f"{self.base}/{path}", data=_encode(payload), timeout=60)
        return self._handle(resp, where)

    def get(self, path: str, params: dict, fake: dict) -> dict:
        self.calls += 1
        self.out(f"GET /{path}?{urlencode(params)}")
        if self.dry_run:
            return fake
        resp = self.session.get(f"{self.base}/{path}", params=params, timeout=60)
        return self._handle(resp, f"GET /{path}")

    def get_all(self, path: str, params: dict, fake: dict) -> list:
        """GET with paging.next followed to the end."""
        data = self.get(path, params, fake)
        rows = list(data.get("data") or [])
        nxt = (data.get("paging") or {}).get("next")
        while nxt and not self.dry_run:
            resp = self.session.get(nxt, timeout=60)
            data = self._handle(resp, f"GET /{path} (next page)")
            rows += data.get("data") or []
            nxt = (data.get("paging") or {}).get("next")
        return rows


def split_cents(daily_dollars: float, n: int) -> list:
    """Split a daily budget evenly across n ad sets, in cents. Leftover cents go to the first sets."""
    total = int(round(daily_dollars * 100))
    base, rem = divmod(total, n)
    return [base + (1 if i < rem else 0) for i in range(n)]


class MetaBuilder:
    def __init__(self, plan: dict, cfg: MetaConfig, client: MetaClient, log, base_dir: str, out=print):
        self.plan = plan
        self.meta = plan["meta"]
        self.cfg = cfg
        self.client = client
        self.log = log
        self.base_dir = base_dir
        self.out = out
        self.dry_run = client.dry_run
        self.act = cfg.ad_account
        self.landing = plan["account"]["landing_url"]
        self._this_run = set()
        self._fake_n = 0
        self._image_hashes = {}
        self._audiences = None  # name -> id, loaded once
        self._interests = {}

    # -- helpers -----------------------------------------------------------

    def _fake_id(self, kind: str) -> str:
        self._fake_n += 1
        return f"DRYRUN_{kind.upper()}_{self._fake_n}"

    def _ensure(self, type_: str, parent: str, name: str, create) -> str:
        key = (type_, parent, name)
        existing = self.log.find(type_, parent, name)
        if existing and key in self._this_run:
            self.out(f"  (reuse {type_} \"{name}\" created above: {existing})")
            return existing
        if existing:
            self.out(f"SKIP {type_} \"{name}\": already in build-log, id {existing}")
            self.log.record(type_, name, parent, existing, action="reused")
            self._this_run.add(key)
            return existing
        new_id = create()
        self.log.record(type_, name, parent, new_id)
        self._this_run.add(key)
        if not self.dry_run:
            self.out(f"  -> {type_} id {new_id}")
        return new_id

    # -- steps -------------------------------------------------------------

    def _campaign(self, c: dict) -> str:
        payload = {
            "name": c["name"],
            "objective": c["objective"],
            "status": "PAUSED",
            "special_ad_categories": [],
            "is_adset_budget_sharing_enabled": False,
        }
        return self._ensure("campaign", self.act, c["name"],
                            lambda: self.client.post(f"{self.act}/campaigns", payload, {"id": self._fake_id("campaign")})["id"])

    def _load_audiences(self) -> dict:
        if self._audiences is None:
            rows = self.client.get_all(f"{self.act}/customaudiences", {"fields": "name,id", "limit": 500}, {"data": []})
            self._audiences = {r["name"]: r["id"] for r in rows if r.get("name")}
            if self.dry_run:
                self.out("  (dry run: assuming none of the plan's audiences exist yet)")
        return self._audiences

    def _audience(self, spec: dict) -> str:
        existing = self._load_audiences()
        name = spec["name"]
        if name in existing:
            if (("audience", self.act, name)) not in self._this_run:
                self.out(f"  found audience \"{name}\": {existing[name]}")
                self.log.record("audience", name, self.act, existing[name], action="found")
                self._this_run.add(("audience", self.act, name))
            return existing[name]
        days = int(spec["retention_days"])
        if spec["type"] == "website_visitors":
            filt = {"field": "url", "operator": "i_contains", "value": ""}
        else:
            filt = {"field": "event", "operator": "eq", "value": spec["event"]}
        rule = {"inclusions": {"operator": "or", "rules": [{
            "event_sources": [{"id": self.cfg.pixel_id, "type": "pixel"}],
            "retention_seconds": days * 86400,
            "filter": {"operator": "and", "filters": [filt]},
        }]}}
        # Learned on the first live run: v22.0 rejects "subtype": "WEBSITE" (code 2654, subcode 1870053,
        # "The parameter 'subtype' is not supported"). A website audience is defined by the pixel rule
        # alone; retention lives in the rule as retention_seconds.
        payload = {"name": name, "rule": rule, "prefill": True}
        new_id = self.client.post(f"{self.act}/customaudiences", payload, {"id": self._fake_id("audience")})["id"]
        self.log.record("audience", name, self.act, new_id)
        self._this_run.add(("audience", self.act, name))
        existing[name] = new_id
        if not self.dry_run:
            self.out(f"  -> audience id {new_id}")
        return new_id

    def _interest(self, name: str) -> dict:
        if name not in self._interests:
            fake = {"data": [{"id": f"DRYRUN_INTEREST_ID({name})", "name": name}]}
            data = self.client.get("search", {"type": "adinterest", "q": name, "limit": 1}, fake).get("data") or []
            if not data:
                raise MetaAPIError(f"interest \"{name}\" not found by /search?type=adinterest")
            self._interests[name] = {"id": str(data[0]["id"]), "name": data[0].get("name", name)}
            if not self.dry_run:
                self.out(f"  -> interest \"{name}\" = {self._interests[name]['name']} ({self._interests[name]['id']})")
        return self._interests[name]

    def _targeting(self, t: dict) -> dict:
        targeting = {
            "geo_locations": {"countries": list(self.meta["countries"])},
            "age_min": int(t["age"][0]),
            "targeting_automation": {"advantage_audience": 1 if t.get("advantage_audience") else 0},
        }
        # Learned on the first live run: with Advantage+ audience on, Meta rejects a hard age_max
        # (code 100, subcode 1870189: "You can add a lower maximum age as a suggestion instead").
        # Age is a suggestion in that mode, so only age_min is sent; age_max stays in the plan
        # for the ad sets that target manually.
        if not t.get("advantage_audience"):
            targeting["age_max"] = int(t["age"][1])
        if t.get("interests"):
            targeting["flexible_spec"] = [{"interests": [self._interest(n) for n in t["interests"]]}]
        if t.get("include_audiences"):
            targeting["custom_audiences"] = [{"id": self._audience(a)} for a in t["include_audiences"]]
        if t.get("exclude_audiences"):
            targeting["excluded_custom_audiences"] = [{"id": self._audience(a)} for a in t["exclude_audiences"]]
        return targeting

    def _adset(self, a: dict, campaign_id: str, cents: int) -> str:
        targeting = self._targeting(a.get("targeting") or {})
        payload = {
            "name": a["name"],
            "campaign_id": campaign_id,
            "daily_budget": cents,
            "billing_event": "IMPRESSIONS",
            "optimization_goal": a["optimization_goal"],
            "bid_strategy": a["bid_strategy"],
            "promoted_object": {"pixel_id": self.cfg.pixel_id, "custom_event_type": self.meta["pixel_event"]},
            "status": "PAUSED",
            "targeting": targeting,
        }
        return self._ensure("adset", campaign_id, a["name"],
                            lambda: self.client.post(f"{self.act}/adsets", payload, {"id": self._fake_id("adset")})["id"])

    def _image_hash(self, rel_path: str) -> str:
        if rel_path in self._image_hashes:
            return self._image_hashes[rel_path]
        full = os.path.join(self.base_dir, rel_path)
        fname = os.path.basename(rel_path)

        def upload():
            fake = {"images": {fname: {"hash": f"DRYRUN_HASH({fname})"}}}
            data = self.client.post(f"{self.act}/adimages", {}, fake, file_path=full, file_label=rel_path)
            images = data.get("images") or {}
            if not images:
                raise MetaAPIError(f"POST /{self.act}/adimages: no image hash returned for {rel_path}")
            info = images.get(fname) or next(iter(images.values()))
            return info["hash"]

        h = self._ensure("image", self.act, rel_path, upload)
        self._image_hashes[rel_path] = h
        return h

    def _creative(self, ad_name: str, ad: dict) -> str:
        image_hash = self._image_hash(ad["file"])
        story = {
            "page_id": self.cfg.page_id,
            "link_data": {
                "image_hash": image_hash,
                "link": self.landing,
                "message": ad["primary_text"],
                "name": ad["headline"],
                "call_to_action": {"type": ad["cta"], "value": {"link": self.landing}},
            },
        }
        payload = {"name": ad_name, "object_story_spec": story}
        return self._ensure("creative", self.act, ad_name,
                            lambda: self.client.post(f"{self.act}/adcreatives", payload, {"id": self._fake_id("creative")})["id"])

    def _ad(self, ad_name: str, adset_id: str, creative_id: str) -> str:
        payload = {"name": ad_name, "adset_id": adset_id, "creative": {"creative_id": creative_id}, "status": "PAUSED"}
        return self._ensure("ad", adset_id, ad_name,
                            lambda: self.client.post(f"{self.act}/ads", payload, {"id": self._fake_id("ad")})["id"])

    # -- run ---------------------------------------------------------------

    def run(self) -> None:
        for c in self.meta["campaigns"]:
            if is_cut(c):
                self.out(f"\n# CUT, skipped: {c['name']}")
                continue
            sets = c["ad_sets"]
            cents = split_cents(c["daily_budget"], len(sets))
            self.out(f"\n# Campaign: {c['name']} ({money(c['daily_budget'])}/day, budget on the ad sets: "
                     f"{', '.join(money(x / 100) for x in cents)})")
            campaign_id = self._campaign(c)
            for a, budget in zip(sets, cents):
                self.out(f"# Ad set: {a['name']}")
                adset_id = self._adset(a, campaign_id, budget)
                for ad_name in a["ads"]:
                    ad = self.meta["ads"][ad_name]
                    creative_id = self._creative(ad_name, ad)
                    self._ad(ad_name, adset_id, creative_id)
