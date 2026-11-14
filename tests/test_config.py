import pytest

from moviefilter.config import load_config
from moviefilter.errors import ConfigError


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    c = load_config()
    assert (c.ffmpeg, c.quality, c.profile) == ("ffmpeg", "medium", "mp4-h264") and c.source is None


def test_explicit_file_values(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[tools]\nffmpeg = "/x/ffmpeg"\n[plugins]\nextra_dirs = ["~/p"]\n[defaults]\nquality = "high"\n',
                 encoding="utf-8")
    c = load_config(f)
    assert c.ffmpeg == "/x/ffmpeg" and c.quality == "high" and c.extra_dirs[0].name == "p"


def test_cwd_file_found(tmp_path, monkeypatch):
    (tmp_path / "moviefilter.toml").write_text('[defaults]\nquality = "low"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert load_config().quality == "low"


@pytest.mark.parametrize("text", ['[tools]\nffmpeg = 3\n', '[defaults]\nquality = "ultra"\n',
                                  '[plugins]\nextra_dirs = "notalist"\n', 'broken ['])
def test_invalid_values(tmp_path, text):
    f = tmp_path / "c.toml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(f)
    assert e.value.code == "E_CONFIG_INVALID"


def test_missing_explicit_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_unknown_keys_only_warn(tmp_path, caplog):
    f = tmp_path / "c.toml"
    f.write_text('[defaults]\nfoo = 1\n[mystery]\nx = 1\n', encoding="utf-8")
    with caplog.at_level("WARNING", logger="moviefilter"):
        load_config(f)
    assert "defaults.foo" in caplog.text and "mystery" in caplog.text
