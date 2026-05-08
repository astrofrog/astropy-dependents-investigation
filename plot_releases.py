#!/usr/bin/env python
"""Plot distribution of most recent release dates for astropy dependents."""

import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

raw = json.loads(Path("packages2.json").read_text())
dates = []
for p in raw:
    ts = p["last_release"].replace(" UTC", "+00:00")
    dt = datetime.fromisoformat(ts)
    dates.append(dt)

cutoff = datetime(2011, 1, 1, tzinfo=timezone.utc)
dates = [d for d in dates if d >= cutoff]

fig, ax = plt.subplots(figsize=(12, 5))
ax.hist(dates, bins=60, edgecolor="white", linewidth=0.3)
ax.set_xlabel("Date of most recent release")
ax.set_ylabel("Number of packages")
ax.set_title(f"Most recent release date for {len(dates)} astropy dependents (since 2011)")
fig.tight_layout()
fig.savefig("release_dates.png", dpi=150)
print(f"Saved release_dates.png ({len(dates)} packages)")
