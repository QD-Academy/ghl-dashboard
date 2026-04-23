"""
ghl_client.py
--------------
Handles all GHL API communication.
Used by fetcher.py to pull usage data.
"""

import time
import requests
from datetime import datetime, timezone


BASE_URL    = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"


class GHLClient:
    def __init__(self, access_token: str, location_id: str):
        self.location_id = location_id
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Version":       API_VERSION,
            "Accept":        "application/json",
        })

    # ----------------------------------------------------------
    # INTERNAL: safe request with retry on rate limit
    # ----------------------------------------------------------
    def _get(self, path: str, params: dict = {}) -> dict:
        url = f"{BASE_URL}{path}"
        for attempt in range(3):
            resp = self.session.get(url, params=params, timeout=30)

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                print(f"  ⚠ Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue

            # Any other error — raise with details
            raise RuntimeError(
                f"GHL API {resp.status_code} on {path}: {resp.text[:300]}"
            )

        raise RuntimeError(f"Failed after 3 attempts: {path}")

    # ----------------------------------------------------------
    # 1. Location info (confirms token works)
    # ----------------------------------------------------------
    def get_location(self) -> dict:
        data = self._get(f"/locations/{self.location_id}")
        return data.get("location", data)

    # ----------------------------------------------------------
    # 2. Conversations (WhatsApp, Email, SMS usage)
    #    Returns ALL conversations in date range, paginated
    # ----------------------------------------------------------
    def get_conversations(self, start_date: str, end_date: str) -> list:
        """
        start_date / end_date: 'YYYY-MM-DD'
        GHL conversations endpoint requires Unix timestamps in milliseconds.
        """
        # Convert YYYY-MM-DD strings to Unix ms timestamps
        start_dt  = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt    = datetime.strptime(end_date,   "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_ms  = int(start_dt.timestamp() * 1000)
        end_ms    = int(end_dt.timestamp()   * 1000)

        all_conversations = []
        last_id = None

        print(f"  Fetching conversations {start_date} → {end_date}...")

        while True:
            params = {
                "locationId":     self.location_id,
                "limit":          100,
                "startAfterDate": start_ms,
                "endDate":        end_ms,
            }
            if last_id:
                params["lastMessageId"] = last_id

            data   = self._get("/conversations/search", params)
            convos = data.get("conversations", [])
            all_conversations.extend(convos)

            print(f"    Got {len(convos)} conversations "
                  f"(total: {len(all_conversations)})")

            if len(convos) < 100:
                break

            last_id = convos[-1].get("id")
            time.sleep(0.2)

        return all_conversations

    # ----------------------------------------------------------
    # 3. Messages inside a conversation
    #    Used to count per-type messages (WhatsApp/Email/SMS)
    # ----------------------------------------------------------
    def get_messages(self, conversation_id: str) -> list:
        data = self._get(
            f"/conversations/{conversation_id}/messages",
            {"limit": 100}
        )
        return data.get("messages", {}).get("messages", [])

    # ----------------------------------------------------------
    # 4. Transactions (top-ups / purchases)
    # ----------------------------------------------------------
    def get_transactions(self, start_date: str, end_date: str) -> list:
        all_transactions = []
        offset = 0

        print(f"  Fetching transactions {start_date} → {end_date}...")

        while True:
            params = {
                "altId":   self.location_id,
                "altType": "location",
                "startAt": start_date,
                "endAt":   end_date,
                "limit":   100,
                "offset":  offset,
            }
            data = self._get("/payments/transactions", params)
            txns = data.get("data", [])
            all_transactions.extend(txns)

            total = data.get("totalCount", 0)
            print(f"    Got {len(txns)} transactions "
                  f"(total: {len(all_transactions)}/{total})")

            if len(txns) < 100 or len(all_transactions) >= total:
                break

            offset += 100
            time.sleep(0.2)

        return all_transactions

    # ----------------------------------------------------------
    # 5. Orders
    # ----------------------------------------------------------
    def get_orders(self, start_date: str, end_date: str) -> list:
        all_orders = []
        offset = 0

        print(f"  Fetching orders {start_date} → {end_date}...")

        while True:
            params = {
                "altId":   self.location_id,
                "altType": "location",
                "limit":   100,
                "offset":  offset,
            }
            data   = self._get("/payments/orders", params)
            orders = data.get("data", [])
            all_orders.extend(orders)

            total = data.get("totalCount", 0)
            print(f"    Got {len(orders)} orders "
                  f"(total: {len(all_orders)}/{total})")

            if len(orders) < 100 or len(all_orders) >= total:
                break

            offset += 100
            time.sleep(0.2)

        return all_orders


# ----------------------------------------------------------
# Quick test — run this file directly to verify token works
# ----------------------------------------------------------
if __name__ == "__main__":
    import os
    import json

    token       = os.getenv("GHL_ACCESS_TOKEN")
    location_id = os.getenv("GHL_LOCATION_ID")

    if not token or not location_id:
        print("❌ Set GHL_ACCESS_TOKEN and GHL_LOCATION_ID first.")
        exit(1)

    client = GHLClient(token, location_id)

    print("Testing GHL connection...\n")

    # Test 1: location
    try:
        loc = client.get_location()
        print(f"✅ Location: {loc.get('name', 'unknown')} "
              f"(id: {loc.get('id', 'unknown')})")
    except Exception as e:
        print(f"❌ Location fetch failed: {e}")

    # Test 2: conversations (last 7 days)
    try:
        from datetime import timedelta
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        convos = client.get_conversations(
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d")
        )
        print(f"✅ Conversations (last 7 days): {len(convos)} found")
        if convos:
            sample = convos[0]
            print(f"   Sample keys: {list(sample.keys())[:8]}")
            print(f"   Sample type: {sample.get('type', 'N/A')}")
            print(f"   Sample channel: {sample.get('channel', 'N/A')}")
    except Exception as e:
        print(f"❌ Conversations fetch failed: {e}")

    # Test 3: transactions (last 30 days)
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=30)
        txns  = client.get_transactions(
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d")
        )
        print(f"✅ Transactions (last 30 days): {len(txns)} found")
        if txns:
            print(f"   Sample keys: {list(txns[0].keys())[:8]}")
    except Exception as e:
        print(f"❌ Transactions fetch failed: {e}")

    print("\nDone. Share the output above so we can verify data shape.")
