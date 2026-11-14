import pytest

from moviefilter.cli import main, parse_plugin_spec
from moviefilter.errors import MovieFilterError


def test_spec_parse():
    s = parse_plugin_spec("sepia")
    assert (s.name, s.raw_params) == ("sepia", {})
    s = parse_plugin_spec("film-grain:sigma=3,mode=a,label=")
    assert s.name == "film-grain" and s.raw_params == {"sigma": "3", "mode": "a", "label": ""}


@pytest.mark.parametrize("bad", ["", "Sepia", "1abc", "sepia:", "sepia:x", "sepia:=1", "sepia:A=1",
                                 "sepia:a=1,,b=2", "sepia:a=1=2", "sepia:a=1,a=2"])
def test_spec_errors(bad):
    with pytest.raises(MovieFilterError) as e:
        parse_plugin_spec(bad)
    assert e.value.code == "E_PARAM_INVALID" and e.value.exit_code == 2


def test_version_and_help(capsys):
    assert main(["--version"]) == 0
    assert "moviefilter" in capsys.readouterr().out
    assert main(["-h"]) == 0
    assert main(["apply", "-h"]) == 0


@pytest.mark.parametrize("argv,text", [
    ([], "サブコマンド"),
    (["apply"], "必須の引数"),
    (["apply", "in.mp4"], "-p/--plugin"),
    (["apply", "in.mp4", "-p", "sepia", "--quality", "ultra"], "--quality"),
    (["apply", "in.mp4", "-p", "sepia", "--bogus"], "不明な引数"),
    (["bogus"], "command"),
])
def test_usage_errors(argv, text, capsys):
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert "エラー [E_USAGE]" in err and text in err


@pytest.mark.parametrize("seed", ["-1", "4294967296", "abc", "1.5"])
def test_bad_seed(seed, capsys):
    assert main(["apply", "in.mp4", "-p", "sepia", "--seed", seed]) == 2
    assert "エラー [E_" in capsys.readouterr().err


def test_list_info_profiles(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "sepia" in out and "video" in out and "名前" in out
    assert main(["info", "sepia"]) == 0
    out = capsys.readouterr().out
    assert "intensity" in out and "0.0〜1.0" in out
    assert main(["profiles"]) == 0
    assert "mp4-h264" in capsys.readouterr().out
    assert main(["info", "nope"]) == 2
    assert "E_PLUGIN_NOT_FOUND" in capsys.readouterr().err


def test_list_shows_load_failures(tmp_path, monkeypatch, capsys):
    (tmp_path / "broken.py").write_text("def x(:\n", encoding="utf-8")
    monkeypatch.setenv("MOVIEFILTER_PLUGIN_PATH", str(tmp_path))
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "sepia" in out and "ロード失敗" in out and "broken.py" in out
