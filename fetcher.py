"""
fetcher.py
-----------
Pulls usage data from GHL API, counts messages by type,
applies pricing rates, and saves to local database.

Run once manually, or schedule to run every 15 minutes.
"""

import os
import json
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from ghl_client import GHLClient
from database  import (
    init_db, upsert_daily_usage, rebuild_monthly_from_daily,
    upsert_transaction, get_last_fetch, set_last_fetch,
    mark_conversation_processed, get_processed_conversation_ids,
)


# ----------------------------------------------------------
# Message type → pricing key mapping
# GHL uses these TYPE_ values in lastMessageType field
# ----------------------------------------------------------
MESSAGE_TYPE_MAP = {
    # WhatsApp
    "TYPE_WHATSAPP":                  "whatsapp_marketing",
    "TYPE_WHATSAPP_TEMPLATE":         "whatsapp_marketing",
    "TYPE_WHATSAPP_MARKETING":        "whatsapp_marketing",
    "TYPE_WHATSAPP_UTILITY":          "whatsapp_utility",

    # Email
    "TYPE_EMAIL":                     "email",
    "TYPE_EMAIL_VERIFICATION":        "email_verification",
    "TYPE_EMAIL_NOTIFICATION":        "email_notification",

    # SMS
    "TYPE_SMS":                       "sms",

    # Phone
    "TYPE_PHONE":                     "calls",
    "TYPE_CALL":                      "calls",

    # AI
    "TYPE_CONVERSATION_AI":           "conversation_voice_ai",
    "TYPE_VOICE_AI":                  "conversation_voice_ai",
    "TYPE_REVIEW_AI":                 "reviews_ai",
}


def load_config(path: str = "config.json") -> dict:
    with open(path, "r") as f:
        return json.load(f)


def map_message_type(raw_type: str) -> str:
    """Map a GHL message type string to our pricing key."""
    if not raw_type:
        return "other"
    normalized = raw_type.upper().strip()
    return MESSAGE_TYPE_MAP.get(normalized, "other")


# ----------------------------------------------------------
# MAIN FETCH LOGIC
# ----------------------------------------------------------
def fetch_and_store(client: GHLClient, pricing: dict,
                    months_back: int = 3):
    """
    Pull conversations + messages from GHL for the last N months,
    count by service type, apply pricing, store in DB.
    """

    now        = datetime.now(timezone.utc)
    start_date = (now - timedelta(days=30 * months_back)).strftime("%Y-%m-%d")
    end_date   = now.strftime("%Y-%m-%d")

    print(f"\n{'='*55}")
    print(f"  Fetch started: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  Range: {start_date} → {end_date}")
    print(f"{'='*55}\n")

    # --------------------------------------------------------
    # Step A: Get all conversations in range
    # --------------------------------------------------------
    conversations = client.get_conversations(start_date, end_date)
    print(f"\n  Total conversations: {len(conversations)}")

    if not conversations:
        print("  No conversations found. Nothing to process.")
        return

    # --------------------------------------------------------
    # Step B: For each conversation, fetch messages
    #         and count by type per day
    # --------------------------------------------------------
    # Structure: { 'YYYY-MM-DD': { 'service_key': count } }
    daily_counts = defaultdict(lambda: defaultdict(int))

    already_processed = get_processed_conversation_ids()
    new_count         = 0
    skip_count        = 0

    for i, convo in enumerate(conversations):
        convo_id     = convo.get("id")
        last_updated = convo.get("dateUpdated", "")

        # Print progress every 10 conversations
        if (i + 1) % 10 == 0:
            print(f"  Processing conversation {i+1}/{len(conversations)}...")

        # Try to get messages for this conversation
        try:
            messages = client.get_messages(convo_id)
            new_count += 1

            for msg in messages:
                # Get the message date
                msg_date = msg.get("dateAdded") or msg.get("createdAt", "")
                if msg_date:
                    # Convert timestamp (ms) or ISO string to YYYY-MM-DD
                    if isinstance(msg_date, (int, float)):
                        dt = datetime.fromtimestamp(
                            msg_date / 1000, tz=timezone.utc
                        )
                    else:
                        try:
                            dt = datetime.fromisoformat(
                                msg_date.replace("Z", "+00:00")
                            )
                        except Exception:
                            dt = now
                    date_str = dt.strftime("%Y-%m-%d")
                else:
                    date_str = now.strftime("%Y-%m-%d")

                # Map message type to service key
                raw_type    = msg.get("messageType") or msg.get("type", "")
                service_key = map_message_type(raw_type)

                # Only count outbound messages (we sent them = we pay for them)
                direction = msg.get("direction", "").upper()
                if direction in ("OUTBOUND", "SENT", "") or not direction:
                    daily_counts[date_str][service_key] += 1

            mark_conversation_processed(convo_id, len(messages))
            time.sleep(0.1)  # gentle rate limiting

        except Exception as e:
            print(f"  ⚠ Skipped conversation {convo_id}: {e}")
            skip_count += 1
            continue

    print(f"\n  Processed: {new_count} conversations "
          f"({skip_count} skipped)")

    # --------------------------------------------------------
    # Step C: Apply pricing rates and save to database
    # --------------------------------------------------------
    print("\n  Saving usage to database...")
    total_records = 0

    for date_str, services in daily_counts.items():
        for service_key, count in services.items():
            rate = pricing.get(service_key, 0.0)
            cost = count * rate

            upsert_daily_usage(date_str, service_key, count, cost)
            total_records += 1

    print(f"  Saved {total_records} daily records.")

    # --------------------------------------------------------
    # Step D: Rebuild monthly totals
    # --------------------------------------------------------
    rebuild_monthly_from_daily()
    print("  Monthly totals rebuilt.")

    # --------------------------------------------------------
    # Step E: Fetch transactions (top-ups)
    # --------------------------------------------------------
    print("\n  Fetching transactions...")
    try:
        transactions = client.get_transactions(start_date, end_date)
        for txn in transactions:
            upsert_transaction(txn)
        print(f"  Saved {len(transactions)} transactions.")
    except Exception as e:
        print(f"  ⚠ Transactions fetch failed: {e}")

    # --------------------------------------------------------
    # Step F: Update last fetch timestamp
    # --------------------------------------------------------
    set_last_fetch("conversations")
    set_last_fetch("transactions")

    print(f"\n✅ Fetch complete at "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")


# ----------------------------------------------------------
# SCHEDULER: run every N minutes
# ----------------------------------------------------------
def run_scheduler(interval_minutes: int = 15):
    print(f"🔄 Scheduler started — fetching every {interval_minutes} min.")
    print("   Press Ctrl+C to stop.\n")

    while True:
        try:
            config   = load_config()
            pricing  = config.get("pricing", {})
            token    = os.getenv("GHL_ACCESS_TOKEN")
            location = os.getenv("GHL_LOCATION_ID") or config.get("location_id")

            if not token:
                print("❌ GHL_ACCESS_TOKEN not set.")
                break

            client = GHLClient(token, location)
            fetch_and_store(client, pricing)

        except Exception as e:
            print(f"❌ Fetch error: {e}")

        print(f"\n  Next fetch in {interval_minutes} minutes...")
        time.sleep(interval_minutes * 60)


# ----------------------------------------------------------
# Entry point
# ----------------------------------------------------------
if __name__ == "__main__":
    import sys

    # Initialize DB first
    init_db()

    config   = load_config()
    pricing  = config.get("pricing", {})
    token    = os.getenv("GHL_ACCESS_TOKEN")
    location = os.getenv("GHL_LOCATION_ID") or config.get("location_id")

    if not token:
        print("❌ Set GHL_ACCESS_TOKEN environment variable first.")
        sys.exit(1)

    client = GHLClient(token, location)

    # Check for --schedule flag
    if "--schedule" in sys.argv:
        interval = config.get("refresh_interval_minutes", 15)
        run_scheduler(interval)
    else:
        # Single run
        fetch_and_store(client, pricing, months_back=3)
