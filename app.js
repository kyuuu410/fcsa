'use strict';

const data = window.FCSA_RECORDS;
const pageSize = 8;
const positionLabels = {GK: '골키퍼', DF: '수비수', MF: '미드필더', FW: '공격수'};
const state = {recordView: 'dates', month: 'all', recordPage: 1, position: 'all', search: '', playerPage: 1, ranking: 'goals'};
const dialog = document.querySelector('#detail-dialog');
const dialogBody = document.querySelector('#dialog-body');
let previouslyFocused = null;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
}

function formatDate(value) { return value.slice(0, 10).replaceAll('-', '. '); }
function weekday(value) { return new Intl.DateTimeFormat('ko-KR', {weekday: 'long', timeZone: 'UTC'}).format(new Date(`${value}T12:00:00Z`)); }
function percent(value) { return value == null ? '—' : `${(value * 100).toFixed(1)}%`; }
function role(player) { return positionLabels[player.position] || player.position; }
function sourceCaption() { return `${data.season} 시즌 · ${formatDate(data.source.modifiedAt)} 원본 기준`; }

function openDialog(content, type = 'detail') {
  if (!dialog.open) previouslyFocused = document.activeElement;
  dialog.classList.toggle('video-dialog', type === 'video');
  dialogBody.innerHTML = content;
  if (!dialog.open) dialog.showModal();
  document.body.classList.add('no-scroll');
  dialog.querySelector('.dialog-close').focus();
}

dialog.querySelector('.dialog-close').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', event => {
  if (event.target !== dialog) return;
  const bounds = dialog.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
});
dialog.addEventListener('close', () => {
  if (dialog.open) return;
  dialogBody.querySelector('iframe')?.remove();
  dialog.classList.remove('video-dialog');
  document.body.classList.remove('no-scroll');
  if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
  if (pendingVideoFeed) renderVideos(pendingVideoFeed);
});

function activate(button, selector) {
  document.querySelectorAll(selector).forEach(item => item.setAttribute('aria-pressed', String(item === button)));
}

function bindPlayerButtons(container) {
  container.querySelectorAll('[data-player-id]').forEach(button => button.addEventListener('click', () => showPlayer(button.dataset.playerId)));
}

function renderStrip() {
  const latest = [...data.dates].sort((first, second) => second.date.localeCompare(first.date))[0];
  const content = latest
    ? `<div class="strip-label"><b>최근 경기 기록</b>${formatDate(latest.date)} · ${weekday(latest.date)}</div><div class="mini-score"><span class="mini-team"><img class="crest" src="assets/crest.png" alt="" width="35" height="35">FCSA</span><strong class="num">${latest.goals}<small>득점</small></strong><span class="mini-team">${latest.participants}명 참여</span></div>`
    : '<p>등록된 경기 기록이 없습니다.</p>';
  document.querySelector('#latest-record').innerHTML = content;
  document.querySelector('#season-summary').textContent = `${data.summary.wins}승 ${data.summary.draws}무 ${data.summary.losses}패`;
  document.querySelector('#season-games').textContent = data.summary.played;
  document.querySelector('#source-status').textContent = `${formatDate(data.source.modifiedAt)} 원본 · ${formatDate(data.source.importedAt)} 반영`;
}

function renderRecordPagination(length) {
  const lastPage = Math.max(1, Math.ceil(length / pageSize));
  document.querySelector('#record-page-label').textContent = `${state.recordPage} / ${lastPage}`;
  document.querySelector('#record-prev').disabled = state.recordPage <= 1;
  document.querySelector('#record-next').disabled = state.recordPage >= lastPage;
  document.querySelector('#record-count').textContent = state.recordView === 'dates' ? `경기일 ${length}일 · 날짜를 선택해 출전·득점·도움 보기` : `상대팀 ${length}팀 · 팀을 선택해 누적 전적 보기`;
}

function renderRecords() {
  document.querySelector('#record-month').disabled = state.recordView !== 'dates';
  let rows;
  if (state.recordView === 'dates') {
    const dates = data.dates.filter(item => state.month === 'all' || item.date.slice(0, 7) === state.month).sort((first, second) => second.date.localeCompare(first.date));
    renderRecordPagination(dates.length);
    rows = dates.slice((state.recordPage - 1) * pageSize, state.recordPage * pageSize).map(item => `<button class="date-row" data-date="${item.date}" aria-label="${formatDate(item.date)} ${weekday(item.date)}, ${item.participants}명 참여, ${item.goals}득점, 상세 기록 보기"><span class="date-label">${formatDate(item.date)}<small>${weekday(item.date)} · 경기 기록</small></span><span class="fixture-team"><img class="crest" src="assets/crest.png" alt="" width="32" height="32">FCSA</span><span class="date-metric"><b>${item.participants}</b><small>참여 인원</small></span><span class="date-metric"><b>${item.goals}</b><small>득점 기록</small></span><span class="date-metric"><b>${item.assists}</b><small>도움 기록</small></span><span aria-hidden="true">↗</span></button>`).join('');
  } else {
    const opponents = [...data.opponents].sort((first, second) => (second.wins + second.draws + second.losses) - (first.wins + first.draws + first.losses));
    renderRecordPagination(opponents.length);
    rows = opponents.slice((state.recordPage - 1) * pageSize, state.recordPage * pageSize).map(opponent => `<button class="opponent-row" data-opponent="${escapeHtml(opponent.name)}" aria-label="${escapeHtml(opponent.name)}, ${opponent.wins}승 ${opponent.draws}무 ${opponent.losses}패, 상대 전적 보기"><span class="opponent-name">${escapeHtml(opponent.name)}<small>${opponent.wins + opponent.draws + opponent.losses}경기</small></span><span class="date-metric"><b>${opponent.wins}</b><small>승</small></span><span class="date-metric"><b>${opponent.draws}</b><small>무</small></span><span class="date-metric"><b>${opponent.losses}</b><small>패</small></span><span class="opponent-rate">${percent(opponent.winRate)}</span><span aria-hidden="true">↗</span></button>`).join('');
  }
  document.querySelector('#fixture-list').innerHTML = rows || '<p class="empty">선택한 기간에 등록된 기록이 없습니다.</p>';
  document.querySelectorAll('[data-date]').forEach(button => button.addEventListener('click', () => showDate(button.dataset.date)));
  document.querySelectorAll('[data-opponent]').forEach(button => button.addEventListener('click', () => showOpponent(button.dataset.opponent)));
}

function showDate(date) {
  const record = data.dates.find(item => item.date === date);
  if (!record) return;
  const performances = data.players.map(player => ({player, match: player.matchRecords.find(item => item.date === date)})).filter(item => item.match && (item.match.attended === true || item.match.attended === null || item.match.goals > 0 || item.match.assists > 0));
  const performanceRows = performances.map(({player, match}) => `<tr><th scope="row"><button class="inline-player" data-player-id="${escapeHtml(player.id)}">${escapeHtml(player.name)} <small>${player.number}</small></button></th><td>${match.attended === true ? '출전' : match.attended === null ? '미확인' : '미표기'}</td><td>${match.goals}</td><td>${match.assists}</td></tr>`).join('');
  openDialog(`<span class="source-badge">원본 기록</span><h2 id="dialog-title">${formatDate(date)}<br><span class="dialog-subtitle">${weekday(date)} · FCSA 경기 기록</span></h2><div class="dialog-stats"><div><b>${record.participants}</b><span>참여 인원</span></div><div><b>${record.goals}</b><span>득점 기록</span></div><div><b>${record.assists}</b><span>도움 기록</span></div></div><p class="source-explanation">해당 날짜의 상대팀·최종 스코어는 원본에 없습니다. 아래는 출장·득점·도움 시트의 기록입니다.</p><div class="table-scroll"><table class="detail-table"><thead><tr><th>선수</th><th>출장 표기</th><th>득점</th><th>도움</th></tr></thead><tbody>${performanceRows || '<tr><td colspan="4">표시할 선수 기록이 없습니다.</td></tr>'}</tbody></table></div><p class="source-explanation">${escapeHtml(sourceCaption())}</p>`);
  // Reuse the open dialog so closing returns focus to the original match row.
  dialogBody.querySelectorAll('[data-player-id]').forEach(button => button.addEventListener('click', () => {
    showPlayer(button.dataset.playerId);
  }));
}

function showOpponent(name) {
  const opponent = data.opponents.find(item => item.name === name);
  if (!opponent) return;
  openDialog(`<img class="modal-brand crest" src="assets/crest.png" alt="FCSA"><span class="source-badge">${data.season} 시즌 상대별 전적</span><h2 id="dialog-title">FCSA vs ${escapeHtml(opponent.name)}</h2><p>총 ${opponent.wins + opponent.draws + opponent.losses}경기 · 승률 ${percent(opponent.winRate)}</p><div class="dialog-stats"><div><b>${opponent.wins}</b><span>승리</span></div><div><b>${opponent.draws}</b><span>무승부</span></div><div><b>${opponent.losses}</b><span>패배</span></div></div><p class="source-explanation">‘26년 팀 기록’의 상대팀별 누적 전적입니다. 날짜별 상대팀 정보가 없어 개별 날짜와 연결하지 않았습니다.</p>`);
}

function filteredPlayers() {
  return data.players.filter(player => (state.position === 'all' || player.position === state.position) && (!state.search || player.name.includes(state.search) || String(player.number).includes(state.search)));
}

function renderPlayers() {
  const filtered = filteredPlayers();
  const lastPage = Math.max(1, Math.ceil(filtered.length / pageSize));
  const start = (state.playerPage - 1) * pageSize;
  document.querySelector('#player-grid').innerHTML = filtered.length ? filtered.slice(start, start + pageSize).map(player => `<button class="player-card" data-player-id="${escapeHtml(player.id)}" aria-label="${escapeHtml(player.name)}, ${player.number}번 ${escapeHtml(role(player))}, 개인 기록 보기"><div class="player-art" data-number="${String(player.number).padStart(2, '0')}"><span class="position-tag">${escapeHtml(player.position)}</span><div class="shirt" aria-hidden="true"><img class="crest" src="assets/crest.png" alt=""></div><span class="shirt-num">${String(player.number).padStart(2, '0')}</span></div><div class="player-info"><div class="player-info-top"><h3>${escapeHtml(player.name)}</h3><span class="player-card-arrow" aria-hidden="true">↗</span></div><p>${escapeHtml(role(player))} · ${player.number}번</p><div class="player-metrics"><span><b>${player.appearances}</b>출전</span><span><b>${player.goals}</b>득점</span><span><b>${player.assists}</b>도움</span></div></div></button>`).join('') : '<p class="empty" style="grid-column:1/-1">조건에 맞는 선수가 없습니다.</p>';
  document.querySelector('#player-count').textContent = filtered.length ? `${start + 1}–${Math.min(start + pageSize, filtered.length)} / ${filtered.length}명` : '검색 결과 0명';
  document.querySelector('#player-page-label').textContent = `${state.playerPage} / ${lastPage}`;
  document.querySelector('#player-prev').disabled = state.playerPage <= 1;
  document.querySelector('#player-next').disabled = state.playerPage >= lastPage;
  bindPlayerButtons(document.querySelector('#player-grid'));
}

function showPlayer(id) {
  const player = data.players.find(item => item.id === id);
  if (!player) return;
  const playerMatches = [...player.matchRecords].sort((first, second) => second.date.localeCompare(first.date));
  const rows = playerMatches.map(match => `<tr><th scope="row">${formatDate(match.date)}</th><td>${match.attended === true ? '출전' : match.attended === null ? '미확인' : '미표기'}</td><td>${match.goals}</td><td>${match.assists}</td></tr>`).join('');
  openDialog(`<span class="source-badge">${data.season} 시즌 선수 기록</span><div class="dialog-player-number">${String(player.number).padStart(2, '0')}</div><h2 id="dialog-title">${escapeHtml(player.name)} <span class="dialog-subtitle">${escapeHtml(player.position)}</span></h2><p>${escapeHtml(role(player))} · ${player.number}번</p><div class="dialog-stats"><div><b>${player.appearances}</b><span>출전</span></div><div><b>${player.goals}</b><span>득점</span></div><div><b>${player.assists}</b><span>도움</span></div></div><div class="player-extra"><span>공격포인트 <b>${player.points}</b></span><span>참석률 <b>${percent(player.attendanceRate)}</b></span></div><details class="player-history"><summary>날짜별 출전·득점·도움 보기 <span>${playerMatches.length}일</span></summary><div class="table-scroll"><table class="detail-table"><thead><tr><th>경기일</th><th>출장 표기</th><th>득점</th><th>도움</th></tr></thead><tbody>${rows}</tbody></table></div></details><p class="source-explanation">${escapeHtml(sourceCaption())}<br>총계는 선수 기록 시트의 값을 그대로 표시합니다.</p>`);
}

function renderRanking() {
  const unit = {goals: '골', assists: '도움', appearances: '경기'}[state.ranking];
  const sorted = [...data.players].sort((first, second) => second[state.ranking] - first[state.ranking] || first.number - second.number);
  document.querySelector('#ranking-list').innerHTML = sorted.slice(0, 4).map(player => {
    const rank = sorted.findIndex(item => item[state.ranking] === player[state.ranking]) + 1;
    return `<button class="rank-row" data-player-id="${escapeHtml(player.id)}" aria-label="${rank}위 ${escapeHtml(player.name)}, ${player[state.ranking]} ${unit}, 개인 기록 보기"><span class="rank-index">${String(rank).padStart(2, '0')}</span><span class="rank-avatar">${player.number}</span><span class="rank-name">${escapeHtml(player.name)}<small>${escapeHtml(role(player))} · ${player.number}번</small></span><span class="rank-value">${player[state.ranking]}<span class="rank-unit">${unit}</span></span></button>`;
  }).join('');
  bindPlayerButtons(document.querySelector('#ranking-list'));
}

function renderStats() {
  const summary = data.summary;
  document.querySelector('#total-games').textContent = summary.played;
  document.querySelector('#season-record').textContent = `${summary.wins}승 ${summary.draws}무 ${summary.losses}패`;
  document.querySelector('#win-rate').textContent = percent(summary.winRate);
  document.querySelector('#goals-for').textContent = summary.goalsFor;
  document.querySelector('#goals-against').textContent = summary.goalsAgainst;
  document.querySelector('#recent-form').innerHTML = `<span>시즌 전적</span><b class="form-dot">${summary.wins}승</b><b class="form-dot d">${summary.draws}무</b><b class="form-dot l">${summary.losses}패</b>`;
  document.querySelector('#player-total').textContent = `등록 선수 ${data.players.length}명`;
  const quality = document.querySelector('#record-quality');
  const notes = data.qualityNotes || [];
  quality.hidden = !notes.length;
  quality.innerHTML = notes.length ? `<summary>원본 기록의 집계 차이 확인 <span>${notes.length}건</span></summary><ul>${notes.map(note => `<li>${escapeHtml(note)}</li>`).join('')}</ul><p>원본 수식이나 값을 수정하지 않고, 각 표의 기록을 그대로 표시했습니다.</p>` : '';
  const individualGoals = data.players.reduce((total, player) => total + player.goals, 0);
  document.querySelector('#records-note').textContent = individualGoals === summary.goalsFor ? `${sourceCaption()} · 팀 기록과 선수 기록 원본값을 표시합니다.` : `팀 득점 ${summary.goalsFor}골은 팀 기록 원본값이며, 선수별 득점 합계는 ${individualGoals}골입니다. 차이는 원본 그대로 유지했습니다.`;
}

function showKit() {
  openDialog('<div class="eyebrow">FCSA · 우리의 유니폼</div><h2 id="dialog-title">유니폼 원본 시안</h2><p>팀에서 제공한 앞면·뒷면 시안입니다. 시안의 이름과 등번호는 실제 선수 정보가 아닙니다.</p><img class="dialog-image" src="assets/kit-original.jpg" alt="FCSA 유니폼 앞면과 뒷면 및 검정 반바지 원본 시안">');
}

document.querySelector('#view-kit').addEventListener('click', showKit);
document.querySelector('#films').addEventListener('click', event => {
  const link = event.target.closest('[data-video]');
  if (!link) return;
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const videoId = link.dataset.video;
  if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) return;
  event.preventDefault();
  const title = link.dataset.videoTitle || link.getAttribute('aria-label');
  const caption = link.dataset.publishedAt ? `${videoDate(link.dataset.publishedAt)} 업로드` : 'FC쏘아 채널';
  openDialog(`<span class="source-badge">FCSA TV · ${escapeHtml(caption)}</span><h2 id="dialog-title">${escapeHtml(title)}</h2><iframe class="dialog-video" src="https://www.youtube-nocookie.com/embed/${videoId}?autoplay=1&playsinline=1&rel=0" title="${escapeHtml(title)} 영상" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe><div class="video-actions"><a class="text-link" href="https://www.youtube.com/watch?v=${videoId}" target="_blank" rel="noopener">YouTube에서 보기 ↗</a><span>재생이 안 되면 YouTube에서 볼 수 있어요.</span></div>`, 'video');
});

const videoLayout = document.querySelector('#video-list');
const videoStatus = document.querySelector('#video-status');
let videoSignature = '';
let videoRequestPending = false;
let pendingVideoFeed = null;

function videoDate(value, includeTime = false) {
  const options = {timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit'};
  if (includeTime) Object.assign(options, {hour: '2-digit', minute: '2-digit', hour12: false});
  return new Intl.DateTimeFormat('ko-KR', options).format(new Date(value));
}

function validateVideos(feed) {
  if (feed.version !== 1 || feed.channelId !== 'UCRAyNAfsPxvbI3Kd1xRntfQ' || !Number.isFinite(Date.parse(feed.fetchedAt)) || !Array.isArray(feed.videos) || !feed.videos.length || feed.videos.length > 4) throw new Error('Invalid video feed');
  const ids = new Set();
  for (const video of feed.videos) {
    if (!/^[A-Za-z0-9_-]{11}$/.test(video.id) || ids.has(video.id) || typeof video.title !== 'string' || !video.title.trim() || !Number.isFinite(Date.parse(video.publishedAt))) throw new Error('Invalid video entry');
    ids.add(video.id);
    if (video.url !== `https://www.youtube.com/watch?v=${video.id}` || video.thumbnail !== `https://i.ytimg.com/vi/${video.id}/hqdefault.jpg`) throw new Error('Invalid video URL');
  }
}

function renderVideos(feed) {
  const signature = JSON.stringify(feed.videos);
  // Apply pending updates after playback, preserving the focused card when possible.
  if (dialog.open) { pendingVideoFeed = feed; return; }
  pendingVideoFeed = null;
  if (signature !== videoSignature) {
    const focusedVideo = videoLayout.contains(document.activeElement) ? document.activeElement.closest('[data-video]')?.dataset.video : null;
    const cards = feed.videos.map((video, index) => {
      const attributes = `href="${escapeHtml(video.url)}" target="_blank" rel="noopener" data-video="${video.id}" data-video-title="${escapeHtml(video.title)}" data-published-at="${escapeHtml(video.publishedAt)}" aria-label="${escapeHtml(video.title)} 영상 보기"`;
      const published = `${videoDate(video.publishedAt)} 업로드`;
      if (index === 0) return `<a class="film-main" ${attributes}><img src="${escapeHtml(video.thumbnail)}" alt="${escapeHtml(video.title)} 썸네일" width="480" height="360" loading="lazy"><span class="play-circle" aria-hidden="true">▷</span><div class="film-main-content"><span class="film-category">최신 업로드 · ${published}</span><h3>${escapeHtml(video.title)}</h3><p>가장 최근에 올라온 우리의 장면.</p></div></a>`;
      return `<a class="film-small" ${attributes}><div class="film-thumb match-thumb"><img src="${escapeHtml(video.thumbnail)}" alt="" width="480" height="360" loading="lazy"></div><div><small>${published}</small><h3>${escapeHtml(video.title)}</h3><span class="film-hint">영상 보기 ▷</span></div></a>`;
    });
    videoLayout.innerHTML = `${cards[0]}${cards.length > 1 ? `<div class="film-side">${cards.slice(1).join('')}</div>` : ''}`;
    videoLayout.classList.toggle('single-video', cards.length === 1);
    videoSignature = signature;
    if (focusedVideo) {
      const nextFocus = [...videoLayout.querySelectorAll('[data-video]')].find(link => link.dataset.video === focusedVideo) || videoLayout.querySelector('[data-video]');
      nextFocus?.focus({preventScroll: true});
    }
  }
  const stale = Date.now() - Date.parse(feed.fetchedAt) > 2 * 60 * 60 * 1000;
  videoStatus.textContent = `${stale ? '업데이트가 지연되어 마지막으로 확인한 영상을 표시합니다. · ' : ''}최근 영상 ${feed.videos.length}개 · 마지막 확인 ${videoDate(feed.fetchedAt, true)}`;
}

async function refreshVideos() {
  if (videoRequestPending || document.hidden) return;
  videoRequestPending = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const url = new URL('data/videos.json', document.baseURI);
    url.searchParams.set('refresh', String(Date.now()));
    const response = await fetch(url, {cache: 'no-store', signal: controller.signal});
    if (!response.ok) throw new Error(`Video feed HTTP ${response.status}`);
    const feed = await response.json();
    validateVideos(feed);
    renderVideos(feed);
  } catch (error) {
    videoStatus.textContent = '최신 목록을 확인하지 못해 이전 영상을 표시하고 있어요. 잠시 후 다시 확인합니다.';
    console.warn('Unable to refresh FCSA videos:', error.message);
  } finally {
    clearTimeout(timeout);
    videoRequestPending = false;
  }
}

refreshVideos();
setInterval(refreshVideos, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshVideos(); });

const menuButton = document.querySelector('.menu-button');
const navigation = document.querySelector('#navigation');
function closeMenu() {
  navigation.classList.remove('open');
  menuButton.setAttribute('aria-expanded', 'false');
  menuButton.setAttribute('aria-label', '메뉴 열기');
  menuButton.textContent = '☰';
}
menuButton.addEventListener('click', () => {
  const isOpen = navigation.classList.toggle('open');
  menuButton.setAttribute('aria-expanded', String(isOpen));
  menuButton.setAttribute('aria-label', isOpen ? '메뉴 닫기' : '메뉴 열기');
  menuButton.textContent = isOpen ? '×' : '☰';
});
navigation.querySelectorAll('a').forEach(link => link.addEventListener('click', closeMenu));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && navigation.classList.contains('open')) { closeMenu(); menuButton.focus(); }
});

function initializeRecords() {
  if (!data || data.version !== 1 || !Array.isArray(data.players) || !data.summary) {
    document.querySelector('#fixture-list').innerHTML = '<p class="empty" role="alert">기록 파일을 불러오지 못했습니다. 페이지를 새로고침해 주세요.</p>';
    document.querySelector('#player-grid').innerHTML = '<p class="empty">선수 기록을 불러오지 못했습니다.</p>';
    document.querySelector('#source-status').textContent = '기록 파일 로드 실패';
    return;
  }
  const months = [...new Set(data.dates.map(item => item.date.slice(0, 7)))].sort().reverse();
  document.querySelector('#record-month').innerHTML = `<option value="all">${data.season} 전체</option>${months.map(month => `<option value="${month}">${Number(month.slice(5))}월</option>`).join('')}`;
  document.querySelectorAll('[data-record-view]').forEach(button => button.addEventListener('click', () => {
    state.recordView = button.dataset.recordView;
    state.recordPage = 1;
    activate(button, '[data-record-view]');
    renderRecords();
  }));
  document.querySelector('#record-month').addEventListener('change', event => { state.month = event.target.value; state.recordPage = 1; renderRecords(); });
  document.querySelector('#record-prev').addEventListener('click', () => { state.recordPage--; renderRecords(); });
  document.querySelector('#record-next').addEventListener('click', () => { state.recordPage++; renderRecords(); });
  document.querySelectorAll('[data-position]').forEach(button => button.addEventListener('click', () => {
    state.position = button.dataset.position;
    state.playerPage = 1;
    activate(button, '[data-position]');
    renderPlayers();
  }));
  document.querySelector('#player-search').addEventListener('input', event => { state.search = event.target.value.trim(); state.playerPage = 1; renderPlayers(); });
  document.querySelector('#player-prev').addEventListener('click', () => { state.playerPage--; renderPlayers(); });
  document.querySelector('#player-next').addEventListener('click', () => { state.playerPage++; renderPlayers(); });
  document.querySelectorAll('[data-ranking]').forEach(button => button.addEventListener('click', () => { state.ranking = button.dataset.ranking; activate(button, '[data-ranking]'); renderRanking(); }));
  renderStrip();
  renderRecords();
  renderPlayers();
  renderRanking();
  renderStats();
}

initializeRecords();
