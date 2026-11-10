"""Page layout, theme and small shared components, built from MonsterUI and rendered to HTML for Flask.

MonsterUI components are FastHTML FT objects; `render` turns a tree of them into an HTML string,
so Flask serves them like any template output. FT escapes every string it is given, so text from
an input file can never become markup.
"""

from fasthtml.common import (A, Body, Button, Div, Footer, Form, Head, Header, Html, Img, Input, Link, Main, Meta,
                             Nav, NotStr, Script, Span, Style, Title, to_xml)
from monsterui.all import Theme

HTMX = "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js"
FONTS = ("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Inter+Tight:wght@600&"
         "family=JetBrains+Mono:wght@400;500&display=swap")

# House style: Inter and Inter Tight, deep navy ink, one solid blue accent on white.
# Red, amber and green appear only as status colors.
CSS = """
:root{--ink:#0e2542;--ink-2:#3a4558;--muted:#5b6678;--faint:#8a93a3;--accent:#1b3d63;--accent-tint:rgba(27,61,99,.10);
--paper:#ffffff;--surface:#f4f6f9;--rule:#dde2ea;--rule-2:#edf0f4;
--bad:#9b1c1c;--bad-bg:#fbeceb;--warn:#7a5200;--warn-bg:#fbf3df;--ok:#1f6b3a;--ok-bg:#e8f3ec}
html,body{background:var(--paper)!important;color:var(--ink)}
body{font-family:'Inter',ui-sans-serif,system-ui,sans-serif;font-size:14px;line-height:1.55;-webkit-font-smoothing:antialiased}
h1,h2,h3,.display{font-family:'Inter Tight','Inter',ui-sans-serif,sans-serif;font-weight:600;letter-spacing:-.02em;color:var(--ink)}
body h1{font-size:32px;line-height:1.2}body h2{font-size:20px;line-height:1.3;letter-spacing:-.01em;margin-bottom:4px}
body h3{font-size:16px;letter-spacing:-.01em}body p+p{margin-top:4px}body .panel>p.muted{margin-bottom:10px}
.num,code,.mono{font-family:'JetBrains Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}
code{font-size:12px;background:var(--surface);padding:1px 5px;border-radius:3px;color:var(--ink-2);word-break:break-all}
a{color:var(--ink)}a.link{text-decoration:underline;text-underline-offset:3px;text-decoration-color:var(--rule)}
a.link:hover{text-decoration-color:var(--accent)}
svg.i{flex-shrink:0;display:inline-block;vertical-align:-3px}
.topbar{background:var(--ink);color:#fff}
.topbar a{color:#fff;text-decoration:none}
.brand{display:flex;align-items:center;gap:12px}.brand img{width:26px;height:26px}
.brand .name{font-family:'Inter Tight',sans-serif;font-size:16px;font-weight:600;letter-spacing:-.01em}
.brand .sep{width:1px;height:16px;background:rgba(255,255,255,.24)}
.brand .app{font-size:14px;font-weight:500;color:rgba(255,255,255,.82)}
.who{display:flex;align-items:center;gap:8px;font-size:14px;color:rgba(255,255,255,.82)}
.signout .btn.ghost{color:rgba(255,255,255,.62);font-size:13px}.signout .btn.ghost:hover{color:#fff}
.wrap{max-width:1344px;margin:0 auto;padding:0 48px}
@media(max-width:700px){.wrap{padding:0 16px}}
.panel{background:var(--paper);border:1px solid var(--rule);border-radius:6px;padding:20px}
.panel+.panel{margin-top:16px}
.eyebrow{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:500}
.muted{color:var(--muted)}.small{font-size:12px}
abbr[title]{text-decoration:underline dotted var(--faint);text-underline-offset:3px;cursor:help}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:500;padding:3px 10px;border-radius:999px;
white-space:nowrap;line-height:1.4}
.pill.bad{color:var(--bad);background:var(--bad-bg)}.pill.warn{color:var(--warn);background:var(--warn-bg)}
.pill.ok{color:var(--ok);background:var(--ok-bg)}.pill.info{color:var(--ink-2);background:var(--surface)}
.pill.final{color:#fff;background:var(--accent)}.pill.accent{color:var(--accent);background:var(--accent-tint)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{background:var(--paper);border:1px solid var(--rule);border-top:3px solid var(--ink);padding:12px 14px;border-radius:4px}
.kpi.bad{border-top-color:var(--bad)}.kpi.warn{border-top-color:#c89a3a}.kpi.ok{border-top-color:var(--ok)}
.kpi .v{font-family:'JetBrains Mono',monospace;font-size:26px;font-weight:500;line-height:1.1}
.kpi .l{font-size:12px;color:var(--ink-2)}
table.t{width:100%;border-collapse:collapse;font-size:14px}
table.t th{text-align:left;font-weight:500;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
border-bottom:2px solid var(--ink);padding:10px 8px}
table.t td{border-bottom:1px solid var(--rule-2);padding:12px 8px;vertical-align:top}
table.t tr:hover td{background:var(--surface)}table.t td.r{text-align:right}
.tabs{display:flex;gap:28px;border-bottom:1px solid var(--rule);margin:20px 0 24px;overflow-x:auto}
.tabs a{padding:12px 0 10px;text-decoration:none;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px;
white-space:nowrap;font-weight:500}
.tabs a:hover{color:var(--ink)}
.tabs a.on{color:var(--ink);font-weight:600;border-bottom-color:var(--accent)}
.tabs a .cnt{font-family:'JetBrains Mono',monospace;font-size:12px;margin-left:6px;color:var(--faint)}
.tabs a.on .cnt{color:var(--accent)}
.split{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:32px;align-items:start}
.aside{position:sticky;top:14px;max-height:calc(100vh - 28px);overflow:auto}
@media(max-width:960px){.split{grid-template-columns:1fr}.aside{position:static;max-height:none}}
.flow{display:flex;align-items:center;gap:6px;margin-top:20px;flex-wrap:wrap}
.flow-step{display:inline-flex;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
.flow-step .n{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--faint)}
.flow-step.done{color:var(--ink);font-weight:500}
.flow-step.now{color:#fff;background:var(--accent);font-weight:600;padding:5px 12px;border-radius:999px}
.flow-step.now .n{color:#fff}
.flow-line{width:40px;height:1px;background:var(--rule)}.flow-line.done{background:var(--accent)}
.sentence{padding:14px 16px;border-bottom:1px solid var(--rule-2);border-left:3px solid transparent}
.sentence:last-child{border-bottom:0}
.sentence.picked,.sentence:has(.picked){background:var(--surface);border-left-color:var(--accent);border-radius:4px}
.sentence .txt{font-size:16px;line-height:25px;color:var(--ink)}
.sentence.ex .txt{text-decoration:line-through;color:var(--faint)}
.actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.actions.ghost{gap:16px;margin-top:10px}
.section{border-top:2px solid var(--ink);padding-top:16px}.section+.section{margin-top:32px}
.section-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px}
.section-src{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--muted);padding:6px 0 4px;word-break:break-all}
.crumbs{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted)}.crumbs a{color:var(--muted);text-decoration:none}
.crumbs a:hover{color:var(--ink)}.crumbs .i{color:var(--faint)}
.ev-head{display:flex;align-items:center;justify-content:space-between}
.ev-quote{background:var(--surface);border-radius:4px;padding:12px 14px;margin:12px 0}
.ev-quote .lbl{font-size:12px;font-weight:500;color:var(--muted);margin-bottom:6px}.ev-quote p{font-size:14px;line-height:22px}
.client{font-size:15px;font-weight:500;color:var(--ink);text-decoration:none}.client:hover{color:var(--accent)}
a.go{display:inline-flex;color:var(--accent)}
.btn{display:inline-flex!important;flex-direction:row;white-space:nowrap;align-items:center;gap:6px;font-size:13px;font-weight:500;padding:5px 10px;border:1px solid var(--rule);
background:var(--paper);color:var(--ink);border-radius:6px;cursor:pointer;text-decoration:none;height:auto;min-height:0;line-height:1.35}
.btn:hover{border-color:var(--ink)}.btn.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn.primary:hover{background:var(--ink);border-color:var(--ink)}.btn.danger{color:var(--bad)}
.btn.picked{border-color:var(--accent);color:var(--accent)}
.btn.ghost{border:0;background:none;padding:2px 0;color:var(--ink-2)}.btn.ghost .i{color:var(--muted)}
.btn.ghost:hover{color:var(--ink)}.btn.ghost.mono{color:var(--accent);font-size:12px}.btn.ghost.mono .i{color:var(--accent)}
.btn.ghost.picked{color:var(--accent)}.btn.ghost.danger,.btn.ghost.danger .i{color:var(--bad)}
.row-actions{display:flex;align-items:center;justify-content:flex-end;gap:16px}
@media(hover:hover){table.t tr .row-actions form{opacity:0;transition:opacity .15s}
table.t tr:hover .row-actions form,table.t tr .row-actions form:focus-within{opacity:1}}
.btn[disabled]{opacity:.45;cursor:not-allowed}
.btn.lg{font-size:14px;padding:9px 16px}
.alert{display:flex;gap:10px;align-items:flex-start;border-left:3px solid var(--accent);background:var(--surface);padding:12px 16px;
border-radius:4px;margin:10px 0}
.alert>.body{flex:1}
.alert.bad{border-color:var(--bad);background:var(--bad-bg)}.alert.warn{border-color:#c89a3a;background:var(--warn-bg)}
.alert.ok{border-color:var(--ok);background:var(--ok-bg)}
.field label{display:block;font-size:12px;font-weight:500;color:var(--ink-2);margin-bottom:4px}
.field input[type=text],.field input[type=password],.field select,.field textarea,input.inp,textarea.inp{width:100%;border:1px solid var(--rule);background:#fff;
padding:8px 10px;border-radius:6px;font:inherit}
.field input:focus,.field select:focus,.inp:focus{outline:2px solid var(--accent-tint);border-color:var(--accent)}
textarea.inp{font-size:15px;line-height:1.5;min-height:80px}
.drop{border:1.5px dashed var(--rule);border-radius:8px;padding:18px;background:var(--surface);transition:border-color .15s,background .15s}
.drop.over{border-color:var(--accent);background:var(--accent-tint)}
.drop .hint{display:flex;align-items:center;gap:8px;font-weight:500;color:var(--ink-2);margin-bottom:12px}
.bar{height:6px;background:var(--rule-2);border-radius:3px;overflow:hidden}.bar>div{height:100%;background:var(--accent);transition:width .4s}
.bar.busy>div{background-image:linear-gradient(90deg,transparent 0,rgba(255,255,255,.45) 50%,transparent 100%);
background-size:60px 100%;background-repeat:no-repeat;animation:sweep 1.2s linear infinite}
@keyframes sweep{from{background-position:-60px 0}to{background-position:calc(100% + 60px) 0}}
.log{font-family:'JetBrains Mono',monospace;font-size:12px;max-height:180px;overflow:auto;background:var(--surface);border:1px solid var(--rule-2);
padding:8px;white-space:pre-wrap}
.ev-line{margin:2px 0}.ev-k{color:var(--muted);font-size:12px}.ev-val{font-weight:600;background:var(--accent-tint);padding:0 4px}
.ev-scroll{overflow:auto;margin-top:8px}.ev-grid{border-collapse:collapse;font-family:'JetBrains Mono',monospace;font-size:12px}
.ev-grid th{background:var(--surface);color:var(--ink-2);font-weight:500;padding:3px 6px;border:1px solid var(--rule)}
.ev-grid td{border:1px solid var(--rule-2);padding:3px 6px;white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis;background:#fff}
.ev-grid td.ev-hit{background:var(--accent-tint);outline:2px solid var(--accent);font-weight:600}
.ev-page{width:100%;border:1px solid var(--rule);margin-top:8px;background:#fff}
.ev-slide p{margin:3px 0;font-size:13px}.ev-hit-line{background:var(--accent-tint);padding:0 4px}
.quote{color:var(--ink-2);background:var(--surface);border-radius:4px;padding:8px 12px;margin:6px 0}
details>summary{cursor:pointer}
.reveal{animation:rise .35s ease-out both}@keyframes rise{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.htmx-request.ev-loading{opacity:.5}
"""

# Copies the clicked row's highlight and filters long tables without a framework.
JS = """
document.addEventListener('click',e=>{const r=e.target.closest('[data-pick]');if(!r)return;
document.querySelectorAll('.picked').forEach(x=>x.classList.remove('picked'));r.classList.add('picked')});
function filterRows(inp,id){const q=inp.value.toLowerCase();document.querySelectorAll('#'+id+' tbody tr').forEach(
tr=>{tr.style.display=tr.textContent.toLowerCase().includes(q)?'':'none'})}
function toggle(id){const el=document.getElementById(id);el.hidden=!el.hidden}
// Times arrive as US Eastern text with the UTC instant in datetime=. Rewrite them in the viewer's own zone. Keep
// Eastern when the browser reports no zone, or reports UTC, which is what privacy settings that hide the zone send.
function localTimes(root){let tz;try{tz=Intl.DateTimeFormat().resolvedOptions().timeZone}catch(e){}
if(!tz||/^(Etc\\/)?(UTC|GMT|Universal|Zulu)$/.test(tz)||!root.querySelectorAll)return;
root.querySelectorAll('time[data-tz]').forEach(el=>{const d=new Date(el.getAttribute('datetime'));if(isNaN(d))return;
const at=o=>Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:tz,hourCycle:'h23',timeZoneName:'short',...o})
.formatToParts(d).map(x=>[x.type,x.value]));
const n=at({year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}),
z=n.timeZoneName,hm=n.hour+':'+n.minute;
el.textContent={when:(+n.day)+' '+at({month:'short'}).month+' '+n.year+', '+hm+' '+z,
stamp:n.year+'-'+n.month+'-'+n.day+' '+hm+':'+n.second+' '+z,clock:hm+':'+n.second+' '+z}[el.dataset.tz];el.title=tz})}
document.addEventListener('DOMContentLoaded',()=>localTimes(document));
document.addEventListener('htmx:load',e=>localTimes(e.detail.elt));
// Drag and drop onto a [data-drop] zone. Each drop adds hidden file inputs to the zone's form: files that came
// inside a folder go under name="folder" as "<folder>/<sub>/<file>", the same layout the folder picker sends, and
// loose files go under name="files".
function walk(en,out){return new Promise(done=>{
if(en.isFile){if(en.name.startsWith('.'))return done();en.file(f=>{out.push([en.fullPath.replace(/^\\/+/,''),f]);done()},()=>done());return}
if(!en.isDirectory)return done();const rd=en.createReader(),kids=[];
const more=()=>rd.readEntries(es=>{if(!es.length)return Promise.all(kids).then(done);es.forEach(x=>kids.push(walk(x,out)));more()},()=>done());
more()})}
function dropInput(zone,name,list){if(!list.length)return;const dt=new DataTransfer();
list.forEach(([p,f])=>dt.items.add(new File([f],p,{type:f.type,lastModified:f.lastModified})));
const inp=document.createElement('input');inp.type='file';inp.name=name;inp.multiple=true;inp.hidden=true;inp.dataset.dropped='1';
inp.files=dt.files;zone.appendChild(inp)}
function dropSummary(zone){const ins=zone.querySelectorAll('input[data-dropped]'),n=[...ins].reduce((a,i)=>a+i.files.length,0);
zone.querySelector('[data-drop-status]').hidden=!n;zone.querySelector('[data-drop-count]').textContent=
n+' file'+(n===1?'':'s')+' added by drag and drop'}
function dropClear(btn){const zone=btn.closest('[data-drop]');zone.querySelectorAll('input[data-dropped]').forEach(i=>i.remove());dropSummary(zone)}
['dragenter','dragover'].forEach(t=>document.addEventListener(t,e=>{const z=e.target.closest&&e.target.closest('[data-drop]');
if(!z)return;e.preventDefault();e.dataTransfer.dropEffect='copy';z.classList.add('over')}));
document.addEventListener('dragleave',e=>{const z=e.target.closest&&e.target.closest('[data-drop]');
if(z&&!z.contains(e.relatedTarget))z.classList.remove('over')});
document.addEventListener('drop',async e=>{const z=e.target.closest&&e.target.closest('[data-drop]');if(!z)return;
e.preventDefault();z.classList.remove('over');const ens=[...e.dataTransfer.items].map(i=>i.webkitGetAsEntry&&i.webkitGetAsEntry()).filter(Boolean);
const inFolder=[],loose=[];
if(ens.length){await Promise.all(ens.map(en=>walk(en,en.isDirectory?inFolder:loose)))}
else{[...e.dataTransfer.files].forEach(f=>loose.push([f.name,f]))}
dropInput(z,'folder',inFolder);dropInput(z,'files',loose);dropSummary(z)});
"""

# Single-weight line icons on a 24px grid, drawn with currentColor so they take the text color.
ICONS = {
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/>',
    "clock": '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l2.5 2"/>',
    "blocked": '<circle cx="12" cy="12" r="8"/><path d="M6.5 6.5l11 11"/>',
    "alert": '<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16.5v.01"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "file-text": ('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'
                  '<path d="M9 13h6"/><path d="M9 17h4"/>'),
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 4v5h-5"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "upload": '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M5 20h14"/>',
    "download": '<path d="M12 4v12"/><path d="M7 11l5 5 5-5"/><path d="M5 20h14"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    "chevron": '<path d="M9 6l6 6-6 6"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
    "archive": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/>'
               '<path d="M10 13h4"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/>'
             '<path d="M9 7V4h6v3"/>',
}

PILL_ICONS = {"ok": "check", "final": "check", "accent": "check", "warn": "clock", "bad": "blocked"}
ALERT_ICONS = {"": "alert", "warn": "alert", "bad": "blocked", "ok": "check-circle"}


def icon(name: str, size: int = 16):
    return NotStr(f'<svg class="i" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                  f'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                  f'{ICONS[name]}</svg>')


def render(*content) -> str:
    return to_xml(Div(*content) if len(content) > 1 else content[0])


def page(title: str, *content, user: str, csrf: str) -> str:
    head = Head(Meta(charset="utf-8"), Meta(name="viewport", content="width=device-width,initial-scale=1"),
                Title(f"{title} | Portfolio Reporting"), *Theme.slate.headers(mode="light"),
                Link(rel="icon", type="image/svg+xml", href="/static/logo.svg"),
                Link(rel="stylesheet", href=FONTS), Script(src=HTMX), Style(CSS), Script(JS))
    brand = A(Img(src="/static/logo.svg", alt=""), Span("Corvane", cls="name"), Span(cls="sep"),
              Span("Portfolio Reporting", cls="app"), href="/", cls="brand")
    who = [Span(icon("user"), user, cls="who"),
           Form(Input(type="hidden", name="csrf", value=csrf), Button("Sign out", cls="btn ghost"),
                method="post", action="/signout", cls="signout")] if user else []
    bar = Header(Div(brand, Div(*who, cls="flex items-center gap-5"),
                     cls="wrap flex items-center justify-between", style="height:56px"), cls="topbar")
    body = Body(bar, Main(Div(*content, cls="wrap", style="padding-top:36px;padding-bottom:64px")),
                Footer(Div("Every figure is computed in code from the uploaded workbooks. The model drafts prose only, "
                           "and code checks each sentence against its source.", cls="wrap muted small",
                           style="padding-top:18px;padding-bottom:18px;border-top:1px solid var(--rule)")),
                # HTMX requests carry the CSRF token too.
                hx_headers=f'{{"X-CSRF-Token": "{csrf}"}}')
    return "<!doctype html>" + to_xml(Html(head, body, lang="en"))


def pill(text: str, tone: str = "info"):
    mark = PILL_ICONS.get(tone)
    return Span(*([icon(mark, 14)] if mark else []), text[:1].upper() + text[1:], cls=f"pill {tone}")


def kpi(label: str, value, tone: str = "", sub=""):
    return Div(Div(str(value), cls="v"), Div(label, cls="l"), *([Div(sub, cls="small muted")] if sub else []),
               cls=f"kpi {tone}")


def tabs(base: str, current: str, items: list[tuple[str, str, int | None]]):
    return Nav(*[A(label, *([Span(str(n), cls="cnt")] if n is not None else []), href=f"{base}?tab={key}",
                   cls="on" if key == current else "") for key, label, n in items], cls="tabs")


def steps(done: int):
    names = ["Upload", "Process", "Validate", "Review", "Approve", "Final"]
    out = []
    for i, n in enumerate(names):
        if i:
            out.append(Span(cls="flow-line " + ("done" if i <= done else "")))
        if i < done:
            out.append(Span(icon("check-circle", 18), n, cls="flow-step done"))
        else:
            out.append(Span(Span(str(i + 1), cls="n"), n, cls="flow-step " + ("now" if i == done else "")))
    return Div(*out, cls="flow")


def csrf_input(token: str):
    return Input(type="hidden", name="csrf", value=token)


def alert(*content, tone: str = ""):
    return Div(icon(ALERT_ICONS.get(tone, "alert"), 18), Div(*content, cls="body"), cls=f"alert {tone}")
