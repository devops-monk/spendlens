import json
from pathlib import Path

from spendlens.models import Status
from spendlens.powerpipe import (
    connection_text,
    hcl_value,
    iter_json_documents,
    parse_results,
    var_file_text,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "powerpipe_tags.json"
ROOT = Path(__file__).resolve().parents[2]




def test_hcl_encoding():
    assert hcl_value(["a", "b c"]) == '["a", "b c"]'
    assert hcl_value({"Environment": ["prod", "dev%"]}) == '{"Environment" = ["prod", "dev%"]}'
    assert hcl_value(True) == "true" and hcl_value(None) == "null" and hcl_value(0.25) == "0.25"
    assert hcl_value("${evil}") == '"$${evil}"' and hcl_value("%{x}") == '"%%{x}"'
    assert hcl_value('quo"te') == '"quo\\"te"'



def test_var_file_only_declared_vars():
    text = var_file_text({"mandatory_tags": ["A"], "not_in_mod": 1}, {"database", "mandatory_tags"})
    assert text.splitlines() == ["database = connection.steampipe.spendlens", 'mandatory_tags = ["A"]']



def test_connection_text_omits_empty_password():
    t = connection_text({"host": "h", "port": 9193, "db": "steampipe", "username": "u", "password": None})
    assert 'connection "steampipe" "spendlens"' in t and "password" not in t and "port     = 9193" in t



def test_parse_results_fixture():
    findings = parse_results(json.loads(FIXTURE.read_text()))
    assert [f.status for f in findings] == [Status.ALARM, Status.OK, Status.ERROR]
    alarm = findings[0]
    assert alarm.control == "tags_mandatory_s3_bucket" and alarm.check == "tags_mandatory"
    assert alarm.resource_type == "s3_bucket" and alarm.account_id == "111122223333"
    assert alarm.region == "eu-west-1" and alarm.dimensions == {"Environment": "prod"}
    assert alarm.severity.value == "high"
    assert "AccessDenied" in findings[2].reason



def test_concatenated_documents_and_dedupe():
    doc = FIXTURE.read_text()
    docs = list(iter_json_documents(doc + "\n" + doc))
    assert len(docs) == 2
    # the same control under two benchmarks in one document is only counted once
    d = json.loads(doc)
    d["groups"].append(d["groups"][0])
    assert len(parse_results(d)) == 3

