"""AgriData AI V3.1: chat-first home + lower yield dashboard, local CSV.
Upload app.py + requirements.txt + data.csv + province_data.csv to GitHub root.
"""
import hmac
import html
import io
import json
import re
from pathlib import Path

import pandas as pd
import altair as alt
import streamlit as st

st.set_page_config(page_title="AgriData AI | ถามข้อมูลการเกษตร", page_icon="🌾", layout="wide", initial_sidebar_state="collapsed")
BASE = Path(__file__).resolve().parent
FILES = {"อำเภอ": "data.csv", "จังหวัด": "province_data.csv"}
NUMBERS = {"ผลผลิต (ตัน)": "ผลผลิต (ตัน)", "เนื้อที่เก็บเกี่ยว (ไร่)": "เนื้อที่เก็บเกี่ยว (ไร่)", "ผลผลิตต่อไร่ (กก./ไร่)": "ผลผลิตต่อไร่ (กก./ไร่)"}

def secret(key):
    try:
        return str(st.secrets.get(key, ""))
    except (FileNotFoundError, KeyError):
        return ""

@st.cache_data(show_spinner=False)
def load_csv(file_name, size, modified):
    # size & modified invalidate the Streamlit data cache when a file changes.
    df = pd.read_csv(BASE / file_name, encoding="utf-8-sig", dtype={"จังหวัด": str, "อำเภอ": str, "พืช": str}, low_memory=False)
    for field in ["จังหวัด", "อำเภอ", "พืช"]:
        if field in df:
            df[field] = df[field].fillna("").astype(str).str.strip()
    if "ปี" in df:
        df["ปี"] = df["ปี"].fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        df["ปีเรียงลำดับ"] = pd.to_numeric(df["ปี"].str.extract(r"(25\d{2}|20\d{2})")[0], errors="coerce")
    for field in NUMBERS:
        if field in df:
            df[field] = pd.to_numeric(df[field].astype(str).str.replace(",", "", regex=False), errors="coerce")
    # Derive yield only when both measured production and harvested area exist.
    area = df.get("เนื้อที่เก็บเกี่ยว (ไร่)")
    production = df.get("ผลผลิต (ตัน)")
    if area is not None and production is not None:
        calc = (production * 1000 / area.where(area > 0))
        if "ผลผลิตต่อไร่ (กก./ไร่)" not in df:
            df["ผลผลิตต่อไร่ (กก./ไร่)"] = calc
        else:
            df["ผลผลิตต่อไร่ (กก./ไร่)"] = df["ผลผลิตต่อไร่ (กก./ไร่)"].fillna(calc)
    return df

def data_for(level):
    filename = FILES[level]
    path = BASE / filename
    if not path.exists():
        return None
    return load_csv(filename, path.stat().st_size, path.stat().st_mtime_ns)

def norm(s):
    return re.sub(r"\s+", "", str(s)).replace("จังหวัด", "").replace("อำเภอ", "")

def extract_filter(question, values):
    candidates = sorted((v for v in values if str(v).strip()), key=lambda x: len(str(x)), reverse=True)
    normalized_question = norm(question)
    return next((item for item in candidates if norm(item) in normalized_question), None)

# Normalize common Thai crop names before using optional AI filters.
# An explicitly named crop always takes priority; never show all crops if a requested crop is absent.
CROP_ALIASES = {
    "อ้อย": ["อ้อยโรงงาน", "อ้อย"],
    "อ้อยโรงงาน": ["อ้อยโรงงาน", "อ้อย"],
    "มัน": ["มันสำปะหลังโรงงาน", "มันสำปะหลัง"],
    "มันสำปะหลัง": ["มันสำปะหลังโรงงาน", "มันสำปะหลัง"],
    "มันสำปะหลังโรงงาน": ["มันสำปะหลังโรงงาน", "มันสำปะหลัง"],
    "ยาง": ["ยางพารา"],
    "ยางพารา": ["ยางพารา"],
    "ข้าวนาปี": ["ข้าวนาปี"],
    "นาปี": ["ข้าวนาปี"],
    "ข้าวนาปรัง": ["ข้าวนาปรัง"],
    "นาปรัง": ["ข้าวนาปรัง"],
}

def resolve_crop(question, options):
    """Return (chosen crop, explicitly_requested). No silent fallback to unrelated crops."""
    q = re.sub(r"\s+", "", str(question))
    options_by_norm = {re.sub(r"\s+", "", str(o)): o for o in options}
    # Longer names first, so 'อ้อยโรงงาน' wins over 'อ้อย' and 'ข้าวนาปรัง' over 'นาปรัง'.
    for alias in sorted(CROP_ALIASES, key=len, reverse=True):
        if alias not in q:
            continue
        for target in CROP_ALIASES[alias]:
            key = re.sub(r"\s+", "", target)
            if key in options_by_norm:
                return options_by_norm[key], True
        return None, True
    if "ข้าว" in q and "นาปี" not in q and "นาปรัง" not in q:
        return None, True  # rice type is ambiguous, require clarification
    # Other exact crop names in this particular data set.
    direct = extract_filter(question, options)
    if direct:
        return direct, True
    return None, False

def available_year(question, df):
    # Return requested B.E./C.E. year as a text token; source years may be crop seasons such as 2565/2566.
    m = re.search(r"(?<!\d)(25\d{2}|20\d{2})(?!\d)", question)
    if not m:
        return None
    y = int(m.group(1))
    if y < 2200 and (df["ปีเรียงลำดับ"].dropna() >= 2400).any():
        y += 543
    return str(y)

def period_matches(period, year):
    # A season labelled 2565/2566 should match either 2565 or 2566, but not 2567.
    text = str(period).strip()
    if text == year:
        return True
    parts = re.split(r"[/-]", text)
    if len(parts) < 2:
        return False
    try:
        first = int(parts[0]); second = int(parts[1])
        if second < 100:
            second = (first // 100) * 100 + second
        return int(year) in (first, second)
    except ValueError:
        return False

def no_results(raw, level, question):
    crops = ', '.join(sorted(raw['พืช'].dropna().astype(str).unique()))
    years = ', '.join(sorted(raw['ปี'].dropna().astype(str).unique()))
    return (f"ไม่พบข้อมูลระดับ{level} ที่ตรงครบทุกเงื่อนไข\n\n"
            f"**ปีที่มีในฐานข้อมูลระดับนี้:** {years}\n\n"
            f"**พืชที่มี:** {crops}\n\n"
            "หมายเหตุ: ปีเพาะปลูกอาจเขียนเป็น 2565/2566 หรือ 2560/61 "
            "ซึ่งไม่ใช่ปีข้อมูล 2567; ลองถามโดยไม่ระบุปี หรือใช้ตัวกรองด้านซ้าย")

def try_ai(question, df, level):
    """AI is optional; it only chooses filters, never produces statistics."""
    key = secret("GEMINI_API_KEY")
    if not key.strip():
        return {}
    try:
        from google import genai
        from google.genai import types
        import json
        values = {c: sorted(df[c].dropna().astype(str).unique().tolist()) for c in ("จังหวัด", "พืช") if c in df}
        if level == "อำเภอ":
            values["อำเภอ"] = sorted(df["อำเภอ"].dropna().astype(str).unique().tolist())
        prompt = f'''Extract structured filters for a Thai agricultural database. Output JSON only with optional keys province, district, crop, year, metric, group, top_n. Exact strings MUST be from options. If unclear use null, not guesses. crop 'ข้าวนาปี' is distinct from 'ข้าวนาปรัง'. metric one of production, area, yield. group one of district, province, crop, year, none. Do not invent statistics. Options: {json.dumps(values,ensure_ascii=False)}. Question: {question}'''
        answer = genai.Client(api_key=key).models.generate_content(model="gemini-2.5-flash-lite", contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0))
        parsed = json.loads(answer.text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}

def evidence_note(rows, level):
    """Explain observed district figures vs estimates using province-wide mean yield."""
    if level != "อำเภอ" or rows is None or rows.empty:
        return ""
    status = rows["สถานะข้อมูล"].fillna("").astype(str) if "สถานะข้อมูล" in rows else pd.Series([], dtype=str)
    method = rows["วิธีได้มา"].fillna("").astype(str) if "วิธีได้มา" in rows else pd.Series([], dtype=str)
    estimated = status.str.contains("พื้นที่เก็บเกี่ยวอำเภอ × ผลผลิตเฉลี่ยจังหวัด", regex=False) | method.str.contains("ผลผลิตเฉลี่ยจังหวัด สศก.", regex=False)
    derived = status.str.contains("คำนวณรวมจากข้อมูลจริงรายอำเภอ", regex=False)
    fixed = status.str.contains("อัตราผลผลิตคงที่", regex=False)
    direct = ~(estimated | derived | fixed)
    notes = ["**คำอธิบายที่มาของตัวเลขรายอำเภอ** (ดูคอลัมน์ ‘คำอธิบายข้อมูล’ ประกอบ)"]
    if direct.any(): notes.append(f"• ข้อมูลรายอำเภอจากเอกสาร: {int(direct.sum())} รายการ — เป็นค่าที่ระบุในเอกสารระดับอำเภอหรือหน่วยงานที่เกี่ยวข้อง ไม่ได้หมายความว่า สศก. รับรองค่ารายอำเภอทุกค่า")
    if derived.any(): notes.append(f"• ผลรวมจากรายการรายอำเภอ: {int(derived.sum())} รายการ — คำนวณรวมรายการย่อยที่มีข้อมูลระดับอำเภอ")
    if estimated.any(): notes.append(f"• **ผลผลิตรายอำเภอประมาณการโดยใช้อัตราผลผลิตเฉลี่ยระดับจังหวัดของ สศก.: {int(estimated.sum())} รายการ** — นำพื้นที่เก็บเกี่ยวรายอำเภอ × ผลผลิตเฉลี่ยจังหวัด ÷ 1,000 **ไม่ใช่ผลผลิตรายอำเภอที่ สศก. รายงานโดยตรง และไม่ใช่การนำยอดรวมจังหวัดมาใส่แทนรายอำเภอ**")
    if fixed.any(): notes.append(f"• ผลผลิตคำนวณตามอัตราคงที่ของรายงานท้องถิ่น: {int(fixed.sum())} รายการ — เป็นค่าคำนวณ ไม่ใช่ผลสำรวจผลผลิตจริง")
    notes.append("⚠️ รายการประมาณการไม่ควรใช้จัดอันดับ/อ้างว่าเป็นผลผลิตจริงรายอำเภอโดยไม่ระบุวิธีประมาณ; โปรดตรวจปีข้อมูลและหลักฐานก่อนใช้ในรายงานราชการ")
    return "\n\n" + "\n\n".join(notes)

def add_explanation(df, level):
    df = df.copy()
    if level == "จังหวัด":
        df["คำอธิบายข้อมูล"] = "ข้อมูลสรุประดับจังหวัดจากเอกสารอ้างอิง สศก."
        return df
    if "สถานะข้อมูล" not in df: return df
    status = df["สถานะข้อมูล"].fillna("").astype(str)
    descriptions = []
    for val in status:
        if "พื้นที่เก็บเกี่ยวอำเภอ × ผลผลิตเฉลี่ยจังหวัด" in val:
            descriptions.append("ประมาณการระดับอำเภอจากพื้นที่เก็บเกี่ยวอำเภอ × ผลผลิตเฉลี่ยระดับจังหวัด สศก. ÷ 1,000; ไม่ใช่ตัวเลขผลผลิตรายอำเภอที่ สศก. รายงานโดยตรง")
        elif "คำนวณรวมจากข้อมูลจริงรายอำเภอ" in val:
            descriptions.append("คำนวณรวมจากรายการข้อมูลจริงในระดับอำเภอ (โปรดดูวิธีได้มา)")
        elif "อัตราผลผลิตคงที่" in val:
            descriptions.append("ค่าคำนวณตามอัตราผลผลิตคงที่จากรายงานท้องถิ่น ไม่ใช่ผลผลิตที่สำรวจโดยตรง")
        else:
            descriptions.append("ผลผลิตระดับอำเภอตามเอกสารอ้างอิง โปรดตรวจปีและประเภทหลักฐาน")
    df["คำอธิบายข้อมูล"] = descriptions
    return df

YIELD_EXPLANATION = ("📏 **ผลผลิตต่อไร่** = ผลผลิต (ตัน) × 1,000 ÷ พื้นที่เก็บเกี่ยว (ไร่) "
                     "หน่วย กก./ไร่ · กรณีประมาณการ อาจใช้ค่าเฉลี่ยระดับจังหวัด")

def with_yield_note(message, table=None):
    """A one-line note for successful data answers; never repeat long text on errors."""
    message = str(message).strip()
    if table is None or getattr(table, "empty", True):
        return message
    return message + "\n\n" + YIELD_EXPLANATION

# --- Conversational search: deterministic context resolution, no invented statistics ---
RICE_TYPES = ("ข้าวนาปี", "ข้าวนาปรัง")


def rice_is_generic(question):
    q = str(question).replace(" ", "")
    return "ข้าว" in q and not any(x in q for x in ("ข้าวนาปี", "ข้าวนาปรัง", "นาปี", "นาปรัง"))


def mentioned_crop(question, choices):
    value, explicit = resolve_crop(question, choices)
    return value if explicit else None


def build_conversational_query(question, context, district_df, province_df):
    """Fill missing dimensions from chat state; return (query, pending_reply, new_context).
    The original user's wording is preserved, while inherited dimensions are appended.
    """
    user_q = str(question).strip()
    ctx = dict(context or {})
    crops = sorted(set(district_df["พืช"].dropna().tolist()) | set(province_df["พืช"].dropna().tolist()))
    provinces = sorted(set(district_df["จังหวัด"].dropna().tolist()) | set(province_df["จังหวัด"].dropna().tolist()))
    districts = sorted(set(district_df["อำเภอ"].dropna().tolist()))
    crop, _ = resolve_crop(user_q, crops)
    prov = extract_filter(user_q, provinces)
    dist = extract_filter(user_q, districts)
    clear_context = any(w in user_q for w in ("เริ่มใหม่", "ล้างบริบท", "เปลี่ยนเรื่อง"))
    if clear_context:
        ctx = {}
    asked_rice = rice_is_generic(user_q)
    # 'ข้าว' without any location is a genuine choice; keep short.
    if asked_rice and not prov and not dist and not ctx.get("province"):
        return user_q, "🌾 ต้องการ **ข้าวนาปี** หรือ **ข้าวนาปรัง** ครับ?", {"pending_rice": True}
    # A province-specific generic rice query means two separate crop rows, not adding them.
    if asked_rice:
        ctx.pop("crop", None)
        ctx["rice_both"] = True
        ctx.pop("pending_rice", None)
    if crop:
        ctx["crop"] = crop
        ctx.pop("rice_both", None)
        ctx.pop("pending_rice", None)
    if prov:
        if prov != ctx.get("province"):
            ctx.pop("district", None)
        ctx["province"] = prov
    if dist:
        ctx["district"] = dist
        # If the district's province is unique, resolve it from the data.
        matched = district_df.loc[district_df["อำเภอ"] == dist, "จังหวัด"].dropna().unique()
        if len(matched) == 1:
            ctx["province"] = matched[0]
    if "รายอำเภอ" in user_q or "แต่ละอำเภอ" in user_q or "ขอรายอำเภอ" in user_q or dist:
        ctx["level"] = "อำเภอ"
    elif "ระดับจังหวัด" in user_q or "รวมจังหวัด" in user_q or "ทั้งจังหวัด" in user_q:
        ctx["level"] = "จังหวัด"
        ctx.pop("district", None)
    elif "จังหวัด" in user_q and "อำเภอ" not in user_q:
        ctx["level"] = "จังหวัด"
        ctx.pop("district", None)
    ctx.setdefault("level", "จังหวัด")
    year_match = re.search(r"(?<!\d)(25\d{2}|20\d{2})(?!\d)", user_q)
    if year_match:
        ctx["year"] = year_match.group(1)
    elif "ปีล่าสุด" in user_q or "ล่าสุด" in user_q or "ไม่ระบุปี" in user_q:
        ctx.pop("year", None)
    if ctx.get("pending_rice") and not crop:
        return user_q, "🌾 ต้องการ **ข้าวนาปี** หรือ **ข้าวนาปรัง** ครับ?", ctx
    # Query synthesis: never pass stale crop from prior question if a new crop named.
    suffix = []
    if ctx.get("province") and not prov:
        suffix.append("จังหวัด" + ctx["province"])
    if ctx.get("district") and not dist and ctx.get("level") == "อำเภอ":
        suffix.append("อำเภอ" + ctx["district"])
    if ctx.get("crop") and not crop and not asked_rice:
        suffix.append(ctx["crop"])
    if ctx.get("level") == "อำเภอ" and "อำเภอ" not in user_q:
        suffix.append("รายอำเภอ")
    if ctx.get("level") == "จังหวัด" and "อำเภอ" not in user_q and "จังหวัด" not in user_q:
        suffix.append("ระดับจังหวัด")
    if ctx.get("year") and not year_match and "ล่าสุด" not in user_q:
        suffix.append("ปี " + ctx["year"])
    query = user_q + " " + " ".join(suffix)
    return query, None, ctx


def answer_both_rice(question, district_df, province_df):
    """Display napee/naprang independently at province scope, with no addition."""
    level = "อำเภอ" if "รายอำเภอ" in question or "แต่ละอำเภอ" in question else "จังหวัด"
    raw = district_df if level == "อำเภอ" else province_df
    prov = extract_filter(question, raw["จังหวัด"].dropna().unique())
    if not prov:
        return "ต้องการข้อมูลข้าวของ **จังหวัดไหน** ครับ?", None, None
    subset = raw[(raw["จังหวัด"] == prov) & (raw["พืช"].isin(RICE_TYPES))].copy()
    if subset.empty:
        return f"ไม่พบข้อมูลข้าวนาปีหรือข้าวนาปรังระดับ{level}ของ{prov}", None, None
    year = available_year(question, raw)
    if year:
        subset = subset[subset["ปี"].apply(lambda x: period_matches(x,year))]
    elif "ย้อนหลัง" not in question and "ทุกปี" not in question:
        keys=["จังหวัด","พืช"]+(["อำเภอ"] if level=="อำเภอ" else [])
        subset=subset[subset["ปีเรียงลำดับ"]==subset.groupby(keys,dropna=False)["ปีเรียงลำดับ"].transform("max")]
    if subset.empty:
        return f"ไม่พบข้อมูลข้าวนาปีหรือข้าวนาปรังของ{prov} ในปีที่ระบุ",None,None
    subset=add_explanation(subset,level)
    cols=[c for c in ["จังหวัด","อำเภอ","พืช","ปี","เนื้อที่เก็บเกี่ยว (ไร่)","ผลผลิต (ตัน)","ผลผลิตต่อไร่ (กก./ไร่)","คำอธิบายข้อมูล","สถานะข้อมูล","แหล่งข้อมูล","อ้างอิง สศก.","ลิงก์อ้างอิง","URL สศก."] if c in subset]
    table=subset[cols].sort_values([c for c in ["พืช","อำเภอ","ปี"] if c in cols])
    years=sorted(subset["ปี"].astype(str).unique())
    warning=" · ⚠️ ข้อมูลอาจคนละปี" if len(years)>1 else ""
    return (f"🌾 **ข้าวนาปีและข้าวนาปรัง จังหวัด{prov}** — แสดงแยกประเภท ไม่รวมยอด{warning}\n\n"
            + f"ปีข้อมูล: {', '.join(years)}"), table, "ผลผลิต (ตัน)"


def answer(question, district_df, province_df):
    level = "อำเภอ" if "อำเภอ" in question or "รายอำเภอ" in question else "จังหวัด"
    if "จังหวัด" in question and "อำเภอ" in question:
        level = "อำเภอ"
    raw = district_df if level == "อำเภอ" else province_df
    if raw is None or raw.empty:
        return f"ไม่มีไฟล์ข้อมูลระดับ{level} กรุณาอัปโหลด {FILES[level]}", None, None
    df = raw.copy()
    ai = try_ai(question, df, level)
    shown = []
    for field, ai_field in [("จังหวัด", "province"), ("อำเภอ", "district"), ("พืช", "crop")]:
        if field not in df:
            continue
        choices = df[field].dropna().astype(str).unique().tolist()
        if field == "พืช":
            chosen, explicitly_requested = resolve_crop(question, choices)
            # If a recognized crop has no corresponding database entry, return an empty result;
            # never ignore the user's crop filter and display other crops instead.
            if explicitly_requested and chosen is None:
                if "ข้าว" in question and "นาปี" not in question and "นาปรัง" not in question:
                    return "กรุณาระบุ **ข้าวนาปี** หรือ **ข้าวนาปรัง** เพื่อป้องกันการรวมข้อมูลคนละประเภท", None, None
                return (f"ไม่พบพืชที่ระบุในฐานข้อมูลระดับ{level}\n\n"
                        f"พืชที่มี: {', '.join(sorted(choices))}"), None, None
        else:
            chosen = extract_filter(question, choices)
        ai_candidate = ai.get(ai_field)
        if not chosen and field == "พืช" and not explicitly_requested:
            # AI can assist for unrecognized crop words, but cannot override explicit filters.
            if ai_candidate in choices:
                chosen = ai_candidate
        elif not chosen and field != "พืช" and ai_candidate in choices:
            chosen = ai_candidate
        if chosen:
            df = df[df[field] == chosen]
            shown.append(f"{field}: {chosen}")
    year = available_year(question, raw)
    if year is None and ai.get("year") is not None:
        try:
            year = str(int(ai["year"]))
        except (TypeError, ValueError):
            pass
    if year is not None:
        df = df[df["ปี"].apply(lambda x: period_matches(x, year))]
        shown.append(f"ปี: {year}")
    else:
        # Avoid merging unlike year periods; per province/crop choose latest available year.
        keys = ["จังหวัด", "พืช"] + (["อำเภอ"] if level == "อำเภอ" else [])
        if "ย้อนหลัง" not in question and "ทุกปี" not in question and "แนวโน้ม" not in question:
            df = df[df["ปีเรียงลำดับ"] == df.groupby(keys, dropna=False)["ปีเรียงลำดับ"].transform("max")]
            shown.append("ปีล่าสุดแยกตามรายการ (อาจต่างปีกัน)")
    if df.empty:
        return no_results(raw, level, question), None, None
    if "ต่อไร่" in question or "กก./ไร่" in question:
        metric = "ผลผลิตต่อไร่ (กก./ไร่)"
    elif "พื้นที่" in question or "เนื้อที่" in question or ai.get("metric") == "area":
        metric = "เนื้อที่เก็บเกี่ยว (ไร่)"
    elif ai.get("metric") == "yield":
        metric = "ผลผลิตต่อไร่ (กก./ไร่)"
    else:
        metric = "ผลผลิต (ตัน)"
    if metric not in df.columns:
        return f"ไฟล์ระดับ{level} ไม่มีคอลัมน์ {metric}", None, None
    valid = df[df[metric].notna()].copy()
    if valid.empty:
        return f"มีรายการที่ตรงกับคำถาม แต่ไม่มีตัวเลข {metric} ในไฟล์ต้นทาง", None, None
    # Never add separate years or crops by default. Preserve individual records.
    valid = add_explanation(valid, level)
    cols = [c for c in ["คำอธิบายข้อมูล", "จังหวัด", "อำเภอ", "พืช", "ปี", "เนื้อที่เก็บเกี่ยว (ไร่)", "ผลผลิต (ตัน)", "ผลผลิตต่อไร่ (กก./ไร่)", "สถานะข้อมูล", "วิธีได้มา", "ประเภทหลักฐาน", "แหล่งข้อมูล", "อ้างอิง สศก.", "ลิงก์อ้างอิง", "URL สศก.", "ข้อจำกัด", "ไฟล์ต้นฉบับ"] if c in valid]
    result = valid[cols].copy()
    wants_ranking = any(term in question for term in ("อันดับ", "มากที่สุด", "สูงสุด", "น้อยที่สุด", "ต่ำสุด", "เปรียบเทียบ", "เทียบ", "จังหวัดไหน", "อำเภอไหน"))
    years_compared = sorted(str(v) for v in valid["ปี"].dropna().unique())
    if wants_ranking and len(years_compared)>1:
        comparison_warning = "⛔ **ยังไม่จัดอันดับหรือสรุปผู้ชนะ** เพราะรายการที่ค้นพบมีข้อมูลต่างปี/ฤดูเพาะปลูก กรุณาระบุปีเดียวกัน หรือดูข้อมูลแยกปีในตาราง"
    else:
        comparison_warning = ""
    if wants_ranking and not comparison_warning:
        result = result.sort_values(metric, ascending=True if "น้อยที่สุด" in question else False)
        match = re.search(r"(?:อันดับ|สูงสุด|มากที่สุด)\s*(\d{1,2})", question)
        top = int(match.group(1)) if match else 10
        result = result.head(max(1, min(top, 50)))
    else:
        result = result.sort_values([c for c in ["จังหวัด", "อำเภอ", "พืช", "ปี"] if c in result])
    years = sorted(str(v) for v in valid["ปี"].dropna().unique())
    header = f"พบ **{len(valid):,} รายการ** ระดับ**{level}** | ปีข้อมูล: {', '.join(years)}"
    if shown:
        header += "\n\nเงื่อนไข: " + " · ".join(shown)
    header += "\n\nแสดงค่าตามรายการในฐานข้อมูล ไม่รวมยอดข้ามปี/ข้ามพืช และไม่ใช้ยอดรวมจังหวัดแทนยอดอำเภอ"
    if comparison_warning:
        header += "\n\n" + comparison_warning
    if level == "อำเภอ":
        short_sources = valid.apply(lambda row: source_short(row, level), axis=1).dropna().unique().tolist()
        if short_sources:
            header += "\n\n📚 **ที่มา:** " + " · ".join(str(x) for x in short_sources[:2])
            if len(short_sources) > 2:
                header += " (ดูที่มาเพิ่มเติมในตาราง)"
    if len(years) > 1:
        header += "\n\n⚠️ รายการอาจมีปีข้อมูลต่างกัน ไม่ควรใช้จัดอันดับเปรียบเทียบโดยตรงจนกว่าจะเลือกปีเดียวกัน"
    return header, result, metric


# --- V2: evidence QA, export, dashboard, and human-readable comparisons ---
def audit(df, level):
    if df is None or df.empty:
        return pd.DataFrame(columns=["ระดับ", "แถว", "ประเด็น", "รายละเอียด"])
    problems=[]
    for i, row in df.iterrows():
        keys=[str(row.get(c, "")).strip() for c in ("จังหวัด", "พืช", "ปี")]
        if any(not x or x=="nan" for x in keys):
            problems.append([level, i+2, "ข้อมูลระบุตัวตนไม่ครบ", "จังหวัด/พืช/ปีต้องไม่ว่าง"])
        area=pd.to_numeric(row.get("เนื้อที่เก็บเกี่ยว (ไร่)"),errors="coerce")
        prod=pd.to_numeric(row.get("ผลผลิต (ตัน)"),errors="coerce")
        yieldval=pd.to_numeric(row.get("ผลผลิตต่อไร่ (กก./ไร่)"),errors="coerce")
        if (pd.notna(area) and area<0) or (pd.notna(prod) and prod<0) or (pd.notna(yieldval) and yieldval<0):
            problems.append([level,i+2,"ค่าติดลบ","พบค่าตัวเลขติดลบ"])
        if pd.notna(area) and pd.notna(prod) and pd.notna(yieldval) and area>0:
            computed=prod*1000/area
            if abs(computed-yieldval)>max(5, 0.02*abs(computed)):
                problems.append([level,i+2,"ผลผลิตต่อไร่ไม่สอดคล้อง",f"คำนวณได้ {computed:,.2f} กก./ไร่ แต่ข้อมูลระบุ {yieldval:,.2f}"])
        if pd.notna(prod) and (pd.isna(area) or area==0):
            # missing area is common and NOT automatically a data error; no warning
            pass
    cols=["จังหวัด", "พืช", "ปี"] + (["อำเภอ"] if level=="อำเภอ" else [])
    if set(cols).issubset(df.columns):
        for idx in df.index[df.duplicated(cols,keep=False)]:
            problems.append([level,int(idx)+2,"ระเบียนซ้ำ"," / ".join(str(df.loc[idx,c]) for c in cols)])
    return pd.DataFrame(problems,columns=["ระดับ", "แถว", "ประเด็น", "รายละเอียด"])

def to_excel_bytes(df):
    output=io.BytesIO()
    with pd.ExcelWriter(output,engine="xlsxwriter") as writer:
        df.to_excel(writer,index=False,sheet_name="ข้อมูล")
        ws=writer.sheets["ข้อมูล"]
        ws.freeze_panes(1,0)
        ws.autofilter(0,0,max(len(df),1),max(len(df.columns)-1,0))
        for ix,col in enumerate(df.columns):
            ws.set_column(ix,ix,min(max(len(str(col))+3,16),45))
        numeric_format=writer.book.add_format({"num_format":"#,##0.00"})
        for ix,col in enumerate(df.columns):
            if col in NUMBERS:
                ws.set_column(ix,ix,23,numeric_format)
    return output.getvalue()

def download_data(df, name, key):
    st.download_button("📗 ดาวน์โหลด Excel",to_excel_bytes(df),f"{name}.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",key=f"xlsx_{key}")
    st.download_button("📥 ดาวน์โหลด CSV",df.to_csv(index=False).encode("utf-8-sig"),f"{name}.csv",mime="text/csv",key=f"csv_{key}")

def display_table(table, prefix):
    opts={col: st.column_config.NumberColumn(col,format="%.2f") for col in NUMBERS if col in table.columns}
    st.dataframe(table,hide_index=True,use_container_width=True,column_config=opts)
    download_data(table,prefix,prefix)

# --- V3 Dashboard: Yield-first design with concise provenance ---
YIELD_COL = "ผลผลิตต่อไร่ (กก./ไร่)"
PRODUCTION_COL = "ผลผลิต (ตัน)"
AREA_COL = "เนื้อที่เก็บเกี่ยว (ไร่)"
FEATURED_CROPS = ["ข้าวนาปี", "ข้าวนาปรัง", "อ้อยโรงงาน", "มันสำปะหลังโรงงาน", "ยางพารา"]


def valid_reference(value):
    value = str(value or "").strip()
    return value if re.match(r"^https?://[^\s]+$", value) else ""


def source_short(row, level):
    """Source classification derived from *the actual record*, never from the question."""
    if level == "จังหวัด":
        ref = str(row.get("อ้างอิง สศก.", "") or "").strip()
        return "สศก." + (" · " + ref if ref else " · สถิติการเกษตร")
    status = str(row.get("สถานะข้อมูล", "") or "")
    method = str(row.get("วิธีได้มา", "") or "")
    evidence = str(row.get("ประเภทหลักฐาน", "") or "")
    if "พื้นที่เก็บเกี่ยวอำเภอ × ผลผลิตเฉลี่ยจังหวัด" in status or "ผลผลิตเฉลี่ยจังหวัด สศก." in method:
        return "ประมาณการ · พื้นที่อำเภอ × ค่าเฉลี่ย สศก. จังหวัด"
    if "อัตราผลผลิตคงที่" in status:
        return "คำนวณ · อัตราคงที่ตามรายงานท้องถิ่น"
    if "คำนวณรวมจากข้อมูลจริงรายอำเภอ" in status:
        return "คำนวณรวม · จากเอกสารระดับอำเภอ"
    if "สำนักงานเศรษฐกิจการเกษตร" in evidence or "สศก." in evidence:
        return "สศก. · ข้อมูลระดับอำเภอ"
    if "สำนักงานเกษตรจังหวัด" in evidence:
        return "สำนักงานเกษตรจังหวัด · รายงานระดับอำเภอ"
    if "สำนักงานสถิติจังหวัด" in evidence:
        return "สำนักงานสถิติจังหวัด · ตารางรายอำเภอ"
    if "การยางแห่งประเทศไทย" in evidence:
        return "การยางแห่งประเทศไทย · รายงานรายอำเภอ"
    if "สถิติตามรายงานรายอำเภอ" in evidence:
        return "รายงานสถิติระดับอำเภอ"
    return "เอกสารผลผลิตระดับอำเภอ (ดูแหล่งอ้างอิง)"


def prepare_dashboard_rows(df, level):
    """Keep raw observations intact; never sum yields or substitute totals across levels."""
    frame = df.copy()
    frame["ที่มา (สั้น)"] = frame.apply(lambda row: source_short(row, level), axis=1)
    frame["เอกสารอ้างอิง"] = frame.apply(
        lambda row: valid_reference(row.get("ลิงก์อ้างอิง", "")) or valid_reference(row.get("URL สศก.", "")), axis=1
    )
    if level == "จังหวัด":
        frame["พื้นที่แสดง"] = frame["จังหวัด"].astype(str)
    else:
        frame["พื้นที่แสดง"] = frame["อำเภอ"].astype(str) + " · " + frame["จังหวัด"].astype(str)
    return frame


def yield_summary_rows(frame, level):
    """Weighted pooled yield is a DERIVED value for the selected rows only.

    Published provincial yields remain unchanged in their individual rows.
    Missing-area district rows do not silently enter the pooled denominator.
    """
    if frame.empty:
        return {"yield": None, "area": None, "production": None, "eligible": 0, "total": 0, "method": "ไม่มีข้อมูล"}
    chosen = frame.loc[
        frame[AREA_COL].notna() & (frame[AREA_COL] > 0) & frame[PRODUCTION_COL].notna() & (frame[PRODUCTION_COL] >= 0)
    ].copy()
    if chosen.empty:
        return {"yield": None, "area": None, "production": None, "eligible": 0, "total": len(frame), "method": "ไม่มีพื้นที่เก็บเกี่ยวสำหรับคำนวณ"}
    area = float(chosen[AREA_COL].sum())
    production = float(chosen[PRODUCTION_COL].sum())
    # For an individual province use the official reported kg/rai (may be rounded
    # differently from an independently recomputed ratio).
    if level == "จังหวัด" and len(frame) == 1 and pd.notna(frame.iloc[0].get(YIELD_COL)):
        yield_val = float(frame.iloc[0][YIELD_COL])
        method = "ค่าผลผลิตต่อไร่ที่รายงานโดย สศก."
    else:
        yield_val = production * 1000 / area if area > 0 else None
        method = ("เฉลี่ยถ่วงน้ำหนักจากข้อมูลที่เลือก (คำนวณ)" if level == "จังหวัด"
                  else "คำนวณจากรายการอำเภอที่มีพื้นที่และผลผลิต")
    return {"yield": yield_val, "area": area, "production": production,
            "eligible": len(chosen), "total": len(frame), "method": method}


def dashboard_number(value, unit="", decimals=2):
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):,.{decimals}f}" + (" " + unit if unit else "")


def render_metric_cards(stats, area_count, level):
    # `safe` escapes dynamic display values before embedding them into HTML.
    def card(label, value, unit, note, focused=False):
        return (f'<div class="agri-kpi {"agri-kpi-main" if focused else ""}">'
                f'<div class="agri-kpi-label">{html.escape(label)}</div>'
                f'<div class="agri-kpi-value">{html.escape(dashboard_number(value))}'
                f'<span>{html.escape(unit)}</span></div>'
                f'<div class="agri-kpi-note">{html.escape(note)}</div></div>')
    coverage = f"{stats['eligible']:,}/{stats['total']:,} รายการมีพื้นที่และผลผลิต"
    method = stats["method"]
    # Production/area totals are limited to records usable for a comparable kg/rai.
    # Clearly say so, rather than labeling them as complete province totals.
    html_cards = (
        '<div class="agri-kpi-grid">'
        + card("ผลผลิตต่อไร่ · ตัวชี้วัดหลัก", stats["yield"], "กก./ไร่", method, True)
        + card("ผลผลิตจากรายการที่คำนวณได้", stats["production"], "ตัน", coverage)
        + card("พื้นที่เก็บเกี่ยวที่ใช้คำนวณ", stats["area"], "ไร่", "ไม่รวมรายการที่ไม่มีพื้นที่เก็บเกี่ยว")
        + card("รายการที่เปรียบเทียบได้", stats["eligible"], "รายการ", f"จากทั้งหมด {stats['total']:,} รายการ")
        + '</div>'
    )
    st.markdown(html_cards, unsafe_allow_html=True)


def plot_ranking(frame, metric, title, height_step=42, max_rows=12):
    plot = frame.dropna(subset=[metric]).copy()
    if plot.empty:
        st.info(f"ยังไม่มีตัวเลข {metric} สำหรับตัวกรองที่เลือก")
        return
    plot = plot.sort_values(metric, ascending=False).head(max_rows).copy()
    plot["ตัวเลข"] = plot[metric]
    tip = [alt.Tooltip("พื้นที่แสดง:N", title="พื้นที่"),
           alt.Tooltip("ตัวเลข:Q", title=metric, format=",.2f"),
           alt.Tooltip("ปี:N", title="ปี"),
           alt.Tooltip("ที่มา (สั้น):N", title="ที่มา")]
    bars = alt.Chart(plot).mark_bar(cornerRadiusEnd=6, size=19, color="#169675").encode(
        y=alt.Y("พื้นที่แสดง:N", sort="-x", title=None,
                axis=alt.Axis(labelLimit=260, labelFontSize=15, labelPadding=10)),
        x=alt.X("ตัวเลข:Q", title=metric, axis=alt.Axis(format=",.0f", gridColor="#e5ece8"),
                scale=alt.Scale(zero=True)), tooltip=tip,
    )
    st.altair_chart(bars.properties(height=min(480, max(260, len(plot)*height_step)), title=title), use_container_width=True)
    if len(frame.dropna(subset=[metric])) > max_rows:
        st.caption(f"กราฟแสดงสูงสุด {max_rows} อันดับ · ตารางด้านล่างมีข้อมูลครบ")


def dashboard(district, province):
    """V3 homepage: one crop & one exact year per comparison; provenance in each row."""
    st.markdown('<div class="agri-eyebrow">AGRICULTURAL DATA INTELLIGENCE</div>', unsafe_allow_html=True)
    st.markdown('## 📊 Dashboard ผลผลิตพืชเศรษฐกิจ')
    st.caption("เจาะลึกผลผลิตต่อไร่ (กก./ไร่) พร้อมปีและแหล่งอ้างอิง · เลือกพืชและปีเดียวกันเพื่อเปรียบเทียบ")
    sets = {name:df for name,df in (("จังหวัด",province),("อำเภอ",district)) if df is not None and not df.empty}
    if not sets:
        st.error("ไม่พบข้อมูลสำหรับ Dashboard")
        return
    total_prov = len(set().union(*(set(df["จังหวัด"].dropna()) for df in sets.values())))
    all_districts = int(district[["จังหวัด","อำเภอ"]].drop_duplicates().shape[0]) if district is not None else 0
    all_crops = len(set().union(*(set(df["พืช"].dropna()) for df in sets.values())))
    total_records = sum(len(df) for df in sets.values())
    st.caption(f"🗂️ ฐานข้อมูล: {total_prov:,} จังหวัด · {all_districts:,} อำเภอ · {all_crops:,} ชนิดพืช · {total_records:,} รายการ")

    level = st.radio("เปรียบเทียบระดับข้อมูล", list(sets), key="v3_level_choice", horizontal=True)
    raw = sets[level]
    crops = sorted(raw["พืช"].dropna().astype(str).unique().tolist())
    quick = [c for c in FEATURED_CROPS if c in crops]
    st.markdown("**เลือกพืชเศรษฐกิจ**")
    if quick:
        buttons=st.columns(len(quick))
        for i,crop_name in enumerate(quick):
            if buttons[i].button(crop_name, key=f"crop_shortcut_{i}", use_container_width=True):
                st.session_state["v3_crop_choice"] = crop_name
                st.session_state.pop("v3_year_choice", None)
                st.session_state.pop("v3_place_choice", None)
                st.rerun()
    if st.session_state.get("v3_crop_choice") not in crops:
        st.session_state["v3_crop_choice"] = "ข้าวนาปี" if "ข้าวนาปี" in crops else crops[0]
    a,b,c = st.columns([1.25,1,1.15])
    with a:
        crop = st.selectbox("ชนิดพืช", crops, key="v3_crop_choice")
    crop_rows = raw.loc[raw["พืช"] == crop].copy()
    years = sorted(crop_rows["ปี"].dropna().astype(str).unique().tolist(),
                   key=lambda x: (int(re.search(r"25\d{2}|20\d{2}",x).group()) if re.search(r"25\d{2}|20\d{2}",x) else 0,x),reverse=True)
    if not years:
        st.info("ไม่พบปีข้อมูลสำหรับพืชนี้")
        return
    if st.session_state.get("v3_year_choice") not in years:
        st.session_state["v3_year_choice"] = years[0]  # latest available crop year
    with b:
        year = st.selectbox("ปีข้อมูล / ฤดูเพาะปลูก", years, key="v3_year_choice")
    same = crop_rows[crop_rows["ปี"].astype(str) == year].copy()
    places = ["ทุกจังหวัด" if level=="จังหวัด" else "ทุกอำเภอ"] + sorted(same["จังหวัด"].dropna().astype(str).unique().tolist())
    if st.session_state.get("v3_place_choice") not in places:
        st.session_state["v3_place_choice"] = places[0]
    with c:
        province_choice = st.selectbox("พื้นที่ที่ต้องการดู", places, key="v3_place_choice")
    if province_choice not in ("ทุกจังหวัด", "ทุกอำเภอ"):
        same = same[same["จังหวัด"] == province_choice].copy()
    result = prepare_dashboard_rows(same,level)
    if result.empty:
        st.warning("ไม่พบข้อมูลที่ตรงเงื่อนไข")
        return

    # Source captions are short and tied to actual rows, not a blanket claim that
    # district data comes from OAE. Distinguish estimates from published district rows.
    st.markdown("#### 📏 ผลผลิตต่อไร่ — ตัวเลขสำคัญ")
    st.caption("กก./ไร่ = ผลผลิต (ตัน) × 1,000 ÷ เนื้อที่เก็บเกี่ยว (ไร่) · ค่ารายอำเภออาจเป็นข้อมูลจริงหรือประมาณการ ตรวจสอบได้ในตาราง")
    stats = yield_summary_rows(result, level)
    render_metric_cards(stats, len(result), level)
    kinds = result["ที่มา (สั้น)"].value_counts().index.tolist()
    if level == "จังหวัด":
        st.caption("📚 ที่มา: สำนักงานเศรษฐกิจการเกษตร (สศก.) · ดูเลขตารางและลิงก์เอกสารในตารางด้านล่าง"
                   + (" · ค่าเฉลี่ยรวมหลายจังหวัดระบบคำนวณถ่วงน้ำหนัก" if len(result)>1 else ""))
    else:
        desc = " · ".join(kinds[:3])
        st.caption(f"📚 **ที่มา:** {desc}" + (" · ดูรายละเอียดรายอำเภอในตาราง" if len(kinds)>3 else ""))
        if stats["eligible"] < stats["total"]:
            st.warning(f"ผลผลิตต่อไร่คำนวณได้ {stats['eligible']:,}/{stats['total']:,} รายการเท่านั้น; "
                       "รายการไม่มีพื้นที่เก็บเกี่ยวจะไม่ถูกนำมาคำนวณ")
        if any(result["ที่มา (สั้น)"].str.contains("ประมาณการ",regex=False)):
            st.info("บางอำเภอเป็น **ค่าประมาณการจากผลผลิตเฉลี่ยจังหวัด สศก.** ไม่ใช่ค่าที่สำรวจจริงของอำเภอนั้น")
    # Never treat absent provincial crop records as zero production.
    if level=="จังหวัด" and province_choice=="ทุกจังหวัด":
        all_provinces=set(province["จังหวัด"].astype(str).unique()) if province is not None else set()
        absent=sorted(all_provinces - set(same["จังหวัด"].astype(str).unique()))
        if absent:
            st.info(f"ไม่มีข้อมูล {crop} ปี {year} สำหรับ: {', '.join(absent)} (ไม่ได้หมายถึงผลผลิต 0)")

    st.markdown("#### 🏆 จัดอันดับผลผลิตต่อไร่")
    st.caption(f"{crop} · ปีข้อมูล {year} · ระดับ{level} — เปรียบเทียบเฉพาะปี/พืชเดียวกัน")
    if result["พื้นที่แสดง"].duplicated().any():
        st.warning("พบรายการซ้ำในพื้นที่เดียวกัน ระบบไม่รวมรายการซ้ำ กรุณาตรวจสอบในเมนูคุณภาพข้อมูล")
    plot_ranking(result, YIELD_COL, "ผลผลิตต่อไร่ (กก./ไร่)")

    st.markdown("#### 📊 ผลผลิตรวมและพื้นที่เก็บเกี่ยว")
    left,right=st.columns(2)
    with left:
        plot_ranking(result, PRODUCTION_COL, "ผลผลิตรวม (ตัน)", 30, 10)
    with right:
        plot_ranking(result, AREA_COL, "เนื้อที่เก็บเกี่ยว (ไร่)",30,10)
    st.caption("กราฟเปรียบเทียบจากข้อมูลแต่ละรายการ ไม่รวมข้ามชนิดพืชหรือข้ามปี และไม่ใช้ยอดจังหวัดแทนยอดอำเภอ")

    st.markdown("#### 📋 ตารางข้อมูลและแหล่งอ้างอิง")
    st.caption("ที่มาระบุแยกรายการ ไม่เหมารวมค่าประมาณการเป็นข้อมูลที่รายงานโดยตรง · เลือกเปิดเอกสารได้")
    show_columns=["จังหวัด"] + (["อำเภอ"] if level=="อำเภอ" else []) + [
        "พืช","ปี",YIELD_COL,PRODUCTION_COL,AREA_COL,"ที่มา (สั้น)","เอกสารอ้างอิง"]
    if "ปีเพาะปลูก" in result.columns:
        show_columns.insert(4,"ปีเพาะปลูก")
    table=result[show_columns].copy()
    # Ranking for district with missing area should not show fake zero yield.
    table=table.sort_values(YIELD_COL,ascending=False,na_position="last",kind="stable")
    fmt={col:st.column_config.NumberColumn(col,format="%.2f") for col in NUMBERS if col in table}
    fmt["เอกสารอ้างอิง"]=st.column_config.LinkColumn("เอกสารอ้างอิง",display_text="เปิดเอกสาร")
    st.dataframe(table,hide_index=True,use_container_width=True,column_config=fmt)
    xls_col,csv_col=st.columns(2)
    with xls_col:
        st.download_button("📗 ดาวน์โหลด Excel พร้อมแหล่งอ้างอิง",to_excel_bytes(result),
                           f"agridata_{level}_{crop}_{year}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key="v3_dashboard_excel",use_container_width=True)
    with csv_col:
        st.download_button("📥 ดาวน์โหลด CSV",result.to_csv(index=False).encode("utf-8-sig"),
                           f"agridata_{level}_{crop}_{year}.csv",mime="text/csv",
                           key="v3_dashboard_csv",use_container_width=True)
    with st.expander("ℹ️ วิธีอ่านผลผลิตต่อไร่และแหล่งข้อมูล"):
        st.markdown("**ระดับจังหวัด:** แสดงผลผลิตต่อไร่ตามเอกสาร สศก. เมื่อเลือกหลายจังหวัด ตัวเลขสรุปด้านบนเป็นค่าเฉลี่ยถ่วงน้ำหนักที่ระบบคำนวณจากผลผลิตและพื้นที่ของรายการที่เลือก ไม่ใช่ตัวเลขที่ สศก. รายงานรวมให้ 8 จังหวัด")
        st.markdown("**ระดับอำเภอ:** คำนวณ กก./ไร่ ได้เมื่อมีทั้งผลผลิตและพื้นที่เก็บเกี่ยวในปีที่แสดง บางรายการใช้ผลผลิตเฉลี่ยจังหวัดในการประมาณการ จึงต้องดูที่มาของแต่ละแถว")
        st.markdown("**ข้อมูลที่ขาด:** ช่องว่างหมายถึงไม่มีข้อมูลที่เพียงพอ ไม่ใช่ศูนย์ และไม่ควรตีความว่าผลผลิตต่อไร่ของอำเภอใดเท่ากับค่าเฉลี่ยจังหวัดโดยอัตโนมัติ")

# --- V3.1: conversational assistant first, analysis dashboard immediately below ---

def handle_user_question(question, district_data, province_data):
    if "chat" not in st.session_state:
        st.session_state.chat = []
    if "dialog_context" not in st.session_state:
        st.session_state.dialog_context = {}
    st.session_state.chat.append({"role": "user", "text": question})
    resolved, followup, context = build_conversational_query(
        question, st.session_state.dialog_context, district_data, province_data
    )
    st.session_state.dialog_context = context
    if followup is not None:
        reply, table, metric = followup, None, None
    elif context.get("rice_both") and rice_is_generic(resolved):
        reply, table, metric = answer_both_rice(resolved, district_data, province_data)
    elif context.get("rice_both") and not context.get("crop") and not any(
        x in question for x in ("นาปี", "นาปรัง")
    ):
        reply, table, metric = answer_both_rice(resolved + " ข้าว", district_data, province_data)
    else:
        reply, table, metric = answer(resolved, district_data, province_data)
    st.session_state.chat.append({"role": "assistant", "text": with_yield_note(reply, table), "table": table})


def show_chat(district_data, province_data):
    st.markdown('<div class="agri-section-label">01 / ASK AGRI</div>', unsafe_allow_html=True)
    st.markdown('## 💬 ถามข้อมูลพืชเศรษฐกิจได้เลย')
    st.markdown('<p class="agri-subtitle">พิมพ์คำถามสั้น ๆ ได้เหมือนคุยกับเจ้าหน้าที่ เช่น “อ้อยจังหวัดขอนแก่น” หรือ “ขอรายอำเภอด้วย”</p>', unsafe_allow_html=True)
    if "chat" not in st.session_state:
        st.session_state.chat = []
    if "dialog_context" not in st.session_state:
        st.session_state.dialog_context = {}

    examples = [
        ("🌾 ข้าวขอนแก่น", "ข้าวจังหวัดขอนแก่น"),
        ("🌱 อ้อยอุดรธานี", "ผลผลิตอ้อยจังหวัดอุดรธานี"),
        ("📏 ผลผลิตต่อไร่", "ผลผลิตต่อไร่ข้าวนาปีจังหวัดขอนแก่น"),
        ("📍 รายอำเภอ", "ข้าวนาปีรายอำเภอจังหวัดกาฬสินธุ์"),
    ]
    # Two rows of large, easy-to-tap suggestion buttons remain readable on phones.
    preset = None
    for start in (0, 2):
        suggestion_cols = st.columns(2)
        for offset, (label, example_question) in enumerate(examples[start:start + 2]):
            with suggestion_cols[offset]:
                if st.button(label, key=f"chat_example_{start + offset}", use_container_width=True):
                    preset = example_question

    with st.container(height=500, border=True, key="agri_chat_history"):
        if not st.session_state.chat:
            st.markdown("#### 👋 สวัสดีครับ ยินดีต้อนรับสู่ AgriData AI")
            st.markdown("ถามข้อมูลการเกษตรได้ทั้ง **ระดับจังหวัด** และ **รายอำเภอ** จากฐานข้อมูล 8 จังหวัด")
            st.markdown("**คำถามตัวอย่าง**: _ผลผลิตอ้อยขอนแก่นเป็นเท่าไหร่?_  หรือ  _อำเภอไหนมีผลผลิตต่อไร่สูงที่สุด?_ ")
            st.info("📚 คำตอบอ้างอิงข้อมูลที่มีอยู่ในฐานข้อมูล พร้อมปีและที่มาของตัวเลข")
        else:
            last_index = len(st.session_state.chat) - 1
            for idx, msg in enumerate(st.session_state.chat):
                with st.chat_message(msg["role"]):
                    st.markdown(msg["text"])
                    data = msg.get("table")
                    if data is not None and not data.empty:
                        expanded = idx == last_index
                        with st.expander(f"📊 ตารางข้อมูล {len(data):,} รายการ และดาวน์โหลด", expanded=expanded):
                            display_table(data, f"home_chat_{idx}")
    # Explicit nested container keeps the composer INLINE, before the Dashboard,
    # instead of Streamlit's normal bottom-of-page floating chat composer.
    with st.container(key="agri_chat_composer"):
        question = st.chat_input("ถามข้อมูลได้เลย เช่น ผลผลิตต่อไร่อ้อยจังหวัดขอนแก่น…", key="agri_chat_question")
    left, right = st.columns([3.5, 1])
    with left:
        st.caption("💡 ถามต่อได้โดยไม่ต้องพิมพ์จังหวัดหรือชื่อพืชซ้ำ")
    with right:
        if st.button("🧹 ล้างแชท", key="clear_chat_home", use_container_width=True):
            st.session_state.chat = []
            st.session_state.dialog_context = {}
            st.rerun()
    if not secret("GEMINI_API_KEY"):
        st.caption("ℹ️ ระบบค้นหาจากฐานข้อมูลได้แม้ยังไม่เชื่อม Gemini; คำถามซับซ้อนอาจมีข้อจำกัด")
    chosen = preset or question
    if chosen and str(chosen).strip():
        if district_data is None or province_data is None:
            st.error("กรุณาอัปโหลด data.csv และ province_data.csv เพื่อเปิดการสนทนา")
        else:
            handle_user_question(str(chosen).strip(), district_data, province_data)
            st.rerun()


st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Prompt:wght@400;500;600;700;800&display=swap');
:root {--agri-green:#17644d;--agri-green-soft:#e7f4ed;--agri-border:#d9e4dd;}
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"],
[data-testid="stAppViewContainer"] button, [data-testid="stAppViewContainer"] input,
[data-testid="stAppViewContainer"] textarea {
 font-family:'Prompt','Noto Sans Thai',Tahoma,sans-serif !important;
}
.block-container {max-width:1320px; padding-top:1.4rem; padding-bottom:5rem;}
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li {
 font-size:1.075rem; line-height:1.8; letter-spacing:.002em;
}
[data-testid="stMarkdownContainer"] h2 {font-size:clamp(1.55rem,2.6vw,2rem); font-weight:750; letter-spacing:-.02em; line-height:1.42;}
[data-testid="stMarkdownContainer"] h3 {font-size:1.33rem; font-weight:700; line-height:1.55;}
[data-testid="stWidgetLabel"] p, [data-testid="stSelectbox"] label p, [data-testid="stRadio"] label p {
 font-size:1.045rem !important; font-weight:600;
}
[data-testid="stCaptionContainer"] p, .stCaption p {
 font-size:.95rem !important; line-height:1.7 !important;
}
[data-testid="stButton"] button {min-height:3rem; border-radius:12px; font-size:1.02rem; font-weight:600;}
[data-testid="stDownloadButton"] button {font-size:1rem; min-height:3rem; border-radius:12px;}
[data-testid="stChatMessage"] {padding:1.05rem 1.1rem; border-radius:14px;}
[data-testid="stChatMessage"] p {font-size:1.075rem !important; line-height:1.75 !important;}
/* Inline message composer above Dashboard, not the bottom-of-viewport sticky composer */
.st-key-agri_chat_composer [data-testid="stChatInput"] {margin-top:13px; margin-bottom:4px;}
.st-key-agri_chat_composer textarea {font-size:1.17rem !important; line-height:1.5 !important; min-height:70px !important; padding-top:17px !important;}
.st-key-agri_chat_composer [data-testid="stChatInput"] > div {border-radius:16px; border-width:2px;}
.st-key-agri_chat_history {border-radius:18px;}
.agri-hero {border-radius:22px; padding:28px 32px; margin-bottom:24px;
 background:linear-gradient(115deg,#0c473a 0%,#166b50 60%,#27856c 100%); color:#fff;
 box-shadow:0 8px 24px rgba(5,55,35,.12);}
.agri-title {font-size:clamp(2rem,4vw,3rem);font-weight:800;letter-spacing:-.04em;line-height:1.35;}
.agri-lead {font-size:clamp(1.03rem,1.8vw,1.23rem);color:#e1f4e9;margin-top:6px;line-height:1.7;}
.agri-eyebrow, .agri-section-label {font-size:.84rem;font-weight:800;letter-spacing:.14em;color:#1f8668;margin:9px 0 7px;}
.agri-subtitle {font-size:1.06rem !important;line-height:1.65;color:inherit;opacity:.85;margin:0 0 15px;}
.agri-dash-spacer {border-top:1px solid var(--agri-border);margin:37px 0 27px;}
.agri-kpi-grid {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px;margin:16px 0 18px;}
.agri-kpi {min-width:0;border-radius:16px;border:1px solid #cfe0d4;background:#f7fbf8;padding:19px 16px;}
.agri-kpi-main {background:linear-gradient(150deg,#d6f1de,#edf9ee);border:2px solid #7db693;}
.agri-kpi-label {font-size:1rem;font-weight:650;color:#315843;line-height:1.4;}
.agri-kpi-value {font-weight:800;font-variant-numeric:tabular-nums;color:#16593b;font-size:clamp(1.4rem,2.25vw,2.15rem);margin:9px 0 6px;overflow-wrap:anywhere;}
.agri-kpi-value span {font-size:.84rem;font-weight:650;white-space:nowrap;}
.agri-kpi-note {font-size:.88rem;line-height:1.65;color:#52705d;}
[data-testid="stDataFrame"] {border-radius:12px;overflow:hidden;}
@media(max-width:920px) {.agri-kpi-grid {grid-template-columns:repeat(2,minmax(0,1fr));}}
@media(max-width:610px) {
 .block-container {padding-top:1rem;}
 .agri-hero {padding:22px 19px;border-radius:16px;}
 .agri-title {font-size:2rem;}
 .agri-kpi-grid {gap:8px;}
 .agri-kpi {padding:13px 11px;}
 .agri-kpi-value {font-size:1.35rem;}
 .agri-kpi-label {font-size:.9rem;}
 .agri-kpi-note {font-size:.8rem;}
 [data-testid="stButton"] button {font-size:.9rem;}
}
@media(prefers-color-scheme:dark) {
 .agri-kpi {background:#1e352a;border-color:#425d4b;}
 .agri-kpi-main {background:#234632;border-color:#6fb48b;}
 .agri-kpi-label,.agri-kpi-note {color:#c2e0ca;}
 .agri-kpi-value {color:#f0fff2;}
 .agri-eyebrow,.agri-section-label {color:#9bdab8;}
 .agri-dash-spacer {border-color:#425c4e;}
}
</style>""", unsafe_allow_html=True)
st.markdown("""<div class="agri-hero">
<div class="agri-title">🌾 AgriData AI <span style="font-size:.34em;vertical-align:middle;opacity:.8;font-weight:600">V3.1</span></div>
<div class="agri-lead">ผู้ช่วยค้นหาและวิเคราะห์ผลผลิตพืชเศรษฐกิจรายจังหวัดและอำเภอ<br>ถามได้ทันที · ผลผลิตต่อไร่ชัดเจน · พร้อมปีข้อมูลและแหล่งอ้างอิง</div>
</div>""", unsafe_allow_html=True)
password = secret("APP_PASSWORD")
if password and not st.session_state.get("auth"):
    with st.form("login"):
        value = st.text_input("รหัสผ่าน", type="password")
        if st.form_submit_button("เข้าสู่ระบบ"):
            if hmac.compare_digest(value, password):
                st.session_state.auth = True
                st.rerun()
            else:
                st.error("รหัสผ่านไม่ถูกต้อง")
    st.stop()
district = data_for("อำเภอ")
province = data_for("จังหวัด")
if district is None and province is None:
    st.error("ไม่พบ data.csv และ province_data.csv ใน GitHub")
    st.stop()
with st.sidebar:
    st.header("🗃️ ข้อมูลที่มีในระบบ")
    for level, frame in (("อำเภอ", district), ("จังหวัด", province)):
        if frame is not None:
            st.metric(level, f"{len(frame):,} รายการ")
            st.caption(f"{frame['จังหวัด'].nunique()} จังหวัด · {frame['พืช'].nunique()} ชนิดพืช")
    if st.button("🔄 โหลด CSV ใหม่"):
        load_csv.clear()
        st.rerun()
    st.caption("ฐานข้อมูล CSV จาก GitHub · ตัวเลขต้นฉบับไม่ถูกแก้ไข")

home_tab, quality_tab, source_tab = st.tabs(["🏠 หน้าแรก · แชทและ Dashboard", "🛡️ ตรวจสอบคุณภาพ", "📚 ข้อมูลต้นฉบับ"])
with home_tab:
    show_chat(district, province)
    st.markdown('<div class="agri-dash-spacer"></div><div class="agri-section-label">02 / DASHBOARD</div>', unsafe_allow_html=True)
    dashboard(district, province)
with quality_tab:
    st.subheader("🛡️ ตรวจสอบความครบถ้วนและความสอดคล้อง")
    all_issues = pd.concat([audit(d, l) for l, d in (("อำเภอ", district), ("จังหวัด", province)) if d is not None], ignore_index=True)
    st.metric("ข้อสังเกตที่ต้องตรวจทาน", len(all_issues))
    st.caption("ตรวจสอบโครงสร้างและการคำนวณ ไม่ใช่การรับรองข้อมูลกับ PDF สศก. ทุกบรรทัด")
    if all_issues.empty:
        st.success("ไม่พบประเด็นตามกฎตรวจสอบที่กำหนด")
    else:
        display_table(all_issues, "data_quality")
    st.markdown("**หลักการใช้ข้อมูล:** ใช้ปีเดียวกันก่อนจัดอันดับ · แยกค่าประมาณการจากข้อมูลรายงานจริง · ไม่บวกผลผลิตต่อไร่ · ตรวจสอบต้นฉบับก่อนใช้ราชการ")
with source_tab:
    level = st.radio("ชุดข้อมูล", [l for l, d in (("อำเภอ", district), ("จังหวัด", province)) if d is not None], horizontal=True)
    raw = district if level == "อำเภอ" else province
    st.caption(f"ข้อมูลระดับ{level} จำนวน {len(raw):,} รายการ")
    display_table(add_explanation(raw, level), "source_data")
    with st.expander("📘 ความหมายและข้อจำกัด"):
        st.markdown("**ผลผลิตต่อไร่:** ผลผลิต (ตัน) × 1,000 ÷ เนื้อที่เก็บเกี่ยว (ไร่) · แสดงทศนิยม 2 ตำแหน่ง")
        st.markdown("**ระดับจังหวัด:** ข้อมูลสรุประดับจังหวัดจากเอกสารอ้างอิง สศก.")
        st.markdown("**ระดับอำเภอ:** บางรายการประมาณการจากพื้นที่เก็บเกี่ยวอำเภอ × ผลผลิตเฉลี่ยจังหวัด ไม่ใช่ค่าที่สำรวจผลผลิตจริงของอำเภอ")
