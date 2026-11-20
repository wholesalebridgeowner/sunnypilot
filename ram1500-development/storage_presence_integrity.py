"""Fail-closed, privacy-preserving inventory checks for private Q-/R-log buckets.

Input object keys and target names remain in caller memory, never in results.
The function lists nothing and performs no network or vehicle operations.
It is an object PRESENCE audit, not a decoder, integrity hash, or route proof.
"""
from __future__ import annotations

from collections import defaultdict
import re
from typing import Iterable

_KEY = re.compile(r"^logs/(.+)/(0|[1-9][0-9]*)/(qlog|rlog)(?:\.(?:zst|bz2))?$")
_TARGET = re.compile(r"^[A-Za-z0-9_-]{2,40}$")


def audit_inventory(
    object_keys: Iterable[str], *, target_short_id: str,
    expected_segments: int, focus_segment: int, listing_complete: bool
) -> dict:
  """Return aggregate-only evidence from a *complete* local private listing.

  target_short_id matches the route token before '--' after any '|' namespace.
  This mirrors the original collector's key layout without ever returning it.
  Never use this result to assert that an object never existed in Comma cloud.
  """
  if not _TARGET.fullmatch(target_short_id):
    raise ValueError("invalid target selector")
  if type(expected_segments) is not int or not 1 <= expected_segments <= 10000:
    raise ValueError("invalid expected segment count")
  if type(focus_segment) is not int or not 0 <= focus_segment < expected_segments:
    raise ValueError("invalid focus segment")
  if type(listing_complete) is not bool:
    raise ValueError("listing completeness must be explicit")

  routes: dict[str, dict[str, set[int]]] = defaultdict(lambda: {"qlog": set(), "rlog": set()})
  classified = unclassified = total = 0
  for key in object_keys:
    total += 1
    if not isinstance(key, str):
      unclassified += 1
      continue
    match = _KEY.fullmatch(key)
    if not match:
      unclassified += 1
      continue
    route, index_raw, kind = match.groups()
    index = int(index_raw)
    if index > 1000000:
      unclassified += 1
      continue
    routes[route][kind].add(index)
    classified += 1

  matches = [k for k in routes if k.split("|")[-1].startswith(target_short_id + "--")]
  result = {
      "status": ("incomplete_listing" if not listing_complete else
                 "not_found_in_scoped_listing" if not matches else
                 "ambiguous_target" if len(matches) != 1 else "one_target_found"),
      "listingComplete": listing_complete,
      "listedObjects": total,
      "classifiedObjects": classified,
      "unclassifiedObjects": unclassified,
      "distinctRoutes": len(routes),
      "matchedRoutes": len(matches),
      "interpretation": "object_existence_only_not_log_integrity_or_global_cloud_absence",
  }
  if len(matches) == 1:
    for kind in ("qlog", "rlog"):
      indices = routes[matches[0]][kind]
      result[kind] = {
          "observedObjectsBySegment": len(indices),
          "expectedSegmentsPresent": sum(i in indices for i in range(expected_segments)),
          "missingExpectedSegments": sum(i not in indices for i in range(expected_segments)),
          "segmentsBeyondExpected": sum(i >= expected_segments for i in indices),
          "focusSegmentPresent": focus_segment in indices,
      }
  return result
