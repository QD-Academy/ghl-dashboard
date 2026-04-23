"""
app.py
-------
Combined entry point for Railway deployment.
Runs the data fetcher on a schedule AND serves the dashboard,
all in one process — no need for two terminals.
"""

import os
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from flask import Flask, jsonify, render_template_string
from apscheduler.schedulers.background import BackgroundScheduler

from ghl_client import GHLClient
from database import (
    init_db, upsert_daily_usage, rebuild_monthly_from_daily,
    upsert_transaction, set_last_fetch,
    mark_conversation_processed,
    get_available_months, get_monthly_summary, get_total_for_month,
)

app = Flask(__name__)

# ----------------------------------------------------------
# Config
# ----------------------------------------------------------
def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

# ----------------------------------------------------------
# Message type → pricing key mapping
# ----------------------------------------------------------
MESSAGE_TYPE_MAP = {
    "TYPE_WHATSAPP":               "whatsapp_marketing",
    "TYPE_WHATSAPP_TEMPLATE":      "whatsapp_marketing",
    "TYPE_WHATSAPP_MARKETING":     "whatsapp_marketing",
    "TYPE_WHATSAPP_UTILITY":       "whatsapp_utility",
    "TYPE_EMAIL":                  "email",
    "TYPE_EMAIL_VERIFICATION":     "email_verification",
    "TYPE_EMAIL_NOTIFICATION":     "email_notification",
    "TYPE_SMS":                    "sms",
    "TYPE_PHONE":                  "calls",
    "TYPE_CALL":                   "calls",
    "TYPE_CONVERSATION_AI":        "conversation_voice_ai",
    "TYPE_VOICE_AI":               "conversation_voice_ai",
    "TYPE_REVIEW_AI":              "reviews_ai",
}

SERVICE_LABELS = {
    "whatsapp_marketing":    "WhatsApp Marketing Message",
    "whatsapp_utility":      "WhatsApp Utility Message",
    "email":                 "Email",
    "email_notification":    "Email Notification",
    "email_verification":    "Email Verification",
    "conversation_voice_ai": "Conversation & Voice AI",
    "reviews_ai":            "Reviews AI",
    "workflow_premium":      "Workflow Premium Features",
    "sms":                   "SMS",
    "calls":                 "Calls",
    "other":                 "Other",
}

SERVICE_COLORS = {
    "whatsapp_marketing":    "#4f46e5",
    "whatsapp_utility":      "#7c3aed",
    "email":                 "#0891b2",
    "email_notification":    "#0284c7",
    "email_verification":    "#059669",
    "conversation_voice_ai": "#d97706",
    "reviews_ai":            "#dc2626",
    "workflow_premium":      "#7c3aed",
    "sms":                   "#16a34a",
    "calls":                 "#9333ea",
    "other":                 "#6b7280",
}

# Track last successful fetch time
last_fetch_time = None


# ----------------------------------------------------------
# Fetcher logic
# ----------------------------------------------------------
def map_message_type(raw_type: str) -> str:
    if not raw_type:
        return "other"
    return MESSAGE_TYPE_MAP.get(raw_type.upper().strip(), "other")


def run_fetch():
    global last_fetch_time
    try:
        config      = load_config()
        pricing     = config.get("pricing", {})
        token       = os.getenv("GHL_ACCESS_TOKEN")
        location_id = os.getenv("GHL_LOCATION_ID") or config.get("location_id")

        if not token:
            print("❌ GHL_ACCESS_TOKEN not set. Skipping fetch.")
            return

        client     = GHLClient(token, location_id)
        now        = datetime.now(timezone.utc)
        start_date = (now - timedelta(days=90)).strftime("%Y-%m-%d")
        end_date   = now.strftime("%Y-%m-%d")

        print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] Fetching data...")

        # Get conversations
        conversations = client.get_conversations(start_date, end_date)
        print(f"  Found {len(conversations)} conversations")

        daily_counts = defaultdict(lambda: defaultdict(int))

        for i, convo in enumerate(conversations):
            convo_id = convo.get("id")
            try:
                messages = client.get_messages(convo_id)
                for msg in messages:
                    msg_date = msg.get("dateAdded") or msg.get("createdAt", "")
                    if isinstance(msg_date, (int, float)):
                        dt = datetime.fromtimestamp(msg_date / 1000, tz=timezone.utc)
                    else:
                        try:
                            dt = datetime.fromisoformat(msg_date.replace("Z", "+00:00"))
                        except Exception:
                            dt = now
                    date_str    = dt.strftime("%Y-%m-%d")
                    raw_type    = msg.get("messageType") or msg.get("type", "")
                    service_key = map_message_type(raw_type)
                    direction   = msg.get("direction", "").upper()
                    if direction in ("OUTBOUND", "SENT", "") or not direction:
                        daily_counts[date_str][service_key] += 1
                mark_conversation_processed(convo_id, len(messages))
                time.sleep(0.1)
            except Exception as e:
                print(f"  ⚠ Skipped {convo_id}: {e}")
                continue

        # Save to DB
        for date_str, services in daily_counts.items():
            for service_key, count in services.items():
                rate = pricing.get(service_key, 0.0)
                upsert_daily_usage(date_str, service_key, count, count * rate)

        rebuild_monthly_from_daily()

        # Fetch transactions
        try:
            txns = client.get_transactions(start_date, end_date)
            for txn in txns:
                upsert_transaction(txn)
        except Exception as e:
            print(f"  ⚠ Transactions error: {e}")

        set_last_fetch("conversations")
        last_fetch_time = datetime.now(timezone.utc)
        print(f"  ✅ Fetch complete.")

    except Exception as e:
        print(f"❌ Fetch failed: {e}")


# ----------------------------------------------------------
# API endpoint
# ----------------------------------------------------------
@app.route("/api/data")
def api_data():
    months = get_available_months()
    if not months:
        return jsonify({"months": [], "data": {}})

    result = {}
    for month in months:
        summary    = get_monthly_summary(month)
        total      = get_total_for_month(month)
        idx        = months.index(month)
        prev_month = months[idx + 1] if idx + 1 < len(months) else None
        prev_total = get_total_for_month(prev_month) if prev_month else 0

        if prev_total > 0:
            mom_pct = ((total - prev_total) / prev_total) * 100
        elif total > 0:
            mom_pct = 100
        else:
            mom_pct = 0

        cards = []
        for row in summary:
            if row["cost"] == 0 and row["message_count"] == 0:
                continue
            service = row["service"]
            pct     = (row["cost"] / total * 100) if total > 0 else 0
            cards.append({
                "service":       service,
                "label":         SERVICE_LABELS.get(service, service),
                "color":         SERVICE_COLORS.get(service, "#6b7280"),
                "message_count": row["message_count"],
                "cost":          round(row["cost"], 4),
                "pct_of_total":  round(pct, 1),
            })

        result[month] = {
            "total":      round(total, 4),
            "prev_total": round(prev_total, 4),
            "mom_pct":    round(mom_pct, 1),
            "prev_month": prev_month,
            "cards":      cards,
        }

    last_sync = last_fetch_time.strftime("%Y-%m-%d %H:%M UTC") if last_fetch_time else "Not yet"
    return jsonify({"months": months, "data": result, "last_sync": last_sync})


# ----------------------------------------------------------
# Dashboard HTML
# ----------------------------------------------------------
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>QD Academy — Credit Usage</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #f4f6f9; color: #1a1a2e; min-height: 100vh;
    }
    .header {
      background: #fff; border-bottom: 1px solid #e5e7eb;
      padding: 16px 24px; display: flex;
      align-items: center; justify-content: space-between;
    }
    .header h1 { font-size: 18px; font-weight: 600; color: #111827; }
    .header .subtitle { font-size: 13px; color: #6b7280; margin-top: 2px; }
    .sync-info { font-size: 12px; color: #9ca3af; text-align: right; }
    .tabs-wrapper {
      background: #fff; border-bottom: 1px solid #e5e7eb; padding: 0 24px;
      overflow-x: auto;
    }
    .tabs { display: flex; gap: 4px; min-width: max-content; }
    .tab {
      padding: 14px 20px; font-size: 14px; font-weight: 500;
      color: #6b7280; cursor: pointer;
      border-bottom: 2px solid transparent; transition: all 0.15s;
      white-space: nowrap;
    }
    .tab:hover { color: #4f46e5; }
    .tab.active { color: #4f46e5; border-bottom-color: #4f46e5; font-weight: 600; }
    .content { padding: 24px; max-width: 960px; margin: 0 auto; }
    .total-banner {
      margin-bottom: 24px; display: flex;
      align-items: center; gap: 12px; flex-wrap: wrap;
    }
    .total-amount { font-size: 26px; font-weight: 700; color: #111827; }
    .total-label  { font-size: 15px; color: #6b7280; }
    .mom-badge {
      display: inline-flex; align-items: center; gap: 4px;
      padding: 4px 10px; border-radius: 20px;
      font-size: 13px; font-weight: 600;
    }
    .mom-up   { background: #dcfce7; color: #16a34a; }
    .mom-down { background: #fee2e2; color: #dc2626; }
    .mom-flat { background: #f3f4f6; color: #6b7280; }
    .cards-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 16px;
    }
    .card {
      background: #fff; border-radius: 12px; padding: 20px;
      border: 1px solid #e5e7eb; transition: box-shadow 0.15s;
    }
    .card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
    .card-title { font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 10px; }
    .card-row {
      display: flex; align-items: baseline;
      justify-content: space-between; margin-bottom: 10px;
    }
    .card-cost  { font-size: 22px; font-weight: 700; }
    .card-prev  { font-size: 12px; color: #9ca3af; }
    .progress-bar-bg {
      height: 5px; background: #f3f4f6;
      border-radius: 99px; margin-bottom: 8px; overflow: hidden;
    }
    .progress-bar-fill {
      height: 100%; border-radius: 99px; transition: width 0.5s ease;
    }
    .card-pct   { font-size: 12px; color: #6b7280; }
    .card-count { font-size: 11px; color: #9ca3af; margin-top: 4px; }
    .empty {
      text-align: center; padding: 60px 20px; color: #9ca3af;
    }
    .empty h2 { font-size: 18px; margin-bottom: 8px; color: #6b7280; }
    .loading { text-align: center; padding: 60px; color: #6b7280; font-size: 15px; }
    .footer {
      text-align: center; font-size: 12px; color: #9ca3af;
      margin-top: 32px; padding-bottom: 24px;
    }
  </style>
</head>
<body>
<div class="header">
  <div>
    <h1>Credit Usage Dashboard</h1>
    <div class="subtitle">QD Academy — GoHighLevel</div>
  </div>
  <div class="sync-info" id="sync-info">Loading...</div>
</div>
<div class="tabs-wrapper">
  <div class="tabs" id="tabs"></div>
</div>
<div class="content">
  <div id="main" class="loading">Loading data...</div>
</div>
<div class="footer" id="footer"></div>

<script>
  let allData = {}, allMonths = [], activeMonth = null;

  function formatMonth(m) {
    const [y, mo] = m.split("-");
    const names = ["Jan","Feb","Mar","Apr","May","Jun",
                   "Jul","Aug","Sep","Oct","Nov","Dec"];
    return names[parseInt(mo)-1] + " " + y;
  }

  function renderTabs() {
    document.getElementById("tabs").innerHTML = allMonths.map(m => `
      <div class="tab ${m===activeMonth?'active':''}" onclick="switchMonth('${m}')">
        ${formatMonth(m)}
      </div>`).join("");
  }

  function switchMonth(m) {
    activeMonth = m; renderTabs(); renderContent();
  }

  function renderContent() {
    const main = document.getElementById("main");
    const d    = allData[activeMonth];
    if (!d) {
      main.innerHTML = '<div class="empty"><h2>No data for this month</h2></div>';
      return;
    }
    let momHtml = "";
    if (d.prev_month) {
      const pct  = d.mom_pct;
      const cls  = pct>0?"mom-up":pct<0?"mom-down":"mom-flat";
      const sign = pct>0?"↑":pct<0?"↓":"→";
      const abs  = Math.abs(pct);
      const label = abs>100?`>100% ${sign}`:`${abs.toFixed(1)}% ${sign}`;
      momHtml = `<span class="mom-badge ${cls}">${label}</span>
                 <span class="total-label">vs ${formatMonth(d.prev_month)}</span>`;
    }
    let cardsHtml = d.cards.length === 0
      ? '<div class="empty"><h2>No usage recorded this month</h2></div>'
      : '<div class="cards-grid">' + d.cards.map(card => `
          <div class="card">
            <div class="card-title">${card.label}</div>
            <div class="card-row">
              <span class="card-cost" style="color:${card.color}">
                $${card.cost.toFixed(4)}
              </span>
              <span class="card-prev">from $0</span>
            </div>
            <div class="progress-bar-bg">
              <div class="progress-bar-fill"
                   style="width:${card.pct_of_total}%;background:${card.color}">
              </div>
            </div>
            <div class="card-pct">${card.pct_of_total}% of total</div>
            <div class="card-count">${card.message_count.toLocaleString()} transactions</div>
          </div>`).join("") + "</div>";

    main.innerHTML = `
      <div class="total-banner">
        <span class="total-amount">$${d.total.toFixed(2)}</span>
        <span class="total-label">total for ${formatMonth(activeMonth)}</span>
        ${momHtml}
      </div>
      ${cardsHtml}`;
  }

  async function loadData() {
    try {
      const resp = await fetch("/api/data");
      const json = await resp.json();
      allMonths = json.months || [];
      allData   = json.data   || {};
      document.getElementById("sync-info").textContent =
        "Last synced: " + (json.last_sync || "Unknown");
      if (allMonths.length === 0) {
        document.getElementById("main").innerHTML =
          '<div class="empty"><h2>No data yet</h2><p>Fetcher is warming up...</p></div>';
        return;
      }
      if (!activeMonth || !allMonths.includes(activeMonth)) {
        activeMonth = allMonths[0];
      }
      renderTabs();
      renderContent();
    } catch(e) {
      document.getElementById("main").innerHTML =
        '<div class="empty"><h2>Failed to load</h2><p>'+e.message+'</p></div>';
    }
  }

  loadData();
  setInterval(loadData, 15 * 60 * 1000);
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


# ----------------------------------------------------------
# Start scheduler + server
# ----------------------------------------------------------
if __name__ == "__main__":
    init_db()

    # Run first fetch immediately in background
    thread = threading.Thread(target=run_fetch, daemon=True)
    thread.start()

    # Schedule fetch every 15 minutes
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_fetch, "interval", minutes=15)
    scheduler.start()

    port = int(os.environ.get("PORT", 5000))
    print(f"\n✅ App starting on port {port}")
    print(f"   Dashboard: http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
