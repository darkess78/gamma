# Gamma Dashboard Page Filtering Inspection Report

## Current repo state
```
/home/neety/.openclaw/workspace/gamma-main
Modified files:
 M REFACTORING_SUMMARY.md
 M scripts/start_dashboard.sh
 M src/gamma/api/routes.py
 M src/gamma/config.py
 M src/gamma/dashboard/main.py
 D src/gamma/dashboard/static/dashboard.js
 M src/gamma/dashboard/static/index.html
 M src/gamma/dashboard/static/monitor.html
 M src/gamma/dashboard/static/monitor.js
 M src/gamma/dashboard/static/overlay.html
```

Recent commits show recent refactoring (f1c20e8) that broke dashboard.js into multiple files.

## Reproduction summary
- **URL tested**: `/dashboard/settings`
- **What was wrong**: All page sections were visible at once (dashboard-overview, status, stream, settings, memory panels all shown)
- **No console/network errors** reported (the issue is missing initialization call, not an error)

## Files involved

### Backend
1. **`src/gamma/dashboard/main.py`** (lines 131-152)
   - Backend generates `data-dashboard-page` attribute on `<body>` tag
   - Example: `<body data-dashboard-page="settings">`
   - Passes `dashboard_page` parameter through route handlers

### Frontend
2. **`src/gamma/dashboard/static/nav.js`** (lines 222-252)
   - Defines `applyDashboardTabVisibility()` function with complete logic
   - **NEVER CALLED** - the main bug
   - Function should:
     - Get all panels via `querySelectorAll('[data-dashboard-tab]')`
     - Get active tabs via `currentDashboardTabs()`
     - Toggle `tab-hidden` class on each panel based on active tabs
     - Set `data-dashboard-page` and `data-active-tab` on body
     - Activate nav links

3. **`src/gamma/dashboard/static/init.js`**
   - Main initialization entry point
   - Logs module load but does NOT call `applyDashboardTabVisibility()`
   - Line 479: `console.log('Gamma dashboard modules loaded: nav.js, memory.js, ...')`

4. **`src/gamma/dashboard/static/index.html`**
   - Contains `data-dashboard-tab` attributes on `<section>` elements
   - Example: `<section class="panel full-width dashboard-overview" data-dashboard-tab="dashboard-overview">`
   - No `tab-hidden` class applied by default (panels visible by default)

### Styling
5. **`src/gamma/dashboard/static/dashboard.css`** (line 579)
   - Rules `.panel.tab-hidden { display: none; }`
   - This CSS rule works only when `tab-hidden` class is added by JavaScript

## Backend route findings

All routes return the same `index.html`:

| Route | Function | `dashboard_page` param |
|-------|----------|-----------------------|
| `/dashboard` | `dashboard()` (line 74) | `"dashboard"` |
| `/dashboard/live` | `dashboard_live_page()` (line 79) | `"live"` |
| `/dashboard/status` | `dashboard_status_page()` (line 84) | `"status"` |
| `/dashboard/stream` | `dashboard_stream_page()` (line 89) | `"stream"` |
| `/dashboard/memory` | `dashboard_memory_page()` (line 99) | `"memory"` |
| `/dashboard/settings` | `dashboard_settings_page()` (line 104) | `"settings"` |
| `/dashboard/monitor` | `dashboard_monitor_page()` (line 114) | `"monitor"` |

Backend sets `data-dashboard-page` on `</head>` tag (lines 147-150 in main.py):
```python
html = html.replace(
    "</head>",
    f"  {config}\n</head>",
    1
)
```

Where config contains: `window.GAMMA_DASHBOARD_PAGE = "{dashboard_page}"`

## Frontend routing/filtering findings

**Critical bug**: `applyDashboardTabVisibility()` is defined but never invoked.

- **Line 222** in nav.js: `function applyDashboardTabVisibility()` - function defined
- **Never called** anywhere within nav.js (checked via grep)
- **Never called** from init.js
- Function correctly implemented but silent failure (no missing call error)

The function should be called after DOM is ready.

## HTML page section structure

| Panel `data-dashboard-tab` | Default State |
|---------------------------|----------------|
| `dashboard-overview` | Visible |
| `status` (x4 panels) | Visible |
| `providers` | Visible |
| `logs` (x2 panels) | Visible |
| `stream settings` | Visible |
| `stream` | Visible |
| `voice` | Visible |
| `settings` (x3 panels) | Visible |
| `memory` (x4 panels) | Visible |

**Issue**: All have `data-dashboard-tab` attribute but NO `tab-hidden` class applied initially.

## CSS visibility findings

- **Line 579** in dashboard.css: `.panel.tab-hidden { display: none; }`
- Panels default to visible (no `tab-hidden` class)
- `tab-hidden` class must be added by JavaScript (via `applyDashboardTabVisibility()`)
- Current behavior: All elements visible regardless of route

## Most likely cause

**`applyDashboardTabVisibility()` function in `nav.js` is never called.**

The function is complete and correct:
- Identifies active page via `dashboardPage` (from `window.GAMMA_DASHBOARD_PAGE` or `location.pathname`)
- Gets active tabs from `dashboardPageTabs` map
- Iterates all panels with `data-dashboard-tab` attribute
- Compares panel's `data-dashboard-tab` values to active tabs list
- Adds `tab-hidden` class to hide non-matching panels

The function was likely intended to be called but was missed during the code refactoring (commit f1c20e8).

## Minimal safe fix idea

Add a call to `applyDashboardTabVisibility()` after the DOM is ready in `nav.js`:

```javascript
})();

// Call applyDashboardTabVisibility after modules are loaded
if (window.addEventListener) {
  window.addEventListener('DOMContentLoaded', function () {
    applyDashboardTabVisibility();
    updateStickyTabOffset();
  });
} else if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', function () {
    applyDashboardTabVisibility();
    updateStickyTabOffset();
  });
} else {
  applyDashboardTabVisibility();
  updateStickyTabOffset();
}
```

Or simpler - just append to end of nav.js after the existing closing `})();`:

```javascript
// Apply tab visibility after all modules are initialized
if (typeof applyDashboardTabVisibility === 'function') {
  applyDashboardTabVisibility();
}
```

Alternatively, add a `DOMContentLoaded` handler in init.js before the existing `console.log`:

```javascript
// Initialize page filtering and tab offsets
if (window.addEventListener) {
  window.addEventListener('DOMContentLoaded', function () {
    if (typeof applyDashboardTabVisibility === 'function') {
      applyDashboardTabVisibility();
    }
    if (typeof updateStickyTabOffset === 'function') {
      updateStickyTabOffset();
    }
  });
}
```

## Information still missing

None - all evidence points to the missing function call as the root cause. The fix is minimal and surgical.
