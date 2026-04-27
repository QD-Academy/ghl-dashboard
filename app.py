import os, time, sqlite3, threading, requests, calendar, re, io
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)
DB_PATH = "/data/ghl_dashboard.db" if os.path.exists("/data") else "/tmp/ghl_dashboard.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS usage_monthly (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month TEXT NOT NULL, service TEXT NOT NULL,
        message_count INTEGER DEFAULT 0, cost REAL DEFAULT 0.0,
        source TEXT DEFAULT 'api', UNIQUE(month, service))""")
    conn.commit()
    conn.close()
    print(f"DB ready at {DB_PATH}", flush=True)

init_db()

LABELS = {
    "content_ai": "Content AI",
    "whatsapp_marketing": "WhatsApp Marketing Messages",
    "whatsapp_utility": "WhatsApp Utility Messages",
    "email": "Emails", "email_notification": "Email Notifications",
    "email_verification": "LC Email Verification",
    "conversation_voice_ai": "Conversation & Voice AI",
    "reviews_ai": "Reviews AI", "workflow_premium": "Workflow - Premium Features",
    "sms": "SMS", "calls": "Calls", "other": "Other",
}
COLORS = {
    "content_ai": "#F97316", "whatsapp_marketing": "#25D366",
    "whatsapp_utility": "#128C7E", "email": "#3B82F6",
    "email_notification": "#60A5FA", "email_verification": "#10B981",
    "conversation_voice_ai": "#F59E0B", "reviews_ai": "#EF4444",
    "workflow_premium": "#7C3AED", "sms": "#6366F1",
    "calls": "#EC4899", "other": "#9CA3AF",
}
BASE_URL = "https://services.leadconnectorhq.com"
API_VER = "2021-07-28"
last_fetch = None
fetch_status = "idle"

def ghl_get(session, path, params={}):
    for _ in range(3):
        try:
            r = session.get(f"{BASE_URL}{path}", params=params, timeout=30)
            if r.status_code == 200: return r.json()
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 10)))
                continue
            return {}
        except: time.sleep(2)
    return {}

def upsert(month, service, qty, cost, source="api"):
    conn = get_db()
    conn.execute("""INSERT INTO usage_monthly(month,service,message_count,cost,source)
        VALUES(?,?,?,?,?) ON CONFLICT(month,service) DO UPDATE SET
        message_count=excluded.message_count,cost=excluded.cost,source=excluded.source""",
        (month, service, qty, cost, source))
    conn.commit()
    conn.close()

def parse_pdf(pdf_bytes):
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(io.BytesIO(pdf_bytes))
    except ImportError:
        import subprocess, sys
        subprocess.check_call([sys.executable,"-m","pip","install","pdfminer.six","-q"])
        from pdfminer.high_level import extract_text
        text = extract_text(io.BytesIO(pdf_bytes))

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    print("Lines:", lines[:60], flush=True)

    SVCS = [
        ("content ai","content_ai"),
        ("whatsapp utility","whatsapp_utility"),
        ("whatsapp marketing","whatsapp_marketing"),
        ("workflow - premium","workflow_premium"),
        ("workflow premium","workflow_premium"),
        ("email notifications","email_notification"),
        ("lc email verification","email_verification"),
        ("conversation and voice","conversation_voice_ai"),
        ("conversation & voice","conversation_voice_ai"),
        ("reviews ai","reviews_ai"),
        ("emails","email"),
        ("sms","sms"),
        ("calls","calls"),
    ]

    qty_idx = next((i for i,l in enumerate(lines) if l.upper()=="QTY"), None)
    total_idx = next((i for i,l in enumerate(lines) if l.upper()=="TOTAL"), None)
    print(f"QTY at {qty_idx}, TOTAL at {total_idx}", flush=True)

    qtys = []
    if qty_idx is not None and total_idx is not None:
        for l in lines[qty_idx+1:total_idx]:
            if re.match(r"^[\d,]+$", l):
                qtys.append(int(l.replace(",","")))

    totals = []
    if total_idx is not None:
        for l in lines[total_idx+1:]:
            m = re.match(r"^\$?([\d,]+\.\d{2})$", l)
            if m:
                v = float(m.group(1).replace(",",""))
                if v > 0: totals.append(v)
            if "total products" in l.lower() or "total charged" in l.lower():
                break

    svc_keys = []
    seen = set()
    skip = ["item name","unit price","products","total products","total charged",
            "amount due","billed","duration","start date","end date",
            "highlevel","stripe","ein:","qty","total","usage receipts",
            "id:","date:","dallas","singapore","hive","carpenter"]
    for line in lines:
        ll = line.lower()
        if any(h in ll for h in skip): continue
        for pat, key in SVCS:
            if pat in ll and key not in seen:
                svc_keys.append(key)
                seen.add(key)
                break

    print(f"Services: {svc_keys}", flush=True)
    print(f"Qtys: {qtys}", flush=True)
    print(f"Totals: {totals}", flush=True)

    results = []
    for i, svc in enumerate(svc_keys):
        qty = qtys[i] if i < len(qtys) else 0
        total = totals[i] if i < len(totals) else 0
        if total > 0:
            results.append((svc, qty, total))
            print(f"  OK: {svc} qty={qty} total={total}", flush=True)
    return results

@app.route("/api/upload-pdf", methods=["POST"])
def upload_pdf():
    try:
        if "file" not in request.files:
            return jsonify({"error":"No file"}),400
        f = request.files["file"]
        month = request.form.get("month","")
        if not month:
            return jsonify({"error":"No month"}),400
        rows = parse_pdf(f.read())
        if not rows:
            return jsonify({"error":"Could not parse PDF"}),400
        conn = get_db()
        conn.execute("DELETE FROM usage_monthly WHERE month=? AND source='pdf'",(month,))
        conn.commit()
        conn.close()
        for svc,qty,cost in rows:
            upsert(month,svc,qty,cost,"pdf")
        return jsonify({"success":True,"rows_imported":len(rows),
            "services":[{"service":r[0],"qty":r[1],"cost":r[2]} for r in rows]})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error":str(e)}),500

@app.route("/api/import-hardcoded")
def import_hardcoded():
    month = request.args.get("month","2026-03")
    DATA = [
        ("content_ai",30,1.30),
        ("whatsapp_utility",1579,18.76),
        ("whatsapp_marketing",8088,621.41),
        ("workflow_premium",677,6.77),
        ("email_notification",679,0.66),
        ("email_verification",5629,14.07),
        ("conversation_voice_ai",35,0.12),
        ("reviews_ai",3,0.03),
        ("email",162375,109.61),
    ]
    conn = get_db()
    conn.execute("DELETE FROM usage_monthly WHERE month=?",(month,))
    for svc,qty,cost in DATA:
        conn.execute("INSERT INTO usage_monthly(month,service,message_count,cost,source) VALUES(?,?,?,?,'pdf')",(month,svc,qty,cost))
    conn.commit()
    conn.close()
    return jsonify({"success":True,"month":month,"rows":len(DATA)})

@app.route("/api/reset-month")
def reset_month():
    month = request.args.get("month","")
    if not month: return jsonify({"error":"No month"}),400
    conn = get_db()
    conn.execute("DELETE FROM usage_monthly WHERE month=?",(month,))
    conn.commit()
    conn.close()
    return jsonify({"success":True,"cleared":month})

@app.route("/health")
def health():
    return jsonify({"status":"ok","db":DB_PATH})

@app.route("/api/data")
def api_data():
    try:
        conn = get_db()
        months = [r[0] for r in conn.execute("SELECT DISTINCT month FROM usage_monthly ORDER BY month DESC").fetchall()]
        result = {}
        for i,month in enumerate(months):
            rows = conn.execute("SELECT service,message_count,cost,source FROM usage_monthly WHERE month=? ORDER BY cost DESC",(month,)).fetchall()
            total = sum(r["cost"] for r in rows)
            prev = months[i+1] if i+1<len(months) else None
            pt = 0.0
            if prev:
                pr = conn.execute("SELECT COALESCE(SUM(cost),0) t FROM usage_monthly WHERE month=?",(prev,)).fetchone()
                pt = pr["t"] if pr else 0.0
            mom = ((total-pt)/pt*100) if pt>0 else (100 if total>0 else 0)
            ps = {}
            if prev:
                for pr in conn.execute("SELECT service,cost FROM usage_monthly WHERE month=?",(prev,)).fetchall():
                    ps[pr["service"]] = pr["cost"]
            cards = []
            for r in rows:
                if r["cost"]<=0 and r["message_count"]<=0: continue
                pc = ps.get(r["service"],0)
                cm = ((r["cost"]-pc)/pc*100) if pc>0 else (100 if r["cost"]>0 else 0)
                cards.append({"service":r["service"],"label":LABELS.get(r["service"],r["service"]),
                    "color":COLORS.get(r["service"],"#6B7280"),"message_count":r["message_count"],
                    "cost":round(r["cost"],2),"prev_cost":round(pc,2),
                    "pct_of_total":round((r["cost"]/total*100) if total>0 else 0,1),
                    "mom_pct":round(cm,1),"source":r["source"]})
            tm = months[i:i+6][::-1]
            trend = {}
            for t in tm:
                for tr in conn.execute("SELECT service,cost FROM usage_monthly WHERE month=?",(t,)).fetchall():
                    if tr["service"] not in trend: trend[tr["service"]] = {}
                    trend[tr["service"]][t] = round(tr["cost"],2)
            result[month] = {"total":round(total,2),"prev_total":round(pt,2),"mom_pct":round(mom,1),
                "prev_month":prev,"cards":cards,"trend":trend,"trend_months":tm,
                "has_pdf":any(r["source"]=="pdf" for r in rows)}
        conn.close()
        ls = last_fetch.strftime("%Y-%m-%d %H:%M UTC") if last_fetch else "Estimated"
        return jsonify({"months":months,"data":result,"last_sync":ls,"fetch_status":fetch_status})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error":str(e),"months":[],"data":{}}),500


HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Credit Usage Dashboard</title><link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"><style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'DM Sans',system-ui,sans-serif;background:#F0F2F5;min-height:100vh;color:#111827}.hdr{background:#fff;border-bottom:1px solid #E5E7EB;padding:16px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}.hdr h1{font-size:17px;font-weight:700}.hdr-sub{font-size:12px;color:#6B7280;margin-top:2px}.hdr-right{display:flex;align-items:center;gap:10px}.sync-badge{font-size:11px;color:#9CA3AF;background:#F9FAFB;border:1px solid #E5E7EB;padding:4px 10px;border-radius:20px}.upload-btn{font-size:12px;font-weight:600;color:#4F46E5;background:#EEF2FF;border:1px solid #C7D2FE;padding:6px 14px;border-radius:8px;cursor:pointer}.upload-btn:hover{background:#E0E7FF}.tabs-wrap{background:#fff;border-bottom:1px solid #E5E7EB;padding:0 28px;overflow-x:auto}.tabs{display:flex;gap:2px;min-width:max-content}.tab{padding:13px 18px;font-size:13px;font-weight:500;color:#6B7280;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}.tab.active{color:#4F46E5;border-bottom-color:#4F46E5;font-weight:600}.ct{padding:24px 28px;max-width:1100px;margin:0 auto}.banner{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:24px}.total-amt{font-size:30px;font-weight:700}.banner-lbl{font-size:14px;color:#6B7280}.pdf-badge{font-size:11px;font-weight:600;color:#059669;background:#DCFCE7;border:1px solid #BBF7D0;padding:3px 8px;border-radius:20px}.est-badge{font-size:11px;font-weight:600;color:#6B7280;background:#F3F4F6;border:1px solid #E5E7EB;padding:3px 8px;border-radius:20px}.badge{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}.badge-up{background:#DCFCE7;color:#16A34A}.badge-dn{background:#FEE2E2;color:#DC2626}.badge-flat{background:#F3F4F6;color:#6B7280}.chart-wrap{background:#fff;border-radius:14px;padding:20px 24px;margin-bottom:24px;border:1px solid #E5E7EB}.chart-title{font-size:13px;font-weight:600;color:#374151;margin-bottom:16px}.chart-area{position:relative;height:160px}.chart-legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}.legend-item{display:flex;align-items:center;gap:6px;font-size:11px;color:#6B7280}.legend-dot{width:8px;height:8px;border-radius:50%}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}.card{background:#fff;border-radius:14px;padding:18px 20px;border:1px solid #E5E7EB;transition:box-shadow .2s,transform .2s}.card:hover{box-shadow:0 6px 20px rgba(0,0,0,.08);transform:translateY(-2px)}.card-title{font-size:12px;font-weight:600;color:#6B7280;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}.card-row{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:10px}.card-amount{font-size:22px;font-weight:700}.card-from{font-size:11px;color:#9CA3AF}.card-from span{color:#6B7280}.bar-bg{height:5px;background:#F3F4F6;border-radius:99px;overflow:hidden;margin-bottom:7px}.bar-fill{height:100%;border-radius:99px}.card-footer{display:flex;justify-content:space-between}.card-pct{font-size:11px;color:#9CA3AF}.card-badge{font-size:10px;font-weight:600;padding:2px 7px;border-radius:10px}.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:100;align-items:center;justify-content:center}.modal.open{display:flex}.modal-box{background:#fff;border-radius:16px;padding:28px;width:440px;max-width:90vw}.modal-title{font-size:16px;font-weight:700;margin-bottom:6px}.modal-steps{background:#F9FAFB;border-radius:8px;padding:12px 16px;margin-bottom:20px;font-size:12px;color:#374151;line-height:1.8}.modal-field{margin-bottom:16px}.modal-label{font-size:12px;font-weight:600;color:#374151;margin-bottom:6px;display:block}.modal-select,.modal-file{width:100%;padding:9px 12px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;font-family:inherit}.modal-actions{display:flex;gap:10px;margin-top:20px}.btn-primary{flex:1;padding:10px;background:#4F46E5;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer}.btn-primary:hover{background:#4338CA}.btn-secondary{padding:10px 16px;background:#F3F4F6;color:#374151;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer}.msg{font-size:12px;margin-top:12px;padding:8px 12px;border-radius:8px;display:none}.msg.success{background:#DCFCE7;color:#16A34A;display:block}.msg.error{background:#FEE2E2;color:#DC2626;display:block}.empty{text-align:center;padding:60px 20px;color:#9CA3AF}.empty h2{font-size:18px;font-weight:600;color:#6B7280;margin-bottom:8px}.loading{text-align:center;padding:80px;color:#9CA3AF;font-size:14px}.spinner{width:32px;height:32px;border:3px solid #E5E7EB;border-top-color:#4F46E5;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 16px}@keyframes spin{to{transform:rotate(360deg)}}</style></head><body><div class="hdr"><div><h1>Credit Usage Dashboard</h1><div class="hdr-sub">GoHighLevel — Sub-Account Overview</div></div><div class="hdr-right"><button class="upload-btn" onclick="openUpload()">⬆ Upload PDF</button><div class="sync-badge" id="sync">Loading...</div></div></div><div class="tabs-wrap"><div class="tabs" id="tabs"></div></div><div class="ct"><div id="main" class="loading"><div class="spinner"></div>Loading...</div></div><div class="modal" id="uploadModal"><div class="modal-box"><div class="modal-title">Upload GHL Billing PDF</div><div class="modal-steps">1. GHL Agency → Settings → Billing<br>2. Click <b>Product Breakdown</b> tab<br>3. Select sub-account + month<br>4. Click <b>Download</b><br>5. Upload the PDF below</div><div class="modal-field"><label class="modal-label">Month</label><select class="modal-select" id="uploadMonth"></select></div><div class="modal-field"><label class="modal-label">PDF File</label><input type="file" class="modal-file" id="uploadFile" accept=".pdf"></div><div id="uploadMsg" class="msg"></div><div class="modal-actions"><button class="btn-secondary" onclick="closeUpload()">Cancel</button><button class="btn-primary" onclick="submitUpload()">Upload & Import</button></div></div></div><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script><script>let D={},M=[],active=null,chartInst=null;const fmtMonth=m=>{const[y,mo]=m.split("-");return["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+mo-1]+" "+y};function renderTabs(){document.getElementById("tabs").innerHTML=M.map(m=>`<div class="tab${m===active?" active":""}" onclick="switchTo('${m}')">${fmtMonth(m)}</div>`).join("")}function switchTo(m){active=m;renderTabs();renderContent()}function openUpload(){const sel=document.getElementById("uploadMonth");const now=new Date();const opts=[];for(let i=0;i<6;i++){const d=new Date(now.getFullYear(),now.getMonth()-i,1);const val=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}`;opts.push(`<option value="${val}">${fmtMonth(val)}</option>`)}sel.innerHTML=opts.join("");if(active)sel.value=active;document.getElementById("uploadMsg").className="msg";document.getElementById("uploadModal").classList.add("open")}function closeUpload(){document.getElementById("uploadModal").classList.remove("open")}async function submitUpload(){const file=document.getElementById("uploadFile").files[0];const month=document.getElementById("uploadMonth").value;const msg=document.getElementById("uploadMsg");if(!file){msg.className="msg error";msg.textContent="Please select a PDF";return}msg.className="msg";msg.textContent="Uploading...";msg.style.display="block";msg.style.background="#F3F4F6";msg.style.color="#6B7280";const fd=new FormData();fd.append("file",file);fd.append("month",month);try{const r=await fetch("/api/upload-pdf",{method:"POST",body:fd});const j=await r.json();if(j.success){msg.className="msg success";msg.textContent=`Imported ${j.rows_imported} services for ${fmtMonth(month)}`;setTimeout(()=>{closeUpload();load()},2000)}else{msg.className="msg error";msg.textContent="Error: "+(j.error||"Unknown")}}catch(e){msg.className="msg error";msg.textContent="Failed: "+e.message}}function renderContent(){const el=document.getElementById("main"),d=D[active];if(!d){el.innerHTML='<div class="empty"><h2>No data</h2><p>Upload a PDF or wait for sync.</p></div>';return}let mb="";if(d.prev_month){const p=d.mom_pct,cls=p>0?"badge-up":p<0?"badge-dn":"badge-flat",sym=p>0?"↑":p<0?"↓":"→",val=Math.abs(p)>999?">999%":Math.abs(p).toFixed(1)+"%";mb=`<span class="badge ${cls}">${val} ${sym}</span><span class="banner-lbl">vs ${fmtMonth(d.prev_month)}</span>`}const sb=d.has_pdf?'<span class="pdf-badge">✓ Exact GHL Data</span>':'<span class="est-badge">~ Estimated</span>';const ch=d.trend_months&&d.trend_months.length>1?`<div class="chart-wrap"><div class="chart-title">Spending Trend</div><div class="chart-area"><canvas id="trendChart"></canvas></div><div class="chart-legend" id="chartLegend"></div></div>`:"";const ca=d.cards.length?`<div class="grid">${d.cards.map(c=>{const bm=c.mom_pct,bc=bm>0?"badge-up":bm<0?"badge-dn":"badge-flat",bs=bm>0?"↑":bm<0?"↓":"→",bv=Math.abs(bm)>999?">999%":Math.abs(bm).toFixed(1)+"%";return`<div class="card"><div class="card-title">${c.label}</div><div class="card-row"><span class="card-amount" style="color:${c.color}">$${c.cost.toFixed(2)}</span><span class="card-badge ${bc}">${bv} ${bs}</span></div><div class="card-from">from <span>$${c.prev_cost.toFixed(2)}</span> last month</div><div class="bar-bg" style="margin-top:10px"><div class="bar-fill" style="width:${c.pct_of_total}%;background:${c.color}"></div></div><div class="card-footer"><span class="card-pct">${c.pct_of_total}% of total</span><span class="card-pct">${c.message_count.toLocaleString()} units</span></div></div>`}).join("")}</div>`:'<div class="empty"><h2>No data</h2></div>';el.innerHTML=`<div class="banner"><span class="total-amt">$${d.total.toFixed(2)}</span><span class="banner-lbl">total for ${fmtMonth(active)}</span>${mb}${sb}</div>${ch}${ca}`;if(d.trend_months&&d.trend_months.length>1){const top5=d.cards.slice(0,5),labels=d.trend_months.map(fmtMonth),datasets=top5.map(c=>({label:c.label,data:d.trend_months.map(tm=>(d.trend[c.service]||{})[tm]||0),borderColor:c.color,backgroundColor:c.color+"20",borderWidth:2,pointRadius:3,tension:0.35,fill:false}));if(chartInst)chartInst.destroy();chartInst=new Chart(document.getElementById("trendChart"),{type:"line",data:{labels,datasets},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{font:{size:11},color:"#9CA3AF"}},y:{grid:{color:"#F3F4F6"},ticks:{font:{size:11},color:"#9CA3AF",callback:v=>"$"+v.toFixed(2)}}}}});document.getElementById("chartLegend").innerHTML=top5.map(c=>`<div class="legend-item"><div class="legend-dot" style="background:${c.color}"></div>${c.label}</div>`).join("")}}async function load(){try{const r=await fetch("/api/data"),j=await r.json();document.getElementById("sync").textContent="Last synced: "+(j.last_sync||"pending");if(j.error){document.getElementById("main").innerHTML=`<div class="empty"><h2>${j.error}</h2></div>`;return}M=j.months||[];D=j.data||{};if(!M.length){document.getElementById("main").innerHTML='<div class="empty"><h2>No data yet</h2><p>Click Upload PDF to get started.</p></div>';return}if(!active||!M.includes(active))active=M[0];renderTabs();renderContent()}catch(e){document.getElementById("main").innerHTML=`<div class="empty"><h2>${e.message}</h2></div>`}}load();setInterval(load,15*60*1000);</script></body></html>"""

@app.route("/")
def index():
    return render_template_string(HTML)

def run_fetch():
    global last_fetch, fetch_status
    fetch_status = "running"
    token = os.getenv("GHL_ACCESS_TOKEN")
    loc = os.getenv("GHL_LOCATION_ID")
    if not token or not loc:
        fetch_status = "error"; return
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Version": API_VER, "Accept": "application/json"})
    now = datetime.now(timezone.utc)
    months = []
    for i in range(3):
        mo = now.month - i; yr = now.year
        if mo <= 0: mo += 12; yr -= 1
        if (yr, mo) not in months: months.append((yr, mo))
    for yr, mo in months:
        conn = get_db()
        has_pdf = conn.execute("SELECT COUNT(*) c FROM usage_monthly WHERE month=? AND source='pdf'", (f"{yr}-{mo:02d}",)).fetchone()["c"]
        conn.close()
        if has_pdf > 0:
            print(f"  {yr}-{mo:02d}: skip (PDF)", flush=True); continue
        days_in = calendar.monthrange(yr, mo)[1]
        s_ms = int(datetime(yr, mo, 1, tzinfo=timezone.utc).timestamp() * 1000)
        e_ms = int(datetime(yr, mo, days_in, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
        counts = defaultdict(int); offset = 0; total = 0
        while offset < 5000:
            data = ghl_get(session, "/conversations/search", {
                "locationId": loc, "limit": 100, "startAfterDate": s_ms,
                "endDate": e_ms, "sortBy": "last_message_date", "sortOrder": "desc", "offset": offset})
            convs = data.get("conversations", [])
            if not convs: break
            for cv in convs:
                t = (cv.get("lastMessageType", "") or "").lower()
                svc = "whatsapp_marketing" if "whatsapp" in t else "email" if "email" in t else "sms" if "sms" in t else "other"
                counts[svc] += 1
            total += len(convs); offset += 100; time.sleep(0.05)
        for svc, cnt in counts.items():
            upsert(f"{yr}-{mo:02d}", svc, cnt, 0, "api")
    last_fetch = datetime.now(timezone.utc)
    fetch_status = "done"
    print("Fetch complete.", flush=True)

def scheduler():
    time.sleep(5)
    while True:
        try: run_fetch()
        except Exception as e: print(f"Err:{e}", flush=True)
        time.sleep(900)

threading.Thread(target=scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)


HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Credit Usage Dashboard</title><link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"><style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'DM Sans',system-ui,sans-serif;background:#F0F2F5;min-height:100vh;color:#111827}.hdr{background:#fff;border-bottom:1px solid #E5E7EB;padding:16px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}.hdr h1{font-size:17px;font-weight:700}.hdr-sub{font-size:12px;color:#6B7280;margin-top:2px}.hdr-right{display:flex;align-items:center;gap:10px}.sync-badge{font-size:11px;color:#9CA3AF;background:#F9FAFB;border:1px solid #E5E7EB;padding:4px 10px;border-radius:20px}.upload-btn{font-size:12px;font-weight:600;color:#4F46E5;background:#EEF2FF;border:1px solid #C7D2FE;padding:6px 14px;border-radius:8px;cursor:pointer}.upload-btn:hover{background:#E0E7FF}.tabs-wrap{background:#fff;border-bottom:1px solid #E5E7EB;padding:0 28px;overflow-x:auto}.tabs{display:flex;gap:2px;min-width:max-content}.tab{padding:13px 18px;font-size:13px;font-weight:500;color:#6B7280;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}.tab.active{color:#4F46E5;border-bottom-color:#4F46E5;font-weight:600}.ct{padding:24px 28px;max-width:1100px;margin:0 auto}.banner{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:24px}.total-amt{font-size:30px;font-weight:700}.banner-lbl{font-size:14px;color:#6B7280}.pdf-badge{font-size:11px;font-weight:600;color:#059669;background:#DCFCE7;border:1px solid #BBF7D0;padding:3px 8px;border-radius:20px}.est-badge{font-size:11px;font-weight:600;color:#6B7280;background:#F3F4F6;border:1px solid #E5E7EB;padding:3px 8px;border-radius:20px}.badge{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}.badge-up{background:#DCFCE7;color:#16A34A}.badge-dn{background:#FEE2E2;color:#DC2626}.badge-flat{background:#F3F4F6;color:#6B7280}.chart-wrap{background:#fff;border-radius:14px;padding:20px 24px;margin-bottom:24px;border:1px solid #E5E7EB}.chart-title{font-size:13px;font-weight:600;color:#374151;margin-bottom:16px}.chart-area{position:relative;height:160px}.chart-legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}.legend-item{display:flex;align-items:center;gap:6px;font-size:11px;color:#6B7280}.legend-dot{width:8px;height:8px;border-radius:50%}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}.card{background:#fff;border-radius:14px;padding:18px 20px;border:1px solid #E5E7EB;transition:box-shadow .2s,transform .2s}.card:hover{box-shadow:0 6px 20px rgba(0,0,0,.08);transform:translateY(-2px)}.card-title{font-size:12px;font-weight:600;color:#6B7280;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}.card-row{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:10px}.card-amount{font-size:22px;font-weight:700}.card-from{font-size:11px;color:#9CA3AF}.card-from span{color:#6B7280}.bar-bg{height:5px;background:#F3F4F6;border-radius:99px;overflow:hidden;margin-bottom:7px}.bar-fill{height:100%;border-radius:99px}.card-footer{display:flex;justify-content:space-between}.card-pct{font-size:11px;color:#9CA3AF}.card-badge{font-size:10px;font-weight:600;padding:2px 7px;border-radius:10px}.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:100;align-items:center;justify-content:center}.modal.open{display:flex}.modal-box{background:#fff;border-radius:16px;padding:28px;width:440px;max-width:90vw}.modal-title{font-size:16px;font-weight:700;margin-bottom:6px}.modal-steps{background:#F9FAFB;border-radius:8px;padding:12px 16px;margin-bottom:20px;font-size:12px;color:#374151;line-height:1.8}.modal-field{margin-bottom:16px}.modal-label{font-size:12px;font-weight:600;color:#374151;margin-bottom:6px;display:block}.modal-select,.modal-file{width:100%;padding:9px 12px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;font-family:inherit}.modal-actions{display:flex;gap:10px;margin-top:20px}.btn-primary{flex:1;padding:10px;background:#4F46E5;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer}.btn-primary:hover{background:#4338CA}.btn-secondary{padding:10px 16px;background:#F3F4F6;color:#374151;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer}.msg{font-size:12px;margin-top:12px;padding:8px 12px;border-radius:8px;display:none}.msg.success{background:#DCFCE7;color:#16A34A;display:block}.msg.error{background:#FEE2E2;color:#DC2626;display:block}.empty{text-align:center;padding:60px 20px;color:#9CA3AF}.empty h2{font-size:18px;font-weight:600;color:#6B7280;margin-bottom:8px}.loading{text-align:center;padding:80px;color:#9CA3AF;font-size:14px}.spinner{width:32px;height:32px;border:3px solid #E5E7EB;border-top-color:#4F46E5;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 16px}@keyframes spin{to{transform:rotate(360deg)}}</style></head><body><div class="hdr"><div><h1>Credit Usage Dashboard</h1><div class="hdr-sub">GoHighLevel — Sub-Account Overview</div></div><div class="hdr-right"><button class="upload-btn" onclick="openUpload()">⬆ Upload PDF</button><div class="sync-badge" id="sync">Loading...</div></div></div><div class="tabs-wrap"><div class="tabs" id="tabs"></div></div><div class="ct"><div id="main" class="loading"><div class="spinner"></div>Loading...</div></div><div class="modal" id="uploadModal"><div class="modal-box"><div class="modal-title">Upload GHL Billing PDF</div><div class="modal-steps">1. GHL Agency → Settings → Billing<br>2. Click <b>Product Breakdown</b> tab<br>3. Select sub-account + month<br>4. Click <b>Download</b><br>5. Upload the PDF below</div><div class="modal-field"><label class="modal-label">Month</label><select class="modal-select" id="uploadMonth"></select></div><div class="modal-field"><label class="modal-label">PDF File</label><input type="file" class="modal-file" id="uploadFile" accept=".pdf"></div><div id="uploadMsg" class="msg"></div><div class="modal-actions"><button class="btn-secondary" onclick="closeUpload()">Cancel</button><button class="btn-primary" onclick="submitUpload()">Upload & Import</button></div></div></div><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script><script>let D={},M=[],active=null,chartInst=null;const fmtMonth=m=>{const[y,mo]=m.split("-");return["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+mo-1]+" "+y};function renderTabs(){document.getElementById("tabs").innerHTML=M.map(m=>`<div class="tab${m===active?" active":""}" onclick="switchTo('${m}')">${fmtMonth(m)}</div>`).join("")}function switchTo(m){active=m;renderTabs();renderContent()}function openUpload(){const sel=document.getElementById("uploadMonth");const now=new Date();const opts=[];for(let i=0;i<6;i++){const d=new Date(now.getFullYear(),now.getMonth()-i,1);const val=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}`;opts.push(`<option value="${val}">${fmtMonth(val)}</option>`)}sel.innerHTML=opts.join("");if(active)sel.value=active;document.getElementById("uploadMsg").className="msg";document.getElementById("uploadModal").classList.add("open")}function closeUpload(){document.getElementById("uploadModal").classList.remove("open")}async function submitUpload(){const file=document.getElementById("uploadFile").files[0];const month=document.getElementById("uploadMonth").value;const msg=document.getElementById("uploadMsg");if(!file){msg.className="msg error";msg.textContent="Please select a PDF";return}msg.className="msg";msg.textContent="Uploading...";msg.style.display="block";msg.style.background="#F3F4F6";msg.style.color="#6B7280";const fd=new FormData();fd.append("file",file);fd.append("month",month);try{const r=await fetch("/api/upload-pdf",{method:"POST",body:fd});const j=await r.json();if(j.success){msg.className="msg success";msg.textContent=`Imported ${j.rows_imported} services for ${fmtMonth(month)}`;setTimeout(()=>{closeUpload();load()},2000)}else{msg.className="msg error";msg.textContent="Error: "+(j.error||"Unknown")}}catch(e){msg.className="msg error";msg.textContent="Failed: "+e.message}}function renderContent(){const el=document.getElementById("main"),d=D[active];if(!d){el.innerHTML='<div class="empty"><h2>No data</h2><p>Upload a PDF or wait for sync.</p></div>';return}let mb="";if(d.prev_month){const p=d.mom_pct,cls=p>0?"badge-up":p<0?"badge-dn":"badge-flat",sym=p>0?"↑":p<0?"↓":"→",val=Math.abs(p)>999?">999%":Math.abs(p).toFixed(1)+"%";mb=`<span class="badge ${cls}">${val} ${sym}</span><span class="banner-lbl">vs ${fmtMonth(d.prev_month)}</span>`}const sb=d.has_pdf?'<span class="pdf-badge">✓ Exact GHL Data</span>':'<span class="est-badge">~ Estimated</span>';const ch=d.trend_months&&d.trend_months.length>1?`<div class="chart-wrap"><div class="chart-title">Spending Trend</div><div class="chart-area"><canvas id="trendChart"></canvas></div><div class="chart-legend" id="chartLegend"></div></div>`:"";const ca=d.cards.length?`<div class="grid">${d.cards.map(c=>{const bm=c.mom_pct,bc=bm>0?"badge-up":bm<0?"badge-dn":"badge-flat",bs=bm>0?"↑":bm<0?"↓":"→",bv=Math.abs(bm)>999?">999%":Math.abs(bm).toFixed(1)+"%";return`<div class="card"><div class="card-title">${c.label}</div><div class="card-row"><span class="card-amount" style="color:${c.color}">$${c.cost.toFixed(2)}</span><span class="card-badge ${bc}">${bv} ${bs}</span></div><div class="card-from">from <span>$${c.prev_cost.toFixed(2)}</span> last month</div><div class="bar-bg" style="margin-top:10px"><div class="bar-fill" style="width:${c.pct_of_total}%;background:${c.color}"></div></div><div class="card-footer"><span class="card-pct">${c.pct_of_total}% of total</span><span class="card-pct">${c.message_count.toLocaleString()} units</span></div></div>`}).join("")}</div>`:'<div class="empty"><h2>No data</h2></div>';el.innerHTML=`<div class="banner"><span class="total-amt">$${d.total.toFixed(2)}</span><span class="banner-lbl">total for ${fmtMonth(active)}</span>${mb}${sb}</div>${ch}${ca}`;if(d.trend_months&&d.trend_months.length>1){const top5=d.cards.slice(0,5),labels=d.trend_months.map(fmtMonth),datasets=top5.map(c=>({label:c.label,data:d.trend_months.map(tm=>(d.trend[c.service]||{})[tm]||0),borderColor:c.color,backgroundColor:c.color+"20",borderWidth:2,pointRadius:3,tension:0.35,fill:false}));if(chartInst)chartInst.destroy();chartInst=new Chart(document.getElementById("trendChart"),{type:"line",data:{labels,datasets},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{font:{size:11},color:"#9CA3AF"}},y:{grid:{color:"#F3F4F6"},ticks:{font:{size:11},color:"#9CA3AF",callback:v=>"$"+v.toFixed(2)}}}}});document.getElementById("chartLegend").innerHTML=top5.map(c=>`<div class="legend-item"><div class="legend-dot" style="background:${c.color}"></div>${c.label}</div>`).join("")}}async function load(){try{const r=await fetch("/api/data"),j=await r.json();document.getElementById("sync").textContent="Last synced: "+(j.last_sync||"pending");if(j.error){document.getElementById("main").innerHTML=`<div class="empty"><h2>${j.error}</h2></div>`;return}M=j.months||[];D=j.data||{};if(!M.length){document.getElementById("main").innerHTML='<div class="empty"><h2>No data yet</h2><p>Click Upload PDF to get started.</p></div>';return}if(!active||!M.includes(active))active=M[0];renderTabs();renderContent()}catch(e){document.getElementById("main").innerHTML=`<div class="empty"><h2>${e.message}</h2></div>`}}load();setInterval(load,15*60*1000);</script></body></html>"""



HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Credit Usage Dashboard</title><link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"><style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'DM Sans',system-ui,sans-serif;background:#F0F2F5;min-height:100vh;color:#111827}.hdr{background:#fff;border-bottom:1px solid #E5E7EB;padding:16px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}.hdr h1{font-size:17px;font-weight:700}.hdr-sub{font-size:12px;color:#6B7280;margin-top:2px}.hdr-right{display:flex;align-items:center;gap:10px}.sync-badge{font-size:11px;color:#9CA3AF;background:#F9FAFB;border:1px solid #E5E7EB;padding:4px 10px;border-radius:20px}.upload-btn{font-size:12px;font-weight:600;color:#4F46E5;background:#EEF2FF;border:1px solid #C7D2FE;padding:6px 14px;border-radius:8px;cursor:pointer}.upload-btn:hover{background:#E0E7FF}.tabs-wrap{background:#fff;border-bottom:1px solid #E5E7EB;padding:0 28px;overflow-x:auto}.tabs{display:flex;gap:2px;min-width:max-content}.tab{padding:13px 18px;font-size:13px;font-weight:500;color:#6B7280;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}.tab.active{color:#4F46E5;border-bottom-color:#4F46E5;font-weight:600}.ct{padding:24px 28px;max-width:1100px;margin:0 auto}.banner{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:24px}.total-amt{font-size:30px;font-weight:700}.banner-lbl{font-size:14px;color:#6B7280}.pdf-badge{font-size:11px;font-weight:600;color:#059669;background:#DCFCE7;border:1px solid #BBF7D0;padding:3px 8px;border-radius:20px}.est-badge{font-size:11px;font-weight:600;color:#6B7280;background:#F3F4F6;border:1px solid #E5E7EB;padding:3px 8px;border-radius:20px}.badge{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}.badge-up{background:#DCFCE7;color:#16A34A}.badge-dn{background:#FEE2E2;color:#DC2626}.badge-flat{background:#F3F4F6;color:#6B7280}.chart-wrap{background:#fff;border-radius:14px;padding:20px 24px;margin-bottom:24px;border:1px solid #E5E7EB}.chart-title{font-size:13px;font-weight:600;color:#374151;margin-bottom:16px}.chart-area{position:relative;height:160px}.chart-legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}.legend-item{display:flex;align-items:center;gap:6px;font-size:11px;color:#6B7280}.legend-dot{width:8px;height:8px;border-radius:50%}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}.card{background:#fff;border-radius:14px;padding:18px 20px;border:1px solid #E5E7EB;transition:box-shadow .2s,transform .2s}.card:hover{box-shadow:0 6px 20px rgba(0,0,0,.08);transform:translateY(-2px)}.card-title{font-size:12px;font-weight:600;color:#6B7280;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}.card-row{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:10px}.card-amount{font-size:22px;font-weight:700}.card-from{font-size:11px;color:#9CA3AF}.card-from span{color:#6B7280}.bar-bg{height:5px;background:#F3F4F6;border-radius:99px;overflow:hidden;margin-bottom:7px}.bar-fill{height:100%;border-radius:99px}.card-footer{display:flex;justify-content:space-between}.card-pct{font-size:11px;color:#9CA3AF}.card-badge{font-size:10px;font-weight:600;padding:2px 7px;border-radius:10px}.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:100;align-items:center;justify-content:center}.modal.open{display:flex}.modal-box{background:#fff;border-radius:16px;padding:28px;width:440px;max-width:90vw}.modal-title{font-size:16px;font-weight:700;margin-bottom:6px}.modal-steps{background:#F9FAFB;border-radius:8px;padding:12px 16px;margin-bottom:20px;font-size:12px;color:#374151;line-height:1.8}.modal-field{margin-bottom:16px}.modal-label{font-size:12px;font-weight:600;color:#374151;margin-bottom:6px;display:block}.modal-select,.modal-file{width:100%;padding:9px 12px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;font-family:inherit}.modal-actions{display:flex;gap:10px;margin-top:20px}.btn-primary{flex:1;padding:10px;background:#4F46E5;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer}.btn-primary:hover{background:#4338CA}.btn-secondary{padding:10px 16px;background:#F3F4F6;color:#374151;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer}.msg{font-size:12px;margin-top:12px;padding:8px 12px;border-radius:8px;display:none}.msg.success{background:#DCFCE7;color:#16A34A;display:block}.msg.error{background:#FEE2E2;color:#DC2626;display:block}.empty{text-align:center;padding:60px 20px;color:#9CA3AF}.empty h2{font-size:18px;font-weight:600;color:#6B7280;margin-bottom:8px}.loading{text-align:center;padding:80px;color:#9CA3AF;font-size:14px}.spinner{width:32px;height:32px;border:3px solid #E5E7EB;border-top-color:#4F46E5;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 16px}@keyframes spin{to{transform:rotate(360deg)}}</style></head><body><div class="hdr"><div><h1>Credit Usage Dashboard</h1><div class="hdr-sub">GoHighLevel — Sub-Account Overview</div></div><div class="hdr-right"><button class="upload-btn" onclick="openUpload()">⬆ Upload PDF</button><div class="sync-badge" id="sync">Loading...</div></div></div><div class="tabs-wrap"><div class="tabs" id="tabs"></div></div><div class="ct"><div id="main" class="loading"><div class="spinner"></div>Loading...</div></div><div class="modal" id="uploadModal"><div class="modal-box"><div class="modal-title">Upload GHL Billing PDF</div><div class="modal-steps">1. GHL Agency → Settings → Billing<br>2. Click <b>Product Breakdown</b> tab<br>3. Select sub-account + month<br>4. Click <b>Download</b><br>5. Upload the PDF below</div><div class="modal-field"><label class="modal-label">Month</label><select class="modal-select" id="uploadMonth"></select></div><div class="modal-field"><label class="modal-label">PDF File</label><input type="file" class="modal-file" id="uploadFile" accept=".pdf"></div><div id="uploadMsg" class="msg"></div><div class="modal-actions"><button class="btn-secondary" onclick="closeUpload()">Cancel</button><button class="btn-primary" onclick="submitUpload()">Upload & Import</button></div></div></div><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script><script>let D={},M=[],active=null,chartInst=null;const fmtMonth=m=>{const[y,mo]=m.split("-");return["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+mo-1]+" "+y};function renderTabs(){document.getElementById("tabs").innerHTML=M.map(m=>`<div class="tab${m===active?" active":""}" onclick="switchTo('${m}')">${fmtMonth(m)}</div>`).join("")}function switchTo(m){active=m;renderTabs();renderContent()}function openUpload(){const sel=document.getElementById("uploadMonth");const now=new Date();const opts=[];for(let i=0;i<6;i++){const d=new Date(now.getFullYear(),now.getMonth()-i,1);const val=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}`;opts.push(`<option value="${val}">${fmtMonth(val)}</option>`)}sel.innerHTML=opts.join("");if(active)sel.value=active;document.getElementById("uploadMsg").className="msg";document.getElementById("uploadModal").classList.add("open")}function closeUpload(){document.getElementById("uploadModal").classList.remove("open")}async function submitUpload(){const file=document.getElementById("uploadFile").files[0];const month=document.getElementById("uploadMonth").value;const msg=document.getElementById("uploadMsg");if(!file){msg.className="msg error";msg.textContent="Please select a PDF";return}msg.className="msg";msg.textContent="Uploading...";msg.style.display="block";msg.style.background="#F3F4F6";msg.style.color="#6B7280";const fd=new FormData();fd.append("file",file);fd.append("month",month);try{const r=await fetch("/api/upload-pdf",{method:"POST",body:fd});const j=await r.json();if(j.success){msg.className="msg success";msg.textContent=`Imported ${j.rows_imported} services for ${fmtMonth(month)}`;setTimeout(()=>{closeUpload();load()},2000)}else{msg.className="msg error";msg.textContent="Error: "+(j.error||"Unknown")}}catch(e){msg.className="msg error";msg.textContent="Failed: "+e.message}}function renderContent(){const el=document.getElementById("main"),d=D[active];if(!d){el.innerHTML='<div class="empty"><h2>No data</h2><p>Upload a PDF or wait for sync.</p></div>';return}let mb="";if(d.prev_month){const p=d.mom_pct,cls=p>0?"badge-up":p<0?"badge-dn":"badge-flat",sym=p>0?"↑":p<0?"↓":"→",val=Math.abs(p)>999?">999%":Math.abs(p).toFixed(1)+"%";mb=`<span class="badge ${cls}">${val} ${sym}</span><span class="banner-lbl">vs ${fmtMonth(d.prev_month)}</span>`}const sb=d.has_pdf?'<span class="pdf-badge">✓ Exact GHL Data</span>':'<span class="est-badge">~ Estimated</span>';const ch=d.trend_months&&d.trend_months.length>1?`<div class="chart-wrap"><div class="chart-title">Spending Trend</div><div class="chart-area"><canvas id="trendChart"></canvas></div><div class="chart-legend" id="chartLegend"></div></div>`:"";const ca=d.cards.length?`<div class="grid">${d.cards.map(c=>{const bm=c.mom_pct,bc=bm>0?"badge-up":bm<0?"badge-dn":"badge-flat",bs=bm>0?"↑":bm<0?"↓":"→",bv=Math.abs(bm)>999?">999%":Math.abs(bm).toFixed(1)+"%";return`<div class="card"><div class="card-title">${c.label}</div><div class="card-row"><span class="card-amount" style="color:${c.color}">$${c.cost.toFixed(2)}</span><span class="card-badge ${bc}">${bv} ${bs}</span></div><div class="card-from">from <span>$${c.prev_cost.toFixed(2)}</span> last month</div><div class="bar-bg" style="margin-top:10px"><div class="bar-fill" style="width:${c.pct_of_total}%;background:${c.color}"></div></div><div class="card-footer"><span class="card-pct">${c.pct_of_total}% of total</span><span class="card-pct">${c.message_count.toLocaleString()} units</span></div></div>`}).join("")}</div>`:'<div class="empty"><h2>No data</h2></div>';el.innerHTML=`<div class="banner"><span class="total-amt">$${d.total.toFixed(2)}</span><span class="banner-lbl">total for ${fmtMonth(active)}</span>${mb}${sb}</div>${ch}${ca}`;if(d.trend_months&&d.trend_months.length>1){const top5=d.cards.slice(0,5),labels=d.trend_months.map(fmtMonth),datasets=top5.map(c=>({label:c.label,data:d.trend_months.map(tm=>(d.trend[c.service]||{})[tm]||0),borderColor:c.color,backgroundColor:c.color+"20",borderWidth:2,pointRadius:3,tension:0.35,fill:false}));if(chartInst)chartInst.destroy();chartInst=new Chart(document.getElementById("trendChart"),{type:"line",data:{labels,datasets},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{font:{size:11},color:"#9CA3AF"}},y:{grid:{color:"#F3F4F6"},ticks:{font:{size:11},color:"#9CA3AF",callback:v=>"$"+v.toFixed(2)}}}}});document.getElementById("chartLegend").innerHTML=top5.map(c=>`<div class="legend-item"><div class="legend-dot" style="background:${c.color}"></div>${c.label}</div>`).join("")}}async function load(){try{const r=await fetch("/api/data"),j=await r.json();document.getElementById("sync").textContent="Last synced: "+(j.last_sync||"pending");if(j.error){document.getElementById("main").innerHTML=`<div class="empty"><h2>${j.error}</h2></div>`;return}M=j.months||[];D=j.data||{};if(!M.length){document.getElementById("main").innerHTML='<div class="empty"><h2>No data yet</h2><p>Click Upload PDF to get started.</p></div>';return}if(!active||!M.includes(active))active=M[0];renderTabs();renderContent()}catch(e){document.getElementById("main").innerHTML=`<div class="empty"><h2>${e.message}</h2></div>`}}load();setInterval(load,15*60*1000);</script></body></html>"""
