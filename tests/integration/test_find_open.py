"""`spendlens find`, `spendlens open` and the graph hints in check output (offline, demo data)."""

import json

from typer.testing import CliRunner

from spendlens.cli import app

runner = CliRunner()


def run(*args, code=0):
    r = runner.invoke(app, list(args), env={"COLUMNS": "200"})
    assert r.exit_code == code, r.output
    r.flat = " ".join(r.output.split())
    return r


def test_find_one_resource():
    r = run("find", "vol-0rptdata", "--source", "demo")
    assert "EBS volume" in r.flat and "price-book estimate" in r.flat and "legacy-reporting mounts it" in r.flat
    assert "Cleanup group: Stopped instance legacy-reporting" in r.flat and "Suggested tag: Team=data" in r.flat
    assert "spendlens graph impact vol-0rptdata --source demo" in r.flat


def test_find_by_tag_value_and_json():
    r = run("find", "payments", "--source", "demo")
    assert "resources match 'payments'" in r.flat and "payments-api-1" in r.flat
    r = run("find", "vol-c2037e7f2359f0f5", "--source", "demo", "--json")
    hits = json.loads(r.stdout)
    (key, h), = hits.items()  # the waste finding (ARN) and the graph node (short id) are one resource
    assert key.endswith("vol-c2037e7f2359f0f5") and h["graph"]["type"] == "EBS volume"
    assert {f["control"] for f in h["findings"]} >= {"ebs_volume_unattached"}


def test_find_nothing_and_no_live():
    assert run("find", "no-such-thing-anywhere", "--source", "demo", code=1)
    assert runner.invoke(app, ["find", "x", "--source", "live"]).exit_code == 2


def test_open_builds_pages_without_a_browser(tmp_path):
    for page, marker in (("cleanup", "Cleanup groups"), ("traffic", "Data transfer"), ("review", "Review queue")):
        run("open", page, "--source", "demo", "--no-browser")
        path = tmp_path / "reports" / f"demo-{page}.html"  # next to the (test) history
        assert path.is_file() and marker in path.read_text(), page
    out = tmp_path / "x.html"
    run("open", "dashboard", "--source", "demo", "--no-browser", "-o", str(out))
    assert out.is_file()
    r = run("open", "review", "--no-browser", code=1)  # nothing saved yet
    assert "Could not build the review page" in r.flat
    assert runner.invoke(app, ["open", "--source", "live"]).exit_code == 2


def test_check_output_points_at_cleanup_groups():
    r = run("waste", "--source", "demo")
    assert "belong to 4 cleanup group(s)" in r.flat and "spendlens graph orphans --source demo" in r.flat
    assert "What one connects to: spendlens graph show" in r.flat and "--source demo" in r.flat
    r = run("waste", "--source", "demo", "-f", "json")
    assert "cleanup group" not in r.stdout  # hints only with the table
