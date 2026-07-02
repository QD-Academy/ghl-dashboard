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
    "2026-06": [
        ("whatsapp_marketing", 239, 33.9087),
        ("whatsapp_utility", 373, 8.3400),
        ("email_notification", 184, 0.3838),
        ("email_verification", 12, 0.0900),
        ("email", 33, 0.0688),
    ],
}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)


