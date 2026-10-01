const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
let riders=[];

function render(){
  const query=$('#registration-search').value.trim().toLocaleLowerCase();
  const visible=riders.filter(r=>!query||r.name.toLocaleLowerCase().includes(query)||String(r.bib||'').toLocaleLowerCase().includes(query));
  $('#registration-list').innerHTML=visible.length?`<div class="table-scroll"><table class="registration-table"><thead><tr><th>Number</th><th>Rider</th></tr></thead><tbody>${visible.map(r=>`<tr><td class="registration-bib">${esc(r.bib||'—')}</td><td><a class="registration-name" href="/rider/?id=${encodeURIComponent(r.id)}">${esc(r.name)}</a></td></tr>`).join('')}</tbody></table></div>`:'<p class="empty">No registered rider matches that search.</p>';
  $('#registration-status').textContent=query?`${visible.length} matching rider${visible.length===1?'':'s'}`:'';
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
