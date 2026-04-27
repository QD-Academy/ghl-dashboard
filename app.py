"""
app.py - GHL Credit Usage Dashboard (Enhanced UI + Full Pagination)
"""

import os, time, sqlite3, threading, requests
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
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
        service TEXT NOT NULL, message_count INTEGER DEFAULT 0,
        cost REAL DEFAULT 0.0, UNIQUE(date, service))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS usage_monthly (
        id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL,
        service TEXT NOT NULL, message_count INTEGER DEFAULT 0,
        cost REAL DEFAULT 0.0, UNIQUE(month, service))""")
    conn.commit()
    conn.close()
    print("DB ready.")

init_db()

PRICING = {
    "whatsapp_marketing": 0.0769, "whatsapp_utility": 0.0119,
    "email": 0.000675, "email_notification": 0.000960,
    "email_verification": 0.0025, "conversation_voice_ai": 0.0024,
    "reviews_ai": 0.0100, "workflow_premium": 0.0100,
    "workflow_external_ai": 0.0081, "sms": 0.0079, "calls": 0.0,
}
MSG_MAP = {
    "TYPE_WHATSAPP": "whatsapp_marketing",
    "TYPE_WHATSAPP_TEMPLATE": "whatsapp_marketing",
    "TYPE_WHATSAPP_MARKETING": "whatsapp_marketing",
    "TYPE_WHATSAPP_UTILITY": "whatsapp_utility",
    "TYPE_EMAIL": "email",
    "TYPE_EMAIL_VERIFICATION": "email_verification",
    "TYPE_EMAIL_NOTIFICATION": "email_notification",
    "TYPE_SMS": "sms", "TYPE_PHONE": "calls", "TYPE_CALL": "calls",
    "TYPE_CONVERSATION_AI": "conversation_voice_ai",
    "TYPE_VOICE_AI": "conversation_voice_ai",
    "TYPE_REVIEW_AI": "reviews_ai",
    "TYPE_WORKFLOW_PREMIUM": "workflow_premium",
}
LABELS = {
    "whatsapp_marketing": "WhatsApp Marketing",
    "whatsapp_utility": "WhatsApp Utility",
    "email": "Email", "email_notification": "Email Notification",
    "email_verification": "Email Verification",
    "conversation_voice_ai": "Conversation & Voice AI",
    "reviews_ai": "Reviews AI",
    "workflow_premium": "Workflow Premium Actions",
    "workflow_external_ai": "Workflow External AI",
    "sms": "SMS", "calls": "Calls", "other": "Other",
}
COLORS = {
    "whatsapp_marketing": "#4f46e5", "whatsapp_utility": "#7c3aed",
    "email": "#0284c7", "email_notification": "#0891b2",
    "email_verification": "#059669", "conversation_voice_ai": "#d97706",
    "reviews_ai": "#dc2626", "workflow_premium": "#9333ea",
    "workflow_external_ai": "#c026d3", "sms": "#16a34a",
    "calls": "#64748b", "other": "#6b7280",
}

BASE_URL = "https://services.leadconnectorhq.com"
API_VER = "2021-07-28"
last_fetch = None

def ghl_get(s, path, p={}):
    for _ in range(3):
        try:
            r = s.get(f"{BASE_URL}{path}", params=p, timeout=30)
            if r.status_code == 200: return r.json()
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 10))
                print(f"Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            print(f"API {r.status_code}: {path}")
            return {}
        except Exception as e:
            print(f"Request error: {e}")
            time.sleep(2)
    return {}

def get_all_conversations(s, loc, start_ms, end_ms):
    """Fetch ALL conversations with full pagination."""
    all_convos = []
    last_msg_id = None
    page = 1

    MAX_PAGES=30
    page_count=0
    while page <= 30:
        params = {
            "locationId": loc,
            "limit": 100,
            "startAfterDate": start_ms,
            "endDate": end_ms,
        }
        if last_msg_id:
            params["lastMessageId"] = last_msg_id

        data = ghl_get(s, "/conversations/search", params)
        convos = data.get("conversations", [])

        if not convos:
            break

        all_convos.extend(convos)
        print(f"  Page {page}: got {len(convos)} conversations (total: {len(all_convos)})")

        # If less than 100 returned, we've reached the end
        if len(convos) < 100:
            break

        # Use last conversation's id for next page
        last_msg_id = convos[-1].get("id")
        page += 1
        time.sleep(0.3)  # be gentle with rate limits

    return all_convos

def run_fetch():
    global last_fetch
    token = os.getenv("GHL_ACCESS_TOKEN")
    loc = os.getenv("GHL_LOCATION_ID")
    if not token or not loc:
        print("No credentials")
        return

    print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}] Starting fetch...")

    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Version": API_VER,
        "Accept": "application/json"
    })

    now = datetime.now(timezone.utc)
    # Fetch 365 days to ensure we capture current year
    start_ms = int((now - timedelta(days=90)).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    # Get ALL conversations with pagination
    convos = get_all_conversations(s, loc, start_ms, end_ms)
    print(f"Total conversations fetched: {len(convos)}")

    if not convos:
        print("No conversations found.")
        return

    # Count messages by type per day
    daily = defaultdict(lambda: defaultdict(int))

    for i, c in enumerate(convos):
        cid = c.get("id")
        if not cid:
            continue
        if (i + 1) % 20 == 0:
            print(f"  Processing conversation {i+1}/{len(convos)}...")
        try:
            md = ghl_get(s, f"/conversations/{cid}/messages", {"limit": 100})
            msgs = md.get("messages", {})
            if isinstance(msgs, dict):
                msgs = msgs.get("messages", [])
            for m in msgs:
                da = m.get("dateAdded") or m.get("createdAt", "")
                if isinstance(da, (int, float)):
                    dt = datetime.fromtimestamp(da/1000, tz=timezone.utc)
                else:
                    try:
                        dt = datetime.fromisoformat(str(da).replace("Z", "+00:00"))
                    except:
                        dt = now
                ds = dt.strftime("%Y-%m-%d")
                rt = m.get("messageType") or m.get("type", "")
                sk = MSG_MAP.get(rt.upper().strip() if rt else "", "other")
                dr = m.get("direction", "").upper()
                if dr in ("OUTBOUND", "SENT", "") or not dr:
                    daily[ds][sk] += 1
            time.sleep(0.1)
        except Exception as e:
            print(f"  Skip {cid}: {e}")
            continue

    # Save to database
    conn = get_db()
    saved = 0
    for ds, sv in daily.items():
        for sk, cnt in sv.items():
            conn.execute("""INSERT INTO usage_daily(date,service,message_count,cost)
                VALUES(?,?,?,?) ON CONFLICT(date,service) DO UPDATE SET
                message_count=excluded.message_count, cost=excluded.cost""",
                (ds, sk, cnt, cnt * PRICING.get(sk, 0)))
            saved += 1

    # Rebuild monthly totals
    conn.execute("DELETE FROM usage_monthly")
    conn.execute("""INSERT INTO usage_monthly(month,service,message_count,cost)
        SELECT substr(date,1,7), service, SUM(message_count), SUM(cost)
        FROM usage_daily GROUP BY substr(date,1,7), service""")
    conn.commit()
    conn.close()

    last_fetch = datetime.now(timezone.utc)
    print(f"Fetch complete. {saved} daily records saved.")

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/api/data")
def api_data():
    try:
        conn = get_db()
        months = [r[0] for r in conn.execute(
            "SELECT DISTINCT month FROM usage_monthly ORDER BY month DESC"
        ).fetchall()]

        result = {}
        for i, month in enumerate(months):
            rows = conn.execute(
                "SELECT service, message_count, cost FROM usage_monthly WHERE month=? ORDER BY cost DESC",
                (month,)
            ).fetchall()
            total = sum(r["cost"] for r in rows)
            prev = months[i+1] if i+1 < len(months) else None
            pt = 0
            prev_services = {}
            if prev:
                pr = conn.execute(
                    "SELECT COALESCE(SUM(cost),0) t FROM usage_monthly WHERE month=?",
                    (prev,)
                ).fetchone()
                pt = pr["t"] if pr else 0
                prev_rows = conn.execute(
                    "SELECT service, cost FROM usage_monthly WHERE month=?",
                    (prev,)
                ).fetchall()
                prev_services = {r["service"]: r["cost"] for r in prev_rows}

            mom = ((total - pt) / pt * 100) if pt > 0 else (100 if total > 0 else 0)

            cards = []
            for r in rows:
                if r["cost"] == 0 and r["message_count"] == 0:
                    continue
                pct = (r["cost"] / total * 100) if total > 0 else 0
                prev_cost = prev_services.get(r["service"], 0)
                card_mom = ((r["cost"] - prev_cost) / prev_cost * 100) if prev_cost > 0 else (100 if r["cost"] > 0 else 0)
                cards.append({
                    "service": r["service"],
                    "label": LABELS.get(r["service"], r["service"]),
                    "color": COLORS.get(r["service"], "#6b7280"),
                    "message_count": r["message_count"],
                    "cost": round(r["cost"], 4),
                    "prev_cost": round(prev_cost, 4),
                    "mom_pct": round(card_mom, 1),
                    "pct_of_total": round(pct, 1),
                })

            # Build trend data for chart (last 6 months)
            trend_months = months[i:i+6][::-1]
            services_with_data = [r["service"] for r in rows if r["cost"] > 0][:5]
            trend_series = []
            for svc in services_with_data:
                values = []
                for tm in trend_months:
                    tr = conn.execute(
                        "SELECT COALESCE(cost,0) c FROM usage_monthly WHERE month=? AND service=?",
                        (tm, svc)
                    ).fetchone()
                    values.append(round(tr["c"] if tr else 0, 4))
                trend_series.append({
                    "service": svc,
                    "label": LABELS.get(svc, svc),
                    "color": COLORS.get(svc, "#6b7280"),
                    "values": values
                })

            result[month] = {
                "total": round(total, 2),
                "prev_total": round(pt, 2),
                "mom_pct": round(mom, 1),
                "prev_month": prev,
                "cards": cards,
                "trend": {"months": trend_months, "series": trend_series},
            }

        conn.close()
        ls = last_fetch.strftime("%Y-%m-%d %H:%M UTC") if last_fetch else "Fetching..."
        return jsonify({"months": months, "data": result, "last_sync": ls})

    except Exception as e:
        print(f"API error: {e}")
        return jsonify({"error": str(e), "months": [], "data": {}}), 500

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Credit Usage — GHL Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f0f2f7;--surface:#fff;--border:#e5e7eb;
  --text:#111827;--muted:#6b7280;--subtle:#9ca3af;
  --primary:#4f46e5;--success:#16a34a;--danger:#dc2626;
  --radius:14px;--shadow:0 1px 3px rgba(0,0,0,.08),0 4px 16px rgba(0,0,0,.04);
}
body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.header{background:var(--surface);border-bottom:1px solid var(--border);padding:18px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}
.header-left h1{font-size:17px;font-weight:700;color:var(--text);letter-spacing:-.3px}
.header-left p{font-size:12px;color:var(--muted);margin-top:2px}
.sync-badge{font-size:11px;color:var(--subtle);background:#f9fafb;border:1px solid var(--border);padding:4px 10px;border-radius:20px;font-family:'DM Mono',monospace}
.tabs-bar{background:var(--surface);border-bottom:1px solid var(--border);padding:0 28px;overflow-x:auto;scrollbar-width:none}
.tabs-bar::-webkit-scrollbar{display:none}
.tabs{display:flex;gap:2px;min-width:max-content}
.tab{padding:14px 18px;font-size:13px;font-weight:500;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;transition:all .15s}
.tab:hover{color:var(--primary)}
.tab.active{color:var(--primary);border-bottom-color:var(--primary);font-weight:600}
.content{padding:24px 28px;max-width:1100px;margin:0 auto}
.summary{display:flex;align-items:center;gap:14px;margin-bottom:24px;flex-wrap:wrap}
.summary-total{font-size:28px;font-weight:700;color:var(--text);letter-spacing:-1px}
.summary-label{font-size:14px;color:var(--muted)}
.mom-chip{display:inline-flex;align-items:center;gap:4px;padding:5px 12px;border-radius:20px;font-size:12px;font-weight:600}
.chip-up{background:#dcfce7;color:#15803d}
.chip-down{background:#fee2e2;color:#b91c1c}
.chip-flat{background:#f3f4f6;color:var(--muted)}
.chart-card{background:var(--surface);border-radius:var(--radius);border:1px solid var(--border);padding:20px 24px;margin-bottom:24px;box-shadow:var(--shadow)}
.chart-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px}
.chart-title{font-size:13px;font-weight:600;color:var(--text)}
.chart-legend{display:flex;gap:14px;flex-wrap:wrap}
.legend-item{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--muted)}
.legend-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.chart-wrap{position:relative;height:160px}
svg.chart{width:100%;height:100%}
.cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.card{background:var(--surface);border-radius:var(--radius);border:1px solid var(--border);padding:18px 20px;box-shadow:var(--shadow);transition:transform .15s,box-shadow .15s}
.card:hover{transform:translateY(-2px);box-shadow:0 4px 20px rgba(0,0,0,.1)}
.card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.card-name{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
.card-body{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:10px}
.card-cost{font-size:22px;font-weight:700;letter-spacing:-.5px}
.card-prev{font-size:12px;color:var(--subtle)}
.bar-bg{height:4px;background:#f3f4f6;border-radius:99px;margin-bottom:10px;overflow:hidden}
.bar-fill{height:100%;border-radius:99px;transition:width .6s cubic-bezier(.4,0,.2,1)}
.card-footer{display:flex;justify-content:space-between;align-items:center}
.card-pct{font-size:12px;color:var(--muted)}
.card-txn{font-size:11px;color:var(--subtle);font-family:'DM Mono',monospace}
.empty{text-align:center;padding:80px 20px;color:var(--subtle)}
.empty h2{font-size:18px;font-weight:600;color:var(--muted);margin-bottom:8px}
.loading{text-align:center;padding:80px;color:var(--muted);font-size:14px}
.spinner{display:inline-block;width:24px;height:24px;border:2px solid var(--border);border-top-color:var(--primary);border-radius:50%;animation:spin .8s linear infinite;margin-bottom:12px}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="header">
  <div class="header-left">
    <h1>Credit Usage Dashboard</h1>
    <p>GoHighLevel — Sub-Account Overview</p>
  </div>
  <div class="sync-badge" id="sync">Syncing...</div>
</div>
<div class="tabs-bar"><div class="tabs" id="tabs"></div></div>
<div class="content"><div id="main" class="loading"><div class="spinner"></div><br>Loading data...</div></div>

<script>
let D={},M=[],A=null;
function fmtMonth(m){const[y,mo]=m.split('-');return['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+mo-1]+' '+y}
function fmtMom(pct){
  if(pct===null||pct===undefined) return '';
  const cls=pct>0?'chip-up':pct<0?'chip-down':'chip-flat';
  const sign=pct>0?'+':'';
  const arrow=pct>0?'↑':pct<0?'↓':'→';
  const abs=Math.abs(pct);
  const label=abs>100?(pct>0?'>+100%':'<-100%'):`${sign}${abs.toFixed(2)}%`;
  return `<span class="mom-chip ${cls}">${label} ${arrow}</span>`;
}
function renderTabs(){
  document.getElementById('tabs').innerHTML=M.map(m=>
    `<div class="tab${m===A?' active':''}" onclick="sw('${m}')">${fmtMonth(m)}</div>`
  ).join('');
}
function sw(m){A=m;renderTabs();render()}
function buildChart(trend){
  if(!trend||!trend.series||!trend.series.length) return '';
  const months=trend.months, series=trend.series;
  const W=600,H=150,P={t:10,r:10,b:30,l:45};
  const pw=W-P.l-P.r, ph=H-P.t-P.b;
  let maxV=0;
  series.forEach(s=>s.values.forEach(v=>{if(v>maxV)maxV=v}));
  if(maxV===0) return '';
  maxV=maxV*1.15;
  const xS=pw/(months.length-1||1);
  const yS=v=>ph-(v/maxV)*ph;
  let paths='',dots='';
  series.forEach(s=>{
    const pts=s.values.map((v,i)=>`${P.l+i*xS},${P.t+yS(v)}`);
    paths+=`<path d="M${pts.join('L')}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
    const last=s.values.length-1;
    dots+=`<circle cx="${P.l+last*xS}" cy="${P.t+yS(s.values[last])}" r="4" fill="${s.color}" stroke="#fff" stroke-width="2"/>`;
  });
  let xlabels='';
  months.forEach((m,i)=>{
    xlabels+=`<text x="${P.l+i*xS}" y="${H-6}" text-anchor="middle" font-size="10" fill="#9ca3af">${fmtMonth(m).split(' ')[0]}</text>`;
  });
  let ylines='';
  for(let i=0;i<=3;i++){
    const y=P.t+(ph/3)*i, val=maxV*(1-i/3);
    ylines+=`<line x1="${P.l}" y1="${y}" x2="${P.l+pw}" y2="${y}" stroke="#f3f4f6" stroke-width="1"/>`;
    ylines+=`<text x="${P.l-4}" y="${y+4}" text-anchor="end" font-size="9" fill="#9ca3af">$${val.toFixed(val<1?2:0)}</text>`;
  }
  const legend=series.map(s=>
    `<div class="legend-item"><div class="legend-dot" style="background:${s.color}"></div>${s.label}</div>`
  ).join('');
  return `<div class="chart-card">
    <div class="chart-header">
      <span class="chart-title">Spending Trend</span>
      <div class="chart-legend">${legend}</div>
    </div>
    <div class="chart-wrap">
      <svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        ${ylines}${paths}${dots}${xlabels}
      </svg>
    </div>
  </div>`;
}
function render(){
  const main=document.getElementById('main'), d=D[A];
  if(!d){main.innerHTML='<div class="empty"><h2>No data for this month</h2></div>';return}
  const momChip=d.prev_month?fmtMom(d.mom_pct):'';
  const prevLabel=d.prev_month?`<span class="summary-label">vs ${fmtMonth(d.prev_month)}</span>`:'';
  const chart=buildChart(d.trend);
  let cards='';
  if(!d.cards.length){
    cards='<div class="empty"><h2>No usage recorded this month</h2></div>';
  } else {
    cards='<div class="cards-grid">'+d.cards.map(c=>`
      <div class="card">
        <div class="card-header">
          <span class="card-name">${c.label}</span>
          ${fmtMom(c.mom_pct)}
        </div>
        <div class="card-body">
          <span class="card-cost" style="color:${c.color}">$${c.cost.toFixed(2)}</span>
          <span class="card-prev">${c.prev_cost>0?'from $'+c.prev_cost.toFixed(2):'from $0'}</span>
        </div>
        <div class="bar-bg"><div class="bar-fill" style="width:${c.pct_of_total}%;background:${c.color}"></div></div>
        <div class="card-footer">
          <span class="card-pct">${c.pct_of_total}% of total</span>
          <span class="card-txn">${c.message_count.toLocaleString()} msgs</span>
        </div>
      </div>`).join('')+'</div>';
  }
  main.innerHTML=`
    <div class="summary">
      <span class="summary-total">$${d.total.toFixed(2)}</span>
      <span class="summary-label">total for ${fmtMonth(A)}</span>
      ${momChip}${prevLabel}
    </div>
    ${chart}${cards}`;
}
async function load(){
  try{
    const r=await fetch('/api/data'), j=await r.json();
    if(j.error){document.getElementById('main').innerHTML=`<div class="empty"><h2>${j.error}</h2></div>`;return}
    M=j.months||[];D=j.data||{};
    document.getElementById('sync').textContent='Last synced: '+(j.last_sync||'pending');
    if(!M.length){document.getElementById('main').innerHTML='<div class="empty"><h2>Fetching from GHL...</h2><p>Check back in 2 minutes.</p></div>';return}
    if(!A||!M.includes(A))A=M[0];
    renderTabs();render();
  }catch(e){
    document.getElementById('main').innerHTML=`<div class="empty"><h2>Failed: ${e.message}</h2></div>`;
  }
}
load();setInterval(load,15*60*1000);
</script>
</body>
</html>"""

@app.route("/")
def index(): return render_template_string(DASHBOARD_HTML)

def scheduler():
    time.sleep(5)
    while True:
        try: run_fetch()
        except Exception as e: print(f"Scheduler error: {e}")
        time.sleep(15*60)

threading.Thread(target=scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
