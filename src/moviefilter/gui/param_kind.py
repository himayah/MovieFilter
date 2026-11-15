"""ParamSpec から入力部品の種別を決める(GUI 設計書 §5、tkinter 非依存)。"""
from __future__ import annotations

from ..plugin_api import ParamSpec, ParamType

SLIDER = "slider"      # 範囲つき小数: スライダー + 数値入力
SPINBOX = "spinbox"    # 範囲つき整数
NUMBER = "number"      # 範囲なしの数値入力
CHECK = "check"
CHOICE = "choice"
TEXT = "text"


def widget_kind(spec: ParamSpec) -> str:
    """未知の型は TEXT にフォールバックし、検証は validate_params に任せる。"""
    ranged = spec.min is not None and spec.max is not None
    if spec.type is ParamType.FLOAT:
        return SLIDER if ranged else NUMBER
    if spec.type is ParamType.INT:
        return SPINBOX if ranged else NUMBER
    if spec.type is ParamType.BOOL:
        return CHECK
    if spec.type is ParamType.CHOICE and spec.choices:
        return CHOICE
    return TEXT


def range_text(spec: ParamSpec) -> str:
    if spec.min is not None and spec.max is not None:
        return f"{spec.min:g}〜{spec.max:g}"
    return ""
