// --- Tab Routing ---

const TABS = ['browse', 'queue', 'summaries', 'settings'];
const DEFAULT_TAB = 'browse';

function switchTab(tabId) {
  if (!TABS.includes(tabId)) {
    tabId = DEFAULT_TAB;
  }

  // Show/hide sections
  for (const tab of TABS) {
    const section = document.getElementById(tab);
    if (!section) continue;
    if (tab === tabId) {
      section.removeAttribute('hidden');
    } else {
      section.setAttribute('hidden', '');
    }
  }

  // Update active indicator on nav links
  const links = document.querySelectorAll('a[data-tab]');
  for (const link of links) {
    if (link.dataset.tab === tabId) {
      link.setAttribute('aria-current', 'page');
    } else {
      link.removeAttribute('aria-current');
    }
  }

  // Queue polling: start when visible, stop when hidden
  if (tabId === 'queue') { startPolling(); } else { stopPolling(); }
}

function getTabFromHash() {
  const hash = location.hash.slice(1);
  return TABS.includes(hash) ? hash : DEFAULT_TAB;
}

window.addEventListener('hashchange', () => switchTab(getTabFromHash()));

document.addEventListener('DOMContentLoaded', () => {
  switchTab(getTabFromHash());
});

// --- API Helper ---

function clearError(container) {
  const existing = container.querySelector('article[role="alert"]');
  if (existing) {
    existing.remove();
  }
}

function showError(container, message) {
  clearError(container);
  const article = document.createElement('article');
  article.setAttribute('role', 'alert');
  article.textContent = message;
  container.appendChild(article);
}

async function apiFetch(path, opts = {}) {
  const container = opts.container || null;
  if (opts.container) {
    delete opts.container;
  }

  if (container) {
    clearError(container);
    container.setAttribute('aria-busy', 'true');
  }

  // Set Content-Type for JSON bodies on POST/PUT/PATCH
  const method = (opts.method || 'GET').toUpperCase();
  if (['POST', 'PUT', 'PATCH'].includes(method) && opts.body && !(opts.body instanceof FormData)) {
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    if (typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
    }
  }

  let responseReceived = false;

  try {
    const res = await fetch(path, opts);
    responseReceived = true;

    if (!res.ok) {
      const err = await res.json().catch(() => null);
      let message;
      if (err && err.detail) {
        message = err.detail;
      } else if (res.status === 401) {
        message = 'Authentication required. Check your Claude auth status in Settings.';
      } else if (res.status === 404) {
        message = 'The requested resource was not found.';
      } else if (res.status >= 500) {
        message = 'Something went wrong on the server. Try again or check the server logs.';
      } else {
        message = 'An unexpected error occurred. Try again.';
      }
      if (container) {
        showError(container, message);
      }
      throw new Error(message);
    }

    return await res.json();
  } catch (error) {
    if (responseReceived) {
      throw error;
    }
    const message = 'Unable to reach server. Check that the app is running and try again.';
    if (container) {
      showError(container, message);
    }
    throw new Error(message);
  } finally {
    if (container) {
      container.removeAttribute('aria-busy');
    }
  }
}

// --- Browse Tab ---

const browseState = {
  urlType: null,      // 'video' | 'channel' | 'playlist' | null
  channelId: null,    // channel_id for channel URL fetches
  playlistId: null,   // playlist_id for playlist URL fetches
  page: 1,            // current page (reset on filter change or new URL)
  filters: { visibility: 'all', dateFrom: '', dateTo: '' },
  selected: new Set(),  // selected video IDs
  videos: [],           // current video list data
};

function formatDuration(seconds) {
  if (seconds == null) return '--:--';
  seconds = Math.floor(seconds);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }
  return `${m}:${String(s).padStart(2, '0')}`;
}

function formatDate(yyyymmdd) {
  if (!yyyymmdd || yyyymmdd.length !== 8) return '--';
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}

function detectUrlType(url) {
  if (/youtube\.com\/watch\?.*v=|youtu\.be\//.test(url)) return 'video';
  if (/youtube\.com\/(channel\/|c\/|@)/.test(url)) return 'channel';
  if (/youtube\.com\/playlist\?.*list=/.test(url)) return 'playlist';
  return null;
}

function renderVideoCard(info) {
  const resultsDiv = document.getElementById('browse-results');
  resultsDiv.innerHTML = '';

  const article = document.createElement('article');
  article.className = 'video-card';
  article.innerHTML = `
    <img src="${info.thumbnail || ''}" alt="${info.title || ''}" loading="lazy" width="160">
    <div>
      <h3>${info.title || 'Untitled'}</h3>
      <small>${info.channel || 'Unknown channel'} / ${formatDuration(info.duration)} / ${formatDate(info.upload_date)}</small>
      <div style="margin-top:1rem">
        <div class="grid">
          <label>Dedup Mode
            <select id="card-dedup-mode">
              <option value="regular" selected>Regular (pHash)</option>
              <option value="slides">Slides (SSIM)</option>
              <option value="ocr">OCR (text match)</option>
              <option value="none">None</option>
            </select>
          </label>
          <label>Keyframe Mode
            <select id="card-keyframe-mode">
              <option value="image" selected>Image</option>
              <option value="ocr">OCR</option>
              <option value="ocr+image">OCR + Image</option>
              <option value="ocr+inline">OCR Inline</option>
              <option value="ocr+inline+image">OCR Inline + Image</option>
              <option value="none">None</option>
            </select>
          </label>
        </div>
        <button id="card-add-queue" onclick="submitSingleToQueue('${info.id}')">Add to Queue</button>
      </div>
    </div>
  `;
  resultsDiv.appendChild(article);
}

function renderVideoTable(videos, showVisibility) {
  const resultsDiv = document.getElementById('browse-results');
  browseState.videos = videos;
  browseState.selected.clear();

  if (videos.length === 0) {
    // Remove table if it exists, show empty state
    const existing = resultsDiv.querySelector('figure');
    if (existing) existing.remove();
    if (!resultsDiv.querySelector('.empty-msg')) {
      const p = document.createElement('p');
      p.className = 'empty-msg';
      p.style.color = '#7b8495';
      p.textContent = browseState.urlType === 'channel'
        ? 'No videos found matching the current filters. Try adjusting the visibility or date range.'
        : 'This playlist contains no videos.';
      resultsDiv.appendChild(p);
    }
    updateQueueButton();
    return;
  }

  // Remove empty msg if present
  const emptyMsg = resultsDiv.querySelector('.empty-msg');
  if (emptyMsg) emptyMsg.remove();

  // Build or replace table
  let figure = resultsDiv.querySelector('figure');
  if (!figure) {
    figure = document.createElement('figure');
    // Insert after filter row if present, otherwise after h3
    const filterRow = resultsDiv.querySelector('.filter-row');
    if (filterRow) {
      filterRow.after(figure);
    } else {
      const h3 = resultsDiv.querySelector('h3');
      if (h3) h3.after(figure);
      else resultsDiv.appendChild(figure);
    }
  }

  const visCol = showVisibility ? '<th>Visibility</th>' : '';
  let html = `<table class="browse-table"><thead><tr>
    <th><input type="checkbox" id="select-all" aria-label="Select all videos"></th>
    <th>Thumbnail</th>
    <th>Title</th>
    <th>Duration</th>
    ${visCol}
    <th>Upload Date</th>
  </tr></thead><tbody>`;

  for (const v of videos) {
    const visBadge = showVisibility
      ? `<td>${v.visibility === 'members_only'
          ? '<mark class="badge-members">Members Only</mark>'
          : '<small>Public</small>'}</td>`
      : '';
    html += `<tr>
      <td><input type="checkbox" class="video-check" value="${v.id}"></td>
      <td><img src="${v.thumbnail || ''}" alt="" loading="lazy"></td>
      <td>${v.title || 'Untitled'}</td>
      <td style="text-align:right"><small style="color:#7b8495">${formatDuration(v.duration)}</small></td>
      ${visBadge}
      <td><small style="color:#7b8495">${formatDate(v.upload_date)}</small></td>
    </tr>`;
  }

  html += '</tbody></table>';
  figure.innerHTML = html;

  // Bind Select All checkbox
  const selectAll = figure.querySelector('#select-all');
  selectAll.addEventListener('change', () => {
    const checks = figure.querySelectorAll('.video-check');
    checks.forEach(cb => { cb.checked = selectAll.checked; });
    updateSelection();
  });

  // Bind individual checkboxes
  figure.querySelectorAll('.video-check').forEach(cb => {
    cb.addEventListener('change', () => updateSelection());
  });

  updateQueueButton();
}

function updateSelection() {
  const checks = document.querySelectorAll('.video-check');
  const selectAll = document.getElementById('select-all');
  browseState.selected.clear();
  let checkedCount = 0;
  checks.forEach(cb => {
    if (cb.checked) {
      browseState.selected.add(cb.value);
      checkedCount++;
    }
  });
  // Update Select All indeterminate state
  if (selectAll) {
    selectAll.checked = checkedCount === checks.length && checks.length > 0;
    selectAll.indeterminate = checkedCount > 0 && checkedCount < checks.length;
  }
  updateQueueButton();
}

function updateQueueButton() {
  const btn = document.getElementById('queue-submit-btn');
  if (!btn) return;
  const count = browseState.selected.size;
  btn.disabled = count === 0;
  btn.textContent = count === 0 ? 'Add to Queue' : `Add ${count} to Queue`;
}

function buildResultsContainer(headerText, isChannel) {
  const resultsDiv = document.getElementById('browse-results');
  resultsDiv.innerHTML = '';

  // Header
  const h3 = document.createElement('h3');
  h3.textContent = headerText;
  resultsDiv.appendChild(h3);

  // Filter row (channel only, per D-03 and D-04)
  if (isChannel) {
    const filterDiv = document.createElement('div');
    filterDiv.className = 'grid filter-row';
    filterDiv.innerHTML = `
      <label>Visibility
        <select id="filter-visibility">
          <option value="all" selected>All</option>
          <option value="public">Public</option>
          <option value="members_only">Members Only</option>
        </select>
      </label>
      <fieldset>
        <legend>Date Range</legend>
        <div class="grid">
          <button type="button" class="outline date-preset" data-preset="30d">Last 30 days</button>
          <button type="button" class="outline date-preset" data-preset="1y">Last year</button>
          <button type="button" class="outline date-preset" data-preset="all">All time</button>
        </div>
        <div class="grid">
          <label>From <input type="date" id="filter-date-from"></label>
          <label>To <input type="date" id="filter-date-to"></label>
        </div>
      </fieldset>
    `;
    resultsDiv.appendChild(filterDiv);
  }

  // Processing options (per D-12)
  const optionsDiv = document.createElement('div');
  optionsDiv.className = 'grid';
  optionsDiv.id = 'processing-options';
  optionsDiv.innerHTML = `
    <label>Dedup Mode
      <select id="dedup-mode">
        <option value="regular" selected>Regular (pHash)</option>
        <option value="slides">Slides (SSIM)</option>
        <option value="ocr">OCR (text match)</option>
        <option value="none">None</option>
      </select>
    </label>
    <label>Keyframe Mode
      <select id="keyframe-mode">
        <option value="image" selected>Image</option>
        <option value="ocr">OCR</option>
        <option value="ocr+image">OCR + Image</option>
        <option value="ocr+inline">OCR Inline</option>
        <option value="ocr+inline+image">OCR Inline + Image</option>
        <option value="none">None</option>
      </select>
    </label>
  `;
  resultsDiv.appendChild(optionsDiv);

  // Submit button (per D-13)
  const submitBtn = document.createElement('button');
  submitBtn.id = 'queue-submit-btn';
  submitBtn.textContent = 'Add to Queue';
  submitBtn.disabled = true;
  submitBtn.addEventListener('click', submitSelectedToQueue);
  resultsDiv.appendChild(submitBtn);

  // Success message area
  const successDiv = document.createElement('div');
  successDiv.id = 'queue-success';
  resultsDiv.appendChild(successDiv);
}

async function fetchChannelVideos() {
  const { channelId, page, filters } = browseState;
  const params = new URLSearchParams({ visibility: filters.visibility, page, per_page: 20 });
  if (filters.dateFrom) params.set('date_from', filters.dateFrom.replace(/-/g, ''));
  if (filters.dateTo) params.set('date_to', filters.dateTo.replace(/-/g, ''));

  const resultsDiv = document.getElementById('browse-results');
  const videos = await apiFetch(`/api/channel/${channelId}/videos?${params}`, { container: resultsDiv });
  renderVideoTable(videos, true);
  renderPagination(videos.length);
}

async function fetchPlaylistVideos() {
  const resultsDiv = document.getElementById('browse-results');
  const videos = await apiFetch(`/api/playlist/${browseState.playlistId}/videos`, { container: resultsDiv });
  renderVideoTable(videos, false);
}

function renderPagination(resultCount) {
  const resultsDiv = document.getElementById('browse-results');
  let nav = resultsDiv.querySelector('.pagination');
  if (!nav) {
    nav = document.createElement('nav');
    nav.className = 'pagination';
    // Insert after table figure, before processing options
    const figure = resultsDiv.querySelector('figure');
    const options = document.getElementById('processing-options');
    if (figure && options) {
      options.before(nav);
    } else {
      resultsDiv.appendChild(nav);
    }
  }

  const page = browseState.page;
  const perPage = 20;
  const hasNext = resultCount >= perPage;

  // Determine page range (show up to 5 pages centered around current)
  const maxPage = hasNext ? page + 2 : page;
  const startPage = Math.max(1, page - 2);
  const endPage = Math.min(maxPage, startPage + 4);

  let html = `<button ${page <= 1 ? 'disabled' : ''} onclick="goToPage(${page - 1})">Previous</button>`;
  for (let i = startPage; i <= endPage; i++) {
    html += `<button ${i === page ? 'aria-current="page"' : ''} onclick="goToPage(${i})">${i}</button>`;
  }
  html += `<button ${!hasNext ? 'disabled' : ''} onclick="goToPage(${page + 1})">Next</button>`;
  nav.innerHTML = html;
}

function goToPage(page) {
  browseState.page = page;
  fetchChannelVideos();
}

async function handleBrowseFetch(e) {
  e.preventDefault();
  const input = document.getElementById('browse-url-input');
  const url = input.value.trim();
  if (!url) return;

  const resultsDiv = document.getElementById('browse-results');

  // Hide empty state
  const emptyState = document.getElementById('browse-empty-state');
  if (emptyState) emptyState.hidden = true;

  // Reset state
  browseState.urlType = null;
  browseState.channelId = null;
  browseState.playlistId = null;
  browseState.page = 1;
  browseState.filters = { visibility: 'all', dateFrom: '', dateTo: '' };
  browseState.selected.clear();
  browseState.videos = [];
  resultsDiv.innerHTML = '';

  const urlType = detectUrlType(url);
  if (!urlType) {
    showError(resultsDiv, 'Unrecognized URL format. Paste a YouTube video, channel, or playlist URL.');
    return;
  }

  browseState.urlType = urlType;

  try {
    if (urlType === 'video') {
      const info = await apiFetch(`/api/video/info?url=${encodeURIComponent(url)}`, { container: resultsDiv });
      renderVideoCard(info);
    } else if (urlType === 'channel') {
      const info = await apiFetch(`/api/video/info?url=${encodeURIComponent(url)}`, { container: resultsDiv });
      browseState.channelId = info.channel_id;
      buildResultsContainer(info.channel || info.title || 'Channel', true);
      await fetchChannelVideos();
    } else if (urlType === 'playlist') {
      // Extract playlist ID from URL
      const match = url.match(/list=([^&]+)/);
      if (!match) {
        showError(resultsDiv, 'Could not extract playlist ID from URL.');
        return;
      }
      browseState.playlistId = match[1];
      // Fetch playlist info for the title
      const info = await apiFetch(`/api/video/info?url=${encodeURIComponent(url)}`, { container: resultsDiv });
      buildResultsContainer(info.title || 'Playlist', false);
      await fetchPlaylistVideos();
    }
  } catch (err) {
    // apiFetch already shows the error in the container
  }
}

async function submitSingleToQueue(videoId) {
  const resultsDiv = document.getElementById('browse-results');
  const dedupMode = document.getElementById('card-dedup-mode').value;
  const keyframeMode = document.getElementById('card-keyframe-mode').value;

  try {
    await apiFetch('/api/queue', {
      method: 'POST',
      body: { video_ids: [videoId], dedup_mode: dedupMode, keyframe_mode: keyframeMode },
      container: resultsDiv,
    });
    showSuccess(resultsDiv, '1 video added to queue.');
  } catch (err) {
    // apiFetch already handles error display
  }
}

async function submitSelectedToQueue() {
  const videoIds = Array.from(browseState.selected);
  if (videoIds.length === 0) return;

  const resultsDiv = document.getElementById('browse-results');
  const dedupMode = document.getElementById('dedup-mode').value;
  const keyframeMode = document.getElementById('keyframe-mode').value;

  try {
    await apiFetch('/api/queue', {
      method: 'POST',
      body: { video_ids: videoIds, dedup_mode: dedupMode, keyframe_mode: keyframeMode },
      container: resultsDiv,
    });
    showSuccess(resultsDiv, `${videoIds.length} video(s) added to queue.`);
    // Clear selection (per D-14)
    browseState.selected.clear();
    document.querySelectorAll('.video-check').forEach(cb => { cb.checked = false; });
    const selectAll = document.getElementById('select-all');
    if (selectAll) { selectAll.checked = false; selectAll.indeterminate = false; }
    updateQueueButton();
  } catch (err) {
    // apiFetch already handles error display
  }
}

function showSuccess(container, message) {
  const successDiv = document.getElementById('queue-success') || container;
  successDiv.innerHTML = '';
  const article = document.createElement('article');
  article.className = 'success-msg';
  article.innerHTML = `${message} <a href="#queue" onclick="switchTab('queue')">View Queue</a>`;
  successDiv.appendChild(article);
  // Auto-dismiss after 5s (CSS animation handles visual fade, JS removes from DOM)
  setTimeout(() => { article.remove(); }, 5500);
}

function bindFilterEvents() {
  // Visibility dropdown (per D-09)
  document.addEventListener('change', (e) => {
    if (e.target.id === 'filter-visibility') {
      browseState.filters.visibility = e.target.value;
      browseState.page = 1;
      fetchChannelVideos();
    }
    if (e.target.id === 'filter-date-from') {
      browseState.filters.dateFrom = e.target.value;
      browseState.page = 1;
      fetchChannelVideos();
    }
    if (e.target.id === 'filter-date-to') {
      browseState.filters.dateTo = e.target.value;
      browseState.page = 1;
      fetchChannelVideos();
    }
  });

  // Date preset buttons (per D-10)
  document.addEventListener('click', (e) => {
    if (!e.target.classList.contains('date-preset')) return;
    const preset = e.target.dataset.preset;
    const fromInput = document.getElementById('filter-date-from');
    const toInput = document.getElementById('filter-date-to');
    if (!fromInput || !toInput) return;

    const today = new Date();
    if (preset === '30d') {
      const from = new Date(today);
      from.setDate(from.getDate() - 30);
      fromInput.value = from.toISOString().slice(0, 10);
      toInput.value = today.toISOString().slice(0, 10);
    } else if (preset === '1y') {
      const from = new Date(today);
      from.setFullYear(from.getFullYear() - 1);
      fromInput.value = from.toISOString().slice(0, 10);
      toInput.value = today.toISOString().slice(0, 10);
    } else if (preset === 'all') {
      fromInput.value = '';
      toInput.value = '';
    }

    browseState.filters.dateFrom = fromInput.value;
    browseState.filters.dateTo = toInput.value;
    browseState.page = 1;
    fetchChannelVideos();
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('browse-fetch-form');
  if (form) {
    form.addEventListener('submit', handleBrowseFetch);
  }
  bindFilterEvents();
});

// --- Queue Tab ---

const queueState = {
  jobs: [],
  pollInterval: null,
  pollRate: null,
};

const STEP_MAP = {
  downloading: { index: 0, label: 'Downloading...' },
  transcribing: { index: 1, label: 'Transcribing...' },
  extracting_keyframes: { index: 2, label: 'Extracting keyframes...' },
  deduplicating: { index: 2, label: 'Deduplicating...' },
  ocr: { index: 3, label: 'Running OCR...' },
  summarizing: { index: 4, label: 'Summarizing...' },
  cleanup: { index: 4, label: 'Finishing up...' },
};
const TOTAL_SEGMENTS = 5;

function formatCreatedTime(isoString) {
  const date = new Date(isoString);
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 60000) return 'Just now';
  if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)} min ago`;
  if (diffMs < 86400000) return `${Math.floor(diffMs / 3600000)}h ago`;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    + ', ' + date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function getProgressSegments(job) {
  const segments = new Array(TOTAL_SEGMENTS).fill('pending');
  let label = 'Waiting...';

  if (job.status === 'pending') {
    return { segments, label };
  }

  if (job.status === 'done') {
    segments.fill('completed');
    return { segments, label: 'Complete' };
  }

  const stepInfo = STEP_MAP[job.current_step];
  const activeIndex = stepInfo ? stepInfo.index : -1;

  if (job.status === 'cancelled') {
    for (let i = 0; i < activeIndex && i < TOTAL_SEGMENTS; i++) {
      segments[i] = 'completed';
    }
    return { segments, label: 'Cancelled' };
  }

  if (job.status === 'failed') {
    for (let i = 0; i < activeIndex && i < TOTAL_SEGMENTS; i++) {
      segments[i] = 'completed';
    }
    if (activeIndex >= 0 && activeIndex < TOTAL_SEGMENTS) {
      segments[activeIndex] = 'failed';
    }
    const stepName = stepInfo ? stepInfo.label.replace('...', '') : job.current_step;
    return { segments, label: `Failed at ${stepName}` };
  }

  if (job.status === 'processing') {
    for (let i = 0; i < activeIndex && i < TOTAL_SEGMENTS; i++) {
      segments[i] = 'completed';
    }
    if (activeIndex >= 0 && activeIndex < TOTAL_SEGMENTS) {
      segments[activeIndex] = 'active';
    }
    label = stepInfo ? stepInfo.label : 'Processing...';
    return { segments, label };
  }

  return { segments, label };
}

// Badge text and CSS class mapping: badge-pending, badge-processing, badge-done, badge-failed, badge-cancelled
const BADGE_TEXT = {
  pending: 'PENDING',
  processing: 'PROCESSING',
  done: 'COMPLETE',
  failed: 'FAILED',
  cancelled: 'CANCELLED',
};

function renderJobCard(job) {
  const article = document.createElement('article');
  article.className = 'job-card' + ((job.status === 'cancelled' || job.status === 'failed') ? ' dimmed' : '');
  article.dataset.jobId = job.id;

  const { segments, label } = getProgressSegments(job);
  const badgeText = BADGE_TEXT[job.status] || job.status.toUpperCase();

  const segmentsHtml = segments.map(s => `<div class="progress-segment ${s}"></div>`).join('');

  let errorHtml = '';
  if (job.status === 'failed' && job.error) {
    errorHtml = `<small class="job-error" title="${job.error.replace(/"/g, '&quot;')}">${job.error}</small>`;
  }

  let warningsHtml = '';
  try {
    const warnings = job.warnings ? JSON.parse(job.warnings) : [];
    if (warnings.length > 0) {
      const items = warnings.map(w => `<li>${w}</li>`).join('');
      warningsHtml = `<details class="job-warnings"><summary>${warnings.length} warning(s)</summary><ul>${items}</ul></details>`;
    }
  } catch (e) {
    // Invalid JSON, skip warnings
  }

  let actionsHtml = '';
  if (job.status === 'pending' || job.status === 'processing') {
    actionsHtml = `<button class="outline cancel-btn" data-job-id="${job.id}">Cancel Job</button>`;
  } else if (job.status === 'done') {
    actionsHtml = `<a href="#summaries" class="view-summary-link" data-job-id="${job.id}">View Summary</a>`;
  }

  article.innerHTML = `
    <img src="${job.thumbnail_url || ''}" alt="" loading="lazy">
    <div class="job-card-content">
      <div class="job-card-header">
        <strong>${job.title || 'Untitled'}</strong>
        <small class="created-time">${formatCreatedTime(job.created_at)}</small>
      </div>
      <small style="color:#7b8495">${job.channel || 'Unknown'} / ${formatDuration(job.duration)} / ${job.dedup_mode} / ${job.keyframe_mode}</small>
      <div style="margin-top:0.25rem">
        <mark class="badge-${job.status}">${badgeText}</mark>
        <small class="step-label">${label}</small>
      </div>
      <div class="progress-bar">${segmentsHtml}</div>
      ${errorHtml}
      ${warningsHtml}
      <div class="job-card-actions">${actionsHtml}</div>
    </div>
  `;

  return article;
}

function renderJobs() {
  const jobList = document.getElementById('queue-job-list');
  const emptyState = document.getElementById('queue-empty-state');
  if (!jobList) return;

  if (queueState.jobs.length === 0) {
    if (emptyState) emptyState.hidden = false;
    jobList.innerHTML = '';
    return;
  }

  if (emptyState) emptyState.hidden = true;
  jobList.innerHTML = '';
  for (const job of queueState.jobs) {
    jobList.appendChild(renderJobCard(job));
  }
}

async function fetchJobs() {
  try {
    const jobs = await apiFetch('/api/queue', { container: document.getElementById('queue-container') });
    queueState.jobs = jobs;
    renderJobs();
    adjustPollingRate();
  } catch (err) {
    // apiFetch already shows error in the container
  }
}

async function cancelJob(jobId) {
  // Optimistic UI update
  const card = document.querySelector(`[data-job-id="${jobId}"]`);
  if (card) {
    card.classList.add('dimmed');
    const badge = card.querySelector('[class^="badge-"]');
    if (badge) {
      badge.className = 'badge-cancelled';
      badge.textContent = 'CANCELLED';
    }
  }

  try {
    await apiFetch(`/api/queue/${jobId}`, { method: 'DELETE' });
    // Next poll will confirm the state
  } catch (err) {
    // Revert optimistic update on non-404 errors
    if (card && !err.message.includes('not found')) {
      card.classList.remove('dimmed');
      console.error('Cancel failed:', err);
    }
  }
}

function hasActiveJobs() {
  return queueState.jobs.some(j => j.status === 'pending' || j.status === 'processing');
}

function adjustPollingRate() {
  const targetRate = hasActiveJobs() ? 3000 : 10000;
  if (queueState.pollRate === targetRate) return;

  if (queueState.pollInterval) {
    clearInterval(queueState.pollInterval);
  }
  queueState.pollInterval = setInterval(fetchJobs, targetRate);
  queueState.pollRate = targetRate;
}

function startPolling() {
  if (queueState.pollInterval) return;
  fetchJobs();
  const rate = hasActiveJobs() ? 3000 : 10000;
  queueState.pollInterval = setInterval(fetchJobs, rate);
  queueState.pollRate = rate;
}

function stopPolling() {
  clearInterval(queueState.pollInterval);
  queueState.pollInterval = null;
  queueState.pollRate = null;
}

// Queue tab: event delegation for cancel buttons and view summary links
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('cancel-btn')) {
    const jobId = e.target.dataset.jobId;
    if (jobId) cancelJob(jobId);
  }
  if (e.target.classList.contains('view-summary-link')) {
    e.preventDefault();
    switchTab('summaries');
  }
});

// Queue tab: start polling if queue is the active tab on page load
document.addEventListener('DOMContentLoaded', () => {
  if (getTabFromHash() === 'queue') {
    startPolling();
  }
});
