"""
Shared price-extraction helpers.

Given a product page URL, fetch it and pull out {name, price, image}.
Tries schema.org JSON-LD first (most stable - sites embed this for SEO,
so it's less likely to break than scraping CSS classes), then falls
back to a few site-specific selectors, then a last-resort rupee-amount
regex.
"""

import json
import re
import random
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

SITE_SELECTORS = {
    "amazon": [
        "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
        "#corePrice_feature_div .a-price .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
    ],
    "flipkart": [
        "div._30jeq3._16Jk6d",
        "div._30jeq3",
    ],
    "nykaa": [
        "span.css-1jczs19",
        "span.post-card__content-price-offer",
    ],
}


def parse_price_str(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = re.sub(r"[^\d.]", "", str(raw).replace(",", ""))
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_html(url, timeout=8):
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.text
    except requests.RequestException:
        return None


def extract_from_jsonld(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue

        candidates = data if isinstance(data, list) else [data]
        expanded = []
        for item in candidates:
            if isinstance(item, dict) and "@graph" in item:
                expanded.extend(item["@graph"])
            else:
                expanded.append(item)

        for item in expanded:
            if not isinstance(item, dict) or item.get("@type") != "Product":
                continue
            offers = item.get("offers")
            price = None
            if isinstance(offers, dict):
                price = offers.get("price") or offers.get("lowPrice")
            elif isinstance(offers, list) and offers:
                price = offers[0].get("price") or offers[0].get("lowPrice")

            price = parse_price_str(price)
            if price is None:
                continue

            image = item.get("image")
            if isinstance(image, list) and image:
                image = image[0]
            elif not isinstance(image, str):
                image = None

            return {"name": item.get("name"), "price": price, "image": image}
    return None


def extract_fallback(soup, site):
    price = None
    for sel in SITE_SELECTORS.get(site, []):
        el = soup.select_one(sel)
        if el:
            price = parse_price_str(el.get_text())
            if price:
                break

    if price is None:
        match = re.search(r"₹\s?([\d,]+(?:\.\d+)?)", soup.get_text(" "))
        if match:
            price = parse_price_str(match.group(1))

    if price is None:
        return None

    name = None
    title_tag = soup.find("meta", property="og:title") or soup.find("title")
    if title_tag:
        name = title_tag.get("content") if title_tag.name == "meta" else title_tag.get_text()

    image = None
    img_tag = soup.find("meta", property="og:image")
    if img_tag:
        image = img_tag.get("content")

    return {"name": (name or "").strip() or None, "price": price, "image": image}


def detect_site(url, domains):
    """Match a URL's host against the user's domains.json list."""
    host = urlparse(url).netloc.lower()
    for d in domains:
        if d["domain"].lower() in host:
            return d
    return None


def scrape_url(url, site_key="unknown"):
    """Fetch a product page and return {name, price, image, error}."""
    html = fetch_html(url)
    if html is None:
        return {"name": None, "price": None, "image": None, "error": "fetch_failed"}

    soup = BeautifulSoup(html, "html.parser")
    result = extract_from_jsonld(soup) or extract_fallback(soup, site_key)

    if result is None:
        return {"name": None, "price": None, "image": None, "error": "price_not_found"}

    result["error"] = None
    return result
