#!/usr/bin/env python
"""Find all astropy API usage in downstream packages.

Usage:
    # Single package
    python find_api_usage.py scan reproject

    # From package list JSON
    python find_api_usage.py scan-all packages3.json --max-age-days 365 --limit 10

    # Show results
    python find_api_usage.py show api-usage/reproject.json
"""

import argparse
import ast
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


class AstropyUsageFinder(ast.NodeVisitor):
    """Walk an AST to find all astropy imports and attribute access."""

    def __init__(self):
        # Direct imports: "from astropy.table import Table" -> "astropy.table.Table"
        self.imports = set()
        # Map alias -> astropy module path, e.g. {"t": "astropy.table"}
        self.aliases = {}

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name.startswith("astropy"):
                self.imports.add(alias.name)
                local = alias.asname or alias.name
                self.aliases[local] = alias.name
                # Also register subparts: "import astropy.table" makes "astropy" resolve too
                parts = alias.name.split(".")
                for i in range(len(parts)):
                    prefix = ".".join(parts[: i + 1])
                    local_prefix = (alias.asname or parts[0]) if i == 0 else ".".join(
                        [alias.asname or parts[0]] + parts[1: i + 1]
                    )
                    self.aliases[local_prefix] = prefix
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module = node.module or ""
        if not module.startswith("astropy"):
            return
        for alias in node.names:
            if alias.name == "*":
                self.imports.add(f"{module}.*")
            else:
                self.imports.add(f"{module}.{alias.name}")
                local = alias.asname or alias.name
                self.aliases[local] = f"{module}.{alias.name}"
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # Resolve chains like t.Table when t = astropy.table
        chain = self._resolve_chain(node)
        if chain and chain.startswith("astropy."):
            self.imports.add(chain)
        self.generic_visit(node)

    def _resolve_chain(self, node):
        """Resolve an attribute chain to a dotted string, substituting known aliases."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        else:
            return None
        parts.reverse()
        # Try progressively longer prefixes against aliases
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in self.aliases:
                resolved = self.aliases[prefix]
                if i < len(parts):
                    resolved += "." + ".".join(parts[i:])
                return resolved
        return ".".join(parts)


def find_usage_in_source(source_dir):
    """Scan all .py files in a directory for astropy usage."""
    finder = AstropyUsageFinder()
    files_scanned = 0
    errors = []

    for py_file in Path(source_dir).rglob("*.py"):
        try:
            source = py_file.read_text(errors="replace")
            tree = ast.parse(source, filename=str(py_file))
            finder.visit(tree)
            files_scanned += 1
        except SyntaxError:
            errors.append(str(py_file))

    return {
        "imports": sorted(finder.imports),
        "files_scanned": files_scanned,
        "parse_errors": errors,
    }


def scan_package(package, output_dir):
    """Install a package and scan its source for astropy API usage."""
    result_file = output_dir / f"{package}.json"
    if result_file.exists():
        return json.loads(result_file.read_text())

    result = {
        "package": package,
        "install_ok": False,
        "imports": [],
        "public_api": [],
        "private_api": [],
        "files_scanned": 0,
        "error": "",
        "duration": 0,
    }
    start = time.time()
    tmpdir = tempfile.mkdtemp(prefix=f"api-{package[:20]}-", dir=".tmp")

    try:
        venv = os.path.join(tmpdir, "venv")
        subprocess.run(
            ["uv", "venv", venv, "-p", sys.executable, "-q"],
            capture_output=True, timeout=60, check=True,
        )
        python = os.path.join(venv, "bin", "python")

        # Install package
        proc = subprocess.run(
            ["uv", "pip", "install", "--python", python, "-q", package],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            result["error"] = proc.stderr[-500:]
            return result
        result["install_ok"] = True

        # Find only this package's installed source directories
        code = (
            "import importlib.metadata as md, pathlib\n"
            f"dist = md.distribution('{package}')\n"
            "dirs = set()\n"
            "for f in (dist.files or []):\n"
            "    if f.suffix == '.py' and not '.dist-info' in str(f):\n"
            "        # Get the top-level directory\n"
            "        dirs.add(str(f.parts[0]))\n"
            "sp = dist._path.parent\n"
            "for d in sorted(dirs):\n"
            "    p = sp / d\n"
            "    if p.exists():\n"
            "        print(p)\n"
        )
        proc = subprocess.run(
            [python, "-c", code], capture_output=True, text=True, timeout=30,
        )
        pkg_dirs = [l for l in proc.stdout.strip().splitlines() if l]

        usage = {"imports": [], "files_scanned": 0, "parse_errors": []}
        for d in pkg_dirs:
            u = find_usage_in_source(d)
            usage["imports"].extend(u["imports"])
            usage["files_scanned"] += u["files_scanned"]
            usage["parse_errors"].extend(u["parse_errors"])
        usage["imports"] = sorted(set(usage["imports"]))
        # Filter to astropy.* only (excludes astropy_healpix etc)
        all_imports = [i for i in usage["imports"] if i == "astropy" or i.startswith("astropy.")]
        public = [i for i in all_imports if not any(p.startswith("_") for p in i.split("astropy.", 1)[-1].split("."))]
        private = [i for i in all_imports if i not in set(public)]

        result["imports"] = all_imports
        result["public_api"] = public
        result["private_api"] = private
        result["files_scanned"] = usage["files_scanned"]

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


def cmd_scan(args):
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = scan_package(args.package, output_dir)
    n_pub = len(result["public_api"])
    n_priv = len(result["private_api"])
    ok = "yes" if result["install_ok"] else "no"
    print(f"{result['package']}: {ok}, {n_pub} public, {n_priv} private API refs, "
          f"{result['files_scanned']} files, {result['duration']}s")
    if result["error"]:
        print(f"  error: {result['error'][:200]}")
    print(f"  saved to {output_dir / (args.package + '.json')}")


def cmd_scan_all(args):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    raw = json.loads(Path(args.packages_json).read_text())

    # Deduplicate to latest release per package
    by_name = {}
    for row in raw:
        name = row["name"]
        ts = (row.get("release_date") or row.get("last_release") or "").replace(" UTC", "+00:00")
        if name not in by_name or (ts and ts > by_name[name]["_ts"]):
            by_name[name] = {"name": name, "_ts": ts}
    packages = list(by_name.values())

    if args.max_age_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.max_age_days)
        before = len(packages)
        packages = [p for p in packages if not p["_ts"] or datetime.fromisoformat(p["_ts"]) >= cutoff]
        print(f"Filtered to {len(packages)}/{before} packages released in last {args.max_age_days} days")

    if args.shuffle:
        random.shuffle(packages)
    if args.limit:
        packages = packages[: args.limit]

    if args.dry_run:
        print(f"{len(packages)} packages would be scanned")
        return

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    total = len(packages)
    Path(".tmp").mkdir(exist_ok=True)
    print(f"Scanning {total} packages, workers: {args.workers}, output: {output_dir}")

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scan_package, p["name"], output_dir): p["name"] for p in packages}
        for future in as_completed(futures):
            pkg = futures[future]
            done += 1
            try:
                future.result()
            except Exception:
                pass
            pct = done * 100 // total
            bar = f"[{'#' * (pct // 2)}{'.' * (50 - pct // 2)}]"
            print(f"\r  {bar} {done}/{total} ({pct}%)", end="", flush=True)
    print()

    # Write aggregate summary
    all_api = {}
    for f in output_dir.glob("*.json"):
        if f.name.startswith("_"):
            continue
        r = json.loads(f.read_text())
        for imp in r.get("imports", []):
            all_api.setdefault(imp, []).append(r["package"])

    summary = {
        "total_packages": len(packages),
        "api_usage": {k: {"count": len(v), "packages": sorted(v)} for k, v in sorted(all_api.items())},
    }
    (output_dir / "_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nDone! Found {len(all_api)} distinct astropy API references across {len(packages)} packages")


def cmd_show(args):
    r = json.loads(Path(args.result_json).read_text())
    print(f"Package: {r['package']}")
    print(f"Public API ({len(r['public_api'])}):")
    for i in r["public_api"]:
        print(f"  {i}")
    if r["private_api"]:
        print(f"Private API ({len(r['private_api'])}):")
        for i in r["private_api"]:
            print(f"  {i}")


def main():
    parser = argparse.ArgumentParser(description="Find astropy API usage in packages")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="Scan a single package")
    scan_p.add_argument("package")
    scan_p.add_argument("--output-dir", default="api-usage")

    all_p = sub.add_parser("scan-all", help="Scan packages from JSON list")
    all_p.add_argument("packages_json")
    all_p.add_argument("--output-dir", default="api-usage")
    all_p.add_argument("--workers", type=int, default=os.cpu_count())
    all_p.add_argument("--max-age-days", type=int)
    all_p.add_argument("--limit", type=int)
    all_p.add_argument("--shuffle", action="store_true")
    all_p.add_argument("--dry-run", action="store_true")

    show_p = sub.add_parser("show", help="Show results for a package")
    show_p.add_argument("result_json")

    args = parser.parse_args()
    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "scan-all":
        cmd_scan_all(args)
    elif args.command == "show":
        cmd_show(args)


if __name__ == "__main__":
    main()
