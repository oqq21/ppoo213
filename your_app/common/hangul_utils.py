"""hangul_utils.py
한글 초성 검색 유틸.
- to_choseong(text): 한글 음절을 초성 문자열로 변환(공백 제거)
- is_choseong_query(q): ㄱ-ㅎ만으로 이루어진 검색어 판정
"""
from __future__ import annotations
__all__ = ["to_choseong", "is_choseong_query"]

_CHOSEONG_LIST = ["ㄱ","ㄲ","ㄴ","ㄷ","ㄸ","ㄹ","ㅁ","ㅂ","ㅃ","ㅅ","ㅆ","ㅇ","ㅈ","ㅉ","ㅊ","ㅋ","ㅌ","ㅍ","ㅎ"]
_HANGUL_BASE, _CHOS_BASE, _HANGUL_LAST = 0xAC00, 588, 0xD7A3  # 21*28

def to_choseong(text: str) -> str:
    out = []
    for ch in str(text).replace(" ", ""):
        code = ord(ch)
        if _HANGUL_BASE <= code <= _HANGUL_LAST:
            idx = code - _HANGUL_BASE
            out.append(_CHOSEONG_LIST[idx // _CHOS_BASE])
        else:
            out.append(ch)
    return "".join(out)

def is_choseong_query(q: str) -> bool:
    q = q.replace(" ", "")
    return len(q) > 0 and all("ㄱ" <= c <= "ㅎ" for c in q)
