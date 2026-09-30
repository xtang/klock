// Render smoke checks without a browser. Full visual QA still requires a browser.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const elements=new Map(['#app','#modal-root','#toast'].map(id=>[id,{innerHTML:'',textContent:'',classList:{add(){},remove(){}}}]));
let popup='';
const document={querySelector:s=>elements.get(s)||null,querySelectorAll:()=>[],addEventListener(){},title:'',hidden:false};
const context=vm.createContext({document,window:{addEventListener(){},open:()=>({document:{write:s=>popup=s,close(){}}})},URLSearchParams,location:{search:'',hash:'',origin:'http://localhost:5188'},fetch:async()=>({ok:true,json:async()=>({me:null,configured:false})}),setInterval(){},setTimeout(){},clearTimeout(){},console,crypto:globalThis.crypto,Blob,URL,history:{replaceState(){}}});
const code=fs.readFileSync(new URL('../public/app.js',import.meta.url),'utf8');
await vm.runInContext(`(async()=>{${code}\nglobalThis.testUI={renderPage(p){page=p;render();return document.querySelector('#app').innerHTML},getState(){return state},authView(config,query){auth=config;state={me:null,configured:true};location.search=query;demo=false;renderWelcome();return document.querySelector('#app').innerHTML},googleButton,pickerItems,projectPicker,pickerLabel,mutate,printReport,esc};})()`,context);
const ui=context.testUI;
for(const [page,title] of [['timer','把时间，留给重要的事'],['projects','有条理地，投入'],['team','一起，把事情做好'],['reports','每一份投入，都算数']]){assert.ok(ui.renderPage(page).includes(title),page+' renders');}
assert.equal(ui.esc('<script>"&'), '&lt;script&gt;&quot;&amp;');
await ui.mutate('projects',{name:'<img src=x onerror=alert(1)>',client:'test'});
assert.ok(ui.renderPage('projects').includes('&lt;img'));
const s=ui.getState(),p=s.projects.at(-1);
await ui.mutate('timer/start',{project_id:p.id,description:'Focus'});
assert.ok(ui.renderPage('timer').includes('结束计时'));
await ui.mutate('timer/stop',{});
assert.ok(s.entries.some(e=>e.description==='Focus'&&e.seconds>=1));
ui.printReport(s.entries);
assert.ok(popup.includes('月度工时报告'));
assert.ok(popup.includes('&lt;img'));
assert.ok(!popup.includes('<img src=x'));
console.log('UI smoke checks passed: all four pages, escaping, demo timer, printable report.');

// The same project name must remain distinguishable by its client and ID.
s.projects.push({id:'same-a',name:'Website',client:'Acme',color:'#507d69'},
 {id:'same-b',name:'Website',client:'North',color:'#c28b53'},
 {id:'old',name:'Archived site',client:'Acme',archived:1,color:'#507d69'},
 {id:'no-client',name:'Personal',client:'',color:'#507d69'});
assert.deepEqual(Array.from(ui.pickerItems(false,false,'ACME website'),p=>p.id),['same-a']);
assert.deepEqual(Array.from(ui.pickerItems(false,false,'north website'),p=>p.id),['same-b']);
assert.equal(ui.pickerItems(false,false,'Archived').length,0);
assert.equal(ui.pickerItems(true,false,'Archived').length,1);
assert.equal(ui.pickerItems(false,false,'does-not-exist').length,0);
assert.equal(ui.pickerItems(true,true,'')[0].id,'all');
assert.ok(ui.pickerLabel('same-a').includes('Acme'));
assert.ok(ui.pickerLabel('same-b').includes('North'));
assert.ok(ui.pickerLabel('no-client').includes('未关联客户'));
const manual=ui.projectPicker('manual-project','same-b',{name:'project_id'});
assert.ok(manual.includes('name="project_id" value="same-b"'));
assert.ok(manual.includes('aria-haspopup="dialog"'));
const timer=ui.renderPage('timer');
assert.ok(timer.includes('data-picker="timer-project"'));
assert.ok(timer.includes('data-picker="entry-filter"'));
assert.ok(!timer.includes('<select id="timer-project"'));
console.log('Project picker checks passed: client search, duplicate names, archive filtering, selected labels and form values.');

const login=ui.authView({google_enabled:true,google_required:true},'');
assert.ok(login.includes('使用 Google 账号继续'));
assert.ok(login.includes('/api/auth/google/start'));
const invited=ui.authView({google_enabled:true,google_required:true},'?invite=one-time');
assert.ok(invited.includes('invite=one-time'));
assert.ok(!invited.includes('id="join-form"'));
const error=ui.authView({google_enabled:true,google_required:true},'?auth_error=%3Cscript%3E');
assert.ok(error.includes('&lt;script&gt;'));
const disabled=ui.authView({google_enabled:false,google_required:false},'');
assert.ok(disabled.includes('Google 登录待管理员配置'));
console.log('Authentication UI checks passed: login, invite, disabled configuration, escaped error.');
