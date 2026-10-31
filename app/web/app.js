'use strict';
const $ = id => document.getElementById(id);
const agentNames = {identity:'Identity and integrity',scale_team:'Team and workplace',funding:'Funding and financials',traction:'Traction and hiring'};
const phases = {QUEUED:'Waiting to start',INGEST:'Reading your pitch deck',RESOLVE:'Finding the company',ROUND1:'Specialists are checking the claims',CHALLENGE:'Specialists are comparing their findings',REPLY:'Resolving review questions',ADJUDICATE:'The lead reviewer is reviewing the evidence',SCORING:'Preparing the final report',DONE:'Review complete',FAILED:'Review stopped',CANNOT_ASSESS:'More company information is needed'};
const statuses = {VERIFIED:'Supported',PARTIAL:'Partly supported',CONTRADICTED:'Contradicted by available data',UNVERIFIABLE:'Not independently verified'};
const gates = {arr_ratio_over_3:"The revenue claim is more than three times the provider's upper revenue estimate.",funding_round_absent:'The claimed funding round is absent from the available funding history.',founder_not_found:'A named founder could not be matched to available founder records.',entity_not_active:'Available records show the company is no longer active.'};
const reasons = {'Confirmed hard gate forces PASS.':'A confirmed exclusion rule requires a do-not-invest decision, regardless of the numeric score.','No verifiable claims: no investment sizing.':'There are no verifiable claims to support an investment amount.','Missing agents contribute zero; weights are not redistributed.':'Missing specialist results contribute zero to the score.','Severity-5 finding caps the verdict.':'A critical concern prevents an unconditional investment recommendation.','Must obtain founder EBITDA/profit documentation.':'Profit documentation is still required before an unconditional investment recommendation.'};
let token = '', session = new AbortController(), busy = false, savedReport = null, activeRun = '';
const pretty = value => String(value ?? 'Not supplied').replaceAll('_',' ');
const num = value => value == null ? 'Not available' : new Intl.NumberFormat('en-US',{maximumFractionDigits:1}).format(value);
const dollars = value => value == null ? 'Not available' : new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(value);
const date = value => {if(!value)return 'Not supplied by provider'; const parsed=new Date(value);return Number.isNaN(parsed.getTime()) ? 'Date unavailable' : parsed.toLocaleString(undefined,{timeZoneName:'short'});};
function node(tag, content, className){const el=document.createElement(tag);if(content!=null)el.textContent=content;if(className)el.className=className;return el;}
function error(message){$('error').textContent=message;$('error').hidden=!message;}
function setBusy(value){busy=value;$('upload-fields').disabled=value;$('resume-button').disabled=value;}
function signOut(){session.abort();session=new AbortController();token='';savedReport=null;activeRun='';setBusy(false);$('workspace').hidden=true;$('login-panel').hidden=false;$('signout').hidden=true;$('result').hidden=true;$('progress-panel').hidden=true;$('report-content').replaceChildren();$('run-id').value='';$('password').value='';error('');}
async function request(path, options={}){
  const controller=new AbortController(), signal=session.signal;
  const cancel=()=>controller.abort();signal.addEventListener('abort',cancel,{once:true});
  if(signal.aborted)controller.abort();
  const timeout=setTimeout(cancel,30000);
  try{
    const headers=new Headers(options.headers);
    if(token)headers.set('Authorization','Bearer '+token);
    const response=await fetch(path,{...options,headers,signal:controller.signal,cache:'no-store',redirect:'error'});
    if(!response.ok){
      let message='Request failed. Please try again.';
      try{message=(await response.json()).error?.message||message;}catch{}
      if(response.status===401){signOut();throw new Error('Please sign in again. An existing run can be reopened with its run ID.');}
      throw new Error(message+' (HTTP '+response.status+')');
    }
    return response.headers.get('content-type')?.includes('json') ? await response.json() : await response.text();
  }catch(e){
    if(e.name==='AbortError'&&!signal.aborted)throw new Error('The request timed out. If a run ID is shown, reopen it instead of uploading again.');
    if(e instanceof TypeError)throw new Error('Cannot reach Leo. Check that the server is running, then reopen the run ID below.');
    throw e;
  }finally{clearTimeout(timeout);signal.removeEventListener('abort',cancel);}
}
function reportView(report){
  savedReport=report;
  const root=$('report-content');root.replaceChildren();$('result').hidden=false;
  root.append(node('p',report.verdict_label,'outcome'));
  if(report.is_stub)root.append(node('p','DEMO / INCOMPLETE: this report includes synthetic or unfinished components.','error'));
  const decision=report.decision;
  if(decision){
    const stats=node('div',null,'stats');
    for(const [title,value] of [['Overall score',num(decision.total_score)+' / 100'],['Claim credibility',decision.credibility==null?'Not available':num(decision.credibility)+' / 100'],['Assessment confidence',num(decision.confidence*100)+'%'],['Policy investment amount',dollars(decision.check_size_usd)]]){
      const stat=node('div',null,'stat');stat.append(node('strong',value),node('span',title));stats.append(stat);
    }
    root.append(stats,node('p','Confidence describes this assessment, not the chance of business success. Scores are displayed rounded; the saved calculation is unchanged.','muted'));
    root.append(node('h3','Why this recommendation'));
    const list=node('ul');
    for(const gate of report.confirmed_gates)list.append(node('li',gates[gate.name]||pretty(gate.name)));
    for(const reason of decision.reasons)list.append(node('li',reasons[reason]||reason));
    root.append(list);
  }else root.append(node('p','No completed investment decision is available for this run.'));
  root.append(node('h3','What the deck says, and what the review found'));
  const sourceIds=new Map(report.evidence.map((e,i)=>[e.evidence_id,i+1]));
  for(const claim of report.claims){
    const card=node('article',null,'claim');card.append(node('h3','Slide '+claim.slide),node('p',claim.text));
    if(!claim.findings.length)card.append(node('p','Not independently verified: no specialist finding was recorded.'));
    for(const finding of claim.findings){
      card.append(node('strong',statuses[finding.status]||pretty(finding.status)));
      const severity=finding.severity_final??finding.severity;
      card.append(node('p',(agentNames[finding.agent]||pretty(finding.agent))+' · Concern level '+severity+'/5'));
      // Source text is displayed as text, never executed as HTML.
      card.append(node('p',finding.note.replace(/\bseries_([a-z])\b/gi,(_,letter)=>'Series '+letter.toUpperCase())));
      const links=node('p');links.append(document.createTextNode('Evidence: '));
      finding.evidence_ids.forEach((id,i)=>{if(i)links.append(document.createTextNode(', '));const index=sourceIds.get(id);if(index){const a=node('a','Source '+index);a.href='#source-'+index;links.append(a);}else links.append(document.createTextNode('Source reference unavailable'));});
      card.append(links);
    }
    root.append(card);
  }
  const sources=node('details');sources.append(node('summary','Evidence and source dates ('+report.evidence.length+' records)'));
  sources.append(node('p','These are saved evidence records, not necessarily independent sources. Retrieval time is when the saved provider response was fetched; it may have been reused from cache. This is separate from the date the provider last updated its records.'));
  report.evidence.forEach((e,i)=>{const card=node('div',null,'source');card.id='source-'+(i+1);card.append(node('strong','Source '+(i+1)+' — '+(e.display_label||pretty(e.field_path))),node('p',e.display_summary||'Details retained in the saved report.'),node('p','Provider: '+(e.source==='crustdata.company.enrich'?'Crustdata':e.source)),node('p','Retrieved: '+(e.provider_fetched_at?date(e.provider_fetched_at):'Not established from saved evidence')),node('p','Source updated: '+date(e.source_updated_at)));sources.append(card);});
  // Open the evidence section when following a source link.
  root.addEventListener('click',event=>{if(event.target.closest('a[href^="#source-"]'))sources.open=true;},{signal:session.signal});
  root.append(sources,node('h3','Limitations and outstanding documents'));
  const limits=node('ul');for(const item of [...report.limitations,...report.must_obtain])limits.append(node('li',item));root.append(limits);
  root.append(node('p','The Markdown download includes the full review discussion and saved scoring breakdown.','muted'));
}
function progress(status){
  $('progress-panel').hidden=false;$('phase').textContent=phases[status.phase]||pretty(status.phase);
  $('run-label').textContent='Run ID: '+activeRun;
  const list=$('agents');list.replaceChildren();
  for(const agent of Object.keys(agentNames)){
    const states=status.agents.filter(s=>s.agent===agent);
    const failed=states.find(s=>s.state==='FAILED');
    const current=states.find(s=>s.phase===status.phase);
    const allDone=['ROUND1','CHALLENGE','REPLY'].every(phase=>states.some(s=>s.phase===phase&&s.state==='DONE'));
    const state=failed?'A phase failed':allDone?'All review phases complete':current?pretty(current.state):'Waiting';
    list.append(node('li',agentNames[agent]+': '+state));
  }
}
async function monitor(id){
  if(!/^[a-zA-Z0-9-]{1,100}$/.test(id))throw new Error('Enter a valid run ID.');
  activeRun=id;$('run-id').value=id;$('run-label').textContent='Run ID: '+id;
  const signal=session.signal;const started=Date.now();$('spinner').hidden=false;$('result').hidden=true;
  while(!signal.aborted){
    const status=await request('/runs/'+encodeURIComponent(id));progress(status);
    if(['done','failed','cannot_assess'].includes(status.status)){
      $('spinner').hidden=true;
      $('progress-note').textContent=status.status==='done'?'Your saved report is ready.':status.status==='cannot_assess'?'Leo could not confidently identify the company. Check the company name and website domain.':'The review stopped before completion. The report lists available findings and limitations.';
      reportView(await request('/runs/'+encodeURIComponent(id)+'/report'));
      return;
    }
    if(Date.now()-started>15*60*1000)throw new Error('This review is still running. Reopen its run ID to check again; no new upload is needed.');
    await new Promise(resolve=>{const finish=()=>{clearTimeout(timer);signal.removeEventListener('abort',finish);resolve();};const timer=setTimeout(finish,1500);signal.addEventListener('abort',finish,{once:true});});
  }
}
async function review(action){if(busy)return;setBusy(true);error('');$('progress-note').textContent='This can take several minutes. Progress comes from the server.';try{await action();}catch(e){if(e.name!=='AbortError')error(e.message);}finally{setBusy(false);$('spinner').hidden=true;}}
$('login').addEventListener('submit',async event=>{
  event.preventDefault();error('');const button=event.submitter;button.disabled=true;
  try{const result=await request('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('username').value,password:$('password').value})});token=result.access_token;$('password').value='';$('login-panel').hidden=true;$('workspace').hidden=false;$('signout').hidden=false;}
  catch(e){if(e.name!=='AbortError')error(e.message);}finally{button.disabled=false;}
});
$('signout').addEventListener('click',signOut);
$('upload').addEventListener('submit',event=>{event.preventDefault();const form=new FormData(event.target);review(async()=>{const file=form.get('deck');if(!file.size||file.size>20*1024*1024||!file.name.toLowerCase().endsWith('.pdf'))throw new Error('Choose a nonempty PDF no larger than 20 MB.');$('progress-panel').hidden=false;$('phase').textContent='Uploading your pitch deck';$('spinner').hidden=false;$('run-label').textContent='';$('agents').replaceChildren();$('result').hidden=true;const response=await request('/runs',{method:'POST',body:form});await monitor(response.run_id);});});
$('resume').addEventListener('submit',event=>{event.preventDefault();review(()=>monitor($('run-id').value.trim()));});
async function download(format){
  if(!savedReport)return;error('');
  try{const data=format==='json'?JSON.stringify(savedReport,null,2):await request('/runs/'+encodeURIComponent(activeRun)+'/report?format=markdown');const url=URL.createObjectURL(new Blob([data],{type:format==='json'?'application/json':'text/markdown;charset=utf-8'}));const a=node('a');a.href=url;a.download='leo-report-'+activeRun+(format==='json'?'.json':'.md');document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){if(e.name!=='AbortError')error(e.message);}
}
$('download-md').addEventListener('click',()=>download('markdown'));
$('download-json').addEventListener('click',()=>download('json'));
request('/health').then(health=>{$('mode').textContent=health.mode==='live'?'Live analysis':'Demo · synthetic data';}).catch(()=>{$('mode').textContent='Server unavailable';error('Cannot reach Leo. Check that the API is running.');});
