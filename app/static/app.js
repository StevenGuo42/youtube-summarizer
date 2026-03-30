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
