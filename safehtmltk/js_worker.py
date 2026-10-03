from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from .runtime import activate_vendored_quickjs
except ImportError:
    # Direct execution from the package directory.
    from safehtmltk.runtime import activate_vendored_quickjs

activate_vendored_quickjs()

try:
    import quickjs  # provided by the quickjs-ng distribution
except Exception as exc:
    print(json.dumps({"op": "error", "value": f"Could not import quickjs-ng as quickjs: {exc}"}), flush=True)
    raise SystemExit(2)


BOOTSTRAP = r'''
let nodesById = new Map();
let listeners = new Map();
let timers = new Map();
let pendingFetches = new Map();
let nextTimer = 1;
let nextNode = 1;
let windowListeners = new Map();
let nextFetch = 1;
let currentLocation = "about:blank";

function emit(obj) { __safe_emit(JSON.stringify(obj)); }
function s(x, max=200000) { const v = String(x ?? ""); return v.length > max ? v.slice(0,max) : v; }
function typeName(v) { return String(v || "").toLowerCase(); }
function makeNode(raw={}) {
  const n = {...raw};
  n.id = n.id || "__jsnode_" + nextNode++;
  n.tag = String(n.tag || "div").toLowerCase();
  n.attrs = {...(n.attrs || {})};
  n.htmlId = n.htmlId || n.attrs.id || "";
  n.children = [...(n.children || [])];
  n.parentId = n.parentId || "";
  n.value = n.value === undefined ? (n.attrs.value || "") : n.value;
  n.checked = n.checked === undefined ? !!n.attrs.checked : !!n.checked;
  return n;
}
function parseParts(sel) {
  sel = String(sel || "").replace(/\s*([>+~])\s*/g, " $1 ").trim();
  const out=[]; let buf="", depth=0, quote="";
  for (const ch of sel) {
    if (quote) { buf += ch; if (ch === quote) quote=""; continue; }
    if (ch === "'" || ch === '"') { quote=ch; buf+=ch; }
    else if ("[(".includes(ch)) { depth++; buf+=ch; }
    else if ("])".includes(ch)) { depth--; buf+=ch; }
    else if (/\s/.test(ch) && depth===0) { if(buf){out.push(buf);buf="";} if(out.at(-1)!==" ") out.push(" "); }
    else { buf+=ch; }
  }
  if(buf) out.push(buf);
  while(out[0]===" ") out.shift(); while(out.at(-1)===" ") out.pop();
  return out;
}
function simple(n, selector) {
  selector = selector.trim();
  if (!selector || n.tag === "#text") return false;
  if (selector === "*") return true;
  const tag = selector.match(/^[a-zA-Z][\w-]*/);
  if(tag && n.tag !== tag[0].toLowerCase()) return false;
  const id = selector.match(/#([\w-]+)/);
  if(id && (n.htmlId || "") !== id[1]) return false;
  for(const cls of selector.match(/\.([\w-]+)/g) || []) if(!(n.attrs.class||"").split(/\s+/).includes(cls.slice(1))) return false;
  for(const m of selector.matchAll(/\[\s*([\w:-]+)(?:\s*(=|\^=|\$=|\*=|~=)\s*["']?([^\]"']+)["']?)?\s*\]/g)) {
    const key=m[1].toLowerCase(), op=m[2], wanted=(m[3]||"").trim(), actual=n.attrs[key];
    if(op === undefined) { if(actual === undefined) return false; }
    else if(actual === undefined) return false;
    else if(op === "=" && actual !== wanted) return false;
    else if(op === "^=" && !actual.startsWith(wanted)) return false;
    else if(op === "$=" && !actual.endsWith(wanted)) return false;
    else if(op === "*=" && !actual.includes(wanted)) return false;
    else if(op === "~=" && !actual.split(/\s+/).includes(wanted)) return false;
  }
  for(const pseudo of selector.match(/:([\w-]+)/g) || []) {
    const p=pseudo.slice(1);
    if(p === "checked" && !n.checked) return false;
    if(p === "disabled" && !("disabled" in n.attrs)) return false;
    if(p === "empty" && ((n.children||[]).length || n.text)) return false;
    if(["hover","focus","active","visited","before","after"].includes(p)) return false;
  }
  return true;
}
function matches(n, selector) {
  const parts=parseParts(selector); if(!parts.length) return false;
  let i=parts.length-1;
  if(!simple(n,parts[i])) return false;
  let cur=n; i--;
  while(i>=0) {
    let comb=" ";
    if([">","+","~"," "].includes(parts[i])) { comb=parts[i]; i--; }
    if(i<0) break;
    const want=parts[i];
    if(comb === ">") {
      cur=cur.parentId ? nodesById.get(cur.parentId) : null;
      if(!cur || !simple(cur,want)) return false;
    } else if(comb === " ") {
      let p=cur.parentId ? nodesById.get(cur.parentId) : null, ok=false;
      while(p) { if(simple(p,want)){ok=true;cur=p;break;} p=p.parentId ? nodesById.get(p.parentId) : null; }
      if(!ok) return false;
    } else {
      return false;
    }
    i--;
  }
  return true;
}
function descendants(start) {
  const out=[];
  const walk=(n)=>{ for(const id of n.children||[]){ const c=nodesById.get(id); if(c){out.push(c);walk(c);} } };
  walk(start); return out;
}
function elementText(n) {
  if(!n) return "";
  if(n.tag === "#text") return n.text || "";
  return (n.children||[]).map(id=>nodesById.get(id)).filter(Boolean).map(elementText).join("");
}
function styleApi(n) {
  const read=(name)=>{ const key=String(name).toLowerCase(); const chunks=(n.attrs.style||"").split(";"); for(const c of chunks){const i=c.indexOf(":");if(i<0)continue;if(c.slice(0,i).trim().toLowerCase()===key)return c.slice(i+1).trim();} return ""; };
  const write=(name,val)=>{const key=String(name).toLowerCase();const map={};for(const c of(n.attrs.style||"").split(";")){const i=c.indexOf(":");if(i<0)continue;map[c.slice(0,i).trim().toLowerCase()]=c.slice(i+1).trim();}map[key]=String(val);n.attrs.style=Object.entries(map).map(([k,v])=>`${k}: ${v}`).join("; ");emit({op:"set_style",id:n.id,name:key,value:String(val)});};
  const remove=(name)=>{const key=String(name).toLowerCase();const map={};for(const c of(n.attrs.style||"").split(";")){const i=c.indexOf(":");if(i<0)continue;map[c.slice(0,i).trim().toLowerCase()]=c.slice(i+1).trim();}delete map[key];n.attrs.style=Object.entries(map).map(([k,v])=>`${k}: ${v}`).join("; ");emit({op:"set_attr",id:n.id,name:"style",value:n.attrs.style});};
  const api={setProperty:write,getPropertyValue:read,removeProperty:remove,get cssText(){return n.attrs.style||""},set cssText(v){n.attrs.style=s(v,100000);emit({op:"set_attr",id:n.id,name:"style",value:n.attrs.style})}};
  for(const prop of ["color","backgroundColor","fontSize","fontWeight","fontFamily","fontStyle","lineHeight","textAlign","textDecoration","margin","padding","width","height","display","flexDirection","flexWrap","justifyContent","alignItems","gap","border","borderRadius","overflow","boxSizing","cursor"])
    Object.defineProperty(api,prop,{get:()=>read(prop.replace(/[A-Z]/g,m=>"-"+m.toLowerCase())),set:v=>write(prop.replace(/[A-Z]/g,m=>"-"+m.toLowerCase()),v)});
  return api;
}
function eventObj(type,targetId,extra={}) {
  return {type,target:nodeApi(nodesById.get(targetId)),currentTarget:null,defaultPrevented:false,preventDefault(){this.defaultPrevented=true},stopPropagation(){this.__stop=true},...extra};
}
function nodeApi(n) {
  if(!n) return null;
  if(n.__api) return n.__api;
  const api={};
  Object.defineProperties(api,{
    id:{get:()=>n.htmlId||n.attrs.id||"",set:v=>api.setAttribute("id",v)},
    tagName:{get:()=>n.tag.toUpperCase()},
    parentElement:{get:()=>n.parentId?nodeApi(nodesById.get(n.parentId)):null},
    firstElementChild:{get:()=>{for(const id of n.children||[]){const c=nodesById.get(id);if(c&&c.tag!=="#text")return nodeApi(c)}return null}},
    nextElementSibling:{get:()=>{const p=n.parentId?nodesById.get(n.parentId):null;if(!p)return null;let seen=false;for(const id of p.children||[]){const c=nodesById.get(id);if(c===n){seen=true;continue;}if(seen&&c.tag!=="#text")return nodeApi(c)}return null}},
    children:{get:()=> (n.children||[]).map(id=>nodesById.get(id)).filter(c=>c&&c.tag!=="#text").map(nodeApi)},
    textContent:{get:()=>elementText(n),set:v=>{const value=s(v);n.children=[];n.text=value;emit({op:"set_text",id:n.id,value});}},
    innerText:{get:()=>elementText(n),set:v=>{api.textContent=v}},
    value:{get:()=>n.value??n.attrs.value??"",set:v=>{n.value=s(v);n.attrs.value=n.value;emit({op:"set_value",id:n.id,value:n.value})}},
    checked:{get:()=>!!n.checked,set:v=>{n.checked=!!v;if(n.checked)n.attrs.checked="checked";else delete n.attrs.checked;emit({op:"set_checked",id:n.id,value:n.checked})}},
    disabled:{get:()=>"disabled" in n.attrs,set:v=>{if(v)n.attrs.disabled="";else delete n.attrs.disabled;emit({op:v?"set_attr":"remove_attr",id:n.id,name:"disabled",...(v?{value:""}:{})})}},
    className:{get:()=>n.attrs.class||"",set:v=>{n.attrs.class=s(v);emit({op:"set_attr",id:n.id,name:"class",value:n.attrs.class})}},
    style:{get:()=>styleApi(n)},
    outerHTML:{get:()=>`<${n.tag}${Object.entries(n.attrs).map(([k,v])=>` ${k}${v?`="${String(v).replaceAll('"','&quot;')}"`:""}`).join("")}>${elementText(n)}</${n.tag}>`},
    innerHTML:{get:()=>elementText(n),set:v=>{n.children=[];n.text="";emit({op:"set_html",id:n.id,value:s(v,500000)})}},
  });
  api.getAttribute=name=>{const k=String(name).toLowerCase();return n.attrs[k]??null};
  api.hasAttribute=name=>Object.prototype.hasOwnProperty.call(n.attrs,String(name).toLowerCase());
  api.setAttribute=(name,val)=>{const k=String(name).toLowerCase(),v=s(val,100000);n.attrs[k]=v;if(k==="id")n.htmlId=v;emit({op:"set_attr",id:n.id,name:k,value:v})};
  api.removeAttribute=name=>{const k=String(name).toLowerCase();delete n.attrs[k];emit({op:"remove_attr",id:n.id,name:k})};
  api.addEventListener=(type,fn)=>{const key=n.id+"::"+typeName(type);if(!listeners.has(key))listeners.set(key,[]);listeners.get(key).push(fn)};
  api.removeEventListener=(type,fn)=>{const key=n.id+"::"+typeName(type);listeners.set(key,(listeners.get(key)||[]).filter(x=>x!==fn))};
  api.dispatchEvent=ev=>{dispatch({type:typeName(ev.type),target:n.id,value:ev.value,key:ev.key});return true};
  api.focus=()=>emit({op:"focus",id:n.id});
  api.click=()=>emit({op:"click",id:n.id});
  api.remove=()=>{const p=n.parentId?nodesById.get(n.parentId):null;if(p)p.children=(p.children||[]).filter(x=>x!==n.id);n.parentId="";emit({op:"remove_node",id:n.id})};
  api.appendChild=child=>{const cn=child&&child.__node?child.__node:(child&&child.id?nodesById.get(child.id):null);if(!cn)return child;if(!cn.id)cn.id="__jsnode_"+(nextNode++);nodesById.set(cn.id,cn);const old=cn.parentId?nodesById.get(cn.parentId):null;if(old)old.children=(old.children||[]).filter(x=>x!==cn.id);cn.parentId=n.id;if(!(n.children||[]).includes(cn.id))n.children.push(cn.id);emit({op:"insert_node",parentId:n.id,node:{id:cn.id,tag:cn.tag,attrs:cn.attrs,text:elementText(cn)}});return nodeApi(cn)};
  api.querySelector=sel=>{for(const c of descendants(n))if(matches(c,sel))return nodeApi(c);return null};
  api.querySelectorAll=sel=>descendants(n).filter(c=>matches(c,sel)).map(nodeApi);
  api.insertAdjacentHTML=(where,html)=>{if(typeName(where)!=="beforeend")throw new Error("Only beforeend is supported");emit({op:"insert_html",parentId:n.id,value:s(html,500000)})};
  const cl={add:(...names)=>{const set=new Set((n.attrs.class||"").split(/\s+/).filter(Boolean));for(const x of names)set.add(String(x));api.className=[...set].join(" ")},remove:(...names)=>{const ban=new Set(names.map(String));api.className=(n.attrs.class||"").split(/\s+/).filter(Boolean).filter(x=>!ban.has(x)).join(" ")},contains:name=>(n.attrs.class||"").split(/\s+/).includes(String(name)),toggle:(name,force)=>{const has=cl.contains(name),want=force===undefined?!has:!!force;if(want&&!has)cl.add(name);if(!want&&has)cl.remove(name);return want}};
  Object.defineProperty(api,"classList",{get:()=>cl});
  Object.defineProperty(api,"__node",{value:n});
  n.__api=api;return api;
}
function build(snapshot){
  nodesById=new Map();
  for(const raw of snapshot){const n=makeNode(raw);nodesById.set(n.id,n);}
  for(const n of nodesById.values())if(n.parentId&&nodesById.has(n.parentId)){const p=nodesById.get(n.parentId);if(!(p.children||[]).includes(n.id))p.children.push(n.id)}
}
function dispatch(event){
  const target=nodesById.get(event.target); if(!target)return {defaultPrevented:false};
  let cur=target; const path=[];
  while(cur){path.push(cur);cur=cur.parentId?nodesById.get(cur.parentId):null}
  let prevented=false;
  for(const n of path){const arr=(listeners.get(n.id+"::"+typeName(event.type))||[]).slice();if(!arr.length)continue;const ev=eventObj(typeName(event.type),target.id,{value:event.value??target.value??"",checked:event.checked??target.checked??false,key:event.key||""});ev.currentTarget=nodeApi(n);for(const fn of arr){try{fn(ev)}catch(e){emit({op:"error",value:String(e&&e.stack||e)})}}prevented=prevented||ev.defaultPrevented;if(ev.__stop)break}
  return {defaultPrevented:prevented};
}
function install(snapshot){
  build(snapshot);
  const documentApi={
    getElementById:id=>{const target=[...nodesById.values()].find(n=>(n.htmlId||n.attrs.id||"")===String(id));return nodeApi(target)},
    querySelector:sel=>{for(const n of nodesById.values())if(n.tag!=="#text"&&matches(n,sel))return nodeApi(n);return null},
    querySelectorAll:sel=>[...nodesById.values()].filter(n=>n.tag!=="#text"&&matches(n,sel)).map(nodeApi),
    createElement:tag=>{const n=makeNode({id:"__jsnode_"+(nextNode++),tag:String(tag),attrs:{},children:[],text:"",htmlId:""});nodesById.set(n.id,n);return nodeApi(n)},
    createTextNode:text=>{const n=makeNode({id:"__jsnode_"+(nextNode++),tag:"#text",attrs:{},children:[],text:s(text),htmlId:""});nodesById.set(n.id,n);return nodeApi(n)},
    addEventListener:(type,fn)=>{const key="__document__::"+typeName(type);if(!listeners.has(key))listeners.set(key,[]);listeners.get(key).push(fn)},
  };
  Object.defineProperty(documentApi,"readyState",{get:()=>"complete"});
  Object.defineProperty(documentApi,"title",{get:()=>{const t=[...nodesById.values()].find(n=>n.tag==="title");return t?elementText(t):""},set:v=>{const t=[...nodesById.values()].find(n=>n.tag==="title");if(t){t.text=s(v);t.children=[];emit({op:"set_text",id:t.id,value:s(v)})}}});
  Object.defineProperty(documentApi,"body",{get:()=>{for(const n of nodesById.values())if(n.tag==="body")return nodeApi(n);return null}});
  Object.defineProperty(documentApi,"documentElement",{get:()=>{for(const n of nodesById.values())if(n.tag==="html")return nodeApi(n);return null}});
  globalThis.document=documentApi;
  globalThis.window=globalThis;
  globalThis.globalThis=globalThis;

  const locParts = () => {
    const raw=String(currentLocation||"about:blank");
    try {
      const m=raw.match(/^([a-zA-Z][\w+.-]*):\/\/([^\/\?#]*)([^\?#]*)?(\?[^#]*)?(#.*)?$/);
      if(!m) return {href:raw,origin:"null",protocol:"",host:"",hostname:"",port:"",pathname:raw,search:"",hash:""};
      const authority=m[2]; const hostOnly=authority.replace(/:([0-9]+)$/,""), portM=authority.match(/:([0-9]+)$/);
      return {href:raw,origin:m[1].toLowerCase()+"://"+authority.toLowerCase(),protocol:m[1].toLowerCase()+":",host:authority,hostname:hostOnly,port:portM?portM[1]:"",pathname:m[3]||"/",search:m[4]||"",hash:m[5]||""};
    } catch(_) { return {href:raw,origin:"null",protocol:"",host:"",hostname:"",port:"",pathname:raw,search:"",hash:""}; }
  };
  const locationApi={
    assign:url=>emit({op:"navigate",url:s(url,20000),replace:false}),
    replace:url=>emit({op:"navigate",url:s(url,20000),replace:true}),
    reload:()=>emit({op:"reload"}),
    toString:()=>locParts().href,
  };
  for(const key of ["origin","protocol","host","hostname","port","pathname","search","hash"]) Object.defineProperty(locationApi,key,{get:()=>locParts()[key]});
  Object.defineProperty(locationApi,"href",{get:()=>locParts().href,set:v=>emit({op:"navigate",url:s(v,20000),replace:false}),configurable:false});
  globalThis.location=locationApi;
  windowListeners = new Map();
  globalThis.addEventListener=(type,fn)=>{const k="__window__::"+typeName(type);if(!windowListeners.has(k))windowListeners.set(k,[]);windowListeners.get(k).push(fn)};
  globalThis.removeEventListener=(type,fn)=>{const k="__window__::"+typeName(type);windowListeners.set(k,(windowListeners.get(k)||[]).filter(x=>x!==fn))};
  globalThis.alert=v=>emit({op:"alert",value:s(v,200000)});
  const con={log:(...a)=>emit({op:"console",level:"log",value:a.map(x=>String(x)).join(" ").slice(0,20000)}),info:(...a)=>emit({op:"console",level:"info",value:a.map(x=>String(x)).join(" ").slice(0,20000)}),warn:(...a)=>emit({op:"console",level:"warn",value:a.map(x=>String(x)).join(" ").slice(0,20000)}),error:(...a)=>emit({op:"console",level:"error",value:a.map(x=>String(x)).join(" ").slice(0,20000)})};
  globalThis.console=con;
  globalThis.setTimeout=(fn,ms=0)=>{const id=nextTimer++;timers.set(id,{fn,interval:false});emit({op:"set_timeout",timerId:id,ms:Math.max(0,Math.min(86400000,Number(ms)||0))});return id};
  globalThis.clearTimeout=id=>{timers.delete(Number(id));emit({op:"clear_timeout",timerId:Number(id)})};
  globalThis.setInterval=(fn,ms=0)=>{const id=nextTimer++;timers.set(id,{fn,interval:true});emit({op:"set_interval",timerId:id,ms:Math.max(1,Math.min(86400000,Number(ms)||1))});return id};
  globalThis.clearInterval=id=>{timers.delete(Number(id));emit({op:"clear_interval",timerId:Number(id)})};

  class Headers {
    constructor(init={}){this.map=new Map(); if(init instanceof Headers){init.forEach((v,k)=>this.set(k,v));}else if(Array.isArray(init)){for(const [k,v] of init)this.set(k,v)}else{for(const [k,v] of Object.entries(init||{}))this.set(k,v)}}
    get(k){return this.map.get(String(k).toLowerCase())??null} has(k){return this.map.has(String(k).toLowerCase())}
    set(k,v){this.map.set(String(k).toLowerCase(),String(v));return this} append(k,v){const key=String(k).toLowerCase();this.map.set(key,this.map.has(key)?this.map.get(key)+", "+String(v):String(v));return this}
    delete(k){this.map.delete(String(k).toLowerCase())} forEach(fn){for(const [k,v] of this.map)fn(v,k,this)} entries(){return this.map.entries()} keys(){return this.map.keys()} values(){return this.map.values()}
  }
  globalThis.Headers=Headers;
  class Response {
    constructor(raw){this._body=String(raw.body||"");this._used=false;this.status=Number(raw.status||0);this.statusText=String(raw.statusText||"");this.ok=this.status>=200&&this.status<300;this.url=String(raw.url||currentLocation);this.headers=new Headers(raw.headers||{});this.redirected=!!raw.redirected;this.type=raw.type||"basic"}
    text(){this._used=true;return Promise.resolve(this._body)}
    json(){this._used=true;return Promise.resolve().then(()=>JSON.parse(this._body))}
    clone(){if(this._used)throw new TypeError("Body has already been used");return new Response({body:this._body,status:this.status,statusText:this.statusText,url:this.url,headers:Object.fromEntries(this.headers.entries()),redirected:this.redirected,type:this.type})}
  }
  globalThis.Response=Response;
  globalThis.fetch=(input,init={})=>{
    const requestId=nextFetch++;
    const url=typeof input === "string" ? input : (input && input.url) ? input.url : String(input);
    const method=String((init&&init.method)||"GET").toUpperCase();
    let headers=(init&&init.headers) || {};
    if(headers instanceof Headers) headers=Object.fromEntries(headers.entries());
    else if(Array.isArray(headers)) headers=Object.fromEntries(headers);
    else headers={...headers};
    let body=(init&&init.body);
    if(body !== undefined && body !== null && typeof body !== "string") body=String(body);
    const mode=String((init&&init.mode)||"cors").toLowerCase();
    const credentials=String((init&&init.credentials)||"omit").toLowerCase();
    const referrer=String((init&&init.referrer)||currentLocation);
    return new Promise((resolve,reject)=>{
      pendingFetches.set(requestId,{resolve,reject});
      emit({op:"network_request",requestId,url:s(url,20000),method,headers,body:body===undefined?null:s(body,524288),mode,credentials,referrer,origin:locParts().origin});
    });
  };
  globalThis.fetch = globalThis.fetch;
  globalThis.XMLHttpRequest=undefined;globalThis.WebSocket=undefined;globalThis.require=undefined;globalThis.process=undefined;globalThis.Bun=undefined;globalThis.Deno=undefined;
}
function run(source){
  listeners=new Map();timers=new Map();pendingFetches=new Map();windowListeners=new Map();nextTimer=1;nextFetch=1;build(currentSnapshot);install(currentSnapshot);
  try{
    (0,eval)(source||"");
    const ready=listeners.get("__document__::domcontentloaded")||[];
    for(const fn of ready.slice())try{fn(eventObj("DOMContentLoaded",""))}catch(e){emit({op:"error",value:String(e&&e.stack||e)})}
    if(typeof windowListeners!=="undefined"){const loaded=windowListeners.get("__window__::load")||[];for(const fn of loaded.slice())try{fn(eventObj("load",""))}catch(e){emit({op:"error",value:String(e&&e.stack||e)})}}
    drainJobs();emit({op:"ready"})
  }catch(e){emit({op:"error",value:String(e&&e.stack||e)})}
}
function deliverNetworkResponse(msg){
  const item=pendingFetches.get(Number(msg.fetchRequestId)); if(!item)return;
  pendingFetches.delete(Number(msg.fetchRequestId));
  if(msg.error){item.reject(new TypeError(String(msg.error)));return;}
  item.resolve(new Response(msg.response||{}));
}
function drainJobs(){
  if(typeof __execute_pending_job !== "function") return;
  for(let i=0;i<1000;i++){let more=false;try{more=__execute_pending_job()}catch(e){emit({op:"error",value:String(e&&e.stack||e)});break}if(!more)break;}
}
function eventLoop(msg){
  try {
    if(msg.op==="event"){
      const result=dispatch(msg.event||{});
      drainJobs();
      emit({op:"done",requestId:msg.requestId,defaultPrevented:!!result.defaultPrevented});
    } else if(msg.op==="timer"){
      const t=timers.get(Number(msg.timerId));
      if(t){if(!t.interval)timers.delete(Number(msg.timerId));try{t.fn()}catch(e){emit({op:"error",value:String(e&&e.stack||e)})}}
      drainJobs();
      emit({op:"done",requestId:msg.requestId,defaultPrevented:false});
    } else if(msg.op==="network_response"){
      deliverNetworkResponse(msg);
      drainJobs();
      if(msg.requestId !== undefined) emit({op:"done",requestId:Number(msg.requestId)});
    }
  } catch(e) {
    emit({op:"error",value:String(e&&e.stack||e)});
    if(msg.requestId !== undefined) emit({op:"done",requestId:Number(msg.requestId)});
  }
}
let currentSnapshot=[];
'''


def main() -> int:
    def emit_line(payload: str) -> None:
        print(payload, flush=True)

    ctx = quickjs.Context()
    try:
        raw_memory = int(sys.argv[1]) if len(sys.argv) > 1 else 128
        ctx.set_memory_limit(raw_memory * 1024 * 1024)
        ctx.set_max_stack_size(512 * 1024)
    except Exception:
        pass

    ctx.add_callable("__safe_emit", emit_line)
    def execute_pending_job() -> bool:
        fn = getattr(ctx, "execute_pending_job", None)
        if not callable(fn):
            return False
        return bool(fn())
    ctx.add_callable("__execute_pending_job", execute_pending_job)
    ctx.eval(BOOTSTRAP)

    for raw in sys.stdin:
        raw = raw.rstrip("\n")
        if not raw:
            continue
        try:
            msg = json.loads(raw)
            op = msg.get("op")
            if op == "load":
                current = msg.get("snapshot") or []
                source = msg.get("source") or ""
                location = msg.get("location") or "about:blank"
                ctx.eval("currentSnapshot = " + json.dumps(current, separators=(",", ":")))
                ctx.eval("currentLocation = " + json.dumps(location))
                ctx.eval("run(" + json.dumps(source) + ")")
            elif op in {"event", "timer", "network_response"}:
                ctx.eval("eventLoop(" + json.dumps(msg, separators=(",", ":")) + ")")
        except Exception as exc:
            emit_line(json.dumps({"op": "error", "value": "".join(traceback.format_exception_only(type(exc), exc)).strip()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
