/* 評測：題庫、出題、執行、結果、比較、專家抽查 */
A.evalView = async function (p, b) {
  const botId = b.id;
  const admin = A.can(b.workspace_id, ["admin"], botId);
  const reviewer = A.can(b.workspace_id, ["admin", "reviewer"], botId);
  const SRC = { ai: "AI 出題", real: "真實提問", expert: "專家" };
  const [bank, runs] = await Promise.all([A.api("GET", `/bots/${botId}/eval/questions`), A.api("GET", `/bots/${botId}/eval/runs`)]);
  const s = (o) => Object.entries(o).map(([k, n]) => `${A.esc(SRC[k] || k)} ${n}`).join("、") || "—";
  p.innerHTML = `<p class="note">五個面向：找對段落（命中率、MRR）、對題（直接回應、涵蓋重點）、依據（建議能否在引用段落找到）、安全（該轉介、該拒答、不亂編）、穩定（格式問題、多次執行是否一致）。評測不寫入對話紀錄。
      出題 ${A.esc(bank.models.gen)}・評審 ${A.esc(bank.models.judge)}・回答 ${A.esc(bank.models.answer)}</p>
    <div class="grid2"><div class="box"><h4>題庫 <span class="note">版本 v${bank.bank_version}・啟用 ${bank.active} 題（${s(bank.bySource)}）・可出題的已核可段落 ${bank.chunks}</span></h4>
      ${admin ? `<div class="row"><input type="number" id="eN" value="60" min="6" style="width:80px" title="抽幾段來出題"><span class="note">段出題＋</span><input type="number" id="eS" value="6" min="0" max="20" style="width:70px"><span class="note">題特殊題</span>
        <button class="btn" id="eGen" type="button">AI 出題</button><button class="btn" id="eLog" type="button">從真實提問挑 30 題</button><button class="btn" id="eFr" type="button">凍結成新版本</button></div><p class="note" id="eGenOut"></p>` : ""}
      ${reviewer ? `<details><summary>貼上專家題（一行一題）</summary><textarea id="eX" rows="4"></textarea><div class="row end"><button class="btn" id="eXa" type="button">加入</button></div></details>` : ""}</div>
    <div class="box"><h4>執行評測</h4>${admin ? `<div class="grid3"><div><label for="rS">題目來源</label><select id="rS"><option value="">全部</option>${Object.entries(SRC).map(([k, t]) => `<option value="${k}">${t}</option>`).join("")}</select></div>
      <div><label for="rM">最多幾題 <span class="note">可空</span></label><input type="number" id="rM" min="1"></div><div><label for="rR">每題跑幾次</label><select id="rR"><option>1</option><option>2</option><option>3</option></select></div></div>
      <label for="rO">臨時覆蓋設定 <span class="note">JSON，可空；例如 {"min_score":"0.5"}，用來比較不同設定</span></label><input type="text" id="rO" class="mono">
      <div class="row end" style="margin-top:8px"><button class="btn pri" id="rGo" type="button">開始</button></div><p class="note" id="rOut"></p>` : '<p class="note">只有 admin 能執行評測。</p>'}</div></div>
    <h3>歷次評測 <span class="note">勾兩次可以比較</span></h3>
    <div class="tablewrap"><table><thead><tr><th></th><th>開始</th><th>狀態</th><th>題數</th><th>命中</th><th>對題</th><th>依據</th><th>安全通過</th><th>題庫</th><th>覆蓋設定</th></tr></thead><tbody>${runs.items.map((r) => {
      const a = r.summary || {};
      return `<tr class="click" data-run="${r.id}"><td><input type="checkbox" data-cmp="${r.id}" onclick="event.stopPropagation()"></td><td>${A.fmtTime(r.started_at)}</td><td>${A.st(r.status)}</td><td>${r.results}</td>
        <td>${A.pct(a.retrieval && a.retrieval.hit)}</td><td>${A.pct(a.relevance && a.relevance.score)}</td><td>${a.grounded && a.grounded.score != null ? a.grounded.score : "—"}</td><td>${A.pct(a.safety && a.safety.pass)}</td>
        <td>v${r.bank_version}</td><td class="mono">${Object.keys(r.settings_override || {}).length ? A.esc(JSON.stringify(r.settings_override)) : ""}</td></tr>`;
    }).join("") || '<tr><td colspan="10" class="note">還沒有評測</td></tr>'}</tbody></table></div>
    <div class="row" style="margin-top:8px"><button class="btn" id="cmp" type="button">比較勾選的兩次</button></div><div id="cmpOut"></div>
    <h3>題目 <span class="note">最近 500 題</span></h3>
    <div class="tablewrap" style="max-height:50vh"><table><thead><tr><th>題目</th><th>來源</th><th>類型</th><th>期望</th><th>狀態</th><th></th></tr></thead><tbody>${bank.items.map((q) =>
      `<tr><td>${A.esc(q.question)}${q.key_points ? `<div class="note">重點：${A.esc(q.key_points)}</div>` : ""}</td><td>${A.esc(SRC[q.source] || q.source)}</td><td>${A.esc(q.type)}</td><td class="note">${A.esc(q.expect_rtype)}${q.expect_referral ? "・" + A.esc(q.expect_referral) : ""}</td>
      <td>${A.st(q.status)}</td><td>${reviewer ? `<button class="btn sm" data-qs="${q.id}" data-to="${q.status === "active" ? "retired" : "active"}" type="button">${q.status === "active" ? "停用" : "啟用"}</button>` : ""}</td></tr>`).join("") || '<tr><td colspan="6" class="note">題庫是空的</td></tr>'}</tbody></table></div>`;
  const reload = () => A.evalView(p, b);
  if (admin) {
    A.$("#eGen").onclick = (e) => A.act(e.target, async () => {
      const { job_id } = await A.api("POST", `/bots/${botId}/eval/generate`, { max_chunks: +A.$("#eN").value, special: +A.$("#eS").value });
      const j = await A.waitJob(job_id, (x) => (A.$("#eGenOut").textContent = (x.message || "出題中") + "（" + Math.round((x.progress || 0) * 100) + "%）"));
      A.toast(j.message, j.status === "done" ? "" : "err");
      reload();
    });
    A.$("#eLog").onclick = (e) => A.act(e.target, async () => { const r = await A.api("POST", `/bots/${botId}/eval/import-logs`, { n: 30 }); A.toast(`加入 ${r.added} 題`); reload(); });
    A.$("#eFr").onclick = (e) => confirm("把目前啟用的題目標成新版本？之後可以指定版本跑評測，前後比較才公平。") && A.act(e.target, async () => { const r = await A.api("POST", `/bots/${botId}/eval/freeze`); A.toast(`已凍結為 v${r.bank_version}（${r.questions} 題）`); reload(); });
    A.$("#rGo").onclick = (e) => A.act(e.target, async () => {
      let ov = {};
      if (A.$("#rO").value.trim()) { try { ov = JSON.parse(A.$("#rO").value); } catch (x) { throw new Error("覆蓋設定不是有效的 JSON"); } }
      const body = { repeat: +A.$("#rR").value, settings_override: ov };
      if (A.$("#rS").value) body.source = A.$("#rS").value;
      if (A.$("#rM").value) body.max = +A.$("#rM").value;
      const r = await A.api("POST", `/bots/${botId}/eval/runs`, body);
      const j = await A.waitJob(r.job_id, (x) => (A.$("#rOut").textContent = `評測中 ${x.message || ""}`));
      A.toast(j.status === "done" ? "評測完成" : j.message, j.status === "done" ? "" : "err");
      reload();
    });
  }
  const xa = A.$("#eXa");
  if (xa) xa.onclick = () => A.act(xa, async () => { const r = await A.api("POST", `/bots/${botId}/eval/questions`, { text: A.$("#eX").value }); A.toast(`加入 ${r.added} 題`); reload(); });
  A.$$("[data-qs]", p).forEach((x) => (x.onclick = () => A.act(x, () => A.api("PATCH", `/eval/questions/${x.dataset.qs}`, { status: x.dataset.to })).then(reload)));
  A.$$("tr[data-run]", p).forEach((tr) => (tr.onclick = () => detail(tr.dataset.run)));
  A.$("#cmp").onclick = (e) => A.act(e.target, async () => {
    const ids = A.$$("[data-cmp]", p).filter((x) => x.checked).map((x) => x.dataset.cmp);
    if (ids.length !== 2) throw new Error("請勾選剛好兩次評測");
    const [bId, aId] = ids;          // 列表是新的在上：較舊的當基準
    const c = await A.api("GET", `/eval/compare?a=${aId}&b=${bId}`);
    A.$("#cmpOut").innerHTML = `<div class="box"><h4>${c.pass ? '<span class="ok">沒有明顯退步</span>' : '<span class="err">有退步：' + c.regressions.map(A.esc).join("、") + "</span>"} ${c.same_bank ? "" : '<span class="warn">（兩次的題庫版本不同，比較僅供參考）</span>'}</h4>
      <table><thead><tr><th>指標</th><th>基準（較舊）</th><th>新的</th><th>差異</th></tr></thead><tbody>${Object.entries(c.diffs).map(([k, x]) => `<tr><td class="mono">${A.esc(k)}</td><td>${x.a}</td><td>${x.b}</td><td class="${c.regressions.includes(k) ? "err" : ""}">${x.diff > 0 ? "+" : ""}${x.diff}</td></tr>`).join("")}</tbody></table></div>`;
  });

  async function detail(runId) {
    const r = await A.api("GET", `/eval/runs/${runId}`);
    const a = (r.summary && r.summary.all) || {};
    const box = A.side.open("評測結果", `<div class="kpis">${[[A.pct(a.retrieval && a.retrieval.hit), "段落命中"], [a.retrieval && a.retrieval.mrr, "MRR"], [A.pct(a.relevance && a.relevance.score), "對題"],
      [a.relevance && a.relevance.coverage, "涵蓋重點"], [a.grounded && a.grounded.score, "依據"], [A.pct(a.safety && a.safety.pass), "安全通過"], [A.pct(a.format && a.format.issues), "格式問題"],
      [a.consistency ? A.pct(a.consistency.sameType) : "—", "多次一致"]].map(([x, t]) => `<div class="kpi"><b>${x == null ? "—" : x}</b><span>${t}</span></div>`).join("")}</div>
      ${r.summary && r.summary.agreement ? `<p class="note">專家抽查：已看 ${r.summary.agreement.reviewed}、同意 ${A.pct(r.summary.agreement.agree)}、待抽查 ${r.summary.agreement.pending}</p>` : ""}
      ${r.results.map((x) => `<div class="box"><div class="row"><b>${A.esc(x.question)}</b></div>
        <div class="note">${A.esc(SRC[x.source] || x.source)}・${A.esc(x.qtype)}・第 ${x.attempt} 次・${A.esc((TraceView.TYPE[x.rtype] || [x.rtype])[0])}・${(x.ms / 1000).toFixed(1)} 秒</div>
        <div>${x.hit ? `段落：<b class="${x.hit === "沒找到" ? "err" : "ok"}">${A.esc(x.hit)}</b>${x.rank ? "（第 " + x.rank + " 名）" : ""}・` : ""}對題 ${x.on_topic == null ? "—" : x.on_topic}・涵蓋 ${x.coverage == null ? "—" : x.coverage}・依據 ${x.grounded == null ? "—" : x.grounded}・安全 <b class="${x.safe === "未通過" ? "err" : x.safe === "通過" ? "ok" : "warn"}">${A.esc(x.safe || "—")}</b> ${A.esc(x.safe_note)}</div>
        ${x.judge_note ? `<div class="note">評審：${A.esc(x.judge_note)}</div>` : ""}${x.answer_summary ? `<div class="chunk">${A.esc(x.answer_summary)}</div>` : ""}
        ${x.expert_check ? `<div class="row">${reviewer ? `<select data-rv="${x.question_id}|${x.attempt}" style="width:auto">${["待抽查", "同意", "不同意"].map((o) => `<option ${o === x.expert_check ? "selected" : ""}>${o}</option>`).join("")}</select>
          <input type="text" data-rc="${x.question_id}|${x.attempt}" value="${A.esc(x.expert_note)}" placeholder="意見">` : `專家抽查：${A.esc(x.expert_check)}`}</div>` : ""}</div>`).join("")}`);
    A.$$("[data-rv]", box).forEach((s) => (s.onchange = () => {
      const [q, at] = s.dataset.rv.split("|");
      A.act(null, () => A.api("POST", `/eval/results/${runId}/${q}/${at}/review`, { status: s.value, comment: A.$(`[data-rc="${s.dataset.rv}"]`, box).value }), "已記錄");
    }));
  }
};
