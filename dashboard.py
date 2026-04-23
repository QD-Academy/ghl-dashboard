"""
dashboard.py
-------------
Serves the credit usage dashboard as a local web page.
Open http://localhost:5000 in your browser after running this.

Install Flask first:
    python -m pip install flask
"""

import os
import json
from datetime import datetime
from flask import Flask, jsonify, render_template_string

from database import (
    get_available_months,
    get_monthly_summary,
    get_total_for_month,
)

app = Flask(__name__)

# ----------------------------------------------------------
# Service display names (maps DB key → friendly label)
# ----------------------------------------------------------
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


# ----------------------------------------------------------
# API: return dashboard data as JSON
# ----------------------------------------------------------
@app.route("/api/data")
def api_data():
    months     = get_available_months()
    if not months:
        return jsonify({"months": [], "data": {}})

    result = {}
    for month in months:
        summary    = get_monthly_summary(month)
        total      = get_total_for_month(month)

        # Find previous month total for MoM comparison
        idx        = months.index(month)
        prev_month = months[idx + 1] if idx + 1 < len(months) else None
        prev_total = get_total_for_month(prev_month) if prev_month else 0

        if prev_total > 0:
            mom_pct = ((total - prev_total) / prev_total) * 100
        elif total > 0:
            mom_pct = 100
        else:
            mom_pct = 0

        # Build service cards
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

    return jsonify({"months": months, "data": result})


# ----------------------------------------------------------
# Main dashboard page
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
      background: #f4f6f9;
      color: #1a1a2e;
      min-height: 100vh;
    }

    /* Header */
    .header {
      background: #fff;
      border-bottom: 1px solid #e5e7eb;
      padding: 16px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .header h1 {
      font-size: 18px;
      font-weight: 600;
      color: #111827;
    }
    .header .subtitle {
      font-size: 13px;
      color: #6b7280;
      margin-top: 2px;
    }
    .refresh-btn {
      background: #4f46e5;
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 8px 16px;
      font-size: 13px;
      cursor: pointer;
      transition: background 0.2s;
    }
    .refresh-btn:hover { background: #4338ca; }

    /* Month tabs */
    .tabs-wrapper {
      background: #fff;
      border-bottom: 1px solid #e5e7eb;
      padding: 0 24px;
    }
    .tabs {
      display: flex;
      gap: 4px;
    }
    .tab {
      padding: 14px 20px;
      font-size: 14px;
      font-weight: 500;
      color: #6b7280;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      transition: all 0.15s;
    }
    .tab:hover { color: #4f46e5; }
    .tab.active {
      color: #4f46e5;
      border-bottom-color: #4f46e5;
      font-weight: 600;
    }

    /* Content area */
    .content { padding: 24px; max-width: 960px; margin: 0 auto; }

    /* Total banner */
    .total-banner {
      margin-bottom: 24px;
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .total-amount {
      font-size: 26px;
      font-weight: 700;
      color: #111827;
    }
    .total-label {
      font-size: 15px;
      color: #6b7280;
    }
    .mom-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 600;
    }
    .mom-up   { background: #dcfce7; color: #16a34a; }
    .mom-down { background: #fee2e2; color: #dc2626; }
    .mom-flat { background: #f3f4f6; color: #6b7280; }

    /* Service cards grid */
    .cards-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 16px;
    }

    .card {
      background: #fff;
      border-radius: 12px;
      padding: 20px;
      border: 1px solid #e5e7eb;
      transition: box-shadow 0.15s;
    }
    .card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.08); }

    .card-title {
      font-size: 13px;
      font-weight: 600;
      color: #374151;
      margin-bottom: 10px;
    }
    .card-row {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      margin-bottom: 10px;
    }
    .card-cost {
      font-size: 22px;
      font-weight: 700;
    }
    .card-prev {
      font-size: 12px;
      color: #9ca3af;
    }
    .progress-bar-bg {
      height: 5px;
      background: #f3f4f6;
      border-radius: 99px;
      margin-bottom: 8px;
      overflow: hidden;
    }
    .progress-bar-fill {
      height: 100%;
      border-radius: 99px;
      transition: width 0.5s ease;
    }
    .card-pct {
      font-size: 12px;
      color: #6b7280;
    }
    .card-count {
      font-size: 11px;
      color: #9ca3af;
      margin-top: 4px;
    }

    /* Empty state */
    .empty {
      text-align: center;
      padding: 60px 20px;
      color: #9ca3af;
    }
    .empty h2 { font-size: 18px; margin-bottom: 8px; color: #6b7280; }

    /* Loading */
    .loading {
      text-align: center;
      padding: 60px;
      color: #6b7280;
      font-size: 15px;
    }

    /* Last updated */
    .last-updated {
      text-align: right;
      font-size: 12px;
      color: #9ca3af;
      margin-top: 24px;
    }
  </style>
</head>
<body>

<div class="header">
  <div>
    <h1>Credit Usage Dashboard</h1>
    <div class="subtitle">QD Academy — GoHighLevel</div>
  </div>
  <button class="refresh-btn" onclick="loadData()">↻ Refresh</button>
</div>

<div class="tabs-wrapper">
  <div class="tabs" id="tabs"></div>
</div>

<div class="content">
  <div id="main" class="loading">Loading data...</div>
  <div class="last-updated" id="last-updated"></div>
</div>

<script>
  let allData    = {};
  let allMonths  = [];
  let activeMonth = null;

  function formatMonth(m) {
    const [y, mo] = m.split("-");
    const names = ["Jan","Feb","Mar","Apr","May","Jun",
                   "Jul","Aug","Sep","Oct","Nov","Dec"];
    return names[parseInt(mo) - 1] + " " + y;
  }

  function renderTabs() {
    const tabs = document.getElementById("tabs");
    tabs.innerHTML = allMonths.map(m => `
      <div class="tab ${m === activeMonth ? 'active' : ''}"
           onclick="switchMonth('${m}')">
        ${formatMonth(m)}
      </div>
    `).join("");
  }

  function switchMonth(month) {
    activeMonth = month;
    renderTabs();
    renderContent();
  }

  function renderContent() {
    const main = document.getElementById("main");
    const d    = allData[activeMonth];

    if (!d) {
      main.innerHTML = '<div class="empty"><h2>No data for this month</h2></div>';
      return;
    }

    // MoM badge
    let momHtml = "";
    if (d.prev_month) {
      const pct  = d.mom_pct;
      const cls  = pct > 0 ? "mom-up" : pct < 0 ? "mom-down" : "mom-flat";
      const sign = pct > 0 ? "↑" : pct < 0 ? "↓" : "→";
      const abs  = Math.abs(pct);
      const label = abs > 100
        ? `>100% ${sign}`
        : `${abs.toFixed(1)}% ${sign}`;
      momHtml = `<span class="mom-badge ${cls}">${label}</span>
                 <span class="total-label">vs ${formatMonth(d.prev_month)}</span>`;
    }

    // Cards
    let cardsHtml = "";
    if (d.cards.length === 0) {
      cardsHtml = '<div class="empty"><h2>No usage recorded this month</h2></div>';
    } else {
      cardsHtml = '<div class="cards-grid">' +
        d.cards.map(card => `
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
                   style="width:${card.pct_of_total}%;
                          background:${card.color}">
              </div>
            </div>
            <div class="card-pct">${card.pct_of_total}% of total</div>
            <div class="card-count">${card.message_count.toLocaleString()} transactions</div>
          </div>
        `).join("") +
      "</div>";
    }

    main.innerHTML = `
      <div class="total-banner">
        <span class="total-amount">$${d.total.toFixed(2)}</span>
        <span class="total-label">total for ${formatMonth(activeMonth)}</span>
        ${momHtml}
      </div>
      ${cardsHtml}
    `;
  }

  async function loadData() {
    document.getElementById("main").innerHTML =
      '<div class="loading">Loading...</div>';
    try {
      const resp = await fetch("/api/data");
      const json = await resp.json();

      allMonths  = json.months || [];
      allData    = json.data   || {};

      if (allMonths.length === 0) {
        document.getElementById("main").innerHTML =
          '<div class="empty"><h2>No data yet</h2>' +
          '<p>Run fetcher.py to pull your GHL usage data.</p></div>';
        return;
      }

      activeMonth = allMonths[0];
      renderTabs();
      renderContent();

      document.getElementById("last-updated").textContent =
        "Last updated: " + new Date().toLocaleString();

    } catch (e) {
      document.getElementById("main").innerHTML =
        '<div class="empty"><h2>Failed to load data</h2>' +
        '<p>' + e.message + '</p></div>';
    }
  }

  // Auto-refresh every 15 minutes
  loadData();
  setInterval(loadData, 15 * 60 * 1000);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


if __name__ == "__main__":
    print("\n✅ Dashboard starting...")
    print("   Open your browser and go to: http://localhost:5000")
    print("   Press Ctrl+C to stop.\n")
    app.run(debug=False, port=5000)
