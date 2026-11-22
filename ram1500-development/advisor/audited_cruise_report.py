"""Summarize locally audited item-28 cruise edges for one Central calendar day."""
import argparse
from collections import Counter
from datetime import date, datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

CAUSES = {
    "gas": "gas pedal", "brake": "brake pedal", "both pedals": "both pedals",
    "host cancel": "Comma cancel", "factory button": "cruise switch",
    "ESP request": "truck request", "availability lost": "cruise unavailable",
    "raw driver brake before processed pedal flag": "brake pedal",
}


def audited_counts(routes, day):
    counts = Counter()
    seen = set()
    for route, result in routes.items():
        for event in result["events"]:
            key = (route, event["ns"])
            stamp = datetime.fromisoformat(event["central"])
            if stamp.utcoffset() is None:
                raise ValueError("An audited cutoff requires a timestamp with a UTC offset")
            if key in seen or stamp.astimezone(ZoneInfo("America/Chicago")).date() != day:
                continue
            seen.add(key)
            counts[CAUSES.get(event["kind"], "cause unresolved")] += 1
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=Path, help="packed-cruise.json or cabin-cruise.json")
    parser.add_argument("day", type=date.fromisoformat)
    args = parser.parse_args()
    data = json.loads(args.audit.read_bytes())
    counts = audited_counts(data.get("routes", data), args.day)
    print(f"Audited factory cruise cutoffs for {args.day} (Central), in available logs")
    for cause, total in sorted(counts.items()):
        print(f"  {cause}: {total}")
    print(f"Total: {sum(counts.values())}")
