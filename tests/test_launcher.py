"""起動用スクリプト(リポジトリ直下の moviefilter / moviefilter.cmd)のテスト。"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX シェルスクリプト")


def run(script, *args, env=None, cwd=None):
    e = dict(os.environ, MOVIEFILTER_PYTHON=sys.executable, PYTHONIOENCODING="utf-8")
    e.update(env or {})
    return subprocess.run([str(script), *args], capture_output=True, text=True, encoding="utf-8", env=e, cwd=cwd)


@posix_only
def test_launcher_runs_from_any_directory(tmp_path):
    r = run(ROOT / "moviefilter", "--version", cwd=tmp_path)
    assert r.returncode == 0 and "moviefilter" in r.stdout
    r = run(ROOT / "moviefilter", "list", cwd=tmp_path)
    assert r.returncode == 0 and "sepia" in r.stdout
    assert run(ROOT / "moviefilter", "apply", cwd=tmp_path).returncode == 2       # 終了コードがそのまま伝わる


@posix_only
def test_launcher_uses_src_next_to_the_script_and_follows_symlinks(tmp_path):
    """スクリプトの隣の src を使うこと(PYTHONPATH に追加されること)、引数を渡すこと、シンボリックリンクを辿ることを、
    偽のパッケージで確かめる。"""
    app = tmp_path / "app"
    (app / "src" / "moviefilter").mkdir(parents=True)
    (app / "src" / "moviefilter" / "__init__.py").write_text("", encoding="utf-8")
    (app / "src" / "moviefilter" / "__main__.py").write_text(
        "import sys\nprint('FAKE', *sys.argv[1:])\nraise SystemExit(7)\n", encoding="utf-8")
    shutil.copy(ROOT / "moviefilter", app / "moviefilter")
    r = run(app / "moviefilter", "a", "b c", cwd=tmp_path)
    assert (r.returncode, r.stdout.strip()) == (7, "FAKE a b c")
    link_dir = tmp_path / "bin"
    link_dir.mkdir()
    (link_dir / "mf").symlink_to(app / "moviefilter")
    r = run(link_dir / "mf", "x", cwd=tmp_path)
    assert (r.returncode, r.stdout.strip()) == (7, "FAKE x")


@posix_only
def test_launcher_is_executable_and_has_lf_endings():
    p = ROOT / "moviefilter"
    assert os.access(p, os.X_OK)
    data = p.read_bytes()
    assert data.startswith(b"#!/bin/sh\n") and b"\r" not in data


def test_cmd_launcher_is_crlf_ascii_and_points_to_src():
    data = (ROOT / "moviefilter.cmd").read_bytes()
    data.decode("ascii")                       # cmd.exe の文字コードに依存しないよう、ASCII のみ
    text = data.decode("ascii")
    assert text.count("\r\n") == text.count("\n")      # すべて CRLF
    assert "%~dp0src" in text and "-m moviefilter %*" in text and "MOVIEFILTER_PYTHON" in text
    assert "exit /b %ERRORLEVEL%" in text


def test_main_module_guards_python_version_and_numpy():
    src = (ROOT / "src" / "moviefilter" / "__main__.py").read_text(encoding="utf-8")
    assert "sys.version_info < (3, 11)" in src and "import numpy" in src
