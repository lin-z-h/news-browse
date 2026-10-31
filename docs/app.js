(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const list = $("#list"), chips = $("#chips"), foot = $("#foot"),
        toastEl = $("#toast"), dateLine = $("#date-line");

  const I = {
    orig: '<svg viewBox="0 0 24 24"><path d="M14 5h5v5"/><path d="M19 5l-9 9"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
    arch: '<svg viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M5 6v13a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V6"/><path d="M10 11h4"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>',
    clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    news: '<svg viewBox="0 0 24 24"><path d="M4 5h13a1 1 0 0 1 1 1v12a2 2 0 0 0 2 2H6a2 2 0 0 1-2-2V5z"/><path d="M18 14h2V9h-2"/><path d="M7 9h8M7 13h8M7 17h5"/></svg>',
  };

  let items = [];
  let filter = null;

  const preferred = it => it.archive_url || it.wayback_url || it.url;
  const archiveTarget = it => it.archive_url || it.wayback_url || "https://archive.ph/" + it.url;
  const open = url => window.open(url, "_blank", "noopener");

  function relTime(iso) {
    const t = Date.parse(iso || "");
    if (Number.isNaN(t)) return "";
    const m = Math.max(0, Math.round((Date.now() - t) / 60000));
    if (m < 1) return "now";
    if (m < 60) return m + "m";
    const h = Math.round(m / 60);
    if (h < 24) return h + "h";
    return Math.round(h / 24) + "d";
  }

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

  function card(it) {
    const c = el(
      `<article class="card" tabindex="0">
        <div class="kicker"><span class="src"></span><span class="time"></span></div>
        <h2 class="title"></h2>
        <div class="row">
          <div class="actions">
            <button class="act" data-act="orig" aria-label="Open original">${I.orig}</button>
            <button class="act" data-act="copy" aria-label="Copy link">${I.copy}</button>
            <button class="act ${it.archive_url || it.wayback_url ? "" : "ghost"}" data-act="arch" aria-label="Open archive">${I.arch}</button>
          </div>
        </div>
      </article>`
    );
    c.querySelector(".src").textContent = it.source;
    c.querySelector(".time").textContent = relTime(it.published);
    c.querySelector(".title").textContent = it.title;
    c.addEventListener("click", e => {
      const btn = e.target.closest(".act");
      if (!btn) return open(preferred(it));
      e.stopPropagation();
      if (btn.dataset.act === "orig") open(it.url);
      else if (btn.dataset.act === "arch") open(archiveTarget(it));
      else copyLink(it.url);
    });
    c.addEventListener("keydown", e => { if (e.key === "Enter") open(preferred(it)); });
    return c;
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

  function renderChips(sources) {
    chips.innerHTML = "";
    const mk = (label, value) => {
      const b = el(`<button class="chip"></button>`);
      b.textContent = label;
      b.dataset.v = value ?? "";
      b.addEventListener("click", () => {
        filter = value;
        render();
        [...chips.children].forEach(x => x.classList.toggle("on", x.dataset.v === (value ?? "")));
      });
      return b;
    };
    chips.appendChild(mk("All", null));
    (sources.length ? sources : [...new Set(items.map(i => i.source))]).forEach(s => chips.appendChild(mk(s, s)));
    chips.firstElementChild.classList.add("on");
  }

  function render() {
    list.innerHTML = "";
    const shown = filter ? items.filter(i => i.source === filter) : items;
    if (!shown.length) {
      list.appendChild(el(`<div class="empty">${I.news}</div>`));
      return;
    }
    let lastDay = "";
    for (const it of shown) {
      const day = dayLabel(it.published);
      if (day && day !== lastDay) {
        const d = el(`<div class="day"></div>`);
        d.textContent = day;
        list.appendChild(d);
        lastDay = day;
      }
      list.appendChild(card(it));
    }
  }

  async function load() {
    dateLine.textContent = new Date().toLocaleDateString(undefined, {
      weekday: "long", year: "numeric", month: "long", day: "numeric",
    });
    try {
      const res = await fetch("news.json", { cache: "no-store" });
      const data = await res.json();
      items = (data.items || []).slice()
        .sort((a, b) => (Date.parse(b.published) || 0) - (Date.parse(a.published) || 0));
      renderChips(data.sources || []);
      render();
      if (data.generated_at) {
        foot.innerHTML = I.clock + "<span></span>";
        foot.querySelector("span").textContent = relTime(data.generated_at);
      }
    } catch (e) {
      renderChips([]);
      render();
    }
  }

  load();
})();
