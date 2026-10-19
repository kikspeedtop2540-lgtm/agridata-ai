"""AgriData AI: local CSV, two geographic levels, transparent calculations.
Upload app.py + requirements.txt + data.csv + province_data.csv to GitHub root.
"""
import hmac
import io
import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="AgriData AI", page_icon="🌾", layout="wide")
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

YIELD_EXPLANATION = (
    "\n\n**📏 ความหมายของผลผลิตต่อไร่** — ปริมาณผลผลิตเฉลี่ยที่ได้จากพื้นที่เก็บเกี่ยว 1 ไร่ "
    "มีหน่วยเป็น **กิโลกรัมต่อไร่ (กก./ไร่)** โดยคำนวณจาก "
    "**ผลผลิตรวม (ตัน) × 1,000 ÷ เนื้อที่เก็บเกี่ยว (ไร่)** "
    "(ต้องมีพื้นที่เก็บเกี่ยวมากกว่า 0 ไร่) "
    "ตัวเลขผลผลิตต่อไร่ช่วยเปรียบเทียบประสิทธิภาพการผลิต แต่ไม่ใช่ผลผลิตรวมทั้งหมด. "
    "**ถ้าเป็นข้อมูลประมาณการ** เช่น ใช้ผลผลิตเฉลี่ยระดับจังหวัดของ สศก. "
    "คำนวณผลผลิตรายอำเภอ ค่า กก./ไร่ ที่แสดงอาจเป็นค่าเฉลี่ยระดับจังหวัด "
    "**ไม่ใช่ค่าที่วัดจริงของอำเภอนั้น**; ควรตรวจปีและแหล่งอ้างอิงก่อนเปรียบเทียบ."
)

def with_yield_note(message):
    """Always append the same yield definition to every chatbot answer."""
    return str(message) + YIELD_EXPLANATION

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
    header += evidence_note(valid, level)
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

def dashboard(district, province):
    st.subheader("📊 ภาพรวมฐานข้อมูล")
    available=[(label,df) for label,df in (("อำเภอ",district),("จังหวัด",province)) if df is not None]
    if not available: return
    common=pd.concat([d for _,d in available],ignore_index=True)
    c1,c2,c3,c4=st.columns(4)
    c1.metric("จังหวัด",int(common["จังหวัด"].nunique()))
    c2.metric("อำเภอ",int(district["อำเภอ"].nunique()) if district is not None else 0)
    c3.metric("ชนิดพืช",int(common["พืช"].nunique()))
    c4.metric("ระเบียน",len(common))
    a,b,c=st.columns(3)
    level=a.selectbox("ระดับข้อมูล",[x for x,_ in available],key="dashlevel")
    current=district if level=="อำเภอ" else province
    pv=b.selectbox("จังหวัด",["ทั้งหมด"]+sorted(current["จังหวัด"].dropna().unique().tolist()),key="dashprovince")
    crop=c.selectbox("พืช",["ทั้งหมด"]+sorted(current["พืช"].dropna().unique().tolist()),key="dashcrop")
    subset=current.copy()
    if pv!="ทั้งหมด": subset=subset[subset["จังหวัด"]==pv]
    if crop!="ทั้งหมด": subset=subset[subset["พืช"]==crop]
    periods=sorted(subset["ปี"].dropna().astype(str).unique().tolist())
    yr=st.selectbox("ปี / ฤดูเพาะปลูก",["ทั้งหมด"]+periods,key="dashyear")
    if yr!="ทั้งหมด":subset=subset[subset["ปี"]==yr]
    else: st.warning("ข้อมูลมีหลายปีได้ กราฟนี้แยกตามปี ไม่รวมข้ามปี; เลือกปีเดียวเพื่อเปรียบเทียบโดยตรง")
    metric=st.selectbox("ตัวชี้วัด",list(NUMBERS.keys()),key="dashmetric")
    if metric in subset:
        viz=subset.dropna(subset=[metric]).copy()
        group="อำเภอ" if level=="อำเภอ" else "จังหวัด"
        if yr=="ทั้งหมด": groupby=["ปี",group]
        else: groupby=[group]
        if not viz.empty:
            # Not additive yield; no pooling/averaging of district yield values
            if metric=="ผลผลิตต่อไร่ (กก./ไร่)":
                if len(viz)>30: st.info("แสดงเฉพาะ 30 รายการแรกของกราฟเพื่อให้อ่านง่าย; ดูข้อมูลทั้งหมดในตาราง")
                viz["รายการ"]=viz[group].astype(str)+" · "+viz["ปี"].astype(str)
                chart_data=viz[["รายการ",metric]].head(30).set_index("รายการ")
            else:
                chart_data=viz.groupby(groupby,dropna=False)[metric].sum().sort_values(ascending=False).head(30).to_frame()
                chart_data.index=chart_data.index.map(lambda val:" · ".join(map(str,val)) if isinstance(val,tuple) else str(val))
            st.bar_chart(chart_data,horizontal=True)
            st.caption("กราฟใช้ข้อมูลตามแถวที่มีในฐานข้อมูล; แถวประมาณการต้องอ่านคำอธิบายประกอบ")
    st.caption(f"พบ {len(subset):,} รายการภายใต้ตัวกรอง")
    display_table(add_explanation(subset,level),"dashboard_filtered")

st.title("🌾 AgriData AI · V2.0")
st.caption("วิเคราะห์พืชเศรษฐกิจ 8 จังหวัด · แสดงหลักฐาน · ควบคุมการเปรียบเทียบต่างปี · ส่งออก Excel")
password=secret("APP_PASSWORD")
if password and not st.session_state.get("auth"):
    with st.form("login"):
        value=st.text_input("รหัสผ่าน",type="password")
        if st.form_submit_button("เข้าสู่ระบบ"):
            if hmac.compare_digest(value,password):st.session_state.auth=True;st.rerun()
            else:st.error("รหัสผ่านไม่ถูกต้อง")
    st.stop()
district=data_for("อำเภอ")
province=data_for("จังหวัด")
if district is None and province is None:
    st.error("ไม่พบ data.csv และ province_data.csv ใน GitHub")
    st.stop()
with st.sidebar:
    st.header("🗃️ ชุดข้อมูลที่อ่านได้")
    for level,frame in (("อำเภอ",district),("จังหวัด",province)):
        if frame is not None:
            st.metric(level,f"{len(frame):,} รายการ")
            st.caption(f"{frame['จังหวัด'].nunique()} จังหวัด · {frame['พืช'].nunique()} ชนิดพืช")
    if st.button("🔄 โหลด CSV ใหม่"):
        load_csv.clear();st.rerun()
    st.caption("ใช้ไฟล์ CSV ใน GitHub · ไม่มีการแก้ข้อมูลต้นฉบับ")

tab_chat,tab_dashboard,tab_quality,tab_source=st.tabs(["💬 ถาม AgriData","📊 Dashboard","🛡️ ตรวจสอบคุณภาพ","📚 ข้อมูลต้นฉบับ"])
with tab_chat:
    st.markdown("**ตัวอย่าง:** ผลผลิตอ้อยจังหวัดขอนแก่น · ข้าวนาปีรายอำเภอในกาฬสินธุ์ · อันดับอ้อยสูงสุดปี 2567")
    if not secret("GEMINI_API_KEY"):
        st.info("ยังไม่มี Gemini API Key — ใช้คำถามที่ระบุจังหวัด/อำเภอ/พืช/ปีโดยตรงได้")
    if "chat" not in st.session_state:st.session_state.chat=[]
    for idx,msg in enumerate(st.session_state.chat):
        with st.chat_message(msg["role"]):
            st.markdown(msg["text"])
            if msg.get("table") is not None:display_table(msg["table"],f"chat_{idx}")
    q=st.chat_input("พิมพ์คำถามเกี่ยวกับผลผลิตพืชเศรษฐกิจ…")
    if q:
        st.session_state.chat.append({"role":"user","text":q})
        reply,table,metric=answer(q,district,province)
        st.session_state.chat.append({"role":"assistant","text":with_yield_note(reply),"table":table})
        st.rerun()
with tab_dashboard:
    dashboard(district,province)
with tab_quality:
    st.subheader("🛡️ ตรวจสอบความครบถ้วนและความสอดคล้อง")
    all_issues=pd.concat([audit(d,l) for l,d in (("อำเภอ",district),("จังหวัด",province))],ignore_index=True)
    st.metric("ข้อสังเกตที่ต้องตรวจทาน",len(all_issues))
    st.caption("เป็นการตรวจสอบเชิงโครงสร้างและการคำนวณ ไม่ใช่การรับรองข้อมูลกับ PDF สศก. ทุกบรรทัด")
    if all_issues.empty:st.success("ไม่พบประเด็นตามกฎตรวจสอบที่กำหนด")
    else:display_table(all_issues,"data_quality")
    st.markdown("**หลักการใช้ข้อมูล:** เลือกปีเดียวกันก่อนจัดอันดับ · แยกข้อมูลประมาณการออกจากข้อมูลรายงานจริง · ไม่รวมผลผลิตต่อไร่ด้วยการบวก · ตรวจสอบเอกสารต้นทางก่อนใช้ราชการ")
with tab_source:
    level=st.radio("ชุดข้อมูล",[l for l,d in (("อำเภอ",district),("จังหวัด",province)) if d is not None],horizontal=True)
    raw=district if level=="อำเภอ" else province
    st.caption(f"ข้อมูลระดับ{level} จำนวน {len(raw):,} รายการ")
    display_table(add_explanation(raw,level),"source_data")
    with st.expander("📘 ความหมายและข้อจำกัด"):
        st.markdown("**ผลผลิตต่อไร่:** ผลผลิต (ตัน) × 1,000 ÷ เนื้อที่เก็บเกี่ยว (ไร่) · แสดงทศนิยม 2 ตำแหน่ง")
        st.markdown("ข้อมูลจังหวัด: **ข้อมูลสรุประดับจังหวัดจากเอกสารอ้างอิง สศก.**")
        st.markdown("กรณีรายอำเภอประมาณการจากพื้นที่เก็บเกี่ยวอำเภอ × ผลผลิตเฉลี่ยจังหวัด ควรระบุวิธีคำนวณและปีข้อมูล ไม่ใช่ค่าจากการสำรวจผลผลิตจริงของอำเภอ")
