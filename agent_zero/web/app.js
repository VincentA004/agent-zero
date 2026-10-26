"use strict";
import { VoiceSession } from "/voice.js";
const $ = (id) => document.getElementById(id);
let previousMode = null, controlsBusy = false, controlsDigest = "";
let permissionDigest = "", trapDigest = "", eventDigest = "", processDigest = "";
let state = null, token = "", tokenUntil = 0, busy = false, voiceSession = null, view = "jarvis", toastTimer;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
function toast(message) { $("toast").textContent = message; $("toast").classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => $("toast").classList.remove("show"), 4500); }
async function rpc(method, params = {}, signal) {
  const response = await fetch("/api/rpc", {method:"POST",headers:{"Content-Type":"application/json","X-Agent-Zero":"1"},body:JSON.stringify({method,params,token}),signal});
  const data = await response.json();
  if (!data.ok) {
    if (/expired|authorization required/.test(data.error)) { token = ""; tokenUntil = 0; }
    throw new Error(data.error || "Runtime request failed");
  }
  return data.result;
}
function node(tag, className, text) { const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = text; return el; }
function duration(seconds) { return [Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60].map(x=>String(x).padStart(2,"0")).join(":"); }
function authorized() { return !!token && Date.now() < tokenUntil; }
function requireAuth() {
  if (authorized()) return true;
  void voiceSession?.pause();
  const setup = state?.auth_configured === false;
  $("confirm-passphrase-row").hidden = !setup;
  $("confirm-passphrase").required = setup;
  $("passphrase").minLength = setup ? 12 : 1;
  $("passphrase").autocomplete = setup ? "new-password" : "current-password";
  $("auth-instructions").textContent = setup
    ? "Create your kernel passphrase (12+ characters). It stays local, outside agent memory."
    : "Type your kernel passphrase, or explicitly speak it using offline verification.";
  $("spoken-auth-button").hidden = setup;
  $("auth-error").textContent = "";
  if (!$("auth-dialog").open) $("auth-dialog").showModal();
  $("passphrase").focus();
  return false;
}
function requestKernel() { if (requireAuth()) showView("kernel"); }
function showView(next) {
  if (next === "kernel" && !authorized()) { requireAuth(); return; }
  view = next;
  if(next !== "kernel") { $("inspect-stats").textContent="";$("inspect-pages").replaceChildren();$("inspect-body").textContent="";$("mailbox-body").textContent=""; }
  document.body.classList.toggle("orb-mode", next === "jarvis");
  document.body.classList.toggle("kernel-mode", next === "kernel");
  $("jarvis-view").classList.toggle("hidden", next !== "jarvis"); $("kernel-view").classList.toggle("hidden", next !== "kernel");
  $("breadcrumb").textContent = next === "jarvis" ? "JARVIS" : "CARDINAL";

  if (next === "kernel") { void voiceSession?.pause(); cancelAnimationFrame(orbFrame); $("kernel-input").focus(); }
  else voiceSession?.resume();
  if (next === "jarvis") scheduleDraw();
}
document.querySelectorAll("[data-view=kernel]").forEach(el=>el.addEventListener("click",requestKernel));
function voiceSetup() {
  void voiceSession?.pause();
  $("voice-readiness").textContent = state?.voice?.input_ready ? "Offline speech input is ready. Click the orb, or hold Space, to speak." : state?.voice?.reason || "Waiting for the local runtime.";
  $("voice-config-path").textContent = state?.voice?.config_path || "config.json";
  $("voice-cloud-readiness").textContent = state?.local_enabled ? `Local conversation uses ${state.local_model}. No cloud inference is used.` : state?.cloud_enabled ? (state.cloud_free_only ? "OpenRouter is enabled in free-only mode. Paid routes are blocked; free NVIDIA endpoints may log prompts under their trial terms." : "Cloud conversation is explicitly enabled in the daemon configuration.") : "Cloud conversation is disabled. With offline speech installed, the voice preview works without calling a model.";
  $("voice-setup-dialog").showModal();
}
function renderVoicePhase(phase) {
  const labels = {idle:"STANDBY",unavailable:"VOICE SETUP",starting:"OPENING MICROPHONE",dormant:"DORMANT · HEY JARVIS",checking:"DORMANT · HEY JARVIS",awake:"AWAKE",paused:"VOICE PAUSED",listening:"LISTENING",thinking:"THINKING",speaking:"SPEAKING",suspended:"SUSPENDED"};
  $("jarvis-view").dataset.phase = phase;
  $("voice-status").textContent = labels[phase] || "STANDBY";
  busy = phase === "thinking";
  $("voice-button").setAttribute("aria-pressed", String(phase === "listening"));
  $("voice-button").setAttribute("aria-label", phase === "listening" ? "Finish speaking" : "Start listening");
  $("orb-trigger").setAttribute("aria-label", phase === "listening" ? "Finish speaking to JARVIS" : "Speak to JARVIS");
}
$("voice-setup-button").addEventListener("click",voiceSetup);
$("voice-setup-close").addEventListener("click",()=>$("voice-setup-dialog").close());
$("voice-setup-dialog").addEventListener("close",()=>voiceSession?.resume());
$("wake-button").addEventListener("click",()=>void voiceSession.toggleWake());
$("wake-setting-button").addEventListener("click",()=>{$("voice-setup-dialog").close();void voiceSession.toggleWake();});
function renderWake(enabled) {
  $("wake-button").setAttribute("aria-pressed",String(enabled));
  $("wake-button").setAttribute("aria-label",enabled?"Disable Hey Jarvis":"Enable Hey Jarvis");
  $("wake-setting-button").setAttribute("aria-pressed",String(enabled));
  $("wake-setting-button").textContent=enabled?"Disable “Hey Jarvis”":"Enable “Hey Jarvis”";
}
$("voice-button").addEventListener("click",()=>void voiceSession.toggle());
$("orb-trigger").addEventListener("click",()=>void voiceSession.toggle());
$("stop-voice-button").addEventListener("click",()=>void voiceSession.cancel());
$("sound-button").addEventListener("click",()=>{const muted=voiceSession.toggleMute();$("sound-button").setAttribute("aria-pressed",String(muted));$("sound-button").setAttribute("aria-label",muted?"Unmute voice output":"Mute voice output");});
$("metrics-button").addEventListener("click",()=>{const hidden=$("voice-vitals").classList.toggle("collapsed");$("metrics-button").setAttribute("aria-expanded",String(!hidden));});
let spaceHeld=false;
document.addEventListener("keydown",event=>{
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); requestKernel(); return; }
  if(event.code==="Escape" && view==="jarvis") void voiceSession.cancel();
  if(event.code!=="Space" || event.repeat || view!=="jarvis" || document.querySelector("dialog[open]") || event.target.closest("input,textarea,select,button:not(#orb-trigger)"))return;
  event.preventDefault();spaceHeld=true;void voiceSession.start();
});
document.addEventListener("keyup",event=>{if(event.code==="Space"&&spaceHeld){event.preventDefault();spaceHeld=false;if(voiceSession.phase==="starting")void voiceSession.cancel();else void voiceSession.stop();}});
window.addEventListener("blur",()=>{spaceHeld=false;void voiceSession?.pause();});
window.addEventListener("focus",()=>voiceSession?.resume());
window.addEventListener("pagehide",()=>void voiceSession.cancel());
async function authenticate(e) {
  e.preventDefault(); const button = $("auth-form").querySelector('[type="submit"]'); button.disabled = true;
  const passcode = $("passphrase").value, confirmation = $("confirm-passphrase").value; $("passphrase").value = ""; $("confirm-passphrase").value = "";
  try { const result = await rpc("sys_auth",{passcode, ...(state?.auth_configured === false ? {create:true,confirmation} : {})}); token = result.token; tokenUntil = Date.now()+result.expires_in*1000; $("auth-dialog").close(); $("auth-error").textContent = ""; showView("kernel"); toast("Kernel authority granted for five minutes."); await refresh(); }
  catch (error) { $("auth-error").textContent = error.message; }
  finally { button.disabled = false; }
}
let authVoice = null;
$("spoken-auth-button").addEventListener("click", async () => {
  await voiceSession?.pause();
  if (!authVoice) {
    authVoice = new VoiceSession({request: rpc, getState: () => ({...state, mode:"userspace"}),
      onStatus: phase => { $("spoken-auth-button").textContent = phase === "listening" ? "Finish speaking" : phase === "thinking" ? "Verifying locally…" : "Speak passphrase locally"; },
      onError: message => { $("auth-error").textContent = message; },
      onSetup: () => { $("auth-error").textContent = "Configure offline speech first, or type the passphrase."; }, onTrap: () => {}});
    authVoice.onAuthenticated = async result => {
      token=result.token; tokenUntil=Date.now()+result.expires_in*1000;
      $("auth-dialog").close(); $("auth-error").textContent=""; showView("kernel"); await refresh();
    };
  }
  if (authVoice.capture) await authVoice.stop();
  else await authVoice.start({kind:"auth"});
});
window.addEventListener("blur", () => void authVoice?.cancel());
$("auth-dialog").addEventListener("close", () => void authVoice?.cancel());
$("auth-form").addEventListener("submit",authenticate);
$("auth-cancel").addEventListener("click",()=>{$("passphrase").value="";$("auth-dialog").close();});
$("auth-dialog").addEventListener("close",()=>{$("passphrase").value="";$("confirm-passphrase").value="";if(view==="jarvis")voiceSession?.resume();});
$("auth-button").addEventListener("click",async()=>{if (authorized()) {try { await rpc("sys_exit");token="";tokenUntil=0;toast("Authority dropped. Userspace may resume after traps are resolved.");await refresh();} catch(error){toast(error.message);} } else requireAuth();});
$("kernel-registry-button").addEventListener("click",openRegistry);
async function togglePermission(capability,enabled) { if(!requireAuth()) return; try {await rpc("sys_perm_toggle",{capability_id:capability,enabled});await refresh();}catch(error){toast(error.message);} }
async function resolveTrap(id,approved) { if(!requireAuth())return;try {await rpc("sys_resolve",{trap_id:id,approved});toast(approved?"Request reviewed and approved. No privileged action was automatically executed.":"Request rejected.");await refresh();}catch(error){toast(error.message);} }
function renderState(s) {
  state = s;
  if (!authorized()) {token="";tokenUntil=0;}
  if (s.mode === "kernel" && authorized() && view !== "kernel") showView("kernel");
  else if ((s.mode !== "kernel" || !authorized()) && view === "kernel") showView("jarvis");
  if (s.mode !== "userspace" && previousMode !== s.mode && !authorized()) requireAuth();
  previousMode = s.mode;
  $("connection").replaceChildren(node("i","green-dot"),document.createTextNode(s.local_enabled?" LOCAL ONLINE":s.cloud_enabled?" ONLINE":" LOCAL PREVIEW"));
  const model = s.registry.cloud.find(m=>m.id===s.model);
  $("active-model").textContent=s.local_enabled?s.local_model:(model?.name||s.model);
  $("cloud-status").textContent=s.local_enabled?"OLLAMA · OFFLINE":s.cloud_enabled?(s.cloud_free_only?"OPENROUTER · FREE ONLY":"CLOUD ACTIVE"):"LOCAL PREVIEW";
  voiceSession?.sync(s);
  $("pending-mail").textContent=String(s.background?.root_mail_pending || 0);
  const vm=s.virtual_memory;
  $("memory-summary").textContent=vm?`${vm.pages} PAGES / ${vm.stored_bytes} BYTES BACKING STORE\n${vm.shared_regions} SHARED REGIONS / ${vm.resident_bytes} BYTES RESIDENT\n${vm.resident_budget_per_pid} BYTES WORKING SET PER PID · mem spaces / mem stats PID`:"Waiting for memory subsystem";
  const t=s.telemetry;
  $("memory-value").textContent=t.memory_used_gb===null?"— GB":`${t.memory_used_gb} GB`;
  $("memory-detail").textContent=t.memory_total_gb?`HOST TOTAL ${t.memory_total_gb} GB · SYSTEM-WIDE` : "Install psutil for live host telemetry";
  $("memory-meter").style.width=t.memory_total_gb?`${100*t.memory_used_gb/t.memory_total_gb}%`:"0%";
  $("cpu-value").textContent=t.cpu_percent===null?"— %":`${t.cpu_percent.toFixed(0)} %`;
  $("cpu-meter").style.width=`${t.cpu_percent||0}%`;
  $("local-budget").textContent=`${s.local_memory_gb} GB`;$("kernel-memory").textContent=`${s.local_memory_gb} GB`;
  $("uptime").textContent=duration(s.uptime);$("token-count").textContent=s.tokens.toLocaleString();$("spend").textContent=`$${s.spent_usd.toFixed(5)}`;
  $("authority").textContent=authorized()?`AUTHENTICATED / ${Math.ceil((tokenUntil-Date.now())/1000)}s REMAINING`:"READ-ONLY OBSERVER";
  $("auth-button").textContent=authorized()?"[ exit kernel ]":"[ authenticate ]";
  $("kernel-brain").textContent=`LOCAL ENGINE · ${s.local_model.toUpperCase()}`;

  const nextProcesses=JSON.stringify(s.processes||[]);
  if(processDigest!==nextProcesses){processDigest=nextProcesses;
    const lines=[];
    function branch(process,prefix=""){
      lines.push(`${prefix}${process.pid===1?"◈":"└─"} PID ${process.pid} / PPID ${process.ppid}  ${process.name}  [${process.state.toUpperCase()}]`);
      lines.push(`${prefix}   ${process.model}`);
      for(const child of process.children||[])branch(child,prefix+"   ");
    }
    for(const root of s.process_tree||[])branch(root);
    $("process-tree").textContent=lines.join("\n");
    $("process-controls").replaceChildren();
    for(const process of s.processes||[]){if(process.pid===1)continue;
      const row=node("div","process-row"),description=node("span","",`PID ${process.pid} · ${process.task.slice(0,70)}`);row.append(description);
      const terminal=["exited","failed","cancelled","interrupted"].includes(process.state);
      const actions=terminal?[["REAP","sys_reap",{pid:process.pid}]]:process.state==="created"?[["EXEC","sys_exec",{pid:process.pid}],["CANCEL","sys_signal",{pid:process.pid,signal:"TERM"}]]:process.state==="waiting_review"?[]:[[process.state==="stopped"?"CONT":"STOP","sys_signal",{pid:process.pid,signal:process.state==="stopped"?"CONT":"STOP"}],["CANCEL","sys_signal",{pid:process.pid,signal:"TERM"}]];
      for(const [label,method,params]of actions){const button=node("button","",label);button.addEventListener("click",async()=>{if(!requireAuth())return;try{await rpc(method,params);await refresh();}catch(error){toast(error.message);}});row.append(button);}
      $("process-controls").append(row);
    }
  }
  const nextPermissions=JSON.stringify(s.permissions);
  if (permissionDigest!==nextPermissions) { permissionDigest=nextPermissions;
  $("permissions").replaceChildren();
  for(const [key,enabled] of Object.entries(s.permissions)){const row=node("div","permission");const label=node("span","",key.replaceAll("_"," ").toUpperCase());const button=node("button",enabled?"enabled":"",enabled?"[ × ] ENABLED":"[   ] DISABLED");button.setAttribute("aria-label",`Toggle ${key}`);button.addEventListener("click",()=>togglePermission(key,!enabled));if(key==="shell_exec"||key==="network"){button.disabled=true;button.title="No execution adapter installed";}row.append(label,button);$("permissions").append(row);}
  }
  const nextTraps=JSON.stringify(s.pending);
  if (trapDigest!==nextTraps) { trapDigest=nextTraps;
  $("trap-count").textContent=`${s.pending.length} PENDING`;$("traps").replaceChildren();
  if(!s.pending.length)$("traps").append(node("div","empty-state","[ NO PENDING INTERRUPTS ]\nAll processes within their authority."));
  for(const trap of s.pending){const el=node("div","trap-item");el.append(node("strong","",trap.id.slice(0,12)+" / "+trap.status.toUpperCase()),node("p","",trap.request));for(const [label,approve]of[["APPROVE REVIEW",true],["REJECT",false]]){const b=node("button","",label);b.addEventListener("click",()=>resolveTrap(trap.id,approve));el.append(b);}$("traps").append(el);}
  }
  const nextEvents=JSON.stringify(s.events);
  if (eventDigest!==nextEvents) { eventDigest=nextEvents;
  $("events").replaceChildren();
  for(const event of s.events){const row=node("div","event-row"),detail=node("div","",event.event);detail.append(node("small","",event.detail));const time=node("time","",new Date(event.created*1000).toLocaleTimeString("en-GB"));row.append(time,detail);$("events").append(row);}
  }
}
async function refresh(){try{const response=await fetch("/api/status");const data=await response.json();if(!data.ok)throw new Error(data.error);renderState(data.result);await refreshControls();}catch(error){$("connection").textContent="RUNTIME OFFLINE";}}
function openRegistry(){if(!state)return toast("Waiting for local runtime.");void voiceSession?.pause();$("model-list").replaceChildren();for(const model of state.registry.cloud){const row=node("div","registry-entry"),info=node("div");info.append(node("h3","",model.name),node("small","",model.id),node("p","",`$${model.input_per_million} input / $${model.output_per_million} output per 1M`));const source=node("a","","Catalog source ↗");source.href=model.source;source.target="_blank";source.rel="noopener noreferrer";info.append(node("p","",model.role),node("p","registry-note",model.note),source);const b=node("button","",model.id===state.model?"ACTIVE":state.cloud_free_only&&!model.free?"PAID LOCKED":"SELECT");b.disabled=model.id===state.model||(state.cloud_free_only&&!model.free);b.addEventListener("click",async()=>{if(!authorized()){$("registry-dialog").close();requireAuth();return;}try{await rpc("sys_model_swap",{target_model_id:model.id});await refresh();openRegistry();toast("Model selected. Billing configuration is unchanged.");}catch(error){toast(error.message);}});row.append(info,b);$("model-list").append(row);}$("local-list").replaceChildren(...state.registry.local.map(m=>node("div","",`${m.id} · ${m.weights_gb} GB weights · ~${m.budget_gb} GB budget`)));if(!$("registry-dialog").open)$("registry-dialog").showModal();}
$("registry-button").addEventListener("click",openRegistry);$("registry-close").addEventListener("click",()=>$("registry-dialog").close());$("registry-dialog").addEventListener("close",()=>{if(view==="jarvis")voiceSession?.resume();});
$("kernel-form").addEventListener("submit",async e=>{e.preventDefault();const text=$("kernel-input").value.trim();if(!text)return;$("kernel-input").value="";const out=$("command-result");if(text==="help"){out.textContent="status · ps · tree · spawn TASK · fork TASK · exec PID · wait PID · signal PID TERM|STOP|CONT · reap PID · model ID · allow file_write · flush session · mem · schedule · service · mail · trace · inspect · exit\nControl: schedule list|after|every|notice|at|runs|pause|resume|run|remove; service list|register|grant|revoke|start|disable|enable|remove; mail recv|send|ack; trace [PID]; inspect [PID]\nMemory: mem spaces | mem stats [PID] | mem create NAME | mem grant NAME PID r|rw | mem revoke NAME PID | mem list SPACE | mem read SPACE KEY | mem write SPACE KEY TEXT | mem drop NAME\nOther text is sent only to your installed local model as a proposal. No proposal executes automatically.\nUse the trap table to approve or reject requests.";return;}if(text!=="status"&&!requireAuth())return;const[command,...rest]=text.split(" "),arg=rest.join(" ");let method="status",params={};if(["schedule","service","mail","trace","inspect"].includes(command)){method="sys_control_command";params={text};}else if(command==="mem"){method="sys_mem_command";params={text};}else if(command==="exit"){method="sys_exit";}else if(command==="model"){method="sys_model_swap";params={target_model_id:arg};}else if(command==="allow"||command==="deny"){method="sys_perm_toggle";params={capability_id:arg,enabled:command==="allow"};}else if(command==="flush"){method="sys_mem_flush";params={scope:arg||"session"};}else if(command==="ps"){method="sys_proc_table";}else if(command==="tree"){method="sys_proc_tree";}else if(command==="spawn"||command==="fork"){method=command==="spawn"?"sys_spawn":"sys_fork";params={task:arg};}else if(command==="exec"||command==="wait"||command==="reap"||command==="info"){method={exec:"sys_exec",wait:"sys_wait",reap:"sys_reap",info:"sys_proc_info"}[command];params={pid:Number(arg)};}else if(command==="signal"){const [pid,signal]=arg.split(/\s+/);method="sys_signal";params={pid:Number(pid),signal:signal||"TERM"};}else if(command!=="status"){method="sys_parse";params={text};}out.textContent="PROCESSING…";try{const result=await rpc(method,params);if(method==="sys_exit"){token="";tokenUntil=0;}out.textContent=method==="status"?`MODE ${result.mode.toUpperCase()} / MODEL ${result.model}\nLOCAL ${result.local_model} / CLOUD ${result.cloud_enabled?"ON":"OFF"}\n${result.pending.length} PENDING TRAPS / ${result.tokens} TOKENS / $${result.spent_usd.toFixed(6)}`:JSON.stringify(result,null,2)+(method==="sys_parse"?"\nPROPOSAL ONLY / issue an explicit command to execute":"");await refresh();}catch(error){out.textContent=`DENIED / ${error.message}`;}});

async function controlAction(method, params = {}) {
  if(!requireAuth()) return;
  try { const result=await rpc(method,params);$("command-result").textContent=JSON.stringify(result,null,2);controlsDigest="";await refresh();return result; }
  catch(error){toast(error.message);}
}
function actionButton(label, method, params) {
  const button=node("button","",`[ ${label} ]`);
  button.addEventListener("click",()=>void controlAction(method,params));return button;
}
async function refreshControls() {
  if(view!=="kernel"||!authorized()||controlsBusy)return;
  controlsBusy=true;const owner=token;
  try {
    const data=await rpc("sys_control_snapshot");
    if(owner!==token||view!=="kernel")return;
    const digest=JSON.stringify(data);if(digest===controlsDigest)return;controlsDigest=digest;
    $("schedule-list").replaceChildren();
    for(const job of data.jobs){const row=node("div","runtime-row");row.append(node("span","",`${job.name} / ${job.kind} / ${job.enabled?"ARMED":"PAUSED"} / ${new Date(job.next_run*1000).toLocaleString()}`),actionButton(job.enabled?"pause":"resume","sys_job_enable",{name:job.name,enabled:!job.enabled}),actionButton("run","sys_job_run",{name:job.name}),actionButton("remove","sys_job_remove",{name:job.name}));$("schedule-list").append(row);}
    if(!data.jobs.length)$("schedule-list").append(node("div","empty-state","[ NO SCHEDULED JOBS ]"));
    for(const run of data.runs.slice(0,5))$("schedule-list").append(node("div","runtime-row",`RUN ${run.id} / ${run.job} / ${run.status} / PID ${run.pid||"—"}`));
    $("service-list").replaceChildren();
    for(const service of data.services){const row=node("div","runtime-row");row.append(node("span","",`${service.name} / ${service.enabled?service.state:"DISABLED"} / callers ${service.callers.join(",")||"none"}`),actionButton("start","sys_service_start",{name:service.name}),actionButton(service.enabled?"disable":"enable","sys_service_enable",{name:service.name,enabled:!service.enabled}));$("service-list").append(row);}
    if(!data.services.length)$("service-list").append(node("div","empty-state","[ NO REGISTERED SERVICES ]"));
    $("mailbox-list").replaceChildren();
    for(const mail of data.mailboxes){const row=node("div","runtime-row");const read=node("button","",`[ read PID ${mail.pid} ]`);read.addEventListener("click",async()=>{const result=await controlAction("sys_mail_recv",{pid:mail.pid});if(result&&authorized()&&view==="kernel")$("mailbox-body").textContent=JSON.stringify(result,null,2);});row.append(node("span","",`PID ${mail.pid} / ${mail.pending} PENDING`),read);$("mailbox-list").append(row);}
    $("trace-list").replaceChildren();
    for(const trace of data.traces)$("trace-list").append(node("div","trace-row",`#${trace.id}  PID ${trace.pid}  ${trace.kind.padEnd(7)} ${trace.operation.padEnd(22)} ${trace.status.padEnd(11)} ${trace.duration_ms??"—"}ms${trace.parent?`  parent #${trace.parent}`:""}${trace.error_type?`  ${trace.error_type}`:""}`));
    if(!data.traces.length)$("trace-list").append(node("div","empty-state","[ NO EXECUTION SPANS ]"));
  }catch(error){if(owner===token)toast(error.message);}finally{controlsBusy=false;}
}
$("inspect-form").addEventListener("submit",async event=>{
  event.preventDefault();if(!requireAuth())return;const owner=token,pid=Number($("inspect-pid").value);
  try { const result=await rpc("sys_mem_inspect",{pid});if(owner!==token||view!=="kernel")return;
    $("inspect-stats").textContent=JSON.stringify({stats:result.stats,resident:result.resident},null,2);$("inspect-body").textContent="";$("inspect-pages").replaceChildren();
    for(const page of result.pages){const button=node("button","memory-page",`${page.space}/${page.key} / ${page.bytes}B / rev ${page.revision}`);button.addEventListener("click",async()=>{try{const data=await rpc("sys_mem_read",{pid,space:page.space,key:page.key});if(owner===token&&view==="kernel")$("inspect-body").textContent=data.content;}catch(error){toast(error.message);}});$("inspect-pages").append(button);}
    if(!result.pages.length)$("inspect-pages").append(node("div","empty-state","[ PRIVATE MEMORY EMPTY ]"));
  }catch(error){toast(error.message);}
});

// The orb is procedural: no downloaded assets, GPU libraries, or paid generation.
const canvas=$("orb"),ctx=canvas.getContext("2d");let width=600,height=409,dpr=1,orbFrame=0,lastOrbFrame=0;
function scheduleDraw(){cancelAnimationFrame(orbFrame);orbFrame=requestAnimationFrame(draw);}
const particles=Array.from({length:1000},(_,i)=>{const y=1-2*(i+.5)/1000,r=Math.sqrt(1-y*y),phi=i*Math.PI*(3-Math.sqrt(5));return{x:Math.cos(phi)*r,y,z:Math.sin(phi)*r,seed:i*.731};});
const resize=new ResizeObserver(entries=>{const r=entries[0].contentRect;width=r.width;height=r.height;dpr=Math.min(devicePixelRatio||1,2);canvas.width=width*dpr;canvas.height=height*dpr;scheduleDraw();});resize.observe(canvas);
function draw(now){if(!reducedMotion&&now-lastOrbFrame<32){orbFrame=requestAnimationFrame(draw);return;}lastOrbFrame=now;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);const time=reducedMotion?1:now*.00017,cx=width/2,cy=height*.44,radius=Math.min(width*(width<600?.29:.245),height*.22),active=busy?1.06:1+(voiceSession?.level||0)*.13;
  const glow=ctx.createRadialGradient(cx,cy,0,cx,cy,radius*1.9);glow.addColorStop(0,"#83eaff1b");glow.addColorStop(.4,"#31b8ef2b");glow.addColorStop(.7,"#1686b410");glow.addColorStop(1,"#1686b400");ctx.fillStyle=glow;ctx.fillRect(0,0,width,height);
  ctx.save();ctx.translate(cx,cy);ctx.rotate(-.23);ctx.strokeStyle="#437b9430";ctx.lineWidth=.6;ctx.beginPath();ctx.ellipse(0,0,radius*1.47,radius*.49,0,0,Math.PI*2);ctx.stroke();ctx.rotate(.56);ctx.strokeStyle="#71bfd34a";ctx.beginPath();ctx.ellipse(0,0,radius*1.33,radius*.67,0,.4,5.7);ctx.stroke();ctx.restore();
  for(let ring=0;ring<3;ring++){ctx.strokeStyle=ring===0?"#5495af30":"#38647c25";ctx.lineWidth=.6;ctx.beginPath();ctx.arc(cx,cy,radius*(1.22+ring*.16),time*(ring%2?1:-1)+.3,Math.PI*1.4+time*(ring%2?1:-1));ctx.stroke();}
  const projected=[];for(const p of particles){const a=time*.7;let x=p.x*Math.cos(a)+p.z*Math.sin(a),z=-p.x*Math.sin(a)+p.z*Math.cos(a),y=p.y;const tilt=.22;y=p.y*Math.cos(tilt)-z*Math.sin(tilt);z=p.y*Math.sin(tilt)+z*Math.cos(tilt);const wave=1+.028*Math.sin(p.seed+time*8)+.015*Math.sin(y*11+time*5);const scale=radius*wave*active;projected.push({x:cx+x*scale,y:cy+y*scale,z,alpha:.18+(z+1)*.33,size:.45+(z+1)*.48});}projected.sort((a,b)=>a.z-b.z);
  for(let i=0;i<projected.length;i++){const p=projected[i];if(i%3===0){for(let j=i+1;j<Math.min(i+16,projected.length);j++){const q=projected[j],dist=Math.hypot(p.x-q.x,p.y-q.y);if(dist<radius*.15){ctx.strokeStyle=`rgba(68,186,227,${.10*(1-dist/(radius*.15))*(p.z+1.3)})`;ctx.lineWidth=.45;ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.stroke();}}}ctx.fillStyle=`rgba(${p.z>.3?"160,241,255":"72,167,206"},${p.alpha})`;ctx.beginPath();ctx.arc(p.x,p.y,p.size,0,Math.PI*2);ctx.fill();}
  const core=ctx.createRadialGradient(cx-radius*.18,cy-radius*.2,0,cx,cy,radius);core.addColorStop(0,"#b4f4ff18");core.addColorStop(.5,"#48d9ff08");core.addColorStop(.9,"#65cfff08");core.addColorStop(1,"#8aeaff38");ctx.fillStyle=core;ctx.beginPath();ctx.arc(cx,cy,radius*active,0,Math.PI*2);ctx.fill();
  ctx.save();ctx.translate(cx,cy+radius*1.2);ctx.scale(1,.13);const shadow=ctx.createRadialGradient(0,0,0,0,0,radius);shadow.addColorStop(0,"#55ceff24");shadow.addColorStop(1,"#55ceff00");ctx.fillStyle=shadow;ctx.beginPath();ctx.arc(0,0,radius,0,Math.PI*2);ctx.fill();ctx.restore();
  if(!reducedMotion&&!document.hidden&&view==="jarvis")orbFrame=requestAnimationFrame(draw);
}
document.addEventListener("visibilitychange",()=>{if(!document.hidden){scheduleDraw();voiceSession?.resume();}else {cancelAnimationFrame(orbFrame);void voiceSession?.pause();}});scheduleDraw();
setInterval(()=>{$("clock").textContent=new Date().toLocaleTimeString("en-GB");},1000);
voiceSession = new VoiceSession({request:rpc,getState:()=>state,onStatus:renderVoicePhase,onError:toast,onSetup:voiceSetup,onWake:renderWake,isActive:()=>view==="jarvis"&&!document.hidden&&document.hasFocus()&&!document.querySelector("dialog[open]"),onTrap:()=>{void refresh();requireAuth();}});
refresh();setInterval(refresh,2000);
