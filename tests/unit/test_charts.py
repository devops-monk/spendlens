import re

from spendlens.render import charts as ch


def test_money_formatting():
    assert ch.money(55409.93) == "$55.4K"
    assert ch.money(4613.24) == "$4,613"
    assert ch.money(47.47) == "$47.47"
    assert ch.money(-3709.7) == "-$3,710"
    assert ch.money(2_400_000, "GBP") == "£2.4M"
    assert ch.money(12, "CHF") == "12.00 CHF"
    assert ch.money(None) == "–"
    assert ch.signed_money(959.07) == "+$959" and ch.pct(1.76, True) == "+1.8%"


def test_bar_path_square_baseline_rounded_end():
    p = ch._bar_path(100, 10, 50, 16)
    assert p.startswith("M100.0,10.0 H146.0") and "A4,4 0 0 1 150.0" in p  # rounded data end at x=150
    left = ch._bar_path(300, 10, 50, 16, leftward=True)
    assert left.startswith("M300.0,10.0 H254.0") and "250.0" in left
    assert "A0,0" in ch._bar_path(10, 0, 0, 16)  # zero-length bar does not explode


def test_hbars_escapes_and_tooltips():
    svg = ch.hbars([{"label": "<b>x</b>", "value": 10}, {"label": "y", "value": 5, "muted": True}], label="t")
    assert "<b>x</b>" not in svg and "&lt;b&gt;x&lt;/b&gt;" in svg
    assert svg.count('tabindex="0"') == 2 and 'data-tip="&lt;b&gt;x&lt;/b&gt;: $10.00"' in svg
    assert "var(--muted-fill)" in svg and 'style="max-width:760px"' in svg
    assert ch.hbars([], label="t") == '<p class="empty">No data.</p>'


def test_diverging_signs_and_colours():
    svg = ch.diverging_hbars([{"label": "a", "value": 100}, {"label": "b", "value": -50}], label="t")
    assert svg.count("var(--div-pos)") == 1 and svg.count("var(--div-neg)") == 1
    assert "+$100" in svg and "-$50.00" in svg


def test_nice_ticks():
    assert ch._nice_ticks(24747) == [0, 10000, 20000, 30000]
    assert ch._nice_ticks(9) == [0, 2.5, 5.0, 7.5, 10.0]
    assert ch._nice_ticks(0) == [0, 1]


def test_lines_direct_labels_only_without_collision():
    apart = [{"name": "EC2", "values": [100, 200], "slot": 1}, {"name": "S3", "values": [10, 20], "slot": 2}]
    close = [{"name": "EC2", "values": [100, 200], "slot": 1}, {"name": "S3", "values": [100, 199], "slot": 2}]
    labelled = ch.lines(apart, ["Jan", "Feb"], label="t")
    assert 'class="lbl"' in labelled and "var(--series-2)" in labelled
    assert 'class="lbl"' not in ch.lines(close, ["Jan", "Feb"], label="t")  # never stack colliding labels
    assert labelled.count('class="hit band"') == 2  # one hover band per x


def test_meter_and_segmented():
    m = ch.meter("Account", 107.5, "critical", "over budget")
    assert "width:100.0%" in m and "108%" in m and "✕" in m  # icon + label, never colour alone
    seg = ch.segmented([{"label": "A", "value": 90, "slot": 1}, {"label": "B", "value": 5, "slot": 2},
                        {"label": "C", "value": 0, "slot": 3}], label="t")
    assert seg.count('class="seg"') == 2  # zero-value parts draw nothing
    assert re.search(r'seg-txt light">95%<', seg) and 'seg-txt light"></span>' in seg  # narrow block prints no label
