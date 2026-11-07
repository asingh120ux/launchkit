# AI prompts and what the AI got wrong

How AI was used across this assessment, in order, with the prompts and the places I overrode it.

## 1. Reading the data

The workbook was dumped to text and aggregated with a short script the AI wrote: weekly by campaign for Meta and Google, weekly by source for PostHog, period totals. Prompt, in substance:

> Load the workbook. For Meta_Daily and Google_Daily, aggregate spend, clicks and reported conversions by ISO week and campaign, and print CPA per row. For Trials_PostHog, aggregate signups by week and utm_source. Print period totals per campaign with CPA, CTR and CPC. Do not interpret anything, print the tables.

The reading was a back-and-forth on those tables, not a one-shot. The prompts that mattered, in substance:

> For each row in Change_Log, show the campaign it touched for the two weeks before and after the date: spend, conversions, CPA, and the matching PostHog source column.

> Where do Meta-reported trials and PostHog "facebook" trials diverge, and by how much per week?

> Google Trials fell after 09-15 and the UK campaign has zero conversions. Did the US campaigns get worse, or did they get less money?

The four findings in Task 1 came out of that conversation. The pixel break on 09-22 is the one that shaped everything else: once Meta-reported trials and PostHog disagree by half, every Meta decision after that date has to be re-read against PostHog, and the plan cannot raise Meta budget until the two agree again.

## 2. The plan

`plan.yaml` was drafted by the AI from the four findings and a brief from me: trial CPA target $250 to $280, Meta $65k and Google $55k, one budget per campaign and no shared budgets, retargeting back to 14 days with fresh ads, the founder concept back in as two hooks, UK cut, Performance Max rebuilt with brand excluded, and no budget increase on Meta until the pixel is fixed. The AI turned that into campaigns, ad sets, keywords, copy and the "why" line on each campaign. I read every line before the first dry run and changed what I disagreed with. The budgets and the structure are the brief; the AI filled in the rest and the validator caught two of its headlines over length (section 3) and I caught the placeholder landing page too late (section 4).

## 3. The build

`docs/build-spec.md` is the prompt, verbatim. It was given to a coding agent (Claude, Opus) with one instruction on top: work only inside this folder, never run `--live`, report the test and dry-run output when done.

The agent returned the package, the tests, the README and the placeholder creatives in one pass, and reported five things it changed or was unsure about. Two of those were corrections to my spec:

- My spec said `url_expansion_opt_out = False` for Performance Max. That field does not exist in API v25. The agent checked the installed library and used `asset_automation_settings` with `FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION = OPTED_IN` instead.
- My spec wrote the geo resource as `geo_target_constants/2840`. The real format is `geoTargetConstants/2840`.

It also refused to silently fix two of my Google headlines that were over 30 characters. It failed the test and told me, which is the right behaviour: the copy is mine to change.

## 4. What the AI got wrong, and how I caught it

**The dry run passed and the first live Google run failed on the first ad.** The spec (mine) and the build (the AI's) both used `https://example.com/trial` as the landing page, and the AI's validation accepted it. Google rejects a responsive search ad at creation when the final URL is a placeholder domain, with a `PROHIBITED` policy topic, even on a PAUSED campaign. The build log showed the budget, campaign, criteria, ad group and five keywords created and the ad refused.

Caught by reading the failure table the tool prints, not by the AI. Fix: real landing page in the plan, and a validation rule that refuses `example.com` or `PLACEHOLDER` in `landing_url` so the dry run catches it next time. The re-run skipped everything already built and finished the account.

**The Meta dry run passed and the live run failed twice, both on things the agent had flagged as unverified.** When it handed the build back, it listed what it could not test without a real token: `age_max` together with Advantage+ audience, the `subtype=WEBSITE` parameter on website custom audiences, and the live Meta path in general (it had only run against a fake session). Two of the three failed live.

- Ad set with Advantage+ audience on: Meta rejected `age_max` with "You can add a lower maximum age as a suggestion instead" (code 100, subcode 1870189). In that mode age is a suggestion, so the builder now sends `age_min` only when Advantage+ audience is on and keeps the hard `age_max` for the manual ad sets. The ad set was renamed to 25+ so the name tells the truth.
- Website custom audience: v22.0 rejected `"subtype": "WEBSITE"` (code 2654, "The parameter 'subtype' is not supported in the current API version"). The audience is defined by the pixel rule alone, with retention inside the rule as `retention_seconds`, so `subtype` and `retention_days` were dropped from the payload. Meta reported both audiences back as subtype WEBSITE with 14 and 90 day retention, so nothing was lost.

Neither failure cost anything. The campaign and every object before the failing call were already in the build log, each fix was one function, and the re-run skipped the 26 objects that existed and created the 9 that were missing. The useful part is that the agent's own "I could not verify this" list predicted both failures. Read that list before the first live run, not after.

## 5. The written answers

Tasks 1 and 3 were drafted by the AI from the aggregated tables and the findings, then edited by me for voice and checked number by number against the weekly tables. The first draft had several ranges quoted from the AI's memory of the tables rather than from the tables (for example "$160 to $220" for the founder ad, where the weekly figures run $177 to $223). Those were corrected to the computed values before anything stayed in. The positions in Task 3 (what counts as a winner, 20% every three days, the answer to the CEO) are the ones I would defend in the room; the AI wrote them up and I edited the wording.
