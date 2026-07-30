from pathlib import Path
from wbj.config import load_settings


def test_loads_keys_from_env_file(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FMP_API_KEY=abc123\nFINNHUB_API_KEY=\n")
    s = load_settings(env_file=env)
    assert s.fmp_api_key == "abc123"
    assert s.finnhub_api_key is None  # empty string → None (key absent)


def test_missing_env_file_is_not_fatal(tmp_path: Path):
    s = load_settings(env_file=tmp_path / "nope.env")
    assert s.fmp_api_key is None


def test_settings_never_repr_keys(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FMP_API_KEY=SECRETVALUE\n")
    s = load_settings(env_file=env)
    assert "SECRETVALUE" not in repr(s)
