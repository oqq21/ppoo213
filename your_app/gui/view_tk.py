# ui_app.py — 더블클릭: profileUrl 열기 / tradeUrl·profileUrl 컬럼 숨김 / tradeStatus 강조 / color 정규화
from __future__ import annotations
import tkinter as tk
from tkinter import ttk
from your_app.common import debugger
from tkinter import messagebox
import tkinter.font as tkfont
import traceback, re
from typing import List, Dict, Any
from datetime import datetime, timezone
import pandas as pd
import webbrowser

# ─────────────────────────────────────────────────────────────
# Base stat columns (from item.xlsx across all sheets)
# ─────────────────────────────────────────────────────────────
BASE_STAT_COLS = ["공","마","힘","덱","인","럭","명","물방","마방","이속","점프","회피"]

def _format_base_stats_from_row(row, cols=BASE_STAT_COLS) -> str:
    parts = []
    for c in cols:
        if c in row.index:
            try:
                v = int(row[c])
            except Exception:
                # try float then int cast
                try:
                    v = int(float(row[c]))
                except Exception:
                    continue
            if v != 0:
                parts.append(f"{c} {v}")
    return "\n".join(parts) if parts else "(모든 기본 스탯 = 0)"



def _as_bool_highlight_gui(v):
    """GUI용 highlight 정규화:
    - True/False: 그대로
    - None/NaN/빈칸: True(판매중)
    - 'false','0','no','n','off','판매완료' : False
    - 'true','1','yes','y','on','판매중'    : True
    - 그 외: True
    """
    try:
        import pandas as _pd
        if v is None or (isinstance(v, float) and v != v):
            return True
        # pandas NA
        try:
            if isinstance(v, _pd._libs.missing.NAType):
                return True
        except Exception:
            pass
    except Exception:
        if v is None:
            return True
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s == "":
        return True
    if s in {"0","false","f","no","n","off","판매완료"}:
        return False
    if s in {"1","true","t","yes","y","on","판매중"}:
        return True
    try:
        return bool(int(s))
    except Exception:
        return True


def _build_item_page_url(sheet: str, item_name: str, params: dict) -> str:
    """
    완드/스태프일 때 item 페이지로 열기.
    - 기본: https://mapleland.gg/item/{itemCode}
    - 필터 포함: 위 URL에 필요한 쿼리스트링을 붙임
    귀고리/기타는 기존 build_url(params) 사용.
    """
    try:
        from your_app.api.client import _find_item_code, SHEET_TO_ITEMTYPE
    except Exception:
        return ""
    itype = SHEET_TO_ITEMTYPE.get(str(sheet).strip(), "")
    if itype not in ("wand","staff"):
        return ""
    code = _find_item_code(str(sheet).strip(), str(item_name).strip()) or params.get("itemCode","")
    if not code:
        return ""
    base = f"https://mapleland.gg/item/{code}"
    # 필터 쿼리 구성
    allow = ["lowPrice","highPrice","lowincPAD","highincPAD","lowincMAD","highincMAD",
             "lowHapma","highHapma","lowUpgrade","highUpgrade","lowTuc","highTuc"]
    q = []
    for k in allow:
        v = params.get(k, "")
        if v is None: v = ""
        q.append(f"{k}={v}")
    return base + "?" + "&".join(q)
from urllib.parse import urlencode
from your_app.common.config import dbg, SHEET_TO_ITEMTYPE  # ← 디버깅용
from your_app.api.client import build_url as _build_url  # ← 안전한 URL 조립용

# === helper: man-unit price formatter ===
def _format_price_man_display(v):
    try:
        n = int(float(v))
    except Exception:
        return "-"
    if n >= 10000:
        base = n // 10000
        rem = n % 10000
        return f"{base}만 {rem:,}"
    return f"{n:,}"

def _format_price_sortkey(v):
    try:
        return int(float(v))
    except Exception:
        return 0

def _man_stat(s: str) -> str:
    s = str(s or "").strip()
    return s

# === color chip ===
COLOR_HEX = {
    "purple": "#a855f7", "yellow": "#f59e0b", "white": "#e5e7eb",
    "blue": "#60a5fa",   "gray": "#9ca3af",
}
COLOR_ALIASES = {
    "보라":"purple","퍼플":"purple","노랑":"yellow","흰":"white","파랑":"blue","회":"gray",
    "purple":"purple","yellow":"yellow","white":"white","blue":"blue","gray":"gray",
}
DEFAULT_HEX = "#d1d5db"
HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")

def normalize_color_key(v: str) -> str:
    s = str(v or "").strip().lower()
    if not s: return "_default"
    if HEX_RE.match(s):
        return s if s.startswith("#") else f"#{s}"
    return COLOR_ALIASES.get(s, s)

def make_color_chip(color_key: str, size: int = 12) -> tk.PhotoImage:
    key = str(color_key or "").lower()
    color = COLOR_HEX.get(key, key if HEX_RE.match(key) else DEFAULT_HEX)
    img = tk.PhotoImage(width=size, height=size)
    for y in range(size):
        img.put("{" + " ".join([color]*size) + "}", to=(0,y))
    return img

# ─────────────────────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────────────────────
from urllib.parse import urlencode, quote


def build_site_search_url(params: Dict[str, Any]) -> str:
    """
    (1) 완성 API 파라미터(dict): itemCode 또는 itemType 기준으로 URL 생성
    (2) 의미 파라미터: build_params()로 변환 후 동일 로직
    - 완드/스태프 등 itemCode가 있으면: https://mapleland.gg/item/{itemCode}?...
    - 그 외 itemType이면: https://mapleland.gg/items/{itemType}?...
    """
    try:
        from your_app.api.client import build_params
        from your_app.common.config import ORDER
        from urllib.parse import urlencode, quote

        # 1) 파라미터 확보
        if isinstance(params, dict) and (params.get("itemType") or params.get("itemCode")):
            p = dict(params)
        else:
            p = build_params(
                params.get("sheet", ""),
                params.get("gender", ""),
                int(params.get("reqLevel", 0) or 0),
                item_name=params.get("item_name", ""),
                stat_tokens=params.get("stat_tokens") or [],
                low_tuc=params.get("low_tuc"),
            )
            if not p:
                return ""

        # 2) itemCode 우선 (완드/스태프)
        item_code = (p.get("itemCode") or "").strip()
        if item_code:
            base = f"https://mapleland.gg/item/{quote(item_code)}"
            allow = ["lowPrice","highPrice","lowincPAD","highincPAD","lowincMAD","highincMAD","lowHapma","highHapma","lowUpgrade","highUpgrade","lowTuc","highTuc"]
            have = set(k for k in p.keys() if p.get(k) not in (None,""))
            q = [(k, p[k]) for k in allow if k in have]
            if "lowPrice" not in have:
                q.append(("lowPrice",""))
            if "highPrice" not in have:
                q.append(("highPrice","9999999999"))
            return base + "?" + urlencode(q, doseq=True)

        # 3) itemType 경로
        item_type = (p.get("itemType") or "").strip()
        if not item_type:
            return ""
        base = f"https://mapleland.gg/items/{quote(item_type)}"

        have = set(p.keys())
        q = []
        for k in ORDER:
            if k == "itemType":
                continue
            v = p.get(k, "")
            if v in (None, ""):
                continue
            q.append((k, v))

        if "lowPrice" not in have:
            q.append(("lowPrice",""))
        if "highPrice" not in have:
            q.append(("highPrice","9999999999"))
        if "lowLevel" in p and "highLevel" not in have:
            q.append(("highLevel", p.get("lowLevel","")))

        return base + "?" + urlencode(q, doseq=True)
    except Exception:
        return ""


def _rows_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    return df.to_dict(orient="records") if isinstance(df, pd.DataFrame) else []

# ─────────────────────────────────────────────────────────────
# App (GUI)
# ─────────────────────────────────────────────────────────────
class App:
    def __init__(self, root, df_items: pd.DataFrame):
        self.root = root
        self.df_items = df_items
        self.df_filtered = df_items.iloc[0:0].copy()
        self.df_groups_view = pd.DataFrame(columns=["대표아이템명","개수"])
        self.df_api_sell = pd.DataFrame(); self.df_api_buy = pd.DataFrame()
        self.current_query = ""
        self.history: List[dict] = []
        self._is_loading_history = False

        self.color_imgs: Dict[str, tk.PhotoImage] = {}
        for k in list(COLOR_HEX.keys()) + ["_default"]:
            self.color_imgs[k] = make_color_chip(k if k!="_default" else "white", size=12)

        root.title("Item Search + Grouped API Fetch")
        root.geometry("1500x800"); root.minsize(1480, 740)

        root.grid_rowconfigure(0, weight=0); root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        # ── 상단 영역: PanedWindow로 좌:우 분할 (초기 4:6, 드래그 가능)
        self.top_pane = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        self.top_pane.grid(row=0, column=0, sticky="nsew")
        self.bottom_frame = ttk.Frame(root, padding=(8, 4, 8, 8)); self.bottom_frame.grid(row=1, column=0, sticky="nsew")

        # 좌/우 컨테이너
        self.left_pane  = ttk.Frame(self.top_pane, padding=(8, 8, 4, 4))
        self.right_pane = ttk.Frame(self.top_pane, padding=(4, 8, 8, 4))
        self.top_pane.add(self.left_pane)
        self.top_pane.add(self.right_pane)

        # 좌측 스택: 1 / 2-1 / 2-2
        self.left_stack = ttk.Frame(self.left_pane)
        self.left_stack.grid(row=0, column=0, sticky="nsew")
        self.left_pane.grid_rowconfigure(0, weight=1); self.left_pane.grid_columnconfigure(0, weight=1)
        self.left_stack.grid_columnconfigure(0, weight=1)
        self.left_stack.grid_rowconfigure(0, weight=0)
        self.left_stack.grid_rowconfigure(1, weight=1)
        self.left_stack.grid_rowconfigure(2, weight=1)

        # Slot 1  ← 검색어 넓게 / 스탯3 아랫줄 / 버튼은 맨 아래줄
        self.slot1 = ttk.LabelFrame(self.left_stack, text="1  (검색어 / 스탯 입력)", padding=6)
        self.slot1.grid(row=0, column=0, sticky="nsew", pady=(0,8))

        # ── 0행: 검색어 (넓게)
        ttk.Label(self.slot1, text="검색어").grid(row=0, column=0, sticky="w")
        self.ent_query = ttk.Entry(self.slot1)
        # 검색어 엔트리를 가로로 넓게 쓰도록 columnspan=7
        self.ent_query.grid(row=0, column=1, columnspan=7, sticky="ew", padx=(6,8))
        self.ent_query.focus()

        # ── 1행: 스탯1, 스탯2, 스탯3  (스탯3을 여기 아랫줄에 배치)
        ttk.Label(self.slot1, text="스탯1").grid(row=1, column=0, sticky="w")
        self.ent_stat1 = ttk.Entry(self.slot1, width=12)
        self.ent_stat1.grid(row=1, column=1, sticky="w", padx=(6,16))

        ttk.Label(self.slot1, text="스탯2").grid(row=1, column=2, sticky="w")
        self.ent_stat2 = ttk.Entry(self.slot1, width=12)
        self.ent_stat2.grid(row=1, column=3, sticky="w", padx=(6,16))

        ttk.Label(self.slot1, text="스탯3").grid(row=1, column=4, sticky="w")
        self.ent_stat3 = ttk.Entry(self.slot1, width=12)
        self.ent_stat3.grid(row=1, column=5, sticky="w", padx=(6,16))

        # ── 2행: 버튼들 (탭키 흐름: 검색어 → 스탯1 → 스탯2 → 스탯3 → [검색] → [검색 페이지 열기])
        btns = ttk.Frame(self.slot1)
        btns.grid(row=2, column=0, columnspan=8, sticky="e", pady=(6,0))
        ttk.Button(btns, text="검색", command=self.on_search).grid(row=0, column=0, padx=(0,6))
        ttk.Button(btns, text="검색 페이지 열기", command=self.on_open_search_page).grid(row=0, column=1)

        
        ttk.Button(btns, text="대표아이템 스탯", command=self.on_show_base_stat).grid(row=0, column=2, padx=(6,0))
# 컬럼 가중치: 검색어 엔트리(컬럼 1)를 확장
        self.slot1.grid_columnconfigure(1, weight=1)
        # 나머지 열은 고정
        for col in (0,2,3,4,5,6,7):
            self.slot1.grid_columnconfigure(col, weight=0)



            # === 단축키 바인딩 ===
            # Ctrl+1: 검색 실행, Ctrl+2: 대표아이템 스탯
            # (애플/맥에서도 Ctrl 기준. 포커스가 앱 내부에 있으면 동작)
            self.root.bind_all("<Control-Key-1>", lambda e: self.on_execute_group())
            self.root.bind_all("<Control-Key-2>", lambda e: self.on_show_base_stat())
        # Slot 2-1 그룹 요약
        self.slot21 = ttk.LabelFrame(self.left_stack, text="2-1  (그룹 요약: 대표아이템명 / 개수)", padding=6)
        self.slot21.grid(row=1, column=0, sticky="nsew", pady=(0,8))
        grp_wrap = ttk.Frame(self.slot21); grp_wrap.grid(row=0, column=0, sticky="nsew")
        self.slot21.grid_rowconfigure(0, weight=1); self.slot21.grid_columnconfigure(0, weight=1)
        self.tree_groups = ttk.Treeview(grp_wrap, columns=("대표아이템명","개수"), show="headings", selectmode="extended", height=6)
        self.tree_groups.heading("대표아이템명", text="대표아이템명"); self.tree_groups.heading("개수", text="개수")
        self.tree_groups.column("대표아이템명", width=120, anchor="w"); self.tree_groups.column("개수", width=60, anchor="center")
        vbar1 = ttk.Scrollbar(grp_wrap, orient="vertical", command=self.tree_groups.yview)
        self.tree_groups.configure(yscrollcommand=vbar1.set)
        self.tree_groups.grid(row=0, column=0, sticky="nsew"); vbar1.grid(row=0, column=1, sticky="ns")
        grp_wrap.grid_rowconfigure(0, weight=1); grp_wrap.grid_columnconfigure(0, weight=1)
        self.tree_groups.bind("<Double-1>", lambda e: self.root.after(50, self.on_execute_group))

        # Slot 2-2 검색 기록
        self.slot22 = ttk.LabelFrame(self.left_stack, text="2-2  (검색 기록)", padding=6)
        self.slot22.grid(row=2, column=0, sticky="nsew")
        hist_wrap = ttk.Frame(self.slot22); hist_wrap.grid(row=0, column=0, sticky="nsew")
        self.slot22.grid_rowconfigure(0, weight=1); self.slot22.grid_columnconfigure(0, weight=1)
        self.hist_tree = ttk.Treeview(hist_wrap, columns=("대표아이템명","스탯1"), show=("tree headings"), selectmode="browse", height=6)
        self.hist_tree.heading("#0", text="#"); self.hist_tree.column("#0", width=28, anchor="center", stretch=False)
        self.hist_tree.heading("대표아이템명", text="대표아이템명"); self.hist_tree.heading("스탯1", text="스탯")  # 라벨 변경
        self.hist_tree.column("대표아이템명", width=260, anchor="w")  # ≈ 7/10
        self.hist_tree.column("스탯1",       width=120, anchor="w")  # ≈ 3/10, 좌측정렬;self.hist_tree.column("스탯1", width=100, anchor="center")
        vbar2 = ttk.Scrollbar(hist_wrap, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=vbar2.set)
        self.hist_tree.grid(row=0, column=0, sticky="nsew"); vbar2.grid(row=0, column=1, sticky="ns")
        hist_wrap.grid_rowconfigure(0, weight=1); hist_wrap.grid_columnconfigure(0, weight=1)
        self.hist_tree.bind("<<TreeviewSelect>>", lambda e: self._on_history_select())
        self.hist_tree.bind("<Double-1>", lambda e: self._on_history_select())
        self.hist_tree.bind("<Return>",    lambda e: self._on_history_select())

        # 우측: 4,5만 크게 배치
        self.right_pane.grid_rowconfigure(0, weight=1); self.right_pane.grid_columnconfigure(0, weight=1)
        self.slot4 = ttk.LabelFrame(self.right_pane, text="4  (가공 결과 — 통합)", padding=6); self.slot4.grid(row=0, column=0, sticky="nsew")
        self.slot5 = None

        # 초기 분할 비율(좌:우 = 4:6)
        def _set_initial_sash():
            try:
                total = self.top_pane.winfo_width() or root.winfo_width() or 1500
                self.top_pane.sashpos(0, int(total * 0.30))  # 좌측 30%
            except Exception:
                pass
        self.root.after(200, _set_initial_sash)


        # 하단: 결과 탭
        self.slot3 = ttk.LabelFrame(self.bottom_frame, text="3  (검색 결과 영역)", padding=6)
        self.slot3.grid(row=0, column=0, sticky="nsew")
        self.bottom_frame.grid_rowconfigure(0, weight=1); self.bottom_frame.grid_columnconfigure(0, weight=1)
        self.result_note = ttk.Notebook(self.slot3); self.result_note.grid(row=0, column=0, sticky="nsew")
        self.slot3.grid_rowconfigure(0, weight=1); self.slot3.grid_columnconfigure(0, weight=1)
        self.tab_sell = ttk.Frame(self.result_note); self.result_note.add(self.tab_sell, text="판매(sell)")
        self.tab_buy  = ttk.Frame(self.result_note); self.result_note.add(self.tab_buy,  text="구매(buy)")

        # 테이블
        self.tree_sell = self._make_tree(self.tab_sell)
        self.tree_buy  = self._make_tree(self.tab_buy)
        # 페이지/정렬 상태
        self.API_PAGE_SIZE = 7
        self.api_state = {
            "sell": {"page": 1, "sort": "time"},
            "buy":  {"page": 1, "sort": "time"},
        }
        self.tree_sell.bind("<Double-1>", lambda e: self._open_profile_from_tree(self.tree_sell, getattr(self, "df_api_sell", pd.DataFrame())))
        self.tree_buy.bind("<Double-1>",  lambda e: self._open_profile_from_tree(self.tree_buy,  getattr(self, "df_api_buy",  pd.DataFrame())))

        # 페이지네이션/정렬 바 (탭별 독립)
        # 판매 탭 컨트롤 바
        self.tab_sell.grid_rowconfigure(1, weight=0)
        sell_bar = ttk.Frame(self.tab_sell); sell_bar.grid(row=1, column=0, sticky="ew", pady=(4,0))
        self.btn_sell_prev = ttk.Button(sell_bar, text="◀", width=3, command=lambda: self._api_page_step("sell", -1))
        self.btn_sell_prev.grid(row=0, column=0, padx=(0,6))
        self.lbl_sell_page = ttk.Label(sell_bar, text="1/1 · 총 0")
        self.lbl_sell_page.grid(row=0, column=1)
        self.btn_sell_next = ttk.Button(sell_bar, text="▶", width=3, command=lambda: self._api_page_step("sell", +1))
        self.btn_sell_next.grid(row=0, column=2, padx=(6,0))
        sell_bar.grid_columnconfigure(10, weight=1)
        ttk.Button(sell_bar, text="시간정렬", command=lambda: self._api_set_sort("sell","time")).grid(row=0, column=11, padx=(6,6))
        ttk.Button(sell_bar, text="가격정렬", command=lambda: self._api_set_sort("sell","price")).grid(row=0, column=12)

        # 구매 탭 컨트롤 바
        self.tab_buy.grid_rowconfigure(1, weight=0)
        buy_bar = ttk.Frame(self.tab_buy); buy_bar.grid(row=1, column=0, sticky="ew", pady=(4,0))
        self.btn_buy_prev = ttk.Button(buy_bar, text="◀", width=3, command=lambda: self._api_page_step("buy", -1))
        self.btn_buy_prev.grid(row=0, column=0, padx=(0,6))
        self.lbl_buy_page = ttk.Label(buy_bar, text="1/1 · 총 0")
        self.lbl_buy_page.grid(row=0, column=1)
        self.btn_buy_next = ttk.Button(buy_bar, text="▶", width=3, command=lambda: self._api_page_step("buy", +1))
        self.btn_buy_next.grid(row=0, column=2, padx=(6,0))
        buy_bar.grid_columnconfigure(10, weight=1)
        ttk.Button(buy_bar, text="시간정렬", command=lambda: self._api_set_sort("buy","time")).grid(row=0, column=11, padx=(6,6))
        ttk.Button(buy_bar, text="가격정렬", command=lambda: self._api_set_sort("buy","price")).grid(row=0, column=12)

        # 이벤트
        self.root.bind("<Return>", lambda e: self.on_search())
        self.root.bind("<Left>",  self._on_key_left)
        self.root.bind("<Right>", self._on_key_right)
        self._refresh_history_view()
        

    # ─────────────────────────────────────────────────────────
    # ③ 검색결과(구매/판매) — 페이지/정렬/키보드
    def _api_reset_state(self):
        self.api_state["sell"] = {"page": 1, "sort": "time"}
        self.api_state["buy"]  = {"page": 1, "sort": "time"}

    def _api_set_sort(self, tab: str, mode: str):
        if tab not in self.api_state: return
        if mode not in ("time","price"): return
        self.api_state[tab]["sort"] = mode
        self.api_state[tab]["page"] = 1
        self._api_refresh(tab)

    def _api_page_step(self, tab: str, delta: int):
        st = self.api_state.get(tab, None)
        if not st: return
        total, pages = self._api_total_pages(tab)
        page = max(1, min(pages if pages>0 else 1, st["page"] + delta))
        if page != st["page"]:
            st["page"] = page
            self._api_refresh(tab)

    def _api_total_pages(self, tab: str):
        base = getattr(self, "df_api_"+tab, pd.DataFrame())
        n = int(len(base)) if hasattr(base, "__len__") else 0
        pages = (n + self.API_PAGE_SIZE - 1)//self.API_PAGE_SIZE if n>0 else 0
        return n, pages

    def _api_refresh(self, tab: str):
        base = getattr(self, "df_api_"+tab, pd.DataFrame())
        if base is None: base = pd.DataFrame()
        d = base.copy().reset_index(drop=True)
        if "_load_order" not in d.columns:
            d["_load_order"] = range(len(d))
        st = self.api_state.get(tab, {"page":1,"sort":"time"})
        if st["sort"] == "price" and "itemPrice" in d.columns:
            d["_price"] = pd.to_numeric(d["itemPrice"], errors="coerce")
            if "updated_at" in d.columns:
                d["_updated"] = pd.to_datetime(d["updated_at"], errors="coerce")
            else:
                d["_updated"] = pd.NaT
            d.sort_values(by=["_price","_updated","_load_order"], ascending=[True,False,True], inplace=True, kind="mergesort")
        else:
            d.sort_values(by=["_load_order"], ascending=True, inplace=True, kind="mergesort")
        total = len(d)
        pages = (total + self.API_PAGE_SIZE - 1)//self.API_PAGE_SIZE if total>0 else 0
        page = max(1, min(pages if pages>0 else 1, st["page"]))
        start = (page-1)*self.API_PAGE_SIZE
        end = start + self.API_PAGE_SIZE
        page_df = d.iloc[start:end].copy()
        view = self._api_to_display_df(page_df, sort_latest=False)
        tree = self.tree_sell if tab=="sell" else self.tree_buy
        self._tree_set_df(tree, view, is_api=True)
        label = self.lbl_sell_page if tab=="sell" else self.lbl_buy_page
        btn_prev = self.btn_sell_prev if tab=="sell" else self.btn_buy_prev
        btn_next = self.btn_sell_next if tab=="sell" else self.btn_buy_next
        label.configure(text=f"{page}/{pages or 0} · 총 {total}")
        if page <= 1 or pages == 0:
            try: btn_prev.state(["disabled"])
            except: pass
        else:
            try: btn_prev.state(["!disabled"])
            except: pass
        if pages == 0 or page >= pages:
            try: btn_next.state(["disabled"])
            except: pass
        else:
            try: btn_next.state(["!disabled"])
            except: pass

    def _on_key_left(self, _evt=None):
        w = self.root.focus_get()
        try:
            if w and self.tree_sell in (w, getattr(w,"master",None), getattr(getattr(w,"master",None),"master",None)):
                return self._api_page_step("sell", -1)
        except Exception: pass
        try:
            if w and self.tree_buy in (w, getattr(w,"master",None), getattr(getattr(w,"master",None),"master",None)):
                return self._api_page_step("buy", -1)
        except Exception: pass
        try:
            if hasattr(self, "slot_proc_tree") and w and self.slot_proc_tree in (w, getattr(w,"master",None), getattr(getattr(w,"master",None),"master",None)):
                return self._proc_page_step(-1)
        except Exception: pass

    def _on_key_right(self, _evt=None):
        w = self.root.focus_get()
        try:
            if w and self.tree_sell in (w, getattr(w,"master",None), getattr(getattr(w,"master",None),"master",None)):
                return self._api_page_step("sell", +1)
        except Exception: pass
        try:
            if w and self.tree_buy in (w, getattr(w,"master",None), getattr(getattr(w,"master",None),"master",None)):
                return self._api_page_step("buy", +1)
        except Exception: pass
        try:
            if hasattr(self, "slot_proc_tree") and w and self.slot_proc_tree in (w, getattr(w,"master",None), getattr(getattr(w,"master",None),"master",None)):
                return self._proc_page_step(+1)
        except Exception: pass
    def _make_tree(self, parent) -> ttk.Treeview:
        frame = ttk.Frame(parent); frame.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        parent.grid_rowconfigure(0, weight=1); parent.grid_columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=(), show=("tree headings"), selectmode="browse")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew"); vsb.grid(row=0, column=1, sticky="ns"); hsb.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1); frame.grid_columnconfigure(0, weight=1)

        # 맨 앞(색상칩) 칸 — 넓혀서 겹침 방지
        tree.column("#0", width=28, stretch=False, anchor="center"); tree.heading("#0", text="")

        # 태그 스타일: tradeStatus 기반
        try:
            base  = tkfont.nametofont("TkDefaultFont")
            bold  = tkfont.Font(self.root, base); bold.configure(weight="bold")
            normal = base
        except Exception:
            bold = None; normal = None
        tree.tag_configure("active",   foreground="#2563eb", font=bold)   # True
        tree.tag_configure("inactive", foreground="#111827", font=normal) # False

        return tree
    def _tree_set_df(self, tree: ttk.Treeview, df: pd.DataFrame, is_api: bool=False, hide_cols: List[str]|None=None):
        self._init_tree_fonts()  # ← 헤더 볼드/행 폰트 적용
        hide_cols = set(hide_cols or [])

        # 초기화
        for iid in tree.get_children():
            tree.delete(iid)

        # DF 보관(행-인덱스 싱크용)
        if df is None or df.empty:
            tree.config(columns=())
            tree._view_df = pd.DataFrame()
            return
        view_df = df.reset_index(drop=True).copy()
        tree._view_df = view_df

        cols = list(view_df.columns)

        # API 표라면 메타/URL 숨김 (값은 DF에 유지)
        if is_api:
            hide_cols.update({"tradeUrl","profileUrl","tradeStatus","_active","_color","color","sheet","gender","reqLevel","offer_raw"})

        # 컬럼 구성
        vis_cols = [c for c in cols if c not in hide_cols]
        tree.config(columns=tuple(vis_cols))
        tree["show"] = "headings"

        # ── 기본 폭(기존 로직) 계산
        base_width = {}
        for c in vis_cols:
            w = 140
            if c in ("itemName", "option", "comment", "판매자"): w = 220
            if c == "itemPrice": w = 90
            if c in ("경신시간", "등록시간"): w = 90
            if c == "상태": w = 70
            if c == "흥정": w = 80
            if c == "server": w = 90
            base_width[c] = w

        # ── 3번(API) 전용: 폭 비율 적용
        if is_api:
            mul = {
                "itemName": 2/5,    # 축소
                "itemPrice": 2/5,   # 축소
                "option": 4/3,      # 확장
                "판매자": 2/5,      # 축소
            }
            for k, m in mul.items():
                if k in base_width:
                    base_width[k] = max(50, int(round(base_width[k] * m)))

        # 헤더/폭/정렬
        for c in vis_cols:
            tree.heading(c, text=c)
            anchor = "e" if c in ("itemPrice",) else "w"
            tree.column(c, width=base_width.get(c, 140), anchor=anchor)

        # ── 태그 스타일 (색/상태)
        try:
            tree.tag_configure("c-purple", background="#f3e9ff")
            tree.tag_configure("c-yellow", background="#fff7da")
            tree.tag_configure("c-blue",   background="#e8f2ff")
            tree.tag_configure("c-gray",   background="#f3f3f3")
            tree.tag_configure("c-white",  background="#ffffff")
            tree.tag_configure("inactive", background="#eeeeee", foreground="#777777")
        except Exception:
            pass

        # 데이터 삽입 (행 순서 = view_df 인덱스)
        for i, row in view_df.iterrows():
            values = [row[c] for c in vis_cols]
            tags = []
            col = str(row.get("_color","white")).lower()
            color_tag = {"purple":"c-purple","yellow":"c-yellow","blue":"c-blue","gray":"c-gray","white":"c-white"}.get(col, "c-white")
            tags.append(color_tag)
            try:
                active = bool(row.get("_active", True))
            except Exception:
                active = True
            if not active:
                tags.append("inactive")
            tree.insert("", "end", text="", values=values, tags=tuple(tags))

        # (3번은 가로 스크롤이 많진 않지만) 안전하게 부착
        self._ensure_hscroll(tree)
        
    def _apply_api_post_filters(self, df: pd.DataFrame, tokens: list[str]) -> pd.DataFrame:
        """API 응답 DataFrame에 토큰 기반 후필터 적용.
        신점: incDEX>=incACC, 신민: incACC>=incDEX,
        법지: incINT>=incLUK, 법행: incLUK>=incINT,
        법신 n: incINT+incLUK+incMAD == n
        """
        if df is None or df.empty:
            return pd.DataFrame()

        d = df.copy()
        import re

        # 검색어 포함 필터: 공백/특수/성별 토큰 제거 + 괄호 안 텍스트도 붙여서 비교
        q = (getattr(self, "current_query", "") or "").strip()
        if q:
            def _norm_name(s: str) -> str:
                raw = str(s or "").lower()
                paren_parts = re.findall(r"\(([^)]*)\)", raw)
                main = re.sub(r"\([^)]*\)", "", raw)
                def _strip(x: str) -> str:
                    x = x.replace("남자","").replace("여자","").replace("공용","").replace("남","").replace("여","")
                    return re.sub(r"[^0-9a-zA-Z가-힣]+", "", x)
                base = _strip(main); extra = _strip("".join(paren_parts))
                return base + extra
            q_norm = _norm_name(q)
            try:
                name_norm = d["itemName"].astype(str).map(_norm_name)
                keep_name = name_norm.str.contains(q_norm, regex=False)
            except Exception:
                keep_name = d["itemName"].astype(str).str.contains(q, regex=False)
            d = d[keep_name]
            if d.empty:
                return pd.DataFrame()
        # === base 조인: (sheet, repName) ↔ (sheet, itemName) ===
        try:
            from your_app.common import debugger as _dbg
            base_df = self.df_items.copy()
            rename_map = {
                '힘':'baseSTR','덱':'baseDEX','인':'baseINT','럭':'baseLUK','명':'baseACC','마':'baseMAD',
                'STR':'baseSTR','DEX':'baseDEX','INT':'baseINT','LUK':'baseLUK','ACC':'baseACC','MAD':'baseMAD'
            }
            for k,v in list(rename_map.items()):
                if k in base_df.columns: base_df = base_df.rename(columns={k:v})
            # 조인 키: 가공과 동일 — sheet + 대표아이템명
            left_on = ['sheet','repName']
            right_on = ['sheet','itemName']
            need_cols = set(right_on + ['baseSTR','baseDEX','baseINT','baseLUK','baseACC','baseMAD'])
            base_mini = base_df[[c for c in need_cols if c in base_df.columns]].drop_duplicates()
            before_n = len(d)
            d = d.merge(base_mini, left_on=left_on, right_on=right_on, how='left')
            # 중복된 itemName 컬럼 정리(merge로 인해 _x/_y 생성 방지)
            try:
                if 'itemName_x' in d.columns or 'itemName_y' in d.columns:
                    if 'itemName_x' in d.columns:
                        d.rename(columns={'itemName_x':'itemName'}, inplace=True)
                    if 'itemName_y' in d.columns:
                        d.drop(columns=['itemName_y'], errors='ignore', inplace=True)
            except Exception:
                pass
            # 결측 보정
            for c in ['baseSTR','baseDEX','baseINT','baseLUK','baseACC','baseMAD']:
                if c not in d.columns: d[c] = 0
                d[c] = pd.to_numeric(d[c], errors='coerce').fillna(0).astype(int)
            # 로그 샘플
            try:
                sample = d[['repName','sheet','itemName','incDEX','incACC','incINT','incLUK','incMAD','baseDEX','baseACC','baseINT','baseLUK','baseMAD']].head(5).to_dict(orient='records')
                miss = int(d['baseINT'].isna().sum()) if 'baseINT' in d.columns else None
                _dbg.log('post_filter_base_join', before=before_n, after=len(d), join_keys=left_on, missing_base=miss, sample=sample)
            except Exception:
                pass
        except Exception as _e:
            from your_app.common import debugger as _dbg
            _dbg.log('base_join_error', err=str(_e))
            for c in ['baseSTR','baseDEX','baseINT','baseLUK','baseACC','baseMAD']:
                if c not in d.columns: d[c] = 0
                d[c] = pd.to_numeric(d[c], errors='coerce').fillna(0).astype(int)
        except Exception as _e:
            from your_app.common import debugger as _dbg
            _dbg.log('base_join_error', err=str(_e))
            # 실패해도 진행은 하되 add*=inc*로 이어짐
            for c in ['baseSTR','baseDEX','baseINT','baseLUK','baseACC','baseMAD']:
                if c not in d.columns: d[c] = 0

        # 비교에 필요한 옵션 컬럼 보정(없으면 0)
        for col in ("incDEX", "incACC", "incINT", "incLUK", "incMAD"):
            if col not in d.columns:
                d[col] = 0
            d[col] = pd.to_numeric(d[col], errors="coerce").fillna(0).astype(int)
        # add* 계산
        d['addDEX'] = d['incDEX'] - d['baseDEX']
        d['addACC'] = d['incACC'] - d['baseACC']
        d['addINT'] = d['incINT'] - d['baseINT']
        d['addLUK'] = d['incLUK'] - d['baseLUK']
        d['addMAD'] = d['incMAD'] - d['baseMAD']

        keep = pd.Series(True, index=d.index)
        from your_app.api.client import option_has_any_component, parse_zero_option_token
        for t in (tokens or []):
            t = str(t or "").strip()

            zero_components = parse_zero_option_token(t)
            if zero_components:
                opt_col = "optionSummarize" if "optionSummarize" in d.columns else "option"
                if opt_col in d.columns:
                    keep &= ~d[opt_col].map(lambda x: option_has_any_component(x, zero_components))
                continue

            m = re.fullmatch(r"신점\s*(\d+)", t)
            if m:
                keep &= (d["addDEX"] >= d["addACC"])
                continue

            m = re.fullmatch(r"신민\s*(\d+)", t)
            if m:
                keep &= (d["addACC"] >= d["addDEX"])
                continue

            m = re.fullmatch(r"법지\s*(\d+)", t)
            if m:
                keep &= (d["addINT"] >= d["addLUK"])
                continue

            m = re.fullmatch(r"법행\s*(\d+)", t)
            if m:
                keep &= (d["addLUK"] >= d["addINT"])
                continue

            m = re.fullmatch(r"법신\s*(\d+)", t)
            if m:
                v = int(m.group(1))
                keep &= ((d["addINT"] + d["addLUK"] + d["addMAD"]) == v)
                continue

        return d[keep].reset_index(drop=True)



    def _open_profile_from_tree(self, tree, df=None):
        """
        3번 표(판매/구매) 더블클릭 시: 해당 행의 profileUrl만 연다.
        (폴백으로 검색 URL 재조립하지 않음)
        """
        from tkinter import messagebox as _mb
        import webbrowser

        # (핵심) 트리에 저장된 뷰 DF를 사용
        vdf = getattr(tree, "_view_df", None)
        if vdf is None or not isinstance(vdf, pd.DataFrame) or vdf.empty:
            _mb.showinfo("안내", "데이터가 없습니다.")
            return

        sel = tree.selection()
        if not sel:
            return
        iid = sel[0]
        try:
            idx = tree.index(iid)  # Tree의 현재 보이는 순서 = vdf 인덱스
            rec = vdf.iloc[idx]
        except Exception:
            _mb.showinfo("안내", "행 데이터를 찾을 수 없습니다.")
            return

        url = str(rec.get("profileUrl") or "").strip()
        if url.startswith("http"):
            webbrowser.open_new_tab(url)
        else:
            _mb.showinfo("안내", "프로필 URL이 없습니다.")



    # ── 검색
    def _selected_rows_to_df(self, tree: ttk.Treeview, columns: List[str]) -> pd.DataFrame:
        sel = tree.selection()
        if not sel: return pd.DataFrame(columns=columns)
        rows = []
        for iid in sel:
            idx = tree.index(iid)
            if idx < 0 or idx >= len(getattr(self, "df_groups_view", pd.DataFrame())):
                continue
            rows.append(getattr(self, "df_groups_view", pd.DataFrame()).iloc[idx])
        return pd.DataFrame(rows)[columns] if rows else pd.DataFrame(columns=columns)

    def _read_stat_tokens(self) -> List[str]:
        return [s.strip() for s in [self.ent_stat1.get(), self.ent_stat2.get(), self.ent_stat3.get()] if s and s.strip()]

    def on_search(self):
        try:
            q = (self.ent_query.get() or "").strip()
            self.current_query = q
            df = self.df_items.copy()
            if q:
                # 다중 토큰(AND/OR/괄호/따옴표) + 초성/공백무시 벡터화 검색
                from your_app.common.query_utils import mask_for_query
                m = mask_for_query(df, q)
                df = df[m]
            self.df_filtered = df.reset_index(drop=True)

            from your_app.domain.grouping import group_by_sgr
            g = group_by_sgr(self.df_filtered)
            self.df_groups_full = g  # 🔹 선택 조회/자동 조회에서 sheet/gender/reqLevel 참조용
            self.df_groups_view = g[["대표아이템명","개수"]].copy()
            self._tree_set_df(self.tree_groups, g[["대표아이템명","개수"]], is_api=False)

            # 1개면 자동 조회
            if len(g) == 1:
                self.fetch_groups(g[["대표아이템명","sheet","gender","reqLevel"]])

        except Exception:
            print("[ERR] on_search crashed:\n"+traceback.format_exc(), flush=True)


    def on_show_base_stat(self):
        """Slot 1: [대표아이템 스탯]
        - 검색어로 '대표아이템 1개'만 남도록 기존 로직 그대로 필터/그룹
        - 가공/API 없이 엑셀 base stat만 추출해 팝업 표시
        """
        q = (self.ent_query.get() or "").strip()
        if not q:
            messagebox.showerror("오류", "검색어에 대표아이템명을 입력하세요.")
            return
        # 기존 검색 파이프라인과 동일: mask_for_query -> df_filtered
        df = self.df_items.copy()
        try:
            from your_app.common.query_utils import mask_for_query
            m = mask_for_query(df, q)
        except Exception:
            # 검색 파서 불가 시, itemName 부분 포함으로 폴백
            m = df["itemName"].astype(str).str.contains(q, regex=False)
        df_filtered = df[m]
        if df_filtered.empty:
            messagebox.showerror("오류", f"대표아이템을 찾지 못했습니다: {q}")
            return
        # 대표아이템 그룹핑
        from your_app.domain.grouping import group_by_sgr
        g = group_by_sgr(df_filtered)
        if g is None or g.empty:
            messagebox.showerror("오류", f"대표아이템을 찾지 못했습니다: {q}")
            return
        if len(g) != 1:
            # 후보명 몇 개를 보여줌 (최대 5개)
            name_col = "대표아이템명" if "대표아이템명" in g.columns else ("itemName" if "itemName" in g.columns else None)
            cand = ", ".join(map(str, g[name_col].head(5).tolist())) if name_col else ""
            messagebox.showerror("오류", f"여러 개가 일치합니다: {cand} (그룹에서 하나 선택)")
            return
        row_g = g.iloc[0]
        # 그룹 기준으로 정확 행 찾기: itemName + sheet/gender/reqLevel 일치
        name_col = "대표아이템명" if "대표아이템명" in g.columns else ("itemName" if "itemName" in g.columns else None)
        item_name = str(row_g[name_col]) if name_col else None
        cond = df_filtered["itemName"].astype(str).eq(item_name)
        for col in ("sheet","gender","reqLevel"):
            if col in df_filtered.columns and col in g.columns:
                cond &= (df_filtered[col] == row_g[col])
        df_one = df_filtered[cond]
        if df_one.empty:
            # 보수적으로 첫 행
            df_one = df_filtered.iloc[[0]]
        row = df_one.iloc[0]
        text_stat = _format_base_stats_from_row(row, BASE_STAT_COLS)
        title = f"{item_name}" + (f" ({row_g['sheet']})" if 'sheet' in g.columns else "")
        self._show_or_replace_base_stat_popup(text_stat, title)

    def _show_or_replace_base_stat_popup(self, text: str, title: str = "대표아이템 스탯"):
        """대표아이템 스탯 팝업
        - 글꼴을 2배로 키움
        - 닫기 버튼 제거
        - 팝업이 떠 있는 동안 **아무 키보드 입력** 또는 **마우스 좌/우 클릭**이 발생하면 즉시 닫힘
          (grab_set으로 포커스/이벤트를 팝업으로 모아, 앱 어디를 눌러도 닫히는 효과)"""
        # 기존 팝업 정리 (1개만 유지)
        try:
            if hasattr(self, "_base_stat_popup") and self._base_stat_popup and self._base_stat_popup.winfo_exists():
                self._base_stat_popup.destroy()
        except Exception:
            pass
    
        import tkinter.font as tkfont
        win = tk.Toplevel(self.root)
        win.title(title)
    
        # 글꼴 2배 확대
        try:
            base = tkfont.nametofont("TkDefaultFont")
            big  = tkfont.Font(self.root, base)
            size = int(base.cget("size"))
            big.configure(size=max(1, size * 2))
        except Exception:
            big = tkfont.Font(self.root, family="TkDefaultFont", size=20)
    
        # 내용 라벨
        lbl = tk.Label(win, text=text, justify="left", anchor="w", font=big)
        lbl.pack(padx=16, pady=16)
    
        # 레이아웃 계산 후 위치 고정, 크기는 내용에 맞게 자동
        win.update_idletasks()
        win.geometry("+10+10")
        win.resizable(False, False)
    
        # === 닫기 동작: 키/마우스 이벤트로 즉시 종료 ===
        def _close(_=None):
            try:
                win.destroy()
            except Exception:
                pass
    
        # 모든 키 입력
        win.bind("<Key>", _close)
        # 마우스 좌/우(및 기타 버튼) 클릭
        win.bind("<Button>", _close)           # Button-1/2/3 등 모두 포함
    
        # 포커스/이벤트를 팝업으로 모아 전역 어디를 눌러도 닫히게 함
        try:
            win.transient(self.root)
            win.grab_set()
            win.focus_set()
        except Exception:
            pass
    
        self._base_stat_popup = win
    def on_fetch_all(self):
        df_full = getattr(self, "df_groups_full", pd.DataFrame())
        if df_full is None or df_full.empty:
            messagebox.showinfo("안내", "조회할 그룹이 없습니다."); return
        self.fetch_groups(df_full[["대표아이템명","sheet","gender","reqLevel"]])

    def on_fetch_selected(self):
        try:
            sel = self._selected_rows_to_df(self.tree_groups, ["대표아이템명","개수"])
            if sel is None or sel.empty:
                messagebox.showinfo("안내", "먼저 2-1에서 그룹을 선택하세요."); return
            # df_groups_full과 조인해서 sheet/gender/reqLevel 채우기
            df_full = getattr(self, "df_groups_full", pd.DataFrame())
            m = pd.merge(sel[["대표아이템명"]], df_full, on="대표아이템명", how="left")
            self.fetch_groups(m[["대표아이템명","sheet","gender","reqLevel"]])
        except Exception:
            print("[ERR] on_fetch_selected crashed:\n"+traceback.format_exc(), flush=True)
    def fetch_groups(self, df_groups_sel: pd.DataFrame):
        try:
            if df_groups_sel is None or df_groups_sel.empty:
                messagebox.showinfo("안내", "조회할 그룹이 없습니다."); return

            from your_app.api.client import build_params, fetch_json_with_retries, parse_trade_json
            out_records = []

            for _, row in df_groups_sel.iterrows():
                
                # 대표아이템명으로 엑셀 '직업' 값을 찾아서 job 파라미터로 사용
                job_val = ""
                try:
                    rep_name = str(row.get("대표아이템명","")).strip()
                    cand = self.df_items[self.df_items["itemName"].astype(str) == rep_name]
                    if "직업" in cand.columns and not cand["직업"].dropna().empty:
                        j = str(cand["직업"].dropna().iloc[0]).strip()
                        if j in {"전사","법사","궁수","도적"}:
                            job_val = j
                except Exception:
                    job_val = ""
                params = build_params(
                    row.get("sheet",""),
                    row.get("gender",""),
                    int(float(row.get("reqLevel",0) or 0)),
                    item_name=row.get("대표아이템명",""),
                    stat_tokens=self._read_stat_tokens(),
                    job=job_val,
                )
                if not params:
                    continue

                data = fetch_json_with_retries(params, retries=2, delay=0.6)
                if not data:
                    continue

                # 응답을 list로 전개
                entries = []
                if isinstance(data, list):
                    entries = data
                elif isinstance(data, dict):
                    for k in ("data","results","items","list","trades"):
                        v = data.get(k)
                        if isinstance(v, list):
                            entries = v
                            break
                    else:
                        entries = [data]

                for d in entries:
                    if isinstance(d, dict):
                        _rec = parse_trade_json(d)
                        # 태그: 대표아이템 컨텍스트를 응답 레코드에 부착 (가공과 동일 기준)
                        _rec['sheet'] = row.get('sheet','')
                        _rec['repName'] = row.get('대표아이템명','')
                        _rec['gender'] = row.get('gender','')
                        _rec['reqLevel'] = int(float(row.get('reqLevel',0) or 0))
                        out_records.append(_rec)

            df_api = pd.DataFrame(out_records)

            # SELL/BUY 분기 + 활성만
            self.df_api_sell = pd.DataFrame(); self.df_api_buy = pd.DataFrame()
            if not df_api.empty:
                # 활성
                active = df_api.get("tradeStatus")
                if active is not None:
                    try: active = active.astype(bool)
                    except Exception: pass
                else:
                    active = True

                sell_mask = df_api.get("tradeType", pd.Series(["sell"]*len(df_api))).astype(str).str.lower().eq("sell")
                buy_mask  = df_api.get("tradeType", pd.Series(["sell"]*len(df_api))).astype(str).str.lower().eq("buy")

                self.df_api_sell = df_api[sell_mask].reset_index(drop=True)
                self.df_api_buy  = df_api[buy_mask].reset_index(drop=True)

            # (핵심) 표시용 DF 생성 후, Tree에 매핑/보관
            tokens = self._read_stat_tokens()
            self.df_api_sell = self._apply_api_post_filters(self.df_api_sell, tokens)
            self.df_api_buy  = self._apply_api_post_filters(self.df_api_buy, tokens)
            self.df_api_sell = self.df_api_sell.reset_index(drop=True)

            self.df_api_sell["_load_order"] = range(len(self.df_api_sell))

            self.df_api_buy  = self.df_api_buy.reset_index(drop=True)

            self.df_api_buy["_load_order"]  = range(len(self.df_api_buy))

            self._api_reset_state()

            self._api_refresh("sell")

            self._api_refresh("buy")

            # ── 디버깅 로그 (3번 표)
            try:
                print(f"[DBG][API] sell_total={len(self.df_api_sell)} buy_total={len(self.df_api_buy)}", flush=True)
                if isinstance(self.df_api_sell, pd.DataFrame) and not self.df_api_sell.empty:
                    r0 = self.df_api_sell.iloc[0]
                    print(f"[DBG][API] first sell item='{r0.get('itemName','')}', profileUrl='{r0.get('profileUrl','')}'", flush=True)
                if isinstance(self.df_api_buy, pd.DataFrame) and not self.df_api_buy.empty:
                    r0 = self.df_api_buy.iloc[0]
                    print(f"[DBG][API] first buy  item='{r0.get('itemName','')}', profileUrl='{r0.get('profileUrl','')}'", flush=True)
            except Exception as e:
                print(f"[DBG][API] debug_log_error: {e}", flush=True)
            self._process_groups(df_groups_sel)

        except Exception as e:
            traceback.print_exc()
            messagebox.showerror("오류", str(e))
        try:
            self._save_history(df_groups_sel)
        except Exception as e:
            print(f"[DBG][HIST] save failed: {e}", flush=True)



    def _relative_ago(self, ts):
        """API 시각 vs 현재 한국시간(UTC+9) → N분전/N시간전/N일전"""
        try:
            t = pd.to_datetime(ts, errors="coerce")
            if pd.isna(t):
                return "-"
            now_kst = pd.Timestamp.utcnow() + pd.Timedelta(hours=9)
            try:
                if getattr(t, "tzinfo", None) is not None:
                    t_utc = t.tz_convert("UTC")
                    t_kst = t_utc.tz_localize(None) + pd.Timedelta(hours=9)
                else:
                    t_kst = t
            except Exception:
                t_kst = t if t.tzinfo is None else (t.tz_localize(None) + pd.Timedelta(hours=9))
            sec = max(0, int((now_kst - t_kst).total_seconds()))
            if sec < 3600:
                m = max(1, sec // 60)
                return f"{m}분 전"
            if sec < 86400:
                h = max(1, sec // 3600)
                return f"{h}시간 전"
            d = max(1, sec // 86400)
            return f"{d}일 전"
        except Exception:
            return "-"
    def _api_to_display_df(self, df: pd.DataFrame, sort_latest: bool = True) -> pd.DataFrame:
        """
        API DF -> 화면 표시 DF (3번 표용)
        보이는 컬럼: itemName | itemPrice(만) | option | 판매자 | 흥정 | 경신시간 | 등록시간 | 상태 | server
        숨김(DF에는 유지): profileUrl, tradeUrl, tradeStatus, color, sheet, gender, reqLevel, _color, _active
        """
        if df is None or df.empty:
            return pd.DataFrame()

        d = df.copy()

        # 최신순 정렬
        if sort_latest and "updated_at" in d.columns:
            d["_sort_key"] = pd.to_datetime(d["updated_at"], errors="coerce")
            d.sort_values("_sort_key", ascending=False, inplace=True)
            d.drop(columns=["_sort_key"], errors="ignore", inplace=True)

        # option 통일
        if "option" not in d.columns and "optionSummarize" in d.columns:
            d.rename(columns={"optionSummarize": "option"}, inplace=True)

        # 가격(만)
        try:
            from your_app.processing.legacy_processor import _format_price_man as _proc_fmt
            if "itemPrice" in d.columns:
                d["itemPrice"] = d["itemPrice"].map(_proc_fmt)
        except Exception:
            pass

        
        # 상대시간
        def _rel(ts):
            """UTC ISO -> Asia/Seoul 기준 상대 표기 (N분전/N시간전/N일전)"""
            try:
                t = pd.to_datetime(ts, utc=True, errors="coerce")
                if pd.isna(t):
                    return ""
                t_kst = t.tz_convert("Asia/Seoul")
                now_kst = pd.Timestamp.now(tz="Asia/Seoul")
                sec = int(max(0, (now_kst - t_kst).total_seconds()))
                if sec < 3600:
                    return f"{max(1, sec//60)}분전"
                if sec < 86400:
                    return f"{max(1, sec//3600)}시간전"
                return f"{max(1, sec//86400)}일전"
            except Exception:
                return ""


        if "updated_at" in d.columns:
            d["경신시간"] = d["updated_at"].map(_rel)
        if "created_at" in d.columns:
            d["등록시간"] = d["created_at"].map(_rel)

        # 판매자
        if "global_name" in d.columns and "판매자" not in d.columns:
            d["판매자"] = d["global_name"].fillna("").astype(str)

        # 흥정: offer_raw 0/1/2 → '흥정불가'/'흥정가능'/''
        def _map_offer(v):
            try:
                iv = int(v)
                if iv == 0: return "흥정불가"
                if iv == 1: return "흥정가능"
                if iv == 2: return ""
            except Exception:
                pass
            # fallback: 과거 텍스트가 들어온 경우
            s = str(v or "")
            if "불가" in s: return "흥정불가"
            if "가능" in s: return "흥정가능"
            return ""

        if "offer_raw" in d.columns:
            d["흥정"] = d["offer_raw"].map(_map_offer)
        elif "offer" in d.columns:
            d["흥정"] = d["offer"].map(_map_offer)
        else:
            d["흥정"] = ""

        # 상태 + 메타
        if "color" in d.columns:
            d["_color"] = d["color"].astype(str).str.lower()
        else:
            d["_color"] = "white"

        if "tradeStatus" in d.columns:
            try:
                active_bool = d["tradeStatus"].astype(bool)
            except Exception:
                active_bool = True
            d["_active"] = active_bool
            d["상태"] = active_bool.map(lambda x: "판매중" if x else "판매완료")
        else:
            d["_active"] = True
            d["상태"] = "판매중"

        # URL/메타 보존(표시 숨김)
        for keep in ("profileUrl","tradeUrl","sheet","gender","reqLevel","color","tradeStatus","offer_raw"):
            if keep not in d.columns:
                d[keep] = ""

        # 표시 컬럼
        show_cols = ["상태","itemName","itemPrice","option","server","판매자","comment","등록시간","경신시간"]
        show_cols = [c for c in show_cols if c in d.columns]
        meta_cols = [c for c in ["_color","_active","profileUrl","tradeUrl","sheet","gender","reqLevel","color","tradeStatus","offer_raw"] if c in d.columns]

        return d[show_cols + meta_cols].reset_index(drop=True)





    def _refresh_history_view(self):
        """좌측 2-2 검색기록 트리를 history 스냅샷으로 갱신."""
        hist = getattr(self, "history", [])
        tree = getattr(self, "hist_tree", None)
        if tree is None:
            return

        # 초기화
        for iid in tree.get_children():
            tree.delete(iid)

        # 컬럼 구성 + headings 표시 보장
        tree["show"] = "headings"
        tree["columns"] = ("이름","요약")
        tree.heading("이름", text="이름");   tree.column("이름", width=200, anchor="w")
        tree.heading("요약", text="요약");   tree.column("요약", width=260, anchor="w")

        # 데이터 채우기
        for i, snap in enumerate(hist):
            name = str(snap.get("label_name","-"))
            summ = str(snap.get("label_stat1","-"))
            tree.insert("", "end", text="", values=(name, summ))

    def _save_history(self, df_groups_sel: pd.DataFrame):
        """검색/가공 1회 요약 저장: 대표아이템명 / 스탯표기 + 결과 스냅샷 포함"""
        if getattr(self, "_is_loading_history", False):
            return

        # 대표아이템명
        try:
            label_name = str(df_groups_sel.iloc[0].get("대표아이템명", "") or "-")
        except Exception:
            label_name = "-"

        # 스탯 토큰 라벨
        try:
            tokens = [t for t in self._read_stat_tokens() if t]
        except Exception:
            tokens = []
        stat_disp = ", ".join(tokens) if tokens else "-"

        # 카운트(표시용)
        def _len_df(x):
            try: return int(len(x))
            except: return 0
        api_cnt = _len_df(getattr(self, "df_api_sell", pd.DataFrame())) + _len_df(getattr(self, "df_api_buy", pd.DataFrame()))
        proc4 = len((getattr(self, "_last_proc_rows", {}) or {}).get(4, []) or [])
        proc5 = len((getattr(self, "_last_proc_rows", {}) or {}).get(5, []) or [])
        proc_cnt = proc4 + proc5
        label_stat = f"{stat_disp} ({api_cnt}/{proc_cnt})"

        if not hasattr(self, "history") or not isinstance(self.history, list):
            self.history = []

        # 입력 필드 스냅샷
        try:
            input_query = self.ent_query.get()
            input_stat1 = self.ent_stat1.get()
            input_stat2 = self.ent_stat2.get()
            input_stat3 = self.ent_stat3.get()
        except Exception:
            input_query = input_stat1 = input_stat2 = input_stat3 = ""

        snap = {
            "timestamp": datetime.now(),
            "label_name": label_name,
            "label_stat1": label_stat,
            "input_query": input_query,
            "input_stat1": input_stat1,
            "input_stat2": input_stat2,
            "input_stat3": input_stat3,
            "df_sell": getattr(self, "df_api_sell", pd.DataFrame()).copy() if isinstance(getattr(self, "df_api_sell", None), pd.DataFrame) else pd.DataFrame(),
            "df_buy":  getattr(self, "df_api_buy",  pd.DataFrame()).copy() if isinstance(getattr(self, "df_api_buy",  None), pd.DataFrame) else pd.DataFrame(),
            "slots":   dict(getattr(self, "_last_proc_rows", {})) if isinstance(getattr(self, "_last_proc_rows", {}), dict) else {},
        }
        # 같은 내용 연속 저장 방지(간단 비교)
        if not self.history or self.history[0].get("label_name") != snap["label_name"] or self.history[0].get("label_stat1") != snap["label_stat1"]:
            self.history.insert(0, snap)
            self.history = self.history[:10]

        self._refresh_history_view()





    def _on_history_select(self):
        sel = self.hist_tree.selection()
        if not sel:
            focus = self.hist_tree.focus()
            if focus:
                sel = (focus,)
        if not sel:
            return
        idx = self.hist_tree.index(sel[0])  # 0 = 최신
        self._load_history(idx)

    def _load_history(self, idx: int):
        """히스토리 선택 → 표/슬롯 복원 (레거시 기록 방어)"""
        if not (0 <= idx < len(getattr(self, "history", []))):
            return
        snap = self.history[idx]
        try:
            self._is_loading_history = True
            # 입력 필드 복원 (레거시 기록이면 빈값)
            def _set_entry(ent, val: str):
                try:
                    ent.delete(0, tk.END)
                    ent.insert(0, val or "")
                except Exception:
                    pass
            _set_entry(self.ent_query, snap.get("input_query", ""))
            _set_entry(self.ent_stat1, snap.get("input_stat1", ""))
            _set_entry(self.ent_stat2, snap.get("input_stat2", ""))
            _set_entry(self.ent_stat3, snap.get("input_stat3", ""))

            _sell = snap.get("df_sell", None)
            _buy  = snap.get("df_buy",  None)
            self.df_api_sell = _sell.copy() if isinstance(_sell, pd.DataFrame) else pd.DataFrame()
            self.df_api_buy  = _buy.copy()  if isinstance(_buy,  pd.DataFrame) else pd.DataFrame()
            if not self.df_api_sell.empty:
                self.df_api_sell = self.df_api_sell.reset_index(drop=True)
                self.df_api_sell["_load_order"] = range(len(self.df_api_sell))
            if not self.df_api_buy.empty:
                self.df_api_buy  = self.df_api_buy.reset_index(drop=True)
                self.df_api_buy["_load_order"]  = range(len(self.df_api_buy))
            self._api_reset_state()
            self._api_refresh("sell")
            self._api_refresh("buy")
            slots = snap.get("slots") or {}
            self._proc_build_combined(slots)
            self._proc_render_page()
            self._last_proc_rows = {4: list(slots.get(4, []) or []), 5: list(slots.get(5, []) or [])}
            self._last_proc_counts = {4: len(self._last_proc_rows.get(4, [])), 5: len(self._last_proc_rows.get(5, []))}
        finally:
            self._is_loading_history = False

    def on_execute_group(self):
        """2-1 대표아이템 트리 더블클릭: 선택(없으면 첫 그룹) 기준으로 즉시 조회+가공을 실행."""
        try:
            df_full = getattr(self, "df_groups_full", None)
            if df_full is None or (hasattr(df_full, "empty") and df_full.empty):
                from tkinter import messagebox as _mb
                _mb.showinfo("안내", "그룹이 없습니다. 먼저 검색하세요.")
                return

            # 선택 행 → 없으면 첫 행
            sel = self._selected_rows_to_df(self.tree_groups, ["대표아이템명", "개수"])
            if sel is None or sel.empty:
                row_full = df_full.iloc[0]
            else:
                key = sel.iloc[0]["대표아이템명"]
                row_full = df_full[df_full["대표아이템명"] == key].iloc[0]

            # 단일 행 DF 구성 후 파이프라인 실행
            import pandas as _pd
            df_one = _pd.DataFrame([{
                "대표아이템명": row_full.get("대표아이템명", ""),
                "sheet": row_full.get("sheet", ""),
                "gender": row_full.get("gender", ""),
                "reqLevel": row_full.get("reqLevel", 0),
            }])
            self.fetch_groups(df_one[["대표아이템명","sheet","gender","reqLevel"]])
        except Exception:
            import traceback
            from tkinter import messagebox as _mb
            print("[ERR] on_execute_group]\n" + traceback.format_exc(), flush=True)
            _mb.showinfo("오류", "대표아이템 실행 중 오류가 발생했습니다.")

    def on_open_search_page(self):
        """선택(없으면 첫 그룹) 기준으로 사이트 검색 페이지를 브라우저에서 연다."""
        import webbrowser
        from tkinter import messagebox as _mb
        try:
            df_full = getattr(self, "df_groups_full", None)
            if df_full is None or (hasattr(df_full, "empty") and df_full.empty):
                _mb.showinfo("안내", "그룹이 없습니다. 먼저 검색하세요.")
                return

            # 선택 행 → 없으면 첫 행
            sel = self._selected_rows_to_df(self.tree_groups, ["대표아이템명", "개수"])
            if sel is None or sel.empty:
                row_full = df_full.iloc[0]
            else:
                key = sel.iloc[0]["대표아이템명"]
                row_full = df_full[df_full["대표아이템명"] == key].iloc[0]

            # 스탯 토큰
            stat_tokens = [t for t in self._read_stat_tokens() if t]

            # 대표아이템명으로 R열(직업) 추출
            job_val = ""
            try:
                rep_name = str(row_full.get("대표아이템명", "")).strip()
                cand = self.df_items[self.df_items["itemName"].astype(str) == rep_name]
                if "직업" in cand.columns and not cand["직업"].dropna().empty:
                    j = str(cand["직업"].dropna().iloc[0]).strip()
                    if j in {"전사","궁수","법사","도적"}:
                        job_val = j
            except Exception:
                job_val = ""

            # 파라미터 생성 (job 포함)
            from your_app.api.client import build_params
            params = build_params(
                row_full.get("sheet", ""),
                row_full.get("gender", ""),
                int(float(row_full.get("reqLevel", 0) or 0)),
                row_full.get("대표아이템명", ""),
                stat_tokens,
                low_tuc=None,
                job=job_val,
            )

            # 사이트 URL 생성 및 오픈
            url = build_site_search_url(params)
            if not (isinstance(url, str) and url.startswith("http")):
                _mb.showinfo("안내", "검색 URL을 만들 수 없습니다.")
                return
            webbrowser.open_new_tab(url)
        except Exception:
            import traceback
            print("[ERR] on_open_search_page]\n" + traceback.format_exc(), flush=True)
            _mb.showinfo("오류", "검색 페이지 열기 중 오류가 발생했습니다.")


    # ── 4/5 슬롯 렌더링
    def _init_slot_views(self):
        """우측 결과칸 2개(4,5). 6/7은 숨김."""
        if getattr(self, "_slot_views_ready", False):
            return
        self._slot_views_ready = True

        for nm in ("slot6", "slot7"):
            fr = getattr(self, nm, None)
            if fr:
                try: fr.grid_remove()
                except Exception: pass

        def make_tree(frame):
            for w in frame.winfo_children():
                w.destroy()

            tree = ttk.Treeview(
                frame,
                columns=("days_ago","item","price","stats","seller","comment"),
                show="headings", selectmode="browse"
            )
            tree.heading("days_ago", text="일전");   tree.column("days_ago", width=30, anchor="center")
            tree.heading("item", text="아이템");     tree.column("item", width=70, anchor="w")
            tree.heading("price", text="가격(만)");  tree.column("price", width=60, anchor="e")
            tree.heading("stats", text="스탯");      tree.column("stats", width=120, anchor="w")
            tree.heading("seller", text="판매자");   tree.column("seller", width=60, anchor="w")
            tree.heading("comment", text="코멘트");  tree.column("comment", width=240, anchor="w")

            style = ttk.Style(tree)
            style.configure("TTreeview", rowheight=22)

            vbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            hbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
            tree.grid(row=0, column=0, sticky="nsew"); vbar.grid(row=0, column=1, sticky="ns"); hbar.grid(row=1, column=0, sticky="ew")
            frame.grid_rowconfigure(0, weight=1); frame.grid_columnconfigure(0, weight=1)
            return tree

        self.slot_proc_tree = make_tree(self.slot4)
        self.slot_proc_tree.bind("<Double-1>", self._on_proc_tree_double_click)
        self.slot4.grid_rowconfigure(1, weight=0)
        proc_bar = ttk.Frame(self.slot4); proc_bar.grid(row=1, column=0, sticky="ew", pady=(6,0))
        self.btn_proc_prev = ttk.Button(proc_bar, text="◀", width=3, command=lambda: self._proc_page_step(-1))
        self.btn_proc_prev.grid(row=0, column=0, padx=(0,6))
        self.lbl_proc_page = ttk.Label(proc_bar, text="1/1 · 총 0")
        self.lbl_proc_page.grid(row=0, column=1)
        self.btn_proc_next = ttk.Button(proc_bar, text="▶", width=3, command=lambda: self._proc_page_step(+1))
        self.btn_proc_next.grid(row=0, column=2, padx=(6,0))
        proc_bar.grid_columnconfigure(10, weight=1)
        self.PROC_PAGE_SIZE = 18
        self._proc_rows_combined = []
        self._proc_page = 1

    # ─────────────────────────────────────────────────────────
    # ④ 가공결과 — 통합 + 페이지네이션
    def _proc_build_combined(self, result_dict: dict):
        rows4 = result_dict.get(4, []) or []
        rows5 = result_dict.get(5, []) or []
        rows = list(rows4) + list(rows5)
        for i, r in enumerate(rows):
            if isinstance(r, dict):
                r.setdefault("_orig_idx", i)
        import pandas as pd
        if not rows:
            self._proc_rows_combined = []
            self._proc_page = 1
            return
        d = pd.DataFrame(rows)
        if "date_key" in d.columns and not d["date_key"].isna().all():
            d["_grp"] = d["date_key"]
            d["_hl"] = d.get("highlight", False).apply(_as_bool_highlight_gui).map(lambda x: 0 if x else 1)
            d.sort_values(by=["_grp","_hl","_orig_idx"], ascending=[False, True, True], inplace=True, kind="mergesort")
        elif "days_ago" in d.columns:
            d["_grp"] = pd.to_numeric(d["days_ago"], errors="coerce").fillna(999999).astype(int)
            d["_hl"] = d.get("highlight", False).apply(_as_bool_highlight_gui).map(lambda x: 0 if x else 1)
            d.sort_values(by=["_grp","_hl","_orig_idx"], ascending=[True, True, True], inplace=True, kind="mergesort")
        else:
            d["_grp"] = 0
            d["_hl"] = d.get("highlight", False).apply(_as_bool_highlight_gui).map(lambda x: 0 if x else 1)
            d.sort_values(by=["_hl","_orig_idx"], ascending=[True, True], inplace=True, kind="mergesort")
        self._proc_rows_combined = d.to_dict(orient="records")
        self._proc_page = 1

    def _proc_total_pages(self):
        n = len(self._proc_rows_combined or [])
        pages = (n + self.PROC_PAGE_SIZE - 1)//self.PROC_PAGE_SIZE if n>0 else 0
        return n, pages

    def _proc_page_step(self, delta: int):
        n, pages = self._proc_total_pages()
        page = max(1, min(pages if pages>0 else 1, self._proc_page + delta))
        if page != self._proc_page:
            self._proc_page = page
            self._proc_render_page()

    def _proc_render_page(self):
        rows = self._proc_rows_combined or []
        n, pages = self._proc_total_pages()
        page = max(1, min(pages if pages>0 else 1, self._proc_page))
        start = (page-1)*self.PROC_PAGE_SIZE
        end = start + self.PROC_PAGE_SIZE
        page_rows = rows[start:end]
        tree = getattr(self, "slot_proc_tree", None)
        if not tree: return
        tree._page_rows = list(page_rows)
        tree.delete(*tree.get_children())
        try: tree.tag_configure("hl", background="#fff7d6")
        except Exception: pass
        if not hasattr(self, "_proc_fmt_man"):
            try:
                from your_app.processing.legacy_processor import _format_price_man as _proc_fmt
                self._proc_fmt_man = _proc_fmt
            except Exception:
                self._proc_fmt_man = lambda x: x
        spec = [("날짜", 70, "center"), ("아이템", 200, "w"), ("판매자", 110, "w"), ("가격(만)", 80, "e"), ("스탯", 320, "w"), ("비고", 480, "w")]
        tree.config(columns=[n for n,_,_ in spec]); tree.heading("#0", text=""); tree.column("#0", width=0, stretch=False)
        for name, w, anc in spec:
            tree.heading(name, text=name); tree.column(name, width=w, anchor=anc, stretch=(name in ("아이템","스탯","비고")))
        for r in page_rows:
            try:
                date = str(r.get("date_key","")) or str(r.get("date_raw",""))
                item = str(r.get("item",""))
                seller = str(r.get("seller",""))
                price = self._proc_fmt_man(int(r.get("price_num", r.get("price", 0)) or 0))
                stats = str(r.get("stats",""))
                comment = str(r.get("comment",""))
                tags = ["hl"] if _as_bool_highlight_gui(r.get("highlight", False)) else []
                tree.insert("", "end", text="", values=(date, item, seller, price, stats, comment), tags=tags)
            except Exception:
                pass
        if hasattr(self, "lbl_proc_page"):
            self.lbl_proc_page.configure(text=f"{page}/{pages or 0} · 총 {n}")
        try:
            if page<=1 or pages==0: self.btn_proc_prev.state(["disabled"]) 
            else: self.btn_proc_prev.state(["!disabled"])
            if pages==0 or page>=pages: self.btn_proc_next.state(["disabled"]) 
            else: self.btn_proc_next.state(["!disabled"])
        except Exception:
            pass

    def _on_proc_tree_double_click(self, _evt=None):
        """가공 결과(4번 통합 표) 더블클릭 시 링크 열기."""
        tree = getattr(self, "slot_proc_tree", None)
        if tree is None:
            return
        rows = getattr(tree, "_page_rows", [])
        if not rows:
            messagebox.showinfo("안내", "행 데이터가 없습니다.")
            return
        sel = tree.selection()
        if not sel:
            return
        try:
            idx = tree.index(sel[0])
            rec = rows[idx] if 0 <= idx < len(rows) else None
        except Exception:
            rec = None
        if not isinstance(rec, dict):
            messagebox.showinfo("안내", "행 데이터를 찾을 수 없습니다.")
            return
        url = str(rec.get("profileUrl") or rec.get("tradeUrl") or rec.get("url") or "").strip()
        if url.startswith("http"):
            webbrowser.open_new_tab(url)
        else:
            messagebox.showinfo("안내", "열 수 있는 링크가 없습니다.")
    def _render_slot(self, slot_idx: int, rows):
        tree = self.slot4_tree if slot_idx == 4 else self.slot5_tree
        if tree is None:
            return
        tree.delete(*tree.get_children())

        # highlight 태그 스타일
        try:
            tree.tag_configure("hl", background="#fff7d6")
        except Exception:
            pass

        # 가격 포맷터
        if not hasattr(self, "_proc_fmt_man"):
            try:
                from your_app.processing.legacy_processor import _format_price_man as _proc_fmt
                self._proc_fmt_man = _proc_fmt
            except Exception:
                self._proc_fmt_man = lambda x: x

        # ── 컬럼 사양 (원하는 픽셀로 여기만 고치면 됨)
        spec = [
            ("days_ago", 50,  "e"),   # n일전
            ("HL",       44,  "center"),  # highlight 표시
            ("price",    60,  "e"),   # 가격(만)
            ("item",     100, "w"),   # 아이템
            ("stats",    150, "w"),   # 스탯
            ("seller",   50, "w"),   # 판매자
            ("comment",  300, "w"),   # 코멘트
        ]
        # 고정 폭 컬럼 적용(자동늘어남 차단)
        self._set_columns_fixed(tree, spec)

        # 가로 스크롤 강제 재부착 (검색 후에도 유지)
        self._ensure_hscroll(tree, force=True)

        # 데이터 채우기
        for r in rows or []:
            price = r.get("price_num", r.get("price"))
            price_disp = self._proc_fmt_man(price) if isinstance(price, (int, float)) else (r.get("price") or "")
            dd = (r.get("days_ago","") or "").replace(" ", "").replace("일전전", "일전")
            tag = "hl" if _as_bool_highlight_gui(r.get("highlight")) else ""
            tree.insert(
                "", "end",
                values=(dd, ("T" if _as_bool_highlight_gui(r.get("highlight")) else "F"), price_disp, r.get("item",""), r.get("stats",""), r.get("seller",""), r.get("comment","")),
                tags=((tag,) if tag else ()),
            )




    # App 클래스 안에 추가
    def _set_columns_fixed(self, tree, spec):
        """
        spec = [("colname", width_px, anchor), ...]
        - stretch=False + minwidth=width 로 자동 리사이즈 방지
        - displaycolumns 를 컬럼과 동일 순서로 고정
        - idle 시점에 한 번 더 폭 재적용
        """
        tree["show"] = "headings"
        cols = [c for c, _, _ in spec]
        tree["columns"] = cols
        tree["displaycolumns"] = cols

        for name, w, anc in spec:
            tree.heading(name, text=name)
            tree.column(name, width=w, minwidth=w, stretch=False, anchor=anc)

        # 뒤늦게 들어오는 레이아웃 이벤트 대비, idle 에서 한번 더 고정
        def _reapply():
            try:
                for name, w, anc in spec:
                    tree.column(name, width=w, minwidth=w, stretch=False, anchor=anc)
            except Exception:
                pass
        tree.after_idle(_reapply)




    def _process_groups(self, df_groups_sel: pd.DataFrame):
        import os, pandas as pd, traceback
        from your_app.processing.legacy_processor import process_items

        self._init_slot_views()

        # 판매_데이터 파일 확인
        preferred = ['요약본.parquet', 'data.parquet']
        sales_file = next((f for f in preferred if os.path.exists(f)), None)
        if not sales_file or df_groups_sel is None or df_groups_sel.empty:
            self._last_proc_counts = {4: 0, 5: 0}
            print(f"[DBG][PROC] skip — sales_file={sales_file}, groups_sel_empty={df_groups_sel is None or df_groups_sel.empty}", flush=True)
            return

        # 같은 시트/성별/레벨 아이템 수집
        row = df_groups_sel.iloc[0]
        sheet  = str(row.get("sheet",""))
        gender = str(row.get("gender",""))
        req    = str(row.get("reqLevel",""))
        rep    = str(row.get("대표아이템명","")).strip()

        
        # (수정) 같은 시트/성별/레벨 **이면서 현재 검색필터(df_filtered)에 남아있는 아이템들**만 사용
        # 즉, '대표아이템명 + 개수(n)'에 해당하는 정확한 목록을 허용 집합으로 만든다.
        df_src = getattr(self, "df_filtered", pd.DataFrame())
        if df_src is None or df_src.empty:
            df_src = getattr(self, "df_items", pd.DataFrame())
        selected_items = []
        if not df_src.empty:
            m = (
                (df_src["sheet"].astype(str) == sheet) &
                (df_src["gender"].astype(str) == gender) &
                (df_src["reqLevel"].astype(str) == req)
            )
            names = df_src.loc[m, "itemName"].astype(str).tolist()
            selected_items = [rep] + [x for x in names if x != rep]

        tokens = [t for t in self._read_stat_tokens() if t]

        # ── 디버깅 로그(입력 요약)
        print(f"[DBG][PROC] sheet='{sheet}', gender='{gender}', req='{req}', rep='{rep}'", flush=True)
        print(f"[DBG][PROC] selected_items={len(selected_items)} (ex: {selected_items[:3]})", flush=True)
        print(f"[DBG][PROC] tokens={tokens}", flush=True)

        # 가공 실행
        try:
            debugger.start_run({
                'sheet': sheet,
                'gender': gender,
                'reqLevel': req,
                'rep': rep,
                'selected_items': selected_items,
                'tokens': tokens,
                'sales_file': sales_file,
            })
            result = process_items(tokens, sheet, selected_items, 'item.xlsx', sales_file)
            try:
                c4 = len(result.get(4, []) or [])
                c5 = len(result.get(5, []) or [])
            except Exception:
                c4 = c5 = 0
            debugger.finish_run(summary={'slot4': c4, 'slot5': c5})
        except Exception:
            print('[ERR][PROC] process_items crashed:\n' + traceback.format_exc(), flush=True)
            result = {4: [], 5: []}

        c4 = len(result.get(4, []) or [])
        c5 = len(result.get(5, []) or [])
        print(f"[DBG][PROC] slot4={c4}, slot5={c5}", flush=True)

        # 통합+페이지네이션 렌더
        self._last_proc_rows   = {4: list(result.get(4, [])), 5: list(result.get(5, []))}
        self._last_proc_counts = {4: c4, 5: c5}
        self._proc_build_combined(self._last_proc_rows)
        self._proc_render_page()
    def _init_tree_fonts(self):
        """Treeview 가독성(헤더 볼드, 행 글꼴 살짝 키우기) 초깃값 1회 설정"""
        if getattr(self, "_tv_fonts_init", False):
            return
        import tkinter.font as tkfont
        from tkinter import ttk
        try:
            base = tkfont.nametofont("TkDefaultFont")
            self._row_font = tkfont.Font(family=base.cget("family"),
                                        size=base.cget("size")+1,
                                        weight="normal")
            self._head_font = tkfont.Font(family=base.cget("family"),
                                        size=base.cget("size")+1,
                                        weight="bold")
            style = ttk.Style()
            style.configure("Treeview", font=self._row_font)
            style.configure("Treeview.Heading", font=self._head_font)
        except Exception:
            pass
        self._tv_fonts_init = True
    def _ensure_hscroll(self, tree, force: bool = False):
        """
        Treeview에 가로 스크롤바(<->)를 안전하게 부착/재부착한다.
        - 기존 핸들이 죽었거나(force=True)면 파괴 후 재생성
        - 항상 xscrollcommand를 재바인딩
        """
        import tkinter as tk
        from tkinter import ttk

        try:
            old = getattr(tree, "_hsb", None)
            need_new = (
                force
                or old is None
                or not str(old)               # 빈 핸들
                or (hasattr(old, "winfo_exists") and not old.winfo_exists())
            )
            if not need_new:
                # 기존 핸들 재바인딩만
                tree.configure(xscrollcommand=old.set)
                return

            # 기존이 있으면 정리
            try:
                if old and hasattr(old, "winfo_exists") and old.winfo_exists():
                    old.destroy()
            except Exception:
                pass

            # 새 스크롤바 생성
            hsb = ttk.Scrollbar(tree.master, orient="horizontal", command=tree.xview)
            tree.configure(xscrollcommand=hsb.set)

            # 배치 (grid/pack 모두 대응)
            mgr = tree.winfo_manager()
            if mgr == "grid":
                gi = tree.grid_info()
                row = int(gi.get("row", 0))
                col = int(gi.get("column", 0))
                hsb.grid(row=row + 1, column=col, sticky="ew")
            elif mgr == "pack":
                hsb.pack(side="bottom", fill="x")
            else:
                # 최후 수단
                hsb.place(relx=0, rely=1.0, relwidth=1.0, anchor="sw")

            tree._hsb = hsb
        except Exception:
            # 실패해도 앱이 죽지 않게 억제
            pass

# ─────────────────────────────────────────────────────────────
# 런 단독 실행용
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from your_app.common.data_loader import load_item_data
    root = tk.Tk()
    df = load_item_data("item.xlsx")
    App(root, df)
    root.mainloop()
