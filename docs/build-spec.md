# launchkit build spec

You are building `launchkit`, a small Python CLI that turns `plan.yaml` (already written, read it first) into ready-to-launch PAUSED campaigns on Meta and Google Ads. Work only inside this directory. Do not run anything with `--live`. Do not touch files outside this folder.

## What it must do

```
python launchkit.py validate              # schema + budget checks, prints a tree, exit 1 on any problem
python launchkit.py build --platform meta    # dry run by default: prints every API call it WOULD make
python launchkit.py build --platform google
python launchkit.py build --platform meta --live     # creates everything, status PAUSED, writes build-log/<timestamp>-meta.json
python launchkit.py build --platform google --live
python launchkit.py report build-log/<file>.json     # prints what was created, with IDs, as a table
```

Everything created is PAUSED. There is no flag to activate. Say so in the README.

## Layout

```
launchkit.py            thin CLI (argparse), dispatches to the package
launchkit/
  __init__.py
  plan.py               load + validate plan.yaml, budget math, tree printer
  meta.py               Meta Marketing API builder
  google.py             Google Ads API builder
  buildlog.py           append results to build-log/<ts>-<platform>.json, idempotency lookups
  creative.py           make_placeholders(): generates the PNGs listed in plan.yaml with Pillow
                        (solid colour, ad name as text, correct dimensions) if they do not exist
creative/               generated placeholders (gitignored? NO, commit them, they are tiny)
build-log/              gitignored
tests/test_plan.py      pytest: validate() passes on plan.yaml, fails when a budget is over, fails on unknown ad name
.env.example
requirements.txt        pyyaml, requests, python-dotenv, pillow, google-ads==32.0.0, pytest
README.md
.gitignore              .env, build-log/, __pycache__/, .venv/
```

## Validation rules (plan.py)

- Sum of `daily_budget` x 30 per platform must be <= that platform's `monthly_budget`, and both together <= `account.total_monthly_budget`. Print the numbers either way.
- Every ad name under an ad set must exist in `meta.ads`. Every ad file must exist (after `make_placeholders()`).
- Meta primary_text <= 125 characters, headline <= 40.
- Google: headlines <= 30 characters, descriptions <= 90, at least 3 headlines and 2 descriptions per ad. PMax long_headline <= 90, headlines <= 30.
- A campaign with `status: CUT` or `daily_budget: 0` is printed in the tree as CUT and skipped by the builders.
- Tree printer shows: platform > campaign ($/day, $/month) > ad set / ad group > ads or keywords count. Founder-readable.

## Config (.env, loaded with python-dotenv)

```
META_ACCESS_TOKEN=
META_API_VERSION=v22.0
META_AD_ACCOUNT_ID=act_...
META_PAGE_ID=
META_PIXEL_ID=
GOOGLE_APPLICATION_CREDENTIALS=~/.config/.../adc.json     # OAuth "authorized_user" ADC json with the adwords scope
GOOGLE_CLOUD_PROJECT=
GOOGLE_ADS_CUSTOMER_ID=1234567890
```

## Meta builder (meta.py), Graph API via `requests`, no SDK

Base `https://graph.facebook.com/{META_API_VERSION}`. Bearer token header. Raise with the API's error message on any `error` key.

Order per campaign, each step logged with the returned id:

1. Campaign: `POST /{act}/campaigns` with `name`, `objective` (from plan), `status=PAUSED`, `special_ad_categories=[]`, `is_adset_budget_sharing_enabled=false`. Budget lives on the ad sets: split the campaign `daily_budget` evenly across its ad sets.
2. Audiences (only for ad sets that list `include_audiences` / `exclude_audiences`): look up existing by name via `GET /{act}/customaudiences?fields=name,id`; create if missing with `POST /{act}/customaudiences`:
   - `website_visitors`: `subtype=WEBSITE`, `retention_days`, `rule` = `{"inclusions":{"operator":"or","rules":[{"event_sources":[{"id":PIXEL,"type":"pixel"}],"retention_seconds":<days*86400>,"filter":{"operator":"and","filters":[{"field":"url","operator":"i_contains","value":""}]}}]}}` as a JSON string, `prefill=true`.
   - `website_event`: same shape but the filter is `{"field":"event","operator":"eq","value":<event>}`.
3. Ad set: `POST /{act}/adsets` with `name`, `campaign_id`, `daily_budget` in cents, `billing_event=IMPRESSIONS`, `optimization_goal`, `bid_strategy`, `promoted_object={"pixel_id":PIXEL,"custom_event_type":<meta.pixel_event>}` (JSON string), `status=PAUSED`, `targeting` as a JSON string:
   - `geo_locations: {countries: [...]}` from `meta.countries`
   - `age_min`, `age_max`
   - `targeting_automation: {advantage_audience: 1 or 0}`
   - if `interests` present: resolve each name with `GET /search?type=adinterest&q=<name>&limit=1` and set `flexible_spec: [{"interests": [{id, name}, ...]}]`
   - `custom_audiences: [{id}]` / `excluded_custom_audiences: [{id}]` from step 2
4. Image upload once per file: `POST /{act}/adimages` multipart `filename` -> `images.<name>.hash`. Cache by file path within a run.
5. Creative: `POST /{act}/adcreatives` with `name`, `object_story_spec` JSON: `{"page_id":PAGE,"link_data":{"image_hash":...,"link":account.landing_url,"message":primary_text,"name":headline,"call_to_action":{"type":cta,"value":{"link":landing_url}}}}`.
6. Ad: `POST /{act}/ads` with `name`, `adset_id`, `creative={"creative_id":...}` JSON, `status=PAUSED`.

Dry run prints each call as `POST /path {payload}` with the token never printed. Live run does the calls, and skips any object whose exact name already exists under the same parent in `build-log/` (idempotent re-runs). On failure, stop, print what was created so far with IDs, exit 1.

## Google builder (google.py), `google-ads` library v32 (API v25), ADC auth

Client: `GoogleAdsClient(credentials=google.auth.default(scopes=["https://www.googleapis.com/auth/adwords"])[0], use_proto_plus=True)`. No developer token is needed on this project. Customer id from env, digits only.

Order per campaign (skip `status: CUT`):

1. Budget: `CampaignBudgetService.mutate_campaign_budgets`, `amount_micros = daily_budget * 1_000_000`, `explicitly_shared = False`, `delivery_method = STANDARD`.
2. Campaign: `CampaignService.mutate_campaigns`, `status = PAUSED`, `campaign_budget`, `contains_eu_political_advertising = DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING` (required on this API version, the build fails without it).
   - SEARCH: `advertising_channel_type = SEARCH`, `network_settings.target_google_search = True`, `target_search_network = False`, `target_content_network = False`, `target_partner_search_network = False`.
   - PERFORMANCE_MAX: `advertising_channel_type = PERFORMANCE_MAX`, `url_expansion_opt_out = False`.
   - Bidding: `MAXIMIZE_CONVERSIONS` -> set `campaign.maximize_conversions.target_cpa_micros` when `target_cpa` is not null (else leave the message empty but present). `TARGET_IMPRESSION_SHARE` -> `campaign.target_impression_share.location = ANYWHERE_ON_PAGE`, `location_fraction_micros = impression_share * 1_000_000`, `cpc_bid_ceiling_micros = max_cpc * 1_000_000`.
3. Geo + language: `CampaignCriterionService.mutate_campaign_criteria`, one criterion `location.geo_target_constant = geo_target_constants/2840`, one `language.language_constant = languageConstants/1000`.
4. Campaign negatives: one campaign criterion per word, `keyword.text`, `keyword.match_type = BROAD`, `negative = True`.
5. SEARCH ad groups: `AdGroupService` (`type = SEARCH_STANDARD`, `status = ENABLED`, `cpc_bid_micros = 2_000_000`), then `AdGroupCriterionService` keywords with the plan's match type, then `AdGroupAdService` one responsive search ad per `ads` entry: `ad.responsive_search_ad.headlines[]` / `descriptions[]` as `AdTextAsset`, `ad.final_urls = [account.landing_url]`, `status = PAUSED`.
6. PMax: for each asset group, upload the three images with `AssetService` (`image_asset.data` = file bytes, `type = IMAGE`, name = file stem), create text assets for headlines, long_headline, descriptions, business_name, then `AssetGroupService.mutate_asset_groups` (`final_urls`, `status = PAUSED`) and link every asset with `AssetGroupAssetService` using field types `HEADLINE`, `LONG_HEADLINE`, `DESCRIPTION`, `BUSINESS_NAME`, `MARKETING_IMAGE`, `SQUARE_MARKETING_IMAGE`, `LOGO`. Do the asset group + its asset links in ONE `GoogleAdsService.mutate` batch with temporary negative resource ids (`customers/{cid}/assetGroups/-1`), because the API requires the minimum asset set to exist in the same request. Brand exclusions: skip with a printed note `brand exclusion list: set in the UI (requires a shared set of type BRAND_HOOK, not built here)`.

Dry run prints each mutate as `<Service>.<method> {fields}`. Live run logs resource names to the build log. On failure, stop, print what was created so far, exit 1.

## README.md

Short. Sections: what it does, 60-second setup (venv, pip install -r, copy .env.example), the five commands, "how to reuse it for the next launch" (edit plan.yaml, run validate, run build), "what it does not do" (never activates, no brand exclusion list on PMax, placeholder creatives). Plain language, a founder should be able to follow it.

## Working rules

- Python 3.11+. Run `python -m pytest -q` and `python launchkit.py validate` and both dry runs before you finish; paste their output in your final message.
- Do not invent plan fields; if the plan is missing something the API needs, pick a sensible default, print it in the dry run, and list it in your final message under "assumptions".
- No emoji, no em dashes in any file.
- Final message: files created, the three command outputs, assumptions, and anything you were unsure about.
