/* 機器人：設定、知識庫掛載、Skill、對照表、邀請碼、流程、測試對話、紀錄、統計、流程控制台、評測 */
A.views.bot = async function (botId, tab) {
  const b = await A.api("GET", `/bots/${botId}`);
  const ws = A.me.workspaces.find((w) => w.id === b.workspace_id) || {};
  const admin = A.can(b.workspace_id, ["admin"], botId);
  A.crumbs([[ws.name || "工作區", "#/ws/" + b.workspace_id], [b.name, "#/bot/" + botId]]);
  const T = admin ? [["settings", "設定"], ["knowledge", "知識庫"], ["skills", "Skill"], ["lists", "對照表"], ["invites", "邀請碼"], ["flows", "流程"], ["test", "測試對話"],
    ["logs", "紀錄"], ["stats", "統計"], ["console", "流程控制台"], ["eval", "評測"]]
    : (A.can(b.workspace_id, ["analyst"], botId) ? [["logs", "紀錄"], ["stats", "統計"]] : []).concat([["eval", "評測"]]);   // reviewer 看不到對話原文
  if (!T.find((x) => x[0] === tab)) tab = T[0][0];
  const v = A.$("#view");
  v.innerHTML = `<div class="row" style="justify-content:space-between"><h2>${A.esc(b.name)} <span class="note">${A.esc(b.slug)}</span> ${b.disabled ? A.pill("disabled", "已停用") : ""}</h2>
    ${admin ? `<div class="row"><button class="btn sm" id="bRename" type="button">改名</button><button class="btn sm" id="bToggle" type="button">${b.disabled ? "啟用" : "停用"}</button></div>` : ""}</div>` +
    A.tabs("#/bot/" + botId, T, tab) + `<div id="pane"></div>`;
  const p = A.$("#pane");
  if (admin) {
    A.$("#bRename").onclick = () => { const n = prompt("新名稱", b.name); if (n) A.act(null, () => A.api("PATCH", `/bots/${botId}`, { name: n }), "已改名").then(() => A.go(location.hash)); };
    A.$("#bToggle").onclick = () => confirm(b.disabled ? "啟用後聊天頁可以使用，確定？" : "停用後聊天頁會顯示「服務暫停」，確定？") &&
      A.act(null, () => A.api("PATCH", `/bots/${botId}`, { disabled: !b.disabled }), "已更新").then(() => A.go(location.hash));
  }
  const V = { settings, knowledge, skills, lists, invites, flows, test, logs, stats, console: flowConsole, eval: () => A.evalView(p, b) };
  await V[tab]();

  async function settings() { await A.settingsEditor(p, botId, b.workspace_id); }

  async function knowledge() {
    const [cur, av] = await Promise.all([A.api("GET", `/bots/${botId}/knowledge`), A.api("GET", `/bots/${botId}/knowledge/available`)]);
    let rows = cur.items.map((x) => ({ ...x }));
    const draw = () => {
      const free = av.items.filter((k) => !rows.find((r) => r.kb_id === k.id));
      p.innerHTML = `<p class="note">機器人回答時只會檢索這裡掛的知識庫。「最少段數」保證每次至少從這個庫取幾段；「權重」放大或縮小這個庫的分數。同一個機器人掛的知識庫要用同一個向量模型。</p>
        <div class="tablewrap"><table><thead><tr><th>知識庫</th><th>標籤（給模型看的來源名稱）</th><th>最少段數</th><th>權重</th><th>啟用</th><th></th></tr></thead><tbody>${rows.map((r, i) =>
          `<tr><td><b>${A.esc(r.name)}</b><div class="note mono">${A.esc(r.embed_model)}・${r.embed_dim}</div></td><td><input type="text" data-f="label" data-i="${i}" value="${A.esc(r.label)}"></td>
          <td><input type="number" data-f="min_hits" data-i="${i}" value="${r.min_hits}" min="0" max="10" style="width:70px"></td><td><input type="number" data-f="weight" data-i="${i}" value="${r.weight}" step="0.1" min="0.1" max="3" style="width:80px"></td>
          <td><input type="checkbox" data-f="enabled" data-i="${i}" ${r.enabled ? "checked" : ""}></td><td><button class="btn sm danger" data-del="${i}" type="button">卸下</button></td></tr>`).join("") || `<tr><td colspan="6" class="note">還沒有掛任何知識庫</td></tr>`}</tbody></table></div>
        <div class="row" style="margin-top:10px"><select id="kAdd" style="max-width:360px"><option value="">— 選擇要掛上的知識庫 —</option>${free.map((k) => `<option value="${k.id}" ${k.compatible ? "" : "disabled"}>${A.esc(k.name)}（${A.esc(k.embed_model)}・${k.embed_dim}）${k.compatible ? "" : "－向量模型不同"}</option>`).join("")}</select>
        <button class="btn" id="kAddB" type="button">掛上</button><span style="flex:1"></span><button class="btn pri" id="kSave" type="button">儲存</button></div>`;
      A.$$("[data-f]", p).forEach((x) => (x.onchange = () => { const r = rows[+x.dataset.i]; r[x.dataset.f] = x.type === "checkbox" ? x.checked : x.type === "number" ? +x.value : x.value; }));
      A.$$("[data-del]", p).forEach((x) => (x.onclick = () => { rows.splice(+x.dataset.del, 1); draw(); }));
      A.$("#kAddB").onclick = () => { const k = av.items.find((x) => x.id === A.$("#kAdd").value); if (k) { rows.push({ kb_id: k.id, name: k.name, label: k.name, min_hits: 0, weight: 1, enabled: true, embed_model: k.embed_model, embed_dim: k.embed_dim }); draw(); } };
      A.$("#kSave").onclick = (e) => A.act(e.target, async () => { const r = await A.api("PUT", `/bots/${botId}/knowledge`, { items: rows }); rows = r.items; draw(); }, "已儲存");
    };
    draw();
  }

  async function skills() {
    const d = await A.api("GET", `/bots/${botId}/skills`);
    p.innerHTML = `<div class="row" style="margin-bottom:10px"><button class="btn pri" id="sNew" type="button">新增 Skill</button><span class="note">分流判斷的主題對到 Skill 的主題就載入；「一律載入」的每次都帶</span>
      <span style="flex:1"></span><button class="btn" id="tImp" type="button">匯入專案範本</button><button class="btn" id="tExp" type="button">匯出專案範本</button><input type="file" id="tFile" accept=".json,application/json" hidden></div>
      ${d.items.length ? `<div class="tablewrap"><table><thead><tr><th>名稱</th><th>主題</th><th>一律載入</th><th>啟用</th><th>版本</th><th>更新</th></tr></thead><tbody>${d.items.map((k) =>
        `<tr class="click" data-id="${k.id}"><td><b>${A.esc(k.name)}</b> <span class="note">${A.esc(k.key)}</span></td><td>${k.topics.map((t) => `<span class="chip">${A.esc(t)}</span>`).join("")}</td>
        <td>${k.always ? "是" : ""}</td><td>${k.enabled ? "是" : A.pill("disabled", "停用")}</td><td>v${k.version}</td><td>${A.fmtTime(k.updated_at)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">還沒有 Skill。可以新增，或匯入專案範本。</div>`}`;
    A.$("#sNew").onclick = () => edit(null);
    A.$$("tr.click", p).forEach((tr) => (tr.onclick = () => edit(d.items.find((x) => x.id === tr.dataset.id))));
    A.$("#tExp").onclick = (e) => A.act(e.target, () => A.download(`/bots/${botId}/template/export`, b.slug + "-template.json"));
    A.$("#tImp").onclick = () => A.$("#tFile").click();
    A.$("#tFile").onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const replace = confirm("要「取代」現有的 Skill、同義詞、禁止用語與轉介資源嗎？\n按「確定」＝取代；按「取消」＝合併（同名的更新，其他保留）");
      await A.act(null, async () => {
        const r = await A.api("POST", `/bots/${botId}/template/apply?replace=${replace}`, await f.text());
        A.toast(`已匯入：設定 ${r.settings}、Skill ${r.skills}、同義詞 ${r.synonyms}、禁止用語 ${r.forbidden}、轉介 ${r.referral}` + (r.skipped.length ? "\n略過：" + r.skipped.join("；") : ""));
      });
      skills();
    };
    function edit(k) {
      const box = A.side.open(k ? "編輯 Skill" : "新增 Skill", `<label for="kn">名稱</label><input type="text" id="kn" value="${A.esc(k ? k.name : "")}">
        <label for="kt">主題 <span class="note">用頓號或逗號分開；分流判斷的主題對到才載入</span></label><input type="text" id="kt" value="${A.esc(k ? k.topics.join("、") : "")}">
        <div class="row" style="margin:10px 0"><label class="inline"><input type="checkbox" id="ka" ${k && k.always ? "checked" : ""}> 一律載入</label><label class="inline"><input type="checkbox" id="ke" ${!k || k.enabled ? "checked" : ""}> 啟用</label></div>
        <label for="kc">內容（給回答模型的指引）</label><textarea id="kc" rows="16">${A.esc(k ? k.content : "")}</textarea>
        <div class="row" style="margin-top:10px">${k ? `<button class="btn danger" id="kd" type="button">刪除</button><button class="btn" id="kv" type="button">版本紀錄</button>` : ""}<span style="flex:1"></span><button class="btn pri" id="ks" type="button">儲存</button></div><div id="kvs"></div>`);
      const body = () => ({ name: A.$("#kn", box).value, topics: A.$("#kt", box).value, always: A.$("#ka", box).checked, enabled: A.$("#ke", box).checked, content: A.$("#kc", box).value });
      A.$("#ks", box).onclick = (e) => A.act(e.target, async () => { k ? await A.api("PUT", `/skills/${k.id}`, body()) : await A.api("POST", `/bots/${botId}/skills`, body()); A.side.close(); skills(); }, "已儲存");
      if (!k) return;
      A.$("#kd", box).onclick = (e) => confirm("確定刪除這個 Skill？") && A.act(e.target, async () => { await A.api("DELETE", `/skills/${k.id}`); A.side.close(); skills(); }, "已刪除");
      A.$("#kv", box).onclick = (e) => A.act(e.target, async () => {
        const vs = (await A.api("GET", `/skills/${k.id}/versions`)).items;
        A.$("#kvs", box).innerHTML = `<h3>版本紀錄</h3>` + vs.map((x) => `<div class="box"><div class="row"><b>v${x.version}</b><span class="note">${A.fmtTime(x.updated_at)}</span><span style="flex:1"></span>${x.version === k.version ? '<span class="note">目前</span>' : `<button class="btn sm" data-rv="${x.version}" type="button">還原成這版</button>`}</div><div class="chunk">${A.esc(x.content.slice(0, 600))}</div></div>`).join("");
        A.$$("[data-rv]", box).forEach((x) => (x.onclick = () => A.act(x, async () => { await A.api("POST", `/skills/${k.id}/restore`, { version: +x.dataset.rv }); A.side.close(); skills(); }, "已還原")));
      });
    }
  }

  async function lists() {
    const L = {
      synonyms: { t: "同義詞", note: "提問者的口語詞 → 知識庫用的概念詞；檢索前自動補上概念詞", cols: [["word", "口語詞"], ["concept", "概念詞"]] },
      forbidden: { t: "禁止用語", note: "回答出現就擋下重寫；類型選「正規式」時用正規表示式比對", cols: [["rule", "用語"], ["kind", "類型"], ["note", "說明"]] },
      referrals: { t: "轉介資源", note: "分流判斷需要轉介時，依類別帶出找誰、怎麼聯絡、現在先做什麼", cols: [["category", "類別"], ["who", "找誰"], ["contact", "聯絡方式"], ["now", "現在先做"]] },
    };
    const data = {};
    await Promise.all(Object.keys(L).map(async (k) => (data[k] = (await A.api("GET", `/bots/${botId}/${k}`)).items)));
    p.innerHTML = Object.entries(L).map(([k, c]) => `<div class="box" data-l="${k}"><h4>${c.t} <span class="note">${c.note}</span></h4><div class="lt"></div>
      <div class="row" style="margin-top:8px"><button class="btn sm" data-add type="button">新增一列</button><span style="flex:1"></span><button class="btn pri sm" data-save type="button">儲存${c.t}</button></div></div>`).join("");
    Object.entries(L).forEach(([k, c]) => {
      const box = A.$(`[data-l="${k}"]`, p);
      const rows = data[k];
      const draw = () => {
        A.$(".lt", box).innerHTML = `<table><thead><tr>${c.cols.map((x) => `<th>${x[1]}</th>`).join("")}<th></th></tr></thead><tbody>${rows.map((r, i) => `<tr>${c.cols.map(([f]) =>
          `<td>${f === "kind" ? `<select data-f="kind" data-i="${i}"><option value="word" ${r.kind !== "regex" ? "selected" : ""}>字詞</option><option value="regex" ${r.kind === "regex" ? "selected" : ""}>正規式</option></select>` : `<input type="text" data-f="${f}" data-i="${i}" value="${A.esc(r[f] || "")}">`}</td>`).join("")}<td><button class="btn sm danger" data-del="${i}" type="button">刪</button></td></tr>`).join("") || `<tr><td colspan="${c.cols.length + 1}" class="note">（空）</td></tr>`}</tbody></table>`;
        A.$$("[data-f]", box).forEach((x) => (x.oninput = x.onchange = () => (rows[+x.dataset.i][x.dataset.f] = x.value)));
        A.$$("[data-del]", box).forEach((x) => (x.onclick = () => { rows.splice(+x.dataset.del, 1); draw(); }));
      };
      A.$("[data-add]", box).onclick = () => { rows.push(Object.fromEntries(c.cols.map(([f]) => [f, f === "kind" ? "word" : ""]))); draw(); };
      A.$("[data-save]", box).onclick = (e) => A.act(e.target, async () => { const r = await A.api("PUT", `/bots/${botId}/${k}`, { items: rows }); rows.splice(0, rows.length, ...r.items); draw(); }, "已儲存");
      draw();
    });
  }

  async function invites() {
    const [d, s] = await Promise.all([A.api("GET", `/bots/${botId}/invite-codes`), A.api("GET", `/bots/${botId}/settings`)]);
    const on = String((s.items.find((x) => x.key === "enable_invite") || {}).value).toUpperCase() !== "FALSE";
    p.innerHTML = `<div class="box">${on ? "邀請碼<b>已開啟</b>：聊天頁要輸入有效的邀請碼才能使用。" : `<span class="warn">邀請碼<b>已關閉</b>：任何人拿到網址就能使用，請在「設定」設好每日上限。</span>`}
      <span class="note">（在「設定 › 版本與基本」切換）</span></div>
      <div class="box"><h4>新增邀請碼</h4><div class="grid3"><div><label for="ic">邀請碼 <span class="note">留空自動產生</span></label><input type="text" id="ic"></div>
      <div><label for="iu">單位（紀錄與統計用）</label><input type="text" id="iu"></div><div><label for="ie">到期日 <span class="note">可空</span></label><input type="date" id="ie"></div></div>
      <div class="row" style="margin-top:8px"><label class="inline"><input type="checkbox" id="it"> 可看流程紀錄（給開發測試用）</label><span style="flex:1"></span><button class="btn pri" id="ia" type="button">新增</button></div></div>
      <div class="tablewrap"><table><thead><tr><th>邀請碼</th><th>單位</th><th>到期</th><th>流程紀錄</th><th>啟用</th><th></th></tr></thead><tbody>${d.items.map((c) =>
        `<tr><td class="mono"><b>${A.esc(c.code)}</b></td><td>${A.esc(c.unit)}</td><td>${c.expires_at ? c.expires_at.slice(0, 10) : ""}</td>
        <td><input type="checkbox" data-t="${c.id}" ${c.trace ? "checked" : ""}></td><td><input type="checkbox" data-e="${c.id}" ${c.enabled ? "checked" : ""}></td>
        <td><button class="btn sm danger" data-del="${c.id}" type="button">刪除</button></td></tr>`).join("") || '<tr><td colspan="6" class="note">還沒有邀請碼</td></tr>'}</tbody></table></div>`;
    A.$("#ia").onclick = (e) => A.act(e.target, async () => {
      const ex = A.$("#ie").value;
      await A.api("POST", `/bots/${botId}/invite-codes`, { code: A.$("#ic").value, unit: A.$("#iu").value, trace: A.$("#it").checked, expires_at: ex ? ex + "T23:59:59+08:00" : null });
      invites();
    }, "已新增");
    A.$$("[data-t]", p).forEach((x) => (x.onchange = () => A.act(null, () => A.api("PATCH", `/invite-codes/${x.dataset.t}`, { trace: x.checked }), "已更新")));
    A.$$("[data-e]", p).forEach((x) => (x.onchange = () => A.act(null, () => A.api("PATCH", `/invite-codes/${x.dataset.e}`, { enabled: x.checked }), "已更新")));
    A.$$("[data-del]", p).forEach((x) => (x.onclick = () => confirm("確定刪除？用這組邀請碼的人將無法再使用。") && A.act(x, () => A.api("DELETE", `/invite-codes/${x.dataset.del}`), "已刪除").then(invites)));
  }

  async function flows() {
    const d = await A.api("GET", `/bots/${botId}/flows`);
    p.innerHTML = `<p class="note">同一個機器人可以有多條流程，共用知識庫與 Skill，但可以各自覆蓋部分設定（模型、檢索參數、開關等）。聊天頁網址加 <span class="mono">&amp;flow=&lt;流程 ID&gt;</span> 就會用那條流程；沒帶就用預設流程。</p>
      <div class="tablewrap"><table><thead><tr><th>名稱</th><th>流程 ID</th><th>預設</th><th></th></tr></thead><tbody>${d.items.map((f) =>
        `<tr><td><b>${A.esc(f.name)}</b></td><td class="mono">${f.id}</td><td>${f.is_default ? "是" : `<button class="btn sm" data-def="${f.id}" type="button">設為預設</button>`}</td>
        <td class="row"><button class="btn sm" data-ov="${f.id}" type="button">覆蓋設定</button><button class="btn sm" data-copy="${f.id}" type="button">複製</button>${f.is_default ? "" : `<button class="btn sm danger" data-del="${f.id}" type="button">刪除</button>`}</td></tr>`).join("")}</tbody></table></div>
      <div class="row" style="margin-top:10px"><input type="text" id="fn" placeholder="新流程名稱" style="max-width:260px"><button class="btn" id="fa" type="button">新增流程</button></div>`;
    A.$("#fa").onclick = (e) => A.act(e.target, () => A.api("POST", `/bots/${botId}/flows`, { name: A.$("#fn").value }), "已新增").then(flows);
    A.$$("[data-def]", p).forEach((x) => (x.onclick = () => A.act(x, () => A.api("PATCH", `/flows/${x.dataset.def}`, { is_default: true }), "已設為預設").then(flows)));
    A.$$("[data-copy]", p).forEach((x) => (x.onclick = () => { const n = prompt("新流程名稱"); if (n) A.act(x, () => A.api("POST", `/bots/${botId}/flows`, { name: n, copy_from: x.dataset.copy }), "已複製").then(flows); }));
    A.$$("[data-del]", p).forEach((x) => (x.onclick = () => confirm("確定刪除這條流程？") && A.act(x, () => A.api("DELETE", `/flows/${x.dataset.del}`), "已刪除").then(flows)));
    A.$$("[data-ov]", p).forEach((x) => (x.onclick = () => override(d.items.find((f) => f.id === x.dataset.ov))));
    async function override(f) {
      const [o, s] = await Promise.all([A.api("GET", `/flows/${f.id}/settings`), A.api("GET", `/bots/${botId}/settings`)]);
      const bv = Object.fromEntries(s.items.map((x) => [x.key, x.value]));
      const cur = Object.fromEntries(o.items.map((x) => [x.key, x.value]));
      const box = A.side.open("覆蓋設定：" + f.name, `<p class="note">留空＝沿用機器人設定（右邊灰字）。只有這些設定可以在流程層覆蓋。</p>
        <table><tbody>${o.overridable.map((k) => `<tr><td class="mono" style="width:40%">${A.esc(k)}</td><td><input type="text" data-k="${k}" value="${A.esc(cur[k] || "")}" placeholder="${A.esc(bv[k] == null ? "" : bv[k])}"></td></tr>`).join("")}</tbody></table>
        <div class="row end" style="margin-top:10px"><button class="btn pri" id="os" type="button">儲存</button></div>`);
      A.$("#os", box).onclick = (e) => A.act(e.target, async () => {
        const items = A.$$("[data-k]", box).filter((x) => (x.value || null) !== (cur[x.dataset.k] || null)).map((x) => ({ key: x.dataset.k, value: x.value.trim() === "" ? null : x.value }));
        if (!items.length) return A.toast("沒有修改");
        await A.api("PATCH", `/flows/${f.id}/settings`, { items });
        A.side.close();
      }, "已儲存");
    }
  }

  async function test() {
    const fl = await A.api("GET", `/bots/${botId}/flows`);
    const S = { history: [], nameMap: {}, followups: 0, answered: 0, session: "", turn: 0 };
    p.innerHTML = `<div class="grid2"><div><div class="row" style="margin-bottom:8px"><select id="tf" style="max-width:240px">${fl.items.map((f) => `<option value="${f.id}" ${f.is_default ? "selected" : ""}>${A.esc(f.name)}</option>`).join("")}</select>
      <button class="btn" id="tn" type="button">開新對話</button><span class="note">不需邀請碼；不算進紀錄與統計</span></div>
      <div id="tm" class="box" style="min-height:280px;max-height:60vh;overflow:auto"><p class="note">輸入一個問題試試看。</p></div>
      <form id="tform" class="row"><textarea id="tq" rows="3" style="flex:1" placeholder="輸入問題（Ctrl+Enter 送出）"></textarea><button class="btn pri" type="submit">送出</button></form></div>
      <div><h4>流程紀錄</h4><div id="tt" class="box" style="max-height:75vh;overflow:auto"><p class="note">送出後顯示每一步做了什麼</p></div></div></div>`;
    const restore = (s) => { let o = String(s == null ? "" : s); Object.entries(S.nameMap).forEach(([n, l]) => (o = o.split(l).join(n))); return o; };
    A.$("#tn").onclick = () => { Object.assign(S, { history: [], nameMap: {}, followups: 0, answered: 0, session: "", turn: 0 }); A.$("#tm").innerHTML = '<p class="note">已開新對話。</p>'; A.$("#tt").innerHTML = ""; };
    A.$("#tq").onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) A.$("#tform").requestSubmit(); };
    A.$("#tform").onsubmit = async (e) => {
      e.preventDefault();
      const q = A.$("#tq").value.trim();
      if (!q) return;
      const btn = A.$("#tform button");
      A.$("#tm").insertAdjacentHTML("beforeend", `<div class="msg me">${A.esc(q)}</div>`);
      A.$("#tq").value = "";
      btn.disabled = true;
      try {
        S.turn++;
        const r = await A.api("POST", `/bots/${botId}/test-chat`, { message: q, history: S.history, nameMap: S.nameMap, followups: S.followups, answered: S.answered, session: S.session, turn: S.turn, flow_id: A.$("#tf").value });
        S.session = r.session; S.nameMap = r.nameMap || S.nameMap;
        S.followups = r.type === "followup" ? r.followups : r.newCase ? 0 : S.followups;
        S.answered = r.answered != null ? r.answered : S.answered;
        S.history.push({ role: "teacher", text: r.deidText || q });
        const html = replyHtml(r, restore);
        S.history.push({ role: "bot", text: r.answer ? AnswerView.summary(r) : r.type === "followup" ? (r.questions || []).join(" ") : r.message || "" });
        A.$("#tm").insertAdjacentHTML("beforeend", `<div class="msg"><div class="note">${A.esc((TraceView.TYPE[r.type] || [r.type])[0])}・${(r.ms / 1000).toFixed(1)} 秒</div>${html}</div>`);
        A.$("#tm").scrollTop = 1e9;
        A.$("#tt").innerHTML = TraceView.render(r);
      } catch (err) { A.$("#tm").insertAdjacentHTML("beforeend", `<div class="msg err">${A.esc(err.message)}</div>`); }
      finally { btn.disabled = false; }
    };
  }

  /** 測試對話的回覆：回答用 AnswerView（已跳脫）；追問、轉介、訊息自己組 */
  function replyHtml(r, restore) {
    const R = (x) => A.esc(restore(x));
    if (r.answer) return AnswerView.text(r, restore) + (r.answer.notice ? `\n\n<span class="warn">${A.esc(r.answer.notice)}</span>` : "");
    if (r.type === "followup") return R(r.empathy) + "\n" + (r.questions || []).map((q) => "・" + R(q)).join("\n");
    if (r.type === "referral") { const f = r.referral || {}; return `<b class="err">${A.esc(f.category || "")}・${A.esc(f.level || "")}</b>\n${R(f.reason)}\n找：${A.esc(f.who || "")} ${A.esc(f.contact || "")}\n現在先：${A.esc(f.now || "")}`; }
    return R(r.message);
  }

  async function logs() {
    let next = null;
    p.innerHTML = `<div class="row" style="margin-bottom:10px"><select id="ld" style="width:auto"><option value="7">近 7 天</option><option value="30" selected>近 30 天</option><option value="">全部</option></select>
      <select id="lt" style="width:auto"><option value="">全部類型</option>${Object.entries(TraceView.TYPE).map(([k, v]) => `<option value="${k}">${v[0]}</option>`).join("")}</select>
      <input type="search" id="lq" placeholder="關鍵字" style="max-width:200px"><input type="search" id="lu" placeholder="單位" style="max-width:140px">
      <label class="inline"><input type="checkbox" id="lr"> 只看重寫過的</label><label class="inline"><input type="checkbox" id="lx"> 含後台測試</label>
      <button class="btn" id="lg" type="button">查詢</button><span style="flex:1"></span><button class="btn" id="lc" type="button">匯出 CSV</button></div>
      <div class="tablewrap"><table><thead><tr><th>時間</th><th>單位</th><th>訊息（去識別化）</th><th>類型</th><th>檢索</th><th>引用</th><th>重寫</th><th>回饋</th><th>耗時</th></tr></thead><tbody id="lb"></tbody></table></div>
      <div class="row end" style="margin-top:8px"><button class="btn" id="lm" type="button" hidden>載入更多</button></div>`;
    const qs = () => { const q = new URLSearchParams(); const dd = A.$("#ld").value; if (dd) q.set("days", dd); [["type", "#lt"], ["q", "#lq"], ["unit", "#lu"]].forEach(([k, s]) => A.$(s).value && q.set(k, A.$(s).value)); if (A.$("#lr").checked) q.set("rewritten", "true"); if (A.$("#lx").checked) q.set("include_test", "true"); return q; };
    async function load(reset) {
      const q = qs(); q.set("limit", 50);
      if (!reset && next) q.set("cursor", next);
      const d = await A.api("GET", `/bots/${botId}/logs?` + q);
      next = d.next;
      const html = d.items.map((r) => `<tr class="click" data-id="${r.id}"><td>${A.fmtTime(r.at)}</td><td>${A.esc(r.unit)}</td><td>${A.esc(r.message)}</td><td>${A.esc((TraceView.TYPE[r.type] || [r.type])[0])}</td>
        <td>${r.retrieved}</td><td>${r.cited}</td><td>${r.rewritten ? "是" : ""}</td><td>${r.fb === "up" ? "👍" : r.fb === "down" ? "👎" : ""}${r.copied ? " 📋" : ""}</td><td>${(r.ms / 1000).toFixed(1)}s</td></tr>`).join("");
      A.$("#lb").innerHTML = reset ? html || '<tr><td colspan="9" class="note">沒有紀錄</td></tr>' : A.$("#lb").innerHTML + html;
      A.$("#lm").hidden = !next;
      A.$$("tr.click", p).forEach((tr) => (tr.onclick = () => detail(tr.dataset.id)));
    }
    async function detail(id) {
      const r = await A.api("GET", `/logs/${id}`);
      A.side.open("紀錄 #" + id, `<div class="msg me">${A.esc(r.message)}</div><div class="note">${A.fmtTime(r.at)}｜${A.esc(r.unit)}｜第 ${r.turn} 輪｜${A.esc((TraceView.TYPE[r.type] || [r.type])[0])}</div>
        <h4 style="margin-top:12px">流程紀錄</h4>${TraceView.render({ ...r, trace: r.trace, metrics: { retrieved: r.retrieved, top: r.top, cited: r.cited, skills: r.skills }, rewritten: r.rewritten })}`);
    }
    A.$("#lg").onclick = () => load(true);
    A.$("#lm").onclick = () => load(false);
    A.$("#lc").onclick = (e) => A.act(e.target, () => A.download(`/bots/${botId}/logs/export.csv?` + qs(), b.slug + "-logs.csv"));
    await load(true);
  }

  async function stats() {
    p.innerHTML = `<div class="row" style="margin-bottom:12px"><select id="sd" style="width:auto"><option value="7">近 7 天</option><option value="30" selected>近 30 天</option><option value="90">近 90 天</option></select></div><div id="sb"></div>`;
    const load = async () => {
      const d = await A.api("GET", `/bots/${botId}/stats?days=${A.$("#sd").value}`);
      const s = d.stats;
      const k = (v, t, n) => `<div class="kpi"><b>${v == null ? "—" : v}</b><span>${t}</span>${n ? `<div class="note">${n}</div>` : ""}</div>`;
      A.$("#sb").innerHTML = `<div class="kpis">${k(s.turns, "回合")}${k(s.sessions, "對話")}${k(A.pct(s.hitRate), "有找到知識", "檢索到至少一段的比例")}${k(A.pct(s.citeRate), "引用率", "採用的段落有被引用的比例")}
        ${k(s.avgTop, "平均最高相似度")}${k(A.pct(s.rewriteRate), "重寫率")}${k(A.pct(s.upRate), "有幫助", `👍 ${s.ups}／👎 ${s.downs}`)}${k(s.copied, "複製次數")}${k(s.avgMs == null ? null : (s.avgMs / 1000).toFixed(1) + " 秒", "平均耗時")}</div>
        <div class="grid2"><div class="box"><h4>回應類型</h4>${Object.entries(s.byType).map(([t, n]) => `<div class="row"><span>${A.esc((TraceView.TYPE[t] || [t])[0])}</span><span style="flex:1"></span><b>${n}</b></div>`).join("") || '<p class="note">—</p>'}</div>
        <div class="box"><h4>常被引用的文件</h4>${d.topDocs.map((x) => `<div class="row"><span>${A.esc(x.title)}</span><span style="flex:1"></span><b>${x.n}</b></div>`).join("") || '<p class="note">—</p>'}</div>
        <div class="box"><h4>知識缺口 <span class="note">保守回答、AI 補足、或沒找到知識的提問（最近 30 筆）</span></h4>${d.gaps.map((g) => `<div class="chunk">${A.esc(g.message)}<div class="note">${A.fmtTime(g.at)}｜最高相似度 ${g.top}</div></div>`).join("") || '<p class="note">沒有</p>'}</div>
        <div class="box"><h4>品質</h4><p class="note">缺口類型</p>${Object.entries(d.quality.gapKinds).map(([a, n]) => `<span class="chip">${A.esc(a)} ${n}</span>`).join("") || "—"}
          <p class="note">AI 檢查問題</p>${Object.entries(d.quality.l5).map(([a, n]) => `<span class="chip">${A.esc(a)} ${n}</span>`).join("") || "—"}
          <p class="note">格式穩定度事件</p>${Object.entries(d.stability).map(([a, n]) => `<span class="chip">${A.esc(a)} ${n}</span>`).join("") || "—"}</div></div>`;
    };
    A.$("#sd").onchange = load;
    await load();
  }

  async function flowConsole() {
    const NAMES = { deid: "去識別化", triage: "L4 分流", state: "狀態判斷", skill: "載入 Skill", kg: "知識圖譜", retrieve: "檢索", rerank: "重排與配額", assemble: "組 Prompt", answer: "生成回答", check: "檢查", final: "組合／降級" };
    p.innerHTML = `<div class="row" style="margin-bottom:12px"><select id="cd" style="width:auto"><option value="7">近 7 天</option><option value="30" selected>近 30 天</option></select>
      <span style="flex:1"></span><input type="text" id="cq" placeholder="試跑：輸入一個問題" style="max-width:360px"><button class="btn" id="cr" type="button">試跑</button></div><div id="cb"></div><div id="ct"></div>`;
    const load = async () => {
      const d = await A.api("GET", `/bots/${botId}/flow-stats?days=${A.$("#cd").value}`);
      const st = (k) => d.steps[k] || { runs: 0, avg: null };
      A.$("#cb").innerHTML = `<p class="note">近期 ${d.turns} 回合，平均 ${d.avgMs == null ? "—" : (d.avgMs / 1000).toFixed(1) + " 秒"}。Skill ${d.skills}（一律載入 ${d.skillsAlways}）・可檢索段落 ${d.chunks}・同義詞 ${d.synonyms}・禁止用語 ${d.forbidden}・轉介 ${d.referral}</p>
        <div class="cards">${Object.keys(NAMES).map((k) => `<div class="card"><b>${NAMES[k]}</b><span class="note">執行 ${st(k).runs} 次・平均 ${st(k).avg == null ? "—" : (st(k).avg / 1000).toFixed(2) + " 秒"}</span>
          ${k === "retrieve" ? `<div class="note">有找到 ${d.retrieval.hit}／${d.retrieval.n}・平均最高相似度 ${d.retrieval.topN ? (d.retrieval.top / d.retrieval.topN).toFixed(3) : "—"}</div>` : ""}
          ${k === "rerank" ? `<div class="note">採用 ${d.rerank.used}・失敗 ${d.rerank.failed}・關閉 ${d.rerank.off}</div>` : ""}
          ${k === "answer" ? `<div class="note">重寫 ${d.answer.rewrite}／${d.answer.n}・格式問題 ${d.answer.format}</div>` : ""}
          ${k === "check" ? `<div class="note">有問題 ${d.check.l5}・嚴重 ${d.check.severe}・最後保守回答 ${d.check.conservative}</div>` : ""}
          ${k === "final" ? `<div class="note">知識缺口 ${d.gap.n}：${Object.entries(d.gap.byOutcome).map(([a, n]) => A.esc(a) + " " + n).join("、") || "—"}</div>` : ""}</div>`).join("")}</div>`;
    };
    A.$("#cd").onchange = load;
    A.$("#cr").onclick = (e) => A.act(e.target, async () => {
      const r = await A.api("POST", `/bots/${botId}/flow-trial`, { message: A.$("#cq").value });
      A.$("#ct").innerHTML = `<h3>試跑結果：${A.esc((TraceView.TYPE[r.type] || [r.type])[0])}・${(r.ms / 1000).toFixed(1)} 秒</h3><div class="tablewrap"><table><tbody>${r.steps.map((s) => `<tr><td>${A.esc(s.step)}</td><td style="width:120px">${(s.ms / 1000).toFixed(2)} 秒</td></tr>`).join("")}</tbody></table></div>`;
    });
    await load();
  }
};
