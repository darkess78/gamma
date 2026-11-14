/* Isolated default and optional visual previews. No API requests, browser preference writes, or control/permission changes. */
(function (global) {
  'use strict';
  const styles = [
    {id:'quiet-desk', name:'Quiet desk', subtitle:'A restrained personal workspace', layout:'slim left rail', density:'comfortable', family:'system-ui, sans-serif', headings:'Georgia, serif', scheme:'dark', description:'A narrow navigation rail, warm charcoal and calm serif headings. The conversation and current task take priority.', tradeoff:'Clear and quiet, with less room for many simultaneous panels.', colors:{bg:'#1a1a1a',panel:'#2b2b2b',text:'#f2f5f8',muted:'#c2c9d3',accent:'#a8b5a2',onAccent:'#11161f',line:'#7b8797',good:'#9bdcb0',danger:'#ffb0b7'}},
    {id:'night-studio', name:'Night studio', subtitle:'A spacious room with a control dock', layout:'bottom navigation dock', density:'spacious', family:'system-ui, sans-serif', headings:'system-ui, sans-serif', scheme:'dark', description:'Deep plum panels, large headings and a floating navigation dock. A studio-like setting for longer sessions.', tradeoff:'The dock stays close, but occupies some vertical space on a phone.', colors:{bg:'#15121f',panel:'#211d2e',text:'#f3efff',muted:'#c3b9d8',accent:'#c6a9ff',onAccent:'#21132f',line:'#81778f',good:'#b2e1c5',danger:'#ffc2cb'}},
    {id:'field-notebook', name:'Field notebook', subtitle:'A reading-first shared journal', layout:'single reading column and tabs', density:'reading', family:'Georgia, serif', headings:'Georgia, serif', scheme:'dark', description:'Dark amber pages, a ruled rhythm and horizontal section tabs. Notes and conversation read like a shared notebook.', tradeoff:'Comfortable for reading; fewer things fit on screen at once.', colors:{bg:'#1f1a14',panel:'#2e261c',text:'#f2f5f8',muted:'#c2c9d3',accent:'#d4a574',onAccent:'#11161f',line:'#7b8797',good:'#9bdcb0',danger:'#ffb0b7'}},
    {id:'signal-room', name:'Signal room', subtitle:'A precise, compact control strip', layout:'top command strip and aligned panels', density:'dense', family:'"Courier New", monospace', headings:'"Courier New", monospace', scheme:'dark', description:'A flat technical layout with crisp dividers, teal accents and aligned status panels. Every control keeps its plain label.', tradeoff:'Fast to scan, with a more technical and less conversational feel.', colors:{bg:'#0a1b24',panel:'#102b35',text:'#edfaff',muted:'#b2d0d8',accent:'#78d7ed',onAccent:'#08202b',line:'#7196a0',good:'#8de1b3',danger:'#ffc2b2'}},
    {id:'soft-companion', name:'Soft companion', subtitle:'A small, welcoming conversation space', layout:'centered pill navigation and one column', density:'open', family:'"Trebuchet MS", system-ui, sans-serif', headings:'"Trebuchet MS", system-ui, sans-serif', scheme:'dark', description:'A centered layout, rounded cards and muted burgundy and rose accents. It puts the person-to-person feeling ahead of dashboard density.', tradeoff:'Friendly and simple, but longer pages need more scrolling.', colors:{bg:'#1e141e',panel:'#2d1f2d',text:'#f2f5f8',muted:'#c2c9d3',accent:'#c9a0b5',onAccent:'#11161f',line:'#7b8797',good:'#9bdcb0',danger:'#ffb0b7'}},
    {id:'editorial-gallery', name:'Editorial gallery', subtitle:'Strong type and asymmetric content', layout:'large masthead and asymmetric columns', density:'editorial', family:'system-ui, sans-serif', headings:'Georgia, serif', scheme:'dark', description:'Bold typography, generous spacing and unequal columns. The main activity gets a larger canvas than supporting controls.', tradeoff:'More character and hierarchy; less compact than a utility dashboard.', colors:{bg:'#141e1e',panel:'#1f2d2d',text:'#f2f5f8',muted:'#c2c9d3',accent:'#74b5b5',onAccent:'#11161f',line:'#7b8797',good:'#9bdcb0',danger:'#ffb0b7'}},
    {id:'compact-workspace', name:'Compact workspace', subtitle:'More useful context in one view', layout:'wide modular workspace', density:'compact', family:'system-ui, sans-serif', headings:'system-ui, sans-serif', scheme:'dark', description:'A wide, flat workspace with tight spacing and a modular panel grid. It favors seeing work, availability and tools together.', tradeoff:'Efficient on a large screen; the phone view becomes a simple stack.', colors:{bg:'#1a1a24',panel:'#262633',text:'#f2f5f8',muted:'#c2c9d3',accent:'#8a8ac9',onAccent:'#11161f',line:'#7b8797',good:'#9bdcb0',danger:'#ffb0b7'}},
    {id:'paper-panels', name:'Paper panels', subtitle:'A structured folio with navigation on the right', layout:'right-side folio navigation', density:'balanced', family:'system-ui, sans-serif', headings:'Georgia, serif', scheme:'dark', description:'A dark sage folio, square paper panels and a right-side navigation column. Content stays on the left with a clear page edge.', tradeoff:'A distinctive desktop arrangement; navigation returns to the top on a phone.', colors:{bg:'#1a1e1a',panel:'#262d26',text:'#f2f5f8',muted:'#c2c9d3',accent:'#a2b5a2',onAccent:'#11161f',line:'#7b8797',good:'#9bdcb0',danger:'#ffb0b7'}},
    {id:'calm-horizon', name:'Calm horizon', subtitle:'A broad header and clear sections', layout:'full-width color masthead', density:'airy', family:'system-ui, sans-serif', headings:'system-ui, sans-serif', scheme:'dark', description:'A blue masthead sets the current moment apart from dark navy working panels. Sections feel open and orderly rather than boxed into a console.', tradeoff:'The large header gives orientation while using more space above the controls.', colors:{bg:'#141a24',panel:'#1f2633',text:'#f2f5f8',muted:'#c2c9d3',accent:'#74a5d4',onAccent:'#11161f',line:'#7b8797',good:'#9bdcb0',danger:'#ffb0b7'}},
    {id:'high-clarity', name:'High clarity', subtitle:'Large, direct and strongly contrasted', layout:'large single column and explicit tabs', density:'large controls', family:'system-ui, sans-serif', headings:'system-ui, sans-serif', scheme:'dark', description:'Strong light-on-dark structure, large text and unmistakable focus states. It removes decorative effects and makes controls easier to find.', tradeoff:'Very clear, with a longer page and less visual subtlety.', colors:{bg:'#0a0a0a',panel:'#1a1a1a',text:'#f2f5f8',muted:'#c2c9d3',accent:'#e0e0e0',onAccent:'#11161f',line:'#7b8797',good:'#9bdcb0',danger:'#ffb0b7'}}
  ].map(style => Object.freeze({...style, colors:Object.freeze(style.colors)}));
  Object.freeze(styles);
  const combinedStyle = Object.freeze({id:'night-signal', name:'Night + Signal', subtitle:'Room to talk. A clear view of what is happening.', layout:'roomy studio with aligned status strip', density:'comfortable', family:'system-ui, sans-serif', headings:'system-ui, sans-serif', scheme:'dark', description:"Night studio's dark plum panels, large headings and generous spacing, with Signal room's teal accents and clear status grouping. The desktop dock wraps; phone navigation stays above the content.", tradeoff:'Keeps the main actions easy to find. Longer phone pages scroll naturally without a dock covering the controls.', colors:Object.freeze({bg:'#15121f',panel:'#211d2e',text:'#f3efff',muted:'#c3b9d8',accent:'#78d7ed',onAccent:'#08202b',line:'#81778f',good:'#b2e1c5',danger:'#ffc2cb'})});
  const previewStyles = Object.freeze([...styles, combinedStyle]);
  const byId = new Map(previewStyles.map(style => [style.id, style]));
  const pages = {home:'/dashboard/home', activities:'/dashboard/activities', rehearsal:'/dashboard/rehearsal', phone:'/dashboard/phone'};
  const knownPages = new Set([...Object.values(pages), '/dashboard/monitor', '/dashboard/work', '/dashboard/presence', '/dashboard/personal', '/dashboard/growth', '/dashboard/designs']);
  const variableNames = {bg:'bg',panel:'panel',text:'text',muted:'muted',accent:'accent',onAccent:'on-accent',line:'line',good:'good',danger:'danger'};
  function setVariables(element, style) {
    for (const [key, value] of Object.entries(style.colors)) element.style.setProperty(`--design-${variableNames[key]}`, value);
    element.style.setProperty('--design-font', style.family); element.style.setProperty('--design-heading-font', style.headings);
    element.style.setProperty('--design-scheme', style.scheme);
  }
  function chooseStyle(requested, fallback, allowed) {
    if (Array.isArray(allowed)) {
      if (allowed.includes(requested)) return { id: requested, preview: true };
      if (allowed.includes(fallback)) return { id: fallback, preview: false };
    }
    return null;
  }
  function applyPreview(id, carryQuery = true) {
    const style = byId.get(id);
    if (!style || typeof document === 'undefined' || location.protocol === 'file:') return false;
    document.body.dataset.shanaStyle = style.id;
    document.body.dataset.shanaPage = Object.keys(pages).find(key => pages[key] === location.pathname) || 'other';
    setVariables(document.body, style);
    if (carryQuery) for (const link of document.querySelectorAll('.shana-nav a, a[data-style-carry]')) {
      const url = new URL(link.getAttribute('href'), location.origin);
      if (url.origin === location.origin && knownPages.has(url.pathname)) {url.searchParams.set('style', style.id); link.href = url.pathname + url.search + url.hash;}
    }
    return true;
  }
  function resetPreview() {
    delete document.body.dataset.shanaStyle;
    for (const name of [...Object.values(variableNames), 'font', 'heading-font', 'scheme']) document.body.style.removeProperty(`--design-${name}`);
    const url = new URL(location.href); url.searchParams.delete('style'); history.replaceState(null, '', url.pathname + url.search + url.hash);
    const bar = document.querySelector('.design-preview-bar'); if (bar) bar.remove();
    for (const link of document.querySelectorAll('.shana-nav a, a[data-style-carry]')) {
      const target = new URL(link.getAttribute('href'), location.origin);
      if (target.origin === location.origin && knownPages.has(target.pathname)) {target.searchParams.delete('style'); link.href = target.pathname + target.search + target.hash;}
    }
    if (location.protocol !== 'file:' && location.pathname !== '/dashboard/designs' && byId.has(document.body.dataset.shanaDefaultStyle)) applyPreview(document.body.dataset.shanaDefaultStyle, false);
  }
  function node(tag, className, text) {const value = document.createElement(tag); if (className) value.className = className; if (text) value.textContent = text; return value;}
  function previewBar(style) {
    if (location.pathname === '/dashboard/designs') return;
    const bar = node('aside', 'design-preview-bar'); bar.setAttribute('aria-label', 'Temporary design preview');
    const label = node('label', '', 'Style preview '), select = node('select'); select.setAttribute('aria-label', 'Preview style');
    for (const item of previewStyles) {const option = node('option', '', item.name); option.value = item.id; select.append(option);}
    select.value = style.id;
    select.addEventListener('change', () => {
      if (!applyPreview(select.value)) return;
      const url = new URL(location.href); url.searchParams.set('style', select.value); history.replaceState(null, '', url.pathname + url.search + url.hash);
    });
    label.append(select); const gallery = node('a', '', 'All ten directions'); gallery.href = '/dashboard/designs';
    const clear = node('button', '', byId.has(document.body.dataset.shanaDefaultStyle) ? 'Use Night + Signal' : 'End preview'); clear.type = 'button'; clear.addEventListener('click', resetPreview);
    bar.append(label, node('span', 'design-preview-note', 'Preview only · not saved'), gallery, clear);
    const nav = document.querySelector('.shana-nav'); if (nav) nav.after(bar); else document.body.prepend(bar);
  }
  const samples = {
    home:{title:'Here with Shana', subtitle:'Talk, share a moment, or focus.', status:'Awake', cards:[['A moment together','A quiet place to talk, or leave a task in progress.','Open conversation'],['What is happening','A small notes helper is ready to review.','View work'],['Share an activity','Keep company during a relaxed skilling session.','Choose a window']]},
    activities:{title:'A little company', subtitle:'A chosen window. A shared goal. Room for silence.', status:'Not sharing', cards:[['Your window','Selected OSRS window · fictional sample view','Choose a window'],['Our context','A relaxed fishing session. Mostly keeping company.','Save context'],['The current moment','Nothing is being captured in this sample.','Private only']]},
    rehearsal:{title:'Try an idea together', subtitle:'Gameplay or building, before taking it further.', status:'Private rehearsal', cards:[['Choose an idea','Build a small tool and chat about the choices.','Start rehearsal'],['Try an example','A quiet moment can stay quiet.','Send example'],['Contributions','We could try that another way. Want to compare?','Text stays here']]},
    phone:{title:'Shana', subtitle:'Your conversation, wherever you are.', status:'Private session', cards:[['Conversation','You: How is our small task?\nShana: Ready for you to review.','Send'],['Updates','A considered reminder can wait until a good moment.','Got it'],['Availability','Reminders off. You choose when contact is welcome.','Settings']]}
  };
  function miniature(style, page, mobile) {
    const sample = samples[page] || samples.home;
    const board = node('div', `design-mini${mobile ? ' mini-phone' : ''}`); board.dataset.design = style.id; board.dataset.samplePage = page;
    board.setAttribute('role', 'img'); board.setAttribute('aria-label', `Static fictional ${page} sample for ${style.name}`); setVariables(board, style);
    const nav = node('div', 'mini-nav'); for (const [index, label] of ['Shana','Talk','Work','Activities','Rehearsal','Phone'].entries()) {const item = node('span', index ? '' : 'mini-brand', label); if (label.toLowerCase() === page || page === 'home' && index === 0) item.classList.add('mini-current'); nav.append(item);}
    const content = node('div', 'mini-content'), header = node('div', 'mini-header');
    const heading = node('div'); heading.append(node('span','mini-eyebrow','Your shared space'),node('h3','',sample.title),node('p','',sample.subtitle)); header.append(heading,node('span','mini-status',sample.status)); content.append(header);
    const grid = node('div', 'mini-grid');
    sample.cards.forEach(([title, text, action], index) => {const card = node('div',`mini-card mini-card-${index}`); card.append(node('strong','',title)); if (page === 'activities' && index === 0) card.append(node('div','mini-window','Selected window · sample')); card.append(node('p','',text),node('span','mini-action',action)); grid.append(card);});
    content.append(grid); board.append(nav,content); return board;
  }
  function installGallery() {
    const list = document.getElementById('designGrid'); if (!list) return;
    const page = document.getElementById('designPage'), width = document.getElementById('designWidth');
    const combined = document.getElementById('combinedDesign');
    const render = (choices, featured = false) => {
      const fragment = document.createDocumentFragment();
      choices.forEach((style, index) => {
        const card = node('article', `design-choice${featured ? ' design-expanded' : ''}`); card.dataset.styleId = style.id;
        const heading = node('header', 'design-choice-heading'), title = node('div'); title.append(node('p','design-number',featured ? '02 + 04 · combined review' : String(index + 1).padStart(2,'0')),node('h2','',style.name),node('p','design-subtitle',style.subtitle)); heading.append(title);
        const expand = node('button','design-expand',featured ? 'Smaller sample' : 'Larger sample'); expand.type = 'button'; expand.setAttribute('aria-expanded',String(featured));
        expand.addEventListener('click', () => {const active = card.classList.toggle('design-expanded'); expand.textContent = active ? 'Smaller sample' : 'Larger sample'; expand.setAttribute('aria-expanded',String(active));}); heading.append(expand);
        card.append(heading,miniature(style,page.value,width.value === 'phone'),node('p','design-sample-label','Fictional sample · these pictured controls are not live'),node('p','design-description',style.description));
        const facts = node('p','design-facts',`${style.layout} · ${style.density}`); card.append(facts,node('p','design-tradeoff',style.tradeoff));
        const links = node('div','design-links');
        for (const [key, path] of Object.entries(pages)) {
          const link = node('a','',key === 'home' ? 'Home' : key[0].toUpperCase()+key.slice(1)); link.href = `${path}?style=${style.id}`; link.target = '_blank'; link.rel = 'noopener'; link.dataset.previewPage = key;
          if (location.protocol === 'file:') link.addEventListener('click', event => {event.preventDefault(); document.getElementById('designNotice').textContent = 'This saved gallery is static. Open Designs in the running dashboard to preview working pages.';});
          links.append(link);
        }
        card.append(links); fragment.append(card);
      });
      return fragment;
    };
    const redraw = () => {
      list.replaceChildren(render(styles));
      if (combined) combined.replaceChildren(render([combinedStyle], true));
    };
    page.addEventListener('change',redraw); width.addEventListener('change',redraw); redraw();
  }
  global.ShanaDesigns = Object.freeze({styles, combinedStyle, previewStyles, getStyle:id => byId.get(id) || null, applyPreview});
  if (typeof document === 'undefined') return;
  function install() {
    const selected = chooseStyle(new URL(location.href).searchParams.get('style'), document.body.dataset.shanaDefaultStyle, previewStyles.map(style => style.id));
    if (selected && location.protocol !== 'file:' && location.pathname !== '/dashboard/designs') {
      applyPreview(selected.id, selected.preview);
      if (selected.preview) previewBar(byId.get(selected.id));
    }
    installGallery();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',install,{once:true}); else install();
})(typeof window === 'undefined' ? globalThis : window);
