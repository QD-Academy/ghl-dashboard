import os, time, sqlite3, threading, requests, calendar
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
DB_PATH = "/tmp/ghl_dashboard.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS usage_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, service TEXT NOT NULL,
        message_count INTEGER DEFAULT 0, cost REAL DEFAULT 0.0,
        UNIQUE(date, service))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS usage_monthly (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month TEXT NOT NULL, service TEXT NOT NULL,
        message_count INTEGER DEFAULT 0, cost REAL DEFAULT 0.0,
        UNIQUE(month, service))""")
    conn.commit()
    conn.close()
    print("DB ready.", flush=True)

init_db()

PRICING = {
    "whatsapp_marketing": 0.0769, "whatsapp_utility": 0.0119,
    "email": 0.000675, "email_notification": 0.000960,
    "email_verification": 0.0025, "conversation_voice_ai": 0.0024,
    "reviews_ai": 0.0100, "workflow_external_ai": 0.0081,
    "workflow_premium": 0.0100, "sms": 0.0079, "calls": 0.0, "other": 0.0,
}
MSG_MAP = {
    "TYPE_WHATSAPP": "whatsapp_marketing",
    "TYPE_WHATSAPP_TEMPLATE": "whatsapp_marketing",
    "TYPE_WHATSAPP_MARKETING": "whatsapp_marketing",
    "TYPE_WHATSAPP_UTILITY": "whatsapp_utility",
    "TYPE_EMAIL": "email", "TYPE_EMAIL_VERIFICATION": "email_verification",
    "TYPE_EMAIL_NOTIFICATION": "email_notification", "TYPE_SMS": "sms",
    "TYPE_PHONE": "calls", "TYPE_CALL": "calls",
    "TYPE_CONVERSATION_AI": "conversation_voice_ai",
    "TYPE_VOICE_AI": "conversation_voice_ai",
    "TYPE_REVIEW_AI": "reviews_ai", "TYPE_WORKFLOW": "workflow_premium",
    "TYPE_EXTERNAL_AI": "workflow_external_ai",
}
LABELS = {
    "whatsapp_marketing": "WhatsApp Marketing Message",
    "whatsapp_utility": "WhatsApp Utility Message",
    "email": "Email", "email_notification": "Email Notification",
    "email_verification": "LC Email Verification",
    "conversation_voice_ai": "Conversation & Voice AI",
    "reviews_ai": "Reviews AI", "workflow_external_ai": "Workflow - External AI",
    "workflow_premium": "Workflow Premium Features",
    "sms": "SMS", "calls": "Calls", "other": "Other",
}
COLORS = {
    "whatsapp_marketing": "#25D366", "whatsapp_utility": "#128C7E",
    "email": "#3B82F6", "email_notification": "#60A5FA",
    "email_verification": "#10B981", "conversation_voice_ai": "#F59E0B",
    "reviews_ai": "#EF4444", "workflow_external_ai": "#8B5CF6",
    "workflow_premium": "#7C3AED", "sms": "#6366F1",
    "calls": "#EC4899", "other": "#9CA3AF",
}
BASE_URL = "https://services.leadconnectorhq.com"
API_VER  = "2021-07-28"
last_fetch = None
fetch_status = "idle"

def ghl_get(session, path, params={}):
    for attempt in range(3):
        try:
            r = session.get(f"{BASE_URL}{path}", params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 10)))
                continue
            return {}
        except Exception as e:
            time.sleep(2)
    return {}

def save_to_db(daily):
    conn = get_db()
    for date_str, services in daily.items():
        for service, count in services.items():
            cost = count * PRICING.get(service, 0)
            conn.execute("""INSERT INTO usage_daily(date,service,message_count,cost)
                VALUES(?,?,?,?) ON CONFLICT(date,service) DO UPDATE SET
                message_count=excluded.message_count, cost=excluded.cost""",
                (date_str, service, count, cost))
    conn.execute("DELETE FROM usage_monthly")
    conn.execute("""INSERT INTO usage_monthly(month,service,message_count,cost)
        SELECT substr(date,1,7),service,SUM(message_count),SUM(cost)
        FROM usage_daily GROUP BY substr(date,1,7),service""")
    conn.commit()
    conn.close()

def run_fetch():
    global last_fetch, fetch_status
    fetch_status = "running"
    token = os.getenv("GHL_ACCESS_TOKEN")
    loc   = os.getenv("GHL_LOCATION_ID")
    if not token or not loc:
        print("Missing credentials", flush=True)
        fetch_status = "error"
        return
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Version": API_VER, "Accept": "application/json"
    })
    now = datetime.now(timezone.utc)
    print(f"Fetch started: {now.strftime('%Y-%m-%d %H:%M UTC')}", flush=True)
    months = []
    for i in range(3):
        mo = now.month - i
        yr = now.year
        if mo <= 0:
            mo += 12
            yr -= 1
        if (yr, mo) not in months:
            months.append((yr, mo))
    all_daily = defaultdict(lambda: defaultdict(int))
    for yr, mo in months:
        days_in = calendar.monthrange(yr, mo)[1]
        s_ms = int(datetime(yr,mo,1,tzinfo=timezone.utc).timestamp()*1000)
        e_ms = int(datetime(yr,mo,days_in,23,59,59,tzinfo=timezone.utc).timestamp()*1000)
        print(f"  Fetching {yr}-{mo:02d}...", flush=True)
        offset = 0
        total  = 0
        while offset < 5000:
            data  = ghl_get(session, "/conversations/search", {
                "locationId": loc, "limit": 100,
                "startAfterDate": s_ms, "endDate": e_ms,
                "sortBy": "last_message_date", "sortOrder": "desc",
                "offset": offset,
            })
            convs = data.get("conversations", [])
            if not convs:
                break
            for cv in convs:
                lmd = cv.get("lastMessageDate") or cv.get("dateAdded","")
                try:
                    dt = datetime.fromtimestamp(lmd/1000,tz=timezone.utc) if isinstance(lmd,(int,float)) else datetime.fromisoformat(str(lmd).replace("Z","+00:00"))
                except:
                    dt = datetime(yr,mo,1,tzinfo=timezone.utc)
                svc = MSG_MAP.get((cv.get("lastMessageType","") or "").upper().strip(),"other")
                dr  = (cv.get("lastMessageDirection","") or "").upper()
                if dr in ("OUTBOUND","SENT",""):
                    all_daily[dt.strftime("%Y-%m-%d")][svc] += 1
            total  += len(convs)
            offset += 100
            time.sleep(0.05)
        print(f"  {yr}-{mo:02d}: {total} conversations", flush=True)
        save_to_db(all_daily)
    last_fetch   = datetime.now(timezone.utc)
    fetch_status = "done"
    print(f"Fetch complete at {last_fetch.strftime('%Y-%m-%d %H:%M UTC')}", flush=True)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "fetch_status": fetch_status})

@app.route("/api/data")
def api_data():
    try:
        conn   = get_db()
        months = [r[0] for r in conn.execute(
            "SELECT DISTINCT month FROM usage_monthly ORDER BY month DESC"
        ).fetchall()]
        result = {}
        for i, month in enumerate(months):
            rows  = conn.execute(
                "SELECT service,message_count,cost FROM usage_monthly WHERE month=? ORDER BY cost DESC",
                (month,)
            ).fetchall()
            total = sum(r["cost"] for r in rows)
            prev_month = months[i+1] if i+1 < len(months) else None
            prev_total = 0.0
            if prev_month:
                pr = conn.execute(
                    "SELECT COALESCE(SUM(cost),0) t FROM usage_monthly WHERE month=?",
                    (prev_month,)
                ).fetchone()
                prev_total = pr["t"] if pr else 0.0
            mom = ((total-prev_total)/prev_total*100) if prev_total>0 else (100 if total>0 else 0)
            prev_by_svc = {}
            if prev_month:
                for pr in conn.execute(
                    "SELECT service,cost FROM usage_monthly WHERE month=?",(prev_month,)
                ).fetchall():
                    prev_by_svc[pr["service"]] = pr["cost"]
            cards = []
            for r in rows:
                if r["cost"]<=0 and r["message_count"]<=0:
                    continue
                pc = prev_by_svc.get(r["service"],0)
                cm = ((r["cost"]-pc)/pc*100) if pc>0 else (100 if r["cost"]>0 else 0)
                cards.append({
                    "service": r["service"],
                    "label": LABELS.get(r["service"],r["service"]),
                    "color": COLORS.get(r["service"],"#6B7280"),
                    "message_count": r["message_count"],
                    "cost": round(r["cost"],4),
                    "prev_cost": round(pc,4),
                    "pct_of_total": round((r["cost"]/total*100) if total>0 else 0,1),
                    "mom_pct": round(cm,1),
                })
            trend_months = months[i:i+6][::-1]
            trend = {}
            for tm in trend_months:
                for tr in conn.execute(
                    "SELECT service,cost FROM usage_monthly WHERE month=?",(tm,)
                ).fetchall():
                    if tr["service"] not in trend:
                        trend[tr["service"]] = {}
                    trend[tr["service"]][tm] = round(tr["cost"],4)
            result[month] = {
                "total": round(total,4), "prev_total": round(prev_total,4),
                "mom_pct": round(mom,1), "prev_month": prev_month,
                "cards": cards, "trend": trend, "trend_months": trend_months,
            }
        conn.close()
        ls = last_fetch.strftime("%Y-%m-%d %H:%M UTC") if last_fetch else "Fetching..."
        return jsonify({"months": months, "data": result, "last_sync": ls, "fetch_status": fetch_status})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e), "months": [], "data": {}}), 500

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Credit Usage Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'DM Sans',system-ui,sans-serif;background:#F0F2F5;min-height:100vh;color:#111827}
.hdr{background:#fff;border-bottom:1px solid #E5E7EB;padding:16px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}
.hdr h1{font-size:17px;font-weight:700}.hdr-sub{font-size:12px;color:#6B7280;margin-top:2px}
.sync-badge{font-size:11px;color:#9CA3AF;background:#F9FAFB;border:1px solid #E5E7EB;padding:4px 10px;border-radius:20px}
.syncing{color:#F59E0B;border-color:#FDE68A;background:#FFFBEB}
.tabs-wrap{background:#fff;border-bottom:1px solid #E5E7EB;padding:0 28px;overflow-x:auto}
.tabs{display:flex;gap:2px;min-width:max-content}
.tab{padding:13px 18px;font-size:13px;font-weight:500;color:#6B7280;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;transition:color .15s,border-color .15s}
.tab:hover{color:#374151}.tab.active{color:#4F46E5;border-bottom-color:#4F46E5;font-weight:600}
.ct{padding:24px 28px;max-width:1100px;margin:0 auto}
.banner{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:24px}
.total-amt{font-size:30px;font-weight:700}.banner-lbl{font-size:14px;color:#6B7280}
.badge{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}
.badge-up{background:#DCFCE7;color:#16A34A}.badge-dn{background:#FEE2E2;color:#DC2626}.badge-flat{background:#F3F4F6;color:#6B7280}
.chart-wrap{background:#fff;border-radius:14px;padding:20px 24px;margin-bottom:24px;border:1px solid #E5E7EB}
.chart-title{font-size:13px;font-weight:600;color:#374151;margin-bottom:16px}
.chart-area{position:relative;height:160px}
.chart-legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}
.legend-item{display:flex;align-items:center;gap:6px;font-size:11px;color:#6B7280}
.legend-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:14px}
.card{background:#fff;border-radius:14px;padding:18px 20px;border:1px solid #E5E7EB;transition:box-shadow .2s,transform .2s}
.card:hover{box-shadow:0 6px 20px rgba(0,0,0,.08);transform:translateY(-2px)}
.card-title{font-size:12px;font-weight:600;color:#6B7280;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}
.card-row{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:10px}
.card-amount{font-size:22px;font-weight:700}
.card-from{font-size:11px;color:#9CA3AF}.card-from span{color:#6B7280}
.bar-bg{height:5px;background:#F3F4F6;border-radius:99px;overflow:hidden;margin-bottom:7px}
.bar-fill{height:100%;border-radius:99px;transition:width .6s ease}
.card-footer{display:flex;justify-content:space-between;align-items:center}
.card-pct{font-size:11px;color:#9CA3AF}
.card-badge{font-size:10px;font-weight:600;padding:2px 7px;border-radius:10px}
.empty{text-align:center;padding:60px 20px;color:#9CA3AF}
.empty h2{font-size:18px;font-weight:600;color:#6B7280;margin-bottom:8px}
.loading{text-align:center;padding:80px;color:#9CA3AF;font-size:14px}
.spinner{width:32px;height:32px;border:3px solid #E5E7EB;border-top-color:#4F46E5;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 16px}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="hdr">
  <div><h1>Credit Usage Dashboard</h1><div class="hdr-sub">GoHighLevel — Sub-Account Overview</div></div>
  <div class="sync-badge" id="sync">Loading...</div>
</div>
<div class="tabs-wrap"><div class="tabs" id="tabs"></div></div>
<div class="ct"><div id="main" class="loading"><div class="spinner"></div>Loading dashboard...</div></div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
let D={},M=[],active=null,chartInst=null;
const fmtMonth=m=>{const[y,mo]=m.split("-");return["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+mo-1]+" "+y};
function renderTabs(){document.getElementById("tabs").innerHTML=M.map(m=>`<div class="tab${m===active?" active":""}" onclick="switchTo('${m}')">${fmtMonth(m)}</div>`).join("")}
function switchTo(m){active=m;renderTabs();renderContent()}
function renderContent(){
  const el=document.getElementById("main"),d=D[active];
  if(!d){el.innerHTML='<div class="empty"><h2>No data</h2></div>';return}
  let mb="";
  if(d.prev_month){const p=d.mom_pct,cls=p>0?"badge-up":p<0?"badge-dn":"badge-flat",sym=p>0?"↑":p<0?"↓":"→",val=Math.abs(p)>999?">999%":Math.abs(p).toFixed(1)+"%";mb=`<span class="badge ${cls}">${val} ${sym}</span><span class="banner-lbl">vs ${fmtMonth(d.prev_month)}</span>`}
  const ch=d.trend_months&&d.trend_months.length>1?`<div class="chart-wrap"><div class="chart-title">Spending Trend</div><div class="chart-area"><canvas id="trendChart"></canvas></div><div class="chart-legend" id="chartLegend"></div></div>`:"";
  const ca=d.cards.length?`<div class="grid">${d.cards.map(c=>{const bm=c.mom_pct,bc=bm>0?"badge-up":bm<0?"badge-dn":"badge-flat",bs=bm>0?"↑":bm<0?"↓":"→",bv=Math.abs(bm)>999?">999%":Math.abs(bm).toFixed(1)+"%";return`<div class="card"><div class="card-title">${c.label}</div><div class="card-row"><span class="card-amount" style="color:${c.color}">$${c.cost.toFixed(4)}</span><span class="card-badge ${bc}">${bv} ${bs}</span></div><div class="card-from">from <span>$${c.prev_cost.toFixed(4)}</span> last month</div><div class="bar-bg" style="margin-top:10px"><div class="bar-fill" style="width:${c.pct_of_total}%;background:${c.color}"></div></div><div class="card-footer"><span class="card-pct">${c.pct_of_total}% of total</span><span class="card-pct">${c.message_count.toLocaleString()} transactions</span></div></div>`}).join("")}</div>`:'<div class="empty"><h2>No usage data</h2></div>';
  el.innerHTML=`<div class="banner"><span class="total-amt">$${d.total.toFixed(2)}</span><span class="banner-lbl">total for ${fmtMonth(active)}</span>${mb}</div>${ch}${ca}`;
  if(d.trend_months&&d.trend_months.length>1){
    const top5=d.cards.slice(0,5),labels=d.trend_months.map(fmtMonth),datasets=top5.map(c=>({label:c.label,data:d.trend_months.map(tm=>(d.trend[c.service]||{})[tm]||0),borderColor:c.color,backgroundColor:c.color+"20",borderWidth:2,pointRadius:3,tension:0.35,fill:false}));
    if(chartInst)chartInst.destroy();
    chartInst=new Chart(document.getElementById("trendChart"),{type:"line",data:{labels,datasets},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{font:{size:11},color:"#9CA3AF"}},y:{grid:{color:"#F3F4F6"},ticks:{font:{size:11},color:"#9CA3AF",callback:v=>"$"+v.toFixed(2)}}}}});
    document.getElementById("chartLegend").innerHTML=top5.map(c=>`<div class="legend-item"><div class="legend-dot" style="background:${c.color}"></div>${c.label}</div>`).join("");
  }
}
async function load(){
  try{
    const r=await fetch("/api/data"),j=await r.json();
    const se=document.getElementById("sync");
    if(j.fetch_status==="running"){se.className="sync-badge syncing";se.textContent="⟳ Fetching data..."}
    else{se.className="sync-badge";se.textContent="Last synced: "+(j.last_sync||"pending")}
    if(j.error){document.getElementById("main").innerHTML=`<div class="empty"><h2>${j.error}</h2></div>`;return}
    M=j.months||[];D=j.data||{};
    if(!M.length){document.getElementById("main").innerHTML='<div class="empty"><div class="spinner"></div><h2>Fetching from GoHighLevel...</h2><p>First sync takes a few minutes.</p></div>';setTimeout(load,30000);return}
    if(!active||!M.includes(active))active=M[0];
    renderTabs();renderContent();
  }catch(e){document.getElementById("main").innerHTML=`<div class="empty"><h2>${e.message}</h2></div>`}
}
load();setInterval(load,15*60*1000);
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(HTML)

def scheduler():
    time.sleep(5)
    while True:
        try:
            run_fetch()
        except Exception as e:
            print(f"Fetch error: {e}", flush=True)
        print("Next fetch in 15 minutes.", flush=True)
        time.sleep(900)

threading.Thread(target=scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
