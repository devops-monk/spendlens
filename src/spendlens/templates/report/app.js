(function(){
 const root=document.documentElement;
 try{const t=localStorage.getItem('spendlens-theme');if(t)root.dataset.theme=t;}catch(e){}
 const tb=document.querySelector('button.theme');
 if(tb)tb.addEventListener('click',()=>{const cur=root.dataset.theme||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
  const nxt=cur==='dark'?'light':'dark';root.dataset.theme=nxt;try{localStorage.setItem('spendlens-theme',nxt);}catch(e){}});
 const tabs=[...document.querySelectorAll('nav.tabs button')];
 function show(id){tabs.forEach(b=>b.setAttribute('aria-selected',b.dataset.tab===id));
  document.querySelectorAll('section.tab').forEach(s=>s.classList.toggle('active',s.id==='tab-'+id));}
 tabs.forEach(b=>b.addEventListener('click',()=>{show(b.dataset.tab);history.replaceState(null,'','#'+b.dataset.tab);window.scrollTo(0,0);}));
 if(tabs.length){const h=location.hash.slice(1);show(tabs.some(b=>b.dataset.tab===h)?h:tabs[0].dataset.tab);}
 const tip=document.getElementById('tip');
 function place(x,y){const w=tip.offsetWidth,h=tip.offsetHeight;tip.style.left=Math.min(x+14,innerWidth-w-8)+'px';
  tip.style.top=Math.max(8,y-h-12)+'px';}
 document.addEventListener('pointermove',e=>{const el=e.target.closest('[data-tip]');
  if(!el){tip.style.opacity=0;return;}tip.textContent=el.dataset.tip;tip.style.opacity=1;place(e.clientX,e.clientY);});
 document.addEventListener('focusin',e=>{const el=e.target.closest('[data-tip]');if(!el)return;
  tip.textContent=el.dataset.tip;tip.style.opacity=1;const r=el.getBoundingClientRect();place(r.left+r.width/2,r.top);});
 document.addEventListener('focusout',()=>{tip.style.opacity=0;});
 document.querySelectorAll('table.sortable th').forEach((th,i)=>th.addEventListener('click',()=>{
  const tb=th.closest('table').tBodies[0];const rows=[...tb.rows];const dir=th.dataset.dir==='asc'?'desc':'asc';th.dataset.dir=dir;
  const val=r=>{const c=r.cells[i];const v=c.dataset.v!==undefined?c.dataset.v:c.textContent;const n=parseFloat(v);return isNaN(n)?v.toLowerCase():n;};
  rows.sort((a,b)=>{const x=val(a),y=val(b);return (x>y?1:x<y?-1:0)*(dir==='asc'?1:-1);});rows.forEach(r=>tb.appendChild(r));}));
})();
(function(){ // findings report: search, status/service filters, expand/collapse, CSV of visible rows, print
 const fr=document.querySelector('.fr');if(!fr)return;
 const bar=fr.querySelector('.fr-toolbar');if(bar)bar.hidden=false;
 const q=fr.querySelector('.fr-q'),svc=fr.querySelector('.fr-svc'),chips=[...fr.querySelectorAll('.chip')];
 const ctls=[...fr.querySelectorAll('details.ctl')],count=fr.querySelector('.fr-count'),none=fr.querySelector('.fr-none');
 const total=ctls.reduce((n,c)=>n+c.querySelectorAll('tbody tr').length,0);
 function apply(){
  const term=(q&&q.value||'').trim().toLowerCase(),sv=svc?svc.value:'';
  const on=new Set(chips.filter(c=>c.getAttribute('aria-pressed')==='true').map(c=>c.dataset.status));
  let shown=0,checks=0;
  ctls.forEach(c=>{let n=0;const svcOk=!sv||c.dataset.service===sv;
   c.querySelectorAll('tbody tr').forEach(tr=>{const ok=svcOk&&on.has(tr.dataset.status)&&(!term||tr.dataset.q.includes(term));
    tr.classList.toggle('fr-hide',!ok);if(ok)n++;});
   c.classList.toggle('fr-hide',n===0);if(n){shown+=n;checks++;}
   if(term&&n)c.open=true;});
  if(count)count.textContent=shown===total?`${total} results in ${ctls.length} checks`:`Showing ${shown} of ${total} results in ${checks} checks`;
  if(none)none.hidden=shown>0;}
 chips.forEach(c=>c.addEventListener('click',()=>{c.setAttribute('aria-pressed',c.getAttribute('aria-pressed')==='true'?'false':'true');apply();}));
 if(q)q.addEventListener('input',apply);if(svc)svc.addEventListener('change',apply);
 const ex=fr.querySelector('.fr-expand'),co=fr.querySelector('.fr-collapse');
 if(ex)ex.addEventListener('click',()=>ctls.forEach(c=>{if(!c.classList.contains('fr-hide'))c.open=true;}));
 if(co)co.addEventListener('click',()=>ctls.forEach(c=>c.open=false));
 let opened=[];
 addEventListener('beforeprint',()=>{opened=ctls.filter(c=>!c.open);opened.forEach(c=>c.open=true);});
 addEventListener('afterprint',()=>{opened.forEach(c=>c.open=false);opened=[];});
 const csvBtn=fr.querySelector('.fr-csv');
 if(csvBtn)csvBtn.addEventListener('click',()=>{
  let data,cols;try{data=JSON.parse(document.getElementById('spendlens-data').textContent);
   cols=JSON.parse(document.getElementById('spendlens-csv-columns').textContent);}catch(e){return;}
  const idx=[];ctls.forEach(c=>c.querySelectorAll('tbody tr:not(.fr-hide)').forEach(tr=>idx.push(+tr.dataset.i)));
  idx.sort((a,b)=>a-b);const rows=idx.map(i=>data.rows[i]).filter(Boolean);
  const esc=v=>{if(v==null)return'';const s=typeof v==='object'?JSON.stringify(v):String(v);
   return /[",\n\r]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};
  const text=[cols.join(',')].concat(rows.map(r=>cols.map(k=>esc(r[k])).join(','))).join('\r\n')+'\r\n';
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type:'text/csv'}));
  a.download=(document.title||'spendlens').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')+'.csv';
  document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},0);});
 apply();
})();
