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

DATA_BY_MONTH = {
    "2026-03": [
        ("whatsapp_marketing",116,16.4942),
        ("whatsapp_utility",212,4.6534),
    ],
    "2026-04": [
        ("whatsapp_marketing",205,29.1492),
        ("whatsapp_utility",186,4.0827),
    ],
    "2026-05": [
        ("whatsapp_marketing",45,6.3986),
        ("whatsapp_utility",65,1.4268),
    ],
}

@app.route("/api/import-hardcoded")
def import_hardcoded():
    month = request.args.get("month","2026-03")
    DATA = DATA_BY_MONTH.get(month, DATA_BY_MONTH["2026-03"])
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


HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Aktivate's Credit Usage Dashboard</title><link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"><style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'DM Sans',system-ui,sans-serif;background:#F0F2F5;min-height:100vh;color:#111827}.hdr{background:#fff;border-bottom:1px solid #E5E7EB;padding:16px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}.hdr h1{font-size:17px;font-weight:700}.hdr-sub{font-size:12px;color:#6B7280;margin-top:2px}.hdr-right{display:flex;align-items:center;gap:10px}.sync-badge{font-size:11px;color:#9CA3AF;background:#F9FAFB;border:1px solid #E5E7EB;padding:4px 10px;border-radius:20px}.tabs-wrap{background:#fff;border-bottom:1px solid #E5E7EB;padding:0 28px;overflow-x:auto}.tabs{display:flex;gap:2px;min-width:max-content}.tab{padding:13px 18px;font-size:13px;font-weight:500;color:#6B7280;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}.tab.active{color:#4F46E5;border-bottom-color:#4F46E5;font-weight:600}.ct{padding:24px 28px;max-width:1100px;margin:0 auto}.disclaimer{background:#FFFBEB;border:1px solid #FDE68A;border-radius:10px;padding:10px 16px;margin-bottom:20px;font-size:12px;color:#92400E;display:flex;align-items:center;gap:8px}.plan-card{background:#fff;border-radius:14px;padding:20px 24px;margin-bottom:14px;border:1px solid #E5E7EB;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}.plan-left{display:flex;align-items:center;gap:14px}.plan-icon{width:40px;height:40px;background:#EEF2FF;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:18px}.plan-name{font-size:15px;font-weight:700;color:#111827}.plan-sub{font-size:12px;color:#6B7280;margin-top:2px}.plan-price{font-size:28px;font-weight:700;color:#4F46E5}.plan-price-lbl{font-size:12px;color:#9CA3AF;font-weight:400}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;margin-top:20px}.card{background:#fff;border-radius:14px;padding:18px 20px;border:1px solid #E5E7EB;transition:box-shadow .2s,transform .2s}.card:hover{box-shadow:0 6px 20px rgba(0,0,0,.08);transform:translateY(-2px)}.card-title{font-size:11px;font-weight:600;color:#6B7280;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}.card-row{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:10px}.card-amount{font-size:22px;font-weight:700}.bar-bg{height:5px;background:#F3F4F6;border-radius:99px;overflow:hidden;margin-bottom:7px}.bar-fill{height:100%;border-radius:99px}.card-footer{display:flex;justify-content:space-between}.card-pct{font-size:11px;color:#9CA3AF}.empty{text-align:center;padding:60px 20px;color:#9CA3AF}.empty h2{font-size:18px;font-weight:600;color:#6B7280;margin-bottom:8px}.loading{text-align:center;padding:80px;color:#9CA3AF;font-size:14px}.spinner{width:32px;height:32px;border:3px solid #E5E7EB;border-top-color:#4F46E5;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 16px}@keyframes spin{to{transform:rotate(360deg)}}</style></head><body><div class="hdr"><div><h1>Aktivate's Credit Usage Dashboard</h1><div class="hdr-sub">Engages.ai Account Overview</div></div><div class="hdr-right"><div class="sync-badge" id="sync">Loading...</div></div></div><div class="tabs-wrap"><div class="tabs" id="tabs"></div></div><div class="ct"><div id="main" class="loading"><div class="spinner"></div>Loading...</div></div><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script><script>let D={},M=[],active=null;const SGD=1.28;const fmtMonth=m=>{const[y,mo]=m.split("-");return["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+mo-1]+" "+y};function renderTabs(){document.getElementById("tabs").innerHTML=M.map(m=>`<div class="tab${m===active?" active":""}" onclick="switchTo('${m}')">${fmtMonth(m)}</div>`).join("")}function switchTo(m){active=m;renderTabs();renderContent()}function renderContent(){const el=document.getElementById("main");if(!el){return;}const d=D[active];if(!d){el.innerHTML='<div class="empty"><h2>No data</h2><p>Upload a PDF or wait for sync.</p></div>';return;}const ca=d.cards&&d.cards.length?'<div class="grid">'+d.cards.map(function(card){return'<div class="card"><div class="card-title">'+card.label+'</div><div class="card-row"><span class="card-amount" style="color:'+card.color+'">S$'+(card.cost*SGD).toFixed(2)+'</span></div><div class="bar-bg"><div class="bar-fill" style="width:'+card.pct_of_total+'%;background:'+card.color+'"></div></div><div class="card-footer"><span class="card-pct">'+card.pct_of_total+'% of total</span><span class="card-pct">'+card.message_count.toLocaleString()+' units</span></div></div>';}).join('')+'</div>':'<div class="empty"><h2>No data</h2></div>';el.innerHTML='<div class="disclaimer">⚠️ <span><b>Disclaimer:</b> This billing report reflects your actual credit usage for the selected month. Reports are updated on the <b>7th of each month</b> once all charges have been finalized. Data shown prior to the 7th may be incomplete or subject to change.</span></div><div class="plan-card"><div class="plan-left"><div class="plan-icon">📋</div><div><div class="plan-name">Professional Plan</div><div class="plan-sub">Engages.ai Subscription</div></div></div><div><span class="plan-price">S$504<span class="plan-price-lbl">/month</span></span></div></div><div class="plan-card"><div class="plan-left"><div class="plan-icon">💳</div><div><div class="plan-name">WhatsApp Total Usage</div><div class="plan-sub">Total for '+fmtMonth(active)+'</div></div></div><div><span class="plan-price">S$'+(d.total*SGD).toFixed(2)+'<span class="plan-price-lbl">/month</span></span></div></div>'+ca;}async function load(){try{const r=await fetch("/api/data"),j=await r.json();if(document.getElementById("sync")){document.getElementById("sync").textContent="Last synced: "+(j.last_sync||"pending");}if(j.error){document.getElementById("main").innerHTML='<div class="empty"><h2>'+j.error+'</h2></div>';return;}M=j.months||[];D=j.data||{};if(!M.length){document.getElementById("main").innerHTML='<div class="empty"><h2>No data yet</h2><p>Upload a PDF or wait for sync.</p></div>';return;}if(!active||!M.includes(active))active=M[0];renderTabs();renderContent();}catch(e){document.getElementById("main").innerHTML='<div class="empty"><h2>'+e.message+'</h2></div>';}}load();setInterval(load,15*60*1000);</script></body></html>"""

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
        if has_pdf > 0 or f"{yr}-{mo:02d}" in DATA_BY_MONTH:
            print(f"  {yr}-{mo:02d}: skip (hardcoded)", flush=True); continue
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
    # Auto-import hardcoded data on startup
    with app.app_context():
        for month, data in DATA_BY_MONTH.items():
            conn = get_db()
            existing = conn.execute("SELECT COUNT(*) c FROM usage_monthly WHERE month=? AND source='pdf'", (month,)).fetchone()["c"]
            conn.close()
            if existing == 0:
                for svc, qty, cost in data:
                    upsert(month, svc, qty, cost, "pdf")
                print(f"Auto-imported {month}", flush=True)
            else:
                print(f"Already has data for {month}, skipping", flush=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)


