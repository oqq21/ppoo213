from __future__ import annotations
import re
from typing import List
try:
    import pandas as pd
except Exception:
    pd = None

from your_app.common.hangul_utils import to_choseong, is_choseong_query

_OP_AND = "AND"
_OP_OR  = "OR"
_OPS = {_OP_AND, _OP_OR}


def _normalize_search_text(value: str) -> str:
    """Ignore whitespace and punctuation while preserving text inside brackets."""
    return re.sub(
        r"[^0-9a-z가-힣ㄱ-ㅎㅏ-ㅣ]+",
        "",
        str(value or "").lower(),
    )


def _needs_expression_mode(q: str) -> bool:
    # Parentheses are common in real item names, e.g. 견랑포(여). Parentheses
    # alone therefore stay literal; AND/OR or quotes explicitly enable the
    # expression parser, where parentheses still work for grouping.
    return '"' in q or bool(
        re.search(r"\b(AND|OR)\b", q, flags=re.IGNORECASE)
    )

def _tokenize(q: str) -> List[str]:
    s = q.strip()
    out: List[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch.isspace():
            i += 1; continue
        if ch == '"':
            j = i+1
            buf = []
            while j < n and s[j] != '"':
                buf.append(s[j]); j += 1
            out.append("".join(buf))
            i = j+1 if j < n and s[j:j+1] == '"' else j
            continue
        if ch in "()":
            out.append(ch); i += 1; continue
        j = i
        while j < n and (not s[j].isspace()) and s[j] not in '()"':
            j += 1
        w = s[i:j]
        if re.fullmatch(r"(?i)AND", w):
            out.append(_OP_AND)
        elif re.fullmatch(r"(?i)OR", w):
            out.append(_OP_OR)
        else:
            out.append(w)
        i = j
    return out

def _to_rpn(tokens: List[str]) -> List[str]:
    prec = {_OP_AND: 2, _OP_OR: 1}
    out: List[str] = []
    stack: List[str] = []
    for t in tokens:
        if t == '(':
            stack.append(t)
        elif t == ')':
            while stack and stack[-1] != '(':
                out.append(stack.pop())
            if stack and stack[-1] == '(':
                stack.pop()
        elif t in _OPS:
            while stack and stack[-1] in _OPS and prec[stack[-1]] >= prec[t]:
                out.append(stack.pop())
            stack.append(t)
        else:
            out.append(t)
    while stack:
        out.append(stack.pop())
    return out

def _ensure_cache_columns(df):
    # Use new cache names so data loaded by an older app cannot leave stale
    # space-only cache columns that still contain parentheses or punctuation.
    if "itemName_search" not in df.columns:
        df["itemName_search"] = df["itemName"].map(_normalize_search_text)
    if "itemName_choseong_search" not in df.columns:
        df["itemName_choseong_search"] = df["itemName_search"].map(to_choseong)

def _mask_for_literal(df, token: str):
    raw = str(token or "").strip()
    anchored_start = raw.startswith("@")
    anchored_end = raw.endswith("@")
    if anchored_start:
        raw = raw[1:]
    if anchored_end and raw:
        raw = raw[:-1]
    raw = raw.strip()
    if not raw:
        return pd.Series(False, index=df.index)

    normalized = _normalize_search_text(raw)
    if not normalized:
        return pd.Series(False, index=df.index)
    is_ch = is_choseong_query(normalized)
    cq = to_choseong(normalized) if is_ch else normalized
    col = "itemName_choseong_search" if is_ch else "itemName_search"
    if anchored_start and anchored_end:
        return df[col].eq(cq)
    if anchored_start:
        return df[col].str.startswith(cq, na=False)
    if anchored_end:
        return df[col].str.endswith(cq, na=False)
    return df[col].str.contains(cq, na=False, regex=False)

def mask_for_query(df, q: str):
    if df is None or len(df) == 0:
        import numpy as np
        return pd.Series(False, index=getattr(df, 'index', None))
    _ensure_cache_columns(df)
    s = str(q or '').strip()
    if not s:
        return pd.Series(True, index=df.index)
    if not _needs_expression_mode(s):
        return _mask_for_literal(df, s)
    tokens = _tokenize(s)
    normalized: List[str] = []
    prev_is_literal = False
    for t in tokens:
        is_literal = (t not in _OPS and t not in ('(', ')'))
        if is_literal and prev_is_literal:
            normalized.append(_OP_AND)
        normalized.append(t)
        prev_is_literal = is_literal
    rpn = _to_rpn(normalized)
    stack: List = []
    for t in rpn:
        if t in _OPS:
            b = stack.pop(); a = stack.pop()
            stack.append((a & b) if t == _OP_AND else (a | b))
        else:
            stack.append(_mask_for_literal(df, t))
    return stack[-1] if stack else pd.Series(False, index=df.index)
