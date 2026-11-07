# launchkit

## What it does

launchkit reads `plan.yaml` (the whole month's paid plan in one file) and builds it on Meta and Google Ads: campaigns, ad sets, ad groups, keywords, audiences, creatives and ads.

Everything it creates is PAUSED. There is no flag to activate anything. Nothing spends money until a person turns it on in Ads Manager or Google Ads.

## 60-second setup

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt     (Mac/Linux: .venv/bin/python)
copy .env.example .env                                            (Mac/Linux: cp)
```

Then fill in `.env`. You only need it for `--live`; `validate` and the dry runs work without it (they use placeholder ids and say so).

- Meta: an access token with ads_management, the ad account id (`act_...`), the Facebook Page id and the pixel id.
- Google: an OAuth "authorized_user" credentials file with the adwords scope (for example from `gcloud auth application-default login --scopes=https://www.googleapis.com/auth/adwords,https://www.googleapis.com/auth/cloud-platform`), the Cloud project, and the Google Ads customer id.

## The five commands

```
python launchkit.py validate                          checks the plan, prints the tree and the budget math, exit 1 on any problem
python launchkit.py build --platform meta             dry run: prints every Meta API call it would make, sends nothing
python launchkit.py build --platform google           dry run: prints every Google Ads request it would make, sends nothing
python launchkit.py build --platform meta --live      creates everything on Meta, PAUSED, and writes build-log/<time>-meta.json
python launchkit.py build --platform google --live    creates everything on Google Ads, PAUSED, and writes build-log/<time>-google.json
python launchkit.py report build-log/<file>.json      shows what a live build created, with ids, as a table
```

`validate` also creates any missing placeholder image in `creative/`.

If a live build fails halfway, it stops, prints what it already created, and exits. Fix the problem and run the same command again: anything already in `build-log/` with the same name under the same parent is skipped, not created twice.

## How to reuse it for the next launch

1. Edit `plan.yaml`: budgets, campaigns, ad copy, keywords. Drop real images into `creative/` with the same file names (or change the names in the plan).
2. `python launchkit.py validate` until it says "No problems".
3. `python launchkit.py build --platform meta` and `--platform google` and read the dry runs.
4. Add `--live` to each. Review in the ad platforms, then turn things on by hand.

To retire a campaign, set `status: CUT` or `daily_budget: 0`. It shows as CUT in the tree and is never built.

## What it does not do

- It never activates anything. Turning campaigns on is always a manual step in the ad platform.
- It does not build the brand exclusion list on Performance Max. Set it in the Google Ads UI (it prints a reminder).
- The images in `creative/` are placeholders (solid colour with the ad name). Swap in real creative before launch.
- It does not set conversion goals. Check in each platform that the trial signup conversion is the one being optimised.
