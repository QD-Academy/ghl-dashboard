import os, time, sqlite3, threading
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
        ("whatsapp_marketing",213,30.3861),
        ("whatsapp_utility",414,9.1642),
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
        for i,month in enumerate(months):
            rows = conn.execute("SELECT service,message_count,cost,source FROM usage_monthly WHERE month=? ORDER BY cost DESC",(month,)).fetchall()
            total = sum(r["cost"] for r in rows)
            prev = months[i+1] if i+1<len(months) else None
            pt = 0.0
            if prev:
                pr = conn.execute("SELECT COALESCE(SUM(cost),0) t FROM usage_monthly WHERE month=?",(prev,)).fetchone()
                pt = pr["t"] if pr else 0.0
            cards = []
            for r in rows:
                cards.append({
                    "service":r["service"],
                    "message_count":r["message_count"],
                    "cost":round(r["cost"],4),
                    "source":r["source"]
                })
            result[month] = {
                "total":round(total,2),
                "cards":cards,
            }
        conn.close()
        return jsonify({"months":months,"data":result,"last_sync":"Estimated"})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error":str(e),"months":[],"data":{}}),500

HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Aktivate's Credit Usage Dashboard</title><link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"><style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'DM Sans',sans-serif;background:#F0F2F5;color:#111827}.hdr{background:#fff;border-bottom:1px solid #E5E7EB;padding:16px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}.hdr h1{font-size:17px;font-weight:700}.hdr-sub{font-size:12px;color:#6B7280;margin-top:2px}.sync-badge{font-size:11px;color:#9CA3AF;background:#F9FAFB;border:1px solid #E5E7EB;padding:4px 10px;border-radius:20px}.tabs-wrap{background:#fff;border-bottom:1px solid #E5E7EB;padding:0 28px;overflow-x:auto}.tabs{display:flex;gap:2px;min-width:max-content}.tab{padding:13px 18px;font-size:13px;font-weight:500;color:#6B7280;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}.tab.active{color:#4F46E5;border-bottom-color:#4F46E5;font-weight:600}.ct{padding:24px 28px;max-width:960px;margin:0 auto}.disclaimer{background:#FFFBEB;border:1px solid #FDE68A;border-radius:10px;padding:10px 16px;margin-bottom:20px;font-size:12px;color:#92400E;display:flex;align-items:center;gap:8px}.total-card{background:#fff;border-radius:14px;padding:20px 24px;margin-bottom:20px;border:1px solid #E5E7EB;display:flex;align-items:center;justify-content:space-between;box-shadow:0 1px 3px rgba(0,0,0,.06)}.total-icon{width:40px;height:40px;background:#EEF2FF;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:18px}.total-left{display:flex;align-items:center;gap:14px}.total-name{font-size:15px;font-weight:700}.total-sub{font-size:12px;color:#6B7280;margin-top:2px}.total-amount{font-size:28px;font-weight:700;color:#4F46E5}.tree{display:flex;flex-direction:column;gap:6px}.node{background:#fff;border-radius:12px;border:1px solid #E5E7EB;overflow:hidden}.node-hdr{display:flex;align-items:center;justify-content:space-between;padding:13px 18px;cursor:pointer;user-select:none}.node-hdr:hover{background:#F9FAFB}.node-left{display:flex;align-items:center;gap:8px}.arrow{font-size:10px;color:#9CA3AF;transition:transform .2s;width:14px}.arrow.open{transform:rotate(90deg)}.node-title{font-size:13px;font-weight:600;color:#111827}.node-right{display:flex;align-items:center;gap:20px}.node-qty{font-size:11px;color:#9CA3AF;min-width:100px;text-align:right}.node-amt{font-size:13px;font-weight:700;color:#374151;min-width:80px;text-align:right}.zero{color:#D1D5DB}.node-body{display:none;border-top:1px solid #F3F4F6;background:#FAFAFA}.node-body.open{display:block}.l2{border-bottom:1px solid #F3F4F6}.l2:last-child{border-bottom:none}.l2-hdr{display:flex;align-items:center;justify-content:space-between;padding:10px 18px 10px 36px;cursor:pointer}.l2-hdr:hover{background:#F3F4F6}.l2-title{font-size:12px;font-weight:600;color:#374151;display:flex;align-items:center;gap:6px}.l2-body{display:none;background:#fff}.l2-body.open{display:block}.l3{border-bottom:1px solid #F9FAFB}.l3:last-child{border-bottom:none}.l3-hdr{display:flex;align-items:center;justify-content:space-between;padding:9px 18px 9px 54px;cursor:pointer}.l3-hdr:hover{background:#F9FAFB}.l3-title{font-size:12px;font-weight:500;color:#374151;display:flex;align-items:center;gap:6px}.l3-body{display:none}.l3-body.open{display:block}.leaf{display:flex;align-items:center;justify-content:space-between;padding:8px 18px 8px 72px;border-bottom:1px solid #F9FAFB;background:#fff}.leaf:last-child{border-bottom:none}.leaf-name{font-size:11px;color:#6B7280}.leaf-right{display:flex;gap:16px;align-items:center}.leaf-qty{font-size:11px;color:#9CA3AF}.leaf-amt{font-size:11px;font-weight:600;color:#374151}.dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;background:#D1D5DB}.empty{text-align:center;padding:60px;color:#9CA3AF}.spinner{width:32px;height:32px;border:3px solid #E5E7EB;border-top-color:#4F46E5;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 16px}@keyframes spin{to{transform:rotate(360deg)}}</style></head><body>
<div class="hdr"><div><h1>Aktivate's Credit Usage Dashboard</h1><div class="hdr-sub">Engages.ai Account Overview</div></div><div class="sync-badge" id="sync">Loading...</div></div>
<div class="tabs-wrap"><div class="tabs" id="tabs"></div></div>
<div class="ct"><div id="main" class="empty"><div class="spinner"></div>Loading...</div></div>
<script>
let D={},M=[],active=null;
const fmtMonth=m=>{const[y,mo]=m.split("-");return["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+mo-1]+" "+y};
const fmt2=v=>"$"+Number(v).toFixed(2);
let uid=0;
function tog(id){const b=document.getElementById("b"+id);const a=document.getElementById("a"+id);if(b)b.classList.toggle("open");if(a)a.classList.toggle("open");}

const COLORS={whatsapp_marketing:"#25D366",whatsapp_utility:"#128C7E",email:"#3B82F6",email_notification:"#60A5FA",email_verification:"#10B981",content_ai:"#F97316",conversation_voice_ai:"#F59E0B",reviews_ai:"#EF4444",workflow_premium:"#7C3AED",sms:"#6366F1",calls:"#EC4899",other:"#9CA3AF"};

function getVal(cm,key){const c=cm[key];return c?{qty:c.message_count,amt:c.cost}:{qty:0,amt:0};}
function fmtAmt(v){return`<span class="node-amt${v===0?" zero":""}">${fmt2(v)}</span>`;}
function fmtAmt2(v){return`<span class="leaf-amt${v===0?" zero":""}">${fmt2(v)}</span>`;}

function leafRow(name,qty,amt){
  return`<div class="leaf"><span class="leaf-name">${name}</span><div class="leaf-right"><span class="leaf-qty">${qty} transactions</span>${fmtAmt2(amt)}</div></div>`;
}

function l3Node(title,children_html,qty,amt){
  const id="n"+(uid++);
  return`<div class="l3"><div class="l3-hdr" onclick="tog('${id}')"><div class="l3-title"><span class="arrow" id="a${id}">▶</span>${title}</div><div class="node-right"><span class="node-qty">${qty} transactions</span>${fmtAmt(amt)}</div></div><div class="l3-body" id="b${id}">${children_html}</div></div>`;
}

function l2Node(title,children_html,qty,amt){
  const id="n"+(uid++);
  return`<div class="l2"><div class="l2-hdr" onclick="tog('${id}')"><div class="l2-title"><span class="arrow" id="a${id}">▶</span>${title}</div><div class="node-right"><span class="node-qty">${qty} transactions</span>${fmtAmt(amt)}</div></div><div class="l2-body" id="b${id}">${children_html}</div></div>`;
}

function topNode(title,children_html,qty,amt){
  const id="n"+(uid++);
  return`<div class="node"><div class="node-hdr" onclick="tog('${id}')"><div class="node-left"><span class="arrow" id="a${id}">▶</span><span class="node-title">${title}</span></div><div class="node-right"><span class="node-qty">${qty} transactions</span>${fmtAmt(amt)}</div></div><div class="node-body" id="b${id}">${children_html}</div></div>`;
}

function buildTree(cm){
  let html='';

  // AI
  const aiItems=["Ask AI","Auto-Complete Address","Content AI","Conversation AI","Conversation and Voice AI","Funnel AI","Reviews AI","Voice AI","Workflow - External AI Models","Workflow AI Assistant"];
  let aiHtml=aiItems.map(n=>leafRow(n,0,0)).join('');
  html+=topNode("AI",aiHtml,0,0);

  // Apps
  html+=`<div class="node"><div class="node-hdr"><div class="node-left"><span style="width:14px"></span><span class="node-title">Apps</span></div><div class="node-right"><span class="node-qty">0 transactions</span>${fmtAmt(0)}</div></div></div>`;

  // Communication
  const en=getVal(cm,"email_notification");
  const em=getVal(cm,"email");
  const ev=getVal(cm,"email_verification");
  const emailTotal=en.amt+em.amt+ev.amt;
  const emailQty=en.qty+em.qty+ev.qty;
  const emailHtml=leafRow("Email Notifications",en.qty,en.amt)+leafRow("Email SMTP",0,0)+leafRow("Emails",em.qty,em.amt)+leafRow("LC Email Verification",ev.qty,ev.amt);
  const emailNode=l2Node("Email",emailHtml,emailQty,emailTotal);

  const compRegHtml=leafRow("A2P Fast track",0,0)+leafRow("A2P Registration",0,0);
  const msgHtml=["Group Messaging Users","Inbound Group SMS","Inbound MMS","Inbound RCS Basic","Inbound RCS Single","Inbound SMS","MMS Carrier Fees","Other SMS Charges","Outbound Group SMS","Outbound MMS","Outbound RCS Basic","Outbound RCS Single","Outbound SMS","RCS Activation Fee","RCS Carrier Fee","SMS Carrier Fees","SMS Notifications","SMS OTP Verification","National"].map(n=>leafRow(n,0,0)).join('');
  const numIntHtml=leafRow("Caller Name Lookup",0,0)+leafRow("Number Validation",0,0)+leafRow("Verified Caller ID",0,0);
  const phoneNumHtml=leafRow("Local",0,0)+leafRow("Mobile",0,0)+leafRow("Toll Free",0,0);
  const voiceHtml=["Answering Machine Detection (AMD)","Call Recording","Call Recording Storage","Conference Calls","IVR Call","Voice Minutes - Inbound Calls","Voice Minutes - Outbound Calls","Voicemail Drop","Workflow Call"].map(n=>leafRow(n,0,0)).join('');
  const voiceIntHtml=leafRow("Amazon Polly - Text to Speech",0,0)+leafRow("Incoming Call Spam Intelligence",0,0)+leafRow("Transcription",0,0);
  const phoneHtml=l3Node("Compliance & Registration",compRegHtml,0,0)+l3Node("Messaging",msgHtml,0,0)+l3Node("Number Intelligence",numIntHtml,0,0)+l3Node("Phone Numbers",phoneNumHtml,0,0)+l3Node("Voice",voiceHtml,0,0)+l3Node("Voice Intelligence",voiceIntHtml,0,0);
  const phoneNode=l2Node("Phone System",phoneHtml,0,0);
  const commTotal=emailTotal;
  const commQty=emailQty;
  html+=topNode("Communication",emailNode+phoneNode,commQty,commTotal);

  // Domain Purchase
  const domHtml=leafRow("Domain Redemption",0,0);
  html+=topNode("Domain Purchase",domHtml,0,0);

  // Tax
  const taxHtml=leafRow("Wallet Sales Tax",0,0);
  html+=topNode("Tax",taxHtml,0,0);

  // Wallet Subscriptions
  const walletItems=["[DEV] PayBridge Connect","GOFINFI-Soft Pull API","Accept online payments easily via PayTabs.","Ad Publishing Subscription","Ad Publishing Subscription","Ad Publishing Subscription","AI Employee Subscription","AI Route Planner","B365 Field Service","blueMSG - Unlimited iMessage with BlueBubbles","Brandblast Content Engine","Calendar Add Buttons","Chatgpt (Workflows 50+)","Clara - Your Service Business AI Assistant","Claude AI (50+ Actions)","Client Portal Subscription","Contact Map","Custom Data Importer","Dedicated IP","Export Conversations","FieldTask - Dispatch, Routes and Time","GenStep","Gr8social Posting and Commenting Add-On","LeadFlow Receptionist AI","LinkedConnector","Listings Subscription","Marketplace App - Advanced AI Call Transcript & Summary","Marketplace App -sg-test","Marketplace_App_65955e842ecede6412d03081","Marketplace_App_665f3ba2a4415aeb385b803e","Marketplace_App_66cad75e2ff96a2469f18fbc","Marketplace_App_67200e0bd2fbd053defb2863","Marketplace_App_6780104a3b03639980cc1b5c","Marketplace_App_679bfc74803b8a60b9bcd855","Marketplace_App_67bf112b10332b528011dcec","Marketplace_App_6836bcb8eb1ce7acf9241b8b","Marketplace_App_6838358434fce042d3f57deb","Marketplace App 687b7fb0a1a7f906cfeef850","Marketplace_App_68ab7c88286b47026318dee2","Meps Jordan","MOYASAR","Music Lessons & Schools Voice AI Agent Template","nerD AI Insights","New external payment subaccount","Opendental Connector","Local Payment","PayBridge Connect","payfast By Dripex","PayMob - Receive your payments online with ease","PayPlus Payment","Priority Support by Help Desk","Prospecting Subscription","Pulse AI","Rocket Integrations","RoofMate AI Voice Agent","Scribe Testing 2","Self Selling Voice AI Agents","SendGrid Email","SEO Search Atlas Subscription","Social CRM","Spintax","Sumit Pay","Tap Payment Pro","Tap Payments","TaraPayments","TaraPayments","TaraPayments","Tasker","TelegrApp","Tiktok CAPI","Ultimate Outbound / Inbound Sales Team","User Created","Volt","WhatsApp Subscription","Wordpress Subscription","Workflows Subscription"];
  const walletHtml=walletItems.map(n=>leafRow(n,0,0)).join('');
  html+=topNode("Wallet Subscriptions",walletHtml,0,0);

  // WhatsApp
  const wm=getVal(cm,"whatsapp_marketing");
  const wu=getVal(cm,"whatsapp_utility");
  const waTotal=wm.amt+wu.amt;
  const waQty=wm.qty+wu.qty;
  const waHtml=leafRow("WhatsApp Calls",0,0)+leafRow("WhatsApp Marketing Messages",wm.qty,wm.amt)+leafRow("WhatsApp Utility Messages",wu.qty,wu.amt);
  html+=topNode("WhatsApp Usage",waHtml,waQty,waTotal);

  // Workflow
  html+=`<div class="node"><div class="node-hdr"><div class="node-left"><span style="width:14px"></span><span class="node-title">Workflow - Premium Features</span></div><div class="node-right"><span class="node-qty">0 transactions</span>${fmtAmt(0)}</div></div></div>`;

  // Other
  html+=`<div class="node"><div class="node-hdr"><div class="node-left"><span style="width:14px"></span><span class="node-title">Other Charges</span></div><div class="node-right"><span class="node-qty">0 transactions</span>${fmtAmt(0)}</div></div></div>`;

  return html;
}

function renderTabs(){document.getElementById("tabs").innerHTML=M.map(m=>`<div class="tab${m===active?" active":""}" onclick="switchTo('${m}')">${fmtMonth(m)}</div>`).join("");}
function switchTo(m){active=m;renderTabs();renderContent();}

function renderContent(){
  const el=document.getElementById("main");
  if(!el)return;
  const d=D[active];
  if(!d){el.innerHTML='<div class="empty"><h2>No data</h2></div>';return;}
  uid=0;
  const cm={};
  d.cards.forEach(c=>{cm[c.service]=c;});
  let html='<div class="disclaimer">⚠️ <span><b>Disclaimer:</b> This billing report reflects actual credit usage. Reports updated on the <b>7th of each month</b> once charges are finalized.</span></div>';
  html+=`<div class="total-card"><div class="total-left"><div class="total-icon">💳</div><div><div class="total-name">Total Usage</div><div class="total-sub">For ${fmtMonth(active)}</div></div></div><div class="total-amount">${fmt2(d.total)}</div></div>`;
  html+=`<div class="tree">${buildTree(cm)}</div>`;
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
