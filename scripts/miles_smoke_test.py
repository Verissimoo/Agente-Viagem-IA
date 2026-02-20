from __future__ import annotations

import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from miles_app.moblix_client import search_flights


def main():
    print("Calling Moblix (LATAM)...")
    r = search_flights(
        origin="BSB",
        destination="SAO",
        departure_date="2026-03-30",
        return_date=None,
        suppliers=["latam"],
        search_type=None,
    )
    print("requestId=", r.get("requestId"), "groups=", len(r.get("flightGroups") or []))
    print("has_totalPoints=", ("totalPoints" in json.dumps(r)))


if __name__ == "__main__":
    main()



