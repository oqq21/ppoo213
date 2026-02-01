from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Tuple

# 단일 진실원천: 스탯/합스탯/비교 규칙
STAT_FIELD_MAP: Dict[str, str] = {
    "힘":"STR","덱":"DEX","인":"INT","럭":"LUK","공":"PAD","마":"MAD","명":"ACC","회피":"EVA",
    "물방":"PDD","마방":"MDD","HP":"MHP","MP":"MMP","이속":"Speed","점프":"Jump",
}
HAP_KEY_MAP: Dict[str, str] = {
    "전사":"STRDEXACC","법사":"INTLUK","궁수":"STRDEX","도적":"DEXLUK","초보자":""
}
COMPARE_RULES: Dict[str, Tuple[str,str,str]] = {
    "전신": ("INT", ">=", "LUK"),
    "전민": ("DEX", ">=", "ACC"),
    "법지": ("INT", ">=", "LUK"),
    "법행": ("INT", ">=", "LUK"),  # 필요시 수정
}

@dataclass(frozen=True)
class Semantics:
    additional_only: bool = True  # parquet 가공은 추가스탯 기준
    use_tuc_only: bool = True     # Tuc만 사용

    def hap_key(self, job: str) -> str:
        return HAP_KEY_MAP.get(job, "")

    def compare_rule(self, name: str) -> Tuple[str,str,str] | None:
        return COMPARE_RULES.get(name)

    def stat_field(self, name: str) -> str | None:
        return STAT_FIELD_MAP.get(name) or STAT_FIELD_MAP.get(name.upper())
