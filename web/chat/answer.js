// 回答呈現：依後端回傳的欄位定義（r.schema，來自設定 answer_schema）畫出回答。聊天頁與後台測試對話共用這一份。
// 沒有 schema（舊紀錄）時，依欄位內容的型別直接呈現。
window.AnswerView = (() => {
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const DEFAULT = [
    { key: "empathy", type: "text", label: "", modes: "full", summary: true },
    { key: "reply", type: "text", label: "", modes: "continue", summary: true },
    { key: "understanding", type: "text", label: "看見與理解", modes: "full" },
    { key: "strategies", type: "items", label: "現在可以試試", labelContinue: "可以再試試", modes: "both", summary: true },
    { key: "parent_message", type: "text", label: "給家長的訊息草稿", modes: "both", copy: true },
    { key: "observe", type: "list", label: "接下來觀察記錄", labelContinue: "觀察重點", modes: "both" },
    { key: "teacher_care", type: "text", label: "也照顧你自己", modes: "full" },
  ];
  const SYSTEM = ["referral", "options", "notice", "knowledge_gap"];

  // 這一則回答用到的欄位：有 schema 就照 schema 的模式過濾；沒有就依內容猜型別
  function fields(r) {
    const a = r.answer || {}, mode = r.type === "continue" ? "continue" : "full";
    const sch = Array.isArray(r.schema) && r.schema.length ? r.schema : null;
    if (sch) return sch.filter((f) => f.modes === "both" || f.modes === mode).map((f) => Object.assign({}, f, { label: mode === "continue" ? f.labelContinue != null ? f.labelContinue : f.label : f.label }));
    const known = DEFAULT.filter((f) => f.modes === "both" || f.modes === mode).map((f) => Object.assign({}, f, { label: mode === "continue" ? f.labelContinue != null ? f.labelContinue : f.label : f.label }));
    Object.keys(a).forEach((k) => {
      if (SYSTEM.includes(k) || known.some((f) => f.key === k)) return;
      const v = a[k];
      known.push({ key: k, label: k, type: Array.isArray(v) ? (v.length && typeof v[0] === "object" ? "items" : "list") : "text" });
    });
    return known;
  }
  const has = (f, v) => (f.type === "text" ? !!v : Array.isArray(v) && v.length > 0);

  /** 聊天頁用的 HTML。opts.restore 還原去識別化的名字；opts.chips(item) 產生出處按鈕 */
  function render(r, opts) {
    opts = opts || {}; const L = opts.labels || {};
    const a = r.answer || {}, R = (s) => esc(opts.restore ? opts.restore(s) : s), chips = opts.chips || (() => "");
    let h = a.notice ? `<div class="notice">${esc(a.notice)}</div>` : "";
    fields(r).forEach((f) => {
      const v = a[f.key];
      if (!has(f, v)) return;
      const head = f.label ? `<h3>${esc(f.label)}</h3>` : "";
      if (f.type === "items") h += head + `<ol>${v.map((s) => `<li><b>${R(s.title)}</b>：${R(s.how)}${chips(s)}</li>`).join("")}</ol>`;
      else if (f.type === "list") h += head + `<ul>${v.map((o) => `<li>${R(o)}</li>`).join("")}</ul>`;
      else if (f.copy) h += head + `<div class="pm" data-field="${esc(f.key)}">${R(v)}</div><div class="actions"><button type="button" data-copy="${esc(f.key)}" data-label="${esc(f.label || f.key)}">複製</button></div>`;
      else h += head + `<p>${R(v)}</p>`;
    });
    if (a.referral) h += `<h3>${esc(L.referral_heading || "需要時找專人")}</h3><p>${R(a.referral)}</p>`;
    if ((a.options || []).length) h += `<div class="opts">${a.options.map((o) => `<button type="button" class="opt" data-opt="${esc(o)}">${esc(o)}</button>`).join("")}</div>`;
    return h;
  }

  /** 後台用的文字版（換行與粗體標題，refs 直接列在後面） */
  function text(r, restore, labels) {
    const L = labels || {};
    const a = r.answer || {}, R = (s) => esc(restore ? restore(s) : s);
    const parts = [];
    fields(r).forEach((f) => {
      const v = a[f.key];
      if (!has(f, v)) return;
      const head = f.label ? `<b>${esc(f.label)}</b>\n` : "";
      if (f.type === "items") parts.push(head + v.map((s, i) => `${i + 1}. ${R(s.title)}：${R(s.how)} [${(s.refs || []).join(",")}]`).join("\n"));
      else if (f.type === "list") parts.push(head + v.map((o) => "・" + R(o)).join("\n"));
      else parts.push(head + R(v));
    });
    if (a.referral) parts.push(`<b>${esc(L.referral_heading || "需要時找專人")}</b>\n${R(a.referral)}`);
    return parts.join("\n\n");
  }

  /** 對話歷史用的摘要：summary 欄位的文字＋建議標題 */
  function summary(r) {
    const a = r.answer || {};
    return fields(r).filter((f) => f.summary)
      .map((f) => { const v = a[f.key]; if (!has(f, v)) return ""; return f.type === "items" ? "建議：" + v.map((s) => s.title).join("、") : f.type === "list" ? v.join("；") : String(v); })
      .filter(Boolean).join(" ");
  }

  return { render, text, summary, fields };
})();
