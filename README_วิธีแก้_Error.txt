AgriData AI V3.4.1: แก้ TypeError Altair / Python 3.14

สาเหตุ: requirements.txt รุ่นก่อนใช้ altair>=5,<6 บน Python 3.14
ซึ่งล้มเหลวตอน import altair (TypedDict, closed=True)

วิธีทำ
1. แตกไฟล์ ZIP นี้
2. เปิด GitHub Repository agridata-ai > Add file > Upload files
3. เลือก app.py, requirements.txt, data.csv, province_data.csv จาก ZIP
4. กด Commit changes แล้วรอ Cloud ติดตั้งแพ็กเกจใหม่
5. เปิดแอปอีกครั้ง หาก Error ค้างอยู่ ให้ Manage app > Reboot app
   และตรวจ logs ว่าติดตั้ง Altair 6 แล้ว

ไฟล์ CSV ทั้ง 2 เหมือนกับชุด V3.4 ทุกไบต์ (ไม่ได้แก้ตัวเลขแม้แต่ค่าเดียว)
แอปและหน้าตายึด V3.4 เดิม; อัปเดตเฉพาะ dependency เพื่อรองรับ Python 3.14
ไม่ต้องเปลี่ยน API Key หรือ Secrets

หมายเหตุ: ทดสอบการ compile และการตรวจข้อมูล CSV ในเครื่องแล้ว
แต่เครื่องนี้ไม่สามารถดึงแพ็กเกจจาก PyPI จึงยังไม่ทดสอบ runtime บน Python 3.14 จริง
