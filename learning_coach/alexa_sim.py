"""Simulated Alexa+ screen: a small web page that plays the part of an Alexa+ device talking to this MCP server.

The page listens (microphone where the browser supports it, or typed text), speaks the coach's replies aloud, and
routes every turn through POST /alexa/tool, which calls the real MCP tools over streamable HTTP on /mcp (the same
protocol Alexa+ would use). Nothing here bypasses the MCP layer."""
from __future__ import annotations

import json
import os

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

ALLOWED_TOOLS = {"list_children", "start_session", "next_question", "check_answer", "get_hint", "repeat_question",
                 "end_session", "get_progress"}  # parent-only tools (PIN) stay out of the child's screen


async def _tool(request):
    try:
        body = await request.json()
        name, args = str(body.get("name", "")), body.get("args") or {}
    except Exception:
        return JSONResponse({"error": "bad request"}, status_code=400)
    if name not in ALLOWED_TOOLS or not isinstance(args, dict):
        return JSONResponse({"error": "unknown tool"}, status_code=400)
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    headers = {}
    if os.getenv("ALEXA_SIM_TOKEN"):  # family link token when the server requires auth
        headers["Authorization"] = f"Bearer {os.environ['ALEXA_SIM_TOKEN']}"
    url = f"http://127.0.0.1:{os.getenv('PORT', '8000')}/mcp/"
    async with streamablehttp_client(url, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            res = await session.call_tool(name, {k: str(v) for k, v in args.items()})
    try:
        data = json.loads(res.content[0].text)
    except Exception:
        data = {"say": res.content[0].text if res.content else ""}
    return JSONResponse({"tool": name, "args": args, "result": data})


async def _page(request):
    return HTMLResponse(PAGE)


def make_alexa_app() -> Starlette:
    return Starlette(routes=[Route("/", _page), Route("/tool", _tool, methods=["POST"])])


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alexa+ simulator - Learning Coach</title>
<style>
:root{--bg:#050d14;--panel:#0c1b27;--line:#17364a;--ink:#e9f4fb;--dim:#8fb0c4;--cy:#27c6ff;--cy2:#1a6dff;--ok:#6fe3a8}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(1200px 600px at 20% -10%,#0f2c40,transparent),var(--bg);color:var(--ink);font:16px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px;display:grid;grid-template-columns:380px 1fr;gap:26px;align-items:start}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
.dev{background:linear-gradient(#0e2231,#0a1722);border:1px solid var(--line);border-radius:28px;padding:34px 20px 24px;text-align:center;position:sticky;top:22px}
.echo{width:210px;height:210px;margin:0 auto 22px;border-radius:50%;background:radial-gradient(circle at 50% 35%,#16384f,#07121b 70%);border:1px solid #1d4660;position:relative;display:grid;place-items:center;box-shadow:inset 0 0 40px #000a}
.ring{position:absolute;inset:-9px;border-radius:50%;border:7px solid #0b2a3b;transition:.3s}
.idle .ring{border-color:#0f3550;animation:breathe 4s ease-in-out infinite}
.listen .ring{border-color:transparent;background:conic-gradient(var(--cy),var(--cy2),#fff0,var(--cy));-webkit-mask:radial-gradient(farthest-side,#0000 calc(100% - 8px),#000 calc(100% - 7px));mask:radial-gradient(farthest-side,#0000 calc(100% - 8px),#000 calc(100% - 7px));animation:spin 1.2s linear infinite}
.think .ring{border-color:#1a6dff55;border-top-color:var(--cy);animation:spin .8s linear infinite}
.speak .ring{border-color:var(--cy);box-shadow:0 0 28px var(--cy),inset 0 0 22px #27c6ff66;animation:pulse .9s ease-in-out infinite}
@keyframes spin{to{transform:rotate(1turn)}}@keyframes pulse{50%{transform:scale(1.045);box-shadow:0 0 46px var(--cy),inset 0 0 30px #27c6ff88}}@keyframes breathe{50%{border-color:#1b5b82}}
.bars{display:flex;gap:5px;align-items:center;height:48px}.bars i{width:6px;height:10px;border-radius:3px;background:var(--cy);opacity:.35;transition:.2s}
.speak .bars i{opacity:1;animation:eq .7s ease-in-out infinite}.listen .bars i{opacity:.9;background:#fff;animation:eq .5s ease-in-out infinite}
.bars i:nth-child(2){animation-delay:.1s}.bars i:nth-child(3){animation-delay:.2s}.bars i:nth-child(4){animation-delay:.3s}.bars i:nth-child(5){animation-delay:.15s}
@keyframes eq{50%{height:42px}}
.state{font-size:15px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);min-height:22px}
.brand{font-weight:700;font-size:22px;margin:2px 0 4px}.brand b{color:var(--cy)}
.chat{background:var(--panel);border:1px solid var(--line);border-radius:22px;padding:22px;min-height:520px;max-height:calc(100vh - 150px);overflow:auto}
.msg{display:flex;margin:12px 0;animation:in .25s ease-out}.msg.child{justify-content:flex-end}
.b{max-width:78%;padding:12px 16px;border-radius:18px;font-size:19px}.coach .b{background:#10303f;border:1px solid #1b4a63;border-bottom-left-radius:4px}
.child .b{background:linear-gradient(135deg,var(--cy2),#2196ff);border-bottom-right-radius:4px}
.who{font-size:12px;color:var(--dim);margin:0 6px 3px;letter-spacing:.08em;text-transform:uppercase}
.tool{font:12px ui-monospace,Menlo,monospace;color:#86c5e5;margin:4px 6px 0}
@keyframes in{from{opacity:0;transform:translateY(8px)}}
form{display:flex;gap:10px;margin-top:16px}input{flex:1;padding:15px 18px;border-radius:999px;border:1px solid var(--line);background:#06121b;color:var(--ink);font-size:18px}
button{border:0;border-radius:999px;padding:0 22px;font-size:16px;font-weight:600;cursor:pointer;background:var(--cy);color:#04202e}button.sec{background:#12384e;color:var(--ink)}
.mic{width:54px;padding:0;font-size:22px}.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.chip{background:#0f2736;border:1px solid var(--line);color:var(--ink);font-weight:500;padding:9px 14px}
.note{color:var(--dim);font-size:13px;margin-top:16px}
</style></head><body><div class="wrap">
<div class="dev" id="dev"><div class="brand">alexa<b>+</b> <span style="font-weight:400;color:var(--dim);font-size:15px">simulator</span></div>
<div class="note" style="margin:0 0 22px">Learning Coach, over MCP</div>
<div class="echo"><div class="ring"></div><div class="bars"><i></i><i></i><i></i><i></i><i></i></div></div>
<div class="state" id="state">Say "Alexa"</div>
<div class="chips"><button class="chip" data-say="Alexa, ask Learning Coach to start">Start practising</button><button class="chip sec" id="demo">Play demo</button></div>
<div class="note">Every turn is a real MCP tool call to <code>/mcp</code> (streamable HTTP). Parent tools need the PIN and are not available here.</div></div>
<div><div class="chat" id="chat"></div>
<form id="f"><input id="u" placeholder="Say something to Alexa..." autocomplete="off"><button type="button" class="mic sec" id="mic" title="Hold to talk">&#127908;</button><button>Send</button></form></div>
</div>
<script>
const $=s=>document.querySelector(s), chat=$('#chat'), dev=$('#dev'), stateEl=$('#state'), sleep=ms=>new Promise(r=>setTimeout(r,ms));
const params=new URLSearchParams(location.search), quiet=params.has('quiet'); // quiet: no speech synthesis (video is voiced separately)
let child='', waitingForChild=false, busy=false;
function setState(s,label){dev.className='dev '+s;stateEl.textContent=label}
setState('idle','Say "Alexa"');
function add(who,text,tool){const m=document.createElement('div');m.className='msg '+who;const w=document.createElement('div');
 w.innerHTML='<div class="who" style="'+(who==='child'?'text-align:right':'')+'">'+(who==='child'?'Child':'Alexa+ · Learning Coach')+'</div><div class="b"></div>'+(tool?'<div class="tool">'+tool+'</div>':'');
 w.querySelector('.b').textContent=text;m.appendChild(w);chat.appendChild(m);chat.scrollTop=chat.scrollHeight}
function speak(text){return new Promise(res=>{const ms=Math.max(1100,text.length*58);
 if(!quiet&&'speechSynthesis' in window){try{const u=new SpeechSynthesisUtterance(text);u.rate=1.0;u.onend=()=>res();speechSynthesis.cancel();speechSynthesis.speak(u);setTimeout(res,ms+4000);return}catch(e){}}
 setTimeout(res,ms)})}
async function tool(name,args){const r=await fetch('tool',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name,args})});return r.json()}
const TOPICS={'times tables':'times tables','multiplication':'times tables','abacus':'abacus','fractions':'fractions','spelling':'spelling','countries':'countries','capitals':'countries','scales':'balance','science':'science','reading':'reading'};
function route(t){const l=t.toLowerCase().replace(/[.,!?]/g,'').trim();
 if(waitingForChild){return{name:'start_session',args:{child:t.replace(/^(it'?s|its|this is|i'?m|i am)\s+/i,'').replace(/[.!]/g,'')}}}
 if(/\b(start|begin|let'?s practi[sc]e|ask learning coach)\b/.test(l)&&!/times|abacus|fraction|spell/.test(l))return{name:'start_session',args:{child}};
 for(const k in TOPICS){if(l.includes(k)&&/(let'?s|do|try|practi[sc]e|want|more|next|some)/.test(l))return{name:'next_question',args:{child,topic:TOPICS[k]}}}
 if(/^(next( question)?|another( one)?|yes|yeah|yep|more|ok(ay)?|again please|go on)$/.test(l)||/another|next question/.test(l))return{name:'next_question',args:{child}};
 if(/^(i'?m done|done|stop|finish|that'?s all|all done|i want to stop)$/.test(l))return{name:'end_session',args:{child}};
 return{name:'check_answer',args:{child,answer:t}}}
async function turn(text){if(busy||!text.trim())return;busy=true;add('child',text);
 setState('think','Thinking');const {name,args}=route(text);const pretty=name+'('+Object.entries(args).filter(e=>e[1]).map(e=>e[0]+'='+JSON.stringify(e[1])).join(', ')+')';
 let out;try{out=await tool(name,args)}catch(e){out={result:{say:'Sorry, something went wrong.'}}}
 const r=out.result||{};const say=r.say||r.error||'Sorry, I did not catch that.';
 if(name==='start_session'||name==='next_question'){const kid=(r.child||r.child_name||r.name||'');if(kid&&!r.need_child)child=String(kid).toLowerCase()}
 waitingForChild=!!r.need_child||/who is practising|which child/i.test(say);
 if(name==='start_session'&&!waitingForChild&&!child){child=args.child.toLowerCase()}
 add('coach',say,'MCP '+pretty);setState('speak','Alexa+ is speaking');await speak(say);setState('idle','Say "Alexa"');busy=false;return r}
$('#f').onsubmit=e=>{e.preventDefault();const v=$('#u').value;$('#u').value='';turn(v)};
document.querySelectorAll('[data-say]').forEach(b=>b.onclick=()=>turn(b.dataset.say));
// microphone (where the browser supports speech recognition)
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
if(SR){const rec=new SR();rec.lang='en-US';rec.onresult=e=>turn(e.results[0][0].transcript);rec.onend=()=>{if(!busy)setState('idle','Say "Alexa"')};
 $('#mic').onclick=()=>{try{setState('listen','Listening');rec.start()}catch(e){}}}else{$('#mic').style.display='none'}
// scripted demo: a child talks to Alexa+; answers are worked out from the question the coach just asked
async function typeIn(text){setState('listen','Listening');const el=$('#u');el.focus();for(const ch of text){el.value+=ch;await sleep(38+Math.random()*30)}await sleep(350);el.value='';await turn(text);await sleep(700)}
function solve(say){let m=say.match(/(\d+)\s*times\s*(\d+)/i);if(m)return String(m[1]*m[2]);m=say.match(/slide\s*(\d+)\s*beads/i);if(m)return(['zero','one','to','three','for','five','six','seven','ate','nine','ten'][+m[1]]||m[1]);return null}
async function demo(){if(busy)return;chat.innerHTML='';child='';waitingForChild=false;
 await typeIn('Alexa, ask Learning Coach to start');await typeIn("It's Toby");
 await typeIn("Let's do times tables");let q=chat.querySelectorAll('.coach .b');let last=()=>[...chat.querySelectorAll('.coach .b')].pop().textContent;
 await typeIn('hint please');await typeIn(solve(last())||'55');await typeIn('next question');
 await typeIn("I don't know");await typeIn('another one');await typeIn(solve(last())||'60');
 await typeIn("let's try abacus");await typeIn(solve(last())||'four');await typeIn("I'm done")}
$('#demo').onclick=demo;if(params.has('autoplay'))setTimeout(demo,1200);
</script></body></html>"""
