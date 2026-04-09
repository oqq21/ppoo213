"""config.py — 공통 설정/상수"""
from __future__ import annotations

DEBUG = True
BASE_URL = "https://api.mapleland.gg/trade"

# API 쿼리 파라미터 순서
ORDER = [
    "itemCode", "itemType", "lowPrice", "highPrice", "lowLevel", "highLevel", "gender", "lowUpgrade", "highUpgrade", "lowTuc", "highTuc", "hapStatsName", "lowHapStatsValue", "highHapStatsValue", "lowincSTR", "highincSTR", "lowincDEX", "highincDEX", "lowincINT", "highincINT", "lowincLUK", "highincLUK", "lowincMAD", "highincMAD", "lowincMHP", "highincMHP", "lowHapma", "highHapma", "lowincACC", "highincACC", "lowincEVA", "highincEVA", "lowincPAD", "highincPAD", "lowincSpeed", "highincSpeed", "lowincJump", "highincJump"]



HEADERS = {
    "accept": "*/*",
    "accept-language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": "https://mapleland.gg",
    "referer": "https://mapleland.gg/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# 시트명 → API itemType 매핑
SHEET_TO_ITEMTYPE = {
    "한손검":"oh_sword","두손검":"th_sword","한손도끼":"oh_axe","두손도끼":"th_axe","한손둔기":"oh_blunt","두손둔기":"th_blunt",
    "창":"spear","폴암":"polearm","활":"bow","석궁":"crossbow","완드":"wand","스태프":"staff","단검":"dagger","아대":"claw",
    "방패":"shield","귀고리":"earrings","망토":"cape","모자":"hat","장갑":"glove","신발":"shoes","전신":"overall","상의":"top","하의":"bottom",
}

def dbg(*args):
    if DEBUG: print("[DBG]", *args, flush=True)


# --- ensure 'job' appears in query order (place before 'gender' when possible) ---
try:
    if "job" not in ORDER:
        if "gender" in ORDER:
            ORDER.insert(ORDER.index("gender"), "job")
        else:
            ORDER.append("job")
except Exception:
    # do not break existing behavior on any error
    pass

