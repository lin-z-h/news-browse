(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const list = $("#list"), metaEl = $("#meta"), foot = $("#foot"),
        toastEl = $("#toast"), dateLine = $("#date-line"), qInput = $("#q"),
        savedBtn = $("#savedToggle"), viewBtn = $("#viewToggle"), themeBtn = $("#themeToggle"),
        sourceTrack = $("#sourceTrack"), topicTrack = $("#topicTrack"),
        readerEl = $("#reader"), rTitle = $("#rTitle"), rMeta = $("#rMeta"), rBody = $("#rBody");
  let readerCtrl = null;

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
      if (!btn) return openReader(it);
      e.stopPropagation();
      const act = btn.dataset.act;
      if (act === "orig") open(it.url);
      else if (act === "arch") open(archiveTarget(it));
      else if (act === "save") toggleSave(it, btn);
      else copyLink(it.url);
    });
    c.addEventListener("keydown", e => { if (e.key === "Enter") openReader(it); });
    return c;
  }

  // —— 阅读模式：无快照时经开源 Jina Reader 按需取正文（一次一篇，本地缓存，不存公开仓库）——
  const RKEY = "journal.read";
  let readCache = {};
  try { readCache = JSON.parse(localStorage.getItem(RKEY) || "{}") || {}; } catch (e) { readCache = {}; }
  // Jina Reader key：仅存本地（localStorage("journal.jinakey")，双击报头可设置），不进仓库。
  // 作用：提升配额并尝试解锁 wayback 域（匿名被 jina 封禁）。
  const JINA_KEY = (() => {
    try { return localStorage.getItem("journal.jinakey") || ""; }
    catch (e) { return ""; }
  })();
  const CACHE_TOKEN = (() => {
    try { return localStorage.getItem("journal.cachetoken") || ""; }
    catch (e) { return ""; }
  })();
  const CACHE_REPO = "lin-z-h/news-cache";
  // 正文链失败记忆（url → 失败时间）：24h 内不再慢试全链，直接给人工兜底入口
  const FAILKEY = "journal.readfail";
  let failMem = {};
  try { failMem = JSON.parse(localStorage.getItem(FAILKEY) || "{}") || {}; } catch (e) { failMem = {}; }
  for (const k of Object.keys(failMem)) {
    if (Date.now() - (failMem[k] || 0) > 7 * 864e5) delete failMem[k];
  }
  const persistFails = () => { try { localStorage.setItem(FAILKEY, JSON.stringify(failMem)); } catch (e) {} };
  // 人工捕获正文缓存（id → {ts, title, paras}）：APK 提取并清洗过的内容，30 天有效，再次打开秒出
  const CAPKEY = "journal.capture";
  let capCache = {};
  try { capCache = JSON.parse(localStorage.getItem(CAPKEY) || "{}") || {}; } catch (e) { capCache = {}; }
  for (const k of Object.keys(capCache)) {
    if (Date.now() - ((capCache[k] || {}).ts || 0) > 30 * 864e5) delete capCache[k];
  }
  const persistCaps = () => { try { localStorage.setItem(CAPKEY, JSON.stringify(capCache)); } catch (e) {} };
  // 各站点正文容器选择器（jina x-target-selector，服务器端有效、浏览器 CORS 下会被忽略）
  const READER_SELECTORS = { "news.tvb.com": ".module_left" };
  // 各站点噪音块过滤（浏览器端兜底）
  const READER_DROP = {
    "news.tvb.com": [/^\d+\.\s[\s\S]*\d+\.\s/, /^publish[:：]/i, /^by[:：]/i],
  };
  // 错误页/验证页特征：jina 会把 403 页面当"内容"带回来，必须识别拒绝
  const GARBAGE_RE = /are you a robot|access denied|just a moment|unusual traffic|verify (?:you are )?human|403 forbidden|captcha|enable javascript and cookies|are you human|error code|request id|we apologise for any inconvenience|for help please visit|security check to access/i;
  // 付费墙/登录/同意墙：整页命中时代表“没有正文”，绝不能当文章渲染
  const PAYWALL_MARKERS = [
    "continue with apple", "continue with google", "sign in with google",
    "do not sell or share my personal information", "unlock this article",
    "unlock election coverage", "unlock the global benchmark", "already a subscriber",
    "subscribe to continue", "create a free account", "get unlimited access",
    "sign in to continue", "by continuing, i agree to the", "claim this offer",
    "we've updated our terms", "two ways to read", "already have an account",
  ];
  // archive.today 的 newest/index 页：跨来源标题列表，常被误当正文（快照页本身不算）
  const ARCHIVE_INDEX_RE = /no results|create a snapshot|this page is not available|submit url/i;
  const INDEX_PUBLICATIONS = [
    "the new york times", "financial times", "the washington post", "lianhe zaobao",
    "reuters", "bloomberg", "the guardian", "the economist", "wall street journal",
    "south china morning post", "time", "associated press",
  ];
  // 全局噪音块（任何站点、任何位置出现即丢弃）
  const READER_NOISE_RE = /^enter your email|after newsletter promotion|^free newsletter|^sign up to |^gift this article|^add us on google|^terms and conditions|^copyright \d{4}|^thanks for sharing|^download now|^most viewed|^related storie|^advertisement|^contact us|^confidential tip|^site feedback|^back to top|^share this|^listen to this|^before it'?s here|^learn more|^bloomberg terminal|^archive\.today|^saved from|^all snapshots|^webpage capture|^archived \d|^short link$|^long link$|^markdown$|^html code$|^wiki code$/i;

  function _host(u) { try { return new URL(u).host; } catch (e) { return ""; } }

  function looksLikePaywall(text) {
    const low = String(text || "").toLowerCase();
    let hits = 0;
    for (const m of PAYWALL_MARKERS) if (low.includes(m)) hits++;
    return hits >= 2 || (low.includes("do not sell or share my personal information") && text.includes("$1"));
  }

  function looksLikeArchiveIndex(texts) {
    const joined = texts.join(" ");
    if (ARCHIVE_INDEX_RE.test(joined)) return true;
    const low = joined.toLowerCase();
    const names = INDEX_PUBLICATIONS.filter(n => low.includes(n)).length;
    if (names >= 4) {
      const sentences = (joined.match(/[.!?。！？](?:\s|$)/g) || []).length;
      if (sentences < Math.max(4, Math.floor(texts.length / 3))) return true;
    }
    return false;
  }

  // 与 CI 侧 quality_ok 同口径：英文 ≥3 段/400 字符；中文短讯允许 2 段/150 字
  function readerQualityOk(kept) {
    const texts = kept.filter(x => x.text).map(x => x.text);
    const joined = texts.join("");
    const cjk = (joined.match(/[\u4e00-\u9fff]/g) || []).length;
    const paras = texts.filter(t => t.length >= 30 || cjk > 100);
    const minParas = cjk >= 120 ? 2 : 3;
    if (paras.length < minParas) return false;
    if (joined.length < 150) return false;
    if (joined.length < 400 && cjk < 120) return false;
    const sentenceLike = paras.filter(t => /[.!?。！？]/.test(t)).length;
    if (sentenceLike < Math.max(2, Math.floor(paras.length * 0.6))) return false;
    if (looksLikePaywall(texts.join(" ")) && joined.length < 2000) return false;
    if (looksLikeArchiveIndex(texts)) return false;
    return true;
  }

  // 清洗 jina markdown → 文本块/图片块。返回 null = 垃圾（错误页/太短），上层换下一个来源。
  // 结束边界标记：命中即认为正文结束（募捐区/推荐区/页脚）
  const READER_END_RE = /^#{0,4}\s*(most viewed|most read|related storie|more from|top reads|explore more on these topics|topics in this article|reuse this content|advertisement|year-end fundraiser|at this unsettling time|now live|please contribute|was this helpful|sign up for our email|fund independent|what readers are saying|do not sell or share|get unlimited access|we've updated our terms|claim this offer|©\s*20\d\d|already have an account|two ways to read|continue with (?:apple|google)|sign in|subscribe|home\b|terms of service)/i;

  // 清洗 jina markdown → 文本块/图片块。返回 null = 垃圾（错误页/太短），上层换下一个来源。
  // title 用于剔除与卡片标题重复的正文标题，避免双标题。
  function denoiseReader(md, host, title) {
    let body = md;
    // "URL Source:" 是 jina 实际抓取的最终页面，作为相对图片地址的解析基准
    let base = "";
    const bm = body.match(/URL Source: (\S+)/);
    if (bm) { try { base = new URL(bm[1]).origin; } catch (e) {} }
    const cut = body.indexOf("Markdown Content:");
    if (cut >= 0) body = body.slice(cut + "Markdown Content:".length);
    if (GARBAGE_RE.test(body.slice(0, 3000))) return null;
    const esc = s => s.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
    const absImg = u => {
      if (!u) return null;
      if (/^blob:/i.test(u)) return null;
      if (/^https?:\/\//i.test(u)) return u;
      if (u.startsWith("//")) return "https:" + u;
      if (u.startsWith("/") && base) return base + u;
      return null;
    };
    const norm = s => String(s).replace(/[\s·•—–'"",.。！？!?:：()（）「」『』|#[\]]/g, "").toLowerCase();
    const raw = body.split(/\n{2,}/).map(s => s.trim()).filter(Boolean);
    const clean = [];
    for (let b of raw) {
      const im = b.match(/^!\[[^\]]*\]\(([^)\s]+)/); // 独立图片块：http/相对真图保留，blob: 死链剔除
      if (im) {
        const u = absImg(im[1]);
        if (u) { clean.push({ img: u }); continue; }
      }
      const head = /^#{1,3}\s/.test(b);
      b = b.replace(/^#{1,4}\s+/gm, "")
           .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
           .replace(/\[[^\]]*\]\([^)]*\)/g, "$1")
           .replace(/\*\*([^*]+)\*\*/g, "$1")
           .replace(/^[-*]\s+/gm, "· ")
           .trim();
      if (b) clean.push({ text: b, head });
    }
    const isSentence = b => /[。！？]/.test(b) || (/[.!?]/.test(b) && b.length > 90);
    const isNoiseHead = b => /^(support|sign in|sign|share|search|menu|close|fund independent|most viewed|related|live)/i.test(b);
    let dropRes = null;
    try { dropRes = READER_DROP[host]; } catch (e) {}
    const tnorm = norm(title);
    // 起点：首个像文章标题的标题块（较长且非站点噪音），否则首个句子块
    let startIdx = clean.findIndex(x => x.head && x.text.length >= 25 && !isNoiseHead(x.text));
    if (startIdx < 0) startIdx = clean.findIndex(x => x.text && isSentence(x.text) && x.text.length >= 20);
    let endIdx = -1;
    if (startIdx >= 0) {
      endIdx = clean.findIndex((x, i) => i > startIdx && (
        (dropRes && dropRes.some(rx => rx.test(x.text))) ||
        (x.text && READER_END_RE.test(x.text))));
    }
    // 终裁：切出正文区间 → 去重（含与卡片标题重复者）→ 丢弃短噪声块
    const seen = new Set([tnorm]);
    const lo = startIdx >= 0 ? startIdx : 0;
    const hi = endIdx > startIdx ? endIdx : clean.length;
    const kept = [];
    for (const x of clean.slice(lo, hi)) {
      if (x.img) { kept.push(x); continue; }
      if (x.text.length < 14) continue;
      if (READER_NOISE_RE.test(x.text)) continue;
      if (/cookie/i.test(x.text)) continue; // cookie 同意墙
      if (/^[·\s—–-]+$/.test(x.text)) continue; // 纯分隔符
      if (/^what readers are saying/i.test(x.text)) continue; // 评论摘要
      const k = norm(x.text);
      if (!k || seen.has(k)) continue;
      seen.add(k);
      kept.push(x);
    }
    while (kept.length && kept[0].text && kept[0].text.length < 40) kept.shift(); // 开头家具修剪
    if (!readerQualityOk(kept)) return null;
    return { blocks: kept, esc };
  }

  function renderReaderBody(den) {
    const esc = den.esc;
    const out = [];
    for (const b of den.blocks) {
      if (b.img) { out.push(`<img class="rimg" loading="lazy" referrerpolicy="no-referrer" src="${esc(b.img)}" alt="">`); continue; }
      let t = b.text, tag = "p";
      if (b.head) tag = "h2";
      const h = t.match(/^(#{1,4})\s+(.*)$/s);
      if (h) { t = h[2]; tag = "h" + Math.min(4, h[1].length + 1); }
      if (t) out.push(`<${tag}>${esc(t)}</${tag}>`);
    }
    rBody.innerHTML = out.join("");
  }

  const escText = s => s.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  // 带总超时的 fetch→text：头和体共用一个时限，超时/失败返回 {ok:false}，外层 ctrl（用户关闭阅读器）仍可整体中止
  async function fetchTextTimeout(url, opts, ms, outer) {
    const ac = new AbortController();
    const relay = () => ac.abort();
    if (outer) {
      if (outer.aborted) ac.abort();
      else outer.addEventListener("abort", relay, { once: true });
    }
    const t = setTimeout(() => ac.abort(), ms);
    try {
      const res = await fetch(url, Object.assign({}, opts, { signal: ac.signal }));
      if (!res.ok) return { ok: false, status: res.status };
      const text = await res.text();
      return { ok: true, text };
    } finally {
      clearTimeout(t);
      if (outer) outer.removeEventListener("abort", relay);
    }
  }

  function renderPlainParas(paras) {
    rBody.innerHTML = paras.map(t => `<p>${escText(t)}</p>`).join("");
  }

  // Wayback 快照直接抓取的解析：jina 永久封禁 web.archive.org 域，经 jina 必失败，
  // 而 wayback 页面本身支持 CORS，直接 fetch + DOM 提取正文（剔除 wayback 工具条与站点家具）
  function denoiseWaybackDoc(html, title) {
    let doc;
    try { doc = new DOMParser().parseFromString(html, "text/html"); } catch (e) { return null; }
    const bodyText = doc.body ? doc.body.textContent : "";
    if (GARBAGE_RE.test(bodyText.slice(0, 3000))) return null;
    doc.querySelectorAll("script,style,noscript,iframe,svg,form,nav,header,footer,aside,#wm-ipp-base,#wm-ipp,[id^='wm-ipp']")
      .forEach(e => e.remove());
    const root = doc.querySelector("article") || doc.querySelector("main")
      || doc.querySelector("[role='main']") || doc.body;
    if (!root) return null;
    const tnorm = String(title || "").replace(/[\s·—–,.。！？!?]/g, "").toLowerCase();
    const seen = new Set([tnorm]);
    const blocks = [];
    for (const el of root.querySelectorAll("p,h1,h2,h3,li,blockquote,figcaption")) {
      const t = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (t.length < 14 || READER_NOISE_RE.test(t) || /cookie/i.test(t)) continue;
      const k = t.replace(/[\s·—–,.。！？!?]/g, "").toLowerCase();
      if (!k || seen.has(k)) continue;
      seen.add(k);
      blocks.push({ text: t, head: /^h[1-3]$/i.test(el.tagName) });
    }
    while (blocks.length && blocks[0].text.length < 40) blocks.shift(); // 开头家具修剪
    if (!readerQualityOk(blocks)) return null;
    return { blocks, esc: escText };
  }

  // APK 捕获文本的垃圾特征：人机验证页 / archive 工具条 / “没有快照”帮助页。原生已做过第一道把关，这里兜底
  const CAP_GARBAGE_RE = /completing the captcha|proves you are (?:a )?human|are you a robot|verify(?:ing)? (?:that )?you are (?:a )?human|security check to access|just a moment|enable javascript and cookies/i;
  const CAP_HELP_RE = /搜索所有来自此域名|根据精确网址查询存档|查询以此网址开头的网页|You may want to|archive this url|立即存档|存档此网址|No results|not archived/i;
  const CAP_DROP_RE = /^(https?:\/\/|www\.)\S+$|^[\w.-]+\.(?:com|net|org|ph|is|li|vn|md|fo|tc|today)(?:\/\S*)?$|^archive\.(?:ph|today|is|li|vn|md|fo|tc)\b|^webpage capture|^saved from|^all \d+|^my blogs|^telegram$|^vk$|^(search|share|report|close|menu|donate|subscribe|newsletter|advertising|cookies?)\b/i;

  // 清洗 APK 提取的纯文本：去 URL 行/工具条行/重复行/与标题重复的行。
  // 达不到正文量（≥3 段且 ≥120 字，中文短讯也覆盖）返回空 = 拒绝渲染
  function cleanCaptureText(text, title) {
    if (CAP_GARBAGE_RE.test(text) || CAP_HELP_RE.test(text) || looksLikePaywall(text)) return [];
    const tnorm = String(title || "").replace(/[\s·—–,.。！？!?]/g, "").toLowerCase();
    const seen = new Set([tnorm]);
    const out = [];
    for (let line of String(text).split(/\n+/)) {
      line = line.trim();
      if (line.length < 14 || CAP_DROP_RE.test(line)) continue;
      const k = line.replace(/[\s·—–,.。！？!?]/g, "").toLowerCase();
      if (!k || seen.has(k)) continue;
      seen.add(k);
      out.push(line);
    }
    if (looksLikeArchiveIndex(out)) return [];
    const joined = out.join("");
    const cjk = (joined.match(/[\u4e00-\u9fff]/g) || []).length;
    return joined.length >= 150 && out.length >= (cjk >= 120 ? 2 : 3) ? out : [];
  }

  // 链路重复失败时的阅读器内错误态：不再自动跳转，把选择权交给用户
  function renderReaderError(it) {
    const wrap = document.createElement("div");
    wrap.className = "rerr";
    const p = document.createElement("p");
    p.textContent = "暂时无法获取正文：没有可用快照，或来源拒绝抓取。可以去归档页人工过验证后提取，也可以直接打开原文。";
    const row = document.createElement("div");
    row.className = "rerrbtns";
    const mk = (label, primary, fn) => {
      const b = el(`<button class="rbtn${primary ? " primary" : ""}"></button>`);
      b.textContent = label;
      b.addEventListener("click", fn);
      return b;
    };
    row.appendChild(mk("去归档页提取", true, () => {
      if (window.Android && Android.captureFor) { try { Android.captureFor(it.id, it.url, it.title || ""); } catch (e) {} }
      else open(archiveTarget(it));
    }));
    row.appendChild(mk("重试", false, () => {
      delete failMem[it.url];
      persistFails();
      openReader(it, true);
    }));
    row.appendChild(mk("打开原文", false, () => open(it.url)));
    wrap.appendChild(p);
    wrap.appendChild(row);
    rBody.innerHTML = "";
    rBody.appendChild(wrap);
  }

  // 封面模式：付费墙源且没有快照/正文缓存时只渲染封面，等用户点“开始阅读”。
  // 有快照 → 直接走正文链；无快照 → APK 进人工验证捕获流程，浏览器打开归档页。
  function renderCover(it) {
    const wrap = el(`<div class="rcover"></div>`);
    if (it.image) {
      const img = document.createElement("img");
      img.className = "rcoverimg";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.alt = "";
      img.src = it.image;
      img.addEventListener("error", () => img.remove());
      wrap.appendChild(img);
    }
    const note = el(`<p class="rcovernote"></p>`);
    note.textContent = it.archive_url || it.wayback_url
      ? "已有快照，点“开始阅读”加载正文。"
      : "这条来自付费墙源，暂时没有可用快照。点“开始阅读”后，在归档页完成人机验证（没有快照时先点 archive this url），再回来提取正文。";
    wrap.appendChild(note);
    const row = el(`<div class="rcoverbtns"></div>`);
    const start = el(`<button class="rbtn primary"></button>`);
    start.textContent = "开始阅读";
    start.addEventListener("click", () => startReading(it));
    const orig = el(`<button class="rbtn"></button>`);
    orig.textContent = "打开原文";
    orig.addEventListener("click", () => open(it.url));
    row.appendChild(start);
    row.appendChild(orig);
    wrap.appendChild(row);
    rBody.innerHTML = "";
    rBody.appendChild(wrap);
  }

  function startReading(it) {
    // 有快照：直接重新走正文链（跳过封面）
    if (it.archive_url || it.wayback_url) { openReader(it, true); return; }
    // APK：进入人工验证捕获流程（archive 页 + 底部“完成验证 · 提取正文”）
    if (window.Android && Android.captureFor) {
      try { Android.captureFor(it.id, it.url, it.title || ""); } catch (e) {}
      return;
    }
    // 浏览器：打开 archive.today 最新快照/存档页，用户过完验证后回来重试
    open("https://archive.ph/newest/" + it.url);
  }


  // APK 原生 wayback 通道：调 Android.fetchSnapshot 后台抓 HTML，原生完成后回调 __wbDeliver。
  // 30s 内无回话（或原生不可用）返回 null，外层继续走兜底
  function nativeWayback(it, ctrl) {
    return new Promise(resolve => {
      if (!(window.Android && Android.fetchSnapshot)) return resolve(null);
      let done = false;
      const finish = v => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        if (ctrl.signal) ctrl.signal.removeEventListener("abort", onAbort);
        try { delete window.__wbDeliver; } catch (e) {}
        resolve(v);
      };
      const timer = setTimeout(() => finish(null), 30000);
      const onAbort = () => finish(null);
      ctrl.signal.addEventListener("abort", onAbort, { once: true });
      window.__wbDeliver = (wid, html) => {
        if (wid !== it.id) return;
        finish(html || null);
      };
      try { Android.fetchSnapshot(it.id, it.url, it.wayback_url || ""); }
      catch (e) { finish(null); }
    });
  }

  async function openReader(it, force) {
    readerEl.hidden = false;
    document.body.style.overflow = "hidden";
    rTitle.textContent = it.title;
    rMeta.textContent = [it.source, it.topic !== "General" ? it.topic : "", relTime(tsOf(it))].filter(Boolean).join(" · ");
    // APK：阅读模式下隐藏右上角按钮——必须在所有早退分支之前，否则缓存命中路径会漏隐藏、压住 ×
    try { window.Android && Android.hideUpdate(true); } catch (e) {}
    const host = _host(it.url);
    // 1) 人工捕获正文（APK 提取并清洗过）优先——用户亲验内容，最可信且零延迟
    const cap = capCache[it.id];
    if (cap && cap.paras && cap.paras.length) {
      rMeta.textContent += " · 提取正文 " + relTime(cap.ts);
      renderPlainParas(cap.paras);
      return;
    }
    // 2) 本地抓取缓存（md=jina markdown；kind=wb 存的是 wayback HTML）
    const hit = readCache[it.url];
    if (hit && hit.md) {
      const den = hit.kind === "wb" ? denoiseWaybackDoc(hit.md, it.title) : denoiseReader(hit.md, host, it.title);
      if (den) { renderReaderBody(den); return; }
    }
    rBody.innerHTML = '<div class="rspin"></div><div class="rstat" id="rstat">正在获取正文…</div>';
    const setStatus = t => { const e = document.getElementById("rstat"); if (e) e.textContent = t; };
    const ctrl = new AbortController();
    readerCtrl = ctrl;
    // 3) 私有缓存（CI 抓好的正文，仅 token 持有者可读；15s 超时防挂）
    if (CACHE_TOKEN) {
      try {
        const r = await fetchTextTimeout("https://api.github.com/repos/" + CACHE_REPO + "/contents/" + it.id + ".json?ref=main",
          { headers: { Authorization: "Bearer " + CACHE_TOKEN, Accept: "application/vnd.github+json" } }, 15000, ctrl.signal);
        if (r.ok) {
          const j = JSON.parse(r.text);
          // v2 缓存直接信；旧缓存（无 v）用同一套质量关复核，好的继续用、垃圾丢弃
          if (Array.isArray(j.blocks) && j.blocks.length
              && (j.v === 2 || readerQualityOk(j.blocks))) {
            renderReaderBody({ blocks: j.blocks, esc: escText });
            return;
          }
        }
      } catch (e) { /* 缓存不可用则照旧走链 */ }
    }
    // 3.4) 转载版直读：news.json 里带 syndicated_url 时，不需要私有缓存 token 也能读
    if (it.syndicated_url && !ctrl.signal.aborted) {
      setStatus("正在获取转载版正文…");
      const h = JINA_KEY ? { Authorization: "Bearer " + JINA_KEY } : {};
      try {
        const sr = await fetchTextTimeout("https://r.jina.ai/" + it.syndicated_url,
          { headers: h }, 25000, ctrl.signal);
        if (sr && sr.ok) {
          const sden = denoiseReader(sr.text, _host(it.syndicated_url), it.title);
          if (sden) { renderReaderBody(sden); return; }
        }
      } catch (e) { if (ctrl.signal.aborted) return; }
    }
    // 3.5) 封面模式：付费墙源且无快照/正文缓存 → 只渲染封面，等用户点“开始阅读”
    if (it.cover_only && !force) { renderCover(it); return; }
    // 4) 失败记忆：24h 内全链失败过 → 不再慢试，直接给人工兜底入口（修“没快照就很卡”）
    if (!force && Date.now() - (failMem[it.url] || 0) < 864e5) {
      renderReaderError(it);
      return;
    }
    // 正文获取链。浏览器 fetch 受 CORS 限制读不到任何快照内容（wayback 实测无 ACAO 头），
    // 而 jina 对 wayback 永久封禁、对 archive.today 家族只会拿到验证页——快照类候选全是无效等待，
    // 直接收敛为：发布者原文(jina, 20s) → APK 原生 wayback 直连（无 CORS 限制）→ 失败兜底
    let den = null, gotText = "", via = "";
    try {
      const headers = {};
      const sel = READER_SELECTORS[host];
      if (sel) headers["x-target-selector"] = sel;
      if (JINA_KEY) headers["Authorization"] = "Bearer " + JINA_KEY;
      setStatus("正在获取正文 · 1/2");
      let r = null;
      try {
        r = await fetchTextTimeout("https://r.jina.ai/" + it.url, { headers }, 20000, ctrl.signal);
        if (!r.ok && ctrl.signal.aborted) throw new Error("aborted");
        if (!r.ok && JINA_KEY) {
          delete headers.Authorization; // 预检失败退回匿名
          r = await fetchTextTimeout("https://r.jina.ai/" + it.url, { headers }, 20000, ctrl.signal);
        }
      } catch (e) {
        if (ctrl.signal.aborted) throw e;
      }
      if (r && r.ok) {
        den = denoiseReader(r.text, host, it.title);
        if (den) { gotText = r.text; via = "md"; }
      }
      // Jina 的服务器 IP 能过 archive.today 的人机验证；直连快照拿不到时走它
      if (!den && !ctrl.signal.aborted) {
        setStatus("正在获取正文 · 快照通道");
        try {
          const ar = await fetchTextTimeout("https://r.jina.ai/https://archive.ph/newest/" + it.url, { headers }, 25000, ctrl.signal);
          if (ar && ar.ok) {
            den = denoiseReader(ar.text, host, it.title);
            if (den) { gotText = ar.text; via = "md"; }
          }
        } catch (e) { /* 快照通道失败则继续原生 wayback */ }
      }
      // APK：原生通道直连 wayback（wayback_url 优先，web/2/ 重定向兜底），无 CORS 限制
      if (!den && window.Android && Android.fetchSnapshot && !ctrl.signal.aborted) {
        setStatus("正在获取正文 · 快照通道");
        const html = await nativeWayback(it, ctrl);
        if (ctrl.signal.aborted) throw new Error("aborted");
        if (html) {
          den = denoiseWaybackDoc(html, it.title);
          if (den) { gotText = html; via = "wb"; }
        }
      }
      if (den) {
        renderReaderBody(den);
        // 大 HTML 不入 localStorage 配额（wayback 页可达数 MB），只缓存够小的
        if (gotText.length < 900000) {
          readCache[it.url] = { ts: Date.now(), md: gotText, src: via === "wb" ? "native-wayback" : "jina:" + it.url, kind: via };
          const old = Object.entries(readCache).sort((a, b) => a[1].ts - b[1].ts);
          while (old.length > 30) { const [k] = old.shift(); delete readCache[k]; }
          try { localStorage.setItem(RKEY, JSON.stringify(readCache)); } catch (e) {}
        }
        delete failMem[it.url];
        persistFails();
        return;
      }
    } catch (e) {
      if (ctrl.signal.aborted) return; // 用户已关闭阅读器
    }
    // 全链失败：记忆 24h。APK 首次失败自动进入人工捕获流程；浏览器给出错误态（不再自动甩快照页）
    failMem[it.url] = Date.now();
    persistFails();
    if (ctrl.signal.aborted) return;
    if (window.Android && Android.captureFor) {
      readerEl.hidden = true;
      document.body.style.overflow = "";
      try { Android.hideUpdate(false); } catch (e2) {}
      // APK：进入人工验证捕获流程——过完验证点底部“完成验证 · 提取正文”
      try { Android.captureFor(it.id, it.url, it.title || ""); } catch (e2) {}
      showToast(I.arch);
      return;
    }
    renderReaderError(it); // 浏览器：按钮自选，不强制跳转
  }

  function closeReader() {
    if (readerCtrl) readerCtrl.abort();
    readerEl.hidden = true;
    document.body.style.overflow = "";
    try { window.Android && Android.hideUpdate(false); } catch (e) {}
  }
  $("#readerClose").addEventListener("click", closeReader);
  $("#readerExport").addEventListener("click", () => {
    // 打印导出 PDF：先把惰性图片强制加载完，再调起系统打印（可另存为 PDF）
    const imgs = [...rBody.querySelectorAll("img")];
    imgs.forEach(im => { im.loading = "eager"; });
    Promise.all(imgs.map(im => im.complete ? Promise.resolve() :
      new Promise(r => { im.onload = im.onerror = r; setTimeout(r, 3000); })))
      .then(() => setTimeout(() => {
        try { if (window.Android && Android.exportPage) { Android.exportPage(); return; } } catch (e) {}
        window.print(); // 浏览器：系统打印（可另存 PDF）
      }, 120));
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !readerEl.hidden) closeReader(); });

  // 收藏清单同步到私有仓库：CI 读取后优先为收藏文章补正文（保证 15 天后仍可读）
  function syncFavoritesRemote() {
    if (!CACHE_TOKEN) return;
    const list = Object.entries(saved).map(([id, v]) => {
      const o = savedObj(v);
      return { id, url: o.url || "", ts: o.ts || 0 };
    }).filter(x => x.url);
    const json = JSON.stringify({ updated_at: new Date().toISOString(), items: list });
    const headers = { Authorization: "Bearer " + CACHE_TOKEN, Accept: "application/vnd.github+json" };
    const api = "https://api.github.com/repos/" + CACHE_REPO + "/contents/favorites.json";
    fetch(api, { headers })
      .then(r => r.ok ? r.json() : null)
      .then(j => {
        const body = { message: "favorites sync", content: btoa(unescape(encodeURIComponent(json))) };
        if (j && j.sha) body.sha = j.sha;
        return fetch(api, { method: "PUT", headers, body: JSON.stringify(body) });
      })
      .catch(() => {});
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
        cover_only: !!it.cover_only, status: it.status || null,
        syndicated_url: it.syndicated_url || null,
      };
      btn.setAttribute("aria-pressed", "true");
    }
    persist();
    syncFavoritesRemote();
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
          cover_only: fresh ? !!fresh.cover_only : !!obj.cover_only,
          status: (fresh && fresh.status) || obj.status || null,
          syndicated_url: (fresh && fresh.syndicated_url) || obj.syndicated_url || null,
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
    // 清除按钮常驻占位（ inactive 时隐藏），保证 meta 行高度恒定、dock 不跳动
    const clear = el(`<button class="clear" aria-label="Clear filters">${I.x}</button>`);
    clear.addEventListener("click", clearFilters);
    if (!active) clear.style.visibility = "hidden";
    metaEl.appendChild(clear);

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
  // 人工验证捕获：APK 在 archive 域上提取并过原生校验后回传的正文；JS 再清洗一道，垃圾直接丢弃
  try {
    const capRaw = localStorage.getItem("journal.captureRaw");
    if (capRaw) {
      localStorage.removeItem("journal.captureRaw");
      const cap = JSON.parse(capRaw);
      const paras = cleanCaptureText(cap.text || "", cap.title);
      if (cap.id && paras.length) {
        capCache[cap.id] = { ts: Date.now(), title: cap.title || "", paras };
        const ces = Object.entries(capCache).sort((a, b) => a[1].ts - b[1].ts);
        while (ces.length > 20) { const [k] = ces.shift(); delete capCache[k]; }
        persistCaps();
        readerEl.hidden = false;
        document.body.style.overflow = "hidden";
        rTitle.textContent = cap.title || "已提取文章";
        rMeta.textContent = "提取正文 · " + relTime(Date.now());
        renderPlainParas(paras);
        try { window.Android && Android.hideUpdate(true); } catch (e2) {}
      }
    }
  } catch (e) {}

  // 双击报头 → 设置 Jina key（手机上没有控制台，这是唯一入口）
  $(".masthead h1").addEventListener("dblclick", () => {
    const cur = (() => { try { return localStorage.getItem("journal.jinakey") || ""; } catch (e) { return ""; } })();
    const k = window.prompt("Jina API key（留空则清除）:", cur);
    if (k !== null) {
      try {
        if (k.trim()) localStorage.setItem("journal.jinakey", k.trim());
        else localStorage.removeItem("journal.jinakey");
      } catch (e) {}
    }
    const curT = (() => { try { return localStorage.getItem("journal.cachetoken") || ""; } catch (e) { return ""; } })();
    const t = window.prompt("私有正文缓存 token（fine-grained PAT，留空则清除）:", curT);
    if (t !== null) {
      try {
        if (t.trim()) localStorage.setItem("journal.cachetoken", t.trim());
        else localStorage.removeItem("journal.cachetoken");
      } catch (e) {}
    }
    location.reload();
  });

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
    syncFavoritesRemote();
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
