"""AgriData AI: local CSV, two geographic levels, transparent calculations.
Upload app.py + requirements.txt + data.csv + province_data.csv to GitHub root.
"""
import hmac
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
        df["คำอธิบายข้อมูล"] = "ข้อมูลสรุประดับจังหวัดจากเอกสารอ้างอิง สศก. (ไม่ใช่ตัวเลขระดับอำเภอ)"
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
        chosen = extract_filter(question, choices)
        ai_candidate = ai.get(ai_field)
        if not chosen and ai_candidate in choices:
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
    if "พื้นที่" in question or "เนื้อที่" in question or ai.get("metric") == "area":
        metric = "เนื้อที่เก็บเกี่ยว (ไร่)"
    elif "ต่อไร่" in question or ai.get("metric") == "yield":
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
    if "อันดับ" in question or "มากที่สุด" in question or "สูงสุด" in question:
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
    header += evidence_note(valid, level)
    if len(years) > 1:
        header += "\n\n⚠️ รายการอาจมีปีข้อมูลต่างกัน ไม่ควรใช้จัดอันดับเปรียบเทียบโดยตรงจนกว่าจะเลือกปีเดียวกัน"
    return header, result, metric

def display_table(table, prefix):
    st.dataframe(table, hide_index=True, use_container_width=True)
    st.download_button("⬇️ ดาวน์โหลดผลลัพธ์ CSV", table.to_csv(index=False).encode("utf-8-sig"), file_name=f"{prefix}.csv", mime="text/csv", key=prefix)

st.title("🌾 AgriData AI")
st.caption("ฐานข้อมูลผลผลิตพืชเศรษฐกิจ 8 จังหวัด — แยกระดับจังหวัด/อำเภอ พร้อมตรวจสอบปีและที่มา")
password = secret("APP_PASSWORD")
if password and not st.session_state.get("auth"):
    with st.form("login"):
        value = st.text_input("รหัสผ่านสำหรับเข้าใช้งาน", type="password")
        submitted = st.form_submit_button("เข้าสู่ระบบ")
    if submitted:
        if hmac.compare_digest(value, password):
            st.session_state["auth"] = True
            st.rerun()
        else:
            st.error("รหัสผ่านไม่ถูกต้อง")
    st.stop()

district = data_for("อำเภอ")
province = data_for("จังหวัด")
if district is None and province is None:
    st.error("ไม่พบ data.csv หรือ province_data.csv ในโฟลเดอร์เดียวกับ app.py")
    st.stop()
with st.sidebar:
    st.header("📚 ตรวจสอบฐานข้อมูล")
    for name, df in [("อำเภอ", district), ("จังหวัด", province)]:
        if df is None:
            st.warning(f"ไม่พบไฟล์ {FILES[name]}")
        else:
            st.metric(f"รายการระดับ{name}", f"{len(df):,}")
            st.caption(f"{df['จังหวัด'].nunique()} จังหวัด · พืช {df['พืช'].nunique()} ชนิด")
    st.caption("อ่านข้อมูลจาก CSV ใน GitHub เท่านั้น · รองรับปีเพาะปลูกแบบ 2565/2566 · ไม่อ่านไฟล์ XLSX หรือ Google Sheets")
    if st.button("🔄 โหลดข้อมูลใหม่"):
        load_csv.clear()
        st.rerun()
    st.divider()
    st.subheader("🔎 ค้นด้วยตัวกรอง (ไม่ใช้ AI)")
    scope = st.radio("ระดับข้อมูล", [x for x,y in [("อำเภอ",district),("จังหวัด",province)] if y is not None])
    filtered = district.copy() if scope == "อำเภอ" else province.copy()
    for field in ["จังหวัด", "อำเภอ", "พืช", "ปี"]:
        if field not in filtered:
            continue
        choices = sorted(filtered[field].dropna().unique().tolist(), key=str)
        choice = st.selectbox(field, ["ทั้งหมด"] + choices, key=f"filter_{field}")
        if choice != "ทั้งหมด":
            filtered = filtered[filtered[field] == choice]
    st.write(f"ตรงเงื่อนไข {len(filtered):,} รายการ")
    st.download_button("⬇️ ดาวน์โหลดรายการที่กรอง", filtered.to_csv(index=False).encode("utf-8-sig"), "filtered_agri.csv", "text/csv")
with st.expander("📋 ดูข้อมูลที่โหลดจริงจาก GitHub"):
    view = st.radio("ชุดข้อมูล", [x for x,y in [("อำเภอ",district),("จังหวัด",province)] if y is not None], horizontal=True)
    display_table(add_explanation(district if view == "อำเภอ" else province, view), "source_data")
st.info("ข้อมูลระดับจังหวัดใช้ยอดจังหวัดตามเอกสารอ้างอิง สศก. ส่วนข้อมูลรายอำเภอมีทั้งค่าที่รายงานจริง ค่ารวมจากข้อมูลย่อย และ **ค่าประมาณการโดยใช้อัตราผลผลิตเฉลี่ยจังหวัด** ซึ่งจะแยกคำอธิบายให้ชัดเจน")
with st.expander("ℹ️ ความหมายของผลผลิตรายอำเภอที่อ้างอิงข้อมูล สศก."):
    st.markdown("""**ข้อความที่ควรใช้อธิบาย:** ‘ผลผลิตรายอำเภอประมาณการจากพื้นที่เก็บเกี่ยวรายอำเภอ โดยใช้อัตราผลผลิตเฉลี่ยระดับจังหวัดของสำนักงานเศรษฐกิจการเกษตร (สศก.)’

วิธีคำนวณ: **พื้นที่เก็บเกี่ยวรายอำเภอ (ไร่) × ผลผลิตเฉลี่ยระดับจังหวัด (กก./ไร่) ÷ 1,000 = ผลผลิตประมาณการรายอำเภอ (ตัน)**

ตัวเลขนี้ **ไม่ใช่ข้อมูลผลผลิตรายอำเภอที่ สศก. เผยแพร่โดยตรง** และ **ไม่ได้ใช้ยอดผลผลิตรวมของจังหวัดมาแทนยอดรายอำเภอ** แต่เป็นการประมาณการด้วยอัตราเฉลี่ยจังหวัด ซึ่งอาจไม่สะท้อนความแตกต่างของผลผลิตจริงในแต่ละอำเภอ

บางรายการมีผลผลิตรายอำเภอจากเอกสารต้นทางจริง หรือคำนวณรวมจากรายการย่อย จึงต้องดู ‘สถานะข้อมูล’ ‘วิธีได้มา’ ‘ประเภทหลักฐาน’ และ ‘คำอธิบายข้อมูล’ ของแต่ละแถวก่อนอ้างอิง""")
st.caption("ตัวอย่าง: 'ข้าวนาปีจังหวัดกาฬสินธุ์ปี 2567' · 'อันดับ 5 อำเภอผลผลิตข้าวนาปีสูงสุดในขอนแก่นปี 2567' · 'จังหวัดอุดรธานีมีผลผลิตอ้อยโรงงานเท่าไร'")
if not secret("GEMINI_API_KEY"):
    st.caption("ยังไม่ได้ใส่ GEMINI_API_KEY — ถามคำถามแบบระบุจังหวัด/อำเภอ/พืช/ปีได้โดยไม่ใช้ AI")
if "chat" not in st.session_state:
    st.session_state.chat = []
for i, entry in enumerate(st.session_state.chat):
    with st.chat_message(entry["role"]):
        st.markdown(entry["text"])
        if entry.get("table") is not None:
            display_table(entry["table"], f"history_{i}")
question = st.chat_input("ถามข้อมูลผลผลิต เช่น อำเภอไหนมีข้าวนาปีในจังหวัดขอนแก่น ปี 2567?")
if question:
    st.session_state.chat.append({"role": "user", "text": question})
    reply, table, metric = answer(question, district, province)
    st.session_state.chat.append({"role": "assistant", "text": reply, "table": table})
    st.rerun()
