const esc=v=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const CATS=['Open','Women','Groms'];
let selected='Open';

/** Official OWAR seeding-race format (2026). */
const FORMAT={
  Open:{
    lead:'Fastest lap sets the seed. Q1 determines positions 17–32; Q2 determines 1–16.',
    sessions:[
      {label:'Q1',detail:'30 min · all riders · locks seeds 17–32 (slowest half)'},
      {label:'Break',detail:'30 min'},
      {label:'Q2',detail:'30 min · top 16 from Q1 only · locks seeds 1–16 · Q1 laps do not carry over'},
    ],
    noteLocked:'Seeds update live from RaceTec as Q1/Q2 laps land. Seeds 17–32 from Q1; 1–16 from Q2.',
    noteOpen:'Waiting for seeding-race laps on the live feed — riders stay A–Z / Seed N until Q1/Q2 lock.',
  },
  Women:{
    lead:'One timed seeding race. Fastest lap earns seed 1.',
    sessions:[{label:'Seeding',detail:'20 min · all riders · locks every seed'}],
    noteLocked:'Seeded live from the Women seeding session best lap.',
    noteOpen:'Waiting for the Women seeding session on the live feed.',
  },
  Groms:{
    lead:'One timed seeding race. Fastest lap earns seed 1.',
    sessions:[{label:'Seeding',detail:'20 min · all riders · locks every seed'}],
    noteLocked:'Seeded live from the Groms seeding session best lap.',
    noteOpen:'Waiting for the Groms seeding session on the live feed.',
  },
};

function hasTime(r){
  return window.BracketProjection?.riderHasTime
    ? BracketProjection.riderHasTime(r)
    : Boolean(r && ((r.timeSec!=null && Number.isFinite(Number(r.timeSec))) || String(r.time||'').trim()));
}

function sessionForSeed(cat, seed){
  if(cat!=='Open' || seed==null) return '';
  const n=Number(seed);
  if(n>=1 && n<=16) return 'Q2';
  if(n>=17) return 'Q1';
  return '';
}

async function get(u){
  const r=await fetch(u,{cache:'no-store'});
  if(!r.ok) throw Error();
  return r.json();
}

function paint(){
  const catalog=BracketProjection.getSeedCatalog()?.categories||{};
  const fmt=FORMAT[selected]||FORMAT.Open;

  document.querySelector('#seeding-cats').innerHTML=CATS.map(cat=>{
    const n=(catalog[cat]||[]).length;
    return `<button type="button" class="tab seeding-cat ${cat===selected?'active':''}" data-cat="${esc(cat)}">${esc(cat)} <small>${n}</small></button>`;
  }).join('');
  document.querySelectorAll('#seeding-cats .tab').forEach(b=>b.onclick=()=>{selected=b.dataset.cat;paint()});

  document.querySelector('#seeding-title').textContent=`${selected} seeding`;
  document.querySelector('#seeding-lead').textContent=fmt.lead;
  document.querySelector('#seed-format').innerHTML=`<div class="seed-format-label">Race format</div><ol class="seed-format-steps">${fmt.sessions.map(s=>`
    <li><strong>${esc(s.label)}</strong><span>${esc(s.detail)}</span></li>
  `).join('')}</ol>`;

  const raw=catalog[selected]||[];
  const locked=raw.some(hasTime);
  const showSession=selected==='Open';
  const rows=locked
    ? [...raw].sort((a,b)=>(a.seed||999)-(b.seed||999))
    : [...raw].sort((a,b)=>String(a.name||'').localeCompare(String(b.name||''),undefined,{sensitivity:'base'}));

  document.querySelector('#seed-list-note').textContent=locked?fmt.noteLocked:fmt.noteOpen;
  const sessionHead=document.querySelector('#seed-session-head');
  if(sessionHead) sessionHead.hidden=!showSession;
  const colSpan=showSession?4:3;

  document.querySelector('#seed-table tbody').innerHTML=rows.map(r=>{
    const session=locked?sessionForSeed(selected,r.seed):'';
    return `<tr>
      <td class="seed">${locked?(r.seed??'—'):'—'}</td>
      ${showSession?`<td class="session">${esc(session||'—')}</td>`:''}
      <td class="time">${esc(locked?(r.time||''):'')}</td>
      <td class="rider">${esc(r.name)}</td>
    </tr>`;
  }).join('')||`<tr><td colspan="${colSpan}">No riders for ${esc(selected)} yet</td></tr>`;

  const board=BracketProjection.buildCategoryHeatGrids(selected);
  document.querySelector('#heats-title').textContent=board.roundLabel;
  document.querySelector('#heats-note').textContent=
    'Staggered gates 1–4. After the first knockout round: both race winners take starts 1–2 (better seed ahead), both 2nds take 3–4 (better seed ahead).';
  document.querySelector('#heat-grid').innerHTML=board.heats.map(h=>`
    <article class="heat-card">
      <header>
        <strong>${esc(h.title)}</strong>
        <span>seeds ${esc(h.subtitle)}</span>
      </header>
      <ol class="start-grid">
        ${h.slots.map(s=>`
          <li class="${s.known?'':'missing'}">
            <span class="gate">${s.startPos}</span>
            <span class="seed-pill">s${s.seed}</span>
            <span class="who">${esc(s.known?s.name:`Seed ${s.seed}`)}</span>
            <span class="tt">${esc(s.time)}</span>
          </li>
        `).join('')}
      </ol>
    </article>
  `).join('');
  document.querySelector('#heat-grid').classList.toggle('heat-grid--few', board.heats.length<=2);
}

async function refresh(){
  try{
    const f=await get('/api/public/events');
    const allItems=await Promise.all(
      (f.events||[]).map(async e=>({e,r:await get('/api/public/events/'+encodeURIComponent(e.id)+'/results').catch(()=>[])}))
    );
    await BracketProjection.refreshSeeds({eventItems:allItems});
  }catch(_){
    await BracketProjection.refreshSeeds({eventItems:[]});
  }
  paint();
}

refresh();
setInterval(refresh,15000);
