const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&gt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
let riders=[];

function chipStatus(r){
  if(r.chipReturned)return '<span class="chip-status returned">✓ Returned</span>';
  if(r.chipAssigned)return '<span class="chip-status assigned">Chip assigned</span>';
  return '<span class="chip-status pending">No chip assigned</span>';
}
function render(){
  const query=$('#registration-search').value.trim().toLocaleLowerCase();
  const visible=riders.filter(r=>!query||r.name.toLocaleLowerCase().includes(query)||String(r.bib||'').toLocaleLowerCase().includes(query));
  $('#registration-list').innerHTML=visible.length?`<div class="table-scroll"><table class="registration-table"><thead><tr><th>Race #</th><th>Rider</th><th>Timing chip</th></tr></thead><tbody>${visible.map(r=>`<tr><td class="registration-bib">${esc(r.bib||'—')}</td><td><a class="registration-name" href="/rider/?id=${encodeURIComponent(r.id)}">${esc(r.name)}</a></td><td>${chipStatus(r)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="empty">No registered rider matches that search.</p>';
  $('#registration-status').textContent=query?`${visible.length} matching rider${visible.length===1?'':'s'}`:`${riders.length} riders shown`;
}
async function refresh(){
  try{
    const response=await fetch('/api/public/registrations',{cache:'no-store'});
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'Unable to load registration');
    riders=data.riders||[];
    $('#registration-count').textContent=`${riders.length} registered`;
    render();
  }catch(error){
    $('#registration-count').textContent='Registration unavailable';
    $('#registration-status').textContent='The live registration export is not available yet.';
    $('#registration-list').innerHTML='';
  }
}
$('#registration-search').addEventListener('input',render);
refresh();
setInterval(refresh,30000);
