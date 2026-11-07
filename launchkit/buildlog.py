"""Build log: one JSON file per run in build-log/, plus name lookups for idempotent re-runs."""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone

LOG_DIR = "build-log"


def load_index(log_dir: str, platform: str) -> dict:
    """(type, parent, name) -> id for every object created or reused by earlier live runs."""
    index = {}
    for path in sorted(glob.glob(os.path.join(log_dir, f"*-{platform}.json"))):
        try:
            data = load(path)
        except (OSError, ValueError):
            continue
        if data.get("platform") != platform or data.get("dry_run"):
            continue
        for obj in data.get("objects") or []:
            if obj.get("id"):
                index[(obj.get("type"), obj.get("parent"), obj.get("name"))] = obj["id"]
    return index


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class BuildLog:
    def __init__(self, base_dir: str, platform: str, dry_run: bool, account: str = ""):
        self.dir = os.path.join(base_dir, LOG_DIR)
        self.platform = platform
        self.dry_run = dry_run
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = os.path.join(self.dir, f"{stamp}-{platform}.json")
        self.index = load_index(self.dir, platform)
        self.data = {
            "platform": platform,
            "account": account,
            "dry_run": dry_run,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "finished_at": None,
            "status": "running",
            "error": None,
            "notes": [],
            "objects": [],
        }

    @property
    def objects(self) -> list:
        return self.data["objects"]

    def find(self, type_: str, parent: str, name: str):
        return self.index.get((type_, parent, name))

    def record(self, type_: str, name: str, parent: str, id_: str, action: str = "created") -> None:
        self.objects.append({"type": type_, "name": name, "parent": parent, "id": id_, "action": action})
        self.index[(type_, parent, name)] = id_
        self._write()

    def note(self, message: str) -> None:
        self.data["notes"].append(message)
        self._write()

    def finish(self, status: str, error: str | None = None) -> None:
        self.data["status"] = status
        self.data["error"] = error
        self.data["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._write()

    def _write(self) -> None:
        if self.dry_run:
            return  # dry runs never touch build-log/
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)


def table(objects: list) -> str:
    """Plain text table of build-log objects."""
    headers = ["TYPE", "NAME", "ID", "ACTION", "PARENT"]
    rows = [[str(o.get("type", "")), str(o.get("name", "")), str(o.get("id", "")),
             str(o.get("action", "")), str(o.get("parent", ""))] for o in objects]
    if not rows:
        return "(nothing created)"
    widths = [min(max(len(r[i]) for r in rows + [headers]), 60) for i in range(len(headers))]

    def fmt(r):
        cells = [(c if len(c) <= w else c[: w - 3] + "...").ljust(w) for c, w in zip(r, widths)]
        return "  ".join(cells).rstrip()

    return "\n".join([fmt(headers), fmt(["-" * w for w in widths])] + [fmt(r) for r in rows])


def report(path: str) -> str:
    data = load(path)
    lines = [
        f"Build log: {path}",
        f"Platform:  {data.get('platform')}   Account: {data.get('account')}",
        f"Started:   {data.get('started_at')}   Finished: {data.get('finished_at')}",
        f"Status:    {data.get('status')}" + (f"   Error: {data['error']}" if data.get("error") else ""),
        "",
        table(data.get("objects") or []),
    ]
    created = sum(1 for o in data.get("objects") or [] if o.get("action") == "created")
    reused = sum(1 for o in data.get("objects") or [] if o.get("action") != "created")
    lines += ["", f"{created} created, {reused} reused from earlier runs. Everything is PAUSED."]
    if data.get("notes"):
        lines += ["", "Notes:"] + [f"  - {n}" for n in data["notes"]]
    return "\n".join(lines)
