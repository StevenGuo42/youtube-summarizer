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
