"""
app.py - GHL Credit Usage Dashboard
Runs fetcher on schedule + serves dashboard web UI.
"""

import os
import json
import time
import sqlite3
import threading
import requests
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
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS usage_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
        service TEXT NOT NULL, message_count INTEGER DEFAULT 0,
        cost REAL DEFAULT 0.0, UNIQUE(date, service))""")
    c.execute("""CREATE TABLE IF NOT EXISTS usage_monthly (
        id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL,
        service TEXT NOT NULL, message_count INTEGER DEFAULT 0,
        cost REAL DEFAULT 0.0, UNIQUE(month, service))""")
    conn.commit()
    conn.close()
    print("DB initialized.")

init_db()

PRICING = {
    "whatsapp_marketing": 0.0769, "whatsapp_utility": 0.0119,
    "email": 0.000675, "email_notification": 0.000972,
    "email_verification": 0.0025, "conversation_voice_ai": 0.0023,
    "reviews_ai": 0.0100, "workflow_premium": 0.0100,
    "sms": 0.0079, "calls": 0.0,
}
MESSAGE_TYPE_MAP = {
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
}
SERVICE_LABELS = {
    "whatsapp_marketing": "WhatsApp Marketing Message",
    "whatsapp_utility": "WhatsApp Utility Message",
    "email": "Email", "email_notification": "Email Notification",
    "email_verification": "Email Verification",
    "conversation_voice_ai": "Conversation & Voice AI",
    "reviews_ai": "Reviews AI",
    "workflow_premium": "Workflow Premium Features",
    "sms": "SMS", "calls": "Calls", "other": "Other",
}
SERVICE_COLORS = {
    "whatsapp_marketing": "#4f46e5", "whatsapp_utility": "#7c3aed",
    "email": "#0891b2", "email_notification": "#0284c7",
    "email_verification": "#059669", "conversation_voice_ai": "#d97706",
    "reviews_ai": "#dc2626", "workflow_premium": "#7c3aed",
    "sms": "#16a34a", "calls": "#9333ea", "other": "#6b7280",
}

BASE_URL = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"
last_fetch_time = None

def ghl_get(session, path, params={}):
    url = f"{BASE_URL}{path}"
    for _ in range(3):
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(10)
                continue
            print(f"API {r.status_code}: {path}")
            return {}
        except Exception as e:
            print(f"Request error: {e}")
            time.sleep(2)
    return {}

def run_fetch():
    global last_fetch_time
    token = os.getenv("GHL_ACCESS_TOKEN")
    location_id = os.getenv("GHL_LOCATION_ID")
    if not token or not location_id:
        print("Missing credentials")
        return
    print(f"Fetching at {datetime.now(timezone.utc).strftime('%H:%M:%S')}...")
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Version": API_VERSION, "Accept": "application/json",
    })
    now = datetime.now(timezone.utc)
    start_ms = int((now - timedelta(days=90)).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    data = ghl_get(session, "/conversations/search", {
        "locationId": location_id, "limit": 100,
        "startAfterDate": start_ms, "endDate": end_ms,
    })
    convos = data.get("conversations", [])
    print(f"Found {len(convos)} conversations")
    daily = defaultdict(lambda: defaultdict(int))
    for convo in convos:
        cid = convo.get("id")
        if not cid:
            continue
        try:
            md = ghl_get(session, f"/conversations/{cid}/messages", {"limit": 100})
            msgs = md.get("messages", {})
            if isinstance(msgs, dict):
                msgs = msgs.get("messages", [])
            for msg in msgs:
                d = msg.get("dateAdded") or msg.get("createdAt", "")
                if isinstance(d, (int, float)):
                    dt = datetime.fromtimestamp(d/1000, tz=timezone.utc)
                else:
                    try:
                        dt = datetime.fromisoformat(str(d).replace("Z", "+00:00"))
                    except:
                        dt = now
                ds = dt.strftime("%Y-%m-%d")
                rt = msg.get("messageType") or msg.get("type", "")
                sk = MESSAGE_TYPE_MAP.get(rt.upper().strip() if rt else "", "other")
                dr = msg.get("direction", "").upper()
                if dr in ("OUTBOUND", "SENT", "") or not dr:
                    daily[ds][sk] += 1
            time.sleep(0.1)
        except Exception as e:
            print(f"Skip {cid}: {e}")
    conn = get_db()
    for ds, svcs in daily.items():
        for sk, cnt in svcs.items():
            rate = PRICING.get(sk, 0.0)
            conn.execute("""INSERT INTO usage_daily (date,service,message_count,cost)
                VALUES(?,?,?,?) ON CONFLICT(date,service) DO UPDATE SET
                message_count=excluded.message_count,cost=excluded.cost""",
                (ds, sk, cnt, cnt*rate))
    conn.execute("DELETE FROM usage_monthly")
    conn.execute("""INSERT INTO usage_monthly(month,service,message_count,cost)
        SELECT substr(date,1,7),service,SUM(message_count),SUM(cost)
        FROM usage_daily GROUP BY substr(date,1,7),service""")
    conn.commit()
    conn.close()
    last_fetch_time = datetime.now(timezone.utc)
    print("Fetch complete.")

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/api/data")
def api_data():
    try:
        conn = get_db()
        months = [r[0] for r in conn.execute(
            "SELECT DISTINCT month FROM usage_monthly ORDER BY month DESC").fetchall()]
        result = {}
        for i, month in enumerate(months):
            rows = conn.execute(
                "SELECT service,message_count,cost FROM usage_monthly WHERE month=? ORDER BY cost DESC",
                (month,)).fetchall()
            total = sum(r["cost"] for r in rows)
            prev = months[i+1] if i+1 < len(months) else None
            pt = 0
            if prev:
                pr = conn.execute("SELECT COALESCE(SUM(cost),0) t FROM usage_monthly WHERE month=?", (prev,)).fetchone()
                pt = pr["t"] if pr else 0
            mom = ((total-pt)/pt*100) if pt > 0 else (100 if total > 0 else 0)
            cards = []
            for r in rows:
                if r["cost"]==0 and r["message_count"]==0:
                    continue
                pct = (r["cost"]/total*100) if total > 0 else 0
                cards.append({
                    "service": r["service"],
                    "label": SERVICE_LABELS.get(r["service"], r["service"]),
                    "color": SERVICE_COLORS.get(r["service"], "#6b7280"),
                    "message_count": r["message_count"],
                    "cost": round(r["cost"], 4),
                    "pct_of_total": round(pct, 1),
                })
            result[month] = {"total": round(total,4), "prev_total": round(pt,4),
                "mom_pct": round(mom,1), "prev_month": prev, "cards": cards}
        conn.close()
        ls = last_fetch_time.strftime("%Y-%m-%d %H:%M UTC") if last_fetch_time else "Fetching..."
        return jsonify({"months": months, "data": result, "last_sync": ls})
    except Exception as e:
        print(f"API error: {e}")
        return jsonify({"error": str(e), "months": [], "data": {}}), 500

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Credit Usage — QD Academy</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f6f9;min-height:100vh}
    .header{background:#fff;border-bottom:1px solid #e5e7eb;padding:16px 24px;display:flex;align-items:center;justify-content:space-between}
    .header h1{font-size:18px;font-weight:600;color:#111827}
    .sub{font-size:13px;color:#6b7280;margin-top:2px}
    .sync{font-size:12px;color:#9ca3af}
    .tabs-wrap{background:#fff;border-bottom:1px solid #e5e7eb;padding:0 24px;overflow-x:auto}
    .tabs{display:flex;gap:4px;min-width:max-content}
    .tab{padding:14px 20px;font-size:14px;font-weight:500;color:#6b7280;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}
    .tab.active{color:#4f46e5;border-bottom-color:#4f46e5;font-weight:600}
    .content{padding:24px;max-width:960px;margin:0 auto}
    .banner{margin-bottom:24px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
    .amount{font-size:26px;font-weight:700;color:#111827}
    .lbl{font-size:15px;color:#6b7280}
    .badge{display:inline-flex;align-items:center;padding:4px 10px;border-radius:20px;font-size:13px;font-weight:600}
    .up{background:#dcfce7;color:#16a34a}.down{background:#fee2e2;color:#dc2626}.flat{background:#f3f4f6;color:#6b7280}
    .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
    .card{background:#fff;border-radius:12px;padding:20px;border:1px solid #e5e7eb}
    .card:hover{box-shadow:0 4px 12px rgba(0,0,0,.08)}
    .ct{font-size:13px;font-weight:600;color:#374151;margin-bottom:10px}
    .cr{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:10px}
    .cc{font-size:22px;font-weight:700}.cp{font-size:12px;color:#9ca3af}
    .bg{height:5px;background:#f3f4f6;border-radius:99px;margin-bottom:8px;overflow:hidden}
    .fill{height:100%;border-radius:99px}
    .pct{font-size:12px;color:#6b7280}.cnt{font-size:11px;color:#9ca3af;margin-top:4px}
    .empty{text-align:center;padding:60px;color:#9ca3af}
    .loading{text-align:center;padding:60px;color:#6b7280}
  </style>
</head>
<body>
<div class="header">
  <div><h1>Credit Usage Dashboard</h1><div class="sub">QD Academy — GoHighLevel</div></div>
  <div class="sync" id="sync">Loading...</div>
</div>
<div class="tabs-wrap"><div class="tabs" id="tabs"></div></div>
<div class="content"><div id="main" class="loading">Loading data...</div></div>
<script>
let D={},M=[],A=null;
const N=m=>{const[y,mo]=m.split('-');return['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+mo-1]+' '+y};
const tabs=()=>document.getElementById('tabs').innerHTML=M.map(m=>`<div class="tab ${m===A?'active':''}" onclick="sw('${m}')">${N(m)}</div>`).join('');
const sw=m=>{A=m;tabs();render()};
const render=()=>{
  const el=document.getElementById('main'),d=D[A];
  if(!d){el.innerHTML='<div class="empty"><h2>No data</h2></div>';return}
  let mom='';
  if(d.prev_month){const p=d.mom_pct,c=p>0?'up':p<0?'down':'flat',s=p>0?'↑':p<0?'↓':'→',a=Math.abs(p);
    mom=`<span class="badge ${c}">${a>100?'>100%':a.toFixed(1)+'%'} ${s}</span><span class="lbl">vs ${N(d.prev_month)}</span>`}
  const cards=d.cards.length?'<div class="grid">'+d.cards.map(c=>`
    <div class="card"><div class="ct">${c.label}</div>
    <div class="cr"><span class="cc" style="color:${c.color}">$${c.cost.toFixed(4)}</span><span class="cp">from $0</span></div>
    <div class="bg"><div class="fill" style="width:${c.pct_of_total}%;background:${c.color}"></div></div>
    <div class="pct">${c.pct_of_total}% of total</div>
    <div class="cnt">${c.message_count.toLocaleString()} transactions</div></div>`).join('')+'</div>':
    '<div class="empty"><h2>No usage this month</h2></div>';
  el.innerHTML=`<div class="banner"><span class="amount">$${d.total.toFixed(2)}</span><span class="lbl">total for ${N(A)}</span>${mom}</div>${cards}`;
};
async function load(){
  try{
    const r=await fetch('/api/data'),j=await r.json();
    if(j.error){document.getElementById('main').innerHTML=`<div class="empty"><h2>${j.error}</h2></div>`;return}
    M=j.months||[];D=j.data||{};
    document.getElementById('sync').textContent='Last synced: '+(j.last_sync||'pending');
    if(!M.length){document.getElementById('main').innerHTML='<div class="empty"><h2>Fetching from GHL...</h2><p>Check back in 2 minutes.</p></div>';return}
    if(!A||!M.includes(A))A=M[0];tabs();render();
  }catch(e){document.getElementById('main').innerHTML=`<div class="empty"><h2>${e.message}</h2></div>`}
}
load();setInterval(load,15*60*1000);
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)

def scheduler():
    time.sleep(5)
    while True:
        try:
            run_fetch()
        except Exception as e:
            print(f"Scheduler error: {e}")
        time.sleep(15*60)

threading.Thread(target=scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)