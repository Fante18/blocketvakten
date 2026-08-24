/* Blocketvakten – client logic (no build step, plain ES). */
"use strict";

const state = {
  searches: [],
  currentSearch: null,
  settings: { push_notify: true },
  lastNotifId: 0,
  initialPoll: true,
  token: localStorage.getItem("blocketvakten_token") || null,
  user: null,
  authMode: "login",
};

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatPrice(price) {
  if (price === null || price === undefined) return "Pris ej angivet";
  return (
    Number(price)
      .toLocaleString("sv-SE")
      .replace(/\u00a0|\u202f/g, " ") + " kr"
  );
}

function timeAgo(iso) {
  if (!iso) return "";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const diff = Math.floor((Date.now() - then.getTime()) / 1000);
  if (diff < 0) return "nyss";
  const mins = Math.floor(diff / 60);
  if (mins < 1) return "nyss";
  if (mins < 60) return `${mins} min sedan`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} tim sedan`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "1 dag sedan";
  if (days < 7) return `${days} dagar sedan`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return weeks === 1 ? "1 vecka sedan" : `${weeks} veckor sedan`;
  return then.toLocaleDateString("sv-SE");
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.hidden = true), 2600);
}

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (state.token) {
    options.headers["Authorization"] = `Bearer ${state.token}`;
  }
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const res = await fetch(path, options);
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    // Session expired – force re-login.
    state.token = null;
    state.user = null;
    localStorage.removeItem("blocketvakten_token");
    showLogin();
    throw new Error("Unauthorized");
  }
  if (!res.ok && res.status !== 400 && res.status !== 401 && res.status !== 404 && res.status !== 409 && res.status !== 500 && res.status !== 502 && res.status !== 503) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

/* ---------------- views ---------------- */

const VIEWS = ["view-home", "view-form", "view-feed", "view-logs", "view-settings", "view-stats", "view-login"];
function show(viewId) {
  if (!state.token && viewId !== "view-login") {
    showLogin();
    return;
  }
  for (const v of VIEWS) $(v).hidden = v !== viewId;
  window.scrollTo(0, 0);
  if (viewId === "view-home") loadHome();
  if (viewId === "view-logs") loadLogs();
  if (viewId === "view-stats") loadOverviewStats();
}

/* ---------------- home ---------------- */

async function loadHome() {
  const searches = await api("GET", "/api/searches");
  state.searches = searches;
  const list = $("search-list");
  $("home-empty").hidden = searches.length > 0;
  $("home-sub").textContent = searches.length
    ? `${searches.length} bevakning${searches.length === 1 ? "" : "ar"} – nya annonser flaggas direkt.`
    : "Håller koll på nya Blocket-annonser åt dig.";
  list.innerHTML = searches.map(searchCard).join("");
}

function searchCard(s) {
  const counts = s.counts || {};
  const chips = [];
  (s.keywords || []).forEach((k) => chips.push(`<span class="chip">${esc(k)}</span>`));
  if (s.max_price) chips.push(`<span class="chip">max ${formatPrice(s.max_price)}</span>`);
  if (s.location) chips.push(`<span class="chip amber">📍 ${esc(s.location)}</span>`);
  (s.exclude_words || []).forEach((w) => chips.push(`<span class="chip red">− ${esc(w)}</span>`));
  if (s.send_email) chips.push('<span class="chip amber">✉ E-post på</span>');
  if (s.send_sms) chips.push('<span class="chip amber">📱 SMS på</span>');
  const freqLabels = { 60: "1 min", 1800: "30 min", 3600: "1 tim", 7200: "2 tim" };
  chips.push(`<span class="chip">⏱ ${freqLabels[s.check_interval] || "30 min"}</span>`);

  const paused = !s.active;
  const status = s.last_error
    ? '<span class="pill error">Fel</span>'
    : paused
      ? '<span class="pill paused">Pausad</span>'
      : '<span class="pill active">Aktiv</span>';

  const newBadge = counts.unseen
    ? `<span class="new-badge">${counts.unseen} nya</span>`
    : "";

  const meta = [];
  if (counts.total) meta.push(`${counts.total} träffar`);
  if (s.avg_price_30d != null)
    meta.push(`snitt 30 d: ${formatPrice(s.avg_price_30d)}`);
  if (s.last_error) meta.push(`⚠️ ${esc(s.last_error)}`);

  return `
  <div class="search-card ${paused ? "paused" : ""}" data-id="${s.id}">
    <div class="search-card-top">
      <h2 class="search-card-title">${esc(s.name || s.keywords.join(" · "))}</h2>
      ${status}
    </div>
    <div class="chips">${chips.join("")}</div>
    <div class="search-card-meta">${newBadge}${meta.map((m) => `<span>${esc(m)}</span>`).join("<span>·</span>")}</div>
    <div class="search-card-actions">
      <button class="btn btn-primary" data-action="feed">Flöde</button>
      <button class="btn btn-ghost" data-action="${paused ? "resume" : "pause"}">${paused ? "Starta" : "Pausa"}</button>
      <button class="btn btn-ghost" data-action="edit">Redigera</button>
      <button class="btn btn-danger" data-action="delete">Ta bort</button>
    </div>
  </div>`;
}

function wireSearchActions() {
  $("search-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const card = btn.closest(".search-card");
    const id = Number(card.dataset.id);
    const search = state.searches.find((s) => s.id === id);
    const action = btn.dataset.action;
    if (action === "feed") return openFeed(id);
    if (action === "edit") return openForm(search);
    if (action === "delete") {
      if (!confirm(`Ta bort bevakningen "${search.name || search.keywords.join(", ")}"?`)) return;
      await api("DELETE", `/api/searches/${id}`);
      toast("Bevakning borttagen");
      return loadHome();
    }
    if (action === "pause" || action === "resume") {
      await api("PUT", `/api/searches/${id}`, { active: action === "resume" });
      toast(action === "resume" ? "Bevakning startad" : "Bevakning pausad");
      return loadHome();
    }
  });
}

/* ---------------- add / edit ---------------- */

function openForm(search) {
  $("form-title").textContent = search ? "Redigera bevakning" : "Lägg till bevakning";
  $("f-name").value = search?.name || "";
  $("f-keywords").value = (search?.keywords || []).join("\n");
  $("f-exclude").value = (search?.exclude_words || []).join(", ");
  $("f-price").value = search?.max_price ?? "";
  $("f-location").value = search?.location || "";
  $("f-active").checked = search ? !!search.active : true;
  $("f-send-email").checked = search ? !!search.send_email : false;
  $("f-send-sms").checked = search ? !!search.send_sms : false;
  $("f-interval").value = search?.check_interval || "1800";
  $("search-form").dataset.id = search?.id || "";
  show("view-form");
}

async function submitForm(e) {
  e.preventDefault();
  const id = $("search-form").dataset.id;
  const payload = {
    name: $("f-name").value.trim(),
    keywords: $("f-keywords").value,
    exclude_words: $("f-exclude").value,
    max_price: $("f-price").value === "" ? null : Number($("f-price").value),
    location: $("f-location").value.trim(),
    active: $("f-active").checked,
    send_email: $("f-send-email").checked,
    send_sms: $("f-send-sms").checked,
    check_interval: Number($("f-interval").value) || 1800,
  };
  if (id) {
    await api("PUT", `/api/searches/${id}`, payload);
    toast("Bevakning uppdaterad");
  } else {
    await api("POST", "/api/searches", payload);
    toast("Bevakning skapad");
    ensureNotificationPermission();
  }
  show("view-home");
}

/* ---------------- feed ---------------- */

async function openFeed(id) {
  const sort = ($("feed-sort") && $("feed-sort").value) || "newest";
  const data = await api("GET", `/api/searches/${id}/listings?sort=${sort}`);
  state.currentSearch = data.search;
  const s = data.search;
  $("feed-title").textContent = s.name || s.keywords.join(" · ");
  const filters = [];
  if (s.max_price) filters.push(`max ${formatPrice(s.max_price)}`);
  if (s.location) filters.push(s.location);
  $("feed-sub").textContent =
    (s.keywords || []).join(" · ") + (filters.length ? "  ·  " + filters.join(" · ") : "");

  const counts = s.counts || {};
  const stats = data.statistics || {};
  $("feed-stats").innerHTML = `
    <div class="stat"><b>${counts.total || 0}</b><span>träffar totalt</span></div>
    <div class="stat"><b>${counts.unseen || 0}</b><span>nya</span></div>
    <div class="stat"><b>${counts.interesting || 0}</b><span>intressanta</span></div>
    <div class="stat good"><b>${stats.avg != null ? formatPrice(stats.avg) : "–"}</b><span>snittpris 30 d</span></div>`;
  renderSearchStats(stats);

  $("listing-list").innerHTML = data.listings.map((l) => listingCard(l)).join("");
  state.listingData = data.listings;
  $("feed-empty").hidden = data.listings.length > 0;
  show("view-feed");
}

function renderSearchStats(stats) {
  const weekly = stats.weekly || [];
  const maxWeek = Math.max(1, ...weekly.map((w) => w.count || 0));
  $("feed-stat-detail").innerHTML = `
    <div class="stat-detail">
      <h2>Prisinsikt & upptäckter</h2>
      <div class="stat-detail-grid">
        <div><span class="stat-detail-value">${stats.min != null ? formatPrice(stats.min) : "–"}</span><span class="stat-detail-label">lägsta 30 d</span></div>
        <div><span class="stat-detail-value">${stats.max != null ? formatPrice(stats.max) : "–"}</span><span class="stat-detail-label">högsta 30 d</span></div>
        <div><span class="stat-detail-value">${stats.total_count || 0}</span><span class="stat-detail-label">hittade totalt</span></div>
      </div>
      <div class="weekly-list">
        ${weekly.map((w) => `<div class="week-row ${w.current ? "current" : ""}">
          <span>${w.current ? "Denna vecka" : new Date(`${w.week_start}T12:00:00Z`).toLocaleDateString("sv-SE", { day: "numeric", month: "short" })}</span>
          <span class="week-bar"><i style="width:${Math.round((w.count / maxWeek) * 100)}%"></i></span>
          <strong>${w.count || 0}</strong>
        </div>`).join("")}
      </div>
    </div>`;
}

function listingCard(l) {
  const img = l.image_url
    ? `<img class="listing-img" src="${esc(l.image_url)}" alt="" loading="lazy"
        onerror="this.onerror=null;this.src='data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2292%22 height=%22118%22><rect fill=%22%23eef0f4%22 width=%22100%22 height=%22118%22/%></svg>'">`
    : `<div class="listing-img" style="background:linear-gradient(135deg,#eef0f4,#dfe3ea)"></div>`;

  const tags = [];
  if (l.good_price) tags.push('<span class="tag good">💚 Bra pris</span>');
  // Deal score tag
  if (l.deal_score != null) {
    const cls = l.deal_score >= 10 ? 'deal-score great' : (l.deal_score > 0 ? 'deal-score' : '');
    if (cls) {
      const prefix = l.deal_score > 0 ? '↓' : (l.deal_score < 0 ? '↑' : '');
      tags.push(`<span class="tag ${cls}">${prefix}${Math.abs(l.deal_score)}% ${l.deal_score > 0 ? 'under' : 'över'} snitt</span>`);
    }
  }
  if (!l.seen) tags.push('<span class="tag new">Ny</span>');
  if (l.interesting) tags.push('<span class="tag interesting">⭐ Intressant</span>');

  // Profit row if resale price is set or if profit data exists
  let profitHTML = '';
  if (l.profit != null) {
    const cls = l.profit > 0 ? 'profit-positive' : 'profit-negative';
    const sign = l.profit > 0 ? '+' : '';
    profitHTML = `<div class="profit-row">
      <span>Återförsäljning: <b>${formatPrice(l.resale_price)}</b></span>
      <span class="${cls}">Vinst: ${sign}${l.profit} kr (${l.profit_pct > 0 ? '+' : ''}${l.profit_pct}%)</span>
      <button class="btn btn-ghost" data-act="edit-resale" style="font-size:11px;padding:2px 5px">Ändra</button>
    </div>`;
  }
  // Resale price input (always rendered, shown only when editing)
  const resaleInputHTML = `<div class="profit-row resale-input" ${l.profit != null ? 'hidden' : ''}>
    <span>Tänkt återförsäljning:</span>
    <input type="number" class="resale-field" placeholder="5000" value="${esc(l.resale_price || '')}" />
    <span>kr</span>
    <button class="btn btn-ghost" data-act="save-resale" style="font-size:11px;padding:2px 5px">Spara</button>
  </div>`;

  return `
  <div class="listing-card ${l.seen ? "seen" : ""}" data-id="${esc(l.ad_id)}">
    ${img}
    <div class="listing-body">
      <a class="listing-title" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || "(utan titel)")}</a>
      <div class="listing-price ${l.good_price ? "good" : ""}">${formatPrice(l.price)}</div>
      <div class="listing-loc">${esc(l.location || "")}${l.location ? " · " : ""}${timeAgo(l.published_at)}</div>
      <div class="listing-tags">${tags.join("")}</div>
      ${profitHTML}
      ${resaleInputHTML}
      <div class="listing-actions">
        <button class="btn btn-ghost" data-act="${l.seen ? "unseen" : "seen"}">${l.seen ? "Osedd" : "Sedd"}</button>
        <button class="btn btn-ghost" data-act="${l.interesting ? "uninterest" : "interest"}">${l.interesting ? "Ej intressant" : "Intressant"}</button>
        <button class="btn btn-ghost" data-act="${l.following ? "unfollow" : "follow"}"${l.following ? " style=\"color:var(--red);font-weight:800\"" : ""}>${l.following ? "🔔 Bevakar pris" : "🔔 Följ pris"}</button>
        <button class="btn btn-ghost" data-act="copy-msg">📋 Meddelande</button>
        <a class="btn btn-primary" href="${esc(l.url)}" target="_blank" rel="noopener">Öppna ↗</a>
      </div>
    </div>
  </div>`;
}

// Sort dropdown handler
  $("feed-sort")?.addEventListener("change", () => {
    if (state.currentSearch) openFeed(state.currentSearch.id);
  });

function wireFeedActions() {
  $("listing-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const card = btn.closest(".listing-card");
    const adId = card.dataset.id;
    const act = btn.dataset.act;

    // Copy quick message
    if (act === "copy-msg") {
      const listing = state.listingData.find(l => l.ad_id === adId);
      if (!listing) return;
      let msg = (state.profile && state.profile.quick_message) || "Hej! Är den kvar?";
      // Substitute variables
      msg = msg.replace(/\{title\}/g, listing.title || '');
      msg = msg.replace(/\{price\}/g, listing.price ? formatPrice(listing.price) : '');
      try {
        await navigator.clipboard.writeText(msg);
        toast('Meddelande kopierat!');
      } catch { toast('Kunde inte kopiera. Prova manuellt.'); }
      return;
    }

    // Save resale price
    if (act === "save-resale") {
      const field = card.querySelector('.resale-field');
      const val = parseInt(field.value, 10);
      if (isNaN(val) || val < 0) { toast('Ange ett giltigt pris.'); return; }
      await api("POST", `/api/searches/${state.currentSearch.id}/listings/${adId}/resale`, { resale_price: val });
      toast('Återförsäljningspris sparat!');
      openFeed(state.currentSearch.id);
      return;
    }

    // Edit resale price (unhide input)
    if (act === "edit-resale") {
      const inputRow = card.querySelector('.resale-input');
      inputRow.hidden = false;
      const profitRow = card.querySelector('.profit-row:not(.resale-input)');
      if (profitRow) profitRow.hidden = true;
      return;
    }

    if (act === "follow" || act === "unfollow") {
      await api("POST", `/api/searches/${state.currentSearch.id}/listings/${adId}/${act}`);
      toast(act === "follow" ? "Prisbevakning på" : "Prisbevakning av");
      openFeed(state.currentSearch.id);
      return;
    }
    const body =
      act === "seen" ? { seen: true }
      : act === "unseen" ? { seen: false }
      : act === "interest" ? { interesting: true }
      : { interesting: false };
    await api("POST", `/api/searches/${state.currentSearch.id}/listings/${adId}`, body);
    openFeed(state.currentSearch.id);
  });

  $("feed-mark-seen").addEventListener("click", async () => {
    await api("POST", `/api/searches/${state.currentSearch.id}/seen`);
    toast("Alla markerade som sedda");
    openFeed(state.currentSearch.id);
  });

  $("feed-edit").addEventListener("click", () => {
    const id = state.currentSearch.id;
    const search = state.searches.find((s) => s.id === id) || state.currentSearch;
    openForm(search);
  });

  $("feed-price-history-btn").addEventListener("click", async () => {
    const ph = $("feed-price-history");
    if (!ph.hidden) { ph.hidden = true; return; }
    await loadPriceHistory(state.currentSearch.id);
    ph.hidden = false;
  });
}

/* ---------------- statistics overview ---------------- */

async function loadOverviewStats() {
  const data = await api("GET", "/api/statistics");
  const top = data.top_search;
  $("overview-stat-cards").innerHTML = `
    <div class="stat good"><b>${data.total_this_week || 0}</b><span>annonser denna vecka</span></div>
    <div class="stat"><b>${top ? esc(top.name) : "–"}</b><span>flest nya denna vecka</span></div>`;
  const rows = data.searches || [];
  $("overview-stat-list").innerHTML = rows.length
    ? `<h2 style="margin:18px 0 10px">Per bevakning</h2>${rows.map((row) => `
      <div class="overview-row">
        <div class="overview-row-main">
          <div class="overview-row-name">${esc(row.name)}</div>
          <div class="overview-row-meta">${row.total_count || 0} hittade totalt · snitt 30 d: ${row.avg_price_30d != null ? formatPrice(row.avg_price_30d) : "–"}</div>
        </div>
        <div class="overview-row-count">${row.this_week || 0} nya</div>
      </div>`).join("")}`
    : '<div class="empty"><h2>Ingen statistik än</h2><p class="muted">Skapa en bevakning och kör en kontroll för att börja samla data.</p></div>';
}

/* ---------------- logs ---------------- */

async function loadLogs() {
  const logs = await api("GET", "/api/logs");
  $("log-list").innerHTML = logs.length
    ? logs
        .map(
          (l) => `
    <div class="log-item ${esc(l.status)}">
      <div class="log-top">
        <span class="log-status">${l.status === "ok" ? "✓ OK" : l.status === "error" ? "⚠ Fel" : "Pausad"}</span>
        <span class="muted">${new Date(l.checked_at).toLocaleString("sv-SE")}</span>
      </div>
      <div class="log-msg">${esc(l.message || "")}${l.fetched_count ? ` · ${l.fetched_count} annonser hämtade` : ""}${l.new_count ? ` · ${l.new_count} nya` : ""}</div>
    </div>`
        )
        .join("")
    : '<div class="empty"><h2>Ingen historik än</h2><p class="muted">Kör en kontroll med "Kolla nu"-knappen.</p></div>';
}

/* ---------------- notifications ---------------- */

function updateBadge(unread) {
  const badge = $("bell-badge");
  badge.hidden = !unread;
  badge.textContent = unread > 99 ? "99+" : String(unread);
}

async function ensureNotificationPermission() {
  if (!("Notification" in window)) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  try {
    return (await Notification.requestPermission()) === "granted";
  } catch {
    return false;
  }
}

function fireBrowserNotification(n) {
  if (state.settings.push_notify === false) return;
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const notif = new Notification(n.title || "Ny annons", {
    body: `${formatPrice(n.price)}${n.location ? " · " + n.location : ""}`,
    tag: `blocket-${n.ad_id}`,
    icon: n.image_url || undefined,
  });
  notif.onclick = () => {
    window.open(n.url, "_blank");
    notif.close();
  };
}

async function pollNotifications() {
  if (!state.token) return;
  try {
    const path =
      "/api/notifications" + (state.lastNotifId ? `?since_id=${state.lastNotifId}` : "");
    const data = await api("GET", path);
    const items = data.notifications || [];
    if (!state.initialPoll) {
      for (const n of items) {
        if (n.id > state.lastNotifId) fireBrowserNotification(n);
      }
    }
    if (items.length) {
      state.lastNotifId = Math.max(state.lastNotifId, ...items.map((n) => n.id));
    }
    state.initialPoll = false;
    updateBadge(data.unread || 0);
    renderNotificationDrawer(data.unread || 0);
  } catch (err) {
    /* network hiccup – retry next tick */
  }
}

async function renderNotificationDrawer(unread) {
  const data = await api("GET", "/api/notifications");
  const list = $("notif-list");
  const items = data.notifications || [];
  list.innerHTML = items.length
    ? items
        .slice(0, 50)
        .map(
          (n) => `
      <a class="notif-item" href="${esc(n.url)}" target="_blank" rel="noopener">
        ${n.image_url ? `<img src="${esc(n.image_url)}" alt="" onerror="this.style.display='none'">` : ""}
        <div>
          <p class="n-title">${esc(n.title || "(utan titel)")}</p>
          <div class="n-sub">${formatPrice(n.price)}</div>
          <div class="n-time">${timeAgo(n.created_at)}</div>
        </div>
      </a>`
        )
        .join("")
    : '<div class="empty"><h2>Inga notiser</h2><p class="muted">Nya annonser dyker upp här.</p></div>';
}

function toggleDrawer(open) {
  $("notif-drawer").hidden = !open;
  $("drawer-scrim").hidden = !open;
}

/* ---------------- settings ---------------- */

async function openSettings() {
  const [settings, profile] = await Promise.all([
    api("GET", "/api/settings"),
    api("GET", "/api/profile"),
  ]);
  state.settings = settings;
  $("set-push").checked = !!settings.push_notify;
  $("profile-email").value = profile.email || "";
  $("profile-phone").value = profile.phone || "";
  if ($("profile-quick-msg")) $("profile-quick-msg").value = profile.quick_message || "";
  $("email-hint").textContent = settings.email_enabled
    ? "SMTP är konfigurerad. E-post skickas för bevakningar där du aktiverat 'Skicka e-post'."
    : "SMTP är inte konfigurerad (sätt BLOCKETVAKTEN_SMTP_* på servern).";
  show("view-settings");
}

async function saveSettings() {
  const email = $("profile-email").value.trim();
  const phone = $("profile-phone").value.trim();
  await api("PUT", "/api/profile", { email, phone });
  const payload = { push_notify: $("set-push").checked };
  state.settings = await api("PUT", "/api/settings", payload);
  toast("Profil och inställningar sparade");
  if (payload.push_notify) ensureNotificationPermission();
}

/* ---------------- auth ---------------- */

function showLogin() {
  $("logout-btn").hidden = true;
  $("check-btn").hidden = true;
  show("view-login");
}

function isLoggedIn() {
  return !!state.token;
}

async function submitAuth(e) {
  e.preventDefault();
  const email = $("auth-email").value.trim();
  const password = $("auth-password").value;
  const errorEl = $("auth-error");
  errorEl.hidden = true;
  const path =
    state.authMode === "register" ? "/api/auth/register" : "/api/auth/login";
  try {
    const data = await api("POST", path, { email, password });
    if (data.token) {
      state.token = data.token;
      state.user = data.user;
      localStorage.setItem("blocketvakten_token", data.token);
      $("auth-email").value = "";
      $("auth-password").value = "";
      $("logout-btn").hidden = false;
      $("check-btn").hidden = false;
      toast(
        state.authMode === "register"
          ? "Konto skapat – välkommen!"
          : "Inloggad"
      );
      show("view-home");
    } else if (data.error) {
      errorEl.textContent = data.error;
      errorEl.hidden = false;
    }
  } catch (err) {
    errorEl.textContent = "Något gick fel. Kontrollera din anslutning.";
    errorEl.hidden = false;
  }
}

function toggleAuthMode() {
  state.authMode = state.authMode === "login" ? "register" : "login";
  $("auth-submit").textContent =
    state.authMode === "login" ? "Logga in" : "Skapa konto";
  $("auth-toggle-mode").textContent =
    state.authMode === "login" ? "Skapa konto" : "Logga in";
  $("auth-error").hidden = true;
}

async function logout() {
  try {
    await api("POST", "/api/auth/logout");
  } catch (_) {}
  state.token = null;
  state.user = null;
  localStorage.removeItem("blocketvakten_token");
  showLogin();
}

/* ---------------- wiring ---------------- */

function init() {
  wireSearchActions();
  wireFeedActions();

  $("add-btn").addEventListener("click", () => openForm(null));
  $("brand-btn").addEventListener("click", () => show("view-home"));
  $("form-cancel").addEventListener("click", () => show("view-home"));
  $("form-back").addEventListener("click", () => show("view-home"));
  $("feed-back").addEventListener("click", () => show("view-home"));
  $("logs-btn").addEventListener("click", () => show("view-logs"));
  $("logs-back").addEventListener("click", () => show("view-home"));
  $("stats-btn").addEventListener("click", () => show("view-stats"));
  $("stats-back").addEventListener("click", () => show("view-home"));
  $("settings-btn").addEventListener("click", () => openSettings());
  $("settings-back").addEventListener("click", () => show("view-home"));
  $("search-form").addEventListener("submit", submitForm);

  $("check-btn").addEventListener("click", checkNow);
  $("bell-btn").addEventListener("click", async () => {
    await ensureNotificationPermission();
    toggleDrawer(true);
    renderNotificationDrawer();
  });
  $("drawer-scrim").addEventListener("click", () => toggleDrawer(false));
  $("notif-read-btn").addEventListener("click", async () => {
    await api("POST", "/api/notifications/read");
    updateBadge(0);
    renderNotificationDrawer(0);
  });
  $("set-save").addEventListener("click", saveSettings);

  // Auth wiring.
  $("auth-form").addEventListener("submit", submitAuth);
  $("auth-toggle-mode").addEventListener("click", toggleAuthMode);
  $("logout-btn").addEventListener("click", logout);

  $("forgot-pw-btn").addEventListener("click", () => {
    $("auth-form").hidden = true;
    $("forgot-pw-btn").hidden = true;
    $("forgot-pw-state").hidden = false;
  });
  $("forgot-back-btn").addEventListener("click", () => {
    $("auth-form").hidden = false;
    $("forgot-pw-btn").hidden = false;
    $("forgot-pw-state").hidden = true;
    $("forgot-msg").hidden = true;
    $("reset-pw-state").hidden = true;
  });
  $("forgot-submit-btn").addEventListener("click", async () => {
    var email = $("forgot-email").value.trim();
    if (!email) {
      $("forgot-msg").textContent = "Ange din e-postadress.";
      $("forgot-msg").hidden = false;
      return;
    }
    $("forgot-submit-btn").disabled = true;
    try {
      var data = await api("POST", "/api/auth/forgot-password", { email: email });
      $("forgot-msg").textContent = data.message || data.error || "";
    $("forgot-msg").hidden = false;
    // If SMTP is not configured, the API returns the reset token directly.
    if (data.token) {
      var resetUrl = location.origin + "/?reset=" + data.token + "&email=" + encodeURIComponent(email);
      $("forgot-token-link").href = resetUrl;
      $("forgot-token-link").textContent = resetUrl;
      $("forgot-token-box").hidden = false;
      } else {
        $("forgot-token-box").hidden = true;
      }
    } catch (err) {
      $("forgot-msg").textContent = "Kunde inte skicka återställningen. Försök igen eller kontrollera SMTP-inställningarna.";
      $("forgot-msg").hidden = false;
    } finally {
      $("forgot-submit-btn").disabled = false;
    }
  });
  $("reset-submit-btn").addEventListener("click", async () => {
    var pw = $("reset-password").value;
    if (pw.length < 4) {
      $("reset-msg").textContent = "Minst 4 tecken.";
      $("reset-msg").hidden = false;
      return;
    }
    var data = await api("POST", "/api/auth/reset-password", {
      token: state._resetToken || "",
      password: pw,
    });
    if (data.ok) {
      toast("Losenord sparat");
      $("reset-pw-state").hidden = true;
      $("reset-password").value = "";
      $("auth-form").hidden = false;
      $("forgot-pw-btn").hidden = false;
    } else {
      $("reset-msg").textContent = data.error || "Fel";
      $("reset-msg").hidden = false;
    }
  });

  // Check for password reset URL parameter.
  var params = new URLSearchParams(window.location.search);
  var resetToken = params.get("reset");
  if (resetToken) {
    state._resetToken = resetToken;
    $("auth-form").hidden = true;
    $("forgot-pw-btn").hidden = true;
    $("forgot-pw-state").hidden = true;
    $("reset-pw-state").hidden = false;
    $("auth-email").value = params.get("email") || "";
    // Clean URL.
    if (window.history.replaceState) {
      window.history.replaceState({}, "", "/");
    }
  }

  if (state.token) {
    // Validate token is still good.
    api("GET", "/api/auth/me")
      .then((data) => {
        if (data.user) {
          state.user = data.user;
          $("logout-btn").hidden = false;
          $("check-btn").hidden = false;
          show("view-home");
        } else {
          showLogin();
        }
      })
      .catch(() => showLogin());
  } else {
    showLogin();
  }

  pollNotifications();
  setInterval(pollNotifications, 15000);
  // Re-sync searches list periodically so new-hit counts stay fresh.
  setInterval(() => {
    if (!$("view-home").hidden) loadHome();
  }, 30000);
}

async function checkNow() {
  const btn = $("check-btn");
  btn.classList.add("spinning");
  try {
    await api("POST", "/api/check");
    toast("Kontroll klar ✓");
    await loadHome();
  } catch (err) {
    toast("Kontrollen misslyckades");
  } finally {
    btn.classList.remove("spinning");
  }
}

/* ---------------- price history graph ---------------- */

async function loadPriceHistory(searchId) {
  const data = await api("GET", `/api/searches/${searchId}/price-history`);
  const graph = $("price-history-graph");
  if (!data.length) {
    graph.innerHTML = '<p class="muted">Ingen prishistorik än – kör en kontroll för att börja samla data.</p>';
    return;
  }
  // Group the last 60 snapshots by date (day-level) and compute daily min/avg.
  const recent = data.slice(0, 200);
  const dayMap = {};
  for (const point of recent) {
    const day = point.recorded_at.slice(0, 10);
    if (!dayMap[day]) dayMap[day] = [];
    if (point.price != null) dayMap[day].push(point.price);
  }
  const days = Object.keys(dayMap).sort();
  const series = days.map((d) => {
    const prices = dayMap[d];
    const avg = Math.round(prices.reduce((a, b) => a + b, 0) / prices.length);
    return { day: d, min: Math.min(...prices), avg, max: Math.max(...prices) };
  });

  if (series.length < 2) {
    graph.innerHTML = `<p class="muted">Behöver data från minst 2 dagar för att visa en graf. Har hittat priser för ${series.length} dag${series.length !== 1 ? "ar" : ""}.</p>`;
    return;
  }

  const allPrices = series.flatMap((s) => [s.min, s.max]);
  const priceMin = Math.min(...allPrices);
  const priceMax = Math.max(...allPrices);
  const range = priceMax - priceMin || 1;
  const width = 100;
  const height = 60;

  const pointsMin = series.map(
    (s, i) => `${(i / (series.length - 1)) * width},${height - ((s.min - priceMin) / range) * height}`
  ).join(" ");
  const pointsAvg = series.map(
    (s, i) => `${(i / (series.length - 1)) * width},${height - ((s.avg - priceMin) / range) * height}`
  ).join(" ");
  const pointsMax = series.map(
    (s, i) => `${(i / (series.length - 1)) * width},${height - ((s.max - priceMin) / range) * height}`
  ).join(" ");

  graph.innerHTML = `
    <svg viewBox="-2 -2 ${width + 4} ${height + 4}" style="width:100%;max-width:100%" preserveAspectRatio="none">
      <polyline points="${esc(pointsMin)}" fill="none" stroke="#8a92a3" stroke-width="0.8" stroke-dasharray="3,3"/>
      <polyline points="${esc(pointsAvg)}" fill="none" stroke="var(--red)" stroke-width="2"/>
      <polyline points="${esc(pointsMax)}" fill="none" stroke="#8a92a3" stroke-width="0.8" stroke-dasharray="3,3"/>
    </svg>
    <div class="graph-labels">
      <span class="muted">${series[0].day.slice(5)}</span>
      <span class="graph-legend">
        <span class="legend-line avg"></span> snitt
        <span class="legend-line min"></span> lägsta
        <span class="legend-line max"></span> högsta
      </span>
      <span class="muted">${series[series.length - 1].day.slice(5)}</span>
    </div>`;
}

document.addEventListener("DOMContentLoaded", init);
