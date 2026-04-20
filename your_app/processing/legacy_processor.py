# your_app/processing/legacy_processor.py
from __future__ import annotations
import openpyxl
from your_app.common.io_cache import open_workbook_cached
import re
from datetime import datetime
from collections import defaultdict, OrderedDict
from typing import List, Dict, Tuple, Any, TYPE_CHECKING
from your_app.common import debugger
from your_app.processing import sales_store
from your_app.processing.sales_store import get_sales, MIN_COLUMNS_FOR_PROCESSOR

# Parquet 분기에서 사용 (설치 필요: pip install pandas pyarrow)
try:
    import pandas as pd
except Exception:
    pd = None

if TYPE_CHECKING:
    # 타입 체커 전용(런타임 의존 없음)
    from pandas import DataFrame


# ----------------------------
# 유틸
# ----------------------------
def _as_bool_highlight(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    # 판매중/판매완료도 허용
    if s in {"", "0", "false", "f", "no", "n", "판매완료"}:
        return False
    if s in {"1", "true", "t", "yes", "y", "판매중"}:
        return True
    try:
        return bool(int(s))
    except Exception:
        return False


# 표준 스탯 키 매핑
STAT_KEYS: Dict[str, str] = {
    "힘": "힘", "STR": "힘",
    "덱": "덱", "DEX": "덱",
    "인": "인", "INT": "인",
    "럭": "럭", "LUK": "럭",
    "공": "공", "공격력": "공",
    "마": "마", "마력": "마",
    "피": "피", "HP": "피", "MHP": "피",
    "명": "명", "명중": "명",
    "회": "회", "회피": "회",
    "이속": "이속",
    "점프": "점프",
    "물방": "물방",
    "마방": "마방",
}

# 출력 시 고정 순서 (0 제외)
STAT_ORDER: List[str] = ["힘","덱","인","럭","공","마","피","명","회","이속","점프","물방","마방"]


def _normalize_itemname_for_match(s: str) -> str:
    """
    아이템명 비교용 정규화
      - 소문자 변환
      - 괄호 안 텍스트를 지우지 않고 붙여서 보존(예: 망토(이속) → 망토이속)
      - 성별/공용 토큰 제거
      - 공백·특수부호 제거
    """
    raw = str(s or "").lower()
    # 괄호 안 내용을 추출해 뒤에 붙인다(부가옵션을 일치시키기 위함)
    paren_parts = re.findall(r"\(([^)]*)\)", raw)
    main = re.sub(r"\([^)]*\)", "", raw)

    def _strip_tokens(x: str) -> str:
        x = x.replace("남자","").replace("여자","").replace("공용","").replace("남","").replace("여","")
        return re.sub(r"[^0-9a-zA-Z가-힣]+", "", x)

    base = _strip_tokens(main)
    extra = _strip_tokens("".join(paren_parts))
    return base + extra


def _normalize_itemname_for_match_strict(s: str) -> str:
    """
    아이템명 비교용 정규화 (성별 토큰 보존)
      - 소문자 변환
      - 괄호 안 텍스트를 지우지 않고 붙여서 보존(예: 망토(이속) → 망토이속)
      - 공백·특수부호 제거
    """
    raw = str(s or "").lower()
    paren_parts = re.findall(r"\(([^)]*)\)", raw)
    main = re.sub(r"\([^)]*\)", "", raw)

    def _strip_tokens(x: str) -> str:
        return re.sub(r"[^0-9a-zA-Z가-힣]+", "", x)

    base = _strip_tokens(main)
    extra = _strip_tokens("".join(paren_parts))
    return base + extra


def _has_gender_token(s: str) -> bool:
    s = str(s or "")
    if "(남" in s or "(여" in s:
        return True
    if "남자" in s or "여자" in s:
        return True
    if s.endswith("남") or s.endswith("여"):
        return True
    return False


def _name_matches(row_name: str, selected_items: list) -> bool:
    """
    (참고용) 부분/초성 매칭. 현재 메인 필터는 동등비교를 사용.
    """
    if not selected_items:
        return True
    try:
        from your_app.common.hangul_utils import to_choseong
    except Exception:
        def to_choseong(x): return ""
    rn = _normalize_itemname_for_match(row_name or "")
    rnc = to_choseong(row_name or "")
    for it in selected_items:
        itn = _normalize_itemname_for_match(it)
        if itn and (itn in rn or rn in itn):  # 부분일치
            return True
        itc = to_choseong(it or "")
        if itc and itc in rnc:                # 초성일치
            return True
    return False


def _format_price_man(v) -> str:
    try:
        n = int(float(v))
    except Exception:
        return "-"
    if n == 0:
        return "0만"
    man = int(round(n / 10000.0))
    return f"{man}만"


# (가공 전용) 합산 스탯 정의
COMPOSITE: Dict[str, List[str]] = {
    "전사": ["힘", "덱", "명"],
    "도적": ["덱", "럭"],
    "궁수": ["힘", "덱"],        # 가공 정의: 덱+인 → 힘+덱
    "법사": ["인", "럭", "마"],    # 가공 정의: 인+럭+마
    "단도": ["힘", "덱", "럭"],
}

# 합산 전용 컬럼 매핑
_COMPOSITE_MAP = {
    frozenset(["힘","덱","명"]): "전사_add",
    frozenset(["힘","덱"]): "궁수_add",
    frozenset(["인","럭","마"]): "법사_add",
    frozenset(["덱","럭"]): "도적_add",
    frozenset(["힘","덱","럭"]): "단도_add",
}

# add 컬럼 키
_ADD_KEYS = ["힘","덱","인","럭","공","마","피","명","회","이속","점프","물방","마방"]


def _parse_condition(cond: str, base_stats: Dict[str, int]):
    """
    입력 토큰(예: '전사 20', '단도 10', '피 10', '인 10', '마 10', '신점 5', '신민 5', '법지 8', '법행 8', '법신 5', '업 7') ->
      - 수치 조건: (keys, target_add)   # target_add는 '추가스탯 합' 목표
      - 후처리 플래그: ('cmp_dex_gte_acc' / 'cmp_acc_gte_dex' / 'cmp_int_gte_luk' / 'cmp_luk_gte_int' / 'tuc_eq', value)

    가공 전용 규칙:
      - '인 10' 또는 '마 10' → (인+마) 추가합 = 10 - (기본인+기본마)
      - '법사 n'            → (인+럭+마) 추가합 = n - (기본합)
      - '법신 n'            → (인+럭+마) 추가합 == n   (기본 보정 없이 '추가값 그대로')
    """
    if not cond:
        return None, None
    t = cond.strip()

    m = re.fullmatch(r"(?:업|업횟)\s*(\d+)", t)
    if m:
        return None, ("tuc_eq", int(m.group(1)))

    # 신점/신민/법지/법행/법신
    m = re.fullmatch(r"(신점|신민)\s*(\d+)", t)
    if m:
        n = int(m.group(2))
        comp = ["힘","덱","명"]  # 전사 합
        base_sum = sum(int(base_stats.get(x,0) or 0) for x in comp)
        flag = "cmp_dex_gte_acc" if m.group(1) == "신점" else "cmp_acc_gte_dex"
        return (comp, n - base_sum), (flag, None)

    m = re.fullmatch(r"(법지|법행)\s*(\d+)", t)
    if m:
        n = int(m.group(2))
        comp = ["인","럭","마"]  # 법사 합
        base_sum = sum(int(base_stats.get(x,0) or 0) for x in comp)
        flag = "cmp_int_gte_luk" if m.group(1) == "법지" else "cmp_luk_gte_int"
        return (comp, n - base_sum), (flag, None)

    m = re.fullmatch(r"법신\s*(\d+)", t)
    if m:
        n = int(m.group(1))
        comp = ["인","럭","마"]
        return (comp, n), None  # 법신: 기본 보정 없이 추가합 == n

    # 수치 조건 (단일/합)
    m = re.fullmatch(r"([가-힣A-Za-z]+)\s*([-+]?\d+)", t)
    if not m:
        return None, None
    key = m.group(1)
    try:
        val = int(m.group(2))
    except ValueError:
        return None, None

    def _base_sum(keys: List[str]) -> int:
        return sum(int(base_stats.get(k, 0) or 0) for k in keys)

    std_key = STAT_KEYS.get(key, key)

    # 복합(가공 전용)
    if std_key in COMPOSITE:
        keys = COMPOSITE[std_key]
        return (keys, val - _base_sum(keys)), None

    # 인/마 단일 입력도 (인+마)로 통일
    if std_key in ("인", "마"):
        keys = ["인", "마"]
        return (keys, val - _base_sum(keys)), None

    # 단일 키
    base_val = int(base_stats.get(std_key, 0) or 0)
    return ([std_key], val - base_val), None


def _name_equals_in_allowed_set(row_name: str, allowed_names: set) -> bool:
    """정규화 동등(equal) 비교 전용 이름 필터."""
    if not allowed_names:
        return True
    rn = _normalize_itemname_for_match(row_name or "")
    return rn in allowed_names


def _parse_stats_string(s: str) -> Dict[str, int]:
    """판매_데이터 열(공백 구분) -> {표준키: 추가스탯값}; 누락 키는 dict에 없음"""
    if not s:
        return {}
    tokens = str(s).strip().split()
    stats: Dict[str, int] = defaultdict(int)
    it = iter(tokens)
    for k, v in zip(it, it):
        std_key = STAT_KEYS.get(k, k)
        try:
            stats[std_key] += int(v)
        except Exception:
            pass
    return stats


def _format_stats(stats: Dict[str, int]) -> str:
    """0은 제외하고 STAT_ORDER 순서로 문자열 생성"""
    parts: List[str] = []
    for k in STAT_ORDER:
        v = int(stats.get(k, 0) or 0)
        if v != 0:
            parts.append(f"{k}{v}")
    return " ".join(parts)


def _seller_key(s: str) -> str:
    return "".join(str(s or "").split()).lower()


def _read_seller_comment(row):
    """판매_데이터.xlsx 컬럼 매핑: 4열=판매자(3), 8열=코멘트(7), 9열=highlight(8)"""
    seller  = str(row[3]) if len(row) > 3 and row[3] is not None else ""
    comment = str(row[7]) if len(row) > 7 and row[7] is not None else ""
    highlight = _as_bool_highlight(row[8]) if len(row) > 8 else True
    return seller, comment, highlight


# 안전 정수 캐스팅 (NaN/None → 0)
def _as_int0(x):
    try:
        if x is None:
            return 0
        v = float(x)
        if v != v:  # NaN
            return 0
        return int(v)
    except Exception:
        return 0


# ----------------------------
# 메모리 캐시(프로세스 생존 동안 유지)
# ----------------------------
_SALES_CACHE_READY: bool = False
_SALES_CACHE_DF: "DataFrame | None" = None  # 전처리 완료본(모든 시트 포함)
_SALES_CACHE_VERSION: str = ""              # 간단한 버전식(변경감지 용도)


def _make_cache_version(df: "DataFrame") -> str:
    """
    데이터 변경 감지용 간단 버전 키.
    파일 mtime을 모르니 데이터 기반으로 가볍게 만든다.
    """
    try:
        nrows, ncols = df.shape
        max_date = str(df["날짜(파일명)"].max())
        f_nonnull = int(df["F"].notna().sum()) if "F" in df.columns else -1
        return f"{nrows}:{ncols}:{max_date}:{f_nonnull}"
    except Exception:
        return "unknown"


def _prepare_sales_df(df: "DataFrame") -> "DataFrame":
    """Apply required derived columns/normalization for sales data."""
    if df is None or df.empty:
        return df
    if "price_history_json" not in df.columns:
        df["price_history_json"] = ""
    # URL column
    if "B" in df.columns:
        df["url"] = df["B"].astype(str)
    else:
        df["url"] = ""
    # Name normalization
    try:
        df["_name_norm"] = df["C"].astype(str).map(_normalize_itemname_for_match)
    except Exception:
        df["_name_norm"] = ""
    try:
        df["_name_norm_strict"] = df["C"].astype(str).map(_normalize_itemname_for_match_strict)
    except Exception:
        df["_name_norm_strict"] = ""
    # add columns
    for k in _ADD_KEYS:
        col = f"{k}_add"
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int64")
    # composite columns
    df["전사_add"] = df["힘_add"] + df["덱_add"] + df["명_add"]
    df["궁수_add"] = df["힘_add"] + df["덱_add"]
    df["법사_add"] = df["인_add"] + df["럭_add"] + df["마_add"]
    df["도적_add"] = df["덱_add"] + df["럭_add"]
    df["단도_add"] = df["힘_add"] + df["덱_add"] + df["럭_add"]
    # date and flags
    try:
        date_raw = df["날짜(파일명)"].astype(str)
    except Exception:
        date_raw = pd.Series([""], index=df.index)
    df["date_raw"] = date_raw
    df["date_key"] = date_raw.str[:6]
    df["_dt"] = pd.to_datetime(df["date_key"], format="%y%m%d", errors="coerce")
    df["price_num"] = pd.to_numeric(df.get("D"), errors="coerce")
    df["highlight"] = df.get("I").map(_as_bool_highlight)
    # seller/comment
    df["_seller"] = df.get("E").astype(str)
    df["_comment"] = df.get("A").astype(str)
    df["_seller_norm"] = df["_seller"].astype(str).str.replace(r"\s+", "", regex=True).str.lower()
    return df


def _duckdb_query_sales(selected_sheet: str, conds: List[Tuple[List[str], int]], post_flags: dict) -> "DataFrame":
    """Query sales data via DuckDB with basic filters."""
    def _q(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    path = sales_store.duckdb_path()
    if not path:
        raise RuntimeError("duckdb path is not set")

    avail = sales_store.duckdb_columns() or set(MIN_COLUMNS_FOR_PROCESSOR)
    tuc_target = post_flags.get("tuc_eq")
    tuc_col = None
    if tuc_target is not None:
        if "업횟" in avail:
            tuc_col = "업횟"
        elif "tuc" in avail:
            tuc_col = "tuc"
        else:
            return pd.DataFrame(columns=MIN_COLUMNS_FOR_PROCESSOR)

    def _num_expr(col: str) -> str:
        return _q(col) if col in avail else "0"

    def _default_expr(col: str) -> str:
        if col.endswith("_add") or col in ("D", "F"):
            return "0"
        return "''"

    select_columns = list(MIN_COLUMNS_FOR_PROCESSOR)
    if tuc_col and tuc_col not in select_columns:
        select_columns.append(tuc_col)
    cols = ", ".join(_q(c) if c in avail else f"{_default_expr(c)} AS {_q(c)}" for c in select_columns)
    sql = f"WITH sales AS (SELECT {cols} FROM read_parquet(?)) SELECT * FROM sales WHERE {_q('시트명')} = ?"
    params: List[Any] = [path, selected_sheet]

    for keys, target in conds:
        if len(keys) == 1:
            col = f"{keys[0]}_add"
            sql += f" AND {_num_expr(col)} = ?"
            params.append(target)
        else:
            sum_expr = " + ".join(_num_expr(f"{k}_add") for k in keys)
            sql += f" AND ({sum_expr}) = ?"
            params.append(target)

    if post_flags.get("cmp_dex_gte_acc"):
        sql += f" AND {_num_expr('덱_add')} >= {_num_expr('명_add')}"
    if post_flags.get("cmp_acc_gte_dex"):
        sql += f" AND {_num_expr('명_add')} >= {_num_expr('덱_add')}"
    if post_flags.get("cmp_int_gte_luk"):
        sql += f" AND {_num_expr('마_add')} >= {_num_expr('럭_add')}"
    if post_flags.get("cmp_luk_gte_int"):
        sql += f" AND {_num_expr('럭_add')} >= {_num_expr('마_add')}"
    if tuc_target is not None:
        sql += f" AND TRY_CAST({_q(tuc_col)} AS BIGINT) = ?"
        params.append(int(tuc_target))

    return sales_store.duckdb_query(sql, params)

def _ensure_sales_cache_loaded(sales_file: str):
    """
    프로그램 최초 1회만 Parquet 전체를 로드하고
    합산 전용 컬럼/정규화/이름정규화/날짜파싱까지 끝낸 DataFrame을
    전역 캐시에 보관한다.
    (add 스탯은 parquet에 이미 계산되어 있음)
    """
    global _SALES_CACHE_READY, _SALES_CACHE_DF, _SALES_CACHE_VERSION

    if _SALES_CACHE_READY and _SALES_CACHE_DF is not None:
        return

    if pd is None:
        raise RuntimeError("Parquet 처리를 위해 pandas/pyarrow가 필요합니다. (pip install pandas pyarrow)")

    # 0) 원본 로드 (요약본.parquet은 get_sales() 내부에서 읽는다고 가정)
    df = get_sales()
    if df is None or df.empty:
        _SALES_CACHE_READY = True
        _SALES_CACHE_DF = df
        _SALES_CACHE_VERSION = "empty"
        return
    df = _prepare_sales_df(df)

    # 버전 기록 + 캐시에 고정
    _SALES_CACHE_VERSION = _make_cache_version(df)
    _SALES_CACHE_DF = df
    _SALES_CACHE_READY = True
    try:
        debugger.log("sales_cache_ready", version=_SALES_CACHE_VERSION,
                     rows=int(df.shape[0]), cols=int(df.shape[1]))
    except Exception:
        pass


def _build_add_stats_from_row(srow) -> Dict[str, int]:
    """행에서 *_add 컬럼을 모아 dict로 만든다(누락시 0)."""
    d: Dict[str, int] = {}
    for k in _ADD_KEYS:
        d[k] = _as_int0(srow.get(f"{k}_add"))
    return d


# ----------------------------
# 메인 엔트리
# ----------------------------
def process_items(
    input_conditions: List[str],
    selected_sheet: str,
    selected_items: List[str],
    item_file: str,
    sales_file: str
) -> Dict[int, List[Dict[str, Any]]]:
    """
    return: {4:[...], 5:[...]}
      각 행: {"days_ago","item","price","price_num","stats","seller","seller_norm","comment","highlight"}
    """
    # 0) 캐시 준비(프로그램 최초 1회만 전처리)
    if sales_file.lower().endswith(".parquet"):
        if not sales_store.is_duckdb():
            _ensure_sales_cache_loaded(sales_file)

    # 1) 대표아이템 Base (엑셀에서)
    wb_item = open_workbook_cached(item_file, data_only=True)
    ws_item = wb_item[selected_sheet]
    headers = [c.value for c in ws_item[1]]
    base_stats: Dict[str, int] = {}
    target_row = None
    rep_name = selected_items[0] if selected_items else None

    name_to_row = {}
    for row in ws_item.iter_rows(min_row=2, values_only=True):
        if row and row[1]:
            name_to_row[row[1]] = row
    matched_by = None
    if rep_name and rep_name in name_to_row:
        target_row = name_to_row[rep_name]; matched_by = "rep_name"
    else:
        for nm in (selected_items or []):
            if nm in name_to_row:
                target_row = name_to_row[nm]; matched_by = "selected_items"; break

    if target_row:
        for idx, h in enumerate(headers):
            if h in STAT_ORDER:
                try:
                    base_stats[h] = int(target_row[idx] or 0)
                except Exception:
                    base_stats[h] = 0
        try:
            debugger.log('base_pick', matched_by=matched_by, itemName=target_row[1], base_stats=base_stats)
        except Exception:
            pass

    # 2) 조건 파싱
    conds: List[Tuple[List[str], int]] = []
    post_flags = {
        "cmp_dex_gte_acc": False,
        "cmp_acc_gte_dex": False,
        "cmp_int_gte_luk": False,
        "cmp_luk_gte_int": False,
        "tuc_eq": None,
    }
    debugger.log('parse_start', input_conditions=input_conditions, base_stats=base_stats)
    for cond in input_conditions:
        numc, flag = _parse_condition(cond, base_stats)
        if numc:
            conds.append(numc)
        if flag:
            debugger.log('parsed_condition', cond=cond, condition=numc, flag=flag)
            k, _ = flag
            if k in ("cmp_dex_gte_acc","cmp_acc_gte_dex","cmp_int_gte_luk","cmp_luk_gte_int"):
                post_flags[k] = True
            elif k == "tuc_eq":
                post_flags[k] = int(flag[1])

    # 3) 판매_데이터 분기
    _cnt = {'rows_scanned':0,'pass_numeric':0,'fail_numeric':0,
            'fail_cmp_dex_gte_acc':0,'fail_cmp_acc_gte_dex':0,'fail_cmp_int_gte_luk':0,'fail_cmp_luk_gte_int':0,
            'selected':0}
    try:
        debugger.log('post_flags', post_flags=post_flags)
    except Exception:
        pass

    now = datetime.now()
    rows: List[Dict[str, Any]] = []

    if sales_file.lower().endswith(".parquet"):
        if pd is None:
            raise RuntimeError("Parquet 처리를 위해 pandas/pyarrow가 필요합니다. (pip install pandas pyarrow)")
        if sales_store.is_duckdb():
            df = _duckdb_query_sales(selected_sheet, conds, post_flags)
            if df is None or df.empty:
                return {4: [], 5: []}
            df = _prepare_sales_df(df)
        else:
            if _SALES_CACHE_DF is None or _SALES_CACHE_DF.empty:
                return {4: [], 5: []}
            # 3-1) 시트 필터 (벡터)
            df = _SALES_CACHE_DF
            df = df[df["시트명"] == selected_sheet].copy()

        # 3-2) 이름 필터 (정규화 동등 비교 / 벡터)
        strict_match = any(_has_gender_token(nm) for nm in (selected_items or []))
        try:
            if strict_match:
                allowed_name_set = { _normalize_itemname_for_match_strict(nm) for nm in (selected_items or []) }
                df = df[df["_name_norm_strict"].isin(allowed_name_set)]
            else:
                allowed_name_set = { _normalize_itemname_for_match(nm) for nm in (selected_items or []) }
                df = df[df["_name_norm"].isin(allowed_name_set)]
        except Exception:
            pass

        _cnt['rows_scanned'] = int(df.shape[0])

        if df.empty:
            debugger.log('scan_summary', counts=_cnt)
            return {4: [], 5: []}

        # 3-3) 조건 마스크 (벡터)
        mask = pd.Series(True, index=df.index)

        for keys, target in conds:
            if len(keys) == 1:
                col = f"{keys[0]}_add"
                if col in df.columns:
                    mask &= (df[col] == target)
                else:
                    # 키 누락시 0으로 간주
                    mask &= (0 == target)
            else:
                comp_col = _COMPOSITE_MAP.get(frozenset(keys))
                if comp_col and (comp_col in df.columns):
                    mask &= (df[comp_col] == target)
                else:
                    # per-key 합산
                    add_cols = [f"{k}_add" for k in keys]
                    existing = [c for c in add_cols if c in df.columns]
                    if existing:
                        mask &= (df[existing].sum(axis=1) == target)
                    else:
                        mask &= (0 == target)

        # 3-4) 후처리 비교 플래그 (벡터)
        if post_flags.get("cmp_dex_gte_acc"):
            mask &= (df["덱_add"] >= df["명_add"])
        if post_flags.get("cmp_acc_gte_dex"):
            mask &= (df["명_add"] >= df["덱_add"])
        if post_flags.get("cmp_int_gte_luk"):
            mask &= (df["마_add"] >= df["럭_add"])
        if post_flags.get("cmp_luk_gte_int"):
            mask &= (df["럭_add"] >= df["마_add"])
        if post_flags.get("tuc_eq") is not None:
            tuc_col = "업횟" if "업횟" in df.columns else ("tuc" if "tuc" in df.columns else None)
            if tuc_col is None:
                mask &= False
            else:
                mask &= pd.to_numeric(df[tuc_col], errors="coerce").eq(int(post_flags["tuc_eq"]))

        df = df[mask].copy()

        # 3-5) days_ago/표시 문자열 (벡터)
        # days_ago는 실행 시점 now 기준
        if "_dt" in df.columns:
            days = (now - df["_dt"]).dt.days
            df["days_ago"] = days.fillna(0).astype("int64").astype(str) + "일 전"
        else:
            df["days_ago"] = ""

        # stats 문자열은 최종 소수행만 apply (부담 적음)
        def _row_stats_string(r):
            stats = {k: _as_int0(r.get(f"{k}_add")) for k in STAT_ORDER}
            return _format_stats(stats)
        df["stats"] = df.apply(_row_stats_string, axis=1)

        # 나머지 표시열
        df["item"] = df["C"].astype(str)
        df["url"] = df.get("url", df.get("B", "")).astype(str)
        df["seller"] = df["_seller"].astype(str)
        df["seller_norm"] = df["_seller_norm"].astype(str)
        df["comment"] = df["_comment"].astype(str)

        # 4) 중복/날짜별 선별(벡터, 기존 로직과 동일)
        if "price_history_json" not in df.columns:
            df["price_history_json"] = ""
        work = df[["date_raw","date_key","days_ago","item","url","price_num","stats",
                   "seller","seller_norm","comment","highlight","price_history_json"]].copy()

        # 최신 우선 정렬 후, (price_num, stats, seller_norm) 기준 중복 제거
        work = work.sort_values("date_raw", ascending=False)
        work = work.drop_duplicates(subset=["price_num", "stats", "seller_norm"], keep="first")

        # 날짜별 최대 10개 선택 (highlight False 우선, 그 안에서 price 오름차순, tie는 최신)
        INF = 10**18
        work["__price_sort"] = work["price_num"].fillna(INF)
        work["__highlight_sort"] = work["highlight"].astype(bool)  # False(0) 먼저

        work = work.sort_values(["date_key", "__highlight_sort", "__price_sort", "date_raw"],
                                ascending=[True, True, True, False])

        picked = work.groupby("date_key", sort=False).head(10).copy()

        # 지연 포맷: price 문자열
        picked["price"] = picked["price_num"].apply(_format_price_man)

        # 최종 정렬: 최신 날짜 우선
        picked = picked.sort_values("date_raw", ascending=False)

        # DataFrame → list[dict]
        selected = picked.drop(columns=["__price_sort", "__highlight_sort"]).to_dict("records")
        _cnt['selected'] = len(selected)

        debugger.log('scan_summary', counts=_cnt)
        return {4: selected, 5: []}

    else:
        # Excel(.xlsx) 경로 (기존 로직 유지; 루프)
        wb_sales = open_workbook_cached(sales_file, data_only=True)
        ws_sales = wb_sales[selected_sheet]

        try:
            allowed_name_set = { _normalize_itemname_for_match(nm) for nm in (selected_items or []) }
        except Exception:
            allowed_name_set = set()

        rows: List[Dict[str, Any]] = []
        for row in ws_sales.iter_rows(min_row=2, values_only=True):
            if not row:
                continue
            itemname = row[1]
            if not _name_equals_in_allowed_set(itemname, allowed_name_set):
                continue

            date_raw = str(row[0] or "")
            if len(date_raw) < 6:
                continue
            date6 = date_raw[:6]
            try:
                dt = datetime.strptime(date6, "%y%m%d")
            except Exception:
                continue
            days_ago = (now - dt).days

            price_cell = row[2]
            try:
                price_num = int(float(price_cell))
            except Exception:
                price_num = None
            stats_str = row[4]
            seller, comment, highlight = _read_seller_comment(row)
            add_stats = _parse_stats_string(stats_str)

            # 조건(AND, 정확 일치)
            ok = True
            for keys, target in conds:
                if len(keys) == 1:
                    if int(add_stats.get(keys[0], 0) or 0) != target:
                        ok = False; break
                else:
                    if sum(int(add_stats.get(k, 0) or 0) for k in keys) != target:
                        ok = False; break
            if not ok:
                continue

            # 후처리 비교(추가스탯 기준)
            if post_flags["cmp_dex_gte_acc"] and not (int(add_stats.get("덱",0) or 0) >= int(add_stats.get("명",0) or 0)):
                continue
            if post_flags["cmp_acc_gte_dex"] and not (int(add_stats.get("명",0) or 0) >= int(add_stats.get("덱",0) or 0)):
                continue
            if post_flags["cmp_int_gte_luk"] and not (int(add_stats.get("마",0) or 0) >= int(add_stats.get("럭",0) or 0)):
                continue
            if post_flags["cmp_luk_gte_int"] and not (int(add_stats.get("럭",0) or 0) >= int(add_stats.get("마",0) or 0)):
                continue
            if post_flags.get("tuc_eq") is not None:
                continue

            rows.append({
                "date_raw": date_raw,
                "date_key": date6,
                "days_ago": f"{days_ago}일 전",
                "item": str(itemname),
                "url": "",
                "price": None,             # 표시용(지연 포맷)
                "price_num": price_num,
                "stats": _format_stats(add_stats),
                "seller": seller,
                "seller_norm": _seller_key(seller),
                "comment": comment,
                "highlight": highlight,
                "price_history_json": "",
            })

        # pandas 경로와 동일한 후반 처리
        selected: List[Dict[str, Any]] = []
        if pd is not None and rows:
            df = pd.DataFrame(rows)
            df = df.sort_values("date_raw", ascending=False)
            df = df.drop_duplicates(subset=["price_num", "stats", "seller_norm"], keep="first")

            INF = 10**18
            df["__price_sort"] = df["price_num"].fillna(INF)
            df["__highlight_sort"] = df["highlight"].astype(bool)

            df = df.sort_values(["date_key", "__highlight_sort", "__price_sort", "date_raw"],
                                ascending=[True, True, True, False])

            picked = df.groupby("date_key", sort=False).head(10).copy()
            picked["price"] = picked["price_num"].apply(_format_price_man)
            picked = picked.sort_values("date_raw", ascending=False)

            selected = picked.drop(columns=["__price_sort", "__highlight_sort"]).to_dict("records")
        else:
            # 폴백(순수 파이썬)
            dedup: Dict[Tuple[Any, str, str], Dict[str, Any]] = {}
            for d in rows:
                key = (d["price_num"], d["stats"], d["seller_norm"])
                if key not in dedup or d["date_raw"] > dedup[key]["date_raw"]:
                    dedup[key] = d
            rows2 = list(dedup.values())

            groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for d in rows2:
                groups[d["date_key"]].append(d)

            selected = []
            LIMIT = 10
            for dk, g in groups.items():
                falses = [x for x in g if not _as_bool_highlight(x.get("highlight", False))]
                trues  = [x for x in g if     _as_bool_highlight(x.get("highlight", False))]

                def _price_key(x):
                    pn = x.get("price_num")
                    return (pn if pn is not None else float("inf"), -int(str(x.get("date_raw","")) or "0"))

                falses.sort(key=_price_key)
                trues.sort(key=_price_key)

                picked = []
                picked.extend(falses[:LIMIT])
                if len(picked) < LIMIT:
                    picked.extend(trues[: (LIMIT - len(picked))])
                selected.extend(picked)

            for r in selected:
                r["price"] = _format_price_man(r.get("price_num"))
            selected.sort(key=lambda x: x['date_raw'], reverse=True)

        debugger.log('scan_summary', counts=_cnt)
        slot4 = selected
        slot5 = []
        return {4: slot4, 5: slot5}
