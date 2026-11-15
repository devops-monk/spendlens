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
