/* Execute the actual preview module with a controlled document. No browser or network. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/gamma/dashboard/static/coordination_designs.js'), 'utf8');
class Element {
  constructor(tag = 'div') {this.tagName = tag; this.children = []; this.dataset = {}; this.attrs = {}; this.listeners = {}; this.className = ''; this.value = ''; this.hidden = false; this.checked = false; this.disabled = false; this.variables = new Map(); this.style = {setProperty:(k,v)=>this.variables.set(k,v),removeProperty:k=>this.variables.delete(k)};}
  get classList() {return {add:name=>{const values=new Set(this.className.split(' ').filter(Boolean));values.add(name);this.className=[...values].join(' ');},contains:name=>this.className.split(' ').includes(name),toggle:name=>{const values=new Set(this.className.split(' ').filter(Boolean));const added=!values.has(name);if(added)values.add(name);else values.delete(name);this.className=[...values].join(' ');return added;}};}
  set href(value) {this.attrs.href=value;} get href(){return this.attrs.href;}
  setAttribute(k,v){this.attrs[k]=v;} getAttribute(k){return this.attrs[k]??null;}
  append(...values){for(const value of values){if(value.tagName==='#fragment')this.append(...value.children);else {this.children.push(value);value.parent=this;}}}
  prepend(value){this.children.unshift(value);value.parent=this;}
  after(value){const index=this.parent.children.indexOf(this);this.parent.children.splice(index+1,0,value);value.parent=this.parent;}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(v=>v!==this);}
  replaceChildren(...values){this.children=[];this.append(...values);}
  addEventListener(k,fn){(this.listeners[k]||=[]).push(fn);}
  async fire(k,event={}){for(const fn of this.listeners[k]||[])await fn({preventDefault(){},...event});}
}
function descendants(node){return node.children.flatMap(child=>[child,...descendants(child)]);}
function setup(address, gallery=false, defaultStyle=null){
  const location = Object.fromEntries(['href','pathname','origin','protocol','search','hash'].map(k=>[k,new URL(address)[k]]));
  const body=new Element('body'), nav=new Element('nav');if(defaultStyle)body.dataset.shanaDefaultStyle=defaultStyle;nav.className='shana-nav';body.append(nav);
  const links=['/dashboard/home','/dashboard/activities','/dashboard/rehearsal','/dashboard/phone'].map(value=>{const link=new Element('a');link.href=value;nav.append(link);return link;});
  const main=new Element('main');body.append(main);const ids=new Map();
  for(const id of ['phonePrivate','phoneSend','phoneContent']){const element=new Element();ids.set(id,element);main.append(element);}
  ids.get('phonePrivate').checked=false;ids.get('phoneSend').disabled=true;ids.get('phoneContent').hidden=true;
  if(gallery){for(const id of ['designGrid','combinedDesign','designPage','designWidth','designNotice']){const element=new Element();ids.set(id,element);main.append(element);}ids.get('designPage').value='home';ids.get('designWidth').value='desktop';}
  const document={readyState:'complete',body,createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('#fragment'),getElementById:id=>ids.get(id)||null,
    querySelector:selector=>selector==='.shana-nav'?nav:descendants(body).find(e=>e.classList.contains('design-preview-bar'))||null,
    querySelectorAll:()=>links};
  let network=0,storage=0;
  const window={};const context={window,document,location,URL,
    fetch:()=>{network++;throw new Error('Preview must not call the network');},
    localStorage:{setItem(){storage++;throw new Error('No saved default');}},
    sessionStorage:{setItem(){storage++;throw new Error('No private/session data storage');}},
    history:{replaceState(_s,_t,value){const url=new URL(value,location.origin);for(const key of ['href','pathname','origin','protocol','search','hash'])location[key]=url[key];}}};
  vm.runInNewContext(source,context);
  return {window,body,nav,links,main,ids,location,network:()=>network,storage:()=>storage};
}
(async()=>{
  const baseline=setup('https://review.example/dashboard/phone');
  assert.equal(baseline.body.dataset.shanaStyle,undefined);assert.equal(baseline.body.variables.size,0);assert.equal(baseline.body.children.length,2);
  assert.equal(baseline.window.ShanaDesigns.styles.length,10);
  assert.equal(baseline.window.ShanaDesigns.previewStyles.length,11);
  assert.equal(baseline.window.ShanaDesigns.combinedStyle.id,'night-signal');
  assert.equal(baseline.window.ShanaDesigns.getStyle('night-signal').name,'Night + Signal');
  for(const invalid of ['__proto__','constructor','quiet-desk;display:none','<script>','missing']){const b=setup('https://review.example/dashboard/phone?style='+encodeURIComponent(invalid));assert.equal(b.body.dataset.shanaStyle,undefined);assert.equal(b.body.variables.size,0);assert.equal(b.network(),0);}
  for(const style of baseline.window.ShanaDesigns.previewStyles){
    for(const page of ['home','activities','rehearsal','phone']){
    const b=setup('https://review.example/dashboard/'+page+'?style='+style.id);
    assert.equal(b.body.dataset.shanaStyle,style.id);assert.equal(b.body.dataset.shanaPage,page);
    assert.equal(b.ids.get('phonePrivate').checked,false);assert.equal(b.ids.get('phoneSend').disabled,true);assert.equal(b.ids.get('phoneContent').hidden,true);
    assert.equal(b.body.variables.get('--design-bg'),style.colors.bg);
    assert.ok(b.links.every(link=>new URL(link.href,'https://review.example').searchParams.get('style')===style.id));
    assert.equal(b.network(),0);assert.equal(b.storage(),0);
    const bar=descendants(b.body).find(e=>e.classList.contains('design-preview-bar'));assert.ok(bar);
    const select=descendants(bar).find(e=>e.tagName==='select');select.value='high-clarity';await select.fire('change');assert.equal(b.body.dataset.shanaStyle,'high-clarity');
    const end=descendants(bar).find(e=>e.tagName==='button');await end.fire('click');assert.equal(b.body.dataset.shanaStyle,undefined);assert.equal(b.body.variables.size,0);assert.equal(new URL(b.location.href).searchParams.has('style'),false);assert.equal(b.ids.get('phoneContent').hidden,true);
    }
  }
  for(const page of ['home','monitor','presence','activities','rehearsal','phone','work','personal','growth']){
    const b=setup('https://review.example/dashboard/'+page,false,'night-signal');
    assert.equal(b.body.dataset.shanaStyle,'night-signal');assert.equal(b.body.variables.get('--design-accent'),'#78d7ed');
    assert.equal(descendants(b.body).some(e=>e.classList.contains('design-preview-bar')),false);
    assert.ok(b.links.every(link=>!new URL(link.href,'https://review.example').searchParams.has('style')));
    assert.equal(b.ids.get('phonePrivate').checked,false);assert.equal(b.ids.get('phoneSend').disabled,true);assert.equal(b.ids.get('phoneContent').hidden,true);
    const changed=setup('https://review.example/dashboard/'+page+'?style=quiet-desk',false,'night-signal');
    assert.equal(changed.body.dataset.shanaStyle,'quiet-desk');
    const bar=descendants(changed.body).find(e=>e.classList.contains('design-preview-bar'));
    await descendants(bar).find(e=>e.tagName==='button').fire('click');
    assert.equal(changed.body.dataset.shanaStyle,'night-signal');assert.equal(new URL(changed.location.href).searchParams.has('style'),false);
    assert.ok(changed.links.every(link=>!new URL(link.href,'https://review.example').searchParams.has('style')));
    assert.equal(changed.network(),0);assert.equal(changed.storage(),0);
  }
  const invalidDefault=setup('https://review.example/dashboard/home',false,'__proto__');assert.equal(invalidDefault.body.dataset.shanaStyle,undefined);
  const invalidOverride=setup('https://review.example/dashboard/home?style=__proto__',false,'night-signal');assert.equal(invalidOverride.body.dataset.shanaStyle,'night-signal');
  const galleryDefault=setup('https://review.example/dashboard/designs',true,'night-signal');assert.equal(galleryDefault.body.dataset.shanaStyle,undefined);
  const gallery=setup('https://review.example/dashboard/designs',true);
  for(const page of ['home','activities','rehearsal','phone']){
    gallery.ids.get('designPage').value=page;await gallery.ids.get('designPage').fire('change');
    const featured=gallery.ids.get('combinedDesign').children;assert.equal(featured.length,1);assert.equal(featured[0].dataset.styleId,'night-signal');assert.ok(featured[0].classList.contains('design-expanded'));
    const sample=descendants(featured[0]).find(e=>e.classList.contains('design-mini'));assert.equal(sample.dataset.samplePage,page);
    const combinedLinks=descendants(featured[0]).filter(e=>e.tagName==='a');assert.equal(combinedLinks.length,4);assert.ok(combinedLinks.every(a=>new URL(a.href,'https://review.example').searchParams.get('style')==='night-signal'));
    const cards=gallery.ids.get('designGrid').children;assert.equal(cards.length,10);assert.equal(new Set(cards.map(c=>c.dataset.styleId)).size,10);
    for(const card of cards){const all=descendants(card);const board=all.find(e=>e.classList.contains('design-mini'));assert.equal(board.dataset.samplePage,page);const live=all.filter(e=>e.tagName==='a');assert.equal(live.length,4);assert.ok(live.every(a=>new URL(a.href,'https://review.example').searchParams.get('style')===card.dataset.styleId));}
  }
  gallery.ids.get('designWidth').value='phone';await gallery.ids.get('designWidth').fire('change');assert.ok(gallery.ids.get('designGrid').children.every(c=>descendants(c).some(e=>e.classList.contains('mini-phone'))));
  assert.ok(descendants(gallery.ids.get('combinedDesign')).some(e=>e.classList.contains('mini-phone')));
  const combinedCard=gallery.ids.get('combinedDesign').children[0],resize=descendants(combinedCard).find(e=>e.tagName==='button');await resize.fire('click');assert.equal(resize.getAttribute('aria-expanded'),'false');assert.equal(combinedCard.classList.contains('design-expanded'),false);
  assert.equal(gallery.network(),0);assert.equal(gallery.storage(),0);
  const offline=setup('file:///saved/gallery.html',true);let prevented=false;const firstLink=descendants(offline.ids.get('designGrid')).find(e=>e.tagName==='a');await firstLink.fire('click',{preventDefault(){prevented=true;}});assert.ok(prevented);assert.match(offline.ids.get('designNotice').textContent,/static/);assert.equal(offline.network(),0);
  console.log(JSON.stringify({passed:true,original_styles:10,combined_styles:1,pages:4,private_gates_unchanged:true,no_network:true,no_saved_default:true,rendered:false,brave_verified:false}));
})().catch(error=>{console.error(error);process.exitCode=1;});
