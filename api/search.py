"""
GET /api/search?q=<query>

1. Builds a single Google Custom Search query combining the user's
   search text with `site:` filters for every domain in domains.json
   (one CSE call regardless of how many domains you track - keeps you
   well inside the 100 free queries/day).
2. Takes the first matching result per domain.
3. Scrapes each matched page for its real price (concurrently, to
   stay under Vercel's execution time limit).
4. Returns a JSON list the frontend can render directly.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
import sys
sys.path.insert(0, str(Path(__file__).parent))
import pricelib

DOMAINS_FILE = Path(__file__).parent / "domains.json"


def load_domains():
    with open(DOMAINS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def google_search(query, domains, api_key, cx):
    site_filter = " OR ".join(f"site:{d['domain']}" for d in domains)
    full_query = f"{query} ({site_filter})"

    resp = requests.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": api_key, "cx": cx, "q": full_query, "num": 10},
        timeout=8,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def first_match_per_domain(items, domains):
    """Keep only the first (highest-ranked) result for each tracked
    domain, so we don't scrape the same site twice."""
    matched = {}
    for item in items:
        link = item.get("link")
        if not link:
            continue
        domain_info = pricelib.detect_site(link, domains)
        if domain_info and domain_info["site"] not in matched:
            matched[domain_info["site"]] = {
                "site": domain_info["site"],
                "label": domain_info["label"],
                "url": link,
                "search_title": item.get("title"),
            }
    return list(matched.values())


def scrape_one(candidate):
    result = pricelib.scrape_url(candidate["url"], candidate["site"])
    return {
        "site": candidate["site"],
        "label": candidate["label"],
        "url": candidate["url"],
        "name": result.get("name") or candidate.get("search_title"),
        "price": result.get("price"),
        "image": result.get("image"),
        "error": result.get("error"),
    }


def handle_search(query):
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx = os.environ.get("GOOGLE_CSE_ID")
    if not api_key or not cx:
        return {"error": "Server is missing GOOGLE_API_KEY / GOOGLE_CSE_ID env vars"}, 500

    domains = load_domains()

    try:
        items = google_search(query, domains, api_key, cx)
    except requests.RequestException as e:
        return {"error": f"Google search failed: {e}"}, 502

    candidates = first_match_per_domain(items, domains)
    if not candidates:
        return {"query": query, "results": []}, 200

    results = []
    with ThreadPoolExecutor(max_workers=min(6, len(candidates))) as ex:
        futures = [ex.submit(scrape_one, c) for c in candidates]
        for fut in as_completed(futures):
            results.append(fut.result())

    # cheapest first
    results.sort(key=lambda r: (r["price"] is None, r["price"]))
    return {"query": query, "results": results}, 200


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        query = (params.get("q", [""])[0]).strip()

        if not query:
            body, status = {"error": "missing ?q= parameter"}, 400
        else:
            body, status = handle_search(query)

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()
