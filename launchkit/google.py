"""Google Ads API builder (google-ads library v32, API v25), ADC auth.

Everything is created PAUSED (campaigns, ads, asset groups). Dry run builds the exact
same request protos and prints them, but sends nothing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from google.protobuf import json_format

from .plan import GOOGLE_GEO_TARGETS, GOOGLE_LANGUAGES, is_cut, money

ADWORDS_SCOPE = "https://www.googleapis.com/auth/adwords"
PLACEHOLDER_CUSTOMER_ID = "0000000000"
AD_GROUP_CPC_MICROS = 2_000_000
BRAND_EXCLUSION_NOTE = "brand exclusion list: set in the UI (requires a shared set of type BRAND_HOOK, not built here)"


class GoogleBuildError(RuntimeError):
    pass


class ConfigError(RuntimeError):
    pass


@dataclass
class GoogleConfig:
    customer_id: str
    credentials_path: str
    project: str


def load_config(dry_run: bool) -> tuple:
    notes = []
    raw = (os.environ.get("GOOGLE_ADS_CUSTOMER_ID") or "").strip()
    cid = "".join(ch for ch in raw if ch.isdigit())
    creds = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    project = (os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    if not cid:
        if not dry_run:
            raise ConfigError("missing in .env: GOOGLE_ADS_CUSTOMER_ID")
        cid = PLACEHOLDER_CUSTOMER_ID
        notes.append(f"GOOGLE_ADS_CUSTOMER_ID not set, using placeholder {cid} (dry run only)")
    if dry_run:
        notes.append("dry run uses no Google credentials (ADC is only loaded with --live)")
        if not creds:
            notes.append("GOOGLE_APPLICATION_CREDENTIALS not set (needed for --live, unless gcloud ADC is already configured)")
    return GoogleConfig(cid, creds, project), notes


def make_client(cfg: GoogleConfig, dry_run: bool):
    from google.ads.googleads.client import GoogleAdsClient

    if dry_run:
        # Builds protos offline; never used to send anything.
        from google.auth.credentials import AnonymousCredentials
        return GoogleAdsClient(credentials=AnonymousCredentials(), use_proto_plus=True)

    import google.auth

    if cfg.credentials_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.expanduser(cfg.credentials_path)
    credentials = google.auth.default(scopes=[ADWORDS_SCOPE])[0]
    if cfg.project and hasattr(credentials, "with_quota_project"):
        credentials = credentials.with_quota_project(cfg.project)
    return GoogleAdsClient(credentials=credentials, use_proto_plus=True)


def _to_dict(msg) -> dict:
    return json_format.MessageToDict(type(msg).pb(msg), preserving_proto_field_name=True)


def _google_error(ex, where: str) -> str:
    parts = []
    for err in ex.failure.errors:
        path = ".".join(e.field_name + (f"[{e.index}]" if "index" in e else "") for e in err.location.field_path_elements)
        parts.append(err.message + (f" (field {path})" if path else ""))
    return f"{where} failed: {'; '.join(parts) or str(ex)} (request_id {ex.request_id})"


class GoogleBuilder:
    def __init__(self, plan: dict, cfg: GoogleConfig, client, log, base_dir: str, dry_run: bool, out=print):
        self.plan = plan
        self.g = plan["google"]
        self.cfg = cfg
        self.client = client
        self.enums = client.enums
        self.log = log
        self.base_dir = base_dir
        self.dry_run = dry_run
        self.out = out
        self.cid = cfg.customer_id
        self.customer = f"customers/{self.cid}"
        self.landing = plan["account"]["landing_url"]
        self.calls = 0
        self._fake_n = 0
        self._this_run = set()
        self._assets = {}

    # -- helpers -----------------------------------------------------------

    def _fake_rn(self, collection: str) -> str:
        self._fake_n += 1
        return f"{self.customer}/{collection}/DRYRUN-{self._fake_n}"

    def _existing(self, type_: str, parent: str, name: str):
        key = (type_, parent, name)
        found = self.log.find(type_, parent, name)
        if not found:
            return None
        if key in self._this_run:
            self.out(f"  (reuse {type_} \"{name}\" created above: {found})")
        else:
            self.out(f"SKIP {type_} \"{name}\": already in build-log, {found}")
            self.log.record(type_, name, parent, found, action="reused")
            self._this_run.add(key)
        return found

    def _mutate(self, service: str, method: str, op, type_: str, parent: str, name: str,
                collection: str, shown: dict | None = None) -> str:
        """One operation per call so every printed line is exactly one API request."""
        found = self._existing(type_, parent, name)
        if found:
            return found
        self.calls += 1
        self.out(f"{service}.{method} {json.dumps(shown if shown is not None else _to_dict(op))}")
        if self.dry_run:
            rn = self._fake_rn(collection)
        else:
            from google.ads.googleads.errors import GoogleAdsException
            try:
                resp = getattr(self.client.get_service(service), method)(customer_id=self.cid, operations=[op])
            except GoogleAdsException as ex:
                raise GoogleBuildError(_google_error(ex, f"{service}.{method} ({type_} \"{name}\")"))
            rn = resp.results[0].resource_name
            self.out(f"  -> {rn}")
        self.log.record(type_, name, parent, rn)
        self._this_run.add((type_, parent, name))
        return rn

    # -- steps -------------------------------------------------------------

    def _budget(self, c: dict) -> str:
        op = self.client.get_type("CampaignBudgetOperation")
        b = op.create
        b.name = f"{c['name']} budget"
        b.amount_micros = int(round(c["daily_budget"] * 1_000_000))
        b.explicitly_shared = False
        b.delivery_method = self.enums.BudgetDeliveryMethodEnum.STANDARD
        return self._mutate("CampaignBudgetService", "mutate_campaign_budgets", op,
                            "budget", self.customer, b.name, "campaignBudgets")

    def _campaign(self, c: dict, budget_rn: str) -> str:
        op = self.client.get_type("CampaignOperation")
        camp = op.create
        camp.name = c["name"]
        camp.status = self.enums.CampaignStatusEnum.PAUSED
        camp.campaign_budget = budget_rn
        camp.contains_eu_political_advertising = (
            self.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING)
        if c["type"] == "SEARCH":
            camp.advertising_channel_type = self.enums.AdvertisingChannelTypeEnum.SEARCH
            camp.network_settings.target_google_search = True
            camp.network_settings.target_search_network = False
            camp.network_settings.target_content_network = False
            camp.network_settings.target_partner_search_network = False
        else:
            camp.advertising_channel_type = self.enums.AdvertisingChannelTypeEnum.PERFORMANCE_MAX
            # v25 has no url_expansion_opt_out field. Its replacement is this automation
            # setting; OPTED_IN is the same as url_expansion_opt_out = False.
            setting = type(camp).AssetAutomationSetting(
                asset_automation_type=self.enums.AssetAutomationTypeEnum.FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION,
                asset_automation_status=self.enums.AssetAutomationStatusEnum.OPTED_IN,
            )
            camp.asset_automation_settings.append(setting)
            # Business name and logo are linked on the asset group (below), which needs this off.
            camp.brand_guidelines_enabled = False
        bidding = c["bidding"]
        if bidding["strategy"] == "MAXIMIZE_CONVERSIONS":
            if bidding.get("target_cpa") is not None:
                camp.maximize_conversions.target_cpa_micros = int(round(bidding["target_cpa"] * 1_000_000))
            else:
                camp.maximize_conversions = self.client.get_type("MaximizeConversions")
        else:  # TARGET_IMPRESSION_SHARE
            tis = camp.target_impression_share
            tis.location = getattr(self.enums.TargetImpressionShareLocationEnum, bidding["location"])
            tis.location_fraction_micros = int(round(bidding["impression_share"] * 1_000_000))
            tis.cpc_bid_ceiling_micros = int(round(bidding["max_cpc"] * 1_000_000))
        return self._mutate("CampaignService", "mutate_campaigns", op,
                            "campaign", self.customer, c["name"], "campaigns")

    def _campaign_criteria(self, c: dict, camp_rn: str) -> None:
        geo_id = GOOGLE_GEO_TARGETS[self.g["country"]]
        lang_id = GOOGLE_LANGUAGES[self.g["language"]]

        op = self.client.get_type("CampaignCriterionOperation")
        op.create.campaign = camp_rn
        op.create.location.geo_target_constant = f"geoTargetConstants/{geo_id}"
        self._mutate("CampaignCriterionService", "mutate_campaign_criteria", op,
                     "campaign_criterion", camp_rn, f"location {geo_id}", "campaignCriteria")

        op = self.client.get_type("CampaignCriterionOperation")
        op.create.campaign = camp_rn
        op.create.language.language_constant = f"languageConstants/{lang_id}"
        self._mutate("CampaignCriterionService", "mutate_campaign_criteria", op,
                     "campaign_criterion", camp_rn, f"language {lang_id}", "campaignCriteria")

        for word in c.get("negatives") or []:
            op = self.client.get_type("CampaignCriterionOperation")
            op.create.campaign = camp_rn
            op.create.negative = True
            op.create.keyword.text = word
            op.create.keyword.match_type = self.enums.KeywordMatchTypeEnum.BROAD
            self._mutate("CampaignCriterionService", "mutate_campaign_criteria", op,
                         "campaign_criterion", camp_rn, f"negative BROAD {word}", "campaignCriteria")

    def _ad_group(self, ag: dict, camp_rn: str) -> None:
        op = self.client.get_type("AdGroupOperation")
        grp = op.create
        grp.name = ag["name"]
        grp.campaign = camp_rn
        grp.type_ = self.enums.AdGroupTypeEnum.SEARCH_STANDARD
        grp.status = self.enums.AdGroupStatusEnum.ENABLED
        grp.cpc_bid_micros = AD_GROUP_CPC_MICROS
        ag_rn = self._mutate("AdGroupService", "mutate_ad_groups", op, "ad_group", camp_rn, ag["name"], "adGroups")

        for kw in ag["keywords"]:
            op = self.client.get_type("AdGroupCriterionOperation")
            crit = op.create
            crit.ad_group = ag_rn
            crit.status = self.enums.AdGroupCriterionStatusEnum.ENABLED
            crit.keyword.text = kw["text"]
            crit.keyword.match_type = getattr(self.enums.KeywordMatchTypeEnum, kw["match"])
            self._mutate("AdGroupCriterionService", "mutate_ad_group_criteria", op,
                         "keyword", ag_rn, f"{kw['match']} {kw['text']}", "adGroupCriteria")

        for i, ad in enumerate(ag["ads"], 1):
            op = self.client.get_type("AdGroupAdOperation")
            aga = op.create
            aga.ad_group = ag_rn
            aga.status = self.enums.AdGroupAdStatusEnum.PAUSED
            aga.ad.final_urls.append(self.landing)
            for h in ad["headlines"]:
                asset = self.client.get_type("AdTextAsset")
                asset.text = h
                aga.ad.responsive_search_ad.headlines.append(asset)
            for d in ad["descriptions"]:
                asset = self.client.get_type("AdTextAsset")
                asset.text = d
                aga.ad.responsive_search_ad.descriptions.append(asset)
            self._mutate("AdGroupAdService", "mutate_ad_group_ads", op,
                         "responsive_search_ad", ag_rn, f"RSA {i}", "adGroupAds")

    def _image_asset(self, rel_path: str) -> str:
        key = ("image", rel_path)
        if key in self._assets:
            return self._assets[key]
        full = os.path.join(self.base_dir, rel_path)
        with open(full, "rb") as fh:
            data = fh.read()
        stem = os.path.splitext(os.path.basename(rel_path))[0]
        op = self.client.get_type("AssetOperation")
        op.create.name = stem
        op.create.type_ = self.enums.AssetTypeEnum.IMAGE
        op.create.image_asset.data = data
        shown = _to_dict(op)
        shown["create"]["image_asset"]["data"] = f"<{len(data)} bytes from {rel_path}>"
        rn = self._mutate("AssetService", "mutate_assets", op, "asset", self.customer,
                          f"image {stem}", "assets", shown=shown)
        self._assets[key] = rn
        return rn

    def _text_asset(self, text: str) -> str:
        key = ("text", text)
        if key in self._assets:
            return self._assets[key]
        op = self.client.get_type("AssetOperation")
        op.create.text_asset.text = text
        rn = self._mutate("AssetService", "mutate_assets", op, "asset", self.customer, f"text {text}", "assets")
        self._assets[key] = rn
        return rn

    def _asset_group(self, ag: dict, camp_rn: str) -> None:
        if self._existing("asset_group", camp_rn, ag["name"]):
            return
        links = []  # (field type, asset resource name)
        images = ag["images"]
        links.append(("MARKETING_IMAGE", self._image_asset(images["landscape"])))
        links.append(("SQUARE_MARKETING_IMAGE", self._image_asset(images["square"])))
        links.append(("LOGO", self._image_asset(ag["logo"])))
        for h in ag["headlines"]:
            links.append(("HEADLINE", self._text_asset(h)))
        links.append(("LONG_HEADLINE", self._text_asset(ag["long_headline"])))
        for d in ag["descriptions"]:
            links.append(("DESCRIPTION", self._text_asset(d)))
        links.append(("BUSINESS_NAME", self._text_asset(ag["business_name"])))

        # Asset group + every link in ONE request, joined by a temporary id.
        temp_rn = f"{self.customer}/assetGroups/-1"
        ops = []
        mo = self.client.get_type("MutateOperation")
        grp = mo.asset_group_operation.create
        grp.resource_name = temp_rn
        grp.name = ag["name"]
        grp.campaign = camp_rn
        grp.final_urls.append(ag["final_url"])
        grp.status = self.enums.AssetGroupStatusEnum.PAUSED
        ops.append(mo)
        for field_type, asset_rn in links:
            mo = self.client.get_type("MutateOperation")
            link = mo.asset_group_asset_operation.create
            link.asset_group = temp_rn
            link.asset = asset_rn
            link.field_type = getattr(self.enums.AssetFieldTypeEnum, field_type)
            ops.append(mo)

        self.calls += 1
        self.out(f"GoogleAdsService.mutate [one request, {len(ops)} operations: asset group \"{ag['name']}\" + {len(links)} asset links]")
        for mo in ops:
            self.out(f"    {json.dumps(_to_dict(mo))}")
        if self.dry_run:
            group_rn = self._fake_rn("assetGroups")
            link_rns = [f"{group_rn}~{a.rsplit('/', 1)[-1]}~{f}" for f, a in links]
        else:
            from google.ads.googleads.errors import GoogleAdsException
            try:
                resp = self.client.get_service("GoogleAdsService").mutate(customer_id=self.cid, mutate_operations=ops)
            except GoogleAdsException as ex:
                raise GoogleBuildError(_google_error(ex, f"GoogleAdsService.mutate (asset group \"{ag['name']}\")"))
            results = resp.mutate_operation_responses
            group_rn = results[0].asset_group_result.resource_name
            link_rns = [r.asset_group_asset_result.resource_name for r in results[1:]]
            self.out(f"  -> {group_rn} (+{len(link_rns)} links)")
        self.log.record("asset_group", ag["name"], camp_rn, group_rn)
        self._this_run.add(("asset_group", camp_rn, ag["name"]))
        for (field_type, asset_rn), rn in zip(links, link_rns):
            self.log.record("asset_group_asset", f"{field_type} {asset_rn}", group_rn, rn)

    # -- run ---------------------------------------------------------------

    def run(self) -> None:
        for c in self.g["campaigns"]:
            if is_cut(c):
                self.out(f"\n# CUT, skipped: {c['name']}")
                continue
            self.out(f"\n# Campaign: {c['name']} [{c['type']}] ({money(c['daily_budget'])}/day)")
            budget_rn = self._budget(c)
            camp_rn = self._campaign(c, budget_rn)
            self._campaign_criteria(c, camp_rn)
            if c["type"] == "SEARCH":
                for ag in c["ad_groups"]:
                    self.out(f"# Ad group: {ag['name']}")
                    self._ad_group(ag, camp_rn)
            else:
                for ag in c["asset_groups"]:
                    self.out(f"# Asset group: {ag['name']}")
                    self._asset_group(ag, camp_rn)
                if c.get("brand_exclusions"):
                    self.out(f"NOTE {BRAND_EXCLUSION_NOTE}")
                    self.log.note(f"{c['name']}: {BRAND_EXCLUSION_NOTE}")
