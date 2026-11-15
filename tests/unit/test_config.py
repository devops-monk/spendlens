from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from spendlens.config import Config, load_config, template_text


def test_defaults_without_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPENDLENS_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load_config()
    assert cfg.source is None
    assert cfg.tagging.mandatory == ["Environment", "Service Owner"]



def test_mandatory_tags_deduped():
    # the original steampipe-mod-aws-tags test.spvars listed Environment twice
    cfg = Config.model_validate({"tagging": {"mandatory": ["Environment", "Environment", "Service Owner"]}})
    assert cfg.tagging.mandatory == ["Environment", "Service Owner"]



def test_template_is_valid_config():
    cfg = Config.model_validate(yaml.safe_load(template_text()))
    assert "CostCentre" in cfg.tagging.mandatory
    assert cfg.llm.provider == "bedrock"



def test_powerpipe_vars_mapping():
    cfg = Config.model_validate({"sizing": {"ec2_cpu_low": 10, "rds_cpu_high": 70,
                                            "rds_multi_az_by_env": {"PROD": True, "*": False}}})
    v = cfg.to_powerpipe_vars()
    assert v["ec2_cpu_low"] == 10
    assert v["rds_cpu_high"] == 70
    assert v["rds_multi_az_by_env"] == {"prod": True, "*": False}  # keys lower-cased
    assert v["environment_tag"] == "Environment"
    assert v["mandatory_tags"] == cfg.tagging.mandatory



def test_env_overrides(tmp_path, monkeypatch):
    p = tmp_path / "f.yaml"
    p.write_text("llm: {provider: openai, model: gpt-x}\n")
    monkeypatch.setenv("SPENDLENS__LLM__MODEL", "other-model")
    cfg = load_config(p)
    assert cfg.llm.provider == "openai"
    assert cfg.llm.model == "other-model"
    assert cfg.source == str(p)



def test_missing_explicit_file():
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/finops.yaml"))



def test_invalid_provider_rejected():
    with pytest.raises(ValidationError):
        Config.model_validate({"llm": {"provider": "nope"}})

