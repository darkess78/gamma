export function element(id) {
  return document.getElementById(id);
}

export function setText(id, value) {
  const target = element(id);
  if (target) target.textContent = value == null ? 'n/a' : String(value);
}

export async function requestJson(path, options = {}) {
  const response = await fetch(path, options);
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    throw new Error(`Invalid JSON response from ${path}`);
  }
  if (!response.ok) {
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  return payload;
}
