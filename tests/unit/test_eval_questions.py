"""The evaluation questions stay valid: every reference query passes the same checker the assistant's queries do."""

import importlib.util
from pathlib import Path

import pytest

from spendlens.ai.catalog import load
from spendlens.ai.sql_check import check

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval_ai.py"
spec = importlib.util.spec_from_file_location("eval_ai", SCRIPT)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)  # type: ignore[union-attr]
QUESTIONS = ev.load_questions()


def test_questions_are_well_formed() -> None:
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids)) >= 15
    for q in QUESTIONS:
        assert {"id", "area", "question", "reference", "expect"} <= set(q)
        assert q["area"] in ("inventory", "security", "cost", "activity", "network")


@pytest.mark.parametrize("q", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_reference_queries_pass_the_checker(q) -> None:
    res = check(q["reference"], load())
    assert res.ok, [str(p) for p in res.errors]
    for col in q["expect"]:
        assert col in q["reference"], col  # an output column of the reference query


def test_answer_matching() -> None:
    assert ev.contains("There are 1,234 running instances.", 1234)
    assert ev.contains("1234 instances", "1234") and not ev.contains("12345 instances", 1234)
    assert not ev.contains("version 1.2", 2) and ev.contains("**t3.micro** (12)", "t3.micro")
