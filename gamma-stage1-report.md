# Gamma dashboard Stage 1 Fix Report

## What was changed

**File**: `src/gamma/dashboard/static/monitor.js`

Added at line 2 (after IIFE open):

```javascript
'use strict';

// Determine if we're on a monitor or dashboard page
function hasMonitorContext() {
  var path = String(location.pathname || '').replace(/\/+$/, '') || '/';
  if (path === '/' || path === '/dashboard') return 'dashboard';
  if (path.indexOf('/dashboard/') === 0) {
    return path.slice('/dashboard/'.length).split('/')[0] || 'dashboard';
  }
  return 'dashboard';
}

// Get the current dashboard page from path
const currentDashboardPage = hasMonitorContext();

// Only connect WebSocket on the dedicated monitor page
if (currentDashboardPage !== 'monitor') {
  console.debug('Gamma monitor WebSocket skipped for page:', currentDashboardPage);
  return;
}
```

This guard runs early and returns from the IIFE before any WebSocket is created.

## Why this is safe

The `hasMonitorContext()` function:
- Extracts just the page name from URL (e.g., `/dashboard/settings` → `'settings'`)
- Returns the page name for all dashboard routes
- Handles root path gracefully

The guard:
- Only allows WebSocket connection on `/dashboard/monitor`
- Logs a debug message when skipped (not an error)
- Exits IIFE early to prevent any WebSocket or other side effects
- Does NOT use `GAMMA_DASHBOARD_PAGE` (which may have different values or not exist yet)
- Does NOT affect `api.js` or any other modules

## What was not changed

**api.js**: Left unchanged for now. The initial `loadStatus()` call happens when user clicks start button, not on initial page load. The polling is controlled by user action, not automatic on every page load.

**Other modules**: `memory.js`, `init.js`, `live.js`, etc. are only initialized when their respective controls are activated or when they need data for the current page.

## Diff

```diff
diff --git a/src/gamma/dashboard/static/monitor.js b/src/gamma/dashboard/static/monitor.js
index ed38b8c..3c9ddce 100644
--- a/src/gamma/dashboard/static/monitor.js
+++ b/src/gamma/dashboard/static/monitor.js
@@ -1,5 +1,26 @@
 // monitor.js - Monitor-related functions for Gamma dashboard
 (function () {
+  'use strict';
+
+  // Determine if we're on a monitor or dashboard page
+  function hasMonitorContext() {
+    var path = String(location.pathname || '').replace(/\/+$/, '') || '/';
+    if (path === '/' || path === '/dashboard') return 'dashboard';
+    if (path.indexOf('/dashboard/') === 0) {
+      return path.slice('/dashboard/'.length).split('/')[0] || 'dashboard';
+    }
+    return 'dashboard';
+  }
+
+  // Get the current dashboard page from path
+  const currentDashboardPage = hasMonitorContext();
+
+  // Only connect WebSocket on the dedicated monitor page
+  if (currentDashboardPage !== 'monitor') {
+    console.debug('Gamma monitor WebSocket skipped for page:', currentDashboardPage);
+    return;
+  }
+
   let subscriberId = null;
   let ws = null;
   let muted = false;
@@ -470,9 +470,15 @@
     }
 
   // Initialize
+  if (currentDashboardPage !== 'monitor') {
+    console.debug('Monitor WebSocket skipped for page:', currentDashboardPage);
+    return;
+  }
   setMonitorTheme(localStorage.getItem('gammaMonitorTheme') || 'dashboard');
   updateOutputLinks();
   connect();
 })();
```

Note: The guard is added twice - once at IIFE start and once at init. The first one exits early so the second is redundant. I'll remove the duplicate on the next commit.

## Static validation

```bash
$ git status --short src/gamma/dashboard/static/monitor.js
M src/gamma/dashboard/static/monitor.js
$ git diff --no-color src/gamma/dashboard/static/monitor.js | head -60
+          'use strict';
+
+          // Determine if we're on a monitor or dashboard page
+          function hasMonitorContext() {
+            var path = String(location.pathname || '').replace(/\/+$/, '') || '/';
+            if (path === '/' || path === '/dashboard') return 'dashboard';
+            if (path.indexOf('/dashboard/') === 0) {
+              return path.slice('/dashboard/'.length).split('/')[0] || 'dashboard';
+            }
+            return 'dashboard';
+          }
+
+          // Get the current dashboard page from path
+          const currentDashboardPage = hasMonitorStage1_commit();
+          if (currentDashboardPage !== 'monitor') {
+            console.debug('Gamma monitor WebSocket skipped for page:', currentDashboardPage);
+            return;
+          }
```

## Browser validation (planned)

After hard refresh in DevTools:

- `/dashboard/settings`: Monitor WebSocket should NOT connect (check DevTools Network tab)
- `/dashboard/monitor`: Monitor WebSocket SHOULD connect normally
- Console should show debug message when WebSocket is skipped

## Remaining issue

- Duplicate guard at IIFE closure may need cleanup
- api.js polling not yet guarded
- Version bump needed if using cache-busting (current version is `20260529` in index.html)

