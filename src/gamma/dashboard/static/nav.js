// nav.js - page shell for the Gamma dashboard: which sections show, nav state, output links.
(function () {
  'use strict';

  var PAGES = ['dashboard', 'shana', 'people', 'integrations', 'coding', 'system'];

  function currentPage() {
    var page = String(window.GAMMA_DASHBOARD_PAGE || '').trim().toLowerCase();
    if (PAGES.indexOf(page) !== -1) return page;
    var path = String(window.location.pathname || '').replace(/\/+$/, '');
    var slug = path.indexOf('/dashboard/') === 0 ? path.slice('/dashboard/'.length).split('/')[0] : '';
    return PAGES.indexOf(slug) !== -1 ? slug : 'dashboard';
  }

  var page = currentPage();
  window.GAMMA_DASHBOARD_PAGE = page;

  function browserReachableBase(rawBase) {
    var value = String(rawBase || '').replace(/\/$/, '');
    if (!value) return '';
    try {
      var url = new URL(value, window.location.origin);
      var local = ['127.0.0.1', 'localhost', '0.0.0.0', '::1'];
      var browserHost = window.location.hostname;
      if (local.indexOf(url.hostname) !== -1 && browserHost && local.indexOf(browserHost) === -1) {
        url.hostname = browserHost;
      }
      return url.toString().replace(/\/$/, '');
    } catch (_error) {
      return value;
    }
  }

  function query(values) {
    var params = new URLSearchParams();
    Object.keys(values).forEach(function (key) {
      if (values[key]) params.set(key, values[key]);
    });
    var text = params.toString();
    return text ? '?' + text : '';
  }

  function updateOutputViewLinks() {
    var apiBase = browserReachableBase(window.GAMMA_SHANA_BASE_URL || '');
    var dashboardBase = browserReachableBase(window.GAMMA_DASHBOARD_BASE_URL || '');
    document.querySelectorAll('[data-dashboard-route]').forEach(function (link) {
      var route = link.getAttribute('data-dashboard-route') || '/';
      link.href = (route === '/' ? '/dashboard' : '/dashboard' + route);
    });
    document.querySelectorAll('[data-output-view="monitor"]').forEach(function (link) {
      link.href = (dashboardBase || '') + '/dashboard/monitor'
        + query({ api_base: apiBase, target_policy: 'dashboard_monitor', client_name: 'gaming_pc_monitor' });
    });
    document.querySelectorAll('[data-output-view="performer"]').forEach(function (link) {
      link.href = (apiBase ? apiBase + '/performer' : '/performer')
        + query({ target_policy: 'stream_public', client_name: 'stream_pc_performer' });
    });
    document.querySelectorAll('[data-output-view="subtitles"]').forEach(function (link) {
      link.href = (dashboardBase || '') + '/overlay/subtitles'
        + query({ api_base: apiBase, target_policy: 'stream_public', client_name: 'stream_pc_subtitle_overlay' });
    });
  }

  function applyPageVisibility() {
    document.body.setAttribute('data-dashboard-page', page);
    document.querySelectorAll('[data-dashboard-tab]').forEach(function (section) {
      var tabs = String(section.getAttribute('data-dashboard-tab') || '').split(/\s+/);
      section.hidden = tabs.indexOf(page) === -1;
    });
    document.querySelectorAll('[data-dashboard-page-link]').forEach(function (link) {
      var active = link.getAttribute('data-dashboard-page-link') === page;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  }

  function toggleNavMenu() {
    var open = !document.body.classList.contains('nav-menu-open');
    document.body.classList.toggle('nav-menu-open', open);
    var toggle = document.querySelector('.nav-menu-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  var toastTimer = null;
  // One shared place for "the last thing you asked for" results on every page.
  function showActionResult(text, tone) {
    var toast = document.getElementById('providerActions');
    if (!toast) return;
    toast.textContent = text;
    toast.hidden = !text;
    toast.classList.remove('good', 'bad');
    if (tone) toast.classList.add(tone);
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { toast.hidden = true; }, 9000);
  }

  window.toggleNavMenu = toggleNavMenu;
  window.showActionResult = showActionResult;
  window.updateOutputViewLinks = updateOutputViewLinks;

  applyPageVisibility();
  updateOutputViewLinks();
})();
