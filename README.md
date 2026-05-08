# astropy-deps

Test astropy reverse dependencies against different astropy versions and analyze their API usage.

## Setup

Requires `uv`, `flask`, `matplotlib`.

## Package list

`packages.json` is generated from BigQuery (`bigquery-public-data.pypi.distribution_metadata`). It contains all PyPI packages that list `astropy` in `requires_dist`, with per-version release dates and info about whether pytest is available (and behind which extra).

```sql
SELECT
  dm.name,
  dm.version,
  MIN(dm.upload_time) AS release_date,
  LOGICAL_OR((
    SELECT COUNTIF(REGEXP_CONTAINS(d, r'\bpytest\b')) > 0
    FROM UNNEST(dm.requires_dist) AS d
  )) AS has_pytest_dep,
  MAX((
    SELECT MIN(REGEXP_EXTRACT(d, r'extra\s*==\s*["\x27](\w+)["\x27]'))
    FROM UNNEST(dm.requires_dist) AS d
    WHERE REGEXP_CONTAINS(d, r'\bpytest\b')
      AND REGEXP_CONTAINS(d, r'extra\s*==\s*["\x27]')
  )) AS pytest_extra
FROM `bigquery-public-data.pypi.distribution_metadata` AS dm,
UNNEST(dm.requires_dist) AS dep
WHERE REGEXP_CONTAINS(dep, r'\bastropy\b')
GROUP BY dm.name, dm.version
ORDER BY dm.name, release_date
```

## Test runner (`test_deps.py`)

Installs each package in an isolated venv with a pinned astropy version, runs `pytest --pyargs`, and records results as JSON.

```bash
# Dry run — see how many packages match
python test_deps.py run packages.json --max-age-days 365 --dry-run

# Run 50 random recent packages against astropy 7.2.0
python test_deps.py run packages.json --astropy-version 7.2.0 --max-age-days 365 --shuffle --limit 50

# Run against a pre-release
python test_deps.py run packages.json --astropy-version 8.0.0b1

# Compare two runs
python test_deps.py compare results/astropy-7.2.0 results/astropy-8.0.0b1
```

Results are saved per-package as JSON in `results/astropy-<version>/` with resume support.

**Limitations:** Only finds tests shipped inside installed wheels (`pytest --pyargs`). Packages that only include tests in their sdist/repo won't be detected. The import name is inferred from `top_level.txt` or installed file layout, which may occasionally be wrong.

## API usage scanner (`find_api_usage.py`)

Uses `ast` to find all `astropy.*` imports and attribute access in a package's installed source.

```bash
# Single package
python find_api_usage.py scan reproject

# Bulk scan
python find_api_usage.py scan-all packages.json --max-age-days 365 --limit 100
```

Results go to `api-usage/`. Reports public vs private API usage per package plus an aggregate summary.

**Limitations:** Static analysis only — catches `import`/`from` statements and attribute chains with alias tracking, but cannot detect dynamic access (`getattr`, `__import__`, etc). Only scans `astropy.*`, not affiliated packages like `astropy_healpix`.

## Web UI (`web.py`)

```bash
python web.py  # http://localhost:5555
```

Browse results, filter by status, compare versions side by side (grid view), view test output.

## Release date plot (`plot_releases.py`)

```bash
python plot_releases.py  # generates release_dates.png
```
