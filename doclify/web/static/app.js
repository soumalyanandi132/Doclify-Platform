'use strict';
const $ = (s, root=document) => root.querySelector(s);
const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const titles={overview:'Overview',files:'Source explorer',documents:'Documentation',assistant:'Code assistant',activity:'Activity',settings:'Workspace settings'};
const state={repos:[],repo:null,view:'overview',session:{},file:null,document:null,messages:[],poll:null,busy:false};
let selectionEpoch=0;
const fmt=n=>Number(n||0).toLocaleString();
const timeLabel=s=>s?new Date(s).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'Not indexed';
const badge=(text,kind='')=>`<span class="badge ${kind}">${escape(text)}</span>`;
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,6000)}
async function api(path,options={}){
 const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
 let data;try{data=await response.json()}catch{throw Error('The server returned an unreadable response.')}
 if(!response.ok){if(response.status===401){state.session.authenticated=false;render()}throw Error(typeof data.detail==='string'?data.detail:'Please check your input and try again.')}
 return data;
}
const post=(path,body={})=>api(path,{method:'POST',body:JSON.stringify(body)});
function markdown(text){
 // Render a small safe Markdown subset; repository HTML is never trusted.
 let code=false,buf=[],list=false;
 const inline=s=>escape(s).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
 for(const line of text.split('\n')){
  if(line.startsWith('```')){if(list){buf.push('</ul>');list=false}buf.push(code?'</code></pre>':'<pre><code>');code=!code;continue}
  if(code){buf.push(escape(line)+'\n');continue}
  if(/^[-*] /.test(line)){if(!list){buf.push('<ul>');list=true}buf.push('<li>'+inline(line.slice(2))+'</li>');continue}
  if(list){buf.push('</ul>');list=false}
  const heading=line.match(/^(#{1,3}) (.*)/);
  if(heading)buf.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`);
  else if(line.startsWith('> '))buf.push('<blockquote>'+inline(line.slice(2))+'</blockquote>');
  else if(line.trim())buf.push('<p>'+inline(line)+'</p>');
 }
 if(list)buf.push('</ul>');if(code)buf.push('</code></pre>');return buf.join('');
}
function renderSidebar(){
 $('#repository-list').innerHTML=state.repos.length?state.repos.map(r=>`<button class="${state.repo?.id===r.id?'selected':''}" data-repo="${r.id}"><span class="repo-dot ${r.status}"></span>${escape(r.name.split('/').pop())}${r.is_sample?' <small>sample</small>':''}</button>`).join(''):'<span class="muted">No repositories yet</span>';
 document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===state.view));
 $('#breadcrumb-title').textContent=titles[state.view];
 $('#mode-badge').textContent=state.session.ai_available?'AI connected':'Source workspace';
}
function heading(title,subtitle,buttons=''){return `<div class="page-heading"><div><h1>${title}</h1><p>${subtitle}</p></div><div class="actions">${buttons}</div></div>`}
const connectButton='<button class="button primary" data-action="connect"><span>+</span> Connect repository</button>';
function empty(){return heading('Your code, understood.','A little less searching. A lot more context.',connectButton)+`<section class="empty-workspace"><div><span class="eyebrow">A CLEARER VIEW OF YOUR CODEBASE</span><h2>From a repository<br>to the bigger picture.</h2><p>Explore your source, find the details that matter, and turn them into documentation your team can use.</p><div class="actions"><button class="button primary" data-action="connect">Connect a repository ↗</button><button class="button" data-action="sample">Explore sample</button></div><p class="form-note">Start with a public GitHub repository, or try the included Orbit API sample. Your work is saved automatically.</p></div><div class="empty-art"><span class="eyebrow">REPOSITORY → CONTEXT</span><div class="code-line">▾ orbit-api /</div><div class="code-line">&nbsp; ├ app / main.py</div><div class="code-line">&nbsp; ├ app / auth.py</div><div class="code-line">&nbsp; └ tests / test_service.py</div><div class="code-line">✧ Find it. Understand it. Document it.</div></div></section>`}
function progressPanel(){
 if(!state.repo)return '';
 const job=state.repo.jobs.find(j=>['running','queued'].includes(j.status));
 if(job)return `<div class="panel progress-panel" role="status"><span class="spinner"></span><strong>${escape(job.stage)}</strong><progress max="100" value="${job.progress}"></progress><small>${job.progress}% · working in the background</small></div>`;
 const failed=state.repo.jobs[0];
 if(failed?.status==='failed')return `<div class="error-banner" role="alert">${escape(failed.error)} <button class="text-link" data-action="${failed.kind==='index'?'reindex':'report'}">Retry</button></div>`;
 return '';
}
function repoBanner(){const r=state.repo;return `<section class="panel repo-banner"><div class="repo-logo">⌘</div><div class="repo-main"><h2>${escape(r.name)} ${r.is_sample?badge('Sample','sample'):badge('Public')}${badge(r.status==='ready'?'Indexed':r.status,r.status==='ready'?'success':'warning')}</h2><p>${r.branch?'⑂ '+escape(r.branch)+' <span class="branch">'+escape((r.commit_sha||'').slice(0,8))+'</span>':'Waiting for source snapshot'} · ${fmt(r.files.length)} files in context</p></div><div class="actions">${r.url?`<a class="button small-button" href="${escape(r.url)}" target="_blank" rel="noreferrer">View on GitHub ↗</a>`:''}<button class="button small-button" data-action="reindex">↻ Re-index</button></div></section>`}
function activityRows(limit=5){return state.repo.jobs.slice(0,limit).map(j=>`<div class="activity-row"><span class="activity-mark">${j.status==='completed'?'✓':j.status==='failed'?'!':'◷'}</span><div><strong>${j.kind==='index'?'Repository indexing':'Documentation generation'}</strong><p>${escape(j.stage)}${j.error?' · '+escape(j.error):''}</p></div><time>${timeLabel(j.created_at)}</time></div>`).join('')||'<div class="panel-empty">Your repository activity will appear here.</div>'}
function overview(){const r=state.repo;
 return heading('A little clarity for your codebase.','Everything you need to understand what you’re building.',connectButton)+progressPanel()+repoBanner()+
 `<div class="stats">${[['Indexed files',r.files.length,'Source files in this snapshot','◫'],['Lines of code',r.total_lines,'Across supported text files','≋'],['Code symbols',r.symbol_count,'Python functions and classes','⌘'],['Documents',r.documents.length,'Saved and versioned','▤']].map(([label,n,desc,icon])=>`<section class="panel stat"><div class="stat-top">${label}<span class="stat-icon">${icon}</span></div><div class="stat-value">${fmt(n)}</div><div class="stat-bottom">${desc}</div></section>`).join('')}</div>
 <div class="dashboard-grid"><div class="stack"><section class="panel"><div class="journey"><span class="eyebrow">YOUR NEXT CHAPTER</span><h2>Let the code tell its story.</h2><p>Create a source report from this snapshot, or use AI to turn source context into a readable README.</p><div class="actions"><button class="button primary" data-action="report">▤ Generate source report</button><button class="button ghost" data-view="documents">Open documents →</button></div></div><div class="step-row"><span class="step-icon">⌘</span><div><strong>Find your way around</strong><p>Browse source files and Python symbols.</p></div><button class="text-link" data-view="files">Explore →</button></div><div class="step-row"><span class="step-icon">✧</span><div><strong>Ask a better question</strong><p>${state.session.ai_available?'Get an AI answer with relevant source excerpts.':'Search the source. Connect AI for explanations.'}</p></div><button class="text-link" data-view="assistant">Ask →</button></div></section><section class="panel"><div class="panel-header"><h2>Recent activity</h2><button class="text-link" data-view="activity">View all →</button></div>${activityRows(3)}</section></div>
 <div class="stack"><section class="panel"><div class="panel-header"><h2>Repository composition</h2><span class="muted">◫</span></div><div class="language-list">${Object.entries(r.languages).sort((a,b)=>b[1]-a[1]).map(([l,n])=>`<div class="language"><span>${escape(l)}</span><small>${n} ${n===1?'file':'files'}</small><meter min="0" max="${r.files.length||1}" value="${n}" aria-label="${escape(l)} files"></meter></div>`).join('')||'<p class="muted">Waiting for indexing…</p>'}</div><div class="source-footer"><span>${Object.keys(r.languages).length} languages & formats</span><span>${r.skipped} files skipped</span></div></section><section class="panel"><div class="panel-header"><h2>Latest documents</h2><span class="muted">▤</span></div>${r.documents.slice(0,3).map(d=>`<div class="step-row"><span class="step-icon">▤</span><div><strong>${escape(d.title)}</strong><p>${timeLabel(d.created_at)}</p></div><button class="text-link" data-open-doc="${d.id}">Open →</button></div>`).join('')||'<div class="panel-empty">A fresh start.<br>Generate your first source report to put this repository into words.</div>'}</section></div></div>`;
}
function filesView(){const r=state.repo;return heading('Explore the source.','A file-by-file view of your indexed snapshot.')+progressPanel()+`<section class="panel file-layout"><aside class="file-browser"><label class="skip-label" for="file-search">Find a file</label><input id="file-search" class="search-input" placeholder="Search files…" autocomplete="off"><div class="file-list" id="file-list">${fileButtons(r.files)}</div></aside><div class="file-content" id="file-content">${fileContent()}</div></section>`}
function fileButtons(files){return files.map(f=>`<button class="file-entry ${state.file?.path===f.path?'selected':''}" data-file="${escape(f.path)}"><span>▤</span>${escape(f.path)}</button>`).join('')||'<div class="panel-empty">No files match your search.</div>'}
function fileContent(){const f=state.file;if(!f)return '<div class="panel-empty">Select a file to read its source and symbols.</div>';return `<div class="code-header"><strong>${escape(f.path)}</strong><span>${escape(f.language)} · ${f.lines} lines</span></div><pre class="code-lines">${f.content.split('\n').map((l,i)=>`<span class="code-line-view" id="line-${i+1}">${escape(l)}</span>`).join('')}</pre><div class="symbol-strip">${f.symbols.map(s=>`<button class="symbol-chip" data-line="${s.line}">${escape(s.name)} : ${s.line}</button>`).join('')||'<span class="muted">No Python symbols extracted from this file.</span>'}</div>`}
function documentsView(){return heading('Documentation with a source.','Generate, revisit, and export documents from repository snapshots.',`<button class="button" data-action="report">Generate source report</button><button class="button primary" data-action="ai-doc">✧ Generate AI README</button>`)+progressPanel()+`<section class="panel docs-layout"><aside class="document-list">${state.repo.documents.map(d=>`<button class="document-entry ${state.document?.id===d.id?'selected':''}" data-open-doc="${d.id}"><strong>▤ ${escape(d.title)}</strong><small>${timeLabel(d.created_at)}</small>${d.commit_sha!==state.repo.commit_sha?badge('Older snapshot','warning'):''}</button>`).join('')||'<div class="panel-empty">Your generated documents will be saved here.</div>'}</aside><div class="document-reader">${documentContent()}</div></section>`}
function documentContent(){const d=state.document;if(!d)return `<div class="chat-intro"><div class="assistant-symbol">▤</div><h2>Make your first page.</h2><p>Source reports work without AI. An AI README uses the configured Groq model and supports snapshots up to 20 files.</p><button class="button primary" data-action="report">Generate source report</button></div>`;return `<div class="reader-actions"><span>${d.kind==='ai'?'AI generated':'Source metadata report'} · ${escape(d.commit_sha.slice(0,10))}</span><button class="button small-button" data-action="download">↓ Export Markdown</button></div>${d.commit_sha!==state.repo.commit_sha?'<div class="error-banner">This document describes an older snapshot. Generate a new version to include current changes.</div>':''}<article class="markdown">${markdown(d.markdown)}</article>`}
function messageHTML(m){return `<article class="message"><div class="question">${escape(m.question)}</div><div class="answer"><span class="answer-avatar">✧</span><div class="answer-body"><div class="answer-label">Doclify <small>${m.mode==='ai'?'AI answer':'Source search'}</small></div><div class="answer-text">${escape(m.answer)}</div><div class="sources">${m.sources.map((s,i)=>`<div class="source-card"><button data-source="${escape(s.path)}" data-start="${s.line}"><span>[${i+1}] ${escape(s.path)}:${s.line}–${s.end_line}</span><span>Open ↗</span></button><pre>${escape(s.content)}</pre></div>`).join('')}</div></div></div></article>`}
function assistantView(){return heading('A conversation with your code.','Find answers in the source, with context you can inspect.')+`<div class="chat-layout"><div id="messages">${state.messages.length?state.messages.map(messageHTML).join(''):`<div class="chat-intro"><div class="assistant-symbol">✧</div><h2>What would you like to understand?</h2><p>${state.session.ai_available?'Ask about this repository. Answers use retrieved source excerpts.':'Source search is ready. Add a Groq key on the server to enable AI explanations.'}</p><div class="prompts">${['How does authentication work?','Where are routes defined?','Find the tests in this project','How are tasks created?'].map(p=>`<button class="prompt" data-prompt="${p}">${p} ↗</button>`).join('')}</div></div>`}</div><form id="chat-form" class="chat-form"><label class="skip" for="question">Ask about this repository</label><textarea id="question" placeholder="Ask about ${escape(state.repo.name.split('/').pop())}…" maxlength="2000" required rows="2"></textarea><button type="submit" class="button primary" id="send-chat">Send ↑</button></form><div class="chat-note">${state.session.ai_available?'AI can make mistakes. Check the linked source excerpts.':'Keyword search · no model connected'} · ${escape((state.repo.commit_sha||'').slice(0,8))}</div></div>`}
function activityView(){return heading('The work behind the workspace.','Every import and document generation, in one place.')+progressPanel()+`<section class="panel"><table class="activity-table"><thead><tr><th>Operation</th><th>Status</th><th>Progress</th><th>Started</th></tr></thead><tbody>${state.repo.jobs.map(j=>`<tr><td>${j.kind==='index'?'Index repository':'Generate documentation'}<small>${escape(j.error||j.stage)}</small></td><td>${badge(j.status,j.status==='completed'?'success':j.status==='failed'?'failed':'warning')}</td><td>${j.progress}%</td><td>${timeLabel(j.created_at)}</td></tr>`).join('')}</tbody></table></section>`}
function settingsView(){return heading('Your workspace, configured.','Connection status and the boundaries of this release.')+`<div class="settings-grid"><section class="panel"><div class="panel-header"><h2>AI connection</h2>${badge(state.session.ai_available?'Connected':'Not configured',state.session.ai_available?'success':'warning')}</div><div class="settings-content"><p>Configure <code>GROQ_API_KEY</code> in the server environment to enable AI README generation and code explanations. Keys are never stored in this browser.</p><div class="setting-row"><span>Provider</span><strong>Groq</strong></div><div class="setting-row"><span>Model</span><strong>${escape(state.session.model)}</strong></div><div class="setting-row"><span>Source search</span><strong>Available without AI</strong></div></div></section><section class="panel"><div class="panel-header"><h2>Workspace access</h2>${badge(state.session.password_required?'Password protected':'Local access',state.session.password_required?'success':'warning')}</div><div class="settings-content"><p>This is a single shared workspace. Configure a workspace password and HTTPS before hosting it. Individual accounts and private GitHub repositories are not included in this release.</p><div class="setting-row"><span>Repositories</span><strong>${state.repos.length} / 20</strong></div><div class="setting-row"><span>Indexed text files</span><strong>Up to 300 per repository</strong></div><div class="setting-row"><span>Persistence</span><strong>Server-side SQLite</strong></div>${state.session.password_required?'<button class="button" data-action="logout">Sign out</button>':''}</div></section></div>`}
function render(){
 renderSidebar();
 if(!state.session.authenticated){$('#main').innerHTML=`<section class="panel login"><div class="settings-content"><span class="eyebrow">WELCOME BACK</span><h1>Your workspace awaits.</h1><p>Enter the workspace password to continue.</p><form id="login-form"><label for="password">Workspace password</label><input type="password" id="password" class="search-input" autocomplete="current-password" required><button class="button primary full">Sign in</button></form></div></section>`;return}
 if(state.view==='settings'){$('#main').innerHTML=settingsView();return}
 if(!state.repo){$('#main').innerHTML=empty();return}
 $('#main').innerHTML=({overview,files:filesView,documents:documentsView,assistant:assistantView,activity:activityView}[state.view]||overview)();
}
async function refreshRepos(){state.repos=await api('/api/repositories');renderSidebar()}
async function selectRepo(id){
 const epoch=++selectionEpoch;
 const repo=await api('/api/repositories/'+id);if(epoch!==selectionEpoch)return;
 state.repo=repo;state.file=null;state.document=null;state.messages=[];
 localStorage.setItem('doclify-repository',id);
 if(repo.files.length)state.file=await api('/api/repositories/'+id+'/file?path='+encodeURIComponent(repo.files[0].path));
 if(repo.documents.length)state.document=await api('/api/documents/'+repo.documents[0].id);
 state.messages=await api('/api/repositories/'+id+'/messages');
 if(epoch!==selectionEpoch)return;
 render();startPolling();
}
async function refreshCurrent(){if(!state.repo)return;const id=state.repo.id;const repo=await api('/api/repositories/'+id);if(state.repo?.id!==id)return;state.repo=repo;await refreshRepos();render()}
function startPolling(){
 clearInterval(state.poll);
 if(!state.repo?.jobs.some(j=>['queued','running'].includes(j.status)))return;
 state.poll=setInterval(async()=>{try{
  const old=state.repo.id, oldDocCount=state.repo.documents.length;
  const repo=await api('/api/repositories/'+old);if(state.repo?.id!==old)return;
  state.repo=repo;
  if(!repo.jobs.some(j=>['queued','running'].includes(j.status))){clearInterval(state.poll);await refreshRepos();
   if(repo.documents.length>oldDocCount){state.document=await api('/api/documents/'+repo.documents[0].id);toast('Your document is ready.')}
   if(!state.file&&repo.files.length)state.file=await api('/api/repositories/'+old+'/file?path='+encodeURIComponent(repo.files[0].path));
   if(repo.jobs[0]?.status==='failed')toast(repo.jobs[0].error);
  }
  if(state.view!=='assistant'&&state.view!=='files')render();
 }catch(e){clearInterval(state.poll);toast(e.message)}},1200);
}
function setView(view){if(!titles[view])return;state.view=view;location.hash=view;$('#sidebar').classList.remove('open');render()}
async function openFile(path,line=0){state.file=await api('/api/repositories/'+state.repo.id+'/file?path='+encodeURIComponent(path));setView('files');if(line){const target=$('#line-'+line);target?.classList.add('highlight');target?.scrollIntoView({block:'center'})}}
async function openDocument(id){state.document=await api('/api/documents/'+id);setView('documents')}
async function action(name,button){
 if(name==='connect'){$('#connect-error').textContent='';$('#connect-dialog').showModal();$('#repo-url').focus();return}
 if(name==='download'){const blob=new Blob([state.document.markdown],{type:'text/markdown;charset=utf-8'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=state.repo.name.split('/').pop()+'-'+state.document.kind+'.md';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);return}
 button.disabled=true;
 try{
  if(name==='sample'){const result=await post('/api/sample');await refreshRepos();await selectRepo(result.repository_id)}
  if(name==='reindex'){await post('/api/repositories/'+state.repo.id+'/index');state.file=null;await refreshCurrent();startPolling()}
  if(name==='report'||name==='ai-doc'){await post('/api/repositories/'+state.repo.id+'/documents',{mode:name==='report'?'report':'ai'});await refreshCurrent();setView('documents');startPolling()}
  if(name==='logout'){await post('/api/logout');location.reload()}
 }finally{button.disabled=false}
}
document.addEventListener('click',async event=>{
 const button=event.target.closest('button');if(!button)return;
 try{
  if(button.dataset.view)setView(button.dataset.view);
  else if(button.dataset.repo)await selectRepo(button.dataset.repo);
  else if(button.dataset.action)await action(button.dataset.action,button);
  else if(button.dataset.file)await openFile(button.dataset.file);
  else if(button.dataset.openDoc)await openDocument(button.dataset.openDoc);
  else if(button.dataset.source)await openFile(button.dataset.source,Number(button.dataset.start));
  else if(button.dataset.line){$('#line-'+button.dataset.line)?.scrollIntoView({block:'center'});$('#line-'+button.dataset.line)?.classList.add('highlight')}
  else if(button.dataset.prompt){$('#question').value=button.dataset.prompt;$('#question').focus()}
 }catch(e){toast(e.message)}
});
$('#sidebar-add').onclick=()=>$('#connect-dialog').showModal();
$('#close-dialog').onclick=()=>$('#connect-dialog').close();
$('#menu-toggle').onclick=()=>$('#sidebar').classList.toggle('open');
$('#connect-form').addEventListener('submit',async e=>{
 e.preventDefault();$('#connect-submit').disabled=true;$('#connect-error').textContent='';
 try{const result=await post('/api/repositories',{url:$('#repo-url').value.trim()});$('#connect-dialog').close();$('#repo-url').value='';await refreshRepos();state.view='overview';await selectRepo(result.repository_id)}
 catch(error){$('#connect-error').textContent=error.message}finally{$('#connect-submit').disabled=false}
});
document.addEventListener('input',e=>{if(e.target.id==='file-search'){const q=e.target.value.toLowerCase();$('#file-list').innerHTML=fileButtons(state.repo.files.filter(f=>f.path.toLowerCase().includes(q)))}});
document.addEventListener('submit',async e=>{
 if(e.target.id==='login-form'){e.preventDefault();try{await post('/api/login',{password:$('#password').value});await init()}catch(error){toast(error.message)}}
 if(e.target.id==='chat-form'){
  e.preventDefault();if(state.busy)return;const question=$('#question').value.trim();if(question.length<2)return;state.busy=true;$('#send-chat').disabled=true;$('#send-chat').textContent='Working…';const repoId=state.repo.id;
  try{const message=await post('/api/repositories/'+repoId+'/chat',{question});if(state.repo?.id===repoId){state.messages.push(message);if(state.view==='assistant'){render();$('#question').focus()}}}
  catch(error){toast(error.message)}finally{state.busy=false;if($('#send-chat')){$('#send-chat').disabled=false;$('#send-chat').textContent='Send ↑'}}
 }
});
window.addEventListener('hashchange',()=>{const view=location.hash.slice(1);if(titles[view]&&view!==state.view){state.view=view;render()}});
async function init(){
 try{state.session=await api('/api/session');state.view=titles[location.hash.slice(1)]?location.hash.slice(1):'overview';
  if(!state.session.authenticated){render();return}
  await refreshRepos();const saved=localStorage.getItem('doclify-repository');const target=state.repos.find(r=>r.id===saved)||state.repos[0];
  if(target)await selectRepo(target.id);else render();
 }catch(error){$('#main').innerHTML=heading('Unable to open your workspace.',escape(error.message),'<button class="button" data-action="reload">Retry</button>');toast(error.message)}
}
document.addEventListener('click',e=>{if(e.target.closest('[data-action="reload"]'))location.reload()});
init();
