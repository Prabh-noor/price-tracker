"""
Runs on a schedule via GitHub Actions (see .github/workflows/check-prices.yml).

For every bookmark belonging to every user in Firestore:
  1. Re-scrape each tracked site link.
  2. If any price dropped since the last check, record it.
  3. Push a notification via FCM to that user's devices that are
     platform == "ios" and isLoggedIn == True.

Uses the Firebase Admin SDK, which bypasses Firestore security rules
(so it can read/write across all users) - the credentials for this
must be kept secret (GitHub Actions secret, never committed).
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore, messaging

# Reuse the same scraping logic as the live search API without duplicating it
sys.path.insert(0, str(Path(__file__).parent.parent / "api"))
import pricelib  # noqa: E402

MAX_HISTORY_POINTS = 120


def init_firebase():
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not raw:
        raise SystemExit("FIREBASE_SERVICE_ACCOUNT env var is not set")
    cred = credentials.Certificate(json.loads(raw))
    firebase_admin.initialize_app(cred)
    return firestore.client()


def check_bookmark(db, bookmark_ref, bookmark_data):
    """Re-scrape each site link on this bookmark, return list of drops."""
    links = bookmark_data.get("links", [])
    last_prices = bookmark_data.get("lastPrices", {}) or {}
    history = bookmark_data.get("history", []) or []

    drops = []
    new_prices = dict(last_prices)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshot_prices = {}

    for link in links:
        site = link.get("site")
        url = link.get("url")
        if not url:
            continue

        result = pricelib.scrape_url(url, site)
        if result.get("error") or result.get("price") is None:
            continue

        new_price = result["price"]
        snapshot_prices[site] = new_price

        old_price = last_prices.get(site)
        if old_price is not None and new_price < old_price:
            drops.append(
                {
                    "site": site,
                    "label": link.get("label", site),
                    "url": url,
                    "old_price": old_price,
                    "new_price": new_price,
                }
            )

        new_prices[site] = new_price

    if snapshot_prices:
        history.append({"t": now, "prices": snapshot_prices})
        history = history[-MAX_HISTORY_POINTS:]

    bookmark_ref.update(
        {
            "lastPrices": new_prices,
            "history": history,
            "lastCheckedAt": now,
        }
    )

    return drops


def get_ios_logged_in_tokens(db, uid):
    devices = (
        db.collection("users")
        .document(uid)
        .collection("devices")
        .where("platform", "==", "ios")
        .where("isLoggedIn", "==", True)
        .stream()
    )
    tokens = []
    refs = []
    for d in devices:
        data = d.to_dict()
        if data.get("fcmToken"):
            tokens.append(data["fcmToken"])
            refs.append(d.reference)
    return tokens, refs


def send_drop_notification(query_label, drops, tokens):
    if not tokens or not drops:
        return []

    best = min(drops, key=lambda d: d["new_price"])
    title = f"Price drop: {query_label}"
    body = f"{best['label']} now ₹{best['new_price']:.0f} (was ₹{best['old_price']:.0f})"
    if len(drops) > 1:
        body += f" · +{len(drops) - 1} more site(s) dropped"

    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data={"url": best["url"]},
        tokens=tokens,
    )
    response = messaging.send_each_for_multicast(message)

    invalid_indexes = [
        i for i, r in enumerate(response.responses) if not r.success
    ]
    return invalid_indexes


def main():
    db = init_firebase()

    bookmarks = db.collection_group("bookmarks").stream()

    checked = 0
    for bm in bookmarks:
        checked += 1
        data = bm.to_dict()
        uid = bm.reference.parent.parent.id
        query_label = data.get("query", "your bookmarked item")

        print(f"Checking bookmark '{query_label}' (user {uid})")
        drops = check_bookmark(db, bm.reference, data)

        if drops:
            print(f"  -> {len(drops)} site(s) dropped, notifying...")
            tokens, refs = get_ios_logged_in_tokens(db, uid)
            invalid = send_drop_notification(query_label, drops, tokens)
            for i in invalid:
                # token is no longer valid (app uninstalled, permission revoked, etc.)
                refs[i].delete()
        else:
            print("  -> no drop")

    print(f"\nChecked {checked} bookmark(s).")


if __name__ == "__main__":
    main()
