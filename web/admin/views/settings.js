/* 機器人設定：版本、分區欄位、結構化編輯（回答欄位、意圖、檢查項目，可「用說的」轉換）、模型測試 */
(function () {
  const BOOL = (v) => typeof v === "boolean" || /^(true|false)$/i.test(String(v));
  const LABELS = { bot_name: "機器人名稱", welcome: "歡迎訊息", default_skill: "預設 Skill", show_sources: "顯示出處給使用者", enable_invite: "邀請碼", daily_limit: "每日上限（次）",
    hourly_limit: "每小時上限（次）", domain_desc: "服務領域", role_user: "提問者稱呼", role_subject: "談論對象稱呼", role_escalate: "轉介對象稱呼", model_answer: "回答模型",
    model_triage: "分流模型", model_rerank: "重排模型", model_check: "檢查模型", temperature: "回答的隨機程度", model_eval_gen: "評測出題模型", model_eval_judge: "評測評審模型",
    enable_triage: "L4 分流", max_followups: "最多追問次數", enable_case_flow: "個案機制（延續回覆）", full_answer_rounds: "完整回答幾次後改延續", enable_rerank: "AI 重排與配額",
    enable_kg: "知識圖譜參與回答", kg_mode: "圖譜模式", enable_deid: "去識別化", deid_mode: "去識別化方式", deid_labels: "代稱前綴", enable_citation: "要求出處",
    knowledge_only: "只用知識庫回答", enable_answer_schema: "回答欄位", enable_llm_check: "AI 檢查", check_items: "檢查項目", check_severe: "什麼算嚴重",
    enable_relevance_check: "回應度檢查", system_prompt: "系統提示詞", answer_schema: "回答欄位", answer_rules: "回答規則", fallback_rules: "AI 補足時的規則", intents: "意圖",
    triage_rules: "分流規則", triage_focus_hint: "提問焦點說明", followup_prompt: "追問提示詞", continue_prompt: "延續回覆提示詞", continue_options: "延續回覆的選項",
    top_k: "採用段數", min_score: "相似度門檻", rerank_pool: "重排候選數", quota_min_actionable: "至少幾段可照做", quota_max_principle: "最多幾段原則／背景",
    kg_boost: "圖譜加分", kg_max_nodes: "圖譜最多命中節點", ui_accent: "主題色" };
  const MODEL = /^model_/;
  const CHOICE = { kg_mode: [["hybrid", "混合（圖譜加分＋一般檢索）"], ["only", "只用圖譜對到的段落"]], deid_mode: [["ai", "AI（較準，多一次呼叫或併入分流）"], ["rule", "規則（不呼叫模型）"]] };
  const STRUCT = { answer_schema: "回答欄位", intents: "意圖", check_items: "檢查項目" };
  const TOP = ["版本與基本", "角色用詞", "模型"];

  A.settingsEditor = async function (box, botId, wsId) {
    const [d, models] = await Promise.all([A.api("GET", `/bots/${botId}/settings`), A.api("GET", "/models").catch(() => null)]);
    const items = d.items.filter((x) => !x.kb_level && x.key !== "tier");
    const by = Object.fromEntries(items.map((x) => [x.key, x]));
    const cur = Object.fromEntries(items.map((x) => [x.key, x.value == null ? "" : BOOL(x.default) && BOOL(x.value) ? String(x.value).toUpperCase() : String(x.value)]));
    const orig = { ...cur };
    const used = new Set();
    const groups = d.groups.map((g, i) => ({ id: "g" + i, title: g.title, keys: g.keys.filter((k) => by[k] && !used.has(k) && used.add(k)) }));
    const rest = items.map((x) => x.key).filter((k) => !used.has(k));
    if (rest.length) groups.push({ id: "gx", title: "其他", keys: rest });
    const tier = String((d.items.find((x) => x.key === "tier") || {}).value || "basic");
    const modelList = models ? models.suggested : [];

    const field = (k) => {
      const s = by[k], v = cur[k], id = "f_" + k;
      const head = `<label for="${id}">${A.esc(label(k, s))}<span class="key">${A.esc(k)}</span></label>`;
      const note = s.note ? `<div class="note">${A.esc(s.note)}</div>` : "";
      let input;
      if (STRUCT[k]) input = `<div class="st" data-st="${k}"></div>`;
      else if (BOOL(s.default)) input = `<label class="inline"><input type="checkbox" id="${id}" data-k="${k}" ${v.toUpperCase() === "TRUE" ? "checked" : ""}> 開啟</label>`;
      else if (CHOICE[k]) input = `<select id="${id}" data-k="${k}">${CHOICE[k].map(([a, b]) => `<option value="${a}" ${a === v ? "selected" : ""}>${A.esc(b)}</option>`).join("")}</select>`;
      else if (MODEL.test(k)) input = `<div class="row"><input type="text" id="${id}" data-k="${k}" value="${A.esc(v)}" list="modelList" style="flex:1"><button class="btn sm" data-ping="${k}" type="button">測試</button></div><div class="note" data-pingout="${k}"></div>`;
      else if (s.text || v.includes("\n") || v.length > 70) input = `<textarea id="${id}" data-k="${k}" rows="${Math.min(12, Math.max(3, v.split("\n").length + 1))}">${A.esc(v)}</textarea>`;
      else input = `<input type="text" id="${id}" data-k="${k}" value="${A.esc(v)}">`;
      return `<div class="fld" data-f="${k}">${head}${input}${note}<div class="err" data-e="${k}"></div></div>`;
    };
    const sec = (g) => `<div class="sec" id="sec-${g.id}"><h4>${A.esc(g.title)}</h4>${g.keys.map(field).join("")}</div>`;
    const top = groups.filter((g) => TOP.includes(g.title)), adv = groups.filter((g) => !TOP.includes(g.title));
    box.innerHTML = `<datalist id="modelList">${modelList.map((m) => `<option value="${A.esc(m)}">`).join("")}</datalist>
      <div class="setwrap"><nav class="setnav"><a data-go="tier">版本</a>${top.map((g) => `<a data-go="${g.id}">${A.esc(g.title)}</a>`).join("")}<span class="note" style="padding:8px 10px 2px">進階</span>${adv.map((g) => `<a data-go="${g.id}">${A.esc(g.title)}</a>`).join("")}</nav>
      <div><div class="sec" id="sec-tier"><h4>版本 <span class="note">選一個就套用該版本的開關組合（直接儲存）；之後每個開關仍可個別調整</span></h4>
        <div class="tiers">${Object.entries(d.tiers).map(([k, t]) => `<label class="tier ${k === tier ? "on" : ""}"><input type="radio" name="tier" value="${k}" ${k === tier ? "checked" : ""}><b>${A.esc(t.name)}</b><span class="note">${A.esc(t.desc)}</span></label>`).join("")}</div></div>
      ${top.map(sec).join("")}
      <details class="box" id="adv"><summary><b>進階設定</b> <span class="note">流程開關、檢查、提示詞、檢索參數、聊天介面文字</span></summary><div style="margin-top:12px">${adv.map(sec).join("")}</div></details>
      ${d.notes && d.notes.length ? `<div class="box warn">設定讀取時發現的問題：${d.notes.map((n) => A.esc(n.join ? n.join("：") : n)).join("；")}</div>` : ""}
      <div class="savebar"><span class="note" id="dirtyN"></span><button class="btn" id="sReset" type="button">復原</button><button class="btn pri" id="sSave" type="button">儲存</button></div></div></div>`;

    // 結構化編輯器
    A.$$("[data-st]", box).forEach((el) => structEditor(el, el.dataset.st));

    function structEditor(el, key) {
      let mode = "table";
      const render = () => {
        el.innerHTML = `<div class="stabs">${[["table", "表格"], ["say", "用說的"], ["raw", "原始文字"]].map(([m, t]) => `<button type="button" data-m="${m}" class="${m === mode ? "on" : ""}">${t}</button>`).join("")}</div><div class="stb"></div>`;
        A.$$(".stabs button", el).forEach((b) => (b.onclick = () => { mode = b.dataset.m; render(); }));
        const b = A.$(".stb", el);
        if (mode === "raw") {
          b.innerHTML = `<textarea rows="8" class="mono">${A.esc(cur[key])}</textarea>`;
          A.$("textarea", b).oninput = (e) => { cur[key] = e.target.value; dirty(); };
        } else if (mode === "say") {
          b.innerHTML = `<p class="note">用一段話描述你要的${STRUCT[key]}，AI 轉成設定後會先顯示在表格，確認了再按下方「儲存」。</p>
            <textarea rows="5" class="say" placeholder="例如：先用兩三句直接回答，再列兩到三個有出處的具體做法，最後提醒什麼時候要找專人"></textarea>
            <div class="row" style="margin-top:6px"><button class="btn" type="button" data-back>把目前設定寫成一段話</button><button class="btn pri" type="button" data-conv>轉換</button><span class="note" data-sayout></span></div>`;
          A.$("[data-back]", b).onclick = (e) => A.act(e.target, async () => { A.$(".say", b).value = (await A.api("POST", `/bots/${botId}/describe-back`, { kind: key })).text; });
          A.$("[data-conv]", b).onclick = (e) => A.act(e.target, async () => {
            const r = await A.api("POST", `/bots/${botId}/describe`, { kind: key, text: A.$(".say", b).value, current: cur[key] });
            cur[key] = key === "answer_schema" ? r.json : r.text;
            if (key === "check_items" && r.severe && by.check_severe) { cur.check_severe = r.severe; const t = A.$("#f_check_severe", box); if (t) t.value = r.severe; }
            mode = "table"; render(); dirty();
            A.toast("已轉換，確認表格內容後按「儲存」" + (r.warn && r.warn.length ? "\n" + r.warn.join("\n") : ""));
          });
        } else b.innerHTML = key === "answer_schema" ? schemaTable() : linesTable();
        bindTable(b);
      };
      const lines = () => String(cur[key] || "").split("\n").filter((l) => l.trim()).map((l) => { const i = l.indexOf("＝"); return i < 0 ? [l.trim(), ""] : [l.slice(0, i).trim(), l.slice(i + 1).trim()]; });
      const linesTable = () => `<table><thead><tr><th style="width:28%">名稱</th><th>說明</th><th></th></tr></thead><tbody>${lines().map(([n, dsc], i) =>
        `<tr><td><input type="text" data-c="0" data-r="${i}" value="${A.esc(n)}"></td><td><input type="text" data-c="1" data-r="${i}" value="${A.esc(dsc)}"></td><td>${key === "intents" ? "" : `<button class="btn sm danger" type="button" data-del="${i}">刪</button>`}</td></tr>`).join("")}</tbody></table>
        ${key === "intents" ? '<p class="note">意圖固定四個，順序是：新問題、補充同一問題、要求特定協助、與主題無關；可以改名稱與說明。</p>' : '<button class="btn sm" type="button" data-add>新增一列</button>'}`;
      let schema;
      const parseSchema = () => { try { const a = JSON.parse(cur[key] || "[]"); return Array.isArray(a) ? a : []; } catch (e) { return null; } };
      const schemaTable = () => {
        schema = parseSchema();
        if (!schema) return `<p class="err">目前內容不是有效的 JSON，請到「原始文字」修正。</p>`;
        const tsel = (v, i) => `<select data-f="type" data-r="${i}">${[["text", "一段文字"], ["list", "條列"], ["items", "有出處的建議"]].map(([a, b]) => `<option value="${a}" ${a === v ? "selected" : ""}>${b}</option>`).join("")}</select>`;
        const msel = (v, i) => `<select data-f="modes" data-r="${i}">${[["full", "完整回答"], ["continue", "延續回覆"], ["both", "都要"]].map(([a, b]) => `<option value="${a}" ${a === (v || "full") ? "selected" : ""}>${b}</option>`).join("")}</select>`;
        const cb = (f, v, i) => `<input type="checkbox" data-f="${f}" data-r="${i}" ${v ? "checked" : ""}>`;
        return `<div style="overflow:auto"><table><thead><tr><th>key</th><th>標題</th><th>給模型的說明</th><th>類型</th><th>出現在</th><th>必填</th><th>摘要</th><th>複製鈕</th><th>數量</th><th></th></tr></thead><tbody>${schema.map((f, i) =>
          `<tr><td><input type="text" data-f="key" data-r="${i}" value="${A.esc(f.key)}" style="width:90px"></td><td><input type="text" data-f="label" data-r="${i}" value="${A.esc(f.label || "")}"></td>
          <td><input type="text" data-f="hint" data-r="${i}" value="${A.esc(f.hint || "")}" style="min-width:220px"></td><td>${tsel(f.type, i)}</td><td>${msel(f.modes, i)}</td>
          <td>${cb("required", f.required, i)}</td><td>${cb("summary", f.summary, i)}</td><td>${cb("copy", f.copy, i)}</td>
          <td>${f.type === "items" ? `<input type="text" data-f="count" data-r="${i}" value="${A.esc(f.count || "2-3")}" style="width:56px">` : ""}</td><td><button class="btn sm danger" type="button" data-del="${i}">刪</button></td></tr>`).join("")}</tbody></table></div>
          <button class="btn sm" type="button" data-add>新增欄位</button> <span class="note">key 用英文；「有出處的建議」會要求模型附段落編號，出處檢查只看這一類欄位</span>`;
      };
      const bindTable = (b) => {
        if (key === "answer_schema" && schema) {
          const save = () => { cur[key] = "[\n" + schema.map((o) => JSON.stringify(o)).join(",\n") + "\n]"; dirty(); };
          A.$$("[data-f]", b).forEach((x) => (x.onchange = () => { const o = schema[+x.dataset.r]; o[x.dataset.f] = x.type === "checkbox" ? x.checked : x.value; if (x.dataset.f === "type") render(); save(); }));
          A.$$("[data-del]", b).forEach((x) => (x.onclick = () => { schema.splice(+x.dataset.del, 1); save(); render(); }));
          const ad = A.$("[data-add]", b);
          if (ad) ad.onclick = () => { schema.push({ key: "field_" + (schema.length + 1), type: "text", label: "", hint: "", modes: "full" }); save(); render(); };
        } else if (key !== "answer_schema") {
          const rows = lines();
          const save = () => { cur[key] = rows.filter((r) => r[0]).map((r) => r[0] + "＝" + r[1]).join("\n"); dirty(); };
          A.$$("[data-c]", b).forEach((x) => (x.oninput = () => { rows[+x.dataset.r][+x.dataset.c] = x.value.replace(/[＝\n]/g, " "); save(); }));
          A.$$("[data-del]", b).forEach((x) => (x.onclick = () => { rows.splice(+x.dataset.del, 1); save(); render(); }));
          const ad = A.$("[data-add]", b);
          if (ad) ad.onclick = () => { rows.push(["新項目", ""]); save(); render(); };
        }
      };
      render();
    }

    function dirty() {
      let n = 0;
      Object.keys(cur).forEach((k) => {
        const dd = norm(k, cur[k]) !== norm(k, orig[k]);
        n += dd ? 1 : 0;
        const f = A.$(`[data-f="${k}"].fld`, box);
        if (f) f.classList.toggle("dirty", dd);
      });
      A.$("#dirtyN", box).textContent = n ? `已修改 ${n} 項，尚未儲存` : "";
      return n;
    }
    const norm = (k, v) => (BOOL(by[k].default) ? String(v).toUpperCase() === "TRUE" : String(v == null ? "" : v));

    box.addEventListener("input", (e) => {
      const k = e.target.dataset && e.target.dataset.k;
      if (!k) return;
      cur[k] = e.target.type === "checkbox" ? (e.target.checked ? "TRUE" : "FALSE") : e.target.value;
      dirty();
    });
    A.$$(".setnav a", box).forEach((a) => (a.onclick = () => {
      const el = A.$("#sec-" + a.dataset.go, box);
      if (!el) return;
      const det = el.closest("details");
      if (det) det.open = true;
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }));
    A.$$('input[name="tier"]', box).forEach((r) => (r.onchange = async () => {
      const t = d.tiers[r.value];
      if (dirty() && !confirm("有尚未儲存的修改，套用版本會重新載入、這些修改會不見。繼續？")) return A.settingsEditor(box, botId, wsId);
      if (!confirm(`套用「${t.name}」：會改這些開關並直接儲存：\n${Object.entries(t.set).map(([k, v]) => label(k, by[k]) + "＝" + (v === true ? "開" : v === false ? "關" : v)).join("、")}\n其他設定不動。確定？`)) return A.settingsEditor(box, botId, wsId);
      await A.act(null, () => A.api("POST", `/bots/${botId}/tier`, { tier: r.value }), "已套用「" + t.name + "」");
      A.settingsEditor(box, botId, wsId);
    }));
    A.$$("[data-ping]", box).forEach((b) => (b.onclick = () => A.act(b, async () => {
      const out = A.$(`[data-pingout="${b.dataset.ping}"]`, box);
      out.textContent = "測試中…";
      const r = await A.api("POST", "/models/ping", { model: cur[b.dataset.ping] });
      out.innerHTML = r.ok ? `<span class="ok">可以用</span>，${(r.ms / 1000).toFixed(1)} 秒｜${A.esc(r.reply)}` : `<span class="err">不能用：${A.esc(r.error)}</span>`;
    })));
    A.$("#sReset", box).onclick = () => A.settingsEditor(box, botId, wsId);
    A.$("#sSave", box).onclick = (e) => A.act(e.target, async () => {
      A.$$("[data-e]", box).forEach((x) => (x.textContent = ""));
      const changed = Object.keys(cur).filter((k) => norm(k, cur[k]) !== norm(k, orig[k])).map((k) => ({ key: k, value: cur[k] }));
      if (!changed.length) return A.toast("沒有修改");
      try {
        const r = await A.api("PATCH", `/bots/${botId}/settings`, { items: changed });
        A.toast(`已儲存 ${r.changed.length} 項`);
        A.settingsEditor(box, botId, wsId);
      } catch (err) {
        if (err.fields) {
          Object.entries(err.fields).forEach(([k, m]) => { const x = A.$(`[data-e="${k}"]`, box); if (x) { x.textContent = m; const det = x.closest("details"); if (det) det.open = true; } });
          const first = A.$$("[data-e]", box).find((x) => x.textContent);
          if (first) first.closest(".fld").scrollIntoView({ behavior: "smooth", block: "center" });
        }
        throw err;
      }
    });
    dirty();
  };

  // 欄位標題：說明文字的第一段（冒號前）比 key 好懂
  function label(k, s) {
    if (LABELS[k]) return LABELS[k];
    const n = (s && s.note) || "";
    const m = n.match(/^([^：:（(，,。]{1,16})[：:（(]/);
    return m ? m[1] : n && n.length <= 14 ? n : k;
  }
})();
