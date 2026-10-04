const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const compactName=value=>{const parts=String(value||'').trim().split(/\s+/);return parts.length>1?`${parts[0]} ${parts.at(-1)[0]}.`:parts[0]||'—'};
let outstanding=[];
function fitGrid(){
  const grid=$('#chip-display-grid'),count=outstanding.length;
  if(!count)return;
  const width=window.innerWidth;
  const columns=width>=1800?(count>42?6:5):width>=1300?(count>32?5:4):width>=900?(count>20?4:3):2;
  const rows=Math.ceil(count/columns);
  grid.style.setProperty('--chip-columns',columns);
  grid.style.setProperty('--chip-rows',rows);
  requestAnimationFrame(()=>{
    const height=Math.max(120,window.innerHeight-grid.getBoundingClientRect().top-24);
    const rowHeight=(height-(rows-1)*14)/rows;
    const density=rowHeight<72?'tight':rowHeight<105?'compact':'normal';
    grid.dataset.density=density;
    grid.querySelectorAll('.chip-display-name').forEach(name=>name.textContent=name.dataset.short);
    grid.style.setProperty('--chip-grid-height',height+'px');
  });
}
async function refresh(){
  try{
    const response=await fetch('/api/public/registrations',{cache:'no-store'});
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'Unable to load chip returns');
    outstanding=(data.riders||[]).filter(rider=>!rider.chipReturned).sort((a,b)=>Number(a.bib||9999)-Number(b.bib||9999)||String(a.name).localeCompare(String(b.name)));
    $('#chip-display-count').textContent=`${outstanding.length} chip${outstanding.length===1?'':'s'} outstanding`;
    $('#chip-display-grid').innerHTML=outstanding.length
      ? outstanding.map(rider=>`<article class="chip-display-rider"><b>${esc(rider.bib||'—')}</b><span class="chip-display-name" data-short="${esc(compactName(rider.name))}">${esc(compactName(rider.name))}</span></article>`).join('')
      : '<section class="chip-display-clear"><strong>All chips returned</strong><span>Thank you!</span></section>';
    fitGrid();
  }catch{
    $('#chip-display-count').textContent='Unavailable';
    $('#chip-display-grid').innerHTML='<p class="chip-display-error">Chip return data is temporarily unavailable.</p>';
  }
}
window.addEventListener('resize',fitGrid);refresh();setInterval(refresh,15000);
