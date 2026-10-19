"""AgriData AI – clean rebuild from eight supplied original provincial workbooks.
Numerical answers are deterministic CSV lookups; province-reference quantities never
become district results. Optional Gemini key is not required for data searching.
"""
import io
import hmac
import re
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="AgriData AI · ฐานข้อมูล 8 จังหวัด", page_icon="🌾", layout="wide", initial_sidebar_state="collapsed")
BASE = Path(__file__).resolve().parent

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Prompt:wght@400;500;600;700&display=swap');
html,body,[class*="css"],.stApp,button,input,select,textarea {font-family:'Prompt',sans-serif!important;}
.block-container {max-width:1280px;padding-top:1.15rem;padding-bottom:3rem;}
p,li,div[data-testid="stMarkdownContainer"] {font-size:1.05rem;line-height:1.72;}
.stButton>button,.stDownloadButton>button {font-size:1.04rem!important;min-height:44px;}
input {font-size:1.08rem!important;}
.agri-hero {padding:22px 25px;border-radius:20px;background:linear-gradient(110deg,#12573c,#20825c);color:#fff;margin-bottom:18px;}
.agri-hero h1 {color:#fff;font-size:2.1rem;margin:0 0 3px 0;}
.agri-hero p {color:#effff6;margin:0;font-size:1.03rem;}
.agri-note {background:#eaf6ee;padding:11px 15px;border-radius:12px;border-left:4px solid #209266;margin-top:9px;}
[data-testid="stMetricValue"] {font-size:1.8rem;}
</style>""", unsafe_allow_html=True)


def secret(name):
    try:
        return str(st.secrets.get(name, ""))
    except (KeyError, FileNotFoundError):
        return ""


app_password=secret("APP_PASSWORD")
if app_password:
    entered=st.text_input("รหัสผ่านเข้าใช้ระบบ",type="password")
    if not hmac.compare_digest(entered,app_password):
        st.info("กรุณาใส่รหัสผ่านเพื่อเข้าใช้งาน")
        st.stop()

@st.cache_data(show_spinner=False)
def load_csv(filename,size,mtime):
    df=pd.read_csv(BASE/filename,encoding="utf-8-sig",dtype=str,keep_default_na=False)
    nums=[x for x in df.columns if x.endswith(('(ตัน)','(ไร่)','(กก./ไร่)'))]
    for col in nums:
        df[col]=pd.to_numeric(df[col].astype(str).str.replace(',','',regex=False),errors='coerce')
    return df


def get_data(filename):
    path=BASE/filename
    if not path.is_file():
        st.error(f"ไม่พบไฟล์ {filename} ใน GitHub")
        st.stop()
    return load_csv(filename,path.stat().st_size,path.stat().st_mtime_ns)

province=get_data('province_data.csv')
district=get_data('data.csv')
CROP_P='พืช'; PROV='จังหวัด'
PROD='ผลผลิต (ตัน)'; AREA='พื้นที่เก็บเกี่ยว (ไร่)'; YIELD='ผลผลิตต่อไร่ (กก./ไร่)'
DPROD='ผลผลิตรายอำเภอ (ตัน)'; DAREA='พื้นที่เก็บเกี่ยวอำเภอ (ไร่)'
REFYIELD='ผลผลิตเฉลี่ยจังหวัด สศก. (กก./ไร่)'

PROVINCE_NAMES={
 'อุดรธานี':['อุดรธานี','อุดร'],'หนองบัวลำภู':['หนองบัวลำภู','หนองบัว'],
 'หนองคาย':['หนองคาย'],'ขอนแก่น':['ขอนแก่น'],
 'กาฬสินธุ์':['กาฬสินธุ์','กาฬ'],'บึงกาฬ':['บึงกาฬ'],
 'มหาสารคาม':['มหาสารคาม','มหาสาร'],'สกลนคร':['สกลนคร','สกล']}
CROP_NAMES={
 'ข้าวนาปี':['ข้าวนาปี','นาปี'],'ข้าวนาปรัง':['ข้าวนาปรัง','นาปรัง'],
 'อ้อยโรงงาน':['อ้อยโรงงาน','อ้อย'],'มันสำปะหลังโรงงาน':['มันสำปะหลังโรงงาน','มันสำปะหลัง','มัน'],
 'ยางพารา':['ยางพารา','ยาง'],'ปาล์มน้ำมัน':['ปาล์มน้ำมัน','ปาล์ม'],
 'ข้าวโพดเลี้ยงสัตว์':['ข้าวโพดเลี้ยงสัตว์','ข้าวโพด']}


def compact(q):
    return re.sub(r'\s+','',str(q)).replace('จังหวัด','').replace('จ.','')


def detect_province(q):
    norm=compact(q)
    hits=[]; occupied=[]
    aliases=sorted([(al,p) for p,als in PROVINCE_NAMES.items() for al in als],key=lambda x:len(x[0]),reverse=True)
    for al,p in aliases:
        idx=norm.find(al)
        if idx>=0 and not any(max(idx,a)<min(idx+len(al),b) for a,b in occupied):
            if p not in hits: hits.append(p)
            occupied.append((idx,idx+len(al)))
    return hits


def detect_crops(q):
    s=compact(q)
    full=[]
    for crop,words in CROP_NAMES.items():
        if any(w in s for w in words):full.append(crop)
    if ('ข้าว' in s) and not any(x.startswith('ข้าวนา') for x in full):
        full.extend(['ข้าวนาปี','ข้าวนาปรัง'])
    return list(dict.fromkeys(full))


def is_rice_ambiguous(q,cs):
    s=compact(q)
    return 'ข้าว' in s and len(cs)==2 and set(cs)=={'ข้าวนาปี','ข้าวนาปรัง'}


def fmt(x,dec=2):
    return 'ไม่พบข้อมูล' if pd.isna(x) else f'{x:,.{dec}f}'


def tidy_table(df, cols):
    z=df[cols].copy()
    num=z.select_dtypes(include='number').columns
    for col in num:
        z[col]=z[col].map(lambda x:fmt(x))
    return z


def as_csv(df):
    return df.to_csv(index=False,encoding='utf-8-sig').encode('utf-8-sig')


def title_prov(rows):
    return '、'.join(rows) if rows else '8 จังหวัด'


def respond(question):
    s=compact(question)
    ctx=st.session_state.setdefault('agri_context',{'provinces':[],'crops':[],'level':'จังหวัด','coverage':False})
    provinces=detect_province(question)
    crops=detect_crops(question)
    all_n= len(set(province['จังหวัด']))
    is_coverage=bool(re.search(r'มีข้อมูล|มีอะไรบ้าง|มีไหม|รู้จัก|ครอบคลุม',s))
    short_followup=('ด้วย' in s or s in ('ต่อ','แล้วหนองบัว','ขอนแก่น','อุดร','อุดรธานี','หนองบัว','นาปี','นาปรัง'))
    if not provinces and short_followup and not is_coverage:provinces=ctx['provinces'][:]
    if not crops and not is_coverage and short_followup:
        crops=ctx['crops'][:]
    if ctx.get('coverage') and 'ด้วย' in s and provinces:is_coverage=True
    level='อำเภอ' if any(w in s for w in ['รายอำเภอ','แต่ละอำเภอ','อำเภอไหน','แยกอำเภอ']) else ('จังหวัด' if 'ระดับจังหวัด' in s else ctx['level'] if short_followup and not is_coverage else 'จังหวัด')
    if 'จังหวัดไหน' in s or 'กี่จังหวัด' in s:provinces=[]
    if provinces:ctx['provinces']=provinces
    if crops:ctx['crops']=crops
    ctx['level']=level
    ctx['coverage']=is_coverage
    years=re.findall(r'25\d{2}',s)
    years=list(dict.fromkeys(years))
    if is_coverage and not any(w in s for w in ['ผลิต','ผลผลิต','พื้นที่เก็บเกี่ยว']):
        if ('จังหวัดไหน' in s or 'กี่จังหวัด' in s or 'ทั้งหมด' in s or not provinces):
            return f'✅ มีข้อมูล **{all_n} จังหวัด** ได้แก่ '+', '.join(sorted(province['จังหวัด'].unique()))+'',None,'จังหวัด'
        lines=[]
        for p in provinces:
            pv=province[province['จังหวัด']==p]
            di=district[district['จังหวัด']==p]
            valid=di[DPROD].notna()
            lines.append(f'✅ **{p}**: ข้อมูลจังหวัด {len(pv)} พืช, รายการอำเภอ×พืช {len(di)} แถว (มีผลผลิตรายอำเภอ {valid.sum()} แถว)')
        return '\n\n'.join(lines),None,'จังหวัด'
    if 'ข้าว' in s and not crops:crops=['ข้าวนาปี','ข้าวนาปรัง']
    if not crops:
        if provinces:
            names=', '.join(province[province['จังหวัด'].isin(provinces)]['พืช'].unique())
            return f'มีข้อมูล **{title_prov(provinces)}** ครับ ชนิดพืชที่มี: {names}\n\nลองถาม เช่น “ผลผลิตอ้อยอุดร” หรือ “ข้าวขอนแก่น”',None,'จังหวัด'
        return 'ลองถาม “อ้อยอุดร” “ข้าวขอนแก่น” หรือ “มีข้อมูลจังหวัดไหนบ้าง” ได้เลยครับ',None,'จังหวัด'
    if not provinces and is_rice_ambiguous(question,crops) and 'จัดอันดับ' not in s and not any(w in s for w in ['มากที่สุด','สูงสุด']):
        return 'ต้องการ **ข้าวนาปี** หรือ **ข้าวนาปรัง** ครับ? หรือระบุจังหวัด เช่น “ข้าวขอนแก่น” เพื่อดูทั้งสองประเภทแยกกัน',None,'จังหวัด'
    if is_rice_ambiguous(question,crops) and any(w in s for w in ['มากที่สุด','สูงสุด','อันดับ']):
        return 'ต้องการจัดอันดับ **ข้าวนาปี** หรือ **ข้าวนาปรัง** ครับ? เพื่อไม่เปรียบเทียบข้อมูลคนละฤดู',None,'จังหวัด'
    if level=='อำเภอ' and not provinces:
        return 'ต้องการข้อมูลรายอำเภอของ **จังหวัดไหน** ครับ? เช่น “อ้อยรายอำเภออุดร”',None,'อำเภอ'
    if level=='จังหวัด':
        df=province[province['พืช'].isin(crops)]
        if provinces:df=df[df['จังหวัด'].isin(provinces)]
        if years:df=df[df['ปีสถิติ'].astype(str).isin(years)]
        if df.empty:
            avail=province[province['พืช'].isin(crops)]
            return f'ไม่พบข้อมูลตามเงื่อนไขนี้ (ปีที่มีในฐานสำหรับพืชนี้: {", ".join(sorted(set(avail["ปีสถิติ"].astype(str))))})',None,'จังหวัด'
        comparison=any(w in s for w in ['มากที่สุด','สูงสุด','อันดับ','เทียบ','เปรียบเทียบ'])
        if comparison and len(df['ปีสถิติ'].unique())>1:
            return 'ข้อมูลมีคนละปีสถิติ กรุณาระบุ **ปีเดียวกัน** ก่อนจัดอันดับครับ',None,'จังหวัด'
        if comparison and len(df['พืช'].unique())>1:
            return 'กรุณาเลือก **พืชชนิดเดียวกัน** ก่อนจัดอันดับครับ',None,'จังหวัด'
        if comparison:
            measure=YIELD if 'ต่อไร่' in s else AREA if 'พื้นที่' in s else PROD
            df=df.sort_values(measure,ascending=False)
        else:df=df.sort_values(['จังหวัด','พืช'])
        t=df[['จังหวัด','พืช','ปีสถิติ','ผลผลิต (ตัน)','พื้นที่เก็บเกี่ยว (ไร่)','ผลผลิตต่อไร่ (กก./ไร่)','อ้างอิงตาราง/หน้า']]
        intro=f'**ผลข้อมูลระดับจังหวัด** · {title_prov(provinces)} · {", ".join(crops)} · {len(df)} รายการ\n\n'
        lines=[]
        for _,r in df.head(8).iterrows():
            lines.append(f'**{r["จังหวัด"]} · {r["พืช"]} · ปี {r["ปีสถิติ"]}** — ผลผลิต **{fmt(r[PROD])} ตัน** · ผลผลิตต่อไร่ **{fmt(r[YIELD])} กก./ไร่** · พื้นที่ **{fmt(r[AREA])} ไร่**  \nที่มา: สศก. {r["อ้างอิงตาราง/หน้า"]} ([ต้นฉบับ]({r["ลิงก์ สศก."]}))')
        return intro+'\n\n'.join(lines)+('\n\nมีข้อมูลเพิ่มเติมในตาราง' if len(df)>8 else ''),t,'จังหวัด'
    df=district[district['พืช'].isin(crops)]
    df=df[df['จังหวัด'].isin(provinces)]
    if years:df=df[df['ปีข้อมูลรายอำเภอ'].astype(str).str.contains('|'.join(years),regex=True)]
    if df.empty:return 'ไม่พบข้อมูลรายอำเภอที่ตรงตามเงื่อนไขและปีที่ระบุครับ ลองถามโดยไม่กำหนดปี',None,'อำเภอ'
    avail=df[df[DPROD].notna()]
    nmissing=len(df)-len(avail)
    text=f'**ข้อมูลรายอำเภอ {title_prov(provinces)} · {", ".join(crops)}** มีผลผลิตรายอำเภอ {len(avail)} จาก {len(df)} แถว; ยังไม่มีผลผลิตรายอำเภอ {nmissing} แถว\n\n'
    if not avail.empty:
        preview=avail[['อำเภอ','พืช','ปีข้อมูลรายอำเภอ',DPROD,'สถานะข้อมูลรายอำเภอ','แหล่งรายอำเภอ']].head(15)
        for _,r in preview.iterrows():
            text+=f'- **{r["อำเภอ"]}** · {r["พืช"]} · ปี {r["ปีข้อมูลรายอำเภอ"]}: **{fmt(r[DPROD])} ตัน** ({r["สถานะข้อมูลรายอำเภอ"]})\n'
    else:
        text+='ยังไม่พบตัวเลขผลผลิตรายอำเภอที่ตรวจสอบได้จากไฟล์ต้นทาง **จึงไม่นำยอดจังหวัดมาแสดงเป็นผลผลิตรายอำเภอ**\n'
    text+='\n📏 **ผลผลิตต่อไร่ในตารางอำเภอเป็นค่าเฉลี่ยระดับจังหวัดของ สศก. เพื่ออ้างอิงเท่านั้น** ไม่ใช่อัตราที่สำรวจจริงของอำเภอ'
    return text,df[['จังหวัด','อำเภอ','พืช','ปีข้อมูลรายอำเภอ',DPROD,DAREA,'สถานะข้อมูลรายอำเภอ',REFYIELD,'ปีสถิติจังหวัด สศก.','อ้างอิง สศก.']],'อำเภอ'

st.markdown('<div class="agri-hero"><h1>🌾 AgriData AI</h1><p>ถามข้อมูลการเกษตร 8 จังหวัด · แยกข้อมูลรายจังหวัดและรายอำเภออย่างชัดเจน · อ้างอิงย้อนกลับได้</p></div>',unsafe_allow_html=True)

with st.container():
    st.subheader('💬 ถามข้อมูลได้เลย')
    st.caption('ตัวอย่าง: อุดร อ้อย · อุดรข้าว · มีข้อมูลหนองบัวไหม · อ้อยรายอำเภออุดร · ผลผลิตต่อไร่ข้าวนาปีขอนแก่น')
    if 'agri_history' not in st.session_state:st.session_state['agri_history']=[]
    with st.container(height=470,border=True):
        for msg in st.session_state.agri_history:
            with st.chat_message(msg['role']):
                st.markdown(msg['content'])
                if msg.get('table') is not None:
                    st.dataframe(msg['table'],use_container_width=True,hide_index=True)
        if not st.session_state.agri_history:
            st.info('พิมพ์คำถามด้านล่างได้ทันที ระบบค้นตัวเลขจากฐานข้อมูลโดยตรง ไม่เดาผลผลิต')
    with st.form('chat',clear_on_submit=True):
        query=st.text_input('พิมพ์คำถามที่นี่',placeholder='เช่น อุดร อ้อย / ข้าวขอนแก่น / อ้อยรายอำเภออุดร')
        c1,c2=st.columns([3,1]); send=c1.form_submit_button('➤ ส่งคำถาม',use_container_width=True)
    if send and query.strip():
        reply,table,level=respond(query.strip())
        st.session_state.agri_history.extend([
            {'role':'user','content':query.strip()},
            {'role':'assistant','content':reply,'table':tidy_table(table,list(table.columns)) if table is not None else None}])
        st.rerun()
    if st.button('🧹 เริ่มบทสนทนาใหม่'):
        st.session_state.agri_history=[]
        st.session_state.agri_context={'provinces':[],'crops':[],'level':'จังหวัด','coverage':False}
        st.rerun()

st.divider()
with st.container():
    st.subheader('📏 Dashboard · ผลผลิตต่อไร่เป็นตัวชี้วัดหลัก')
    st.caption('ตัวเลขระดับจังหวัดจากเอกสาร สศก. · ไม่ใช้ตัวเลขจังหวัดซ้ำในรายอำเภอ')
    cols=st.columns([1.2,1.2,0.9])
    crops=sorted(province['พืช'].unique().tolist())
    crop=cols[0].selectbox('ชนิดพืช',crops,index=crops.index('อ้อยโรงงาน') if 'อ้อยโรงงาน' in crops else 0)
    place=cols[1].selectbox('จังหวัด',['ทั้งหมด']+sorted(province['จังหวัด'].unique().tolist()))
    dashboard_df=province[province['พืช']==crop].copy()
    if place!='ทั้งหมด':dashboard_df=dashboard_df[dashboard_df['จังหวัด']==place]
    years=sorted(dashboard_df['ปีสถิติ'].unique().tolist(),reverse=True)
    year=cols[2].selectbox('ปีสถิติ',years) if years else None
    if year is not None:dashboard_df=dashboard_df[dashboard_df['ปีสถิติ']==year].copy()
    if dashboard_df.empty:
        st.warning('ไม่มีข้อมูลตรงตามตัวกรอง')
    else:
        leader=dashboard_df.sort_values(YIELD,ascending=False).iloc[0]
        a,b,c,d=st.columns(4)
        a.metric(f'⭐ ผลผลิตต่อไร่สูงสุด · {leader["จังหวัด"]}',f'{leader[YIELD]:,.2f} กก./ไร่')
        if len(dashboard_df)==1:
            b.metric('ผลผลิตรวม',f'{dashboard_df[PROD].iloc[0]:,.2f} ตัน')
            c.metric('พื้นที่เก็บเกี่ยว',f'{dashboard_df[AREA].iloc[0]:,.2f} ไร่')
        else:
            b.metric('จังหวัดในชุดเปรียบเทียบ',len(dashboard_df))
            c.metric('ปีสถิติ',year)
        d.metric('เอกสารอ้างอิง สศก.',f'{len(dashboard_df)} รายการ')
        st.markdown('**อันดับผลผลิตต่อไร่รายจังหวัด (กก./ไร่)**')
        st.bar_chart(dashboard_df.set_index('จังหวัด')[[YIELD]],use_container_width=True,horizontal=True)
        st.markdown('**ผลผลิตรวม (ตัน)**')
        st.bar_chart(dashboard_df.set_index('จังหวัด')[[PROD]],use_container_width=True,horizontal=True)
        st.markdown('**พื้นที่เก็บเกี่ยว (ไร่)**')
        st.bar_chart(dashboard_df.set_index('จังหวัด')[[AREA]],use_container_width=True,horizontal=True)
        st.caption('ที่มา: สำนักงานเศรษฐกิจการเกษตร · '+', '.join(dashboard_df['อ้างอิงตาราง/หน้า'].unique()))
        st.dataframe(tidy_table(dashboard_df,['จังหวัด','พืช','ปีสถิติ',PROD,AREA,YIELD,'อ้างอิงตาราง/หน้า','ลิงก์ สศก.']),use_container_width=True,hide_index=True)
        st.download_button('⬇️ ดาวน์โหลดตารางจังหวัด CSV',data=as_csv(dashboard_df),file_name='agri_province_filtered.csv',mime='text/csv')

st.divider()
with st.expander('📚 ที่มาและตรวจสอบข้อมูลต้นทาง', expanded=False):
    n_avail=int(district[DPROD].notna().sum())
    p1,p2,p3=st.columns(3)
    p1.metric('ข้อมูลจังหวัด สศก.',len(province))
    p2.metric('มีตัวเลขผลผลิตรายอำเภอ',n_avail)
    p3.metric('ยังไม่พบผลผลิตรายอำเภอ',len(district)-n_avail)
    st.markdown('**ข้อควรทราบ:** ค่าเฉลี่ยผลผลิตต่อไร่ที่ปรากฏในข้อมูลรายอำเภอเป็น **ค่าเฉลี่ยจังหวัดของ สศก.** เพื่ออ้างอิง ไม่ใช่ตัวเลขผลผลิตต่อไร่ที่วัดระดับอำเภอ')
    st.markdown('ตารางรายอำเภอที่ไม่พบตัวเลข จะเว้นว่าง ไม่ใช้ผลผลิตจังหวัดแทน และทุกแถวมีชื่อไฟล์/แท็บ/แถวต้นทางกำกับ')
    with st.expander('ดูข้อมูลระดับจังหวัดจาก สศก.'):
        st.dataframe(province,use_container_width=True,hide_index=True)
    with st.expander('ดูข้อมูลระดับอำเภอครบ 590 แถว'):
        st.dataframe(district,use_container_width=True,hide_index=True)
    c1,c2=st.columns(2)
    c1.download_button('ดาวน์โหลดข้อมูลจังหวัด CSV',as_csv(province),'province_data.csv',mime='text/csv')
    c2.download_button('ดาวน์โหลดข้อมูลอำเภอ CSV',as_csv(district),'data.csv',mime='text/csv')

st.caption('AgriData AI · จัดเรียงใหม่จาก Excel ต้นทาง 8 จังหวัด · ข้อมูลจากแหล่งอ้างอิงเดิม ไม่ใช่การตรวจยืนยัน PDF สศก. ฉบับจริงทุกตัวเลข')
