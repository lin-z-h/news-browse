(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const list = $("#list"), metaEl = $("#meta"), foot = $("#foot"),
        toastEl = $("#toast"), dateLine = $("#date-line"), qInput = $("#q"),
        savedBtn = $("#savedToggle"), viewBtn = $("#viewToggle"), themeBtn = $("#themeToggle"),
        sourceTrack = $("#sourceTrack"), topicTrack = $("#topicTrack");

  const I = {
    bookmark: '<svg viewBox="0 0 24 24"><path d="M6 4h12v17l-6-4.2L6 21z"/></svg>',
    orig: '<svg viewBox="0 0 24 24"><path d="M14 5h5v5"/><path d="M19 5l-9 9"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
    arch: '<svg viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M5 6v13a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V6"/><path d="M10 11h4"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>',
    clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    news: '<svg viewBox="0 0 24 24"><path d="M4 5h13a1 1 0 0 1 1 1v12a2 2 0 0 0 2 2H6a2 2 0 0 1-2-2V5z"/><path d="M18 14h2V9h-2"/><path d="M7 9h8M7 13h8M7 17h5"/></svg>',
    search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M16.5 16.5 21 21"/></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    sun: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    moon: '<svg viewBox="0 0 24 24"><path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z"/></svg>',
    auto: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/></svg>',
  };

  const SKEY = "journal.saved.v1", VKEY = "journal.view";
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(SKEY) || "{}") || {}; } catch (e) { saved = {}; }
  const persist = () => { try { localStorage.setItem(SKEY, JSON.stringify(saved)); } catch (e) {} };
  const savedObj = v => (v && typeof v === "object") ? v : { ts: Number(v) || 0 }; // 兼容旧版仅存时间戳
  let view = "cozy";
  try { if (localStorage.getItem(VKEY) === "compact") view = "compact"; } catch (e) {}

  const state = {
    items: [], sources: [], topics: [], generatedAt: null,
    q: "", source: null, topic: null, savedOnly: false,
  };

  // Android WebView（APK 壳）里 window.open 不可靠，直接用当前视图导航
  const isWV = /\bwv\b/.test(navigator.userAgent);
  const open = url => { if (isWV) location.href = url; else window.open(url, "_blank", "noopener"); };
  // 快照 → Wayback → archive.today 人机验证页（newest 直达最新快照，免中间页）
  const archiveTarget = it => it.archive_url || it.wayback_url || "https://archive.ph/newest/" + it.url;

  function relTime(ts) {
    if (!ts) return "";
    const m = Math.max(0, Math.round((Date.now() - ts) / 60000));
    if (m < 1) return "now";
    if (m < 60) return m + "m";
    const h = Math.round(m / 60);
    if (h < 24) return h + "h";
    return Math.round(h / 24) + "d";
  }
  const tsOf = it => { const t = Date.parse(it.published || ""); return Number.isNaN(t) ? 0 : t; };

  function dayLabel(iso) {
    const t = Date.parse(iso || "");
    if (Number.isNaN(t)) return "";
    const d = new Date(t), now = new Date();
    const start = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((start(now) - start(d)) / 864e5);
    if (diff <= 0) return "Today";
    if (diff === 1) return "Yesterday";
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function el(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  // —— 缩略图 / 来源色块占位 ——
  const PALETTE = ["#8f2d2d", "#2f5d8f", "#5a3d8f", "#2f7d64", "#8a6d2f", "#8f2f5d", "#3f4f8f", "#56682f"];
  function hash(s) { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return Math.abs(h); }
  function rgba(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  function monogram(it) {
    const words = (it.source || "?").split(/\s+/).filter(w => w.toLowerCase() !== "the");
    const text = words.map(w => w[0]).join("").slice(0, 3).toUpperCase() || "?";
    const c = PALETTE[hash(it.source || "x") % PALETTE.length];
    const d = document.createElement("div");
    d.className = "thumb mono";
    d.style.backgroundColor = rgba(c, .14);
    d.style.color = c;
    d.textContent = text;
    return d;
  }
  function thumbNode(it) {
    if (!it.image) return monogram(it);
    const img = document.createElement("img");
    img.className = "thumb";
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    img.alt = "";
    img.addEventListener("error", () => img.replaceWith(monogram(it)));
    img.src = it.image;
    return img;
  }

  // —— 卡片 ——
  function card(it) {
    const c = el(
      `<article class="card" role="link" tabindex="0">
        <div class="kicker"><span class="src"></span><span class="topic"></span><span class="time"></span></div>
        <div class="body">
          <h2 class="title"></h2>
          <div class="thumbwrap"></div>
        </div>
        <div class="actions">
          <button class="act save" data-act="save" aria-pressed="false" aria-label="Save for later">${I.bookmark}</button>
          <button class="act" data-act="orig" aria-label="Open original">${I.orig}</button>
          <button class="act" data-act="copy" aria-label="Copy link">${I.copy}</button>
          <button class="act primary" data-act="arch" aria-label="存档版">${I.arch}</button>
        </div>
      </article>`
    );
    c.querySelector(".src").textContent = it.source;
    c.querySelector(".topic").textContent = it.topic && it.topic !== "General" ? it.topic : "";
    const time = c.querySelector(".time");
    const ts = tsOf(it);
    if (ts) { time.dataset.ts = String(ts); time.textContent = relTime(ts); } else { time.remove(); }
    c.querySelector(".title").textContent = it.title;
    c.setAttribute("aria-label", it.title);
    c.querySelector(".thumbwrap").appendChild(thumbNode(it));

    c.addEventListener("click", e => {
      const btn = e.target.closest(".act");
      if (!btn) return open(archiveTarget(it));
      e.stopPropagation();
      const act = btn.dataset.act;
      if (act === "orig") open(it.url);
      else if (act === "arch") open(archiveTarget(it));
      else if (act === "save") toggleSave(it, btn);
      else copyLink(it.url);
    });
    c.addEventListener("keydown", e => { if (e.key === "Enter") open(archiveTarget(it)); });
    return c;
  }

  function toggleSave(it, btn) {
    if (saved[it.id]) {
      delete saved[it.id];
      btn.setAttribute("aria-pressed", "false");
    } else {
      saved[it.id] = {
        ts: Date.now(),
        title: it.title, source: it.source, topic: it.topic || "General",
        published: it.published || null, url: it.url,
        image: it.image || null,
        archive_url: it.archive_url || null, wayback_url: it.wayback_url || null,
      };
      btn.setAttribute("aria-pressed", "true");
    }
    persist();
    showToast(saved[it.id] ? I.check : I.bookmark);
    if (state.savedOnly && !saved[it.id]) render();
  }

  // 收藏条目与最新 news.json 同步：后台探测到更好的快照（archive/wayback）时自动更新本地记录
  function syncSavedFromItems() {
    const byId = new Map(state.items.map(i => [i.id, i]));
    let dirty = false;
    for (const [id, v] of Object.entries(saved)) {
      const fresh = byId.get(id);
      if (!fresh) continue;
      const obj = savedObj(v);
      let changed = false;
      for (const k of ["title", "source", "topic", "published", "url", "image"]) {
        if (fresh[k] != null && fresh[k] !== "" && obj[k] !== fresh[k]) { obj[k] = fresh[k]; changed = true; }
      }
      if (fresh.archive_url && obj.archive_url !== fresh.archive_url) { obj.archive_url = fresh.archive_url; changed = true; }
      if (fresh.wayback_url && obj.wayback_url !== fresh.wayback_url) { obj.wayback_url = fresh.wayback_url; changed = true; }
      if (changed) { saved[id] = obj; dirty = true; }
    }
    if (dirty) persist();
  }

  // 稍后读视图完全从本地渲染：文章掉出 news.json 后收藏依然可看
  function savedItems() {
    const byId = new Map(state.items.map(i => [i.id, i]));
    return Object.entries(saved)
      .map(([id, v]) => {
        const obj = savedObj(v);
        const fresh = byId.get(id);
        return {
          id,
          title: (fresh && fresh.title) || obj.title || "Saved story",
          source: (fresh && fresh.source) || obj.source || "",
          topic: (fresh && fresh.topic) || obj.topic || "General",
          published: (fresh && fresh.published) || obj.published || null,
          image: (fresh && fresh.image) || obj.image || null,
          url: (fresh && fresh.url) || obj.url || "",
          archive_url: (fresh && fresh.archive_url) || obj.archive_url || null,
          wayback_url: (fresh && fresh.wayback_url) || obj.wayback_url || null,
          _savedTs: obj.ts || 0,
        };
      })
      .sort((a, b) => b._savedTs - a._savedTs);
  }

  async function copyLink(url) {
    let ok = true;
    try {
      await navigator.clipboard.writeText(url);
    } catch (e) {
      try {
        const ta = document.createElement("textarea");
        ta.value = url;
        ta.style.cssText = "position:fixed;opacity:0";
        document.body.appendChild(ta);
        ta.select();
        ok = document.execCommand("copy");
        ta.remove();
      } catch (err) { ok = false; }
    }
    showToast(ok ? I.check : I.copy);
  }

  let toastTimer;
  function showToast(icon) {
    toastEl.innerHTML = icon;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 1100);
  }

  // —— 筛选 chips（来源 / 话题 两行，循环滚动）——
  function buildTrack(trackEl, entries) {
    trackEl.innerHTML = "";
    const kind = trackEl.dataset.kind;
    const make = (label, value) => {
      const b = el(`<button class="chip ${kind}"></button>`);
      b.textContent = label;
      b.dataset.kind = kind;
      b.dataset.v = value ?? "";
      b.setAttribute("aria-pressed", "false");
      return b;
    };
    const chips = entries.map(e => make(e.label, e.value));
    chips.forEach(c => trackEl.appendChild(c));
    chips.forEach(c => trackEl.appendChild(c.cloneNode(true))); // 复制一份实现无缝循环
    trackEl.style.setProperty("--speed", Math.max(30, Math.round(trackEl.scrollWidth / 42)) + "s");
    trackEl.addEventListener("click", e => {
      const b = e.target.closest(".chip");
      if (!b) return;
      const value = b.dataset.v === "" ? null : b.dataset.v;
      if (kind === "source") state.source = state.source === value ? null : value;
      else state.topic = state.topic === value ? null : value;
      render();
    });
    // 触屏没有 hover：按下时暂停滚动几秒，方便点选
    trackEl.addEventListener("pointerdown", () => {
      trackEl.classList.add("hold");
      clearTimeout(trackEl._holdT);
      trackEl._holdT = setTimeout(() => trackEl.classList.remove("hold"), 4000);
    });
  }

  function syncChips() {
    document.querySelectorAll(".track").forEach(trackEl => {
      const kind = trackEl.dataset.kind;
      const cur = (kind === "source" ? state.source : state.topic) ?? "";
      trackEl.querySelectorAll(".chip").forEach(b => {
        b.setAttribute("aria-pressed", cur === b.dataset.v ? "true" : "false");
      });
      // 选中某项时该行暂停滚动，便于看清当前筛选
      trackEl.classList.toggle("paused", !!cur);
    });
  }

  // —— 列表 ——
  function visible() {
    const q = state.q.trim().toLowerCase();
    const match = it =>
      (!state.source || it.source === state.source) &&
      (!state.topic || (it.topic || "General") === state.topic) &&
      (!q || `${it.title} ${it.source} ${it.topic || ""}`.toLowerCase().includes(q));
    if (state.savedOnly) return savedItems().filter(match);
    return state.items.filter(match);
  }

  function clearFilters() {
    state.q = ""; state.source = null; state.topic = null; state.savedOnly = false;
    qInput.value = "";
    savedBtn.setAttribute("aria-pressed", "false");
    render();
  }

  function render() {
    syncChips();
    const shown = visible();
    const active = state.q.trim() || state.source || state.topic || state.savedOnly;

    metaEl.innerHTML = "";
    const count = el('<span class="count"></span>');
    count.textContent = `${shown.length} ${shown.length === 1 ? "story" : "stories"}`;
    metaEl.appendChild(count);
    const picked = [state.source, state.topic].filter(Boolean);
    if (picked.length) {
      const f = el('<span class="filters"></span>');
      f.textContent = picked.join(" · ");
      metaEl.appendChild(f);
    }
    if (active) {
      const clear = el(`<button class="clear" aria-label="Clear filters">${I.x}</button>`);
      clear.addEventListener("click", clearFilters);
      metaEl.appendChild(clear);
    }

    list.innerHTML = "";
    if (!state.items.length) {
      list.appendChild(el(`<div class="empty"><div class="eicon">${I.news}</div></div>`));
      return;
    }
    if (!shown.length) {
      const d = el(
        `<div class="empty">
          <div class="eicon">${state.savedOnly ? I.bookmark : I.search}</div>
          <p>Nothing here.</p>
          <button class="ghostbtn">Clear filters</button>
        </div>`
      );
      d.querySelector(".ghostbtn").addEventListener("click", clearFilters);
      list.appendChild(d);
      return;
    }
    let lastDay = "";
    for (const it of shown) {
      const day = state.savedOnly ? "" : dayLabel(it.published); // 稍后读按收藏时间排序，不按日期分组
      if (day && day !== lastDay) {
        const d = el('<div class="day"></div>');
        d.textContent = day;
        list.appendChild(d);
        lastDay = day;
      }
      list.appendChild(card(it));
    }
  }

  function applyView() {
    document.body.classList.toggle("compact", view === "compact");
    viewBtn.setAttribute("aria-pressed", view === "compact" ? "true" : "false");
  }

  function updateFoot() {
    if (!state.generatedAt) return;
    const span = foot.querySelector("span");
    if (span) span.textContent = relTime(Date.parse(state.generatedAt));
  }
  function tick() {
    document.querySelectorAll("[data-ts]").forEach(n => { n.textContent = relTime(Number(n.dataset.ts)); });
    updateFoot();
  }
  setInterval(tick, 60000);

  // —— 事件 ——
  let deb;
  qInput.addEventListener("input", () => {
    clearTimeout(deb);
    deb = setTimeout(() => { state.q = qInput.value; render(); }, 140);
  });
  qInput.addEventListener("keydown", e => {
    if (e.key === "Escape") { qInput.value = ""; state.q = ""; render(); qInput.blur(); }
  });
  savedBtn.addEventListener("click", () => {
    state.savedOnly = !state.savedOnly;
    savedBtn.setAttribute("aria-pressed", String(state.savedOnly));
    render();
  });
  viewBtn.addEventListener("click", () => {
    view = view === "compact" ? "cozy" : "compact";
    try { localStorage.setItem(VKEY, view); } catch (e) {}
    applyView();
  });

  // —— 启动 ——
  async function load() {
    dateLine.textContent = new Date().toLocaleDateString(undefined, {
      weekday: "long", year: "numeric", month: "long", day: "numeric",
    });
    applyView();
    try {
      const res = await fetch("news.json", { cache: "no-store" });
      const data = await res.json();
      state.items = (data.items || []).slice().sort((a, b) => tsOf(b) - tsOf(a));
      state.sources = data.sources && data.sources.length
        ? data.sources
        : [...new Set(state.items.map(i => i.source))];
      const seen = new Map();
      state.items.forEach(it => {
        const t = it.topic || "General";
        seen.set(t, (seen.get(t) || 0) + 1);
      });
      state.topics = [...seen.entries()]
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
        .map(e => e[0]);
      state.generatedAt = data.generated_at || null;
    } catch (e) { /* 数据缺失时显示空态图标 */ }
    syncSavedFromItems();
    buildTrack(sourceTrack, [{ label: "All", value: null }, ...state.sources.map(s => ({ label: s, value: s }))]);
    buildTrack(topicTrack, [{ label: "All", value: null }, ...state.topics.map(t => ({ label: t, value: t }))]);
    render();
    if (state.generatedAt) {
      foot.innerHTML = I.clock + "<span></span>";
      updateFoot();
    }
  }

  // —— 主题：system / light / dark 循环 ——
  const TKEY = "journal.theme";
  const THEMES = ["system", "light", "dark"];
  let theme = "system";
  try {
    const t = localStorage.getItem(TKEY);
    if (THEMES.includes(t)) theme = t;
  } catch (e) {}
  function applyTheme() {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.dataset.theme = theme;
    themeBtn.innerHTML = theme === "light" ? I.sun : theme === "dark" ? I.moon : I.auto;
    themeBtn.setAttribute("aria-label", "Theme: " + theme);
    themeBtn.setAttribute("aria-pressed", theme === "system" ? "false" : "true");
    try { localStorage.setItem(TKEY, theme); } catch (e) {}
  }
  themeBtn.addEventListener("click", () => {
    theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    applyTheme();
  });
  applyTheme();

  load();
})();
