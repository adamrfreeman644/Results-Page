const $=s=>document.querySelector(s),esc=v=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
let selected=null,selectedLevel=null,lastUpdateAt=Date.now(),lastSourceStamp=null,staleTimer=null;const raceDetails=new Map();

function renderUpdateAge(){
  const el=$('#updated-status');if(!el)return;
  const seconds=Math.max(0,Math.floor((Date.now()-lastUpdateAt)/1000));
  const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),secs=seconds%60;
  const age=seconds>=18000000?'Long ago':hours?hours+':'+String(minutes).padStart(2,'0')+':'+String(secs).padStart(2,'0'):minutes?minutes+':'+String(secs).padStart(2,'0'):secs+' sec';
  const at=new Date(lastUpdateAt);
  const clock=String(at.getHours()).padStart(2,'0')+':'+String(at.getMinutes()).padStart(2,'0');
  el.textContent='Updated '+clock+' · '+(age==='Long ago'?age:age+' ago');
}
function scheduleStaleLabel(){
  clearTimeout(staleTimer);
  const delay=Math.max(0,18000000-(Date.now()-lastUpdateAt));
  staleTimer=setTimeout(renderUpdateAge,delay+50);
}

const tournamentRank=t=>{
  const n=String(t).toLowerCase().replace(/\s+/g,' ').trim();
  const order=['open','open men','women','groms','wild men','wild women','infinity race','surf & dirt','surf and durt','specials','legends','chair race','2 hour relay race'];
  const i=order.indexOf(n);
  return i<0?100:i;
};
/** Adam RaceTec sometimes labels Open as "Open Men" — show Open for 2026. */
const displayTournament=t=>{
  const n=String(t||'').trim();
  if(/^open(\s+men)?$/i.test(n)) return 'Open';
  return n||'Tournament';
};
const levelRank=level=>['Qualifiers','Heats','Quarters','Semi','Finals'].indexOf(level);

async function get(u){
  const r=await fetch(u,{cache:'no-store'});
  if(!r.ok)throw Error();
  return r.json();
}

function table(e,r,{pending=false,projectedRows=null,expanded=false}={}){
  const rows=pending&&projectedRows?projectedRows:r,mobileGlance=!expanded,compact=pending&&!expanded&&!mobileGlance,timeLabel=e.multi_lap?'Fastest lap':'Time',badge=pending?'Up next':'Results';
  const countLabel=pending?`${rows.filter(x=>x.known).length}/${rows.length||4} locked in`:`${rows.length} riders`,detailId=String(e.id||e.name);
  if(!expanded)raceDetails.set(detailId,{e,r,options:{pending,projectedRows}});
  const body=rows.length?rows.map(x=>{
    const pendingRow=pending||x.pending,finish=x.finishPos??x.position,cls=[!pendingRow&&Number(finish)>0&&Number(finish)<=Number(e.highlight_count??2)?'podium':'',pendingRow?'pending-row':'',x.known?'pending-known':(pendingRow?'pending-unknown':'')].filter(Boolean).join(' ');
    const advancement=x.fromLabel&&x.known?`<small class="advancement">${esc(x.fromLabel)}</small>`:'';
    const pathLabel=x.fromLabel||x.placeholder||x.name||(x.seed?`Seed ${x.seed}`:'Awaiting qualification');
    const category=x.category?String(x.category):'',categoryClass=category.toLowerCase().replace(/[^a-z]+/g,'');
    const riderInner=x.athlete_id?`<a class="rider rider-link" href="/rider/?id=${encodeURIComponent(x.athlete_id)}"><span class="bib bib--${categoryClass}">${esc(x.bib)}</span><span class="name">${esc(x.name)}${advancement}</span>${category?`<span class="rider-category rider-category--${categoryClass}">${esc(category)}</span>`:''}</a>`:`<span class="rider rider-placeholder"><span class="name">${esc(pathLabel)}</span></span>`;
    const finishLabel=pendingRow?'N/A':(finish==null||finish===''?'N/A':finish),timeValue=pendingRow&&(x.time==='Not raced yet'||!x.time)?'N/A':x.time;
    if(pendingRow&&compact)return `<tr class="${cls}"><td class="start">${x.startPos??'—'}</td><td class="seed">s${x.seed??'—'}</td><td>${riderInner}</td></tr>`;
    if(mobileGlance)return `<tr class="${cls}"><td class="place">${esc(finishLabel)}</td><td>${riderInner}</td><td class="time">${esc(timeValue)}</td></tr>`;
    return `<tr class="${cls}"><td class="place">${esc(finishLabel)}</td><td class="start">${x.startPos??'—'}</td><td class="seed">${x.seed??'—'}</td><td>${riderInner}</td><td class="time">${esc(timeValue)}</td></tr>`;
  }).join(''):compact?'<tr class="pending-row"><td class="start">—</td><td class="seed">—</td><td><span class="rider rider-placeholder"><span class="name">Waiting for earlier results</span></span></td></tr>':mobileGlance?'<tr><td colspan="3">No results yet.</td></tr>':'<tr><td colspan="5">No results yet.</td></tr>';
  const columns=compact?'<colgroup><col class="result-start"><col class="result-seed"><col class="result-rider"></colgroup><thead><tr><th>Start</th><th>Seed</th><th>Rider</th></tr></thead>':mobileGlance?`<colgroup><col class="result-pos"><col class="result-rider"><col class="result-time"></colgroup><thead><tr><th>Finish</th><th>Rider</th><th>${esc(timeLabel)}</th></tr></thead>`:`<colgroup><col class="result-pos"><col class="result-start"><col class="result-seed"><col class="result-rider"><col class="result-time"></colgroup><thead><tr><th>Finish position</th><th>Starting grid position</th><th>Seed</th><th>Rider</th><th>${esc(timeLabel)}</th></tr></thead>`;
  const headerContent=`<div><span>${esc(badge)}</span><h3>${esc(e.name)}</h3></div><b>${esc(countLabel)}</b>`;
  const cardHeader=expanded?`<header>${headerContent}</header>`:`<button type="button" class="race-card-header" data-race-detail="${esc(detailId)}" aria-label="Open ${esc(e.name)} details">${headerContent}</button>`;
  return `<article class="race-table ${pending?'race-table--pending':''}">${cardHeader}<div class="table-scroll"><table>${columns}<tbody>${body}</tbody></table></div></article>`;
}
function openRaceDetail(id){
  const detail=raceDetails.get(id);if(!detail)return;
  let overlay=document.querySelector('#race-detail-overlay');
  if(!overlay){overlay=document.createElement('div');overlay.id='race-detail-overlay';overlay.hidden=true;document.body.appendChild(overlay);overlay.addEventListener('click',event=>{if(!event.target.closest('.race-detail'))closeRaceDetail()});}
  overlay.innerHTML=`<div class="race-detail" role="dialog" aria-modal="true" aria-label="Race details"><button class="race-detail-close" type="button" aria-label="Close details">×</button><p class="eyebrow">Race details</p>${table(detail.e,detail.r,{...detail.options,expanded:true})}</div>`;
  overlay.querySelector('.race-detail-close').onclick=closeRaceDetail;overlay.hidden=false;document.body.classList.add('race-detail-open');
}
function closeRaceDetail(){const overlay=document.querySelector('#race-detail-overlay');if(overlay)overlay.hidden=true;document.body.classList.remove('race-detail-open');}


function renderCard(item){
  if(item.pending) return table(item.e,item.r,{pending:true,projectedRows:item.projectedRows||[]});
  return table(item.e,item.r);
}

async function render(){
  try{
    const f=await get('/api/public/events');
    const received=Date.parse(f.updatedAt||'');
    if(Number.isFinite(received)&&f.updatedAt!==lastSourceStamp){
      lastSourceStamp=f.updatedAt;lastUpdateAt=received;renderUpdateAge();scheduleStaleLabel();
    }else if(lastSourceStamp===null){
      renderUpdateAge();scheduleStaleLabel();
    }
    // One batched response replaces a separate request for every race card.
    const ids=f.events.map(e=>String(e.id)).filter(id=>!id.startsWith('manual:'));
    const resultMap=ids.length?await get('/api/public/results?ids='+encodeURIComponent(ids.join(','))).catch(()=>({})):{};
    const allItems=f.events.map(e=>({e,r:resultMap[String(e.id)]||[]}));
    if(window.BracketProjection?.refreshSeeds){
      await BracketProjection.refreshSeeds({eventItems:allItems});
    }

    const rawTs=[...new Set(f.events.map(e=>e.tournament||'Tournament'))];
    const ts=[...new Set(rawTs.map(displayTournament))].sort((a,b)=>tournamentRank(a)-tournamentRank(b)||a.localeCompare(b));
    if(!selected||!ts.includes(selected))selected=ts[0];
    $('#event-tabs').innerHTML=ts.map(t=>`<button class="tab ${t===selected?'active':''}" data-t="${esc(t)}">${esc(t)}</button>`).join('');
    $('#event-tabs').querySelectorAll('button').forEach(b=>b.onclick=()=>{selected=b.dataset.t;selectedLevel=null;render()});

    const raw=allItems.filter(item=>displayTournament(item.e.tournament||'Tournament')===selected);
    if(!raw.length){$('#race-title').textContent='No published results';$('#result-count').textContent='';$('#round-nav').innerHTML='';$('#stage-results').innerHTML='';return}

    const knockout=raw.filter(item=>!(window.BracketProjection?.isSeedingEvent?.(item.e)));
    const enriched=window.BracketProjection
      ? BracketProjection.enrichTournament(knockout)
      : {bracket:knockout.map(x=>({...x,pending:false})),extras:[]};
    const seedingExtras=raw.filter(item=>window.BracketProjection?.isSeedingEvent?.(item.e)).map(item=>{
      const category=window.BracketProjection?.getSeedCatalog?.()?.categories||{};
      const label=[item.e.tournament,item.e.name,item.e.stage,item.e.level].filter(Boolean).join(' ').toLowerCase();
      const seedRows=label.includes('wild')&&label.includes('grom')?category['Wild Groms']||[]:label.includes('wild')&&label.includes('women')?category['Wild Women']||[]:label.includes('wild')?category['Wild Open']||[]:label.includes('grom')?category.Groms||[]:label.includes('women')?category.Women||[]:category.Open||[];
      const seedByRider=new Map(seedRows.map(row=>[String(row.athlete_id||''),row.seed]));
      return {
        ...item,
        pending:false,
        r:(item.r||[]).map(r=>({...r,finishPos:Number(r.position)||null,startPos:null,seed:seedByRider.get(String(r.athlete_id||''))??null})),
      };
    });
    const all=[...enriched.bracket,...enriched.extras,...seedingExtras];
    const levels=[...new Set(all.map(item=>BracketProjection?BracketProjection.stageOfItem(item):'Qualifiers'))].sort((a,b)=>levelRank(a)-levelRank(b));
    if(!selectedLevel||!levels.includes(selectedLevel))selectedLevel=levels[0];
    const pendingCount=all.filter(x=>x.pending).length;
    $('#race-title').textContent=selected;
    $('#result-count').textContent=`${raw.length} published${pendingCount?` · ${pendingCount} up next`:''}`;
    $('#round-nav').innerHTML=levels.map((level,i)=>`<button class="round ${level===selectedLevel?'active':''}" data-level="${esc(level)}"><span class="round-name">${esc(level)}</span><span class="round-step">${i+1}</span></button>`).join('');
    $('#round-nav').querySelectorAll('button').forEach(b=>b.onclick=()=>{selectedLevel=b.dataset.level;render()});
    const set=all.filter(x=>(BracketProjection?BracketProjection.stageOfItem(x):'Qualifiers')===selectedLevel);
    $('#stage-results').innerHTML=`<div class="race-grid ${set.length===1?'single':''}">${set.map(renderCard).join('')}</div><p class="stage-note">Rows are start order. Heats: better seed → earlier gate. Later rounds: both race winners take starts 1–2 (by seed), both 2nds take 3–4. <a href="/seeding/">Open seeding board</a></p>`;
    $('#stage-results').querySelectorAll('.race-card-header[data-race-detail]').forEach(header=>{
      header.addEventListener('click',()=>openRaceDetail(header.dataset.raceDetail));
    });
  }catch{
    $('#race-title').textContent='Waiting for timing feed';
    $('#result-count').textContent='The live results service is not connected yet.';
  }
}
render();
setInterval(render,15000);
setInterval(renderUpdateAge,1000);
