from __future__ import annotations
from typing import Dict, Callable, Iterable
from your_app.domain.stat_semantics import Semantics

ParamBuilder = Callable[[str, int, Semantics], Dict[str, object]]
REGISTRY: Dict[str, ParamBuilder] = {}

def register(keys: Iterable[str]):
    def deco(fn: ParamBuilder):
        for k in keys: REGISTRY[k] = fn
        return fn
    return deco

@register(["힘","덱","인","럭","공","마","명","회피","물방","마방","HP","피","MP","이속","점프"])
def build_simple(name: str, value: int, sem: Semantics) -> Dict[str, object]:
    code = sem.stat_field(name)
    if not code: return {}
    return {f"lowinc{code}": value, f"highinc{code}": value}

@register(["전사","법사","궁수","도적","초보자","단도"])
def build_hap(job: str, value: int, sem: Semantics) -> Dict[str, object]:
    key = sem.hap_key(job) or ""
    return ({"hapStatsName": key, "lowHapStatsValue": value, "highHapStatsValue": value} if key else {})

# 비교류(전신/전민/법지/법행)는 현재 API 파라미터가 없으므로 생략(서버측 미지원)
