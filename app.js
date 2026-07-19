firebase.initializeApp(firebaseConfig);
const auth = firebase.auth();
const db = firebase.firestore();

const loginScreen = document.getElementById("login-screen");
const appScreen = document.getElementById("app-screen");
const loginBtn = document.getElementById("login-btn");
const logoutBtn = document.getElementById("logout-btn");
const notifBanner = document.getElementById("notif-banner");
const searchInput = document.getElementById("search-input");
const searchBtn = document.getElementById("search-btn");
const resultsBlock = document.getElementById("results-block");
const resultsList = document.getElementById("results-list");
const bookmarksList = document.getElementById("bookmarks-list");

let currentUser = null;
let unsubscribeBookmarks = null;
let lastResults = null;
let lastQuery = "";

// ---------- device id (stable per browser install) ----------
function getDeviceId() {
  let id = localStorage.getItem("deviceId");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("deviceId", id);
  }
  return id;
}

function isStandalone() {
  return (
    window.navigator.standalone === true ||
    window.matchMedia("(display-mode: standalone)").matches
  );
}

function isIOS() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent);
}

// ---------- auth ----------
// loginBtn.addEventListener("click", () => {
//   const provider = new firebase.auth.GoogleAuthProvider();
//   auth.signInWithRedirect(provider);
// });

loginBtn.addEventListener("click", async () => {
  const provider = new firebase.auth.GoogleAuthProvider();
  try {
    const result = await auth.signInWithPopup(provider);
    console.log("Signed in:", result.user.email);
  } catch (e) {
    console.error("Sign-in failed:", e.code, e.message);
  }
});

logoutBtn.addEventListener("click", async () => {
  if (currentUser) {
    await db
      .collection("users")
      .doc(currentUser.uid)
      .collection("devices")
      .doc(getDeviceId())
      .set({ isLoggedIn: false }, { merge: true })
      .catch(() => {});
  }
  await auth.signOut();
});

auth.getRedirectResult().catch((e) => console.error("Redirect sign-in failed:", e));

auth.onAuthStateChanged(async (user) => {
  currentUser = user;
  if (user) {
    loginScreen.style.display = "none";
    appScreen.style.display = "block";
    logoutBtn.style.display = "inline-block";

    await db.collection("users").doc(user.uid).set(
      { email: user.email, displayName: user.displayName },
      { merge: true }
    );

    setUpNotificationBanner();
    listenToBookmarks(user.uid);
  } else {
    loginScreen.style.display = "block";
    appScreen.style.display = "none";
    logoutBtn.style.display = "none";
    if (unsubscribeBookmarks) unsubscribeBookmarks();
  }
});

// ---------- notifications ----------
function setUpNotificationBanner() {
  if (!("Notification" in window)) return;

  if (Notification.permission === "granted") {
    registerDeviceToken();
    notifBanner.style.display = "none";
    return;
  }

  if (isIOS() && !isStandalone()) {
    notifBanner.innerHTML =
      "Add this page to your Home Screen (Share → Add to Home Screen), then open it from there to enable price-drop notifications.";
    notifBanner.style.display = "block";
    return;
  }

  notifBanner.innerHTML = "";
  const text = document.createElement("span");
  text.textContent = "Turn on notifications to hear about price drops.";
  const btn = document.createElement("button");
  btn.textContent = "Enable notifications";
  btn.addEventListener("click", requestNotificationPermission);
  notifBanner.appendChild(text);
  notifBanner.appendChild(document.createElement("br"));
  notifBanner.appendChild(btn);
  notifBanner.style.display = "block";
}

async function requestNotificationPermission() {
  const permission = await Notification.requestPermission();
  if (permission === "granted") {
    await registerDeviceToken();
    notifBanner.style.display = "none";
  }
}

async function registerDeviceToken() {
  try {
    const messaging = firebase.messaging();
    const registration = await navigator.serviceWorker.register("/firebase-messaging-sw.js");
    const token = await messaging.getToken({
      vapidKey: VAPID_KEY,
      serviceWorkerRegistration: registration,
    });
    if (!token || !currentUser) return;

    await db
      .collection("users")
      .doc(currentUser.uid)
      .collection("devices")
      .doc(getDeviceId())
      .set(
        {
          fcmToken: token,
          platform: isIOS() ? "ios" : "web",
          isLoggedIn: true,
          lastSeen: firebase.firestore.FieldValue.serverTimestamp(),
        },
        { merge: true }
      );
  } catch (e) {
    console.error("Could not register for notifications:", e);
  }
}

// ---------- search ----------
async function runSearch() {
  const q = searchInput.value.trim();
  if (!q) return;

  lastQuery = q;
  searchBtn.disabled = true;
  searchBtn.innerHTML = '<span class="spinner"></span>';
  resultsBlock.style.display = "block";
  resultsList.innerHTML = '<div class="empty"><span class="spinner"></span> Searching…</div>';

  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    const data = await resp.json();

    if (data.error) throw new Error(data.error);

    lastResults = data.results || [];
    renderResults(lastResults, q, resultsList, true);
  } catch (e) {
    resultsList.innerHTML = `<div class="empty">Search failed: ${escapeHtml(e.message)}</div>`;
  } finally {
    searchBtn.disabled = false;
    searchBtn.textContent = "Search";
  }
}

searchBtn.addEventListener("click", runSearch);
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") runSearch();
});

function renderResults(results, query, container, showSaveButton) {
  if (!results.length) {
    container.innerHTML = '<div class="empty">No matches found across your tracked sites.</div>';
    return;
  }

  const priced = results.filter((r) => r.price != null);
  const cheapest = priced.length
    ? Math.min(...priced.map((r) => r.price))
    : null;

  const card = document.createElement("div");
  card.className = "product-card";

  const head = document.createElement("div");
  head.className = "head";
  head.innerHTML = `
    <div>
      <div class="query">${escapeHtml(query)}</div>
      <div class="meta">${results.length} site${results.length > 1 ? "s" : ""} matched</div>
    </div>
  `;
  if (showSaveButton) {
    const saveBtn = document.createElement("button");
    saveBtn.className = "icon-btn save";
    saveBtn.textContent = "☆ Save";
    saveBtn.addEventListener("click", () => saveBookmark(query, results));
    head.appendChild(saveBtn);
  }
  card.appendChild(head);

  results.forEach((r) => {
    card.appendChild(siteRow(r, cheapest));
  });

  container.innerHTML = "";
  container.appendChild(card);
}

function siteRow(r, cheapest) {
  const row = document.createElement("div");
  row.className = "site-row" + (r.price != null && r.price === cheapest ? " cheapest" : "");

  const link = document.createElement("a");
  link.href = r.url;
  link.target = "_blank";
  link.rel = "noopener";
  link.innerHTML = `<span class="site-name">${escapeHtml(r.label || r.site)}</span>`;
  row.appendChild(link);

  const cell = document.createElement("div");
  cell.className = "price-cell";
  if (r.price != null) {
    if (r.price === cheapest) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "best";
      cell.appendChild(badge);
    }
    const price = document.createElement("span");
    price.className = "price";
    price.textContent = `₹${Math.round(r.price).toLocaleString("en-IN")}`;
    cell.appendChild(price);
  } else {
    const price = document.createElement("span");
    price.className = "price error";
    price.textContent = "unavailable";
    cell.appendChild(price);
  }
  row.appendChild(cell);
  return row;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

// ---------- bookmarks ----------
async function saveBookmark(query, results) {
  if (!currentUser) return;

  const lastPrices = {};
  const links = results.map((r) => {
    if (r.price != null) lastPrices[r.site] = r.price;
    return { site: r.site, label: r.label, url: r.url };
  });

  await db
    .collection("users")
    .doc(currentUser.uid)
    .collection("bookmarks")
    .add({
      query,
      links,
      lastPrices,
      history: [
        {
          t: new Date().toISOString(),
          prices: lastPrices,
        },
      ],
      createdAt: firebase.firestore.FieldValue.serverTimestamp(),
    });
}

function listenToBookmarks(uid) {
  if (unsubscribeBookmarks) unsubscribeBookmarks();

  unsubscribeBookmarks = db
    .collection("users")
    .doc(uid)
    .collection("bookmarks")
    .orderBy("createdAt", "desc")
    .onSnapshot((snap) => {
      if (snap.empty) {
        bookmarksList.innerHTML =
          '<div class="empty">Nothing bookmarked yet — search above and tap Save.</div>';
        return;
      }

      bookmarksList.innerHTML = "";
      snap.forEach((doc) => {
        bookmarksList.appendChild(renderBookmarkCard(doc.id, doc.data()));
      });
    });
}

function renderBookmarkCard(id, data) {
  const results = (data.links || []).map((l) => ({
    site: l.site,
    label: l.label,
    url: l.url,
    price: data.lastPrices ? data.lastPrices[l.site] ?? null : null,
  }));

  const priced = results.filter((r) => r.price != null);
  const cheapest = priced.length ? Math.min(...priced.map((r) => r.price)) : null;

  const card = document.createElement("div");
  card.className = "product-card";

  const head = document.createElement("div");
  head.className = "head";
  const checkedAt = data.lastCheckedAt
    ? new Date(data.lastCheckedAt).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })
    : "not yet checked";
  head.innerHTML = `
    <div>
      <div class="query">${escapeHtml(data.query || "")}</div>
      <div class="meta">Last checked: ${checkedAt}</div>
    </div>
  `;
  const removeBtn = document.createElement("button");
  removeBtn.className = "icon-btn remove";
  removeBtn.textContent = "Remove";
  removeBtn.addEventListener("click", () => {
    db.collection("users").doc(currentUser.uid).collection("bookmarks").doc(id).delete();
  });
  head.appendChild(removeBtn);
  card.appendChild(head);

  results.forEach((r) => card.appendChild(siteRow(r, cheapest)));

  return card;
}
