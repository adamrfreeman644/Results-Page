const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
async function refresh(){
  try{
    const response=await fetch('/api/public/registrations',{cache:'no-store'});
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'Unable to load chip returns');
    const outstanding=(data.riders||[]).filter(rider=>!rider.chipReturned).sort((a,b)=>Number(a.bib||9999)-Number(b.bib||9999)||String(a.name).localeCompare(String(b.name)));
    $('#chip-display-count').textContent=`${outstanding.length} chip${outstanding.length===1?'':'s'} outstanding`;
    $('#chip-display-grid').innerHTML=outstanding.length
      ? outstanding.map(rider=>`<article class="chip-display-rider"><b>${esc(rider.bib||'—')}</b><span>${esc(rider.name)}</span></article>`).join('')
      : '<section class="chip-display-clear"><strong>All chips returned</strong><span>Thank you!</span></section>';
  }catch{
    $('#chip-display-count').textContent='Unavailable';
    $('#chip-display-grid').innerHTML='<p class="chip-display-error">Chip return data is temporarily unavailable.</p>';
  }
}
refresh();setInterval(refresh,15000);
