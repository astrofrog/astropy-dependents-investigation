#!/usr/bin/env python
"""Test astropy reverse dependencies against different astropy versions.

Usage:
    # Run tests against astropy 7.2.0 (baseline)
    python test_deps.py run packages.json --astropy-version 7.2.0

    # Run tests against astropy 8.0.0b1 (pre-release)
    python test_deps.py run packages.json --astropy-version 8.0.0b1

    # Compare two runs
    python test_deps.py compare results/astropy-7.2.0 results/astropy-8.0.0b1
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path


def get_import_name(python, package):
    """Discover the top-level import name for an installed package."""
    code = (
        "import importlib.metadata as md\n"
        f"dist = md.distribution('{package}')\n"
        "tf = dist.read_text('top_level.txt')\n"
        "if tf and tf.strip():\n"
        "    print(tf.strip().split()[0])\n"
        "else:\n"
        "    # Infer from installed files: find top-level __init__.py\n"
        "    candidates = set()\n"
        "    for f in (dist.files or []):\n"
        "        parts = f.parts\n"
        "        if len(parts) == 2 and parts[1] == '__init__.py':\n"
        "            name = parts[0]\n"
        "            if not name.endswith('.dist-info'):\n"
        "                candidates.add(name)\n"
        "    if candidates:\n"
        "        print(sorted(candidates)[0])\n"
        "    else:\n"
        f"        print('{package}'.replace('-','_').lower())\n"
    )
    try:
        r = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=30)
        name = r.stdout.strip()
        if name and r.returncode == 0:
            return name
    except Exception:
        pass
    return package.replace("-", "_").lower()


def test_package(package, astropy_version, results_dir, pytest_extra=None,
                 timeout_install=300, timeout_test=300):
    """Test a single package against a given astropy version. Returns result dict."""
    result_file = results_dir / f"{package}.json"
    if result_file.exists():
        return json.loads(result_file.read_text())

    result = {
        "package": package,
        "astropy_version": astropy_version,
        "install_ok": False,
        "has_tests": False,
        "tests_passed": None,
        "test_output": "",
        "error": "",
        "duration": 0,
    }
    start = time.time()
    tmpdir = tempfile.mkdtemp(prefix=f"atest-{package[:20]}-", dir=".tmp")

    try:
        venv = os.path.join(tmpdir, "venv")
        subprocess.run(
            ["uv", "venv", venv, "-p", sys.executable, "-q"],
            capture_output=True, timeout=60, check=True,
        )
        python = os.path.join(venv, "bin", "python")

        # Install package + pinned astropy + test runner
        install_target = f"{package}[{pytest_extra}]" if pytest_extra else package
        install_cmd = [
            "uv", "pip", "install", "--python", python, "-q",
            install_target, f"astropy=={astropy_version}", "pytest", "pytest-timeout",
        ]
        # Allow pre-releases if the astropy version looks like one
        if any(tag in astropy_version for tag in ("a", "b", "rc", "dev")):
            install_cmd.insert(4, "--prerelease=allow")

        proc = subprocess.run(
            install_cmd, capture_output=True, text=True, timeout=timeout_install
        )
        if proc.returncode != 0:
            result["error"] = proc.stderr[-1000:]
            return result
        result["install_ok"] = True

        # Verify astropy version is what we asked for
        check = subprocess.run(
            [python, "-c", "import astropy; print(astropy.__version__)"],
            capture_output=True, text=True, timeout=30,
        )
        installed_version = check.stdout.strip()
        if not installed_version.startswith(astropy_version.split("b")[0].split("a")[0].split("rc")[0].rstrip(".")):
            result["error"] = f"astropy version mismatch: wanted {astropy_version}, got {installed_version}"
            result["install_ok"] = False
            return result

        # Discover import name and run tests
        import_name = get_import_name(python, package)
        test_cmd = [
            python, "-m", "pytest",
            "--pyargs", import_name,
            "--timeout=120", "-x", "-q", "--tb=line", "--no-header",
        ]
        proc = subprocess.run(
            test_cmd, capture_output=True, text=True,
            timeout=timeout_test, cwd=tmpdir,
        )
        output = proc.stdout[-2000:]
        if proc.stderr:
            output += "\n--- stderr ---\n" + proc.stderr[-500:]
        result["test_output"] = output

        # pytest exit code 5 = no tests collected
        if proc.returncode == 5:
            result["has_tests"] = False
        else:
            result["has_tests"] = True
            result["tests_passed"] = proc.returncode == 0

    except subprocess.TimeoutExpired:
        result["error"] = "timeout"
    except Exception as e:
        result["error"] = str(e)[:500]
    finally:
        result["duration"] = round(time.time() - start, 1)
        shutil.rmtree(tmpdir, ignore_errors=True)
        try:
            result_file.write_text(json.dumps(result, indent=2))
        except Exception:
            pass

    return result


def _parse_packages(raw):
    """Collapse per-version rows into per-package info using the latest release."""
    by_name = {}
    for row in raw:
        name = row["name"]
        ts_str = row.get("release_date") or row.get("last_release") or ""
        ts = ts_str.replace(" UTC", "+00:00") if ts_str else ""
        existing = by_name.get(name)
        if existing is None or (ts and ts > existing["_ts"]):
            by_name[name] = {
                "name": name,
                "_ts": ts,
                "pytest_extra": row.get("pytest_extra") or None,
            }
    return list(by_name.values())


def cmd_run(args):
    raw = json.loads(Path(args.packages_json).read_text())
    packages = _parse_packages(raw)

    if args.max_age_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.max_age_days)
        before = len(packages)
        packages = [
            p for p in packages
            if not p["_ts"] or datetime.fromisoformat(p["_ts"]) >= cutoff
        ]
        print(f"Filtered to {len(packages)}/{before} packages released in last {args.max_age_days} days")

    if args.shuffle:
        random.shuffle(packages)
    if args.limit:
        packages = packages[: args.limit]

    if args.dry_run:
        print(f"{len(packages)} packages would be tested:")
        for p in packages:
            extra = f"  [install with [{p['pytest_extra']}]]" if p["pytest_extra"] else ""
            print(f"  {p['name']}{extra}")
        return

    Path(".tmp").mkdir(exist_ok=True)
    results_dir = Path(args.results_dir) / f"astropy-{args.astropy_version}"
    results_dir.mkdir(parents=True, exist_ok=True)

    already_done = len(list(results_dir.glob("*.json")))
    print(f"Testing {len(packages)} packages against astropy {args.astropy_version}")
    print(f"Workers: {args.workers}, results: {results_dir}, already done: {already_done}")

    counts = {"done": 0, "install_ok": 0, "has_tests": 0, "passed": 0, "failed": 0}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                test_package, p["name"], args.astropy_version, results_dir,
                p["pytest_extra"], args.timeout_install, args.timeout_test,
            ): p["name"]
            for p in packages
        }
        for future in as_completed(futures):
            pkg = futures[future]
            try:
                r = future.result()
            except Exception as e:
                print(f"  CRASH {pkg}: {e}")
                continue

            counts["done"] += 1
            if r["install_ok"]:
                counts["install_ok"] += 1
            if r["has_tests"]:
                counts["has_tests"] += 1
            if r["tests_passed"] is True:
                counts["passed"] += 1
            elif r["tests_passed"] is False:
                counts["failed"] += 1

            # Print progress on every test-bearing package, or every 50
            if r["has_tests"] or counts["done"] % 50 == 0:
                if r["tests_passed"] is True:
                    status = "PASS"
                elif r["tests_passed"] is False:
                    status = "FAIL"
                elif r["install_ok"]:
                    status = "no-tests"
                else:
                    status = "install-fail"
                n, total = counts["done"], len(packages)
                print(
                    f"  [{n}/{total}] {pkg}: {status}  "
                    f"(ok:{counts['install_ok']} tests:{counts['has_tests']} "
                    f"pass:{counts['passed']} fail:{counts['failed']})"
                )

    summary = {
        "astropy_version": args.astropy_version,
        "total": len(packages),
        **counts,
    }
    (results_dir / "_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nDone! {counts['install_ok']} installable, {counts['has_tests']} with tests, "
          f"{counts['passed']} passed, {counts['failed']} failed")


def cmd_compare(args):
    dir_a, dir_b = Path(args.dir_a), Path(args.dir_b)

    def load_results(d):
        results = {}
        for f in d.glob("*.json"):
            if f.name.startswith("_"):
                continue
            r = json.loads(f.read_text())
            results[r["package"]] = r
        return results

    a, b = load_results(dir_a), load_results(dir_b)

    # Find packages that had tests in both runs
    both = set(a) & set(b)
    with_tests = [p for p in both if a[p]["has_tests"] and b[p]["has_tests"]]

    regressions = []  # passed in A, failed in B
    fixes = []        # failed in A, passed in B
    new_failures = [] # installed in A, can't install in B
    for p in sorted(with_tests):
        if a[p]["tests_passed"] and not b[p]["tests_passed"]:
            regressions.append(p)
        elif not a[p]["tests_passed"] and b[p]["tests_passed"]:
            fixes.append(p)

    for p in sorted(both):
        if a[p]["install_ok"] and not b[p]["install_ok"]:
            new_failures.append(p)

    print(f"Comparing {dir_a.name} vs {dir_b.name}")
    print(f"Packages in both: {len(both)}, with tests in both: {len(with_tests)}")
    print(f"\nRegressions (passed -> failed): {len(regressions)}")
    for p in regressions:
        print(f"  {p}")
    print(f"\nFixes (failed -> passed): {len(fixes)}")
    for p in fixes:
        print(f"  {p}")
    print(f"\nNew install failures: {len(new_failures)}")
    for p in new_failures:
        print(f"  {p}: {b[p]['error'][:100]}")


def main():
    parser = argparse.ArgumentParser(description="Test astropy reverse dependencies")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run tests")
    run_p.add_argument("packages_json", help="JSON file with [{name: ...}, ...] entries")
    run_p.add_argument("--astropy-version", default="7.2.0")
    run_p.add_argument("--workers", type=int, default=os.cpu_count())
    run_p.add_argument("--results-dir", default="results")
    run_p.add_argument("--timeout-install", type=int, default=300)
    run_p.add_argument("--timeout-test", type=int, default=300)
    run_p.add_argument("--limit", type=int, help="Only test first N packages")
    run_p.add_argument("--shuffle", action="store_true", help="Randomize package order")
    run_p.add_argument("--max-age-days", type=int, help="Only include packages released within N days")
    run_p.add_argument("--dry-run", action="store_true", help="Print matching package count and list, don't run")

    cmp_p = sub.add_parser("compare", help="Compare two result sets")
    cmp_p.add_argument("dir_a", help="Baseline results directory")
    cmp_p.add_argument("dir_b", help="New results directory")

    args = parser.parse_args()
    if args.command == "run":
        cmd_run(args)
    elif args.command == "compare":
        cmd_compare(args)


if __name__ == "__main__":
    main()
