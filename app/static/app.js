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

  // Summaries: fetch fresh data on tab show
  if (tabId === 'summaries') { fetchSummaries(); }

  // Settings: re-fetch on every tab activation
  if (tabId === 'settings') { loadSettings(); }
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
              <option value="ocr-inline">OCR Inline</option>
              <option value="ocr-inline+image">OCR Inline + Image</option>
              <option value="none">None</option>
            </select>
          </label>
        </div>
        <button class="single-queue-btn" data-video-id="${info.id}">Add to Queue</button>
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
        <option value="ocr-inline">OCR Inline</option>
        <option value="ocr-inline+image">OCR Inline + Image</option>
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

  let html = `<button class="page-btn" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>Previous</button>`;
  for (let i = startPage; i <= endPage; i++) {
    html += `<button class="page-btn" data-page="${i}" ${i === page ? 'aria-current="page"' : ''}>${i}</button>`;
  }
  html += `<button class="page-btn" data-page="${page + 1}" ${!hasNext ? 'disabled' : ''}>Next</button>`;
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
    showSuccess(resultsDiv, '1 video added to queue.', { href: '#queue', text: 'View Queue' });
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
    showSuccess(resultsDiv, videoIds.length + ' video(s) added to queue.', { href: '#queue', text: 'View Queue' });
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

function showSuccess(container, message, link) {
  // Remove any existing success message in this container
  const existing = container.querySelector('.success-msg');
  if (existing) existing.remove();
  const article = document.createElement('article');
  article.className = 'success-msg';
  if (link) {
    article.innerHTML = message + ' <a href="' + link.href + '">' + link.text + '</a>';
  } else {
    article.textContent = message;
  }
  container.appendChild(article);
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

// Browse tab: event delegation for single-queue and pagination buttons
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('single-queue-btn')) {
    const videoId = e.target.dataset.videoId;
    if (videoId) submitSingleToQueue(videoId);
    return;
  }
  if (e.target.classList.contains('page-btn') && !e.target.disabled) {
    const page = parseInt(e.target.dataset.page, 10);
    if (!isNaN(page)) goToPage(page);
    return;
  }
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

// --- Summaries Tab ---

const summariesState = {
  summaries: [],
  cache: {},
  viewStyle: localStorage.getItem('summaries-view-style') || 'compact',
  expandedId: null,
};

async function fetchSummaries() {
  try {
    const data = await apiFetch('/api/summaries', {
      container: document.getElementById('summaries-container'),
    });
    summariesState.summaries = data;
    // Reset expanded ID if the expanded summary no longer exists
    if (summariesState.expandedId && !data.some(s => s.job_id === summariesState.expandedId)) {
      summariesState.expandedId = null;
    }
    renderSummaries();
  } catch (err) {
    // apiFetch already shows error in the container
  }
}

function renderSummaries() {
  const listEl = document.getElementById('summaries-list');
  const emptyEl = document.getElementById('summaries-empty-state');
  if (!listEl) return;

  if (summariesState.summaries.length === 0) {
    if (emptyEl) emptyEl.hidden = false;
    listEl.innerHTML = '';
    return;
  }

  if (emptyEl) emptyEl.hidden = true;
  listEl.innerHTML = '';
  for (const summary of summariesState.summaries) {
    listEl.appendChild(renderSummaryCard(summary));
  }
  // Re-expand if one was expanded
  if (summariesState.expandedId) {
    expandSummary(summariesState.expandedId);
  }
}

function renderSummaryCard(summary) {
  const article = document.createElement('article');
  const isActive = summariesState.expandedId === summary.job_id;
  const cached = summariesState.cache[summary.job_id];
  const tldr = cached && cached.structured ? cached.structured.tldr || '' : '';

  if (summariesState.viewStyle === 'list') {
    // Title-only list view
    article.className = 'summary-card summary-list-item' + (isActive ? ' summary-card-active' : '');
    article.dataset.jobId = summary.job_id;
    article.innerHTML = `
      <div class="summary-card" data-job-id="${summary.job_id}">
        <div class="summary-card-content" style="flex-direction:row;align-items:center;gap:1rem">
          <strong style="flex:1;min-width:0">${summary.title || 'Untitled'}</strong>
          <small style="color:#7b8495;white-space:nowrap">${summary.channel || 'Unknown'} / ${formatCreatedTime(summary.created_at)}</small>
          <div class="summary-actions" style="margin-top:0">
            <button class="outline summary-btn summary-copy-btn" data-job-id="${summary.job_id}">Copy</button>
            <button class="outline summary-btn summary-export-btn" data-job-id="${summary.job_id}">Export</button>
            <button class="outline summary-btn summary-delete-btn" data-job-id="${summary.job_id}">Delete</button>
          </div>
        </div>
      </div>
    `;
  } else {
    // Compact or full view
    const extraClass = summariesState.viewStyle === 'full' ? ' summary-full-card' : '';
    article.className = 'summary-card' + extraClass + (isActive ? ' summary-card-active' : '');
    article.dataset.jobId = summary.job_id;
    article.innerHTML = `
      <img src="${summary.thumbnail_url || ''}" alt="" loading="lazy">
      <div class="summary-card-content">
        <strong>${summary.title || 'Untitled'}</strong>
        <small style="color:#7b8495">${summary.channel || 'Unknown'} / ${formatDuration(summary.duration)} / ${formatCreatedTime(summary.created_at)}</small>
        <div class="summary-tldr">${tldr}</div>
        <div class="summary-actions">
          <button class="outline summary-btn summary-copy-btn" data-job-id="${summary.job_id}">Copy</button>
          <button class="outline summary-btn summary-export-btn" data-job-id="${summary.job_id}">Export</button>
          <button class="outline summary-btn summary-delete-btn" data-job-id="${summary.job_id}">Delete</button>
        </div>
      </div>
    `;
  }

  return article;
}

function renderMarkdown(text) {
  if (!text) return '';

  // Step 0: Extract code blocks and replace with placeholders
  const codeBlocks = [];
  let result = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const idx = codeBlocks.length;
    codeBlocks.push(`<pre><code>${code.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>`);
    return `<!--CODE_BLOCK_${idx}-->`;
  });

  // Step 1: Headings (shift down: # -> h3, ## -> h4, ### -> h5)
  result = result.replace(/^### (.+)$/gm, '<h5>$1</h5>');
  result = result.replace(/^## (.+)$/gm, '<h4>$1</h4>');
  result = result.replace(/^# (.+)$/gm, '<h3>$1</h3>');

  // Step 2: Inline formatting
  result = result.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  result = result.replace(/\*(.+?)\*/g, '<em>$1</em>');
  result = result.replace(/`([^`]+)`/g, '<code>$1</code>');
  result = result.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  // Step 3: Block-level elements (blockquotes, lists)
  // Blockquotes
  result = result.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');
  // Merge consecutive blockquotes
  result = result.replace(/<\/blockquote>\n<blockquote>/g, '\n');

  // Unordered lists: consecutive lines starting with "- "
  result = result.replace(/(^- .+$(\n^- .+$)*)/gm, (match) => {
    const items = match.split('\n').map(line => `<li>${line.replace(/^- /, '')}</li>`).join('');
    return `<ul>${items}</ul>`;
  });

  // Ordered lists: consecutive lines starting with "N. "
  result = result.replace(/(^\d+\. .+$(\n^\d+\. .+$)*)/gm, (match) => {
    const items = match.split('\n').map(line => `<li>${line.replace(/^\d+\. /, '')}</li>`).join('');
    return `<ol>${items}</ol>`;
  });

  // Step 4: Paragraph breaks (empty lines -> <br>)
  result = result.replace(/\n\n+/g, '<br><br>');
  result = result.replace(/\n/g, '\n');

  // Step 5: Restore code blocks
  for (let i = 0; i < codeBlocks.length; i++) {
    result = result.replace(`<!--CODE_BLOCK_${i}-->`, codeBlocks[i]);
  }

  return result;
}

function sanitizeHtml(html) {
  if (!html) return '';
  // Remove dangerous tags and their contents
  let clean = html.replace(/<(script|iframe|object|embed)[^>]*>[\s\S]*?<\/\1>/gi, '');
  // Remove self-closing variants
  clean = clean.replace(/<(script|iframe|object|embed)[^>]*\/?>/gi, '');
  // Remove on* event handler attributes
  clean = clean.replace(/\s+on\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]*)/gi, '');
  return clean;
}

async function expandSummary(jobId) {
  // Toggle off if already expanded
  if (summariesState.expandedId === jobId) {
    collapseSummary();
    return;
  }

  // Collapse any currently expanded summary
  collapseSummary();
  summariesState.expandedId = jobId;

  // Mark the card as active
  const article = document.querySelector(`article[data-job-id="${jobId}"]`);
  if (article) article.classList.add('summary-card-active');

  // Create expanded container as sibling after the card
  const expandedDiv = document.createElement('div');
  expandedDiv.className = 'summary-expanded';

  if (article) article.after(expandedDiv);

  if (summariesState.cache[jobId]) {
    // Render from cache
    renderExpandedContent(expandedDiv, summariesState.cache[jobId]);
    updateCardTldr(jobId);
  } else {
    // Show loading state
    expandedDiv.innerHTML = '<p aria-busy="true">Loading summary...</p>';
    try {
      const data = await apiFetch(`/api/summaries/${jobId}`);
      summariesState.cache[jobId] = data;
      // Verify we're still expanded on this job (user may have clicked elsewhere)
      if (summariesState.expandedId === jobId) {
        renderExpandedContent(expandedDiv, data);
        updateCardTldr(jobId);
      }
    } catch (err) {
      if (summariesState.expandedId === jobId) {
        expandedDiv.innerHTML = '<p>Could not load this summary. Try again.</p>';
      }
    }
  }
}

function renderExpandedContent(container, data) {
  let structured = data.structured;
  if (!structured && data.structured_summary) {
    try {
      structured = JSON.parse(data.structured_summary);
    } catch (e) {
      console.warn('Failed to parse structured_summary JSON:', e);
    }
  }

  if (structured && structured.summary) {
    container.innerHTML = `
      <h3>${structured.title || ''}</h3>
      <p><strong>TL;DR:</strong> ${structured.tldr || ''}</p>
      <div class="summary-markdown">${sanitizeHtml(renderMarkdown(structured.summary))}</div>
    `;
  } else {
    // Fallback to raw_response
    const raw = data.raw_response || 'No summary content available.';
    container.innerHTML = `<pre>${raw.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</pre>`;
  }
}

function updateCardTldr(jobId) {
  const cached = summariesState.cache[jobId];
  if (!cached) return;
  const structured = cached.structured || (cached.structured_summary ? JSON.parse(cached.structured_summary) : null);
  if (!structured || !structured.tldr) return;
  const card = document.querySelector(`article[data-job-id="${jobId}"]`);
  if (!card) return;
  const tldrDiv = card.querySelector('.summary-tldr');
  if (tldrDiv && !tldrDiv.textContent.trim()) {
    tldrDiv.textContent = structured.tldr;
  }
}

function collapseSummary() {
  const existing = document.querySelector('.summary-expanded');
  if (existing) existing.remove();
  const activeCard = document.querySelector('.summary-card-active');
  if (activeCard) activeCard.classList.remove('summary-card-active');
  summariesState.expandedId = null;
}

async function copySummary(jobId) {
  let data = summariesState.cache[jobId];
  if (!data) {
    try {
      data = await apiFetch(`/api/summaries/${jobId}`);
      summariesState.cache[jobId] = data;
    } catch (err) {
      showError(document.getElementById('summaries-container'), 'Failed to copy to clipboard. Try selecting the text manually.');
      return;
    }
  }

  let structured = data.structured;
  if (!structured && data.structured_summary) {
    try { structured = JSON.parse(data.structured_summary); } catch (e) { /* ignore */ }
  }

  const markdownText = structured
    ? `${structured.title || ''}\n\n${structured.tldr || ''}\n\n${structured.summary || ''}`
    : data.raw_response || '';

  try {
    await navigator.clipboard.writeText(markdownText);
    const btn = document.querySelector(`.summary-copy-btn[data-job-id="${jobId}"]`);
    if (btn) {
      btn.textContent = 'Copied!';
      btn.style.color = '#2ea043';
      setTimeout(() => {
        btn.textContent = 'Copy';
        btn.style.color = '';
      }, 2000);
    }
  } catch (err) {
    showError(document.getElementById('summaries-container'), 'Failed to copy to clipboard. Try selecting the text manually.');
  }
}

async function exportSummary(jobId) {
  try {
    const res = await fetch(`/api/summaries/${jobId}/export`);
    if (!res.ok) {
      showError(document.getElementById('summaries-container'), 'Could not export summary. Try again.');
      return;
    }
    const text = await res.text();

    // Derive filename from title
    const cached = summariesState.cache[jobId];
    const summary = summariesState.summaries.find(s => s.job_id === jobId);
    const title = (cached && cached.structured && cached.structured.title) || (summary && summary.title) || 'summary';
    const filename = title.replace(/[^a-zA-Z0-9]+/g, '-').toLowerCase().slice(0, 60) + '.md';

    const blob = new Blob([text], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    showError(document.getElementById('summaries-container'), 'Could not export summary. Try again.');
  }
}

async function deleteSummary(jobId) {
  if (!confirm('Delete this summary? This action cannot be undone.')) return;

  try {
    await apiFetch(`/api/summaries/${jobId}`, { method: 'DELETE' });

    // Remove card and expanded div from DOM
    const card = document.querySelector(`article[data-job-id="${jobId}"]`);
    if (card) {
      const nextSibling = card.nextElementSibling;
      if (nextSibling && nextSibling.classList.contains('summary-expanded')) {
        nextSibling.remove();
      }
      card.remove();
    }

    // Update state
    summariesState.summaries = summariesState.summaries.filter(s => s.job_id !== jobId);
    delete summariesState.cache[jobId];
    if (summariesState.expandedId === jobId) summariesState.expandedId = null;

    // Show empty state if no summaries left
    if (summariesState.summaries.length === 0) {
      const emptyEl = document.getElementById('summaries-empty-state');
      if (emptyEl) emptyEl.hidden = false;
    }
  } catch (err) {
    showError(document.getElementById('summaries-container'), 'Could not delete summary. Try again.');
  }
}

// Summaries tab: event delegation for card clicks, action buttons, and view toggle
document.addEventListener('click', (e) => {
  // View toggle buttons
  if (e.target.dataset && e.target.dataset.view && e.target.closest('.view-toggle')) {
    summariesState.viewStyle = e.target.dataset.view;
    localStorage.setItem('summaries-view-style', summariesState.viewStyle);
    // Update active state on toggle buttons
    const toggleBtns = document.querySelectorAll('.view-toggle button');
    toggleBtns.forEach(btn => btn.classList.remove('active'));
    e.target.classList.add('active');
    collapseSummary();
    renderSummaries();
    return;
  }

  // Action buttons: stop propagation to prevent card expand
  if (e.target.classList.contains('summary-copy-btn')) {
    e.stopPropagation();
    copySummary(e.target.dataset.jobId);
    return;
  }
  if (e.target.classList.contains('summary-export-btn')) {
    e.stopPropagation();
    exportSummary(e.target.dataset.jobId);
    return;
  }
  if (e.target.classList.contains('summary-delete-btn')) {
    e.stopPropagation();
    deleteSummary(e.target.dataset.jobId);
    return;
  }

  // Card click to expand (find closest summary-card ancestor)
  const card = e.target.closest('.summary-card');
  if (card && card.dataset.jobId && !e.target.closest('.summary-actions')) {
    expandSummary(card.dataset.jobId);
  }
});

// Summaries tab: init view toggle active state on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
  // Set correct active button based on saved preference
  const activeView = summariesState.viewStyle;
  const toggleBtn = document.querySelector(`.view-toggle button[data-view="${activeView}"]`);
  if (toggleBtn) {
    document.querySelectorAll('.view-toggle button').forEach(btn => btn.classList.remove('active'));
    toggleBtn.classList.add('active');
  }
  // If summaries is the initial tab, fetch data
  if (getTabFromHash() === 'summaries') {
    fetchSummaries();
  }
});

// --- Settings Tab ---

const settingsState = {
  cookieStatus: { exists: false, modified: null },
  llmConfig: { model: '', custom_prompt: null },
  defaultPrompt: '',
  authStatus: { loggedIn: false },
};

async function loadSettings() {
  const section = document.getElementById('settings');
  section.setAttribute('aria-busy', 'true');
  try {
    const [cookieRes, llmRes, authRes] = await Promise.all([
      apiFetch('/api/auth/status'),
      apiFetch('/api/settings/llm'),
      apiFetch('/api/settings/auth/claude'),
    ]);
    settingsState.cookieStatus = cookieRes;
    settingsState.llmConfig = { model: llmRes.model, custom_prompt: llmRes.custom_prompt };
    settingsState.defaultPrompt = llmRes.default_prompt;
    settingsState.authStatus = authRes;
    renderCookieStatus();
    renderLlmConfig();
    renderAuthStatus();
  } catch (err) {
    showError(section, 'Could not load settings. Check that the server is running and try again.');
  } finally {
    section.removeAttribute('aria-busy');
  }
}

function renderCookieStatus() {
  const el = document.getElementById('cookie-status');
  if (!el) return;
  if (settingsState.cookieStatus.exists) {
    el.innerHTML = '<span class="settings-status-ok">Cookies loaded</span> (uploaded ' +
      formatCreatedTime(settingsState.cookieStatus.modified) +
      ') <button class="outline settings-clear-btn">Clear Cookies</button>';
  } else {
    el.innerHTML = '<span style="color:#7b8495">No cookies loaded</span>';
  }
}

async function uploadCookies(file) {
  const card = document.getElementById('cookie-card');
  clearError(card);
  try {
    const formData = new FormData();
    formData.append('file', file);
    await apiFetch('/api/auth/cookies', { method: 'POST', body: formData, container: card });
    // Refresh cookie status
    const status = await apiFetch('/api/auth/status');
    settingsState.cookieStatus = status;
    renderCookieStatus();
    // Clear paste textarea if it has content
    const pasteArea = document.getElementById('cookie-paste');
    if (pasteArea) pasteArea.value = '';
    updatePasteBtn();
    showSuccess(card, 'Cookies uploaded successfully.');
  } catch (err) {
    // apiFetch already shows error in card container — only show fallback if no container error
    if (!card.querySelector('article[role="alert"]')) {
      showError(card, 'Failed to upload cookies. Check the file format and try again.');
    }
  }
}

async function clearCookies() {
  if (!confirm('Clear cookies? Members-only content will no longer be accessible.')) return;
  const card = document.getElementById('cookie-card');
  try {
    await apiFetch('/api/auth/cookies', { method: 'DELETE', container: card });
    settingsState.cookieStatus = { exists: false, modified: null };
    renderCookieStatus();
    showSuccess(card, 'Cookies cleared.');
  } catch (err) {
    showError(card, 'Failed to clear cookies. Try again.');
  }
}

function renderLlmConfig() {
  const modelInput = document.getElementById('llm-model');
  const promptArea = document.getElementById('llm-prompt');
  if (modelInput) modelInput.value = settingsState.llmConfig.model;
  if (promptArea) promptArea.value = settingsState.llmConfig.custom_prompt || '';
}

async function saveLlmConfig() {
  const card = document.getElementById('llm-card');
  const model = document.getElementById('llm-model').value.trim();
  const promptVal = document.getElementById('llm-prompt').value.trim();
  const custom_prompt = promptVal === '' ? null : promptVal;
  try {
    await apiFetch('/api/settings/llm', {
      method: 'POST',
      body: { model, custom_prompt },
      container: card,
    });
    card.removeAttribute('aria-busy');
    showSuccess(card, 'Settings saved.');
  } catch (err) {
    // apiFetch already shows error in the container
  }
}

function resetPromptToDefault() {
  document.getElementById('llm-prompt').value = settingsState.defaultPrompt;
}

function renderAuthStatus() {
  const el = document.getElementById('auth-status');
  if (!el) return;
  const card = document.getElementById('auth-card');
  // Remove any previous instruction text
  const prevHelp = card.querySelector('.settings-help');
  if (prevHelp) prevHelp.remove();

  if (settingsState.authStatus.loggedIn) {
    el.innerHTML = '<span class="settings-status-ok">&#10003; Authenticated via OAuth</span>';
  } else if (settingsState.authStatus.cli_error) {
    el.innerHTML = '<span class="settings-status-fail">&#10007; Could not check auth status</span>';
    const helpP = document.createElement('p');
    helpP.className = 'settings-help';
    helpP.textContent = 'Ensure the Claude CLI is installed and accessible.';
    card.appendChild(helpP);
  } else {
    el.innerHTML = '<span class="settings-status-fail">&#10007; Not authenticated</span>';
    const helpP = document.createElement('p');
    helpP.className = 'settings-help';
    helpP.innerHTML = 'Run <code>claude auth login</code> in your terminal to authenticate.';
    card.appendChild(helpP);
  }
}

function updatePasteBtn() {
  const pasteArea = document.getElementById('cookie-paste');
  const pasteBtn = document.getElementById('cookie-paste-btn');
  if (pasteArea && pasteBtn) {
    pasteBtn.disabled = pasteArea.value.trim() === '';
  }
}

// Settings tab: event handlers
document.addEventListener('DOMContentLoaded', () => {
  // Drop zone drag-and-drop
  const dropZone = document.getElementById('cookie-drop-zone');
  if (dropZone) {
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('drag-over'); });
    dropZone.addEventListener('dragleave', () => { dropZone.classList.remove('drag-over'); });
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('drag-over');
      const file = e.dataTransfer.files[0];
      if (file && file.name.endsWith('.txt')) {
        uploadCookies(file);
      } else {
        showError(document.getElementById('cookie-card'), 'Please upload a .txt file.');
      }
    });
    // Click on drop zone triggers file picker (unless clicking the Browse Files button itself)
    dropZone.addEventListener('click', (e) => {
      if (e.target.id !== 'cookie-file-btn') {
        document.getElementById('cookie-file-input').click();
      }
    });
  }

  // Browse Files button
  const fileBtn = document.getElementById('cookie-file-btn');
  if (fileBtn) {
    fileBtn.addEventListener('click', (e) => {
      e.stopPropagation(); // Don't trigger drop zone click
      document.getElementById('cookie-file-input').click();
    });
  }

  // File input change
  const fileInput = document.getElementById('cookie-file-input');
  if (fileInput) {
    fileInput.addEventListener('change', () => {
      const file = fileInput.files[0];
      if (file && file.name.endsWith('.txt')) {
        uploadCookies(file);
      } else if (file) {
        showError(document.getElementById('cookie-card'), 'Please upload a .txt file.');
      }
      fileInput.value = ''; // Reset so same file can be re-selected
    });
  }

  // Paste textarea input — enable/disable paste upload button
  const pasteArea = document.getElementById('cookie-paste');
  if (pasteArea) {
    pasteArea.addEventListener('input', updatePasteBtn);
  }

  // Paste upload button click
  const pasteBtn = document.getElementById('cookie-paste-btn');
  if (pasteBtn) {
    pasteBtn.addEventListener('click', () => {
      const text = document.getElementById('cookie-paste').value.trim();
      if (!text) return;
      const blob = new Blob([text], { type: 'text/plain' });
      const file = new File([blob], 'cookies.txt', { type: 'text/plain' });
      uploadCookies(file);
    });
  }

  // LLM and cookie clear buttons — event delegation
  document.addEventListener('click', (e) => {
    if (e.target.id === 'llm-save-btn') saveLlmConfig();
    if (e.target.id === 'llm-reset-btn') resetPromptToDefault();
    if (e.target.classList.contains('settings-clear-btn')) clearCookies();
  });

  // Settings tab initial load on page load if hash is #settings
  if (getTabFromHash() === 'settings') { loadSettings(); }
});
