from __future__ import annotations
from typing import Dict, Callable, Tuple, List, Iterable, Any
from your_app.domain.stat_semantics import Semantics

# 가공 핸들러는 (raw_token, base_stats, sem) -> ((keys, target_add), post_flag | None) 형태로 반환
ProcResult = Tuple[Tuple[List[str], int] | None, Tuple[str, Any] | None]
Handler = Callable[[str, Dict[str,int], Semantics], ProcResult]

REGISTRY: Dict[str, Handler] = {}

def register(keys: Iterable[str]):
    def deco(fn: Handler):
        for k in keys: REGISTRY[k] = fn
        return fn
    return deco

@register(["힘","덱","인","럭","공","마","명","회피","물방","마방","HP","피","MP","이속","점프"])
def handle_simple(raw: str, base: Dict[str,int], sem: Semantics) -> ProcResult:
    import re
    m = re.fullmatch(r"([가-힣A-Za-z]+)\s*([-+]?\d+)", raw.strip())
    if not m: return (None, None)
    k, v = m.group(1), int(m.group(2))
    # 인/마 단일 입력은 0을 포함해 언제나 (인+마) 합으로 본다.
    if k in ("인","마"):
        keys = ["인","마"]
        return ((keys, v), None)
    if v == 0:
        return (([k], 0), None)
    return (([k], v), None)

@register(["전사","법사","궁수","도적","단도"])
def handle_hap(raw: str, base: Dict[str,int], sem: Semantics) -> ProcResult:
    import re
    m = re.fullmatch(r"(전사|법사|궁수|도적|단도)\s*(\d+)", raw.strip())
    if not m: return (None, None)
    job, v = m.group(1), int(m.group(2))
    comp = {
        "전사": ["힘","덱","명"],
        "도적": ["덱","럭"],
        "궁수": ["힘","덱"],
        "법사": ["인","럭"],
        "단도": ["힘","덱","럭"],
    }[job]
    if v == 0:
        return ((comp, 0), None)
    return ((comp, v), None)

@register(["신점","신민"])
def handle_flags(raw: str, base: Dict[str,int], sem: Semantics) -> ProcResult:
    t = raw.strip()
    if t == "신점": return (None, ("shinjump", None))
    if t == "신민": return (None, ("shinming", None))
    return (None, None)


@register(["신점"])
def handle_shin_point(raw: str, base: Dict[str,int], sem: Semantics):
    import re
    m = re.fullmatch(r"신점\s*(\d+)", raw.strip())
    if not m: return (None,None)
    v = int(m.group(1))
    comp = ["힘","덱","명"]
    if v == 0:
        return ((comp, 0), ("cmp_dex_gte_acc", None))
    return ((comp, v), ("cmp_dex_gte_acc", None))

@register(["신민"])
def handle_shin_min(raw: str, base: Dict[str,int], sem: Semantics):
    import re
    m = re.fullmatch(r"신민\s*(\d+)", raw.strip())
    if not m: return (None,None)
    v = int(m.group(1))
    comp = ["힘","덱","명"]
    if v == 0:
        return ((comp, 0), ("cmp_acc_gte_dex", None))
    return ((comp, v), ("cmp_acc_gte_dex", None))

@register(["법지"])
def handle_beop_ji(raw: str, base: Dict[str,int], sem: Semantics):
    import re
    m = re.fullmatch(r"법지\s*(\d+)", raw.strip())
    if not m: return (None,None)
    v = int(m.group(1))
    comp = ["인","럭"]
    if v == 0:
        return ((comp, 0), ("cmp_int_gte_luk", None))
    return ((comp, v), ("cmp_int_gte_luk", None))

@register(["법행"])
def handle_beop_haeng(raw: str, base: Dict[str,int], sem: Semantics):
    import re
    m = re.fullmatch(r"법행\s*(\d+)", raw.strip())
    if not m: return (None,None)
    v = int(m.group(1))
    comp = ["인","럭"]
    if v == 0:
        return ((comp, 0), ("cmp_luk_gte_int", None))
    return ((comp, v), ("cmp_luk_gte_int", None))

@register(["법신"])
def handle_beop_shin(raw: str, base: Dict[str,int], sem: Semantics):
    import re
    m = re.fullmatch(r"법신\s*(\d+)", raw.strip())
    if not m: return (None,None)
    v = int(m.group(1))
    # 추가스탯 그대로의 합이 v여야 함 (기본값 보정 없이)
    # legacy_processor에서 이 반환을 조건으로 사용하면 자동으로 add_stats 합 == v가 됨
    comp = ["인","럭","마"]
    return ((comp, v), None)
