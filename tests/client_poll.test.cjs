const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function fixture(status,body){
 const elements=new Map(),timers=[];
 const document={getElementById(id){if(!elements.has(id))elements.set(id,{textContent:'',value:'',hidden:false,disabled:false,files:[],addEventListener(){},setAttribute(){},classList:{toggle(){}},replaceChildren(){},append(){}});return elements.get(id);}};
 const context=vm.createContext({document,URL,AbortController,fetch:async()=>({ok:status>=200&&status<300,status,json:async()=>body}),setTimeout(fn,ms){timers.push({fn,ms});return timers.length;},clearTimeout(){},sessionStorage:{getItem(){return null;},setItem(){}},navigator:{},confirm(){return false;},console});
 const source=fs.readFileSync(path.join(__dirname,'../app/static/app.js'),'utf8').replace('\ninitialize();','\n');
 vm.runInContext(source,context);
 return {context,elements,timers};
}
test('expired application login stops polling with an explicit message',async()=>{
 const f=fixture(401,{detail:'Unauthorized'});
 await vm.runInContext('poll("a".repeat(32))',f.context);
 assert.equal(f.timers.length,0);
 assert.match(f.elements.get('error').textContent,/autenticação|login|sessão/i);
});
test('missing or expired task does not retry as a connection interruption',async()=>{
 const f=fixture(404,{detail:'Transcrição não encontrada ou expirada.'});
 await vm.runInContext('poll("a".repeat(32))',f.context);
 assert.equal(f.timers.length,0);
 assert.match(f.elements.get('error').textContent,/expirada/);
});
test('rendering error is not labeled as a network interruption',async()=>{
 const f=fixture(200,{status:'done',result:{}});
 vm.runInContext('showResult=()=>{throw Error("render fixture")}',f.context);
 await vm.runInContext('poll("a".repeat(32))',f.context);
 assert.equal(f.timers.length,0);
 assert.match(f.elements.get('error').textContent,/exibir|interface|resultado/i);
});
test('real task failure shows server diagnostic without retry',async()=>{
 const f=fixture(200,{status:'failed',error:'Desafio antiautomação na VPS',error_code:'youtube_bot_challenge'});
 await vm.runInContext('poll("a".repeat(32))',f.context);
 assert.equal(f.timers.length,0);
 assert.match(f.elements.get('error').textContent,/youtube_bot_challenge/);
});
test('transient server errors retry instead of resubmitting the task',async()=>{
 const f=fixture(503,{detail:'Serviço temporariamente indisponível'});
 await vm.runInContext('poll("a".repeat(32))',f.context);
 assert.equal(f.timers.length,1);
 assert.match(f.elements.get('notice').textContent,/temporariamente/);
});
test('network failure may retry but does not create a new task',async()=>{
 const f=fixture(200,{});
 f.context.fetch=async()=>{throw new TypeError('network fixture')};
 await vm.runInContext('poll("a".repeat(32))',f.context);
 assert.equal(f.timers.length,1);
 assert.match(f.elements.get('notice').textContent,/consultar o servidor/);
});
