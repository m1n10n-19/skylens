"""
Summarise user feedback to find the strongest signals.

    python scripts/feedback_report.py                 # new since the last review
    python scripts/feedback_report.py --all           # everything
    python scripts/feedback_report.py --mark-reviewed # then remember where we got to
    python scripts/feedback_report.py --server https://<app>.onrender.com

Reads the database directly when DATABASE_URL is set (in .env or the
environment); otherwise asks a running server's GET /feedback, signing
in with SKYLENS_USERNAME / SKYLENS_PASSWORD.

Prints a Markdown summary and writes every entry, with its full
context, to .cache/feedback-export.json.
"""

import argparse
import json
import os
import sys

from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "backend"))

import feedback  # noqa: E402

REVIEWED = os.path.join(ROOT, ".cache", "feedback-reviewed-id")

EXPORT = os.path.join(ROOT, ".cache", "feedback-export.json")


def from_server(url, since_id):

    import requests

    login = requests.post(f"{url}/auth/login", timeout=30, json={
        "username": os.getenv("SKYLENS_USERNAME", ""), "password": os.getenv("SKYLENS_PASSWORD", ""),
    })
    login.raise_for_status()

    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    found = []

    while True:
        page = requests.get(f"{url}/feedback", params={"since_id": since_id, "limit": 2000},
                            headers=headers, timeout=60).json()["entries"]
        if not page:
            return found
        found += page
        since_id = page[-1]["id"]


def from_database(since_id):

    found = []

    while True:
        page = feedback.entries(since_id, 2000)
        if not page:
            return found
        found += page
        since_id = page[-1]["id"]


def place(entry):

    context = entry.get("context") or {}

    return ((context.get("search_area") or {}).get("name")
            or (context.get("location") or {}).get("name") or "unknown place").split(",")[0]


def report(entries):

    lines = [f"# Feedback: {len(entries)} entries"]

    if not entries:
        return "\n".join(lines + ["", "Nothing new."])

    verdicts = Counter(e["verdict"] or "no rating" for e in entries)
    lines += ["", f"ids {entries[0]['id']}-{entries[-1]['id']}, "
              f"{entries[0]['created_at'][:10]} to {entries[-1]['created_at'][:10]}; "
              + ", ".join(f"{k}: {v}" for k, v in verdicts.most_common())]

    def table(title, counter):
        rows = [f"| {k} | {v} |" for k, v in counter.most_common()]
        return ["", f"## {title}", "", "| | count |", "|---|---|"] + rows

    lines += table("Tags", Counter(feedback.TAGS.get(t, t) for e in entries for t in e["tags"] or []))
    lines += table("Analysis type (thumbs down or tagged)",
                   Counter(e["use_case"] or "none" for e in entries if e["verdict"] != "up"))
    lines += table("Place", Counter(place(e) for e in entries))
    lines += table("Version", Counter(e["app_version"] for e in entries))

    # The same site reported more than once is a strong signal.
    sites = defaultdict(list)

    for e in entries:
        if e["scope"] == "site":
            sites[(place(e), e["site_id"])].append(e)

    repeated = {k: v for k, v in sites.items() if len(v) > 1}

    if repeated:
        lines += ["", "## Sites reported more than once"]
        for (where, site_id), group in sorted(repeated.items(), key=lambda kv: -len(kv[1])):
            tags = Counter(t for e in group for t in e["tags"] or [])
            lines.append(f"- {where} / {site_id}: {len(group)} reports; "
                         + ", ".join(f"{feedback.TAGS.get(t, t)} x{n}" for t, n in tags.most_common()))

    lines += ["", "## Entries"]

    for e in entries:

        context = e.get("context") or {}
        site = context.get("site") or {}
        where = (f" site {e['site_id']} ({site.get('site_type') or ''}, "
                 f"{site.get('latitude')}, {site.get('longitude')}, score {site.get('score')})"
                 if e["scope"] == "site" else "")
        tags = ", ".join(feedback.TAGS.get(t, t) for t in e["tags"] or [])

        lines.append(f"- #{e['id']} [{e['verdict'] or '-'}] {e['use_case'] or e['result_status'] or ''}"
                     f" \"{e['query']}\"{where}" + (f" | {tags}" if tags else "")
                     + (f" | \"{e['comment']}\"" if e["comment"] else ""))

    return "\n".join(lines)


def main():

    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="include entries already reviewed")
    parser.add_argument("--server", help="read from a running server instead of the database")
    parser.add_argument("--mark-reviewed", action="store_true", help="remember the newest id as reviewed")
    args = parser.parse_args()

    since_id = 0

    if not args.all and os.path.exists(REVIEWED):
        with open(REVIEWED) as f:
            since_id = int(f.read().strip() or 0)

    if args.server:
        entries = from_server(args.server.rstrip("/"), since_id)
    else:
        if feedback.storage_name() != "postgres":
            print(f"(No DATABASE_URL: reading the local SQLite file {feedback._sqlite_path()})\n")
        entries = from_database(since_id)

    os.makedirs(os.path.dirname(EXPORT), exist_ok=True)

    with open(EXPORT, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    sys.stdout.reconfigure(encoding="utf-8")
    print(report(entries))
    print(f"\nFull entries with context: {EXPORT}")

    if args.mark_reviewed and entries:
        with open(REVIEWED, "w") as f:
            f.write(str(entries[-1]["id"]))
        print(f"Marked up to #{entries[-1]['id']} as reviewed.")


if __name__ == "__main__":
    main()
