"""AgriData AI V3.4 — reliable eight-province agricultural search.

Data originate from the eight supplied original workbooks. No LLM is allowed to invent
statistics. Province yields are OAE provincial observations; district yield is only
an OAE *province-average reference* and must never masquerade as district productivity.
"""
from __future__ import annotations

import hmac
import io
import re
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="AgriData AI | สารสนเทศพืชเศรษฐกิจ", page_icon="🌾", layout="wide", initial_sidebar_state="collapsed")
ROOT = Path(__file__).resolve().parent

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Prompt:wght@400;500;600;700&display=swap');
html,body,.stApp,[data-testid="stAppViewContainer"],button,input,textarea,select,
[data-testid="stMarkdownContainer"], [data-testid="stWidgetLabel"] {
 font-family: 'Prompt', Tahoma, sans-serif !important;
}
.block-container {max-width:1230px; padding-top:1rem; padding-bottom:3rem;}
[data-testid="stMarkdownContainer"] p,[data-testid="stMarkdownContainer"] li {font-size:1.08rem;line-height:1.78;}
[data-testid="stWidgetLabel"] p {font-weight:600!important;font-size:1.06rem!important;}
.stButton>button,.stDownloadButton>button,[data-testid="stFormSubmitButton"] button {
 border-radius:12px!important;font-size:1.03rem!important;min-height:47px!important;font-weight:600!important;
}
input {font-size:1.13rem!important;min-height:48px!important;}
[data-testid="stMetricLabel"] p {font-size:1.01rem!important;}
[data-testid="stMetricValue"] {font-size:1.86rem!important;font-weight:700!important;}
[data-testid="stChatMessage"] {padding:14px 17px;border-radius:14px;}
.ag-hero {background:linear-gradient(125deg,#164D38,#238260);padding:24px 28px;border-radius:20px;margin:0 0 20px;color:#fff;}
.ag-hero h1 {font-size:2.12rem!important;line-height:1.3;margin:0 0 5px;font-weight:700;color:#fff;}
.ag-hero p {font-size:1.04rem;margin:0;color:#eafbf1;line-height:1.65;}
.ag-kicker {font-weight:700;letter-spacing:.03em;color:#277a59;font-size:.95rem;margin-bottom:4px;}
.ag-card {border:1px solid #dce9e2;border-radius:16px;padding:13px 15px;background:rgba(232,247,239,.4);margin-bottom:8px;}
.ag-info {font-size:.96rem;line-height:1.8;padding:11px 15px;border-left:4px solid #38966c;border-radius:10px;background:rgba(228,244,234,.7);}
.ag-section {border-top:1px solid #dde6df;padding-top:14px;margin-top:14px;}
</style>
""",unsafe_allow_html=True)


def secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, ""))
    except (KeyError, FileNotFoundError):
        return ""


if secret("APP_PASSWORD"):
    passwd = st.text_input("รหัสผ่านเข้าใช้งาน", type="password")
    if not hmac.compare_digest(passwd, secret("APP_PASSWORD")):
        st.info("กรุณากรอกรหัสผ่านก่อนใช้งาน")
        st.stop()


@st.cache_data(show_spinner=False)
def load_csv(name: str, size: int, mtime: int) -> pd.DataFrame:
    df = pd.read_csv(ROOT/name, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    for col in df:
        if col.endswith(("(ตัน)", "(ไร่)", "(กก./ไร่)")):
            df[col] = pd.to_numeric(df[col].str.replace(",", "", regex=False), errors="coerce")
    return df


def read_file(name: str) -> pd.DataFrame:
    path = ROOT/name
    if not path.exists():
        st.error(f"ไม่พบ {name} กรุณาอัปโหลดไฟล์ใน GitHub ให้อยู่โฟลเดอร์เดียวกับ app.py")
        st.stop()
    return load_csv(name, path.stat().st_size, path.stat().st_mtime_ns)


province = read_file("province_data.csv")
district = read_file("data.csv")
CROP = "พืช"
PROV = "จังหวัด"
PCOUNT = "ผลผลิต (ตัน)"
PAREA = "พื้นที่เก็บเกี่ยว (ไร่)"
PYIELD = "ผลผลิตต่อไร่ (กก./ไร่)"
DCOUNT = "ผลผลิตรายอำเภอ (ตัน)"
DAREA = "พื้นที่เก็บเกี่ยวอำเภอ (ไร่)"
DYIELD = "ผลผลิตเฉลี่ยจังหวัด สศก. (กก./ไร่)"

PROVINCE_ALIASES = {
    "อุดรธานี": ["อุดรธานี", "อุดร"], "หนองบัวลำภู": ["หนองบัวลำภู", "หนองบัว"],
    "หนองคาย": ["หนองคาย"], "ขอนแก่น": ["ขอนแก่น"],
    "กาฬสินธุ์": ["กาฬสินธุ์", "กาฬ"], "บึงกาฬ": ["บึงกาฬ"],
    "มหาสารคาม": ["มหาสารคาม", "มหาสาร"], "สกลนคร": ["สกลนคร", "สกล"],
}
CROP_ALIASES = {
    "ข้าวนาปี": ["ข้าวนาปี", "นาปี"], "ข้าวนาปรัง": ["ข้าวนาปรัง", "นาปรัง"],
    "อ้อยโรงงาน": ["อ้อยโรงงาน", "อ้อย"],
    "มันสำปะหลังโรงงาน": ["มันสำปะหลังโรงงาน", "มันสำปะหลัง", "มัน"],
    "ยางพารา": ["ยางพารา", "ยาง"], "ปาล์มน้ำมัน": ["ปาล์มน้ำมัน", "ปาล์ม"],
    "ข้าวโพดเลี้ยงสัตว์": ["ข้าวโพดเลี้ยงสัตว์", "ข้าวโพด"],
}


def normalize(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).replace("จังหวัด", "").replace("จ.", "")


def find_provinces(question: str) -> list[str]:
    s = normalize(question)
    words = sorted([(alias, name) for name, aliases in PROVINCE_ALIASES.items() for alias in aliases], key=lambda x: len(x[0]), reverse=True)
    hits, ranges = [], []
    for alias, name in words:
        pos = s.find(alias)
        if pos >= 0 and not any(max(pos, left) < min(pos+len(alias), right) for left, right in ranges):
            if name not in hits:
                hits.append(name)
            ranges.append((pos, pos+len(alias)))
    return hits


def find_crops(question: str) -> list[str]:
    s = normalize(question)
    crops = [name for name, words in CROP_ALIASES.items() if any(word in s for word in words)]
    if "ข้าว" in s and not any(x in crops for x in ("ข้าวนาปี", "ข้าวนาปรัง")):
        crops.extend(["ข้าวนาปี", "ข้าวนาปรัง"])
    return list(dict.fromkeys(crops))


def fmt(value) -> str:
    return "ไม่พบข้อมูล" if pd.isna(value) else f"{value:,.2f}"


def pretty(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    shown = df[cols].copy()
    for col in shown.select_dtypes(include="number"):
        shown[col] = shown[col].map(fmt)
    return shown.fillna("—").replace("", "—")


def download_csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def year_text(value) -> str:
    v = str(value).strip()
    return v if v and v != "nan" else "ไม่ระบุ"


def oae_cite(row) -> str:
    ref = row.get("อ้างอิงตาราง/หน้า", "").replace("[1] ", "").replace("[2] ", "").replace("[3] ", "").replace("[4] ", "").replace("[5] ", "").replace("[6] ", "").replace("[7] ", "")
    link = row.get("ลิงก์ สศก.", "")
    return f"สศก. · {ref}" + (f" · [เอกสาร]({link})" if link else "")


def respond(question: str, context: dict, pv: pd.DataFrame, ds: pd.DataFrame):
    """Deterministic Thai question matching; never generate unsupported crop amounts."""
    q = normalize(question)
    named_places = find_provinces(question)
    named_crops = find_crops(question)
    is_coverage = bool(re.search(r"มีข้อมูล|มีไหม|รู้จัก|ครอบคลุม|มีจังหวัดไหน", q))
    all_places = bool(any(word in q for word in ["จังหวัดไหน", "ทุกจังหวัด", "ทั้ง8จังหวัด", "ทั้งหมด"]))
    followup = len(q) <= 26 and ("ด้วย" in q or q in ("นาปี", "นาปรัง", "ขอนแก่น", "อุดร", "อุดรธานี", "หนองบัว", "รายอำเภอ"))
    places = named_places or (context.get("places", []) if followup else [])
    crops = named_crops or (context.get("crops", []) if followup and not is_coverage else [])
    if all_places:
        places = []
    level = ("อำเภอ" if any(w in q for w in ["รายอำเภอ", "แต่ละอำเภอ", "อำเภอไหน", "แยกอำเภอ"])
             else ("จังหวัด" if "ระดับจังหวัด" in q else context.get("level", "จังหวัด") if followup else "จังหวัด"))
    if named_places:
        context["places"] = named_places
    elif all_places:
        context["places"] = []
    if named_crops:
        context["crops"] = named_crops
    if "ด้วย" in q and context.get("coverage") and named_places:
        is_coverage = True
    context["level"] = level
    context["coverage"] = is_coverage
    years = list(dict.fromkeys(re.findall(r"25\d{2}", q)))  # Only if explicitly typed, no year UI selector

    if is_coverage and not any(w in q for w in ["ผลผลิต", "กก./ไร่", "เก็บเกี่ยว"]):
        if not places:
            return "✅ มีฐานข้อมูล **" + str(pv[PROV].nunique()) + " จังหวัด**: " + ", ".join(sorted(pv[PROV].unique())), None
        info = []
        for city in places:
            p = pv[pv[PROV] == city]
            d = ds[ds[PROV] == city]
            info.append(f"✅ **{city}**: ข้อมูลจังหวัด {len(p)} รายการ · ข้อมูลอำเภอที่มีผลผลิต {d[DCOUNT].notna().sum()} จาก {len(d)} รายการ")
        return "\n\n".join(info), None

    if not crops:
        if places:
            part = pv[pv[PROV].isin(places)]
            types = ", ".join(sorted(part[CROP].unique()))
            return f"มีข้อมูล **{', '.join(places)}** ครับ ได้แก่ {types}\n\nลองถาม “อ้อยอุดร” หรือ “ข้าวนาปีขอนแก่น”", None
        return "ถามได้เลย เช่น **อุดร อ้อย** · **ข้าวขอนแก่น** · **ผลผลิตต่อไร่สูงสุด** · **มีข้อมูลจังหวัดไหนบ้าง**", None

    rice_both = set(crops) == {"ข้าวนาปี", "ข้าวนาปรัง"}
    wants_ranking = any(w in q for w in ["มากที่สุด", "สูงสุด", "อันดับ", "จัดอันดับ", "เปรียบเทียบ", "เทียบ"])
    if rice_both and not places:
        return ("ต้องการ **ข้าวนาปี** หรือ **ข้าวนาปรัง** ครับ?" if wants_ranking or q == "ข้าว" else "ต้องการข้าวนาปีหรือข้าวนาปรังครับ หรือพิมพ์ชื่อจังหวัดเพื่อดูทั้งสองประเภทแยกกัน"), None
    if rice_both and wants_ranking:
        return "เลือก **ข้าวนาปี** หรือ **ข้าวนาปรัง** ก่อนจัดอันดับ เพื่อไม่รวมผลผลิตคนละประเภทครับ", None

    if level == "จังหวัด":
        sel = pv[pv[CROP].isin(crops)].copy()
        if places:
            sel = sel[sel[PROV].isin(places)]
        if years:
            sel = sel[sel["ปีสถิติ"].astype(str).isin(years)]
        if sel.empty:
            return "ไม่พบข้อมูลจังหวัดที่ตรงคำถามในฐานข้อมูลปัจจุบันครับ (ไม่มีข้อมูล ≠ ผลผลิตเป็นศูนย์)", None
        if wants_ranking and (sel["ปีสถิติ"].nunique() > 1 or sel[CROP].nunique() > 1):
            return "ข้อมูลที่พบต่างปีหรือเป็นคนละพืช จึงยังไม่จัดอันดับรวมกันครับ ดูปีสถิติของแต่ละจังหวัดในตารางได้", sel
        measure = PYIELD if "ต่อไร่" in q else PAREA if "พื้นที่" in q else PCOUNT
        sel = sel.sort_values(measure, ascending=False) if wants_ranking else sel.sort_values([PROV,CROP])
        lines = []
        for _, r in sel.head(5).iterrows():
            lines.append(f"**{r[PROV]} · {r[CROP]} · ปีสถิติ {year_text(r['ปีสถิติ'])}**  \nผลผลิต **{fmt(r[PCOUNT])} ตัน** · **{fmt(r[PYIELD])} กก./ไร่** · พื้นที่ **{fmt(r[PAREA])} ไร่**  \n📚 {oae_cite(r)}")
        head = f"พบ **{len(sel)} รายการ** จากข้อมูลระดับจังหวัด ({', '.join(crops)})"
        if len(sel)>5:
            head += " — แสดงตัวอย่าง 5 รายการ และดูทั้งหมดในตารางด้านล่าง"
        return head + "\n\n" + "\n\n".join(lines), sel

    if not places:
        return "ต้องการข้อมูลรายอำเภอของจังหวัดไหนครับ? เช่น **อ้อยรายอำเภออุดร**", None
    sel = ds[(ds[PROV].isin(places)) & (ds[CROP].isin(crops))].copy()
    if years:
        pattern = "|".join(map(re.escape, years))
        sel = sel[sel["ปีข้อมูลรายอำเภอ"].astype(str).str.contains(pattern)]
    if sel.empty:
        return "ไม่พบรายการอำเภอตรงเงื่อนไขครับ ลองถามโดยไม่ระบุปี หรือเปลี่ยนพืช", None
    valid = sel[sel[DCOUNT].notna()].copy()
    intro = f"**ข้อมูลรายอำเภอ · {', '.join(places)} · {', '.join(crops)}** — มีผลผลิตอำเภอ {len(valid)} จาก {len(sel)} รายการ"
    lines = []
    for _, r in valid.head(5).iterrows():
        years_d = year_text(r["ปีข้อมูลรายอำเภอ"])
        lines.append(f"• **{r['อำเภอ']}** · ปี {years_d}: **{fmt(r[DCOUNT])} ตัน**  \n  📚 {r['ที่มาสั้น']}")
    if not valid.empty:
        intro += "\n\n" + "\n\n".join(lines)
    else:
        intro += "\n\nยังไม่พบตัวเลขผลผลิตรายอำเภอที่มีหลักฐานในไฟล์ต้นทาง จึงไม่ใช้ยอดจังหวัดมาแทน"
    if len(valid)>5:
        intro += "\n\nแสดง 5 รายการแรก; รายการอื่นอยู่ในตารางด้านล่าง"
    intro += "\n\n📏 **ค่า กก./ไร่ที่แสดงในตารางอำเภอเป็นค่าเฉลี่ยระดับจังหวัดของ สศก. ใช้อ้างอิง ไม่ใช่ผลสำรวจเฉพาะอำเภอ**"
    return intro, sel


st.markdown('<div class="ag-hero"><h1>🌾 AgriData AI</h1><p>ค้นหาผลผลิตพืชเศรษฐกิจ 8 จังหวัดภาคอีสาน · เน้นผลผลิตต่อไร่ · ระบุแหล่งข้อมูลทุกครั้ง</p></div>', unsafe_allow_html=True)

st.markdown('<div class="ag-kicker">AI ASSISTANT</div>', unsafe_allow_html=True)
st.subheader("💬 ถามข้อมูลการเกษตร")
st.caption("พิมพ์เหมือนคุยกับเจ้าหน้าที่ เช่น “อุดร อ้อย” หรือ “ข้าวขอนแก่น” · ไม่ต้องเลือกปี ระบบแสดงปีตามเอกสารให้อัตโนมัติ")

if "history" not in st.session_state:
    st.session_state.history = []
if "context" not in st.session_state:
    st.session_state.context = {"places":[],"crops":[],"level":"จังหวัด","coverage":False}

# Clickable shortcuts only request existing evidence; never synthesize numbers.
qa = st.columns(4)
quick = ["อุดร อ้อย", "ข้าวขอนแก่น", "อ้อยรายอำเภออุดร", "มีข้อมูลจังหวัดไหนบ้าง"]
queued = None
for col, label in zip(qa, quick):
    if col.button(label, use_container_width=True, key="quick_"+label):
        queued = label

with st.container(height=485, border=True):
    if not st.session_state.history:
        st.info("ถามข้อมูลได้เลยครับ 📍 ข้อมูลดึงจาก CSV ที่ตรวจสอบแหล่งอ้างอิงแล้ว ไม่ให้ AI สร้างตัวเลขเอง")
    for msg in st.session_state.history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("table") is not None:
                table = msg["table"]
                cols = ([PROV,CROP,"ปีสถิติ",PYIELD,PCOUNT,PAREA,"ที่มาสั้น","ลิงก์ สศก."]
                        if "ปีสถิติ" in table else
                        [PROV,"อำเภอ",CROP,"ปีข้อมูลรายอำเภอ",DCOUNT,DYIELD,"ประเภทแหล่งข้อมูล","ที่มาสั้น","แหล่งรายอำเภอ","URL รายอำเภอ","ที่มาผลผลิตต่อไร่"])
                st.dataframe(pretty(table, cols), use_container_width=True, hide_index=True)
                st.download_button("⬇️ ดาวน์โหลดผลลัพธ์ CSV", download_csv(table), "agri_chat_result.csv", mime="text/csv", key="dl_"+str(msg.get("id")))

with st.form("ask_form", clear_on_submit=True):
    typed = st.text_input("พิมพ์คำถาม", placeholder="เช่น ผลผลิตอ้อยอุดร / ข้าวนาปีจังหวัดขอนแก่น / ขอรายอำเภอด้วย")
    sent = st.form_submit_button("➤ ส่งคำถาม", use_container_width=True)

if (sent and typed.strip()) or queued:
    question = queued or typed.strip()
    answer, table = respond(question, st.session_state.context, province, district)
    idx = len(st.session_state.history)
    st.session_state.history.extend([
        {"role":"user", "content":question, "id":idx},
        {"role":"assistant", "content":answer, "table":table, "id":idx+1},
    ])
    st.rerun()
if st.button("🧹 ล้างบทสนทนา", key="clear_chat"):
    st.session_state.history = []
    st.session_state.context = {"places":[],"crops":[],"level":"จังหวัด","coverage":False}
    st.rerun()

st.markdown('<div class="ag-section"></div>', unsafe_allow_html=True)
st.markdown('<div class="ag-kicker">AGRICULTURAL DASHBOARD</div>', unsafe_allow_html=True)
st.header("📊 ภาพรวมผลผลิตต่อไร่")
st.caption("ค่าผลผลิตต่อไร่ระดับจังหวัดนำจากเอกสาร สศก. โดยตรง (ไม่คำนวณใหม่) · แสดงปีสถิติและที่มาไว้ให้ตรวจสอบ")

m1,m2,m3,m4 = st.columns(4)
m1.metric("จังหวัดในฐานข้อมูล", f"{province[PROV].nunique():,}")
m2.metric("อำเภอในฐานข้อมูล", f"{district[[PROV,'อำเภอ']].drop_duplicates().shape[0]:,}")
m3.metric("ชนิดพืช", f"{province[CROP].nunique():,}")
m4.metric("ข้อมูลจังหวัดจาก สศก.", f"{len(province):,} รายการ")

c1,c2 = st.columns([1,1])
crops_list = sorted(province[CROP].unique().tolist())
def_crop = crops_list.index("อ้อยโรงงาน") if "อ้อยโรงงาน" in crops_list else 0
crop_name = c1.selectbox("🌱 เลือกพืชเศรษฐกิจ", crops_list, index=def_crop)
city_name = c2.selectbox("📍 เลือกจังหวัด", ["ทุกจังหวัด"]+sorted(province[PROV].unique().tolist()))
current = province[province[CROP] == crop_name].copy()
if city_name != "ทุกจังหวัด":
    current = current[current[PROV] == city_name]
if current.empty:
    st.warning("ยังไม่มีข้อมูลระดับจังหวัดสำหรับพืชและจังหวัดที่เลือก (ไม่ถือว่าไม่มีการปลูก)")
else:
    years = sorted({str(y) for y in current["ปีสถิติ"]})
    st.info("ปีสถิติที่ใช้ในชุดข้อมูลนี้: " + ", ".join(years) + " · ระบบเลือกข้อมูลจากฐานที่มีให้อัตโนมัติ")
    same_year = len(years) == 1
    if not same_year:
        st.warning("ปีสถิติไม่ตรงกัน จึงไม่จัดอันดับข้ามปี; โปรดดูปีที่กำกับแต่ละแถวในตาราง")
    leader = current.sort_values(PYIELD, ascending=False).iloc[0]
    k1,k2,k3 = st.columns(3)
    if len(current)==1:
        k1.metric("⭐ ผลผลิตต่อไร่",f"{leader[PYIELD]:,.2f} กก./ไร่")
        k2.metric("ผลผลิตรวม",f"{leader[PCOUNT]:,.2f} ตัน")
        k3.metric("พื้นที่เก็บเกี่ยว",f"{leader[PAREA]:,.2f} ไร่")
    else:
        k1.metric("⭐ ผลผลิตต่อไร่สูงสุด (ปีเดียวกัน)" if same_year else "⭐ ผลผลิตต่อไร่สูงสุด (ยังไม่เทียบปี)", f"{leader[PYIELD]:,.2f} กก./ไร่" if same_year else "—")
        k2.metric("จังหวัดที่มีข้อมูลพืชนี้",f"{len(current):,} จังหวัด")
        k3.metric("ผลผลิตรวม", "ดูแยกรายจังหวัด" )
    st.markdown(f"<div class='ag-info'>📚 <b>ที่มาผลผลิตต่อไร่:</b> สศก. · สถิติการเกษตรของประเทศไทย ปี 2568 · "
                f"{', '.join(current['อ้างอิงตาราง/หน้า'].str.replace(r'^\[\d+\] ', '', regex=True).unique().tolist())} · "
                "เป็นค่า กก./ไร่ ระดับจังหวัดตามเอกสาร ไม่ใช่ค่าเฉลี่ยของหลายจังหวัดที่คำนวณขึ้นใหม่</div>", unsafe_allow_html=True)
    if same_year:
        st.markdown("#### 🏆 เปรียบเทียบผลผลิตต่อไร่ (กก./ไร่)")
        ranked = current.sort_values(PYIELD,ascending=False).copy()
        ranked["ป้ายตัวเลข"] = ranked[PYIELD].map(lambda x:f"{x:,.2f}")
        chart = alt.Chart(ranked).mark_bar(color="#278765", cornerRadiusEnd=6).encode(
            y=alt.Y("จังหวัด:N", sort=alt.SortField(field=PYIELD,order="descending"), title=None, axis=alt.Axis(labelFontSize=15,labelLimit=200)),
            x=alt.X("ผลผลิตต่อไร่ (กก./ไร่):Q",title="กิโลกรัมต่อไร่ (กก./ไร่)",axis=alt.Axis(labelFontSize=12)),
            tooltip=[alt.Tooltip("จังหวัด:N"),alt.Tooltip("พืช:N"),alt.Tooltip("ปีสถิติ:N"),alt.Tooltip("ผลผลิตต่อไร่ (กก./ไร่):Q",format=",.2f")]
        ).properties(height=max(110,55*len(ranked)))
        labels = alt.Chart(ranked).mark_text(align="left",dx=7,fontSize=14,fontWeight="bold",color="#1c5b41").encode(
            y=alt.Y("จังหวัด:N",sort=alt.SortField(field=PYIELD,order="descending")),
            x=alt.X("ผลผลิตต่อไร่ (กก./ไร่):Q"),text="ป้ายตัวเลข:N"
        )
        st.altair_chart((chart+labels).configure_axis(gridColor="#eff3f0",domain=False),use_container_width=True)
    shown_cols=[PROV,CROP,"ปีสถิติ",PYIELD,PCOUNT,PAREA,"ที่มาสั้น","ลิงก์ สศก."]
    st.markdown("#### 📋 ตารางตัวเลขพร้อมแหล่งอ้างอิง")
    st.dataframe(pretty(current.sort_values(PYIELD,ascending=False),shown_cols),hide_index=True,use_container_width=True)
    st.download_button("⬇️ ดาวน์โหลดข้อมูลระดับจังหวัด",download_csv(current),"agri_province.csv",mime="text/csv")
    with st.expander("ดูกราฟผลผลิตรวมและพื้นที่เก็บเกี่ยว", expanded=False):
        v1,v2=st.columns(2)
        with v1:
            st.markdown("**ผลผลิตรวม (ตัน)**")
            st.bar_chart(current.set_index(PROV)[PCOUNT],horizontal=True,use_container_width=True)
        with v2:
            st.markdown("**พื้นที่เก็บเกี่ยว (ไร่)**")
            st.bar_chart(current.set_index(PROV)[PAREA],horizontal=True,use_container_width=True)

st.markdown('<div class="ag-section"></div>', unsafe_allow_html=True)
st.subheader("📍 ข้อมูลรายอำเภอและที่มา")
st.caption("ข้อมูลอำเภอแสดงปีเดิมตามเอกสาร · ค่า กก./ไร่ของอำเภอในฐานนี้เป็นค่าเฉลี่ยจังหวัด สศก. เพื่ออ้างอิง ไม่ใช่การวัดผลผลิตต่อไร่ของอำเภอนั้น")
# Reuse dashboard selectors so the screen stays simple; no independent year search control.
sub = district[district[CROP] == crop_name].copy()
if city_name != "ทุกจังหวัด":
    sub = sub[sub[PROV] == city_name]
valid = sub[DCOUNT].notna()
s1,s2,s3=st.columns(3)
s1.metric("รายการอำเภอ × พืช",len(sub))
s2.metric("รายการที่มีผลผลิตรายอำเภอ",int(valid.sum()))
s3.metric("รายการยังไม่มีผลผลิตอำเภอ",int((~valid).sum()))
if not sub.empty:
    cols = [PROV,"อำเภอ",CROP,"ปีข้อมูลรายอำเภอ",DCOUNT,DYIELD,"ประเภทแหล่งข้อมูล","ที่มาสั้น","ที่มาผลผลิตต่อไร่","แหล่งรายอำเภอ","URL รายอำเภอ","URL สศก."]
    st.dataframe(pretty(sub,cols),use_container_width=True,hide_index=True,height=345)
    st.download_button("⬇️ ดาวน์โหลดข้อมูลรายอำเภอ",download_csv(sub),"agri_district.csv",mime="text/csv")

st.markdown('<div class="ag-section"></div>', unsafe_allow_html=True)
with st.expander("📚 อ่านคำอธิบายแหล่งข้อมูล (สศก. · สำนักงานเกษตร/กรมส่งเสริม · ข้อมูลประมาณการ)"):
    st.markdown("""
**1. สำนักงานเศรษฐกิจการเกษตร (สศก.)** — ผลผลิตรวม พื้นที่เก็บเกี่ยว และผลผลิตต่อไร่ **ระดับจังหวัด**
มาจากตารางใน *สถิติการเกษตรของประเทศไทย ปี 2568* โดยแสดงปีสถิติและเลขตาราง/หน้าไว้ในทุกแถว

**2. สำนักงานเกษตรจังหวัด/อำเภอ หรือกรมส่งเสริมการเกษตร** — ใช้แหล่งนี้เมื่อ **แถวต้นทางระบุหน่วยงานนั้นจริง**
ข้อมูลบางแถวมาจากเอกสารหรือทะเบียนเกษตรกร ซึ่งไม่ได้หมายความว่ามีตัวเลขผลผลิตรายอำเภอทุกแถว

**3. ผลผลิตรายอำเภอที่ประมาณการ** — เฉพาะแถวที่ระบุว่า *ประมาณการจากพื้นที่อำเภอ × ค่าเฉลี่ย สศก.*
ตัวเลขผลผลิตอำเภอคำนวณจากพื้นที่เก็บเกี่ยวอำเภอ × ผลผลิตต่อไร่ระดับจังหวัดที่ สศก. รายงาน ไม่ใช่ผลผลิตที่สำรวจจริงในอำเภอ

**4. แหล่งอื่น / เอกสารต้นทาง** — บางรายการระบุกรมพัฒนาที่ดิน รายงานท้องถิ่น หรือคำนวณรวมจากข้อมูลย่อย
ระบบคงชื่อแหล่งเดิมไว้และไม่เปลี่ยนเป็นชื่อ สศก. หรือกรมส่งเสริมโดยไม่มีหลักฐาน

**หมายเหตุ:** ไม่เติมค่าผลผลิตอำเภอที่ไม่มีหลักฐานด้วยยอดจังหวัด และไม่ใช้ค่าเฉลี่ยจังหวัดเพื่อจัดอันดับประสิทธิภาพของแต่ละอำเภอ
""")
    st.markdown(f"**ตรวจสอบย้อนหลังได้:** ข้อมูลจังหวัด {len(province)} แถว · รายการอำเภอ {len(district)} แถว · มีผลผลิตอำเภอ {district[DCOUNT].notna().sum()} แถว")
    with st.expander("ดูทะเบียนข้อมูลจังหวัดทั้งหมด"):
        st.dataframe(province,use_container_width=True,hide_index=True)
    with st.expander("ดูทะเบียนข้อมูลอำเภอและเลขแถวต้นทางทั้งหมด"):
        st.dataframe(district,use_container_width=True,hide_index=True)

st.caption("หมายเหตุคุณภาพข้อมูล: แหล่งอ้างอิงและตัวเลขถอดจากไฟล์ Excel ต้นฉบับ 8 จังหวัดที่ได้รับ ยังไม่ได้เทียบกับ PDF ทางการทุกแถว; หากต้องใช้ประกอบเอกสารทางราชการควรตรวจต้นฉบับตามลิงก์")
