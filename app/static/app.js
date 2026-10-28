const app = document.getElementById("app");
const state = { projectId: localStorage.getItem("cardnewsProjectId"), project: null, poll: null, lastTopic: null };

function rid() {
  return crypto.randomUUID();
}

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail || data;
    throw Object.assign(new Error(d.message || res.statusText), { payload: d });
  }
  return data;
}

function fmtTime(iso) {
  if (!iso) return "아직 없음";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const text = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Seoul",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(d);
  return `${text} (한국 시간)`;
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function render() {
  const p = state.project;
  if (!p) {
    app.innerHTML = `
      <section class="panel">
        <label for="topic">주제</label>
        <input id="topic" type="text" value="${esc(state.lastTopic || "AI 최신소식")}" />
        <p class="meta">기본 조사 기간은 최근 7일입니다. 결과는 이 브라우저에만 묶입니다.</p>
        <div class="row">
          <button id="start">조사 시작</button>
        </div>
      </section>`;
    document.getElementById("start").onclick = createAndResearch;
    return;
  }

  const err = (p.errors || [])[0];
  const q = p.currentQuestion;
  const order = ["research", "selection", "investigate", "editorial", "storyboard", "produce", "review"];
  const at = order.indexOf(p.stage);
  const showInvestigation = p.investigation && at >= order.indexOf("editorial");
  const showStoryboard = p.storyboard && at >= order.indexOf("storyboard");
  const showCards = p.cards.length && at >= order.indexOf("produce");
  app.innerHTML = `
    <section class="panel">
      <p class="meta">주제 ${esc(p.topic)} · 조사 기준 ${esc(fmtTime(p.researchAsOf))} · ${p.periodDays}일</p>
      <p>상태: <strong>${esc(statusText(p))}</strong></p>
      ${p.waitKind === "editorial" && p.status === "waiting_for_user" ? `<div class="row"><button id="editorial-go">이대로 스토리보드 만들기</button>${backButton()}</div>` : ""}
      ${p.status === "interrupted" ? "<p>서버가 재시작되어 작업이 멈췄습니다. 아래에서 이어서 시도하세요.</p>" : ""}
      ${p.status === "running" ? `<p class="progress">${esc((p.progress.slice(-1)[0] || {}).text || "작업 중")}</p><div class="row"><button class="danger" id="cancel">취소</button></div>` : ""}
    </section>
    ${err ? `<section class="panel error"><p>${esc(err.message)}</p><p class="meta">${esc((err.nextActions || []).join(" / "))}</p>
      <div class="row"><button id="retry">재시도</button>${backButton()}</div></section>` : ""}
    ${p.candidates.length ? renderCandidates(p) : ""}
    ${q && p.waitKind === "question" ? renderQuestion(q) : ""}
    ${showInvestigation ? `<section class="panel"><h2>심층 조사</h2>${renderInvestigation(p)}</section>` : ""}
    ${showStoryboard ? renderStoryboard(p) : ""}
    ${showCards ? renderCards(p) : ""}
  `;

  const cancel = document.getElementById("cancel");
  if (cancel) cancel.onclick = async () => {
    if (p.activeRunId) await api(`/api/runs/${p.activeRunId}/cancel`, { method: "POST", body: "{}" });
    await refresh();
  };
  const retry = document.getElementById("retry");
  if (retry) retry.onclick = () => retryStage(p);
  const sel = document.getElementById("select-go");
  if (sel) sel.onclick = submitSelection;
  const ans = document.getElementById("answer-go");
  if (ans) ans.onclick = submitAnswer;
  const appr = document.getElementById("approve-board");
  if (appr) appr.onclick = () => approve("storyboard", p.storyboardVersion);
  const edGo = document.getElementById("editorial-go");
  if (edGo) edGo.onclick = submitEditorial;
  const chg = document.getElementById("change-board");
  if (chg) chg.onclick = submitStoryboardChange;
  document.querySelectorAll(".back-btn").forEach((el) => { el.onclick = goBack; });
  const fin = document.getElementById("approve-final");
  if (fin) fin.onclick = () => approve("final", p.resultVersion);
  const rev = document.getElementById("revise-go");
  if (rev) rev.onclick = submitRevision;
}

function backButton() {
  return `<button class="secondary back-btn">이전 단계로</button>`;
}

async function goBack() {
  const p = state.project;
  if (!p) return;
  if (["research", "selection"].includes(p.stage)) {
    localStorage.removeItem("cardnewsProjectId");
    state.lastTopic = p.topic;
    state.projectId = null;
    state.project = null;
    if (state.poll) {
      clearInterval(state.poll);
      state.poll = null;
    }
    render();
    return;
  }
  try {
    await api(`/api/projects/${p.id}/back`, { method: "POST", body: JSON.stringify({ requestId: rid() }) });
  } catch (e) {
    alert(e.message);
  }
  await refresh();
}

function statusText(p) {
  if (p.status === "running") {
    return {
      research: "조사 중",
      selection: "심층 조사 중",
      investigate: "심층 조사 중",
      editorial: "스토리보드 만드는 중",
      storyboard: "스토리보드 만드는 중",
      produce: "카드 만드는 중",
      review: "카드 수정 중",
    }[p.stage] || "작업 중";
  }
  if (p.status === "waiting_for_user") {
    return {
      selection: "후보 선택을 기다리는 중",
      question: "질문 답변을 기다리는 중",
      editorial: "편집 방향 선택을 기다리는 중",
      storyboard: "스토리보드 승인을 기다리는 중",
      review: "카드 검수를 기다리는 중",
    }[p.waitKind] || "기다리는 중";
  }
  return {
    created: "시작 전",
    done: "완료",
    error: "오류",
    cancelled: "취소됨",
    interrupted: "중단됨",
  }[p.status] || p.status;
}

function renderCandidates(p) {
  const choosing = p.waitKind === "selection";
  const locked = !choosing || p.status === "running";
  const list = choosing ? p.candidates : p.candidates.filter((c) => p.selection.includes(c.id));
  const items = list.map((c) => `
    <div class="candidate">
      ${choosing ? `<input type="checkbox" value="${esc(c.id)}" ${p.selection.includes(c.id) ? "checked" : ""} ${locked ? "disabled" : ""} />` : "<span></span>"}
      <div>
        <strong>${esc(c.title)}</strong>
        <p>${esc(c.summary)}</p>
        <p class="meta">게시 ${esc(c.publishedAt)} · 사건 ${esc(c.eventAt)} · 묶음 ${esc(c.clusterId)}</p>
        <p class="meta">선택 가치: ${esc(c.whySelect)} · 불확실: ${esc(c.uncertainty)}</p>
        ${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">원문</a>` : ""}
      </div>
    </div>`).join("");
  return `<section class="panel">
    <h2>${choosing ? `후보 ${p.candidates.length}개` : `고른 소식 ${list.length}개`}</h2>
    ${choosing ? `<p class="meta">1~3개를 고른 뒤에만 제작이 시작됩니다. 새로고침해도 유지됩니다.</p>` : ""}
    ${items}
    ${choosing && p.status === "waiting_for_user" ? `<div class="row"><button id="select-go">선택한 소식으로 심층 조사</button>${backButton()}</div>` : ""}
  </section>`;
}

function renderQuestion(q) {
  const opts = (q.options || []).map((o) => `
    <label><input type="radio" name="opt" value="${esc(o.label)}" /> <span>${esc(o.label)}${o.hint ? " — " + esc(o.hint) : ""}</span></label>`).join("");
  return `<section class="panel">
    <h2>${esc(q.question)}</h2>
    ${q.reason ? `<p class="meta">추천: ${esc(q.recommended || "")} · ${esc(q.reason)}</p>` : ""}
    <div class="options">${opts}</div>
    ${q.allowFreeText ? `<input id="free" type="text" placeholder="직접 입력" />` : ""}
    <div class="row"><button id="answer-go">답변하고 이어가기</button></div>
  </section>`;
}

function renderInvestigation(p) {
  const inv = p.investigation || {};
  const facts = (inv.facts || []).map((f) => {
    const src = /^https?:\/\//.test(f.source || "")
      ? ` <a href="${esc(f.source)}" target="_blank" rel="noopener">출처</a>`
      : f.source ? ` (${esc(f.source)})` : "";
    return `<li>${esc(f.text)}${src}</li>`;
  }).join("");
  const claims = (inv.claims || []).map((f) => `<li>${esc(f.who)}: ${esc(f.text)}</li>`).join("");
  const ed = p.editorial || {};
  const choose = p.waitKind === "editorial" && p.status === "waiting_for_user";
  const hooks = ed.hooks || [];
  const picked = p.editorialChoice;
  return `
    <div class="inv-box">
      <h3>독자</h3>
      ${choose
        ? choiceGroup("ed-audience", [
            { value: "일반 사용자", hint: "의미와 사용 조건 중심" },
            { value: "개발자", hint: "도구·API·변경점 중심" },
            { value: "교육자", hint: "설명·수업 활용 중심" },
          ], ed.audience, true)
        : `<p>${esc(picked ? picked.audience : ed.audience)}</p>`}
    </div>
    <div class="inv-box">
      <h3>형식</h3>
      ${choose
        ? choiceGroup("ed-format", [
            { value: "비교", hint: "여러 소식을 같은 기준으로 나란히" },
            { value: "단계설명", hint: "하나를 순서대로 따라 하기" },
            { value: "스토리", hint: "상황 → 변화 → 의미 순서" },
            { value: "체크리스트", hint: "확인할 항목을 번호로" },
          ], ed.format, false)
        : `<p>${esc(picked ? picked.format : ed.format)}</p>`}
    </div>
    <div class="inv-box">
      <h3>표지 후킹 문구</h3>
      ${choose
        ? choiceGroup("ed-hook", hooks, hooks[0], true)
        : picked
          ? `<p>${esc(picked.hook)}</p>`
          : `<ul>${hooks.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>`}
    </div>
    <div class="inv-box">
      <h3>확인된 사실</h3>
      <ul>${facts}</ul>
    </div>
    <div class="inv-box">
      <h3>주장</h3>
      <ul>${claims}</ul>
    </div>`;
}

function sourceLink(src) {
  const urls = String(src || "").match(/https?:\/\/[^\s,)]+/g) || [];
  if (!urls.length) return esc(src || "");
  return urls.map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener">출처${urls.length > 1 ? " " + (i + 1) : ""}</a>`).join(" · ");
}

function renderStoryboard(p) {
  const edit = p.waitKind === "storyboard" && p.status === "waiting_for_user";
  const cards = (p.storyboard.cards || []).map((c, i) => `
    <div class="story">
      <strong>${i + 1}. ${esc(c.role)} · ${esc(c.title)}</strong>
      <p>${esc(c.headline)}</p>
      <p class="story-body">${esc(c.body)}</p>
      <p class="meta"><strong>근거</strong> ${sourceLink(c.source)}</p>
      <p class="meta"><strong>그림</strong> ${esc(c.imagePlan)}</p>
      ${edit ? `<div class="card-edit">
        <label><input type="checkbox" class="chg-card" value="${esc(c.id)}" /> <span>이 카드 수정</span></label>
        <input type="text" class="chg-note" data-card="${esc(c.id)}" placeholder="고칠 내용 (선택)" />
      </div>` : ""}
    </div>`).join("");
  return `<section class="panel">
    <h2>스토리보드</h2>
    ${p.storyboard.title ? `<p><strong>카드뉴스 제목:</strong> ${esc(p.storyboard.title)}</p>` : ""}
    ${cards}
    ${edit ? `<div class="row">
      <button class="secondary" id="change-board">변경 요청 보내기</button>
      <button id="approve-board">기획 승인하고 이미지 제작</button>
      ${backButton()}
    </div>` : ""}
  </section>`;
}

function choiceGroup(name, options, recommended, allowFree) {
  const values = options.map((o) => (typeof o === "string" ? o : o.value));
  let checked = values.findIndex((v) => v === recommended);
  if (checked < 0 && recommended) checked = values.findIndex((v) => String(recommended).includes(v));
  const radios = options.map((o, i) => {
    const v = values[i];
    const hint = typeof o === "string" ? "" : o.hint;
    return `
    <label><input type="radio" name="${name}" value="${esc(v)}" ${i === checked ? "checked" : ""} /> <span>${esc(v)}${hint ? " — " + esc(hint) : ""}</span></label>`;
  }).join("");
  return `<div class="options choice">
    ${radios}
    ${allowFree ? `<label><input type="radio" name="${name}" value="__free" /> <span>직접 입력</span></label>
    <input type="text" id="${name}-free" placeholder="직접 입력" />` : ""}
  </div>`;
}

function pickChoice(name) {
  const el = document.querySelector(`input[name="${name}"]:checked`);
  if (!el || !el.value) return null;
  if (el.value === "__free") {
    const free = document.getElementById(`${name}-free`);
    return (free && free.value.trim()) || null;
  }
  return el.value;
}

function renderCards(p) {
  const phones = p.cards.map((c) => `
    <div>
      <div class="phone"><img src="/api/projects/${p.id}/files/${encodeURIComponent(c.file)}?v=${p.resultVersion}" alt="${esc(c.title)}" /></div>
      <p class="meta">${esc(c.id)} ${c.failed ? "· 이미지 실패" : ""}</p>
    </div>`).join("");
  return `<section class="panel">
    <h2>미리보기 (1080×1350 축소)</h2>
    <div class="cards">${phones}</div>
    <label>수정할 카드 ID (쉼표)</label>
    <input id="rev-ids" type="text" placeholder="s1,s2" />
    <label>수정 요청</label>
    <textarea id="rev-text" rows="3" placeholder="3번 카드 본문이 길어. 핵심 조건은 남기고 두 문장으로 줄여 줘"></textarea>
    <div class="row">
      <button class="secondary" id="revise-go">선택한 카드만 수정</button>
      ${p.waitKind === "review" && p.status !== "running" ? `<button id="approve-final">최종 승인하고 내려받기</button>${backButton()}` : ""}
    </div>
    ${p.finalApproved ? `<p><a href="/api/projects/${p.id}/files/cards.zip">ZIP 내려받기</a> · <a href="/api/projects/${p.id}/files/provenance.md">출처 기록</a></p>` : ""}
  </section>`;
}

async function createAndResearch() {
  const topic = document.getElementById("topic").value.trim();
  const created = await api("/api/projects", { method: "POST", body: JSON.stringify({ topic }) });
  state.projectId = created.projectId;
  localStorage.setItem("cardnewsProjectId", created.projectId);
  await api(`/api/projects/${created.projectId}/research`, { method: "POST", body: JSON.stringify({ requestId: rid() }) });
  startPoll();
}

async function refresh() {
  if (!state.projectId) return;
  try {
    state.project = await api(`/api/projects/${state.projectId}`);
    render();
    if (state.project.status !== "running" && state.poll) {
      clearInterval(state.poll);
      state.poll = null;
    }
  } catch (e) {
    if (String(e.message).includes("없")) {
      localStorage.removeItem("cardnewsProjectId");
      state.projectId = null;
      state.project = null;
      render();
    }
  }
}

function startPoll() {
  refresh();
  if (state.poll) clearInterval(state.poll);
  state.poll = setInterval(refresh, 1500);
}

async function submitSelection() {
  const ids = [...document.querySelectorAll(".candidate input:checked")].map((el) => el.value);
  await api(`/api/projects/${state.projectId}/selection`, {
    method: "POST",
    body: JSON.stringify({ candidateIds: ids, version: state.project.candidateVersion }),
  });
  startPoll();
}

async function submitAnswer() {
  const q = state.project.currentQuestion;
  const picked = document.querySelector('input[name="opt"]:checked');
  const free = document.getElementById("free");
  const answer = (free && free.value.trim()) || (picked && picked.value);
  if (!answer) return alert("선택하거나 입력하세요.");
  await api(`/api/projects/${state.projectId}/answers`, {
    method: "POST",
    body: JSON.stringify({ questionId: q.id, version: q.version, answer, requestId: rid() }),
  });
  startPoll();
}

async function approve(stage, version) {
  await api(`/api/projects/${state.projectId}/approve`, {
    method: "POST",
    body: JSON.stringify({ stage, version, requestId: rid() }),
  });
  if (stage === "final") {
    await api(`/api/projects/${state.projectId}/exports`);
  }
  startPoll();
}

async function submitStoryboardChange() {
  const cardIds = [...document.querySelectorAll(".chg-card:checked")].map((el) => el.value);
  if (!cardIds.length) return alert("수정할 카드를 하나 이상 체크하세요.");
  const cardNotes = {};
  document.querySelectorAll(".chg-note").forEach((el) => {
    if (cardIds.includes(el.dataset.card) && el.value.trim()) cardNotes[el.dataset.card] = el.value.trim();
  });
  await api(`/api/projects/${state.projectId}/storyboard-changes`, {
    method: "POST",
    body: JSON.stringify({ cardIds, cardNotes, version: state.project.storyboardVersion, requestId: rid() }),
  });
  startPoll();
}

async function submitEditorial() {
  await api(`/api/projects/${state.projectId}/editorial`, {
    method: "POST",
    body: JSON.stringify({
      audience: pickChoice("ed-audience"),
      format: pickChoice("ed-format"),
      hook: pickChoice("ed-hook"),
      version: state.project.editorialVersion,
      requestId: rid(),
    }),
  });
  startPoll();
}

async function submitRevision() {
  const raw = document.getElementById("rev-ids").value;
  const instruction = document.getElementById("rev-text").value.trim();
  const cardIds = raw.split(",").map((s) => s.trim()).filter(Boolean);
  await api(`/api/projects/${state.projectId}/revisions`, {
    method: "POST",
    body: JSON.stringify({
      cardIds,
      instruction,
      version: state.project.resultVersion,
      requestId: rid(),
    }),
  });
  startPoll();
}

async function retryStage(p) {
  if (p.stage === "research" || !p.candidates.length) {
    await api(`/api/projects/${p.id}/research`, { method: "POST", body: JSON.stringify({ requestId: rid() }) });
  } else if (p.stage === "investigate" || p.stage === "editorial") {
    await api(`/api/projects/${p.id}/selection`, {
      method: "POST",
      body: JSON.stringify({ candidateIds: p.selection, version: p.candidateVersion }),
    });
  } else if (p.stage === "storyboard") {
    await approve("storyboard", p.storyboardVersion);
  } else if (p.cards.length) {
    const failed = p.cards.filter((c) => c.failed).map((c) => c.storyId);
    await api(`/api/projects/${p.id}/revisions`, {
      method: "POST",
      body: JSON.stringify({
        cardIds: failed.length ? failed : p.cards.map((c) => c.storyId),
        instruction: "실패한 카드 이미지만 다시 생성하고 글자는 유지해 줘.",
        version: p.resultVersion,
        requestId: rid(),
      }),
    });
  }
  startPoll();
}

if (state.projectId) startPoll();
else render();
