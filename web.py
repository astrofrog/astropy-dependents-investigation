#!/usr/bin/env python
"""Web UI for browsing astropy dependency test results."""

import json
from pathlib import Path

from flask import Flask, abort, render_template_string

app = Flask(__name__)
RESULTS_DIR = Path(__file__).parent / "results"

STYLE = """
<style>
  body { font-family: -apple-system, system-ui, sans-serif; margin: 2em; line-height: 1.5; }
  h1, h2 { margin-top: 0; }
  a { color: #0366d6; text-decoration: none; }
  a:hover { text-decoration: underline; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; }
  th { background: #f6f8fa; position: sticky; top: 0; cursor: pointer; }
  tr:hover { background: #f0f0f0; }
  .pass { color: #22863a; font-weight: bold; }
  .fail { color: #cb2431; font-weight: bold; }
  .skip { color: #6a737d; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.85em; }
  .pill.pass { background: #dcffe4; }
  .pill.fail { background: #ffdce0; }
  .pill.skip { background: #f1f1f1; }
  pre { background: #1e1e1e; color: #d4d4d4; padding: 1em; border-radius: 6px;
        overflow-x: auto; font-size: 0.85em; white-space: pre-wrap; word-wrap: break-word; }
  nav { margin-bottom: 1.5em; font-size: 0.9em; }
  .stats { display: flex; gap: 2em; margin-bottom: 1.5em; }
  .stat { text-align: center; }
  .stat .num { font-size: 2em; font-weight: bold; }
  .filter-bar { margin-bottom: 1em; }
  .filter-bar input { padding: 6px 10px; width: 300px; border: 1px solid #ddd; border-radius: 4px; }
  .filter-bar select { padding: 6px; border: 1px solid #ddd; border-radius: 4px; }
</style>
"""

SCRIPT = """
<script>
document.querySelectorAll('th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const table = th.closest('table');
    const tbody = table.querySelector('tbody');
    const idx = Array.from(th.parentNode.children).indexOf(th);
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
    th.dataset.dir = dir;
    rows.sort((a, b) => {
      let av = a.children[idx].dataset.val || a.children[idx].textContent;
      let bv = b.children[idx].dataset.val || b.children[idx].textContent;
      if (!isNaN(av) && !isNaN(bv)) { av = parseFloat(av); bv = parseFloat(bv); }
      if (av < bv) return dir === 'asc' ? -1 : 1;
      if (av > bv) return dir === 'asc' ? 1 : -1;
      return 0;
    });
    rows.forEach(r => tbody.appendChild(r));
  });
});
const filterInput = document.getElementById('pkg-filter');
const statusFilter = document.getElementById('status-filter');
const hideNoTests = document.getElementById('hide-no-tests');
function applyFilters() {
  const text = (filterInput?.value || '').toLowerCase();
  const status = statusFilter?.value || 'all';
  const hideNT = hideNoTests?.checked;
  document.querySelectorAll('tbody tr').forEach(row => {
    const name = row.children[0].textContent.toLowerCase();
    const st = row.dataset.status || '';
    const matchText = !text || name.includes(text);
    const matchStatus = status === 'all' || st === status;
    const matchTests = !hideNT || (st !== 'no-tests' && st !== 'install-fail');
    row.style.display = matchText && matchStatus && matchTests ? '' : 'none';
  });
}
filterInput?.addEventListener('input', applyFilters);
statusFilter?.addEventListener('change', applyFilters);
hideNoTests?.addEventListener('change', applyFilters);
applyFilters();
</script>
"""

INDEX_PAGE = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Astropy Dep Tests</title>"
    + STYLE
    + "</head><body>"
    + """
<h1>Astropy Dependency Test Results</h1>
<table>
<thead><tr><th>Version</th><th>Packages</th><th>Installable</th><th>With Tests</th><th>Passed</th><th>Failed</th></tr></thead>
<tbody>
{% for v in versions %}
<tr>
  <td><a href="/results/{{ v.name }}">{{ v.name }}</a></td>
  <td>{{ v.total }}</td>
  <td>{{ v.install_ok }}</td>
  <td>{{ v.has_tests }}</td>
  <td class="pass">{{ v.passed }}</td>
  <td class="fail">{{ v.failed }}</td>
</tr>
{% endfor %}
</tbody>
</table>
{% if comparisons %}
<h2 style="margin-top:1.5em">Compare</h2>
<ul>
{% for a, b in comparisons %}
<li><a href="/compare/{{ a }}/{{ b }}">{{ a }} vs {{ b }}</a></li>
{% endfor %}
</ul>
{% endif %}
{% if versions|length > 1 %}
<p><a href="/grid">View all versions side by side</a></p>
{% endif %}
</body></html>
"""
)

VERSION_PAGE = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'><title>{{ version }} results</title>"
    + STYLE
    + "</head><body>"
    + """
<nav><a href="/">&larr; All versions</a></nav>
<h1>Results for {{ version }}</h1>
<div class="stats">
  <div class="stat"><div class="num">{{ results|length }}</div>total</div>
  <div class="stat"><div class="num">{{ counts.install_ok }}</div>installable</div>
  <div class="stat"><div class="num">{{ counts.has_tests }}</div>with tests</div>
  <div class="stat"><div class="num pass">{{ counts.passed }}</div>passed</div>
  <div class="stat"><div class="num fail">{{ counts.failed }}</div>failed</div>
</div>
<div class="filter-bar">
  <input id="pkg-filter" placeholder="Filter by package name...">
  <select id="status-filter">
    <option value="all">All</option>
    <option value="pass">Passed</option>
    <option value="fail">Failed</option>
    <option value="no-tests">No tests</option>
    <option value="install-fail">Install failed</option>
  </select>
  <label><input type="checkbox" id="hide-no-tests" checked> Hide packages without tests</label>
</div>
<table>
<thead><tr>
  <th data-sort>Package</th>
  <th data-sort>Status</th>
  <th data-sort>Duration (s)</th>
  <th>Error</th>
</tr></thead>
<tbody>
{% for r in results %}
{% if r.tests_passed == true %}
  {% set status = "pass" %}{% set label = "PASS" %}
{% elif r.tests_passed == false %}
  {% set status = "fail" %}{% set label = "FAIL" %}
{% elif r.install_ok %}
  {% set status = "no-tests" %}{% set label = "no tests" %}
{% else %}
  {% set status = "install-fail" %}{% set label = "install fail" %}
{% endif %}
<tr data-status="{{ status }}">
  <td><a href="/results/{{ version }}/{{ r.package }}">{{ r.package }}</a></td>
  <td data-val="{{ status }}"><span class="pill {{ status }}">{{ label }}</span></td>
  <td data-val="{{ r.duration }}">{{ r.duration }}</td>
  <td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
      title="{{ r.error }}">{{ r.error[:100] }}</td>
</tr>
{% endfor %}
</tbody>
</table>
"""
    + SCRIPT
    + "</body></html>"
)

DETAIL_PAGE = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
    "<title>{{ r.package }} &mdash; {{ r.astropy_version }}</title>"
    + STYLE
    + "</head><body>"
    + """
<nav><a href="/">&larr; All versions</a> / <a href="/results/{{ r.astropy_version }}">{{ r.astropy_version }}</a></nav>
<h1>{{ r.package }}</h1>
<table style="width:auto">
  <tr><td><strong>Astropy version</strong></td><td>{{ r.astropy_version }}</td></tr>
  <tr><td><strong>Installed</strong></td><td>{{ "yes" if r.install_ok else "no" }}</td></tr>
  <tr><td><strong>Has tests</strong></td><td>{{ "yes" if r.has_tests else "no" }}</td></tr>
  <tr><td><strong>Tests passed</strong></td>
      <td>{% if r.tests_passed == true %}<span class="pass">PASS</span>
          {% elif r.tests_passed == false %}<span class="fail">FAIL</span>
          {% else %}-{% endif %}</td></tr>
  <tr><td><strong>Duration</strong></td><td>{{ r.duration }}s</td></tr>
  {% if r.error %}<tr><td><strong>Error</strong></td><td>{{ r.error }}</td></tr>{% endif %}
</table>
{% if r.test_output %}
<h2>Test output</h2>
<pre>{{ r.test_output }}</pre>
{% endif %}
</body></html>
"""
)

COMPARE_PAGE = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
    "<title>Compare {{ name_a }} vs {{ name_b }}</title>"
    + STYLE
    + "</head><body>"
    + """
<nav><a href="/">&larr; All versions</a></nav>
<h1>{{ name_a }} vs {{ name_b }}</h1>

<h2>Regressions (passed &rarr; failed): {{ regressions|length }}</h2>
{% if regressions %}
<table><thead><tr><th>Package</th><th>{{ name_a }}</th><th>{{ name_b }}</th></tr></thead><tbody>
{% for p in regressions %}
<tr>
  <td><a href="/results/{{ name_b }}/{{ p }}">{{ p }}</a></td>
  <td><span class="pill pass">PASS</span></td>
  <td><span class="pill fail">FAIL</span></td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<p class="skip">None</p>{% endif %}

<h2>Fixes (failed &rarr; passed): {{ fixes|length }}</h2>
{% if fixes %}
<table><thead><tr><th>Package</th><th>{{ name_a }}</th><th>{{ name_b }}</th></tr></thead><tbody>
{% for p in fixes %}
<tr>
  <td><a href="/results/{{ name_b }}/{{ p }}">{{ p }}</a></td>
  <td><span class="pill fail">FAIL</span></td>
  <td><span class="pill pass">PASS</span></td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<p class="skip">None</p>{% endif %}

<h2>New install failures: {{ new_install_failures|length }}</h2>
{% if new_install_failures %}
<table><thead><tr><th>Package</th><th>Error</th></tr></thead><tbody>
{% for p in new_install_failures %}
<tr><td>{{ p }}</td><td>{{ b_results[p].error[:150] }}</td></tr>
{% endfor %}
</tbody></table>
{% else %}<p class="skip">None</p>{% endif %}
</body></html>
"""
)


GRID_PAGE = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'><title>All versions</title>"
    + STYLE
    + """
<style>
  .pill-sm { display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 0.8em; }
  .pill-sm.pass { background: #dcffe4; color: #22863a; }
  .pill-sm.fail { background: #ffdce0; color: #cb2431; }
  .pill-sm.no-tests { background: #f1f1f1; color: #6a737d; }
  .pill-sm.install-fail { background: #f1f1f1; color: #999; }
  .regression { background: #fff5f5 !important; }
</style>
</head><body>
<nav><a href="/">&larr; Home</a></nav>
<h1>All versions side by side</h1>
<div class="filter-bar">
  <input id="pkg-filter" placeholder="Filter by package name...">
  <select id="status-filter">
    <option value="all">All</option>
    <option value="regression">Regressions only</option>
    <option value="has-tests">Has tests in any version</option>
    <option value="pass">Pass in all</option>
    <option value="fail">Fail in any</option>
  </select>
  <label><input type="checkbox" id="hide-no-tests" checked> Hide packages without tests</label>
</div>
<table>
<thead><tr>
  <th data-sort>Package</th>
  {% for v in versions %}<th data-sort>{{ v }}</th>{% endfor %}
</tr></thead>
<tbody>
{% for pkg in packages %}
<tr data-status="{{ pkg.filter_status }}" class="{{ 'regression' if pkg.is_regression else '' }}">
  <td>{{ pkg.name }}</td>
  {% for v in versions %}
  {% set s = pkg.statuses[v] %}
  <td data-val="{{ s.sort_key }}">
    {% if s.status %}
    <a href="/results/{{ v }}/{{ pkg.name }}"><span class="pill-sm {{ s.status }}">{{ s.label }}</span></a>
    {% else %}-{% endif %}
  </td>
  {% endfor %}
</tr>
{% endfor %}
</tbody>
</table>
"""
    + """
<script>
document.querySelectorAll('th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const table = th.closest('table');
    const tbody = table.querySelector('tbody');
    const idx = Array.from(th.parentNode.children).indexOf(th);
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
    th.dataset.dir = dir;
    rows.sort((a, b) => {
      let av = a.children[idx].dataset.val || a.children[idx].textContent;
      let bv = b.children[idx].dataset.val || b.children[idx].textContent;
      if (!isNaN(av) && !isNaN(bv)) { av = parseFloat(av); bv = parseFloat(bv); }
      if (av < bv) return dir === 'asc' ? -1 : 1;
      if (av > bv) return dir === 'asc' ? 1 : -1;
      return 0;
    });
    rows.forEach(r => tbody.appendChild(r));
  });
});
const filterInput = document.getElementById('pkg-filter');
const statusFilter = document.getElementById('status-filter');
function applyFilters() {
  const text = (filterInput?.value || '').toLowerCase();
  const status = statusFilter?.value || 'all';
  const hideNT = document.getElementById('hide-no-tests')?.checked;
  document.querySelectorAll('tbody tr').forEach(row => {
    const name = row.children[0].textContent.toLowerCase();
    const fs = row.dataset.status || '';
    const matchText = !text || name.includes(text);
    const matchTests = !hideNT || fs.includes('has-tests');
    let matchStatus = true;
    if (status === 'regression') matchStatus = fs.includes('regression');
    else if (status === 'has-tests') matchStatus = fs.includes('has-tests');
    else if (status === 'pass') matchStatus = fs.includes('all-pass');
    else if (status === 'fail') matchStatus = fs.includes('any-fail');
    row.style.display = matchText && matchStatus && matchTests ? '' : 'none';
  });
}
filterInput?.addEventListener('input', applyFilters);
statusFilter?.addEventListener('change', applyFilters);
document.getElementById('hide-no-tests')?.addEventListener('change', applyFilters);
applyFilters();
</script>
</body></html>
"""
)


def load_results(version_dir):
    results = []
    for f in version_dir.glob("*.json"):
        if f.name.startswith("_"):
            continue
        results.append(json.loads(f.read_text()))
    results.sort(key=lambda r: r["package"].lower())
    return results


def summarize(results):
    counts = {"install_ok": 0, "has_tests": 0, "passed": 0, "failed": 0}
    for r in results:
        if r["install_ok"]:
            counts["install_ok"] += 1
        if r["has_tests"]:
            counts["has_tests"] += 1
        if r["tests_passed"] is True:
            counts["passed"] += 1
        elif r["tests_passed"] is False:
            counts["failed"] += 1
    return counts


@app.route("/")
def index():
    versions = []
    for d in sorted(RESULTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        results = load_results(d)
        counts = summarize(results)
        versions.append({"name": d.name, "total": len(results), **counts})
    names = [v["name"] for v in versions]
    comparisons = list(zip(names, names[1:])) if len(names) > 1 else []
    return render_template_string(INDEX_PAGE, versions=versions, comparisons=comparisons)


@app.route("/results/<version>")
def version_results(version):
    version_dir = RESULTS_DIR / version
    if not version_dir.is_dir():
        abort(404)
    results = load_results(version_dir)
    counts = summarize(results)
    return render_template_string(VERSION_PAGE, version=version, results=results, counts=counts)


@app.route("/results/<version>/<package>")
def package_detail(version, package):
    result_file = RESULTS_DIR / version / f"{package}.json"
    if not result_file.exists():
        abort(404)
    r = json.loads(result_file.read_text())
    return render_template_string(DETAIL_PAGE, r=r)


@app.route("/compare/<version_a>/<version_b>")
def compare(version_a, version_b):
    dir_a, dir_b = RESULTS_DIR / version_a, RESULTS_DIR / version_b
    if not dir_a.is_dir() or not dir_b.is_dir():
        abort(404)
    a = {r["package"]: r for r in load_results(dir_a)}
    b = {r["package"]: r for r in load_results(dir_b)}
    both = set(a) & set(b)
    with_tests = [p for p in both if a[p]["has_tests"] and b[p]["has_tests"]]
    regressions = sorted(p for p in with_tests if a[p]["tests_passed"] and not b[p]["tests_passed"])
    fixes = sorted(p for p in with_tests if not a[p]["tests_passed"] and b[p]["tests_passed"])
    new_install_failures = sorted(p for p in both if a[p]["install_ok"] and not b[p]["install_ok"])
    return render_template_string(
        COMPARE_PAGE, name_a=version_a, name_b=version_b,
        regressions=regressions, fixes=fixes, new_install_failures=new_install_failures,
        b_results=b,
    )


def _status_info(r):
    if r["tests_passed"] is True:
        return {"status": "pass", "label": "PASS", "sort_key": "1"}
    elif r["tests_passed"] is False:
        return {"status": "fail", "label": "FAIL", "sort_key": "2"}
    elif r["install_ok"]:
        return {"status": "no-tests", "label": "no tests", "sort_key": "3"}
    else:
        return {"status": "install-fail", "label": "no install", "sort_key": "4"}


@app.route("/grid")
def grid():
    version_dirs = sorted(d for d in RESULTS_DIR.iterdir() if d.is_dir())
    versions = [d.name for d in version_dirs]
    # Load all results keyed by (version, package)
    all_results = {}
    all_packages = set()
    for d in version_dirs:
        for r in load_results(d):
            all_results[(d.name, r["package"])] = r
            all_packages.add(r["package"])

    packages = []
    for name in sorted(all_packages, key=str.lower):
        statuses = {}
        for v in versions:
            r = all_results.get((v, name))
            statuses[v] = _status_info(r) if r else {"status": "", "label": "", "sort_key": "9"}

        # Detect regressions: passed in any earlier version, failed in a later one
        status_list = [statuses[v]["status"] for v in versions]
        is_regression = False
        seen_pass = False
        for s in status_list:
            if s == "pass":
                seen_pass = True
            elif s == "fail" and seen_pass:
                is_regression = True

        has_tests = any(s["status"] in ("pass", "fail") for s in statuses.values())
        any_fail = any(s["status"] == "fail" for s in statuses.values())
        all_pass = has_tests and all(
            s["status"] in ("pass", "") for s in statuses.values()
        )

        filter_parts = []
        if is_regression:
            filter_parts.append("regression")
        if has_tests:
            filter_parts.append("has-tests")
        if all_pass:
            filter_parts.append("all-pass")
        if any_fail:
            filter_parts.append("any-fail")

        packages.append({
            "name": name,
            "statuses": statuses,
            "is_regression": is_regression,
            "filter_status": " ".join(filter_parts),
        })

    return render_template_string(GRID_PAGE, versions=versions, packages=packages)


if __name__ == "__main__":
    app.run(debug=True, port=5555)
