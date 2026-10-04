const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
let riders=[],returnMode=false;

function chipStatus(r){
  if(returnMode)return r.chipReturned
    ? '<span class="chip-status returned" title="Chip returned">☑</span>'
    : '<span class="chip-status due" title="Chip still to return">☐</span>';
  return '<span class="chip-status assigned" title="Chip assigned">✓</span>';
}
function render(){
  const query=$('#registration-search').value.trim().toLocaleLowerCase();
  const visible=riders.filter(r=>!query||r.name.toLocaleLowerCase().includes(query)||String(r.bib||'').toLocaleLowerCase().includes(query));
  $('#registration-list').innerHTML=visible.length?`<div class="table-scroll"><table class="registration-table"><thead><tr><th>Race #</th><th>Rider</th><th aria-label="${returnMode?'Chip return status':'Chip assigned'}">${returnMode?'Returned':'Chip'}</th></tr></thead><tbody>${visible.map(r=>`<tr class="${returnMode&&!r.chipReturned?'chip-return-due':''}"><td class="registration-bib">${esc(r.bib||'—')}</td><td><a class="registration-name" href="/rider/?id=${encodeURIComponent(r.id)}">${esc(r.name)}</a></td><td>${chipStatus(r)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="empty">No registered rider matches that search.</p>';
  $('#registration-status').textContent=query?`${visible.length} matching rider${visible.length===1?'':'s'}`:`${riders.length} riders shown · chips confirmed from registration`;
}
async function refresh(){
  try{
    const response=await fetch('/api/public/registrations',{cache:'no-store'});
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'Unable to load registration');
    riders=data.riders||[];
    returnMode=Boolean(data.chipReturnMode);
    const remaining=riders.filter(r=>!r.chipReturned).length;
    $('#registration-count').innerHTML=returnMode
      ? `<strong>${riders.length} registered</strong><span>${remaining} chip${remaining===1?'':'s'} to return</span>`
      : `<strong>${riders.length} registered</strong>`;
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
