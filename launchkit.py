"""launchkit CLI: plan.yaml in, PAUSED campaigns out.

    python launchkit.py validate
    python launchkit.py build --platform meta            (dry run)
    python launchkit.py build --platform google          (dry run)
    python launchkit.py build --platform meta --live
    python launchkit.py build --platform google --live
    python launchkit.py report build-log/<file>.json
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_env() -> None:
    from dotenv import load_dotenv

    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        load_dotenv(path)


def cmd_validate(args) -> int:
    from launchkit.plan import load_plan, validate

    base = os.path.dirname(os.path.abspath(args.plan))
    result = validate(load_plan(args.plan), base)
    for f in result.created_files:
        print(f"created placeholder image: {f}")
    if result.created_files:
        print()
    print(result.tree)
    print()
    if result.problems:
        print(f"PROBLEMS ({len(result.problems)}), fix these before building:")
        for p in result.problems:
            print(f"  - {p}")
        return 1
    print("No problems. Ready to build.")
    return 0


def cmd_build(args) -> int:
    from launchkit.buildlog import BuildLog, table
    from launchkit.plan import load_plan, validate

    dry_run = not args.live
    platform = args.platform
    base = os.path.dirname(os.path.abspath(args.plan))
    plan = load_plan(args.plan)
    result = validate(plan, base)
    for f in result.created_files:
        print(f"created placeholder image: {f}")

    mode = "LIVE: creating objects, all PAUSED" if args.live else "DRY RUN: nothing is sent, these are the calls --live would make"
    print(f"launchkit build --platform {platform}  [{mode}]")

    problems = result.problems_for(platform)
    if problems:
        print(f"\nVALIDATION PROBLEMS ({len(problems)}):")
        for p in problems:
            print(f"  - {p}")
        if args.live:
            print("\nRefusing to build live until these are fixed. Run: python launchkit.py validate")
            return 1
        print("Dry run continues so you can review the calls. --live will refuse until these are fixed.")

    try:
        if platform == "meta":
            from launchkit import meta as mod
            cfg, notes = mod.load_config(dry_run)
            account = cfg.ad_account
        else:
            from launchkit import google as mod
            cfg, notes = mod.load_config(dry_run)
            account = cfg.customer_id
    except RuntimeError as e:
        print(f"\nCONFIG ERROR: {e}. Copy .env.example to .env and fill it in.")
        return 1
    for n in notes:
        print(f"note: {n}")

    log = BuildLog(base, platform, dry_run, account=account)
    try:
        if platform == "meta":
            client = mod.MetaClient(cfg, dry_run)
            builder = mod.MetaBuilder(plan, cfg, client, log, base)
            builder.run()
            calls = client.calls
        else:
            client = mod.make_client(cfg, dry_run)
            builder = mod.GoogleBuilder(plan, cfg, client, log, base, dry_run)
            builder.run()
            calls = builder.calls
    except Exception as e:  # stop on the first failure and show what exists
        log.finish("failed", str(e))
        print(f"\nFAILED: {e}")
        print("\nCreated before the failure (all PAUSED):")
        print(table(log.objects))
        if not dry_run:
            print(f"\nBuild log: {log.path}  (re-run the same command to resume; existing objects are skipped)")
        return 1

    log.finish("ok")
    created = sum(1 for o in log.objects if o["action"] == "created")
    if dry_run:
        print(f"\nDRY RUN complete: {calls} API calls printed, {created} objects would be created, nothing was sent.")
        print("Everything would be created PAUSED. Add --live to create it.")
    else:
        print(f"\nDone: {created} objects created, everything PAUSED. Nothing is spending.")
        print(f"Build log: {log.path}")
        print(f"See it as a table: python launchkit.py report {os.path.relpath(log.path, HERE)}")
    return 0


def cmd_report(args) -> int:
    from launchkit.buildlog import report

    if not os.path.exists(args.logfile):
        print(f"no such build log: {args.logfile}")
        return 1
    print(report(args.logfile))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="launchkit", description="Turn plan.yaml into PAUSED campaigns on Meta and Google Ads.")
    parser.add_argument("--plan", default=os.path.join(HERE, "plan.yaml"), help="path to the plan (default: plan.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="check the plan and budgets, print the tree; exit 1 on any problem")
    b = sub.add_parser("build", help="dry run by default; --live creates everything PAUSED")
    b.add_argument("--platform", choices=["meta", "google"], required=True)
    b.add_argument("--live", action="store_true", help="really create the objects (always PAUSED)")
    r = sub.add_parser("report", help="print a build log as a table")
    r.add_argument("logfile")
    args = parser.parse_args(argv)

    _load_env()
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "build":
        return cmd_build(args)
    return cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
