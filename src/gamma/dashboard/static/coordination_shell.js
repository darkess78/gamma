(() => {
  'use strict';
  const id = () => crypto.randomUUID().replaceAll('-', '');
  async function request(path, values, {signal, keepalive = false} = {}) {
    const options = {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}, signal, keepalive};
    if (values !== undefined) {
      options.method = 'POST';
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify({audience: 'owner_private', request_id: id(), ...values});
    }
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(response.status === 401 ? 'Please sign in again.' : typeof data.detail === 'string' ? data.detail.replaceAll('_', ' ') : 'That request did not finish. Check the connection before trying again.');
    return data;
  }
  function notice(element, text, error = false) {element.textContent = text; element.classList.toggle('shana-error', error);}
  function feed(element, text) {const row = document.createElement('li'); row.textContent = text; element.prepend(row); while (element.children.length > 30) element.lastElementChild.remove();}
  window.ShanaUI = {id, request, notice, feed};
  function install() {
    if (document.querySelector('.shana-nav')) return;
    const nav = document.createElement('nav'); nav.className = 'shana-nav'; nav.setAttribute('aria-label', 'Shana');
    const links = [['Shana','/dashboard/home'],['Talk','/dashboard/monitor'],['Work','/dashboard/work'],['Activities','/dashboard/activities'],['Rehearsal','/dashboard/rehearsal'],['Phone','/dashboard/phone']];
    for (const [label, href] of links) {const a = document.createElement('a'); a.href = href; a.textContent = label; if (label === 'Shana') a.className = 'shana-brand'; if (location.pathname === href || label === 'Shana' && location.pathname === '/dashboard') a.setAttribute('aria-current','page'); nav.append(a);}
    document.body.prepend(nav);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, {once: true}); else install();
})();
