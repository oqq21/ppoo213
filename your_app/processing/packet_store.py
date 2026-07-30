from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import pandas as pd


STAT_LABELS = {
    "STR": "힘",
    "DEX": "덱",
    "INT": "인",
    "LUK": "럭",
    "PAD": "공",
    "MAD": "마",
    "ACC": "명",
    "EVA": "회피",
    "SPEED": "이속",
    "JUMP": "점프",
    "PDD": "물방",
    "MDD": "마방",
    "HP": "HP",
    "MP": "MP",
}

SIMPLE_STATS = {
    "힘": "STR",
    "덱": "DEX",
    "인": "INT",
    "인트": "INT",
    "럭": "LUK",
    "공": "PAD",
    "마": "MAD",
    "명": "ACC",
    "명중": "ACC",
    "회": "EVA",
    "회피": "EVA",
    "이속": "SPEED",
    "점프": "JUMP",
    "물방": "PDD",
    "마방": "MDD",
    "피": "HP",
    "HP": "HP",
    "hp": "HP",
    "MP": "MP",
    "mp": "MP",
}

TOTAL_COMPONENTS = {
    "전사": ["STR", "DEX", "ACC"],
    "궁수": ["STR", "DEX"],
    "도적": ["DEX", "LUK"],
    "법사": ["INT", "LUK"],
    "단도": ["STR", "DEX", "LUK"],
    "신점": ["STR", "DEX", "ACC"],
    "신민": ["STR", "DEX", "ACC"],
    "법지": ["INT", "LUK"],
    "법행": ["INT", "LUK"],
}

# 사용자 기준: 양수 '마'와 '인'은 언제나 INT + 마력(MAD) 합계다.
MAGIC_TOTAL_COMPONENTS = ["INT", "MAD"]

ZERO_COMPONENTS = {
    **{name: [stat] for name, stat in SIMPLE_STATS.items()},
    **TOTAL_COMPONENTS,
    "법신": ["INT", "LUK", "MAD"],
}

ACTUAL_STATS = [
    "STR", "DEX", "INT", "LUK", "HP", "MP", "PAD", "MAD",
    "PDD", "MDD", "ACC", "EVA", "SPEED", "JUMP",
]

GEM_OPTION_LABELS = {
    0: ("공", (1, 2, 3)),
    1: ("마", (1, 2, 3)),
    2: ("명", (2, 3, 5)),
    3: ("회", (2, 3, 5)),
    4: ("이속", (2, 3, 5)),
    5: ("점프", (1, 2, 3)),
    6: ("HP", (10, 20, 30)),
    7: ("MP", (10, 20, 30)),
    8: ("힘", (2, 3, 5)),
    9: ("인", (2, 3, 5)),
    10: ("럭", (2, 3, 5)),
    11: ("덱", (1, 3, 5)),
}

ITEM_COLOR_RANGES = (
    (-1, "gray"),
    (5, "white"),
    (22, "blue"),
    (39, "purple"),
    (54, "yellow"),
    (69, "lime"),
)

ITEM_COLOR_STATS = (
    "STR", "DEX", "INT", "LUK", "PAD", "PDD", "MDD",
    "ACC", "EVA", "SPEED", "JUMP",
)


@lru_cache(maxsize=4)
def _read_packet_rows_cached(
    path_text: str,
    file_size: int,
    modified_ns: int,
    codes: tuple[int, ...],
) -> pd.DataFrame:
    # file_size/modified_ns are cache-key inputs. The path may be reused when
    # a local snapshot is replaced, so the file identity must be part of the key.
    del file_size, modified_ns
    path = Path(path_text)
    try:
        return pd.read_parquet(
            path,
            engine="pyarrow",
            filters=[("itemCode", "in", list(codes))],
        )
    except Exception:
        frame = pd.read_parquet(path, engine="pyarrow")
        return frame[frame["itemCode"].isin(codes)].copy()


def read_packet_rows(path: Path, item_codes: Iterable[int]) -> pd.DataFrame:
    codes = tuple(sorted({int(code) for code in item_codes}))
    if not path.exists() or not codes:
        return pd.DataFrame()
    stat = path.stat()
    return _read_packet_rows_cached(
        str(path.resolve()),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        codes,
    )


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(0, index=frame.index, dtype="int64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(0).astype("int64")


def _effective_search_stat(
    frame: pd.DataFrame,
    stat: str,
    include_gems: bool,
) -> pd.Series:
    # 웹 검색창에는 게임에서 보이는 총스탯을 입력한다.
    # 보석 적용 검색을 켠 경우에만 패킷 총스탯에 보석 옵션을 더한다.
    values = _numeric(frame, f"total_{stat}")
    if include_gems:
        values = values + _numeric(frame, f"gem_{stat}")
    return values


def _all_additional_zero(
    frame: pd.DataFrame,
    components: list[str],
    include_gems: bool,
) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    for component in components:
        mask &= _effective_search_stat(frame, component, include_gems).eq(0)
    return mask


def filter_packet_rows(
    frame: pd.DataFrame,
    tokens: list[str],
    include_gems: bool = False,
) -> pd.DataFrame:
    if frame.empty:
        return frame
    work = frame.copy()
    mask = pd.Series(True, index=work.index)

    for raw_token in tokens:
        token = re.sub(r"\s+", "", str(raw_token or ""))
        if not token:
            continue

        upgrade_match = re.fullmatch(r"(?:업|업횟)(\d+)", token)
        if upgrade_match:
            mask &= _numeric(work, "total_upgrade_left").eq(int(upgrade_match.group(1)))
            continue

        match = re.fullmatch(r"([가-힣A-Za-z]+)([-+]?\d+)", token)
        if not match:
            continue
        name, target = match.group(1), int(match.group(2))

        if target == 0:
            if name in ("인", "인트", "마"):
                values = sum(
                    (
                        _effective_search_stat(work, stat, include_gems)
                        for stat in MAGIC_TOTAL_COMPONENTS
                    ),
                    start=pd.Series(0, index=work.index),
                )
                mask &= values.eq(0)
            else:
                components = ZERO_COMPONENTS.get(name)
                if components:
                    mask &= _all_additional_zero(work, components, include_gems)
            continue

        if name == "법신":
            values = sum(
                (
                    _effective_search_stat(work, stat, include_gems)
                    for stat in ["INT", "LUK", "MAD"]
                ),
                start=pd.Series(0, index=work.index),
            )
            mask &= values.eq(target)
            continue

        if name in ("인", "인트", "마"):
            components = MAGIC_TOTAL_COMPONENTS
        elif name in TOTAL_COMPONENTS:
            components = TOTAL_COMPONENTS[name]
        else:
            stat = SIMPLE_STATS.get(name)
            components = [stat] if stat else []
        if not components:
            continue

        # 거래소조회 검색값은 패킷의 총스탯 기준이다. 보석 적용 검색을
        # 켰을 때만 gem_*을 더한 정확한 총스탯과 비교한다.
        values = sum(
            (
                _effective_search_stat(work, stat, include_gems)
                for stat in components
            ),
            start=pd.Series(0, index=work.index),
        )
        mask &= values.eq(target)

        if name == "신점":
            mask &= _effective_search_stat(work, "DEX", include_gems).ge(
                _effective_search_stat(work, "ACC", include_gems)
            )
        elif name == "신민":
            mask &= _effective_search_stat(work, "ACC", include_gems).ge(
                _effective_search_stat(work, "DEX", include_gems)
            )
        elif name == "법지":
            mask &= _effective_search_stat(work, "INT", include_gems).ge(
                _effective_search_stat(work, "LUK", include_gems)
            )
        elif name == "법행":
            mask &= _effective_search_stat(work, "LUK", include_gems).ge(
                _effective_search_stat(work, "INT", include_gems)
            )

    return work[mask].copy()


def _fingerprint(row: pd.Series) -> tuple:
    return (
        int(row.get("itemCode", 0)),
        int(row.get("quantity", 0)),
        int(row.get("total_price", 0)),
        int(row.get("total_upgrade_left", 0)),
        int(row.get("total_work_count", 0)),
        *(int(row.get(f"total_{stat}", 0)) for stat in ACTUAL_STATS),
        str(row.get("option_codes", "")),
    )


def _fingerprint_groups(frame: pd.DataFrame) -> dict[tuple, list[int]]:
    columns = [
        "itemCode",
        "quantity",
        "total_price",
        "total_upgrade_left",
        "total_work_count",
        *(f"total_{stat}" for stat in ACTUAL_STATS),
        "option_codes",
    ]
    normalized = pd.DataFrame(index=frame.index)
    for column in columns:
        if column == "option_codes":
            normalized[column] = frame.get(
                column,
                pd.Series("", index=frame.index),
            ).fillna("").astype(str)
        else:
            normalized[column] = _numeric(frame, column)
    groups: dict[tuple, list[int]] = defaultdict(list)
    for index, values in zip(
        normalized.index,
        normalized.itertuples(index=False, name=None),
    ):
        groups[tuple(values)].append(int(index))
    return groups


def deduplicate_active_completed(
    active: pd.DataFrame,
    completed: pd.DataFrame,
    days: int = 3,
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    completed_result = completed.copy().reset_index(drop=True)
    completed_result["_sale_duration_minutes"] = pd.Series(
        pd.NA,
        index=completed_result.index,
        dtype="Int64",
    )
    if active.empty or completed.empty:
        return active.copy().reset_index(drop=True), completed_result, 0

    active_work = active.copy().reset_index(drop=True)
    completed_work = completed_result
    active_work["_internal_date"] = pd.to_datetime(active_work["internal_time"], errors="coerce").dt.date
    completed_work["_internal_date"] = pd.to_datetime(completed_work["internal_time"], errors="coerce").dt.date
    active_work["_captured_time"] = pd.to_datetime(
        active_work["captured_at"],
        errors="coerce",
    )
    completed_work["_captured_time"] = pd.to_datetime(
        completed_work["captured_at"],
        errors="coerce",
    )

    active_groups = _fingerprint_groups(active_work)
    completed_groups = _fingerprint_groups(completed_work)

    remove: set[int] = set()
    for fingerprint, completed_indexes in completed_groups.items():
        available = sorted(
            active_groups.get(fingerprint, []),
            key=lambda index: (
                pd.isna(active_work.at[index, "_captured_time"]),
                pd.Timestamp.max.value
                if pd.isna(active_work.at[index, "_captured_time"])
                else active_work.at[index, "_captured_time"].value,
            ),
        )
        if not available:
            continue
        for completed_index in sorted(
            completed_indexes,
            key=lambda index: (
                pd.isna(completed_work.at[index, "_captured_time"]),
                pd.Timestamp.max.value
                if pd.isna(completed_work.at[index, "_captured_time"])
                else completed_work.at[index, "_captured_time"].value,
            ),
        ):
            completed_date = completed_work.at[completed_index, "_internal_date"]
            if pd.isna(completed_date):
                continue
            chosen = None
            fallback = None
            completed_captured = completed_work.at[
                completed_index,
                "_captured_time",
            ]
            for position in range(len(available) - 1, -1, -1):
                active_date = active_work.at[available[position], "_internal_date"]
                if pd.isna(active_date):
                    continue
                # active 내부시간은 만료 예정, completed 내부시간은 완료 시각이라
                # 어느 쪽이 앞설지 고정하지 않고 날짜 절대차를 사용한다.
                gap = abs((completed_date - active_date).days)
                if gap <= days:
                    # 유효한 과거 Active가 없을 때의 기존 중복 제거용 후보.
                    # 역순 순회라 마지막에 남는 값이 가장 이른 후보가 된다.
                    fallback = position
                    active_captured = active_work.at[
                        available[position],
                        "_captured_time",
                    ]
                    if (
                        not pd.isna(active_captured)
                        and not pd.isna(completed_captured)
                        and active_captured <= completed_captured
                    ):
                        # captured_at 오름차순의 역순이므로 판매 직전의
                        # 가장 가까운 Active가 처음 선택된다.
                        chosen = position
                        break
            if chosen is None:
                chosen = fallback
            if chosen is not None:
                active_index = available.pop(chosen)
                remove.add(active_index)
                active_captured = active_work.at[active_index, "_captured_time"]
                if (
                    not pd.isna(active_captured)
                    and not pd.isna(completed_captured)
                    and completed_captured >= active_captured
                ):
                    duration_seconds = (
                        completed_captured - active_captured
                    ).total_seconds()
                    completed_work.at[
                        completed_index,
                        "_sale_duration_minutes",
                    ] = int(duration_seconds // 60)
            if not available:
                break

    active_result = active_work.drop(index=list(remove), errors="ignore")
    active_result = active_result.drop(
        columns=["_internal_date", "_captured_time"],
        errors="ignore",
    ).reset_index(drop=True)
    completed_result = completed_work.drop(
        columns=["_internal_date", "_captured_time"],
        errors="ignore",
    ).reset_index(drop=True)
    return active_result, completed_result, len(remove)


def search_packet_data(
    active_path: Path,
    completed_path: Path,
    item_codes: Iterable[int],
    tokens: list[str],
    include_gems: bool = False,
) -> tuple[pd.DataFrame, int]:
    active = filter_packet_rows(
        read_packet_rows(active_path, item_codes), tokens, include_gems
    )
    completed = filter_packet_rows(
        read_packet_rows(completed_path, item_codes), tokens, include_gems
    )
    active, completed, duplicate_count = deduplicate_active_completed(
        active,
        completed,
        days=3,
    )
    combined = pd.concat([active, completed], ignore_index=True) if not active.empty or not completed.empty else pd.DataFrame()
    if not combined.empty:
        # 화면 정렬은 패킷 수신시간, 3일 중복 판정은 위의 internal_time을 사용한다.
        combined["_sort_time"] = pd.to_datetime(combined["captured_at"], errors="coerce")
        combined = combined.sort_values("_sort_time", ascending=False, kind="mergesort").drop(columns="_sort_time")
    return combined.reset_index(drop=True), duplicate_count


def format_stat_text(row: pd.Series, prefix: str, include_gems: bool = False) -> str:
    parts = []
    for stat in ACTUAL_STATS:
        value = int(row.get(f"{prefix}_{stat}", 0) or 0)
        if prefix == "add" and include_gems:
            value += int(row.get(f"gem_{stat}", 0) or 0)
        if value:
            parts.append(f"{STAT_LABELS[stat]}{value}")
    if prefix == "add":
        upgrade_left = int(row.get("total_upgrade_left", 0) or 0)
        if upgrade_left > 0:
            parts.append(f"업횟{upgrade_left}")
    return " ".join(parts)


def format_gem_text(row: pd.Series) -> str:
    parts = []
    for raw_code in re.findall(r"\d+", str(row.get("option_codes", "") or "")):
        code = int(raw_code)
        if 6_000 <= code <= 6_011:
            grade_index, suffix = 0, code - 6_000
        elif 16_000 <= code <= 16_011:
            grade_index, suffix = 1, code - 16_000
        elif 26_000 <= code <= 26_011:
            grade_index, suffix = 2, code - 26_000
        else:
            continue
        stat_name, values = GEM_OPTION_LABELS[suffix]
        parts.append(f"{stat_name}+{values[grade_index]}")
    return ", ".join(parts)


def item_color_score(row: pd.Series) -> int:
    """Additional + gem score. HP/MP count as one point per ten."""
    score = sum(
        int(row.get(f"add_{stat}", 0) or 0)
        + int(row.get(f"gem_{stat}", 0) or 0)
        for stat in ITEM_COLOR_STATS
    )
    score += int(
        (
            int(row.get("add_HP", 0) or 0)
            + int(row.get("gem_HP", 0) or 0)
        ) / 10
    )
    score += int(
        (
            int(row.get("add_MP", 0) or 0)
            + int(row.get("gem_MP", 0) or 0)
        ) / 10
    )
    return score


def item_color_key(score: int) -> str:
    for upper, color in ITEM_COLOR_RANGES:
        if int(score) <= upper:
            return color
    return "red"


def format_packet_time(row: pd.Series) -> str:
    captured = pd.to_datetime(row.get("captured_at"), errors="coerce")
    if pd.isna(captured):
        return ""
    if captured.tzinfo is None:
        captured = captured.tz_localize("Asia/Seoul")
    now = pd.Timestamp.now(tz=captured.tz)
    seconds = max(0, int((now - captured).total_seconds()))
    if seconds < 60:
        return "방금전"
    if seconds < 3600:
        return f"{seconds // 60}분전"
    if seconds < 86_400:
        return f"{seconds // 3600}시간전"
    return f"{seconds // 86_400}일전"


def format_sale_duration(row: pd.Series) -> str:
    duration = row.get("_sale_duration_minutes")
    if str(row.get("status", "")).lower() != "completed" or pd.isna(duration):
        return ""
    minutes = max(0, int(duration))
    if minutes < 60:
        return f"{minutes}분"
    if minutes < 1_440:
        return f"{minutes // 60}시간"
    return f"{minutes // 1_440}일"


def packet_view(
    frame: pd.DataFrame,
    include_gems: bool = False,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    work = frame.copy()

    def _to_man(column: str) -> pd.Series:
        # 가격은 양수 메소 단위이므로 5천 메소 이상을 올리는 일반적인 만원 반올림.
        values = _numeric(work, column).clip(lower=0)
        return ((values + 5_000) // 10_000).astype("int64")

    sale_price_man = _to_man("unit_price")
    recognized_gem_man = _to_man("recognized_gem_value")
    return pd.DataFrame({
        "상태": work["status"].map({
            "active": "🔵 Active",
            "completed": "🟣 Completed",
        }).fillna(work["status"]),
        "패킷시간": work.apply(format_packet_time, axis=1),
        "판매소요": work.apply(format_sale_duration, axis=1),
        "아이템": work["itemName"],
        "_아이템색": work.apply(lambda row: item_color_key(item_color_score(row)), axis=1),
        "추가스탯": work.apply(
            lambda row: format_stat_text(row, "add", include_gems),
            axis=1,
        ),
        "판매가(만)": sale_price_man,
        "보석제외가(만)": sale_price_man - recognized_gem_man,
        # 저장된 옛 등급명 대신 옵션코드로 실제 적용 스탯을 표시한다.
        "보석": work.apply(format_gem_text, axis=1),
        "_보석셀": work.get("gem_cell_style", pd.Series("white", index=work.index)),
        "보석비(원가, 만)": _to_man("gem_cost"),
        "인정보석가치(90%, 만)": recognized_gem_man,
    })
