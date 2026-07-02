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

def upsert(month, service, qty, cost, source="api"):
    conn = get_db()
    conn.execute("""INSERT INTO usage_monthly(month,service,message_count,cost,source)
        VALUES(?,?,?,?,?) ON CONFLICT(month,service) DO UPDATE SET
        message_count=excluded.message_count,cost=excluded.cost,source=excluded.source""",
        (month, service, qty, cost, source))
    conn.commit()
    conn.close()

DATA_BY_MONTH = {
    "2026-03": [
        ("whatsapp_marketing",116,16.4942),
        ("whatsapp_utility",212,4.6534),
        ("email_notification",63,0.1314),
        ("email",33,0.0688),
        ("email_verification",28,0.2100),
    ],
    "2026-04": [
        ("whatsapp_marketing",205,29.1492),
        ("whatsapp_utility",186,4.0827),
        ("email",9,0.0188),
        ("email_verification",12,0.0900),
    ],
    "2026-05": [
        ("whatsapp_marketing",45,6.3986),
        ("whatsapp_utility",65,1.4268),
        ("email_notification",1,0.0021),
    ],
    "2026-06": [
        ("whatsapp_marketing",239,33.9087),
        ("whatsapp_utility",373,8.3400),
        ("email_notification",184,0.3838),
        ("email_verification",12,0.0900),
        ("email",33,0.0688),
    ],
}

for month, data in DATA_BY_MONTH.items():
    conn = get_db()
    existing = conn.execute("SELECT COUNT(*) c FROM usage_monthly WHERE month=?",(month,)).fetchone()["c"]
    conn.close()
    if existing == 0:
        for svc,qty,cost in data:
            upsert(month,svc,qty,cost,"pdf")
        print(f"Auto-imported {month}",flush=True)

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
        LABELS = {
            "whatsapp_marketing":"WhatsApp Marketing Messages",
            "whatsapp_utility":"WhatsApp Utility Messages",
            "email":"Emails","email_notification":"Email Notifications",
            "email_verification":"LC Email Verification",
            "content_ai":"Content AI","conversation_voice_ai":"Conversation & Voice AI",
            "reviews_ai":"Reviews AI","workflow_premium":"Workflow - Premium Features",
            "sms":"SMS","calls":"Calls","other":"Other",
        }
        COLORS = {
            "whatsapp_marketing":"#25D366","whatsapp_utility":"#128C7E",
            "email":"#3B82F6","email_notification":"#60A5FA",
            "email_verification":"#10B981","content_ai":"#F97316",
            "conversation_voice_ai":"#F59E0B","reviews_ai":"#EF4444",
            "workflow_premium":"#7C3AED","sms":"#6366F1","calls":"#EC4899","other":"#9CA3AF",
        }
        for i,month in enumerate(months):
            rows = conn.execute("SELECT service,message_count,cost,source FROM usage_monthly WHERE month=? ORDER BY cost DESC",(month,)).fetchall()
            total = sum(r["cost"] for r in rows)
            prev = months[i+1] if i+1<len(months) else None
            pt = 0.0
            if prev:
                pr = conn.execute("SELECT COALESCE(SUM(cost),0) t FROM usage_monthly WHERE month=?",(prev,)).fetchone()
                pt = pr["t"] if pr else 0.0
            mom = ((total-pt)/pt*100) if pt>0 else (100 if total>0 else 0)
            cards = []
            for r in rows:
                if r["cost"]<=0 and r["message_count"]<=0: continue
                cards.append({
                    "service":r["service"],
                    "label":LABELS.get(r["service"],r["service"]),
                    "color":COLORS.get(r["service"],"#6B7280"),
                    "message_count":r["message_count"],
                    "cost":round(r["cost"],4),
                    "cost2":round(r["cost"],2),
                    "pct_of_total":round((r["cost"]/total*100) if total>0 else 0,1),
                    "source":r["source"]
                })
            result[month] = {
                "total":round(total,2),"prev_total":round(pt,2),
                "mom_pct":round(mom,1),"prev_month":prev,
                "cards":cards,"has_pdf":any(r["source"]=="pdf" for r in rows)
            }
        conn.close()
        return jsonify({"months":months,"data":result,"last_sync":"Estimated","fetch_status":"done"})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error":str(e),"months":[],"data":{}}),500

HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Aktivate's Credit Usage Dashboard</title><link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"><style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'DM Sans',sans-serif;background:#F0F2F5;color:#111827}.hdr{background:#fff;border-bottom:1px solid #E5E7EB;padding:16px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}.hdr h1{font-size:17px;font-weight:700}.hdr-sub{font-size:12px;color:#6B7280;margin-top:2px}.sync-badge{font-size:11px;color:#9CA3AF;background:#F9FAFB;border:1px solid #E5E7EB;padding:4px 10px;border-radius:20px}.tabs-wrap{background:#fff;border-bottom:1px solid #E5E7EB;padding:0 28px;overflow-x:auto}.tabs{display:flex;gap:2px;min-width:max-content}.tab{padding:13px 18px;font-size:13px;font-weight:500;color:#6B7280;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}.tab.active{color:#4F46E5;border-bottom-color:#4F46E5;font-weight:600}.ct{padding:24px 28px;max-width:960px;margin:0 auto}.disclaimer{background:#FFFBEB;border:1px solid #FDE68A;border-radius:10px;padding:10px 16px;margin-bottom:20px;font-size:12px;color:#92400E;display:flex;align-items:center;gap:8px}.total-card{background:#fff;border-radius:14px;padding:20px 24px;margin-bottom:20px;border:1px solid #E5E7EB;display:flex;align-items:center;justify-content:space-between;box-shadow:0 1px 3px rgba(0,0,0,.06)}.total-left{display:flex;align-items:center;gap:14px}.total-icon{width:40px;height:40px;background:#EEF2FF;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:18px}.total-name{font-size:15px;font-weight:700}.total-sub{font-size:12px;color:#6B7280;margin-top:2px}.total-amount{font-size:28px;font-weight:700;color:#4F46E5}.section{background:#fff;border-radius:12px;border:1px solid #E5E7EB;margin-bottom:8px;overflow:hidden}.section-hdr{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;cursor:pointer;user-select:none}.section-hdr:hover{background:#F9FAFB}.section-title{font-size:13px;font-weight:600;display:flex;align-items:center;gap:8px}.arrow{font-size:10px;color:#9CA3AF;transition:transform .2s}.arrow.open{transform:rotate(90deg)}.section-amt{font-size:13px;font-weight:700;color:#374151}.section-body{display:none;border-top:1px solid #F3F4F6}.section-body.open{display:block}.svc-row{display:flex;align-items:center;justify-content:space-between;padding:11px 18px 11px 36px;border-bottom:1px solid #F9FAFB}.svc-row:last-child{border-bottom:none}.svc-left{display:flex;align-items:center;gap:8px}.dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}.svc-label{font-size:12px;font-weight:500;color:#374151}.svc-qty{font-size:11px;color:#9CA3AF;margin-top:1px}.svc-amt{font-size:12px;font-weight:700;color:#374151}.zero{color:#D1D5DB}.empty{text-align:center;padding:60px;color:#9CA3AF}.spinner{width:32px;height:32px;border:3px solid #E5E7EB;border-top-color:#4F46E5;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 16px}@keyframes spin{to{transform:rotate(360deg)}}</style></head><body>
<div class="hdr"><div><h1>Aktivate's Credit Usage Dashboard</h1><div class="hdr-sub">Engages.ai Account Overview</div></div><div class="sync-badge" id="sync">Loading...</div></div>
<div class="tabs-wrap"><div class="tabs" id="tabs"></div></div>
<div class="ct"><div id="main" class="empty"><div class="spinner"></div>Loading...</div></div>
<script>
let D={},M=[],active=null;
const fmtMonth=m=>{const[y,mo]=m.split("-");return["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+mo-1]+" "+y};
const fmt2=v=>"$"+Number(v).toFixed(2);
const fmt4=v=>v===0?"$0.00":"$"+Number(v).toFixed(4);

function tog(id){
  const b=document.getElementById("b"+id);
  const a=document.getElementById("a"+id);
  if(b)b.classList.toggle("open");
  if(a)a.classList.toggle("open");
}

const SECTIONS=[
  {key:"whatsapp",label:"WhatsApp Usage",services:["whatsapp_marketing","whatsapp_utility"]},
  {key:"email",label:"Communication / Email",services:["email","email_notification","email_verification"]},
  {key:"ai",label:"AI Services",services:["content_ai","conversation_voice_ai","reviews_ai"]},
  {key:"workflow",label:"Workflow - Premium Features",services:["workflow_premium"]},
  {key:"sms",label:"SMS",services:["sms"]},
  {key:"calls",label:"Calls",services:["calls"]},
  {key:"other",label:"Other Charges",services:["other"]},
];

const COLORS={
  whatsapp_marketing:"#25D366",whatsapp_utility:"#128C7E",
  email:"#3B82F6",email_notification:"#60A5FA",email_verification:"#10B981",
  content_ai:"#F97316",conversation_voice_ai:"#F59E0B",reviews_ai:"#EF4444",
  workflow_premium:"#7C3AED",sms:"#6366F1",calls:"#EC4899",other:"#9CA3AF"
};

function renderTabs(){document.getElementById("tabs").innerHTML=M.map(m=>`<div class="tab${m===active?" active":""}" onclick="switchTo('${m}')">${fmtMonth(m)}</div>`).join("")}
function switchTo(m){active=m;renderTabs();renderContent()}

function renderContent(){
  const el=document.getElementById("main");
  if(!el)return;
  const d=D[active];
  if(!d||!d.cards){el.innerHTML='<div class="empty"><h2>No data</h2></div>';return;}

  const cardMap={};
  d.cards.forEach(c=>{cardMap[c.service]=c;});

  let html='<div class="disclaimer">⚠️ <span><b>Disclaimer:</b> This billing report reflects actual credit usage. Reports are updated on the <b>7th of each month</b> once charges are finalized.</span></div>';
  html+=`<div class="total-card"><div class="total-left"><div class="total-icon">💳</div><div><div class="total-name">Total Usage</div><div class="total-sub">For ${fmtMonth(active)}</div></div></div><div class="total-amount">${fmt2(d.total)}</div></div>`;

  let uid=0;
  SECTIONS.forEach(sec=>{
    const id="s"+(uid++);
    const svcs=sec.services.map(k=>cardMap[k]).filter(Boolean);
    const secTotal=svcs.reduce((s,c)=>s+c.cost,0);
    const secQty=svcs.reduce((s,c)=>s+c.message_count,0);
    const hasData=secTotal>0;
    html+=`<div class="section">`;
    html+=`<div class="section-hdr" onclick="tog('${id}')">`;
    html+=`<div class="section-title"><span class="arrow" id="a${id}">${svcs.length?"▶":""}</span>${sec.label}</div>`;
    html+=`<div style="display:flex;align-items:center;gap:16px"><span style="font-size:11px;color:#9CA3AF">${secQty} transactions</span><span class="section-amt${hasData?"":" zero"}">${fmt2(secTotal)}</span></div>`;
    html+=`</div>`;
    if(svcs.length){
      html+=`<div class="section-body" id="b${id}">`;
      svcs.forEach(c=>{
        html+=`<div class="svc-row"><div class="svc-left"><div class="dot" style="background:${COLORS[c.service]||'#9CA3AF'}"></div><div><div class="svc-label">${c.label}</div><div class="svc-qty">${c.message_count} transactions</div></div></div><span class="svc-amt${c.cost===0?" zero":""}">${fmt2(c.cost)}</span></div>`;
      });
      html+=`</div>`;
    }
    html+=`</div>`;
  });

  el.innerHTML=html;
}

async function load(){
  try{
    const r=await fetch("/api/data"),j=await r.json();
    document.getElementById("sync").textContent="Last synced: "+(j.last_sync||"pending");
    M=j.months||[];D=j.data||{};
    if(!M.length){document.getElementById("main").innerHTML='<div class="empty"><h2>No data yet</h2></div>';return;}
    if(!active||!M.includes(active))active=M[0];
    renderTabs();renderContent();
  }catch(e){document.getElementById("main").innerHTML=`<div class="empty"><h2>${e.message}</h2></div>`;}
}
load();setInterval(load,15*60*1000);
</script></body></html>"""

@app.route("/")
def index():
    return render_template_string(HTML)

def scheduler():
    time.sleep(5)
    while True:
        time.sleep(900)

threading.Thread(target=scheduler,daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port,debug=False)
