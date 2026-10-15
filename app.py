"""AgriData AI - minimal shared Thai agricultural data chatbot.
AI parses user intent; pandas, not the model, calculates numeric answers.
"""
from __future__ import annotations

import hmac
import io
import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="AgriData AI", page_icon="🌾", layout="wide")

BASE = Path(__file__).parent
COLUMNS = {
    "จังหวัด": "province", "ชื่อจังหวัด": "province", "province": "province",
    "อำเภอ": "district", "ชื่ออำเภอ": "district", "district": "district",
    "พืช": "crop", "ชื่อพืช": "crop", "ชนิดพืช": "crop", "พืชเศรษฐกิจ": "crop", "crop": "crop",
    "ปี": "year", "ปีพศ": "year", "ปีพ.ศ": "year", "ปีข้อมูล": "year", "ปีเพาะปลูก": "year", "year": "year",
    "เนื้อที่เพาะปลูกไร่": "planted_rai", "พื้นที่เพาะปลูกไร่": "planted_rai", "พื้นที่เพาะปลูก": "planted_rai", "เนื้อที่เพาะปลูก": "planted_rai", "plantedrai": "planted_rai",
    "เนื้อที่เก็บเกี่ยวไร่": "harvested_rai", "พื้นที่เก็บเกี่ยวไร่": "harvested_rai", "พื้นที่เก็บเกี่ยว": "harvested_rai", "เนื้อที่เก็บเกี่ยว": "harvested_rai", "harvestedrai": "harvested_rai",
    "ผลผลิตตัน": "production_ton", "ผลผลิตรวมตัน": "production_ton", "ปริมาณผลผลิตตัน": "production_ton", "ผลผลิต": "production_ton", "productionton": "production_ton",
    "ผลผลิตต่อไร่กกไร่": "yield_kg_rai", "ผลผลิตเฉลี่ยกกไร่": "yield_kg_rai", "ผลผลิตต่อไร่": "yield_kg_rai", "yieldkgrai": "yield_kg_rai",
    "แหล่งข้อมูล": "source", "ที่มา": "source", "อ้างอิง": "source", "source": "source",
    "ลิงก์อ้างอิง": "url", "ลิงก์": "url", "url": "url", "sourceurl": "url",
}
METRICS = {
    "production_ton": ("ผลผลิต", "ตัน"),
    "harvested_rai": ("เนื้อที่เก็บเกี่ยว", "ไร่"),
    "planted_rai": ("เนื้อที่เพาะปลูก", "ไร่"),
    "yield_kg_rai": ("ผลผลิตต่อไร่", "กก./ไร่"),
}
DIMENSIONS = {"province": "จังหวัด", "district": "อำเภอ", "crop": "พืช", "year": "ปี"}


def normalized(name: str) -> str:
    return re.sub(r"[\s_()./\-–]+", "", str(name).strip().lower())


def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except (FileNotFoundError, KeyError):
        return default


@st.cache_data(show_spinner=False)
def read_data(path: str, timestamp: float) -> pd.DataFrame:
    del timestamp  # invalidates cached data when file is updated
    file = Path(path)
    if file.suffix.lower() == ".xlsx":
        raw = pd.read_excel(file, sheet_name=0)  # use the first worksheet
    else:
        raw = pd.read_csv(file, encoding="utf-8-sig")
    renamed = {}
    used = set()
    for col in raw.columns:
        mapped = COLUMNS.get(normalized(col))
        if mapped and mapped not in used:
            renamed[col] = mapped
            used.add(mapped)
    df = raw.rename(columns=renamed)
    required = {"province", "district", "crop", "year"}
    if not required.issubset(df.columns):
        missing = ", ".join(sorted(required - set(df.columns)))
        raise ValueError(f"ไม่พบคอลัมน์ที่จำเป็น: {missing} (จังหวัด อำเภอ พืช ปี)")
    df = df.loc[:, ~df.columns.duplicated()].copy()
    for col in ("province", "district", "crop"):
        df[col] = df[col].astype("string").str.strip()
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    for col in ("planted_rai", "harvested_rai", "production_ton", "yield_kg_rai"):
        if col not in df:
            df[col] = float("nan")
        df[col] = pd.to_numeric(df[col].astype("string").str.replace(",", "", regex=False), errors="coerce")
        df.loc[df[col] < 0, col] = float("nan")
    for col in ("source", "url"):
        if col not in df:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str)
    df = df.dropna(subset=["year", "province", "district", "crop"])
    df = df.loc[(df["province"] != "") & (df["district"] != "") & (df["crop"] != "")].copy()
    df["year"] = df["year"].astype(int)
    return df


def nearest_value(value: str | None, options: list[str]) -> str | None:
    if not value:
        return None
    v = normalized(value).replace("จังหวัด", "").replace("อำเภอ", "")
    for item in options:
        if normalized(item).replace("จังหวัด", "").replace("อำเภอ", "") == v:
            return item
    return None


def parse_question(question: str, df: pd.DataFrame, history: list[dict]) -> dict:
    from google import genai
    from google.genai import types

    key = secret("GEMINI_API_KEY")
    client = genai.Client(api_key=key)
    options = {field: sorted(df[field].dropna().astype(str).unique().tolist())[:150]
               for field in ("province", "district", "crop")}
    context = [{"role": h["role"], "text": h["content"][:400]} for h in history[-4:]]
    prompt = f"""Convert the Thai agricultural statistics question into a JSON query. Never answer with guessed numbers.
Valid JSON properties (ALL required):
"intent": one of ["rank","trend","compare","summary","table"];
"province": exact province name from available options or null;
"district": exact district name from options or null;
"crop": exact crop name from options or null;
"year": integer or null; "year_start": integer or null; "year_end": integer or null;
"year_mode": one of ["latest","explicit","all"];
"metric": one of ["production_ton","harvested_rai","planted_rai","yield_kg_rai"];
"group_by": one of ["province","district","crop","year","none"];
"top_n": integer 1..20;
"ascending": boolean.
Rules: A user asking for 'ล่าสุด' or unspecified year means year_mode=latest except trends.
Ranking districts: intent=rank group_by=district. Comparing provinces: intent=compare group_by=province.
Trends: intent=trend group_by=year year_mode=all (last five years unless range given).
If explicit year or range, year_mode=explicit.
If user mentions an item outside the options, still put it in the appropriate field so app can report missing data.
If a named year is Buddhist Era (25xx), preserve it. If AD (20xx), preserve it, app will convert if needed.
Do not make up geography, measure, years, or records.
Options: {json.dumps(options, ensure_ascii=False)}
Recent conversation: {json.dumps(context, ensure_ascii=False)}
Current question: {question}
"""
    result = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
    )
    request = json.loads(result.text)
    if not isinstance(request, dict):
        raise ValueError("AI ตอบกลับด้วยรูปแบบที่ไม่ถูกต้อง")
    return request


def format_num(value: float, unit: str) -> str:
    return f"{value:,.2f} {unit}" if unit == "กก./ไร่" else f"{value:,.0f} {unit}"


def answer_query(data: pd.DataFrame, q: dict):
    work = data.copy()
    filter_text = []
    for col, name in (("province", "จังหวัด"), ("district", "อำเภอ"), ("crop", "พืช")):
        if q.get(col):
            found = nearest_value(str(q[col]), work[col].dropna().astype(str).unique().tolist())
            if not found:
                return f"ไม่พบ{name} ‘{q[col]}’ ในฐานข้อมูล หรือไม่มีข้อมูลในเงื่อนไขก่อนหน้า", None, None
            work = work.loc[work[col] == found]
            filter_text.append(f"{name}: {found}")
    if work.empty:
        return "ไม่พบข้อมูลที่ตรงกับเงื่อนไข", None, None

    metric = q.get("metric", "production_ton")
    if metric not in METRICS:
        metric = "production_ton"
    label, unit = METRICS[metric]
    intent = q.get("intent", "summary")
    if intent not in {"rank", "trend", "compare", "summary", "table"}:
        intent = "summary"

    years = sorted(work["year"].dropna().unique().tolist())
    buddhist = bool(years and min(years) > 2400)

    def year_adjust(value):
        try:
            n = int(value)
        except (ValueError, TypeError):
            return None
        if buddhist and 1900 <= n < 2200:
            return n + 543
        if not buddhist and 2400 <= n <= 2700:
            return n - 543
        return n

    year = year_adjust(q.get("year"))
    start = year_adjust(q.get("year_start"))
    end = year_adjust(q.get("year_end"))
    mode = str(q.get("year_mode", "latest"))
    if intent == "trend" and mode == "latest":
        mode = "all"
    if mode == "explicit" or year is not None or start is not None or end is not None:
        if year is not None:
            work = work.loc[work["year"] == year]
        if start is not None:
            work = work.loc[work["year"] >= start]
        if end is not None:
            work = work.loc[work["year"] <= end]
    elif mode == "latest" and intent != "trend":
        work = work.loc[work["year"] == max(years)]
    elif intent == "trend" and start is None and end is None:
        work = work.loc[work["year"].isin(years[-5:])]

    if work.empty:
        return "ไม่พบข้อมูลสำหรับปีหรือเงื่อนไขที่เลือก กรุณาตรวจสอบช่วงปีในฐานข้อมูล", None, None
    yr_text = ", ".join(map(str, sorted(work["year"].unique().tolist())))
    filter_text.append(f"ปีข้อมูล: {yr_text}")
    dim = q.get("group_by", "none")
    if dim not in DIMENSIONS and dim != "none":
        dim = "none"
    if intent == "trend":
        dim = "year"
    if intent == "rank" and dim == "none":
        dim = "district"

    # Avoid interpreting missing numbers as zero.
    if metric == "yield_kg_rai":
        work = work.copy()
        work["valid_yield_production"] = work["production_ton"].where(work["harvested_rai"].gt(0))
        work["valid_yield_area"] = work["harvested_rai"].where(work["production_ton"].notna() & work["harvested_rai"].gt(0))
        work["yield_weighted"] = work["yield_kg_rai"] * work["harvested_rai"]
    else:
        work = work.loc[work[metric].notna()]
    if work.empty:
        return f"พบพื้นที่และปีที่ระบุ แต่ไม่มีค่าตัวเลข ‘{label}’ ที่ใช้คำนวณได้", None, None

    group_cols = [] if dim == "none" else [dim]
    if dim == "district" and work["province"].nunique() > 1:
        group_cols = ["province", "district"]

    def calc(group: pd.DataFrame):
        if metric != "yield_kg_rai":
            return float(group[metric].sum())
        p = group["valid_yield_production"].sum(min_count=1)
        a = group["valid_yield_area"].sum(min_count=1)
        if pd.notna(a) and a > 0 and pd.notna(p):
            return float(p / a * 1000)
        # If only yield_kg_rai is supplied, use weighted mean where area exists.
        weights = group.dropna(subset=["yield_kg_rai", "harvested_rai"])
        if len(weights) and weights["harvested_rai"].sum() > 0:
            return float(weights["yield_weighted"].sum() / weights["harvested_rai"].sum())
        single = group["yield_kg_rai"].dropna()
        return float(single.iloc[0]) if len(single) == 1 else float("nan")

    if group_cols:
        records = []
        for keys, group in work.groupby(group_cols, dropna=True, sort=True):
            if not isinstance(keys, tuple):
                keys = (keys,)
            record = {DIMENSIONS[field]: str(val) for field, val in zip(group_cols, keys)}
            record[f"{label} ({unit})"] = calc(group)
            records.append(record)
        result = pd.DataFrame(records)
    else:
        result = pd.DataFrame([{f"{label} ({unit})": calc(work)}])
    metric_col = f"{label} ({unit})"
    result = result.dropna(subset=[metric_col])
    if result.empty:
        return f"ไม่สามารถคำนวณ {label} ได้ เนื่องจากข้อมูลเชิงตัวเลขไม่ครบ", None, None
    ascending = bool(q.get("ascending", False))
    if intent == "trend" or dim == "year":
        result["ปี"] = pd.to_numeric(result["ปี"], errors="coerce")
        result = result.sort_values("ปี")
    else:
        result = result.sort_values(metric_col, ascending=ascending)
    if intent == "rank":
        try:
            limit = max(1, min(20, int(q.get("top_n", 10))))
        except (ValueError, TypeError):
            limit = 10
        result = result.head(limit)
    else:
        result = result.head(150)

    if not group_cols:
        msg = f"**{label}รวมตามเงื่อนไข** = **{format_num(float(result.iloc[0][metric_col]), unit)}**"
    else:
        msg = f"**{label}** แยกตาม{DIMENSIONS[dim]} ({len(result)} รายการ)"
    msg += "\n\n" + " · ".join(filter_text)
    if len(work["year"].unique()) > 1 and dim != "year":
        msg += "\n\n⚠️ ผลรวมครอบคลุมหลายปี ใช้เปรียบเทียบข้ามปีด้วยความระมัดระวัง"
    sources = [str(s).strip() for s in work["source"].unique() if str(s).strip()]
    urls = [str(s).strip() for s in work["url"].unique() if str(s).strip()]
    if sources:
        msg += "\n\n**แหล่งข้อมูลในไฟล์:** " + "; ".join(sources[:8])
        if len(sources) > 8:
            msg += " (และแหล่งอื่นเพิ่มเติม)"
    if urls:
        msg += "\n\n**ลิงก์อ้างอิงที่บันทึกไว้:** " + "; ".join(urls[:5])
    return msg, result, metric_col


def show_result(result: pd.DataFrame, metric_col: str, widget_key: str):
    st.dataframe(result, use_container_width=True, hide_index=True)
    if len(result) > 1 and len(result) <= 40:
        x_col = result.columns[0]
        try:
            chart_data = result.set_index(x_col)[[metric_col]]
            st.bar_chart(chart_data)
        except Exception:
            pass
    st.download_button(
        "⬇️ ดาวน์โหลดตาราง CSV (เปิดด้วย Excel)",
        data=result.to_csv(index=False).encode("utf-8-sig"),
        file_name="agri_result.csv", mime="text/csv",
        key=f"export_{widget_key}",
    )


st.title("🌾 AgriData AI")
st.caption("Chatbot วิเคราะห์ฐานข้อมูลผลผลิตพืชเศรษฐกิจรายอำเภอ | ตัวเลขคำนวณจากข้อมูลในไฟล์ ไม่ใช่ AI เดา")

password = secret("APP_PASSWORD")
if password and not st.session_state.get("authorized", False):
    with st.form("login"):
        entered = st.text_input("รหัสเข้าใช้งานที่ผู้ดูแลแจ้งให้", type="password")
        submitted = st.form_submit_button("เข้าสู่ระบบ")
    if submitted:
        if hmac.compare_digest(entered, password):
            st.session_state["authorized"] = True
            st.rerun()
        else:
            st.error("รหัสไม่ถูกต้อง")
    st.stop()

file_path = BASE / "data.xlsx"
if not file_path.exists():
    file_path = BASE / "data.csv"
if not file_path.exists():
    st.error("ยังไม่มีไฟล์ data.xlsx หรือ data.csv ในโปรเจกต์")
    st.stop()
try:
    data = read_data(str(file_path), file_path.stat().st_mtime)
except Exception as e:
    st.error(f"อ่านไฟล์ข้อมูลไม่ได้: {e}")
    st.stop()
if data.empty:
    st.info("ไฟล์ข้อมูลยังไม่มีรายการ กรุณากรอกข้อมูลใน data.csv หรืออัปโหลด data.xlsx ไปยัง GitHub")
    st.stop()
if data["source"].str.contains("ข้อมูลจำลอง", regex=False).any():
    st.warning("⚠️ ขณะนี้เป็น **ข้อมูลจำลองเพื่อทดสอบ** ไม่ใช่ผลผลิตจริง กรุณาแทนที่ด้วยฐานข้อมูลของคุณก่อนเผยแพร่")

st.sidebar.header("📚 ฐานข้อมูล")
st.sidebar.metric("จำนวนระเบียน", f"{len(data):,}")
st.sidebar.write("ปีที่มีข้อมูล:", ", ".join(map(str, sorted(data["year"].unique().tolist()))))
st.sidebar.write("จังหวัด:", f"{data['province'].nunique()} จังหวัด")
st.sidebar.caption("แก้ไขไฟล์ data.xlsx หรือ data.csv ใน GitHub เพื่ออัปเดตข้อมูลถาวร")
with st.sidebar.expander("🔎 ดูข้อมูลด้วยตัวกรอง (ไม่ใช้ AI)"):
    selection = data.copy()
    for col, name in (("province", "จังหวัด"), ("district", "อำเภอ"), ("crop", "พืช")):
        selected = st.selectbox(name, ["ทั้งหมด"] + sorted(selection[col].dropna().astype(str).unique().tolist()))
        if selected != "ทั้งหมด":
            selection = selection.loc[selection[col] == selected]
    selected_year = st.selectbox("ปี", ["ทั้งหมด"] + sorted(selection["year"].unique().tolist(), reverse=True))
    if selected_year != "ทั้งหมด":
        selection = selection.loc[selection["year"] == selected_year]
    st.write(f"พบ {len(selection):,} รายการ")
    st.download_button("ดาวน์โหลดรายการที่กรอง", selection.to_csv(index=False).encode("utf-8-sig"), "filtered.csv", "text/csv")

with st.expander("📋 เปิดดูข้อมูลต้นทาง"):
    rename = {"province": "จังหวัด", "district": "อำเภอ", "crop": "พืช", "year": "ปี", "planted_rai": "เนื้อที่เพาะปลูก (ไร่)", "harvested_rai": "เนื้อที่เก็บเกี่ยว (ไร่)", "production_ton": "ผลผลิต (ตัน)", "yield_kg_rai": "ผลผลิตต่อไร่ (กก./ไร่)", "source": "แหล่งข้อมูล", "url": "ลิงก์อ้างอิง"}
    st.dataframe(data.rename(columns=rename), use_container_width=True, hide_index=True)

if "history" not in st.session_state:
    st.session_state["history"] = []
st.write("**ตัวอย่างคำถาม:** ‘อันดับ 5 อำเภอที่มีผลผลิตข้าวสูงสุดในขอนแก่น’ · ‘แนวโน้มผลผลิตอ้อยย้อนหลัง 5 ปี’ · ‘เปรียบเทียบผลผลิตข้าวแต่ละจังหวัด’")
key_present = bool(secret("GEMINI_API_KEY"))
if not key_present:
    st.info("ยังไม่ได้ตั้งค่า GEMINI_API_KEY — ตัวกรองด้านซ้ายใช้งานได้ฟรี ส่วนแชตจะเปิดหลังเพิ่ม API Key ใน Streamlit Secrets")
for idx, item in enumerate(st.session_state["history"]):
    with st.chat_message(item["role"]):
        st.markdown(item["content"])
        if item.get("result") is not None:
            show_result(item["result"], item["metric_col"], f"history_{idx}")
question = st.chat_input("ถามข้อมูลผลผลิตพืชเศรษฐกิจเป็นภาษาไทย...", disabled=not key_present)
if question:
    with st.chat_message("user"):
        st.markdown(question)
    previous = st.session_state["history"][-6:]
    st.session_state["history"].append({"role": "user", "content": question})
    with st.chat_message("assistant"):
        with st.spinner("กำลังตีความคำถามและคำนวณจากข้อมูล..."):
            try:
                query = parse_question(question, data, previous)
                reply, table, metric_column = answer_query(data, query)
            except Exception as e:
                reply = "ยังประมวลผลคำถามนี้ไม่ได้ อาจติดโควตา API หรือการตั้งค่าไม่สมบูรณ์ ลองใหม่ หรือใช้ตัวกรองด้านซ้ายก่อน"
                table, metric_column = None, None
                st.caption(f"ประเภทข้อผิดพลาด: {type(e).__name__}")
        st.markdown(reply)
        if table is not None:
            show_result(table, metric_column, f"current_{len(st.session_state['history'])}")
    st.session_state["history"].append({"role": "assistant", "content": reply, "result": table, "metric_col": metric_column})
