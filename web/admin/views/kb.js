/* 知識庫：文件（上傳、審核、段落）、知識圖譜、知識庫設定 */
A.views.kb = async function (kbId, tab) {
  const k = await A.api("GET", `/kbs/${kbId}`);
  const ws = A.me.workspaces.find((w) => w.id === k.workspace_id) || {};
  const wsRoles = ws.roles || [];
  const admin = wsRoles.includes("owner") || wsRoles.includes("admin");
  const reviewer = admin || wsRoles.includes("reviewer");
  A.crumbs([[ws.name || "工作區", "#/ws/" + k.workspace_id], ["知識庫", "#/ws/" + k.workspace_id + "/kbs"], [k.name, "#/kb/" + kbId]]);
  const T = [["docs", "文件"], ["kg", "知識圖譜"]].concat(admin ? [["settings", "知識庫設定"]] : []);
  if (!reviewer) T.splice(0, 2);
  if (!T.find((x) => x[0] === tab)) tab = (T[0] || ["docs"])[0];
  const v = A.$("#view");
  v.innerHTML = `<h2>${A.esc(k.name)} <span class="note">${A.esc(k.slug)}・${A.esc(k.embed_model)}・${k.embed_dim} 維・${k.documents} 份文件・${k.chunks} 段</span></h2>` +
    A.tabs("#/kb/" + kbId, T, tab) + `<div id="pane"></div>`;
  const p = A.$("#pane");
  if (!reviewer) { p.innerHTML = `<div class="empty">你可以看到這個知識庫掛在哪些機器人上，但沒有管理文件的權限。</div>`; return; }
  await ({ docs, kg, settings }[tab])();

  async function docs() {
    let status = "", next = null;
    p.innerHTML = `<div class="drop" id="drop">把檔案拖到這裡，或 <button class="btn sm" id="pick" type="button">選擇檔案</button>（PDF、Word、PowerPoint、Excel、純文字；單檔 50 MB 以內，可多選）
        <input type="file" id="files" multiple hidden accept=".pdf,.docx,.pptx,.xlsx,.txt,.md,.csv"></div>
      <div class="grid2" style="margin-top:8px"><div><label for="usrc">出處（套用到這次上傳的檔案）</label><input type="text" id="usrc" placeholder="例如：出版單位、年份"></div>
        <div><label for="uev">證據等級 <span class="note">可空</span></label><input type="text" id="uev"></div></div>
      <details class="box" style="margin-top:10px"><summary>或貼上文字</summary><label for="pt">標題</label><input type="text" id="pt"><label for="px">內容</label><textarea id="px" rows="6"></textarea>
        <div class="row end" style="margin-top:8px"><button class="btn pri" id="pg" type="button">送出</button></div></details>
      <div id="up"></div>
      <div class="row" style="margin:14px 0 8px"><div class="tabs" style="margin:0;border:0">${[["", "全部"], ["processing", "處理中"], ["pending", "待審核"], ["approved", "已核可"], ["disabled", "停用"], ["failed", "失敗"]].map(([s, t]) => `<a href="javascript:void 0" data-s="${s}" class="${s === "" ? "on" : ""}">${t}</a>`).join("")}</div>
        <span style="flex:1"></span>${admin ? '<button class="btn" id="rep" type="button">補算缺少的向量</button>' : ""}</div>
      <div class="tablewrap"><table><thead><tr><th>標題</th><th>檔案</th><th>狀態</th><th>段落</th><th>上傳時間</th></tr></thead><tbody id="db"></tbody></table></div>
      <div class="row end" style="margin-top:8px"><button class="btn" id="dm" type="button" hidden>載入更多</button></div>`;
    const drop = A.$("#drop");
    A.$("#pick").onclick = () => A.$("#files").click();
    A.$("#files").onchange = (e) => upload(Array.from(e.target.files));
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); upload(Array.from(e.dataTransfer.files)); };
    A.$("#pg").onclick = (e) => A.act(e.target, async () => {
      const r = await A.api("POST", `/kbs/${kbId}/documents`, { title: A.$("#pt").value, text: A.$("#px").value, source_note: A.$("#usrc").value, evidence: A.$("#uev").value });
      A.$("#px").value = "";
      track(A.$("#pt").value || "貼上的文字", r.job_id);
    });
    A.$$("[data-s]", p).forEach((a) => (a.onclick = () => { status = a.dataset.s; A.$$("[data-s]", p).forEach((x) => x.classList.toggle("on", x === a)); load(true); }));
    const rep = A.$("#rep");
    if (rep) rep.onclick = () => A.act(rep, async () => { const r = await A.api("POST", `/kbs/${kbId}/repair`); A.toast(r.fixed ? `已補上 ${r.fixed} 段的向量` : "沒有缺向量的段落"); });
    A.$("#dm").onclick = () => load(false);

    async function upload(files) {
      for (const f of files) {
        const fd = new FormData();
        fd.append("file", f);
        fd.append("source_note", A.$("#usrc").value);
        fd.append("evidence", A.$("#uev").value);
        try {
          const r = await A.api("POST", `/kbs/${kbId}/documents`, fd);
          track(f.name, r.job_id);
        } catch (e) {
          if (e.code === "duplicate" && confirm(`「${f.name}」和已上傳的「${e.fields.existing.title}」內容相同，仍要再上傳一份嗎？`)) {
            fd.append("force", "1");
            try { const r = await A.api("POST", `/kbs/${kbId}/documents`, fd); track(f.name, r.job_id); } catch (e2) { line(f.name, `<span class="err">${A.esc(e2.message)}</span>`); }
          } else line(f.name, `<span class="err">${A.esc(e.message)}</span>`);
        }
      }
      A.$("#files").value = "";
    }
    function line(name, html) {
      const id = "u" + Math.random().toString(36).slice(2);
      A.$("#up").insertAdjacentHTML("afterbegin", `<div class="row" id="${id}" style="padding:4px 0"><b>${A.esc(name)}</b><span data-st>${html}</span></div>`);
      return A.$("#" + id + " [data-st]");
    }
    async function track(name, jobId) {
      const el = line(name, "處理中…");
      try {
        const j = await A.waitJob(jobId, (x) => (el.textContent = (x.message || "處理中") + "（" + Math.round((x.progress || 0) * 100) + "%）"));
        el.innerHTML = j.status === "done" ? `<span class="ok">${A.esc(j.message)}，請審核後核可</span>` : `<span class="err">${A.esc(j.message)}</span>`;
      } catch (e) { el.innerHTML = `<span class="err">${A.esc(e.message)}</span>`; }
      load(true);
    }
    async function load(reset) {
      const q = new URLSearchParams({ limit: 50 });
      if (status) q.set("status", status);
      if (!reset && next) q.set("cursor", next);
      const d = await A.api("GET", `/kbs/${kbId}/documents?` + q);
      next = d.next;
      const html = d.items.map((x) => `<tr class="click" data-id="${x.id}"><td><b>${A.esc(x.title)}</b>${x.error ? `<div class="note ${x.status === "failed" ? "err" : ""}">${A.esc(x.error)}</div>` : ""}</td><td class="note">${A.esc(x.filename)}</td>
        <td>${A.st(x.status)}</td><td>${x.chunk_count}</td><td>${A.fmtTime(x.uploaded_at)}</td></tr>`).join("");
      A.$("#db").innerHTML = reset ? html || '<tr><td colspan="5" class="note">沒有文件</td></tr>' : A.$("#db").innerHTML + html;
      A.$("#dm").hidden = !next;
      A.$$("#db tr.click").forEach((tr) => (tr.onclick = () => doc(tr.dataset.id)));
    }
    await load(true);

    async function doc(id) {
      const [d, ch] = await Promise.all([A.api("GET", `/documents/${id}`), A.api("GET", `/documents/${id}/chunks`)]);
      const types = ["", "具體做法", "案例", "溝通範例", "原則／背景", "轉介資訊"];
      const box = A.side.open(d.title, `<div class="row">${A.st(d.status)}<span class="note">${A.esc(d.filename)}・${d.chunk_count} 段</span><span style="flex:1"></span><button class="btn sm" id="dl" type="button">下載原始檔</button></div>
        ${d.error ? `<p class="note ${d.status === "failed" ? "err" : "warn"}">${A.esc(d.error)}</p>` : ""}
        <label for="dt">標題</label><input type="text" id="dt" value="${A.esc(d.title)}">
        <div class="grid2"><div><label for="ds">出處</label><input type="text" id="ds" value="${A.esc(d.source_note)}"></div><div><label for="de">證據等級</label><input type="text" id="de" value="${A.esc(d.evidence)}"></div></div>
        <div class="row" style="margin:10px 0"><button class="btn" id="dsave" type="button">儲存</button><span style="flex:1"></span>
          ${["pending", "disabled"].includes(d.status) ? '<button class="btn pri" id="dok" type="button">核可（開始用於回答）</button>' : ""}${d.status === "approved" ? '<button class="btn" id="doff" type="button">停用</button>' : ""}</div>
        ${admin ? `<div class="row"><button class="btn sm" id="dcl" type="button">重新分類段落</button><button class="btn sm" id="dkg" type="button">圖譜標註</button><span style="flex:1"></span><button class="btn sm danger" id="ddel" type="button">刪除文件</button></div>` : ""}
        <h4 style="margin-top:14px">段落</h4>${ch.items.map((c) => `<div class="chunk"><div class="row note"><span>#${c.seq}${c.page ? "・" + A.esc(c.page) : ""}</span>${c.has_vector ? "" : '<span class="warn">缺向量</span>'}<span style="flex:1"></span>
          <select data-ct="${c.id}" style="width:auto;padding:2px 6px">${types.map((t) => `<option value="${t}" ${t === c.type ? "selected" : ""}>${t || "（未分類）"}</option>`).join("")}</select></div>${A.esc(c.text)}</div>`).join("") || '<p class="note">沒有段落</p>'}`);
      const refresh = () => { load(true); doc(id); };
      A.$("#dl", box).onclick = (e) => A.act(e.target, () => A.download(`/documents/${id}/file`, d.filename || "file"));
      A.$("#dsave", box).onclick = (e) => A.act(e.target, () => A.api("PATCH", `/documents/${id}`, { title: A.$("#dt", box).value, source_note: A.$("#ds", box).value, evidence: A.$("#de", box).value }), "已儲存").then(refresh);
      const ok = A.$("#dok", box), off = A.$("#doff", box);
      if (ok) ok.onclick = () => A.act(ok, () => A.api("PATCH", `/documents/${id}`, { status: "approved" }), "已核可").then(refresh);
      if (off) off.onclick = () => A.act(off, () => A.api("PATCH", `/documents/${id}`, { status: "disabled" }), "已停用").then(refresh);
      A.$$("[data-ct]", box).forEach((s) => (s.onchange = () => A.act(null, () => A.api("PATCH", `/chunks/${s.dataset.ct}`, { type: s.value }), "已更新")));
      if (!admin) return;
      A.$("#dcl", box).onclick = (e) => A.act(e.target, async () => { const r = await A.api("POST", `/documents/${id}/classify`); A.toast(`已分類 ${r.classified}／${r.chunks} 段`); refresh(); });
      A.$("#dkg", box).onclick = (e) => A.act(e.target, async () => { const r = await A.api("POST", `/documents/${id}/kg-tag`); A.toast(r.skipped || `標註 ${r.links} 筆（主要 ${r.main}），新節點候選 ${r.candidates}、累加 ${r.candidateHits}`); });
      A.$("#ddel", box).onclick = (e) => confirm("確定刪除這份文件與它的所有段落？") && A.act(e.target, async () => { await A.api("DELETE", `/documents/${id}`); A.side.close(); load(true); }, "已刪除");
    }
  }

  async function kg() {
    const d = await A.api("GET", `/kbs/${kbId}/kg`);
    const tools = admin ? `<div class="row" style="margin-bottom:12px"><button class="btn" id="gi" type="button">匯入圖譜 JSON</button><input type="file" id="gf" accept=".json" hidden>
      <button class="btn" id="ge" type="button">匯出圖譜</button><button class="btn" id="gt" type="button">下載 Ontology</button><span style="flex:1"></span><button class="btn" id="gg" type="button">依文件產生草稿</button></div><div id="gout"></div>` : "";
    if (d.empty) {
      p.innerHTML = tools + `<div class="empty">這個知識庫還沒有知識圖譜。可以匯入 knowledge-graph.json，或依已核可文件產生一份草稿、審核後再匯入。</div>`;
      bindTools();
      return;
    }
    const s = d.stats;
    p.innerHTML = tools + `<div class="kpis">${[[s.nodes, "節點"], [s.edges, "關係"], [s.links, "段落標註"], [s.covered, "有核可段落的節點"], [s.pending, "待審候選"], [s.sources, "來源"]].map(([a, b]) => `<div class="kpi"><b>${a}</b><span>${b}</span></div>`).join("")}</div>
      ${d.todo.length ? `<div class="box"><h4>還有段落沒標註的文件</h4>${d.todo.map((x) => `<div class="row"><span>${A.esc(x.title)}</span>${A.st(x.status)}<span class="note">${x.todo} 段</span><span style="flex:1"></span>${admin ? `<button class="btn sm" data-tag="${x.id}" type="button">標註</button>` : ""}</div>`).join("")}</div>` : ""}
      <h3>新節點候選 <span class="note">文件標註時，段落主題在圖譜裡找不到合適節點就會提出；同名的會累加次數</span></h3>
      ${d.cands.map((c) => `<div class="box" data-c="${c.id}"><div class="row"><b>${A.esc(c.name)}</b><span class="chip">${A.esc(c.type)}</span><span class="note">出現 ${c.count} 次・${A.esc(c.doc)}</span></div>
        <div>${A.esc(c.def)}</div>${c.parentLabel ? `<div class="note">建議上層：${A.esc(c.parentLabel)}</div>` : ""}${c.rel ? `<div class="note">建議關係：→${A.esc(c.rel.label)}→ ${A.esc(c.rel.targetLabel)}</div>` : ""}
        ${c.reason ? `<div class="note">理由：${A.esc(c.reason)}</div>` : ""}${c.warns.map((w) => `<div class="warn">${A.esc(w)}</div>`).join("")}${c.snippet ? `<div class="chunk note">${A.esc(c.snippet)}</div>` : ""}
        <div class="row"><button class="btn sm pri" data-a="approve" type="button">核可成新節點</button><select data-t style="width:auto"><option value="">合併到…</option>${d.nodes.filter((n) => n.status === "active").map((n) => `<option value="${A.esc(n.key)}">${A.esc(n.label)}</option>`).join("")}</select>
        <button class="btn sm" data-a="merge" type="button">合併</button><button class="btn sm" data-a="skip" type="button">略過</button></div></div>`).join("") || '<p class="note">沒有待審候選</p>'}
      <h3>節點與覆蓋度 <span class="note">「主要」＝直接討論這個節點的段落數；「核可」＝其中已核可的；覆蓋度 0 的節點代表知識庫缺這塊</span></h3>
      <input type="search" id="gq" placeholder="搜尋節點" style="max-width:260px;margin-bottom:8px">
      <div class="tablewrap" style="max-height:60vh"><table><thead><tr><th>節點</th><th>類型</th><th>路徑</th><th>主要</th><th>次要</th><th>核可</th><th>可照做</th></tr></thead><tbody id="gn"></tbody></table></div>`;
    const draw = () => {
      const q = A.$("#gq").value.trim();
      A.$("#gn").innerHTML = d.nodes.filter((n) => !q || (n.label + n.syn + n.path).includes(q)).map((n) => `<tr><td><b>${A.esc(n.label)}</b>${n.syn ? `<div class="note">${A.esc(n.syn)}</div>` : ""}${n.status !== "active" ? " " + A.pill("disabled", "停用") : ""}</td>
        <td>${A.esc(n.type)}</td><td class="note">${A.esc(n.path)}</td><td>${n.all}</td><td>${n.sub}</td><td class="${n.ok ? "" : "err"}">${n.ok}</td><td>${n.act}</td></tr>`).join("");
    };
    A.$("#gq").oninput = draw;
    draw();
    A.$$("[data-c]", p).forEach((box) => A.$$("[data-a]", box).forEach((btn) => (btn.onclick = () => A.act(btn, async () => {
      const a = btn.dataset.a, opts = {};
      if (a === "merge") { opts.target = A.$("[data-t]", box).value; if (!opts.target) throw new Error("請先選擇要合併到的節點"); }
      if (a === "approve") { const n = prompt("節點名稱", A.$("b", box).textContent); if (!n) return; opts.name = n; }
      await A.api("POST", `/kg/candidates/${box.dataset.c}`, { action: a, opts });
      kg();
    }, "已處理"))));
    A.$$("[data-tag]", p).forEach((b) => (b.onclick = () => A.act(b, async () => { const r = await A.api("POST", `/documents/${b.dataset.tag}/kg-tag`); A.toast(`標註 ${r.links} 筆，新候選 ${r.candidates}`); kg(); })));
    bindTools();

    function bindTools() {
      if (!admin) return;
      A.$("#gi").onclick = () => A.$("#gf").click();
      A.$("#gf").onchange = async (e) => {
        const f = e.target.files[0];
        if (!f || !confirm("匯入會取代目前從檔案匯入的節點與關係（人工加的同義說法、停用狀態、段落標註與候選核可的節點會保留）。確定？")) return;
        await A.act(null, async () => { const r = await A.api("POST", `/kbs/${kbId}/kg/import`, await f.text()); A.toast(`已匯入：節點 ${r.nodes}（保留 ${r.kept}）、關係 ${r.edges}、來源 ${r.sources}`); });
        kg();
      };
      A.$("#ge").onclick = (e) => A.act(e.target, () => A.download(`/kbs/${kbId}/kg/export`, k.slug + "-knowledge-graph.json"));
      A.$("#gt").onclick = (e) => A.act(e.target, () => A.download(`/kbs/${kbId}/kg/ontology.ttl`, k.slug + "-ontology.ttl"));
      A.$("#gg").onclick = (e) => confirm("依已核可文件產生圖譜草稿（不會取代目前的圖譜，完成後可下載，審核後再匯入）。文件多時會花幾分鐘與一些模型費用。開始？") && A.act(e.target, async () => {
        const out = A.$("#gout");
        const { job_id } = await A.api("POST", `/kbs/${kbId}/kg/generate`);
        const j = await A.waitJob(job_id, (x) => (out.innerHTML = `<p class="note">${A.esc(x.message || "進行中")}（${Math.round((x.progress || 0) * 100)}%）</p>`));
        if (j.status !== "done") return (out.innerHTML = `<p class="err">${A.esc(j.message)}</p>`);
        const blob = (txt, type, name) => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([txt], { type })); a.download = name; a.textContent = name; return a; };
        out.innerHTML = `<div class="box"><b>${A.esc(j.message)}</b>（抽出 ${j.result.extracted}、合併後 ${j.result.merged}、保留 ${j.result.kept} 個概念）<div class="row" id="gdl" style="margin-top:6px"></div><p class="note">請專家審核草稿，確認後用「匯入圖譜 JSON」取代。</p></div>`;
        A.$("#gdl").append(blob(JSON.stringify(j.result.json, null, 2), "application/json", k.slug + "-knowledge-graph-draft.json"), blob(j.result.ttl, "text/turtle", k.slug + "-ontology-draft.ttl"));
      });
    }
  }

  async function settings() {
    const bots = await A.api("GET", `/workspaces/${k.workspace_id}/bots`);
    p.innerHTML = `<div class="box"><label for="kn">名稱</label><input type="text" id="kn" value="${A.esc(k.name)}"><label for="kd">說明</label><textarea id="kd" rows="2">${A.esc(k.description || "")}</textarea>
      <div class="grid3"><div><label for="kz">段落長度（字）</label><input type="number" id="kz" value="${k.chunk_size}"></div><div><label for="ko">重疊字數</label><input type="number" id="ko" value="${k.chunk_overlap}"></div>
      <div><label for="kv">可見範圍</label><select id="kv"><option value="workspace" ${k.visibility === "workspace" ? "selected" : ""}>整個工作區</option><option value="restricted" ${k.visibility === "restricted" ? "selected" : ""}>只給指定的機器人</option></select></div></div>
      <label class="inline" style="margin-top:10px"><input type="checkbox" id="ka" ${k.auto_kg_tag ? "checked" : ""}> 上傳文件時自動做圖譜標註（需已匯入圖譜）</label>
      <p class="note">向量模型（${A.esc(k.embed_model)}・${k.embed_dim} 維）建立後不能改。段落長度與重疊只影響之後上傳或重新處理的文件。</p>
      <div class="row end"><button class="btn pri" id="ks" type="button">儲存</button></div></div>
      <div class="box" id="ab" ${k.visibility === "restricted" ? "" : "hidden"}><h4>可以掛這個知識庫的機器人</h4>${bots.items.map((b) => `<label class="inline" style="margin-right:14px"><input type="checkbox" data-b="${b.id}" ${k.bots.find((x) => x.id === b.id) ? "checked" : ""}> ${A.esc(b.name)}</label>`).join("")}
      <div class="row end"><button class="btn" id="as" type="button">儲存白名單</button></div></div>
      ${wsRoles.includes("owner") ? `<div class="box"><h4 class="err">刪除知識庫</h4><p class="note">文件、段落、向量與圖譜會一起刪除，無法復原。還被機器人掛著時不能刪。</p><button class="btn danger" id="kdel" type="button">刪除</button></div>` : ""}`;
    A.$("#kv").onchange = () => (A.$("#ab").hidden = A.$("#kv").value !== "restricted");
    A.$("#ks").onclick = (e) => A.act(e.target, () => A.api("PATCH", `/kbs/${kbId}`, { name: A.$("#kn").value, description: A.$("#kd").value, chunk_size: +A.$("#kz").value,
      chunk_overlap: +A.$("#ko").value, visibility: A.$("#kv").value, auto_kg_tag: A.$("#ka").checked }), "已儲存");
    A.$("#as").onclick = (e) => A.act(e.target, () => A.api("PUT", `/kbs/${kbId}/allowed-bots`, { bot_ids: A.$$("[data-b]", p).filter((x) => x.checked).map((x) => x.dataset.b) }), "已儲存");
    const del = A.$("#kdel");
    if (del) del.onclick = () => prompt(`請輸入知識庫的 slug「${k.slug}」確認刪除`) === k.slug && A.act(del, async () => { await A.api("DELETE", `/kbs/${kbId}`); A.go("#/ws/" + k.workspace_id + "/kbs"); }, "已刪除");
  }
};
