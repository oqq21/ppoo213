from __future__ import annotations

import re
from collections import defaultdict
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

ZERO_COMPONENTS = {
    **{name: [stat] for name, stat in SIMPLE_STATS.items()},
    **TOTAL_COMPONENTS,
    "법신": ["INT", "LUK", "MAD"],
}

ACTUAL_STATS = [
    "STR", "DEX", "INT", "LUK", "HP", "MP", "PAD", "MAD",
    "PDD", "MDD", "ACC", "EVA", "SPEED", "JUMP",
]


def read_packet_rows(path: Path, item_codes: Iterable[int]) -> pd.DataFrame:
    codes = sorted({int(code) for code in item_codes})
    if not path.exists() or not codes:
        return pd.DataFrame()
    try:
        return pd.read_parquet(
            path,
            engine="pyarrow",
            filters=[("itemCode", "in", codes)],
        )
    except Exception:
        frame = pd.read_parquet(path, engine="pyarrow")
        return frame[frame["itemCode"].isin(codes)].copy()


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(0, index=frame.index, dtype="int64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(0).astype("int64")


def _all_additional_zero(frame: pd.DataFrame, components: list[str]) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    for component in components:
        mask &= _numeric(frame, f"add_{component}").eq(0)
    return mask


def filter_packet_rows(frame: pd.DataFrame, tokens: list[str]) -> pd.DataFrame:
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
            components = ZERO_COMPONENTS.get(name)
            if components:
                mask &= _all_additional_zero(work, components)
            continue

        if name == "법신":
            values = sum((_numeric(work, f"add_{stat}") for stat in ["INT", "LUK", "MAD"]), start=pd.Series(0, index=work.index))
            mask &= values.eq(target)
            continue

        if name in ("인", "인트", "마"):
            # 기존 사이트 규칙: 양수 인/마 조건은 인트+마력 총합.
            components = ["INT", "MAD"]
        elif name in TOTAL_COMPONENTS:
            components = TOTAL_COMPONENTS[name]
        else:
            stat = SIMPLE_STATS.get(name)
            components = [stat] if stat else []
        if not components:
            continue

        values = sum(
            (_numeric(work, f"total_{stat}") for stat in components),
            start=pd.Series(0, index=work.index),
        )
        mask &= values.eq(target)

        if name == "신점":
            mask &= _numeric(work, "add_DEX").ge(_numeric(work, "add_ACC"))
        elif name == "신민":
            mask &= _numeric(work, "add_ACC").ge(_numeric(work, "add_DEX"))
        elif name == "법지":
            mask &= _numeric(work, "add_INT").ge(_numeric(work, "add_LUK"))
        elif name == "법행":
            mask &= _numeric(work, "add_LUK").ge(_numeric(work, "add_INT"))

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


def deduplicate_active_completed(
    active: pd.DataFrame,
    completed: pd.DataFrame,
    days: int = 3,
) -> tuple[pd.DataFrame, int]:
    if active.empty or completed.empty:
        return active, 0

    active_work = active.copy().reset_index(drop=True)
    completed_work = completed.copy().reset_index(drop=True)
    active_work["_capture_date"] = pd.to_datetime(active_work["captured_at"], errors="coerce").dt.date
    completed_work["_capture_date"] = pd.to_datetime(completed_work["captured_at"], errors="coerce").dt.date

    active_groups: dict[tuple, list[int]] = defaultdict(list)
    completed_groups: dict[tuple, list[int]] = defaultdict(list)
    for index, row in active_work.iterrows():
        active_groups[_fingerprint(row)].append(index)
    for index, row in completed_work.iterrows():
        completed_groups[_fingerprint(row)].append(index)

    remove: set[int] = set()
    for fingerprint, completed_indexes in completed_groups.items():
        available = sorted(
            active_groups.get(fingerprint, []),
            key=lambda index: active_work.at[index, "_capture_date"],
        )
        if not available:
            continue
        for completed_index in sorted(
            completed_indexes,
            key=lambda index: completed_work.at[index, "_capture_date"],
        ):
            completed_date = completed_work.at[completed_index, "_capture_date"]
            if pd.isna(completed_date):
                continue
            chosen = None
            for position in range(len(available) - 1, -1, -1):
                active_date = active_work.at[available[position], "_capture_date"]
                if pd.isna(active_date):
                    continue
                gap = (completed_date - active_date).days
                if 0 <= gap <= days:
                    chosen = position
                    break
            if chosen is not None:
                remove.add(available.pop(chosen))
            if not available:
                break

    result = active_work.drop(index=list(remove), errors="ignore")
    return result.drop(columns=["_capture_date"], errors="ignore").reset_index(drop=True), len(remove)


def search_packet_data(
    active_path: Path,
    completed_path: Path,
    item_codes: Iterable[int],
    tokens: list[str],
) -> tuple[pd.DataFrame, int]:
    active = filter_packet_rows(read_packet_rows(active_path, item_codes), tokens)
    completed = filter_packet_rows(read_packet_rows(completed_path, item_codes), tokens)
    active, duplicate_count = deduplicate_active_completed(active, completed, days=3)
    combined = pd.concat([active, completed], ignore_index=True) if not active.empty or not completed.empty else pd.DataFrame()
    if not combined.empty:
        combined["_sort_time"] = pd.to_datetime(combined["event_time"], errors="coerce")
        combined = combined.sort_values("_sort_time", ascending=False, kind="mergesort").drop(columns="_sort_time")
    return combined.reset_index(drop=True), duplicate_count


def format_stat_text(row: pd.Series, prefix: str) -> str:
    parts = []
    for stat in ACTUAL_STATS:
        value = int(row.get(f"{prefix}_{stat}", 0) or 0)
        if value:
            parts.append(f"{STAT_LABELS[stat]} {value}")
    return " / ".join(parts)


def packet_view(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    work = frame.copy()
    return pd.DataFrame({
        "상태": work["status"].map({"active": "Active", "completed": "Completed"}).fillna(work["status"]),
        "날짜": pd.to_datetime(work["event_time"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M"),
        "아이템": work["itemName"],
        "판매가": _numeric(work, "unit_price"),
        "보석": work.get("gem_options", ""),
        "보석비(원가)": _numeric(work, "gem_cost"),
        "인정보석가치(90%)": _numeric(work, "recognized_gem_value"),
        "찐판매가": _numeric(work, "true_price"),
        "업횟": _numeric(work, "total_upgrade_left"),
        "작횟": _numeric(work, "total_work_count"),
        "추가스탯": work.apply(lambda row: format_stat_text(row, "add"), axis=1),
        "총스탯": work.apply(lambda row: format_stat_text(row, "total"), axis=1),
    })
