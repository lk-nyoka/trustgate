"""Public landing page: canvas motion background, perspective floor and a request-flow animation.

No external assets or scripts. Every animation shows where authority moves or is stopped:
blue = request enters the middleware, green = authorized, amber = waiting for a human,
red = contained before PayPal is called. Respects prefers-reduced-motion.
"""

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TrustGate</title><style>
:root{--bg:#0b0d12;--fg:#e8e6e1;--mut:#8b93a7;--line:#222838;--card:rgba(18,21,28,.72);--acc:#818cf8;--ok:#34d399;--warn:#fbbf24;--bad:#f87171}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(1200px 600px at 70% -10%,#1b2146 0,var(--bg) 60%) fixed;color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
#bg{position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}
.wrap{position:relative;z-index:1;max-width:1080px;margin:0 auto;padding:0 24px}
.nav{display:flex;justify-content:space-between;align-items:center;padding:22px 0}.nav b{letter-spacing:.02em}
.nav nav{display:flex;gap:22px;align-items:center}.nav a{color:var(--mut);text-decoration:none}.nav a:hover{color:var(--fg)}
.btn{display:inline-block;padding:11px 20px;border-radius:10px;border:1px solid var(--line);color:var(--fg)!important;text-decoration:none;background:var(--card)}
.btn.primary{background:var(--acc);border-color:var(--acc);color:#0b0d12!important;font-weight:600}
.hero{display:grid;grid-template-columns:1.05fr 1fr;gap:40px;align-items:center;min-height:78vh;padding:30px 0 60px}
.eyebrow{color:var(--acc);font-size:13px;letter-spacing:.14em;text-transform:uppercase;margin:0 0 14px}
h1{font-size:clamp(34px,5.4vw,60px);line-height:1.04;margin:0 0 18px;letter-spacing:-.02em}h1 span{color:var(--acc)}
.lead{color:var(--mut);font-size:18px;max-width:520px;margin:0 0 28px}.cta{display:flex;gap:12px;flex-wrap:wrap}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px;backdrop-filter:blur(8px);transition:transform .15s ease-out;transform-style:preserve-3d}
#flow{width:100%;height:auto;display:block}.cap{display:flex;justify-content:space-between;gap:12px;padding:8px 6px 2px;font-size:14px;color:var(--mut)}.cap b{color:var(--fg)}
section{padding:56px 0}h2{font-size:28px;margin:0 0 20px;letter-spacing:-.01em}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:22px}
.stats div,.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}
.stats b{display:block;font-size:34px;font-variant-numeric:tabular-nums}.stats span{color:var(--mut);font-size:14px}
.claims{color:var(--mut);padding-left:20px;margin:0}.claims li{margin:6px 0}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;perspective:900px}
.card{transition:transform .15s ease-out;transform-style:preserve-3d}.card h3{margin:0 0 8px}.card ul{margin:0;padding-left:18px;color:var(--mut)}
.k1 h3{color:var(--acc)}.k2 h3{color:var(--ok)}.k3 h3{color:var(--warn)}
footer{color:var(--mut);font-size:14px;padding:30px 0 50px}
@media(max-width:820px){.hero{grid-template-columns:1fr}.cards,.stats{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{transition:none!important}}
</style></head><body><canvas id="bg"></canvas><div class="wrap">
<header class="nav"><b>TrustGate</b><nav><a href="#how">How it works</a><a href="#proof">Proof</a><a class="btn" href="/login">Launch demo</a></nav></header>
<section class="hero"><div><p class="eyebrow">Trust infrastructure for autonomous commerce</p>
<h1>AI may recommend.<br><span>Only policy may authorize.</span></h1>
<p class="lead">TrustGate is trust middleware between your AI agent and PayPal. Deterministic policy decides whether a payment is ever attempted, a human approves the exceptions, and every decision leaves evidence.</p>
<div class="cta"><a class="btn primary" href="/login">Launch live demo</a><a class="btn" href="#how">See how it works</a></div></div>
<div class="panel" id="panel"><canvas id="flow" width="640" height="340"></canvas><div class="cap"><span id="cl"></span><b id="co"></b></div></div></section>
<section id="proof"><h2>Live from this server</h2><div class="stats">
<div><b>{{captured}}</b><span>payments captured</span></div><div><b>{{awaiting}}</b><span>awaiting a human</span></div><div><b>{{blocked}}</b><span>blocked before PayPal</span></div></div>
<ul class="claims"><li>Held and blocked requests never create a PayPal order.</li><li>The agent holds no payment credentials and cannot approve itself.</li>
<li>Approvals are bound to the merchant, payee, amount, currency and policy version.</li><li>Every decision is written to a hash-chained audit log.</li></ul></section>
<section id="how"><h2>How TrustGate works</h2><div class="cards">
<div class="card tilt k1"><h3>AI agent</h3><ul><li>Finds products</li><li>Plans purchases</li><li>Submits purchase intents</li><li>Cannot spend money</li></ul></div>
<div class="card tilt k2"><h3>Trust middleware</h3><ul><li>Resolves trusted facts</li><li>Evaluates deterministic policy</li><li>Requests approval when needed</li><li>Records audit evidence</li></ul></div>
<div class="card tilt k3"><h3>Payment provider</h3><ul><li>Receives only authorized requests</li><li>Executes the payment</li><li>Returns order and capture status</li></ul></div></div></section>
<footer>Prototype. PayPal Sandbox only. All merchants are fictional.</footer></div>
<script>
(()=>{const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;
const C={ok:"#34d399",warn:"#fbbf24",bad:"#f87171",acc:"#818cf8",fg:"#e8e6e1",line:"#2a3147"};
const bg=document.getElementById("bg"),g=bg.getContext("2d");let W,H;
function size(){const d=Math.min(devicePixelRatio||1,2);W=innerWidth;H=innerHeight;bg.width=W*d;bg.height=H*d;g.setTransform(d,0,0,d,0,0)}
addEventListener("resize",size);size();
function floor(t){g.clearRect(0,0,W,H);const hy=H*.58,cx=W/2,N=16,ph=reduce?0:(t*.18)%1;g.lineWidth=1;g.strokeStyle=C.acc;
 for(let i=-16;i<=16;i++){g.globalAlpha=.1;g.beginPath();g.moveTo(cx+i*10,hy);g.lineTo(cx+i*W*.16,H);g.stroke()}
 for(let k=0;k<N;k++){const p=(k+ph)/N,y=hy+(H-hy)*p*p;g.globalAlpha=.05+.2*p;g.beginPath();g.moveTo(0,y);g.lineTo(W,y);g.stroke()}g.globalAlpha=1}
const fc=document.getElementById("flow"),f=fc.getContext("2d"),FW=fc.width,FH=fc.height,cl=document.getElementById("cl"),co=document.getElementById("co");
const X=[.14,.5,.86],Y=.46,S=[
 {l:"$180 Demo Airlines flight",o:"ALLOW \\u00b7 CAPTURED",c:C.ok,m:"allow"},
 {l:"$320 Demo Airlines flight",o:"APPROVAL REQUIRED \\u00b7 HUMAN DECIDES",c:C.warn,m:"hold"},
 {l:"$3 Activation Services Demo fee",o:"BLOCK \\u00b7 PAYPAL NOT CALLED",c:C.bad,m:"block"}],D=6.5;
function rr(x,y,w,h,r){f.beginPath();f.roundRect?f.roundRect(x,y,w,h,r):f.rect(x,y,w,h)}
function node(i,label,col,glow){const w=130,h=64,x=FW*X[i]-w/2,y=FH*Y-h/2;f.save();if(glow){f.shadowColor=col;f.shadowBlur=22}
 f.fillStyle="#12151c";f.strokeStyle=col;f.lineWidth=1.5;rr(x,y,w,h,12);f.fill();f.stroke();f.restore();
 f.fillStyle=C.fg;f.font="600 14px system-ui";f.textAlign="center";f.fillText(label,FW*X[i],FH*Y+5)}
function lane(a,b,col,al,dash){f.save();f.strokeStyle=col;f.globalAlpha=al;f.lineWidth=2;f.setLineDash(dash?[6,6]:[]);f.beginPath();f.moveTo(FW*X[a]+66,FH*Y);f.lineTo(FW*X[b]-66,FH*Y);f.stroke();f.restore()}
function dot(x,col,r){f.save();f.fillStyle=col;f.shadowColor=col;f.shadowBlur=16;f.beginPath();f.arc(x,FH*Y,r||6,0,7);f.fill();f.restore()}
const L=(a,b,p)=>a+(b-a)*Math.min(1,Math.max(0,p));
function flow(t){const i=Math.floor(t/D)%3,s=S[i],u=reduce?3.4:t%D;f.clearRect(0,0,FW,FH);
 const ax=FW*X[0]+66,mx=FW*X[1]-66,mc=FW*X[1],px=FW*X[2]-66;
 const decided=u>2.4,pulse=.5+.5*Math.sin(u*9);
 lane(0,1,C.acc,.8);
 lane(1,2,decided&&s.m==="allow"?C.ok:decided&&s.m==="hold"?C.warn:C.line,decided&&s.m!=="block"?.9:.5,decided&&s.m==="hold");
 node(0,"AI agent",C.acc,false);
 node(1,"TrustGate",!decided?C.acc:s.c,u>1.6&&u<2.6||decided&&s.m!=="allow"&&pulse>.5);
 node(2,"PayPal",decided&&s.m==="allow"&&u>3.9?C.ok:C.line,decided&&s.m==="allow"&&u>3.9);
 f.fillStyle=C.mut||"#8b93a7";f.font="12px system-ui";f.fillText("proposes",FW*X[0],FH*Y+54);f.fillText(decided?s.m==="allow"?"authorized":s.m==="hold"?"held":"contained":"evaluating",mc,FH*Y+54);
 f.fillText(decided&&s.m==="allow"&&u>3.9?"order captured":s.m==="block"&&decided?"not reached":"",FW*X[2],FH*Y+54);
 if(u<1.6)dot(L(ax,mx,u/1.5),C.acc);
 else if(!decided){const r=8+4*pulse;dot(mc,C.acc,r)}
 else if(s.m==="allow"){if(u<3.9)dot(L(mc+66,px,(u-2.4)/1.4),C.ok);}
 else if(s.m==="hold"){dot(mc,C.warn,6+3*pulse)}
 else if(u<3.4){f.save();f.globalAlpha=1-(u-2.4);dot(mc,C.bad,6+(u-2.4)*10);f.restore()}
 cl.textContent=s.l;co.textContent=decided?s.o:"EVALUATING\\u2026";co.style.color=decided?s.c:C.fg}
function tick(ms){const t=ms/1000;floor(t);flow(t);if(!reduce)requestAnimationFrame(tick)}
requestAnimationFrame(tick);
if(!reduce){const tilt=(el,max)=>{el.addEventListener("pointermove",e=>{const r=el.getBoundingClientRect(),x=(e.clientX-r.left)/r.width-.5,y=(e.clientY-r.top)/r.height-.5;
 el.style.transform=`perspective(900px) rotateY(${x*max}deg) rotateX(${-y*max}deg)`});el.addEventListener("pointerleave",()=>el.style.transform="")};
 tilt(document.getElementById("panel"),8);document.querySelectorAll(".tilt").forEach(c=>tilt(c,10))}
})();
</script></body></html>"""


def landing_page(stats):
    html = PAGE
    for key in ("captured", "awaiting", "blocked"):
        html = html.replace("{{" + key + "}}", str(int(stats.get(key, 0))))
    return html
