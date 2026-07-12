# Price Tracker — setup guide

A single-page app you log into (iPhone + Windows), search a product, compare its
price across the sites you choose, bookmark it, and get a push notification on
your iPhone when the price drops. Fully free, no servers to keep alive.

```
Browser (you) ──> index.html + app.js  (Vercel static hosting)
                        │
                        ├─ Firebase Auth        (Google sign-in)
                        ├─ Firestore            (bookmarks, price history, device tokens)
                        └─ /api/search           (Vercel Python function)
                                │
                                ├─ Google Custom Search API (finds product pages
                                │   on your domains.json sites)
                                └─ scrapes each page's real price

GitHub Actions (cron, 2x/day) ──> re-scrapes every bookmark ──> Firestore
                                        │
                                        └─ price dropped? → Firebase Cloud Messaging
                                                              → your iPhone
```

Everything below is a one-time setup. Total cost: ₹0, no credit card required
for any of it.

---

## 1. Create a Firebase project

1. Go to [console.firebase.google.com](https://console.firebase.google.com) → **Add project** → name it anything (e.g. `price-tracker`) → skip Google Analytics if asked.
2. **Build → Authentication → Get started → Sign-in method → Google → Enable**.
3. **Build → Firestore Database → Create database** → start in **production mode** → pick a region close to you (e.g. `asia-south1` for India).
4. **Project settings (gear icon) → General → Your apps → Web (`</>`)** → register an app (any nickname) → copy the `firebaseConfig` object shown.
5. Paste those values into `firebase-config.js` in this project (the `firebaseConfig` object).
6. Still in Project settings → **Cloud Messaging** tab → under "Web configuration" → **Generate key pair** → copy the key into `VAPID_KEY` in `firebase-config.js`.
7. Project settings → **Service accounts** → **Generate new private key** → this downloads a JSON file. Keep it safe — you'll paste its *entire contents* into a GitHub secret in step 4. **Never commit this file to the repo.**

## 2. Deploy Firestore security rules

With the [Firebase CLI](https://firebase.google.com/docs/cli) installed:

```bash
npm install -g firebase-tools
firebase login
firebase init firestore   # select your project, keep default file names
firebase deploy --only firestore:rules
```

(This pushes `firestore.rules`, which restricts every user to their own data.)

## 3. Set up Google Custom Search (for the live search feature)

1. Go to [programmablesearchengine.google.com](https://programmablesearchengine.google.com/) → **Add** → turn on **"Search the entire web"** → create it. Note the **Search engine ID** (this is your `cx`).
2. Go to [console.cloud.google.com](https://console.cloud.google.com) → select the same Google account/project → **APIs & Services → Library** → search "Custom Search API" → **Enable**.
3. **APIs & Services → Credentials → Create credentials → API key** → copy it.
   - Free quota: **100 searches/day**. Each search you run in the app uses exactly 1 query, no matter how many sites are in `domains.json` (they're all combined into one request).
4. Edit `api/domains.json` to list exactly the sites you want compared — this is the enumeration file you mentioned wanting to control yourself. Format:
   ```json
   { "site": "amazon", "label": "Amazon", "domain": "amazon.in" }
   ```

## 4. Deploy to Vercel (frontend + search API)

1. Push this project to a GitHub repo.
2. Go to [vercel.com](https://vercel.com) → sign up free (GitHub login) → **Add New → Project** → import your repo → deploy (no config needed, it auto-detects the static files and `/api` function).
3. Once deployed, go to your Vercel project → **Settings → Environment Variables** and add:
   - `GOOGLE_API_KEY` = the API key from step 3.3
   - `GOOGLE_CSE_ID` = the search engine ID (`cx`) from step 3.1
4. Redeploy (Vercel → Deployments → ⋯ → Redeploy) so the function picks up the new env vars.
5. Your app is now live at `https://your-project.vercel.app`.

## 5. Set up the twice-daily price check (GitHub Actions)

1. In your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**.
2. Name: `FIREBASE_SERVICE_ACCOUNT`. Value: paste the **entire contents** of the service account JSON file from step 1.7.
3. That's it — `.github/workflows/check-prices.yml` is already set to run at 9:00 AM and 9:00 PM IST. Edit the two `cron:` lines in that file if you want different times (they're in UTC — IST is UTC+5:30).
4. You can trigger a check manually any time from the repo's **Actions** tab → *Check Bookmarked Prices* → **Run workflow**, to test before waiting for the schedule.

## 6. Install it on your iPhone

1. Open `https://your-project.vercel.app` in **Safari** (must be Safari, not Chrome, for this to work on iOS).
2. Tap **Share → Add to Home Screen**.
3. Open the app from the **home screen icon** (not the Safari tab) — iOS only allows push notifications for web apps launched this way.
4. Sign in, then tap **Enable notifications** and accept the permission prompt.
5. On Windows, just open the same URL in Chrome/Edge and sign in — no install needed, though you can "Install app" from the browser menu if you want it in its own window.

---

## Notes and limitations

- **Scraping is inherently a bit fragile.** Retailers change their page markup over time. The scraper tries structured `JSON-LD` data first (most stable), then falls back to specific CSS selectors for Amazon/Flipkart/Nykaa, then a last-resort price regex. If a site stops working, check what changed in its page source and update the selectors in `api/pricelib.py`.
- **Amazon in particular may block automated requests** (CAPTCHA) more aggressively than other sites. This is inherent to scraping and not fully avoidable for free.
- Notifications only go to devices marked `platform: "ios"` and `isLoggedIn: true` in Firestore — signing out sets that flag off for the current device, so you won't get pushes on a device you've logged out of.
- Google Custom Search free tier is 100 queries/day — each search in the app = 1 query. The twice-daily bookmark re-check does **not** use Custom Search (it re-scrapes the exact URLs you already bookmarked), so it never touches this quota.
