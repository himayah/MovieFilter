import numpy as np
import pytest

from conftest import write_plugin
from moviefilter.errors import PluginError
from moviefilter.models import PluginSpec
from moviefilter.plugin_api import ParamSpec, ParamType, VideoPlugin
from moviefilter.plugin_manager import PluginManager, create_plugin, validate_params, builtin_dir

GOOD = '''
import numpy as np
from moviefilter.plugin_api import ParamSpec, ParamType, VideoPlugin

class {cls}(VideoPlugin):
    name = "{name}"
    description = "test"
    version = "1.2.3"
    params = (
        ParamSpec("amount", ParamType.INT, 10, "量", min=0, max=100),
        ParamSpec("ratio", ParamType.FLOAT, 0.5, "割合", min=0.0, max=1.0),
        ParamSpec("flag", ParamType.BOOL, False),
        ParamSpec("label", ParamType.STR, ""),
        ParamSpec("mode", ParamType.CHOICE, "a", choices=("a", "b")),
    )
    def process(self, frame, ctx):
        return frame
'''


def mgr(*dirs):
    return PluginManager([builtin_dir(), *dirs])


def test_builtin_sepia_found():
    infos, issues = mgr().discover_all()
    assert [i.name for i in infos] == ["analog-tv", "glitch", "lens-distortion", "old-film", "sepia"] and not issues


def test_discover_and_resolve(plugin_dir):
    write_plugin(plugin_dir, "film_grain", GOOD.format(cls="G", name="film-grain"))
    m = mgr(plugin_dir)
    infos, issues = m.discover_all()
    assert {i.name for i in infos} == {"analog-tv", "glitch", "lens-distortion", "old-film", "sepia", "film-grain"} and not issues
    assert m.resolve("film-grain").version == "1.2.3"
    assert m.resolve("film-grain").cls.__name__ == "G"


def test_resolve_loads_only_requested_plugin(plugin_dir):
    write_plugin(plugin_dir, "good", GOOD.format(cls="G", name="good"))
    write_plugin(plugin_dir, "bomb", "raise RuntimeError('imported!')")
    m = mgr(plugin_dir)
    assert m.resolve("good").name == "good"          # bomb は import されない
    with pytest.raises(PluginError) as e:
        m.resolve("bomb")
    assert e.value.code == "E_PLUGIN_NOT_FOUND" and "imported!" in e.value.detail


def test_not_found_lists_candidates_without_importing(plugin_dir):
    write_plugin(plugin_dir, "bomb", "raise RuntimeError('imported!')")
    with pytest.raises(PluginError) as e:
        mgr(plugin_dir).resolve("nope")
    assert e.value.code == "E_PLUGIN_NOT_FOUND" and "bomb" in e.value.detail and "sepia" in e.value.detail


def _good(name="stem1", cls="A"):
    return GOOD.format(cls=cls, name=name)


@pytest.mark.parametrize("source,expect", [
    ("def broken(:\n", "構文エラー"),
    ("import no_such_module_xyz\n", "import に失敗"),
    ("import sys\nsys.exit(3)\n", "import に失敗"),
    ("x = 1\n", "プラグインクラスが見つかりません"),
    (_good() + "\n" + _good(cls="B").split("import numpy as np")[1].replace(
        "from moviefilter.plugin_api import ParamSpec, ParamType, VideoPlugin", ""), "プラグインクラスが複数あります"),
    (_good(name="other-name"), "ファイル名と name が一致しません"),
    (_good(name="Bad_Name"), "命名規則"),
    (_good().replace("version", "requires_api = (1, 5)\n    version"), "非互換"),
    (_good().replace("version", "requires_api = (2, 0)\n    version"), "非互換"),
])
def test_load_failures_are_isolated(plugin_dir, source, expect):
    write_plugin(plugin_dir, "stem1", source)
    infos, issues = mgr(plugin_dir).discover_all()
    assert "sepia" in [i.name for i in infos]                 # 他のプラグインは影響を受けない
    assert any(expect in i.reason for i in issues), [i.reason for i in issues]


def test_imported_class_from_other_module_is_ignored(plugin_dir):
    write_plugin(plugin_dir, "helper_user", '''
from moviefilter.builtin_plugins.sepia import SepiaPlugin   # 他モジュールのクラスを import しただけ
x = 1
''')
    _, issues = mgr(plugin_dir).discover_all()
    assert any("見つかりません" in i.reason for i in issues)


def test_abstract_base_class_is_not_a_plugin(plugin_dir):
    write_plugin(plugin_dir, "abstract_one", '''
from moviefilter.plugin_api import VideoPlugin
class Base(VideoPlugin):
    name = "abstract_one"
''')
    _, issues = mgr(plugin_dir).discover_all()
    assert any("見つかりません" in i.reason for i in issues)


def test_package_form_with_relative_import(plugin_dir):
    (plugin_dir / "pkg_effect").mkdir(parents=True)
    (plugin_dir / "pkg_effect" / "helper.py").write_text("VALUE = 7\n", encoding="utf-8")
    write_plugin(plugin_dir, "pkg_effect", '''
from moviefilter.plugin_api import VideoPlugin
from .helper import VALUE

class P(VideoPlugin):
    name = "pkg_effect"
    def process(self, frame, ctx):
        return frame
''', package=True)
    info = mgr(plugin_dir).resolve("pkg_effect")
    assert info.name == "pkg_effect"


def test_search_order_first_wins_and_duplicate_warns(plugin_dir, tmp_path, caplog):
    other = tmp_path / "other"
    write_plugin(plugin_dir, "dup", GOOD.format(cls="First", name="dup"))
    write_plugin(other, "dup", GOOD.format(cls="Second", name="dup"))
    m = mgr(plugin_dir, other)
    assert m.resolve("dup").cls.__name__ == "First"
    with caplog.at_level("WARNING", logger="moviefilter"):
        infos, _ = m.discover_all()
    assert [i.name for i in infos].count("dup") == 1


def test_builtin_wins_over_user_sepia(plugin_dir):
    write_plugin(plugin_dir, "sepia", GOOD.format(cls="Fake", name="sepia"))
    assert mgr(plugin_dir).resolve("sepia").cls.__name__ == "SepiaPlugin"


def test_invalid_paramspec_rejected(plugin_dir):
    write_plugin(plugin_dir, "badspec", '''
from moviefilter.plugin_api import *
class P(VideoPlugin):
    name = "badspec"
    params = (ParamSpec("x", ParamType.INT, 500, min=0, max=10),)
    def process(self, frame, ctx): return frame
''')
    _, issues = mgr(plugin_dir).discover_all()
    assert any("既定値が不正" in i.reason for i in issues)


# ---- パラメータの検証・変換

class P(VideoPlugin):
    name = "p"
    params = (
        ParamSpec("amount", ParamType.INT, 10, min=0, max=100),
        ParamSpec("ratio", ParamType.FLOAT, 0.5, min=0.0, max=1.0),
        ParamSpec("flag", ParamType.BOOL, False),
        ParamSpec("label", ParamType.STR, "x"),
        ParamSpec("mode", ParamType.CHOICE, "a", choices=("a", "b")),
    )

    def process(self, frame, ctx):
        return frame


def test_defaults_filled():
    assert validate_params(P, {}) == {"amount": 10, "ratio": 0.5, "flag": False, "label": "x", "mode": "a"}


def test_string_conversion():
    v = validate_params(P, {"amount": "50", "ratio": "1", "flag": "YES", "label": "", "mode": "b"})
    assert v == {"amount": 50, "ratio": 1.0, "flag": True, "label": "", "mode": "b"}
    assert isinstance(v["ratio"], float)


@pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", "Yes"])
def test_bool_true_spellings(raw):
    assert validate_params(P, {"flag": raw})["flag"] is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "NO"])
def test_bool_false_spellings(raw):
    assert validate_params(P, {"flag": raw})["flag"] is False


@pytest.mark.parametrize("params,code", [
    ({"nope": "1"}, "E_PARAM_UNKNOWN"),
    ({"amount": "1.5"}, "E_PARAM_INVALID"),
    ({"amount": "abc"}, "E_PARAM_INVALID"),
    ({"ratio": "nan"}, "E_PARAM_INVALID"),
    ({"flag": "maybe"}, "E_PARAM_INVALID"),
    ({"mode": "c"}, "E_PARAM_INVALID"),
    ({"mode": "A"}, "E_PARAM_INVALID"),
    ({"amount": "101"}, "E_PARAM_RANGE"),
    ({"amount": "-1"}, "E_PARAM_RANGE"),
    ({"ratio": "1.1"}, "E_PARAM_RANGE"),
])
def test_param_errors(params, code):
    with pytest.raises(PluginError) as e:
        validate_params(P, params)
    assert e.value.code == code


def test_typed_values_accepted_and_bool_is_not_int():
    assert validate_params(P, {"amount": 5, "ratio": 1})["ratio"] == 1.0
    with pytest.raises(PluginError):
        validate_params(P, {"amount": True})


def test_constructor_exception_becomes_plugin_init():
    class Bad(P):
        name = "bad"
        def __init__(self, **kw):
            super().__init__(**kw)
            raise ValueError("low は high より小さくしてください")

    with pytest.raises(PluginError) as e:
        create_plugin(Bad, {})
    assert e.value.code == "E_PLUGIN_INIT" and "low は high" in e.value.detail


def test_unsupported_kinds_rejected(plugin_dir):
    from moviefilter.plugin_api import AudioPlugin, TemporalWindow

    class Aud(AudioPlugin):
        name = "aud"

    class Temporal(P):
        name = "temporal"
        temporal_window = TemporalWindow(1, 1)

    with pytest.raises(PluginError) as e:
        create_plugin(Aud, {})
    assert e.value.code == "E_PLUGIN_KIND_UNSUPPORTED"
    with pytest.raises(PluginError) as e:
        create_plugin(Temporal, {})
    assert e.value.code == "E_TEMPORAL_UNSUPPORTED"


def test_audio_plugin_listed_but_not_applicable(plugin_dir):
    write_plugin(plugin_dir, "reverb", '''
from moviefilter.plugin_api import AudioPlugin
class R(AudioPlugin):
    name = "reverb"
''')
    m = mgr(plugin_dir)
    infos, _ = m.discover_all()
    assert next(i for i in infos if i.name == "reverb").kind.value == "audio"
    with pytest.raises(PluginError) as e:
        m.instantiate(PluginSpec("reverb", {}))
    assert e.value.code == "E_PLUGIN_KIND_UNSUPPORTED"


def test_invalid_name_rejected():
    with pytest.raises(PluginError) as e:
        mgr().resolve("Sepia")
    assert e.value.code == "E_PARAM_INVALID"
