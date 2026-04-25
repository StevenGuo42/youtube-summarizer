// --- Tab Routing ---

const TABS = ['browse', 'queue', 'summaries', 'settings', 'help'];
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

  // Put spinner on the section's h2 to avoid layout shift
  const spinnerEl = container ? (container.closest('section')?.querySelector('h2') || container) : null;

  if (container) {
    clearError(container);
  }
  if (spinnerEl) {
    spinnerEl.setAttribute('aria-busy', 'true');
  }

  // Set Content-Type for JSON bodies on POST/PUT/PATCH
  const method = (opts.method || 'GET').toUpperCase();
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method) && opts.body && !(opts.body instanceof FormData)) {
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
    if (spinnerEl) {
      spinnerEl.removeAttribute('aria-busy');
    }
  }
}

// --- Browse Tab ---

let browseDefaultPrompt = '';

function promptPlaceholder() {
  return browseDefaultPrompt || 'Leave empty to use default prompt...';
}

async function fetchDefaultPrompt() {
  if (browseDefaultPrompt) return;
  try {
    const settings = await apiFetch('/api/settings/llm');
    browseDefaultPrompt = settings.default_prompt || '';
    // Update any existing textareas with the real prompt
    for (const id of ['browse-custom-prompt', 'card-custom-prompt']) {
      const ta = document.getElementById(id);
      if (ta) ta.placeholder = promptPlaceholder();
    }
  } catch (e) {
    // Non-critical, use fallback placeholder
  }
}

let defaultProcessingOptions = { dedup_mode: 'regular', keyframe_mode: 'image' };
let defaultOptionsLoaded = false;

async function loadDefaultProcessingOptions() {
  try {
    const data = await apiFetch('/api/settings/defaults');
    defaultProcessingOptions = {
      dedup_mode: data.dedup_mode || 'regular',
      keyframe_mode: data.keyframe_mode || 'image',
    };
    defaultOptionsLoaded = true;
  } catch (e) {
    // Keep fallback defaults
  }
}

async function applyProcessingDefaults() {
  if (!defaultOptionsLoaded) await loadDefaultProcessingOptions();
  for (const id of ['dedup-mode', 'card-dedup-mode']) {
    const el = document.getElementById(id);
    if (el) el.value = defaultProcessingOptions.dedup_mode;
  }
  for (const id of ['keyframe-mode', 'card-keyframe-mode']) {
    const el = document.getElementById(id);
    if (el) el.value = defaultProcessingOptions.keyframe_mode;
  }
}

async function loadProcessingMode() {
  const select = document.getElementById('processing-mode');
  if (!select) return;
  try {
    const settings = await apiFetch('/api/settings/worker', { container: document.getElementById('browse-results') });
    select.value = settings.processing_mode || 'sequential';
    // Store batch_size so we can preserve it when changing mode
    select.dataset.batchSize = settings.batch_size || 5;
  } catch (err) {
    // Default to sequential on error
  }
}

const browseState = {
  urlType: null,      // 'video' | 'channel' | 'playlist' | null
  channelId: null,    // channel_id for channel URL fetches
  playlistId: null,   // playlist_id for playlist URL fetches
  page: 1,            // current page (reset on filter change or new URL)
  filters: { visibility: 'all' },
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
  if (/youtube\.com\/(channel\/|c\/|user\/|@)/.test(url)) return 'channel';
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
              <option value="regular">Regular (pHash)</option>
              <option value="slides">Slides (SSIM)</option>
              <option value="ocr">OCR (text match)</option>
              <option value="none">None</option>
            </select>
          </label>
          <label>Keyframe Context Mode
            <select id="card-keyframe-mode">
              <option value="image">Image</option>
              <option value="ocr">OCR</option>
              <option value="ocr+image">OCR + Image</option>
              <option value="ocr-inline">OCR Inline</option>
              <option value="ocr-inline+image">OCR Inline + Image</option>
              <option value="none">None</option>
            </select>
          </label>
          <label>Output Language
            <input type="text" id="card-language" placeholder="Default (from settings)">
          </label>
        </div>
        <label for="card-custom-prompt">Custom Prompt (optional)</label>
        <select id="card-prompt-mode">
          <option value="insert" selected>Insert into default prompt</option>
          <option value="replace">Replace entire default prompt</option>
        </select>
        <textarea id="card-custom-prompt" rows="8" placeholder="${promptPlaceholder()}"></textarea>
        <button class="single-queue-btn" data-video-id="${info.id}">Add to Queue</button>
      </div>
    </div>
  `;
  resultsDiv.appendChild(article);
  fetchDefaultPrompt();
  applyProcessingDefaults();
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
        ? 'No videos found matching the current filters. Try adjusting the visibility filter.'
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
  const hasDates = videos.some(v => v.upload_date);
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
      <td class="date-cell" data-video-id="${v.id}"><small style="color:#7b8495">${formatDate(v.upload_date)}</small></td>
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

  // Async-fetch upload dates if not already available
  if (!hasDates) {
    fetchAndFillDates(videos.map(v => v.id));
  }
}

async function fetchAndFillDates(videoIds) {
  // Show a subtle loading indicator on date cells
  for (const cell of document.querySelectorAll('.date-cell')) {
    cell.querySelector('small').setAttribute('aria-busy', 'true');
  }

  try {
    const res = await fetch('/api/video/dates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_ids: videoIds }),
    });
    if (!res.ok) throw new Error('Failed to fetch dates');

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Process complete lines
      const lines = buffer.split('\n');
      buffer = lines.pop(); // keep incomplete last line in buffer
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const { id, upload_date } = JSON.parse(line);
          const cell = document.querySelector(`.date-cell[data-video-id="${id}"]`);
          if (cell) {
            const small = cell.querySelector('small');
            small.removeAttribute('aria-busy');
            small.textContent = formatDate(upload_date);
          }
        } catch (e) { /* skip malformed line */ }
      }
    }
  } catch (err) {
    // Non-critical
  }

  // Clear busy state on any remaining cells
  for (const cell of document.querySelectorAll('.date-cell small[aria-busy]')) {
    cell.removeAttribute('aria-busy');
  }
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
        <option value="regular">Regular (pHash)</option>
        <option value="slides">Slides (SSIM)</option>
        <option value="ocr">OCR (text match)</option>
        <option value="none">None</option>
      </select>
    </label>
    <label>Keyframe Context Mode
      <select id="keyframe-mode">
        <option value="image">Image</option>
        <option value="ocr">OCR</option>
        <option value="ocr+image">OCR + Image</option>
        <option value="ocr-inline">OCR Inline</option>
        <option value="ocr-inline+image">OCR Inline + Image</option>
        <option value="none">None</option>
      </select>
    </label>
    <label>Processing Mode
      <select id="processing-mode">
        <option value="sequential">Sequential</option>
        <option value="batch">Batch</option>
      </select>
    </label>
    <label>Output Language
      <input type="text" id="browse-language" placeholder="Default (from settings)">
    </label>
  `;
  resultsDiv.appendChild(optionsDiv);

  // Custom prompt textarea
  const promptDiv = document.createElement('div');
  promptDiv.innerHTML = `
    <label for="browse-custom-prompt">Custom Prompt (optional)</label>
    <select id="browse-prompt-mode">
      <option value="insert" selected>Insert into default prompt</option>
      <option value="replace">Replace entire default prompt</option>
    </select>
    <textarea id="browse-custom-prompt" rows="8" placeholder="${promptPlaceholder()}"></textarea>
  `;
  resultsDiv.appendChild(promptDiv);
  setTimeout(() => fetchDefaultPrompt(), 0);
  setTimeout(() => loadProcessingMode(), 0);
  applyProcessingDefaults();

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
  browseState.filters = { visibility: 'all' };
  browseState.selected.clear();
  browseState.videos = [];
  resultsDiv.innerHTML = '';

  const urlType = detectUrlType(url);
  if (!urlType) {
    showError(resultsDiv, 'Unrecognized URL format. Paste a YouTube video, channel, or playlist URL.');
    return;
  }

  browseState.urlType = urlType;
  fetchDefaultPrompt();

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
  const customPrompt = document.getElementById('card-custom-prompt')?.value.trim() || null;
  const promptMode = document.getElementById('card-prompt-mode')?.value || 'replace';
  const langVal = document.getElementById('card-language')?.value.trim() || null;

  try {
    await apiFetch('/api/queue', {
      method: 'POST',
      body: { video_ids: [videoId], dedup_mode: dedupMode, keyframe_mode: keyframeMode, custom_prompt: customPrompt, custom_prompt_mode: promptMode, output_language: langVal },
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
  const customPrompt = document.getElementById('browse-custom-prompt')?.value.trim() || null;
  const promptMode = document.getElementById('browse-prompt-mode')?.value || 'replace';
  const langVal = document.getElementById('browse-language')?.value.trim() || null;

  try {
    await apiFetch('/api/queue', {
      method: 'POST',
      body: { video_ids: videoIds, dedup_mode: dedupMode, keyframe_mode: keyframeMode, custom_prompt: customPrompt, custom_prompt_mode: promptMode, output_language: langVal },
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
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('browse-fetch-form');
  if (form) {
    form.addEventListener('submit', handleBrowseFetch);
  }
  bindFilterEvents();
  fetchDefaultPrompt();  // Pre-fetch so placeholder is ready before textareas are created
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
  lastJson: null,
  selected: new Set(),
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
  } else if (job.status === 'failed' || job.status === 'cancelled') {
    actionsHtml = `<button class="outline rerun-btn" data-job-id="${job.id}">Rerun</button>`;
  }

  article.innerHTML = `
    <input type="checkbox" class="job-check" value="${job.id}" aria-label="Select job: ${(job.title || 'Untitled').replace(/"/g, '&quot;')}"${queueState.selected.has(job.id) ? ' checked' : ''}>
    <img src="${job.thumbnail_url || ''}" alt="" loading="lazy">
    <div class="job-card-content">
      <div class="job-card-header">
        <strong>${job.title || 'Untitled'}</strong>
        <small class="created-time">${formatCreatedTime(job.created_at)}</small>
      </div>
      <small style="color:#7b8495">${job.channel || 'Unknown'} / ${formatDuration(job.duration)}${job.language ? ' / ' + (job.output_language && job.output_language !== job.language ? job.language + ' → ' + job.output_language : job.language) : (job.output_language ? ' / → ' + job.output_language : '')} / ${job.dedup_mode} / ${job.keyframe_mode}</small>
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
  const headerBar = document.getElementById('queue-header-bar');
  if (!jobList) return;

  if (queueState.jobs.length === 0) {
    if (emptyState) emptyState.hidden = false;
    if (headerBar) headerBar.hidden = true;
    jobList.innerHTML = '';
    return;
  }

  if (emptyState) emptyState.hidden = true;
  if (headerBar) headerBar.hidden = false;
  jobList.innerHTML = '';
  for (const job of queueState.jobs) {
    jobList.appendChild(renderJobCard(job));
  }
  // Prune selections for jobs that no longer exist
  const currentIds = new Set(queueState.jobs.map(j => j.id));
  for (const id of queueState.selected) {
    if (!currentIds.has(id)) queueState.selected.delete(id);
  }
  updateQueueButtons();
}

function updateQueueButtons() {
  const clearBtn = document.getElementById('queue-clear-btn');
  const deleteBtn = document.getElementById('queue-delete-btn');
  const selectAll = document.getElementById('queue-select-all');

  // Clear Finished: disabled when no finished jobs
  const finishedCount = queueState.jobs.filter(j => j.status === 'done' || j.status === 'failed' || j.status === 'cancelled').length;
  if (clearBtn) clearBtn.disabled = finishedCount === 0;

  // Delete Selected: hidden when none selected, shows count
  const selectedCount = queueState.selected.size;
  if (deleteBtn) {
    deleteBtn.style.display = selectedCount > 0 ? 'inline-block' : 'none';
    deleteBtn.textContent = `Delete Selected (${selectedCount})`;
  }

  // Select-all: checked if all selected, indeterminate if partial
  if (selectAll && queueState.jobs.length > 0) {
    const allSelected = queueState.jobs.length === selectedCount;
    const someSelected = selectedCount > 0 && !allSelected;
    selectAll.checked = allSelected;
    selectAll.indeterminate = someSelected;
  }
}

async function clearFinished() {
  if (!confirm('Clear all finished jobs from the queue?')) return;
  const clearBtn = document.getElementById('queue-clear-btn');
  const container = document.getElementById('queue-container');
  try {
    if (clearBtn) clearBtn.setAttribute('aria-busy', 'true');
    const result = await apiFetch('/api/queue/finished', { method: 'DELETE', container });
    if (clearBtn) clearBtn.removeAttribute('aria-busy');
    queueState.selected.clear();
    queueState.lastJson = null; // Force re-render on next fetch
    await fetchJobs();
    showSuccess(container, `${result.deleted} job(s) cleared.`);
  } catch (err) {
    if (clearBtn) clearBtn.removeAttribute('aria-busy');
  }
}

async function deleteSelected() {
  const jobIds = Array.from(queueState.selected);
  if (jobIds.length === 0) return;
  const deleteBtn = document.getElementById('queue-delete-btn');
  const container = document.getElementById('queue-container');
  try {
    if (deleteBtn) deleteBtn.setAttribute('aria-busy', 'true');
    const result = await apiFetch('/api/queue', { method: 'DELETE', body: { job_ids: jobIds }, container });
    if (deleteBtn) deleteBtn.removeAttribute('aria-busy');
    queueState.selected.clear();
    queueState.lastJson = null; // Force re-render on next fetch
    await fetchJobs();
    showSuccess(container, `${result.deleted} job(s) deleted.`);
  } catch (err) {
    if (deleteBtn) deleteBtn.removeAttribute('aria-busy');
  }
}

function toggleSelectAll(checked) {
  if (checked) {
    for (const job of queueState.jobs) {
      queueState.selected.add(job.id);
    }
  } else {
    queueState.selected.clear();
  }
  // Update all visible checkboxes
  document.querySelectorAll('.job-check').forEach(cb => { cb.checked = checked; });
  updateQueueButtons();
}

function toggleJobSelection(jobId, checked) {
  if (checked) {
    queueState.selected.add(jobId);
  } else {
    queueState.selected.delete(jobId);
  }
  updateQueueButtons();
}

async function fetchJobs() {
  try {
    const jobs = await apiFetch('/api/queue', { container: document.getElementById('queue-container') });
    const json = JSON.stringify(jobs);
    if (json === queueState.lastJson) {
      // No changes — skip re-render to avoid flash (per D-04)
      return;
    }
    queueState.lastJson = json;
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

async function rerunJob(jobId) {
  try {
    await apiFetch(`/api/queue/${jobId}/rerun`, { method: 'POST' });
    fetchJobs();
  } catch (err) {
    // apiFetch already handles error display
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

// Queue tab: event delegation for cancel buttons, view summary links, and queue management
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('cancel-btn')) {
    const jobId = e.target.dataset.jobId;
    if (jobId) cancelJob(jobId);
  }
  if (e.target.classList.contains('rerun-btn')) {
    const jobId = e.target.dataset.jobId;
    if (jobId) rerunJob(jobId);
  }
  if (e.target.classList.contains('view-summary-link')) {
    e.preventDefault();
    switchTab('summaries');
  }
  if (e.target.id === 'queue-clear-btn' || e.target.closest('#queue-clear-btn')) {
    clearFinished();
  }
  if (e.target.id === 'queue-delete-btn' || e.target.closest('#queue-delete-btn')) {
    deleteSelected();
  }
});

// Queue tab: event delegation for checkboxes (select-all and per-job)
// Browse tab: processing mode change handler
document.addEventListener('change', (e) => {
  if (e.target.id === 'queue-select-all') {
    toggleSelectAll(e.target.checked);
  }
  if (e.target.classList.contains('job-check')) {
    toggleJobSelection(e.target.value, e.target.checked);
  }
  if (e.target.id === 'processing-mode') {
    const mode = e.target.value;
    const batchSize = parseInt(e.target.dataset.batchSize || '5', 10);
    apiFetch('/api/settings/worker', {
      method: 'POST',
      body: { processing_mode: mode, batch_size: batchSize },
      container: document.getElementById('browse-results'),
    }).catch(() => {
      // Revert select on error
      e.target.value = mode === 'batch' ? 'sequential' : 'batch';
    });
  }
});

// Queue tab: start polling if queue is the active tab on page load
document.addEventListener('DOMContentLoaded', () => {
  if (getTabFromHash() === 'queue') {
    startPolling();
  }
});

// --- Summaries Tab ---

function stripCodeFence(text) {
  if (!text) return text;
  const trimmed = text.trim();
  if (trimmed.startsWith('```')) {
    return trimmed.replace(/^```\w*\n?/, '').replace(/\n?```\s*$/, '');
  }
  return text;
}

function extractEmbeddedJson(summaryText) {
  if (!summaryText) return null;
  const m = summaryText.match(/```(?:json)?\s*\n?\{([\s\S]*)\}\s*\n?```/);
  if (!m) return null;
  const inner = '{' + m[1] + '}';
  try { return JSON.parse(inner); } catch (e) { /* fall through to regex */ }
  const result = {};
  for (const field of ['title', 'tldr']) {
    const fm = inner.match(new RegExp(`"${field}"\\s*:\\s*"([^"]*)"`));
    if (fm) result[field] = fm[1].replace(/\\n/g, '\n').replace(/\\"/g, '"');
  }
  const sm = inner.match(/"summary"\s*:\s*"([\s\S]*)/);
  if (sm) {
    let raw = sm[1].replace(/"\s*\n?\}\s*$/, '');
    raw = raw.replace(/\\n/g, '\n').replace(/\\"/g, '"').replace(/\\'/g, "'");
    result.summary = raw;
  }
  return Object.keys(result).length ? result : null;
}

const summariesState = {
  summaries: [],
  cache: {},
  viewStyle: localStorage.getItem('summaries-view-style') || 'compact',
  expandedId: null,
  selected: new Set(),
};

async function fetchSummaries() {
  // Always start from clean state — collapse expanded card, clear old cards
  collapseSummary();
  const listEl = document.getElementById('summaries-list');
  if (listEl) listEl.innerHTML = '';
  const heading = document.querySelector('#summaries h2');
  if (heading) heading.setAttribute('aria-busy', 'true');
  try {
    const data = await apiFetch('/api/summaries');
    summariesState.summaries = data;
    renderSummaries();
  } catch (err) {
    const container = document.getElementById('summaries-container');
    if (container) showError(container, 'Could not load summaries.');
  } finally {
    if (heading) heading.removeAttribute('aria-busy');
  }
}

function renderSummaries() {
  const listEl = document.getElementById('summaries-list');
  const emptyEl = document.getElementById('summaries-empty-state');
  const bulkBar = document.getElementById('summaries-bulk-bar');
  if (!listEl) return;

  if (summariesState.summaries.length === 0) {
    if (emptyEl) emptyEl.hidden = false;
    if (bulkBar) bulkBar.hidden = true;
    listEl.innerHTML = '';
    return;
  }

  if (emptyEl) emptyEl.hidden = true;
  if (bulkBar) bulkBar.hidden = false;
  listEl.innerHTML = '';
  for (const summary of summariesState.summaries) {
    listEl.appendChild(renderSummaryCard(summary));
  }
  updateSummariesBulkBar();
}

function updateSummariesBulkBar() {
  const selectAll = document.getElementById('summaries-select-all');
  const exportBtn = document.getElementById('summaries-export-selected-btn');
  const deleteBtn = document.getElementById('summaries-delete-selected-btn');
  const count = summariesState.selected.size;

  if (exportBtn) exportBtn.style.display = count > 0 ? '' : 'none';
  if (deleteBtn) deleteBtn.style.display = count > 0 ? '' : 'none';
  if (exportBtn && count > 0) exportBtn.textContent = `Export Selected (${count})`;
  if (deleteBtn && count > 0) deleteBtn.textContent = `Delete Selected (${count})`;

  if (selectAll && summariesState.summaries.length > 0) {
    const allSelected = summariesState.summaries.length === count;
    const someSelected = count > 0 && !allSelected;
    selectAll.checked = allSelected;
    selectAll.indeterminate = someSelected;
  }
}

function renderSummaryCard(summary) {
  const article = document.createElement('article');
  const isActive = summariesState.expandedId === summary.job_id;
  const cached = summariesState.cache[summary.job_id];
  const tldr = (cached && cached.structured ? cached.structured.tldr : null) || summary.tldr || '';

  const checked = summariesState.selected.has(summary.job_id) ? 'checked' : '';

  if (summariesState.viewStyle === 'list') {
    // Title-only list view
    article.className = 'summary-card summary-list-item' + (isActive ? ' summary-card-active' : '');
    article.dataset.jobId = summary.job_id;
    article.innerHTML = `
      <div class="summary-card" data-job-id="${summary.job_id}">
        <input type="checkbox" class="summary-check" data-job-id="${summary.job_id}" ${checked} aria-label="Select summary">
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
    // Compact view (with thumbnail)
    article.className = 'summary-card' + (isActive ? ' summary-card-active' : '');
    article.dataset.jobId = summary.job_id;
    article.innerHTML = `
      <input type="checkbox" class="summary-check" data-job-id="${summary.job_id}" ${checked} aria-label="Select summary">
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

const _markedRenderer = new marked.Renderer();
_markedRenderer.heading = function ({ depth, text }) {
  const level = Math.min(depth + 2, 6);
  return `<h${level}>${text}</h${level}>`;
};
_markedRenderer.link = function ({ href, text }) {
  return `<a href="${href}" target="_blank" rel="noopener">${text}</a>`;
};
marked.setOptions({ renderer: _markedRenderer, breaks: false, gfm: true });

function renderMarkdown(text) {
  if (!text) return '';
  return marked.parse(text);
}

function sanitizeHtml(html) {
  if (!html) return '';
  return DOMPurify.sanitize(html);
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

  // Mark the card as active — scope to summaries list to avoid matching queue cards
  const article = document.querySelector(`#summaries-list article[data-job-id="${jobId}"]`);
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
      structured = JSON.parse(stripCodeFence(data.structured_summary));
    } catch (e) {
      console.warn('Failed to parse structured_summary JSON:', e);
    }
  }

  // Handle embedded JSON: title/tldr empty but real content in summary code block
  if (structured && (!structured.title || structured.title.length <= 2)) {
    const embedded = extractEmbeddedJson(structured.summary || '');
    if (embedded) structured = embedded;
  }

  if (structured && structured.summary) {
    const title = structured.title || '';
    const tldr = structured.tldr || '';
    container.innerHTML = `
      ${title ? `<h3>${title}</h3>` : ''}
      ${tldr ? `<p><strong>TL;DR:</strong> ${tldr}</p>` : ''}
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
  const structured = cached.structured || (cached.structured_summary ? JSON.parse(stripCodeFence(cached.structured_summary)) : null);
  if (!structured || !structured.tldr) return;
  const card = document.querySelector(`#summaries-list article[data-job-id="${jobId}"]`);
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
    try { structured = JSON.parse(stripCodeFence(data.structured_summary)); } catch (e) { /* ignore */ }
  }

  const markdownText = structured
    ? `${structured.title || ''}\n\n${structured.tldr || ''}\n\n${structured.summary || ''}`
    : data.raw_response || '';

  let copied = false;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(markdownText);
      copied = true;
    } catch (err) { /* fall through to fallback */ }
  }
  if (!copied) {
    const ta = document.createElement('textarea');
    ta.value = markdownText;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    copied = document.execCommand('copy');
    document.body.removeChild(ta);
  }
  if (copied) {
    const btn = document.querySelector(`.summary-copy-btn[data-job-id="${jobId}"]`);
    if (btn) {
      btn.textContent = 'Copied!';
      btn.style.color = '#2ea043';
      setTimeout(() => {
        btn.textContent = 'Copy';
        btn.style.color = '';
      }, 2000);
    }
  } else {
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
    const card = document.querySelector(`#summaries-list article[data-job-id="${jobId}"]`);
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

  // Checkbox clicks: stop propagation to prevent card expand
  if (e.target.classList.contains('summary-check')) {
    e.stopPropagation();
    const jobId = e.target.dataset.jobId;
    if (e.target.checked) {
      summariesState.selected.add(jobId);
    } else {
      summariesState.selected.delete(jobId);
    }
    updateSummariesBulkBar();
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
  if (e.target.classList.contains('summary-delete-btn') && e.target.id !== 'summaries-delete-selected-btn') {
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
  // Select-all checkbox
  const selectAllCb = document.getElementById('summaries-select-all');
  if (selectAllCb) {
    selectAllCb.addEventListener('change', () => {
      if (selectAllCb.checked) {
        for (const s of summariesState.summaries) summariesState.selected.add(s.job_id);
      } else {
        summariesState.selected.clear();
      }
      renderSummaries();
    });
  }

  // Bulk delete
  const bulkDeleteBtn = document.getElementById('summaries-delete-selected-btn');
  if (bulkDeleteBtn) {
    bulkDeleteBtn.addEventListener('click', async () => {
      const ids = Array.from(summariesState.selected);
      if (ids.length === 0) return;
      if (!confirm(`Delete ${ids.length} summary(ies)? This cannot be undone.`)) return;
      for (const jobId of ids) {
        try {
          await apiFetch(`/api/summaries/${jobId}`, { method: 'DELETE' });
          summariesState.summaries = summariesState.summaries.filter(s => s.job_id !== jobId);
          delete summariesState.cache[jobId];
        } catch (err) { /* continue with others */ }
      }
      summariesState.selected.clear();
      if (summariesState.expandedId && !summariesState.summaries.some(s => s.job_id === summariesState.expandedId)) {
        summariesState.expandedId = null;
      }
      renderSummaries();
    });
  }

  // Bulk export
  const bulkExportBtn = document.getElementById('summaries-export-selected-btn');
  if (bulkExportBtn) {
    bulkExportBtn.addEventListener('click', async () => {
      const ids = Array.from(summariesState.selected);
      for (const jobId of ids) {
        await exportSummary(jobId);
      }
    });
  }
});

// --- Settings Tab ---

const settingsState = {
  cookieStatus: { exists: false, modified: null },
  llmConfig: {
    active_provider: 'claude',
    providers: {
      claude: { model: '', custom_prompt: null, custom_prompt_mode: 'replace', output_language: '' },
      codex: { model: 'gpt-5.4', custom_prompt: null, custom_prompt_mode: 'replace', output_language: '' },
      litellm: { provider: 'openai', model: 'gpt-4o', api_key: '', api_base_url: '', custom_prompt: null, custom_prompt_mode: 'replace', output_language: '' },
    },
  },
  defaultPrompt: '',
  authStatus: { claude: {}, codex: {}, litellm: {} },
};

async function loadSettings() {
  const section = document.getElementById('settings');
  const heading = section.querySelector('h2');
  if (heading) heading.setAttribute('aria-busy', 'true');
  try {
    const [cookieRes, llmRes, authClaudeRes, authCodexRes, authLitellmRes] = await Promise.all([
      apiFetch('/api/auth/status'),
      apiFetch('/api/settings/llm'),
      apiFetch('/api/settings/auth/claude'),
      apiFetch('/api/settings/auth/codex'),
      apiFetch('/api/settings/auth/litellm'),
    ]);
    settingsState.cookieStatus = cookieRes;
    if (llmRes) {
      settingsState.llmConfig = {
        active_provider: llmRes.active_provider || 'claude',
        providers: llmRes.providers || settingsState.llmConfig.providers,
      };
      settingsState.defaultPrompt = llmRes.default_prompt || '';
    }
    settingsState.authStatus = {
      claude: authClaudeRes || {},
      codex: authCodexRes || {},
      litellm: authLitellmRes || {},
    };
    renderCookieStatus();
    renderLlmConfig();
    renderAuthStatus();
    renderDefaultModes();
  } catch (err) {
    showError(section, 'Could not load settings. Check that the server is running and try again.');
  } finally {
    if (heading) heading.removeAttribute('aria-busy');
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

// Placeholder map: backend → model placeholder text
const _BACKEND_MODEL_PLACEHOLDER = {
  claude: 'claude-sonnet-4-20250514',
  codex: 'gpt-5.4',
  litellm: '',  // driven by litellm provider dropdown
};

// LiteLLM provider → default model placeholder
const _LITELLM_MODEL_PLACEHOLDER = {
  openai: 'gpt-4o',
  anthropic: 'claude-sonnet-4-20250514',
  gemini: 'gemini-2.5-pro',
  ollama: 'llama3',
  custom: 'model-name',
};

function onBackendChange() {
  const backendSel = document.getElementById('llm-backend');
  if (!backendSel) return;
  const backend = backendSel.value;
  settingsState.llmConfig.active_provider = backend;

  // Show/hide LiteLLM-specific fields and Claude/Codex generic model field
  const litellmFields = document.getElementById('litellm-fields');
  if (litellmFields) litellmFields.hidden = (backend !== 'litellm');
  const claudeCodexFields = document.getElementById('claude-codex-fields');
  if (claudeCodexFields) claudeCodexFields.hidden = (backend === 'litellm');

  // Update model placeholder
  const modelInput = document.getElementById('llm-model');
  if (modelInput) {
    modelInput.placeholder = _BACKEND_MODEL_PLACEHOLDER[backend] || '';
  }

  // Render the active provider's current model value
  const cfg = settingsState.llmConfig.providers[backend] || {};
  if (modelInput) modelInput.value = cfg.model || '';

  // Update keyframe mode options (all backends support all modes — no-op for now)
  updateKeyframeModeOptions(null);
}

function onLitellmProviderChange() {
  const providerSel = document.getElementById('litellm-provider');
  if (!providerSel) return;
  const provider = providerSel.value;

  // Show/hide API base URL field
  const baseUrlRow = document.getElementById('litellm-base-url-row');
  if (baseUrlRow) {
    baseUrlRow.hidden = !(provider === 'ollama' || provider === 'custom');
  }

  // Update LiteLLM model placeholder
  const litellmModelInput = document.getElementById('litellm-model');
  if (litellmModelInput) {
    litellmModelInput.placeholder = _LITELLM_MODEL_PLACEHOLDER[provider] || 'model-name';
  }
}

// updateKeyframeModeOptions: gates keyframe dropdown based on supported modes.
// Currently all backends support all modes; this function is a no-op but provides
// the hook for future backends that restrict modes.
function updateKeyframeModeOptions(supportedModes) {
  // supportedModes: null = all modes supported
  const modeSelectors = document.querySelectorAll('select[id$="-keyframe-mode"], #keyframe-mode');
  if (!modeSelectors.length) return;
  modeSelectors.forEach(sel => {
    Array.from(sel.options).forEach(opt => {
      opt.disabled = supportedModes !== null && !supportedModes.includes(opt.value);
    });
  });
}

function renderLlmConfig() {
  const backendSel = document.getElementById('llm-backend');
  if (backendSel) backendSel.value = settingsState.llmConfig.active_provider;

  const litellmFields = document.getElementById('litellm-fields');
  if (litellmFields) litellmFields.hidden = (settingsState.llmConfig.active_provider !== 'litellm');
  const claudeCodexFields = document.getElementById('claude-codex-fields');
  if (claudeCodexFields) claudeCodexFields.hidden = (settingsState.llmConfig.active_provider === 'litellm');

  const activeProvider = settingsState.llmConfig.active_provider;
  const cfg = settingsState.llmConfig.providers[activeProvider] || {};
  const modelInput = document.getElementById('llm-model');
  if (modelInput) {
    modelInput.value = cfg.model || '';
    modelInput.placeholder = _BACKEND_MODEL_PLACEHOLDER[activeProvider] || '';
  }

  // LiteLLM fields
  const litellmCfg = settingsState.llmConfig.providers.litellm || {};
  const providerSel = document.getElementById('litellm-provider');
  if (providerSel) providerSel.value = litellmCfg.provider || 'openai';

  const litellmModel = document.getElementById('litellm-model');
  if (litellmModel) {
    litellmModel.value = litellmCfg.model || '';
    litellmModel.placeholder = _LITELLM_MODEL_PLACEHOLDER[litellmCfg.provider || 'openai'] || 'gpt-4o';
  }

  const apiKeyInput = document.getElementById('litellm-api-key');
  if (apiKeyInput) {
    const rawKey = litellmCfg.api_key || '';
    if (rawKey.startsWith('...')) {
      apiKeyInput.value = rawKey;
      apiKeyInput.dataset.masked = 'true';
    } else {
      apiKeyInput.value = rawKey;
      delete apiKeyInput.dataset.masked;
    }
  }

  const baseUrlInput = document.getElementById('litellm-api-base-url');
  if (baseUrlInput) baseUrlInput.value = litellmCfg.api_base_url || '';

  const baseUrlRow = document.getElementById('litellm-base-url-row');
  if (baseUrlRow) {
    const p = litellmCfg.provider || 'openai';
    baseUrlRow.hidden = !(p === 'ollama' || p === 'custom');
  }

  // Shared fields (use active provider's config)
  const langInput = document.getElementById('llm-language');
  if (langInput) langInput.value = cfg.output_language || '';

  const promptModeSelect = document.getElementById('llm-prompt-mode');
  if (promptModeSelect) promptModeSelect.value = cfg.custom_prompt_mode || 'replace';

  const promptArea = document.getElementById('llm-prompt');
  if (promptArea) promptArea.value = cfg.custom_prompt || '';
}

async function saveLlmConfig() {
  const card = document.getElementById('llm-card');
  const activeProvider = settingsState.llmConfig.active_provider;
  const cfg = settingsState.llmConfig.providers[activeProvider] || {};

  // Read active provider's model
  const model = document.getElementById('llm-model')?.value.trim() || cfg.model || '';
  const outputLanguage = document.getElementById('llm-language')?.value.trim() || null;
  const customPromptMode = document.getElementById('llm-prompt-mode')?.value || 'replace';
  const customPrompt = document.getElementById('llm-prompt')?.value.trim() || null;

  // LiteLLM specific
  const litellmCfg = { ...settingsState.llmConfig.providers.litellm };
  const litellmProvider = document.getElementById('litellm-provider')?.value || litellmCfg.provider;
  const litellmModel = document.getElementById('litellm-model')?.value.trim() || litellmCfg.model;
  const litellmBaseUrl = document.getElementById('litellm-api-base-url')?.value.trim() || null;

  // API key: if data-masked is still set (user didn't retype), omit from POST body
  const apiKeyInput = document.getElementById('litellm-api-key');
  const apiKeyRaw = apiKeyInput?.value || '';
  const apiKey = apiKeyInput?.dataset.masked === 'true' ? undefined : (apiKeyRaw || null);

  const providers = {
    claude: {
      ...settingsState.llmConfig.providers.claude,
      ...(activeProvider === 'claude' ? { model, custom_prompt: customPrompt, custom_prompt_mode: customPromptMode, output_language: outputLanguage } : {}),
    },
    codex: {
      ...settingsState.llmConfig.providers.codex,
      ...(activeProvider === 'codex' ? { model, custom_prompt: customPrompt, custom_prompt_mode: customPromptMode, output_language: outputLanguage } : {}),
    },
    litellm: {
      provider: litellmProvider,
      model: litellmModel,
      api_base_url: litellmBaseUrl,
      custom_prompt: activeProvider === 'litellm' ? customPrompt : litellmCfg.custom_prompt,
      custom_prompt_mode: activeProvider === 'litellm' ? customPromptMode : litellmCfg.custom_prompt_mode,
      output_language: activeProvider === 'litellm' ? outputLanguage : litellmCfg.output_language,
      ...(apiKey !== undefined ? { api_key: apiKey } : {}),
    },
  };

  card.setAttribute('aria-busy', 'true');
  const result = await apiFetch('/api/settings/llm', {
    method: 'POST',
    body: { active_provider: activeProvider, providers },
    container: card,
  });
  card.removeAttribute('aria-busy');

  if (result) {
    showSuccess(card, 'Settings saved.');
    settingsState.llmConfig.providers = { ...providers };
    // Re-fetch auth status to reflect any provider change
    await refreshAuthStatus();
  }
}

async function refreshAuthStatus() {
  const card = document.getElementById('auth-card');
  if (card) card.setAttribute('aria-busy', 'true');
  const [claudeRes, codexRes, litellmRes] = await Promise.all([
    apiFetch('/api/settings/auth/claude'),
    apiFetch('/api/settings/auth/codex'),
    apiFetch('/api/settings/auth/litellm'),
  ]);
  settingsState.authStatus = {
    claude: claudeRes || {},
    codex: codexRes || {},
    litellm: litellmRes || {},
  };
  if (card) card.removeAttribute('aria-busy');
  renderAuthStatus();
}

function resetPromptToDefault() {
  document.getElementById('llm-prompt').value = settingsState.defaultPrompt;
}

async function renderDefaultModes() {
  await loadDefaultProcessingOptions();
  const dedupSel = document.getElementById('default-dedup-mode');
  const keyframeSel = document.getElementById('default-keyframe-mode');
  if (dedupSel) dedupSel.value = defaultProcessingOptions.dedup_mode;
  if (keyframeSel) keyframeSel.value = defaultProcessingOptions.keyframe_mode;
}

async function saveDefaultProcessingOptions() {
  try {
    await apiFetch('/api/settings/defaults', {
      method: 'POST',
      body: {
        dedup_mode: defaultProcessingOptions.dedup_mode,
        keyframe_mode: defaultProcessingOptions.keyframe_mode,
      },
      container: document.getElementById('defaults-card'),
    });
  } catch (e) {
    // apiFetch renders error in container
  }
}

function _renderBackendAuth(elementId, status, opts) {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (status.loggedIn || status.configured) {
    el.innerHTML = '<span class="settings-status-ok">&#10003; ' + (opts.okMsg || 'OK') + '</span>';
  } else if (status.cli_error) {
    el.innerHTML = '<span class="settings-status-fail">&#10007; Could not check auth status</span>';
  } else {
    const msg = opts.notAuthMsg || ('Not authenticated — run <code>' + (opts.helpCmd || '') + '</code>');
    el.innerHTML = '<span class="settings-status-fail">&#10007; ' + msg + '</span>';
  }
}

function renderAuthStatus() {
  _renderBackendAuth('auth-status-claude', settingsState.authStatus.claude, {
    okMsg: 'Authenticated via OAuth',
    helpCmd: 'claude auth login',
  });
  // T-13-06-C: codex.method is raw CLI output — only echo if it starts with the known prefix
  const codexMethod = settingsState.authStatus.codex.method || '';
  _renderBackendAuth('auth-status-codex', settingsState.authStatus.codex, {
    okMsg: codexMethod.startsWith('Logged in') ? codexMethod : 'Logged in',
    helpCmd: 'codex login',
  });
  _renderBackendAuth('auth-status-litellm', settingsState.authStatus.litellm, {
    okMsg: 'API key configured',
    notAuthMsg: 'No API key set — enter an API key above and save',
  });

  // Show help text for the most pressing issue
  const helpEl = document.getElementById('auth-help');
  if (!helpEl) return;
  const { claude, codex, litellm } = settingsState.authStatus;
  if (claude.cli_error) {
    helpEl.innerHTML = 'Ensure the Claude CLI is installed. Run <code>claude auth login</code> in your terminal.';
    helpEl.hidden = false;
  } else if (!claude.loggedIn) {
    helpEl.innerHTML = 'Run <code>claude auth login</code> in your terminal to authenticate.';
    helpEl.hidden = false;
  } else if (codex.cli_error) {
    helpEl.innerHTML = 'Ensure the codex CLI is installed. Run <code>codex login</code> in your terminal.';
    helpEl.hidden = false;
  } else if (!codex.loggedIn) {
    helpEl.innerHTML = 'Run <code>codex login</code> in your terminal to authenticate.';
    helpEl.hidden = false;
  } else if (!litellm.configured) {
    helpEl.innerHTML = 'Enter an API key in the LiteLLM section above and save settings.';
    helpEl.hidden = false;
  } else {
    helpEl.hidden = true;
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
    let dragCounter = 0;
    dropZone.addEventListener('dragenter', (e) => { e.preventDefault(); dragCounter++; dropZone.classList.add('drag-over'); });
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); });
    dropZone.addEventListener('dragleave', () => { dragCounter--; if (dragCounter <= 0) { dragCounter = 0; dropZone.classList.remove('drag-over'); } });
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      dragCounter = 0;
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

  // Backend selector change
  document.getElementById('llm-backend')?.addEventListener('change', onBackendChange);

  // LiteLLM provider change
  document.getElementById('litellm-provider')?.addEventListener('change', onLitellmProviderChange);

  // API key focus: clear masked value so user can type fresh key
  document.getElementById('litellm-api-key')?.addEventListener('focus', function() {
    if (this.dataset.masked === 'true') {
      this.value = '';
      delete this.dataset.masked;
    }
  });

  // LLM and cookie clear buttons — event delegation
  document.addEventListener('click', (e) => {
    if (e.target.id === 'llm-save-btn') saveLlmConfig();
    if (e.target.id === 'llm-reset-btn') {
      const activeProvider = settingsState.llmConfig.active_provider;
      const defaults = { claude: 'claude-sonnet-4-20250514', codex: 'gpt-5.4', litellm: 'gpt-4o' };
      const modelInput = document.getElementById('llm-model');
      if (modelInput) modelInput.value = defaults[activeProvider] || '';
      const promptArea = document.getElementById('llm-prompt');
      if (promptArea) promptArea.value = '';
    }
    if (e.target.classList.contains('settings-clear-btn')) clearCookies();
  });

  // Default processing options — persist to server on change
  document.addEventListener('change', (e) => {
    if (e.target.id === 'default-dedup-mode') {
      defaultProcessingOptions.dedup_mode = e.target.value;
      saveDefaultProcessingOptions();
    }
    if (e.target.id === 'default-keyframe-mode') {
      defaultProcessingOptions.keyframe_mode = e.target.value;
      saveDefaultProcessingOptions();
    }
  });

  // Settings tab initial load on page load if hash is #settings
  if (getTabFromHash() === 'settings') { loadSettings(); }
});
