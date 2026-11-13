(() => {
  const C = window.CHAT_CONFIG;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const newSession = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
  let UI = {};
  const ui = (k, d) => (UI[k] != null && UI[k] !== "" ? UI[k] : d);
  let S = { code: "", session: newSession(), history: [], nameMap: {}, followups: 0, answered: 0, turn: 0, welcome: "", trace: false, replies: [], view: -1 };

  async function api(action, payload) {
    if (!C.GAS_URL || C.GAS_URL.includes("請替換")) throw new Error("尚未設定後端網址（chat/config.js）");
    const res = await fetch(C.GAS_URL, { method: "POST", headers: { "Content-Type": "text/plain;charset=utf-8" }, body: JSON.stringify(Object.assign({ action, code: S.code, session: S.session }, payload || {})) });
    const d = await res.json().catch(() => ({ ok: false, error: "伺服器回應無法解析" }));
    if (!d.ok) throw new Error(d.error || "發生錯誤，請稍後再試");
    return d;
  }
  const restore = (s) => { let o = String(s == null ? "" : s); Object.entries(S.nameMap).forEach(([n, l]) => (o = o.split(l).join(n))); return o; };
  const R = (s) => esc(restore(s));

  function bubble(cls, html) {
    const d = document.createElement("div");
    d.className = "bubble " + cls; d.innerHTML = html;
    $("chat").appendChild(d); d.scrollIntoView({ behavior: "smooth", block: "end" });
    return d;
  }

  function render(r) {
    if (r.type === "followup") return `<p>${R(r.empathy)}</p><ol>${r.questions.map((q) => `<li>${R(q)}</li>`).join("")}</ol>`;
    if (r.type === "referral") {
      const f = r.referral;
      return `<h3>${esc(ui("referral_title", "這個情況需要馬上找專人"))}（${esc(f.category)}）</h3><p>${R(f.reason)}</p><h3>該找誰</h3><p>${esc(f.who)}${f.contact ? "｜" + esc(f.contact) : ""}</p>${f.now ? `<h3>現在要做的事</h3><p>${esc(f.now)}</p>` : ""}${ui("referral_note", "") ? `<p class="muted small">${esc(ui("referral_note", ""))}</p>` : ""}`;
    }
    if (r.type !== "answer" && r.type !== "continue" && r.type !== "ai_fallback") return `<p>${esc(r.message)}</p>`;
    const a = r.answer, src = {};
    (r.sources || []).forEach((s) => (src[s.ref] = s));
    const showSrc = UI.show_sources !== false && UI.show_sources !== "FALSE" && UI.show_sources !== "false";   // 後台 show_sources 關閉時不顯示出處按鈕
    const chips = (s) => (showSrc ? (s.refs || []).filter((x) => src[x]).map((x) => `<button type="button" class="ref" data-ref="${esc(x)}">${esc(x)}</button>`).join("") : "") +
      ((r.type === "ai_fallback" || (a && a.notice)) && !(s.refs || []).some((x) => src[x]) ? `<span class="ai-tag">${esc(ui("ai_tag", "AI 補充"))}</span>` : "");
    return AnswerView.render(r, { restore, chips, labels: UI });
  }

  function attach(el, r, turn) {
    const src = {}; (r.sources || []).forEach((s) => (src[s.ref] = s));
    el.addEventListener("click", (e) => {
      const b = e.target.closest(".ref");
      if (b) {
        const li = b.closest("li"); const old = li.querySelector(`.src[data-for="${b.dataset.ref}"]`);
        if (old) return old.remove();
        const s = src[b.dataset.ref];
        const box = document.createElement("div"); box.className = "src"; box.dataset.for = b.dataset.ref;
        box.innerHTML = `<b>${esc(s.title)}</b>${s.page ? "｜第" + esc(s.page) + "頁" : ""}${s.evidence ? "｜" + esc(s.evidence) : ""}\n${esc(s.source || "")}\n「${esc(s.snippet)}…」`;
        li.appendChild(box); return;
      }
      const c = e.target.closest("[data-copy]");
      if (c) {
        const box = el.querySelector(`.pm[data-field="${c.dataset.copy}"]`) || el.querySelector(".pm");
        navigator.clipboard?.writeText(box ? box.textContent : "").then(() => (c.textContent = "已複製"), () => (c.textContent = "請手動選取複製"));
        api("feedback", { turn, kind: "複製：" + (c.dataset.label || "") }).catch(() => {});
      }
      const op = e.target.closest("[data-opt]");
      if (op) { if ($("send").disabled) return; return send(op.dataset.opt); }
      const tb = e.target.closest("[data-trace]");
      if (tb) return openDrawer(Number(tb.dataset.trace));
      const f = e.target.closest("[data-fb]");
      if (f && !f.classList.contains("on")) {
        f.parentElement.querySelectorAll("[data-fb]").forEach((x) => x.classList.remove("on")); f.classList.add("on");
        api("feedback", { turn, kind: f.dataset.fb }).catch(() => {});
      }
    });
    const fbOn = r.type === "answer" || r.type === "ai_fallback" || r.type === "continue" || r.type === "referral" || r.type === "conservative";
    if (fbOn || S.trace) {
      const a = document.createElement("div"); a.className = "actions";
      a.innerHTML = (fbOn ? `<button type="button" data-fb="有幫助">${esc(ui("fb_up", "👍 有幫助"))}</button><button type="button" data-fb="沒幫助">${esc(ui("fb_down", "👎 沒幫助"))}</button>` : "") +
        (S.trace ? `<button type="button" class="trace-link" data-trace="${S.replies.length - 1}">🔍 看流程</button>` : "");
      el.appendChild(a);
    }
  }

  async function send(text) {
    bubble("me", esc(text));
    const wait = bubble("bot typing", esc(ui("waiting", "正在了解狀況、查找資料…")));
    $("send").disabled = true; S.turn++;
    try {
      const r = await api("chat", { message: text, history: S.history, nameMap: S.nameMap, followups: S.followups, answered: S.answered, turn: S.turn });
      r.question = text; S.replies.push(r);
      S.nameMap = r.nameMap || S.nameMap;
      // 換了新個案：舊的對話不再帶給後端
      if (r.newCase) { S.history = []; S.followups = 0; }
      if (r.answered != null) S.answered = r.answered;
      S.history.push({ role: "teacher", text: r.deidText });
      if (r.type === "followup") S.followups++;
      S.history.push({ role: "bot", text: botSummary(r) });
      wait.remove();
      const el = bubble("bot" + (r.type === "referral" ? " flag" : ""), render(r));
      attach(el, r, S.turn);
      if ($("drawer").classList.contains("open")) openDrawer(S.replies.length - 1);
    } catch (e) {
      wait.remove(); bubble("bot", `<p class="err">${esc(e.message)}</p>`);
    } finally { $("send").disabled = false; $("msg").focus(); }
  }

  function botSummary(r) {
    if (r.type === "answer" || r.type === "ai_fallback" || r.type === "continue") return AnswerView.summary(r);
    if (r.type === "followup") return r.questions.join(" ");
    return r.message || "";
  }

  function start() {
    $("chat").innerHTML = "";
    S = Object.assign(S, { session: newSession(), history: [], nameMap: {}, followups: 0, answered: 0, turn: 0, replies: [], view: -1 });
    if ($("drawer").classList.contains("open")) openDrawer(-1);
    if (S.welcome) bubble("bot", `<p>${esc(S.welcome)}</p>`);
  }

  async function enter(code) {
    $("gateErr").textContent = "";
    $("enter").disabled = true; S.code = code;
    try {
      const r = await api("check");
      if (r.ui) applyUi(r.ui);
      S.welcome = r.welcome || ""; S.trace = !!r.trace; $("traceBtn").hidden = !S.trace; $("botName").textContent = r.botName || $("botName").textContent; document.title = $("botName").textContent;
      try { sessionStorage.setItem("chat-code", code); } catch (e) {}
      $("gate").hidden = true; $("chat").hidden = false; $("composer").hidden = false; $("reset").hidden = false;
      start(); $("msg").focus();
    } catch (e) { $("gateErr").textContent = e.message; $("gate").hidden = false; } finally { $("enter").disabled = false; }
  }
  $("enter").addEventListener("click", () => {
    const code = $("code").value.trim();
    if (!code) return ($("gateErr").textContent = "請輸入邀請碼。");
    enter(code);
  });
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); const t = $("msg").value.trim(); if (!t || $("send").disabled) return; $("msg").value = ""; send(t); });
  $("msg").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); } });
  $("reset").addEventListener("click", start);

  // ---------- 流程觀測面板 ----------
  function openDrawer(i) {
    const n = S.replies.length;
    S.view = i < 0 ? n - 1 : Math.min(i, n - 1);
    const r = S.replies[S.view];
    $("drawerSub").textContent = r ? `第 ${S.view + 1} 回合，共 ${n} 回合｜${r.question.slice(0, 24)}${r.question.length > 24 ? "…" : ""}` : "";
    $("drawerBody").innerHTML = r ? (r.trace ? TraceView.render(r) : '<p class="muted">這一回合沒有流程紀錄</p>') : '<p class="muted">送出一個問題後，這裡會顯示系統怎麼分流、找了哪些資料、做了哪些檢查。</p>';
    $("dPrev").disabled = S.view <= 0; $("dNext").disabled = S.view >= n - 1;
    $("drawer").classList.add("open"); $("drawer").setAttribute("aria-hidden", "false");
    $("scrim").hidden = false; document.body.classList.add("drawer-on"); $("traceBtn").setAttribute("aria-expanded", "true");
    $("drawerBody").scrollTop = 0;
  }
  function closeDrawer() {
    $("drawer").classList.remove("open"); $("drawer").setAttribute("aria-hidden", "true");
    $("scrim").hidden = true; document.body.classList.remove("drawer-on"); $("traceBtn").setAttribute("aria-expanded", "false");
  }
  $("traceBtn").addEventListener("click", () => ($("drawer").classList.contains("open") ? closeDrawer() : openDrawer(-1)));
  $("dClose").addEventListener("click", closeDrawer);
  $("scrim").addEventListener("click", closeDrawer);
  $("dPrev").addEventListener("click", () => openDrawer(S.view - 1));
  $("dNext").addEventListener("click", () => openDrawer(S.view + 1));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("drawer").classList.contains("open")) closeDrawer(); });
  // 聊天頁的文字與外觀：後台「聊天介面」設定；沒設或拿不到時用頁面上的預設字
  function applyUi(u) {
    UI = u || {};
    if (UI.botName) { $("botName").textContent = UI.botName; document.title = UI.botName; }
    if (UI.gate_text) $("gateText").textContent = UI.gate_text;
    if (UI.input_placeholder) $("msg").placeholder = UI.input_placeholder;
    if (UI.accent && /^#[0-9a-f]{3,8}$/i.test(UI.accent)) document.documentElement.style.setProperty("--accent", UI.accent);
  }
  // 載入時先拿介面設定；後台關閉邀請碼時直接進聊天（不顯示邀請碼頁）
  (async () => {
    try {
      const r = await fetch(C.GAS_URL, { method: "POST", headers: { "Content-Type": "text/plain;charset=utf-8" }, body: JSON.stringify({ action: "ui" }) }).then((x) => x.json());
      if (r && r.ok) { applyUi(r.ui); if (r.invite === false) { $("gate").hidden = true; enter(""); } }
    } catch (e) {}
  })();
  try { const c = sessionStorage.getItem("chat-code"); if (c) $("code").value = c; } catch (e) {}
})();
