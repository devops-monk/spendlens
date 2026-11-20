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
(function(){ // findings report: overview, filters over the embedded payload, paged rows, CSV, print
 const fr=document.querySelector('.fr');if(!fr)return;
 let P;try{P=JSON.parse(document.getElementById('spendlens-data').textContent);}catch(e){return;}
 if(!P||!P.rows||!P.controls)return;
 const rows=P.rows,C=P.controls,PAGE=50,MORE=200;
 const byCtl=C.map(()=>[]);rows.forEach((r,i)=>byCtl[r[0]].push(i));
 let low=null;const text=i=>{if(!low)low=rows.map(r=>(r[2]+' '+r[3]+' '+r[4]+' '+r[5]).toLowerCase());return low[i];};
 const bar=fr.querySelector('.fr-toolbar');if(bar)bar.hidden=false;
 const q=fr.querySelector('.fr-q'),svc=fr.querySelector('.fr-svc'),reg=fr.querySelector('.fr-reg');
 const chips=[...fr.querySelectorAll('.chip')],secs=[...fr.querySelectorAll('details.ctl')];
 const count=fr.querySelector('.fr-count'),none=fr.querySelector('.fr-none');
 const LABEL={alarm:'Alarm',error:'Error',info:'Info',skip:'Skip',ok:'OK'};
 const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const short=r=>{r=String(r);if(!r.startsWith('arn:'))return r;const p=r.split(':'),rest=p.slice(5).join(':');
  if(p.length<6||!rest)return r;return /[:/]/.test(rest)?rest.replace(/^[a-z][a-z0-9-]*[:/](?=.)/,''):rest;};
 const money=v=>v==null||v===''?'':(v>=100?'$'+Math.round(v).toLocaleString('en-US'):'$'+Number(v).toFixed(2));
 const fmt=n=>n.toLocaleString('en-US');
 let match=byCtl,limit=new Map(),filtered=false;
 function rowHTML(r){const nm=short(r[2]);
  return '<tr><td class="nw"><span class="sbadge '+esc(r[1])+'">'+(LABEL[r[1]]||esc(r[1]))+'</span></td>'+
   '<td class="res"><b>'+esc(nm)+'</b>'+(nm!==r[2]?'<span>'+esc(r[2])+'</span>':'')+'</td>'+
   '<td class="wrap">'+esc(r[3])+'</td><td class="nw">'+esc(r[4])+'</td><td class="nw">'+esc(r[5])+'</td>'+
   '<td class="num">'+money(r[6])+'</td></tr>';}
 function draw(sec){const ci=+sec.dataset.i,m=match[ci],lim=limit.get(ci)||PAGE,shown=Math.min(lim,m.length);
  sec.querySelector('tbody').innerHTML=m.length?m.slice(0,lim).map(i=>rowHTML(rows[i])).join(''):
   '<tr><td colspan="6" class="empty">No results in this check match the filters.</td></tr>';
  const tot=byCtl[ci].length,left=m.length-shown;
  sec.querySelector('.ctl-shown').textContent='Showing '+fmt(shown)+' of '+fmt(m.length)+(filtered&&m.length<tot?' matching ('+fmt(tot)+' in this check)':'');
  const more=sec.querySelector('.ctl-more'),all=sec.querySelector('.ctl-all');
  more.hidden=left<=0;more.textContent='Show '+fmt(Math.min(MORE,left))+' more';
  all.hidden=left<=MORE;all.textContent='Show all '+fmt(m.length);
  sec.dataset.drawn='1';}
 function apply(){
  const term=(q&&q.value||'').trim().toLowerCase(),sv=svc?svc.value:'',rg=reg?reg.value:'';
  const on=new Set(chips.filter(c=>c.getAttribute('aria-pressed')==='true').map(c=>c.dataset.status));
  filtered=!!(term||sv||rg||on.size<chips.length);
  match=filtered?byCtl.map((idx,ci)=>sv&&C[ci].service!==sv?[]:idx.filter(i=>{const r=rows[i];
    return on.has(r[1])&&(!rg||r[5]===rg)&&(!term||text(i).includes(term));})):byCtl;
  let shown=0,checks=0;
  secs.forEach(sec=>{const ci=+sec.dataset.i,m=match[ci];sec.hidden=m.length===0;if(m.length){shown+=m.length;checks++;}
   const mc=sec.querySelector('.ctl-match');if(mc){mc.hidden=!filtered;mc.textContent=fmt(m.length)+' match';}
   limit.set(ci,PAGE);delete sec.dataset.drawn;if(sec.open)draw(sec);else if(term&&m.length&&checks<=3)sec.open=true;});
  fr.querySelectorAll('.ov-row').forEach(a=>a.classList.toggle('dim',match[+a.dataset.i].length===0));
  if(count)count.textContent=filtered?'Showing '+fmt(shown)+' of '+fmt(rows.length)+' results in '+checks+' checks':
   fmt(rows.length)+' results in '+secs.length+' checks';
  if(none)none.hidden=shown>0;}
 secs.forEach(sec=>sec.addEventListener('toggle',()=>{if(sec.open&&!sec.dataset.drawn)draw(sec);}));
 fr.addEventListener('click',e=>{const t=e.target;
  const sec=t.closest&&t.closest('details.ctl');
  if(t.classList.contains('ctl-more')){const ci=+sec.dataset.i;limit.set(ci,(limit.get(ci)||PAGE)+MORE);draw(sec);}
  else if(t.classList.contains('ctl-all')){limit.set(+sec.dataset.i,Infinity);draw(sec);}
  const ov=t.closest&&t.closest('.ov-row');
  if(ov){e.preventDefault();const s=document.getElementById('check-'+ov.dataset.i);if(s){s.hidden=false;s.open=true;if(!s.dataset.drawn)draw(s);
   s.scrollIntoView({behavior:'smooth',block:'start'});history.replaceState(null,'','#check-'+ov.dataset.i);}}});
 chips.forEach(c=>c.addEventListener('click',()=>{c.setAttribute('aria-pressed',c.getAttribute('aria-pressed')==='true'?'false':'true');apply();}));
 let tq;if(q)q.addEventListener('input',()=>{clearTimeout(tq);tq=setTimeout(apply,160);});
 if(svc)svc.addEventListener('change',apply);if(reg)reg.addEventListener('change',apply);
 const ex=fr.querySelector('.fr-expand'),co=fr.querySelector('.fr-collapse');
 if(ex)ex.addEventListener('click',()=>secs.forEach(s=>{if(!s.hidden)s.open=true;}));
 if(co)co.addEventListener('click',()=>secs.forEach(s=>s.open=false));
 let opened=[];
 addEventListener('beforeprint',()=>{opened=secs.filter(s=>!s.open&&!s.hidden);opened.forEach(s=>{s.open=true;draw(s);});});
 addEventListener('afterprint',()=>{opened.forEach(s=>s.open=false);opened=[];});
 const csv=fr.querySelector('.fr-csv');
 if(csv)csv.addEventListener('click',()=>{
  const cols=P.columns||['status','est_monthly_saving','service','control','resource','reason','account_id','region','severity'];
  const get={status:r=>r[1],est_monthly_saving:r=>r[6],service:r=>C[r[0]].service,control:r=>C[r[0]].id,title:r=>C[r[0]].title,
   resource:r=>r[2],reason:r=>r[3],account_id:r=>r[4],region:r=>r[5],severity:r=>r[7]};
  const q2=v=>{if(v==null)return'';const s=String(v);return /[",\n\r]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};
  const idx=[].concat(...match);idx.sort((a,b)=>a-b);
  const out=[cols.join(',')].concat(idx.map(i=>cols.map(k=>q2((get[k]||(()=>''))(rows[i]))).join(','))).join('\r\n')+'\r\n';
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([out],{type:'text/csv'}));
  a.download=(document.title||'spendlens').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')+'.csv';
  document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},0);});
 apply();
 if(location.hash.startsWith('#check-')){const s=document.querySelector(location.hash);if(s){s.open=true;draw(s);}}
})();
(function(){ // cost breakdown: search across groups, expand/collapse, overview jump
 const bd=document.querySelector('.bd');if(!bd)return;
 const bar=bd.querySelector('.bd-toolbar');if(bar)bar.hidden=false;
 const q=bd.querySelector('.bd-q'),groups=[...bd.querySelectorAll('details.bd-grp')],count=bd.querySelector('.bd-count'),none=bd.querySelector('.bd-none');
 const total=groups.reduce((n,g)=>n+g.querySelectorAll('tbody tr[data-q]').length,0);
 function apply(){const t=(q&&q.value||'').trim().toLowerCase();let shown=0,gs=0;
  groups.forEach(g=>{let n=0;g.querySelectorAll('tbody tr[data-q]').forEach(tr=>{const ok=!t||tr.dataset.q.includes(t);tr.classList.toggle('bd-hide',!ok);if(ok)n++;});
   g.classList.toggle('bd-hide',!!t&&n===0);if(n){shown+=n;gs++;}if(t&&n)g.open=true;});
  if(count)count.textContent=t?'Showing '+shown+' of '+total+' items in '+gs+' groups':total+' items in '+groups.length+' groups';
  if(none)none.hidden=!t||shown>0;}
 let tq;if(q)q.addEventListener('input',()=>{clearTimeout(tq);tq=setTimeout(apply,150);});
 const ex=bd.querySelector('.bd-expand'),co=bd.querySelector('.bd-collapse');
 if(ex)ex.addEventListener('click',()=>groups.forEach(g=>{if(!g.classList.contains('bd-hide'))g.open=true;}));
 if(co)co.addEventListener('click',()=>groups.forEach(g=>g.open=false));
 bd.addEventListener('click',e=>{const a=e.target.closest&&e.target.closest('.bd-row');if(!a)return;e.preventDefault();
  const g=document.getElementById('grp-'+a.dataset.i);if(g){g.classList.remove('bd-hide');g.open=true;g.scrollIntoView({behavior:'smooth',block:'start'});}});
 addEventListener('beforeprint',()=>groups.forEach(g=>g.open=true));
 apply();
 document.querySelectorAll('input[data-review]').forEach(cb=>{const k='spendlens-review:'+cb.dataset.review;
  const mark=()=>cb.closest('tr')&&cb.closest('tr').classList.toggle('reviewed',cb.checked);
  try{cb.checked=localStorage.getItem(k)==='1';}catch(e){}mark();
  cb.addEventListener('change',()=>{mark();try{cb.checked?localStorage.setItem(k,'1'):localStorage.removeItem(k);}catch(e){}});});
})();

(function(){ // review queue: approve / defer / reject + note per proposal, kept in this browser, exported as CSV
 const q=document.querySelector('.rv-queue');if(!q)return;
 let P={};try{P=JSON.parse(document.getElementById('spendlens-data').textContent)||{};}catch(e){}
 const KEY='spendlens-decisions:'+(q.dataset.key||'local'),WHO='spendlens-reviewer';
 let S={};try{S=JSON.parse(localStorage.getItem(KEY)||'{}')||{};}catch(e){S={};}
 const save=()=>{try{localStorage.setItem(KEY,JSON.stringify(S));}catch(e){}};
 const rows=[...q.querySelectorAll('tbody tr[data-id]')];
 const tools=q.querySelector('.rv-tools');if(tools)tools.hidden=false;
 const cur=P.currency||'USD',sym={USD:'$',GBP:'£',EUR:'€'}[cur]||'';
 const money=v=>{const a=Math.abs(v);const s=a>=10000?(a/1000).toFixed(1)+'K':a>=100?Math.round(a).toLocaleString('en-US'):a.toFixed(2);return (v<0?'-':'')+sym+s+(sym?'':' '+cur);};
 const who=q.querySelector('.rv-who');try{if(who)who.value=localStorage.getItem(WHO)||'';}catch(e){}
 if(who)who.addEventListener('input',()=>{try{localStorage.setItem(WHO,who.value);}catch(e){}});
 function paint(tr){const st=S[tr.dataset.id]||{},d=st.d||'';
  tr.querySelectorAll('.rv-dec button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.d===d?'true':'false'));
  tr.classList.remove('d-approve','d-defer','d-reject');if(d)tr.classList.add('d-'+d);
  tr.cells[0].dataset.v={approve:1,defer:2,reject:3}[d]||0;
  const n=tr.querySelector('.rv-note');if(n&&document.activeElement!==n)n.value=st.n||'';}
 function totals(){const c={approve:0,defer:0,reject:0,pending:0},m={approve:0,defer:0,reject:0,pending:0};let all=0;
  rows.forEach(tr=>{const d=(S[tr.dataset.id]||{}).d||'pending',a=+tr.dataset.amt||0;c[d]++;m[d]+=a;all+=a;});
  for(const k in c){const b=q.querySelector('[data-s="'+k+'"]'),e=q.querySelector('[data-m="'+k+'"]');if(b)b.textContent=c[k];if(e)e.textContent=money(m[k]);}
  ['approve','defer','reject'].forEach(k=>{const i=q.querySelector('.rv-bar .'+k);if(i)i.style.width=(all?m[k]/all*100:0)+'%';});}
 rows.forEach(tr=>{paint(tr);
  tr.querySelectorAll('.rv-dec button').forEach(b=>b.addEventListener('click',()=>{const id=tr.dataset.id,st=S[id]||{};
   if(st.d===b.dataset.d){delete st.d;}else{st.d=b.dataset.d;st.t=new Date().toISOString();}
   if(st.d||st.n)S[id]=st;else delete S[id];save();paint(tr);totals();filter();}));
  const n=tr.querySelector('.rv-note');if(n)n.addEventListener('input',()=>{const id=tr.dataset.id,st=S[id]||{};st.n=n.value;
   if(st.d||st.n)S[id]=st;else delete S[id];save();});});
 const fq=q.querySelector('.rv-q'),fs=q.querySelector('.rv-svc'),fe=q.querySelector('.rv-eff'),fd=q.querySelector('.rv-dec-f'),shown=q.querySelector('.rv-shown');
 function filter(){const t=(fq&&fq.value||'').trim().toLowerCase(),sv=fs?fs.value:'',ef=fe?fe.value:'',dc=fd?fd.value:'';let n=0;
  rows.forEach(tr=>{const d=(S[tr.dataset.id]||{}).d||'pending';
   const ok=(!t||tr.dataset.q.includes(t))&&(!sv||tr.dataset.svc===sv)&&(!ef||tr.dataset.eff===ef)&&(!dc||d===dc);
   tr.classList.toggle('rv-hide',!ok);if(ok)n++;});
  if(shown)shown.textContent=n===rows.length?'':'Showing '+n+' of '+rows.length+' proposals.';}
 let tq;if(fq)fq.addEventListener('input',()=>{clearTimeout(tq);tq=setTimeout(filter,150);});
 [fs,fe,fd].forEach(x=>{if(x)x.addEventListener('change',filter);});
 const clr=q.querySelector('.rv-clear');
 if(clr)clr.addEventListener('click',()=>{if(!confirm('Clear every decision and note for this account in this browser?'))return;S={};save();rows.forEach(paint);totals();filter();});
 const csv=q.querySelector('.rv-csv');
 if(csv)csv.addEventListener('click',()=>{const items=P.review||[];
  const cols=['decision','decided_at','reviewer','note','est_monthly_saving','est_yearly_saving','proposal','service','resource','region','owner','effort','kind','found_by','control','reason'];
  const q2=v=>{if(v==null)return'';const s=String(v);return /[",\n\r]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};
  const name=who?who.value:'';
  const lines=items.map(r=>{const st=S[r.id]||{};return [st.d||'pending',st.t||'',st.d?name:'',st.n||'',r.amount,(r.amount*12).toFixed(2),r.what,r.service,r.full,r.region,r.owner,r.effort,r.kind_label,r.source,r.control,r.reason].map(q2).join(',');});
  const out=[cols.join(',')].concat(lines).join('\r\n')+'\r\n';
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([out],{type:'text/csv'}));
  a.download='spendlens-review-decisions-'+(q.dataset.key||'local')+'-'+new Date().toISOString().slice(0,10)+'.csv';
  document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},0);});
 totals();filter();
})();
/* resource graphs: hover a node to highlight its links */
(()=>{document.querySelectorAll('svg.slg').forEach(s=>{if(s.dataset.on)return;s.dataset.on=1;
const E=[...s.querySelectorAll('.e')];s.querySelectorAll('.n').forEach(n=>{const id=n.dataset.id;
n.addEventListener('mouseenter',()=>{s.classList.add('dim');n.classList.add('hi');E.forEach(e=>{if(e.dataset.a===id||e.dataset.b===id){
e.classList.add('hi');const o=e.dataset.a===id?e.dataset.b:e.dataset.a;const m=s.querySelector('.n[data-id="'+CSS.escape(o)+'"]');if(m)m.classList.add('hi');}});});
n.addEventListener('mouseleave',()=>{s.classList.remove('dim');s.querySelectorAll('.hi').forEach(x=>x.classList.remove('hi'));});});});})();
