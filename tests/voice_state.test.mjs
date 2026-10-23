import test from 'node:test';
import assert from 'node:assert/strict';
import { VoiceSession } from '../agent_zero/web/voice.js';

// Deterministic lifecycle checks; no browser, audio hardware, or API service.
globalThis.window = {};
globalThis.FileReader = class {
  async readAsDataURL(blob) {
    this.result = `data:${blob.type};base64,${Buffer.from(await blob.arrayBuffer()).toString('base64')}`;
    this.onload();
  }
};

function fixture(respond) {
  const calls = [], statuses = [];
  const session = new VoiceSession({
    request: async (method, params) => { calls.push({method, params}); return respond(method, params); },
    getState: () => ({mode:'userspace',voice:{input_ready:true}}),
    onStatus: phase => statuses.push(phase), onError: message => { throw Error(message); },
    onSetup: () => { throw Error('Unexpected setup'); }, onTrap: () => {}, isActive: () => true,
  });
  session.wakeEnabled = true;
  return {session, calls, statuses};
}

function capture(session, kind='wake') {
  return {epoch:session.epoch,kind,chunks:[new Blob(['fixture'])],bytes:7,recorder:{mimeType:'audio/webm'},
    stream:{getTracks:()=>[]},source:{disconnect(){}},context:{state:'closed'}};
}

test('ambient nonmatch re-arms dormancy without a conversation request', async () => {
  const {session,calls} = fixture(() => ({awake:false,command:''}));
  await session.finish(capture(session));
  assert.equal(session.phase,'dormant');
  assert.deepEqual(calls.map(call=>call.method),['wake_probe']);
  await session.cancel();
});

test('wake phrase alone activates the next voice turn without model spend', async () => {
  const {session,calls} = fixture(() => ({awake:true,command:''}));
  await session.finish(capture(session));
  assert.equal(session.interactive,true);
  assert.equal(session.phase,'awake');
  assert.deepEqual(calls.map(call=>call.method),['wake_probe']);
  await session.cancel();
});

test('request in the same breath is sent only after a positive wake result', async () => {
  const {session,calls} = fixture(method => method==='wake_probe' ? {awake:true,command:'tell me a story'} : {message:'a story'});
  const spoken=[];
  session.speak=async text=>spoken.push(text);
  await session.finish(capture(session));
  assert.deepEqual(calls.map(call=>call.method),['wake_probe','chat']);
  assert.equal(calls[1].params.text,'tell me a story');
  assert.deepEqual(spoken,['a story']);
  await session.cancel();
});

test('go to sleep returns to dormant wake monitoring', async () => {
  const {session} = fixture(() => ({sleep:true,disable_wake:false}));
  session.interactive=true;
  await session.finish(capture(session,'turn'));
  assert.equal(session.interactive,false);
  assert.equal(session.wakeEnabled,true);
  assert.equal(session.phase,'dormant');
  await session.cancel();
});

test('stop listening disables wake monitoring', async () => {
  const {session} = fixture(() => ({sleep:true,disable_wake:true}));
  await session.finish(capture(session,'turn'));
  assert.equal(session.wakeEnabled,false);
  assert.equal(session.phase,'idle');
});

test('focus loss invalidates a late wake result and preserves opt-in for resume', async () => {
  let release, started;
  const waiting = new Promise(resolve=>{started=resolve;});
  const {session,calls} = fixture(method => method==='wake_probe' ? new Promise(resolve=>{release=resolve;started();}) : {});
  const finishing=session.finish(capture(session));
  await waiting;
  await session.pause();
  release({awake:true,command:'must not run'});
  await finishing;
  assert.equal(session.phase,'paused');
  assert.equal(session.wakeEnabled,true);
  assert.equal(calls.filter(call=>call.method==='chat').length,0);
  session.resume();
  assert.equal(session.phase,'dormant');
  await session.cancel();
});

test('explicit spoken authentication uses its credential route without conversation or speech', async () => {
  const {session,calls} = fixture(() => ({token:'fixture-token',expires_in:300}));
  session.wakeEnabled=false;
  let received;
  session.onAuthenticated=result=>{received=result;};
  session.speak=()=>{throw Error('Credentials must not be spoken back');};
  await session.finish(capture(session,'auth'));
  assert.deepEqual(calls.map(call=>call.method),['voice_auth']);
  assert.equal(received.token,'fixture-token');
  assert.equal(session.interactive,false);
  await session.cancel();
});

function localSpeech(t) {
  const oldWindow=globalThis.window;
  const oldSpeech=globalThis.speechSynthesis;
  const oldUtterance=globalThis.SpeechSynthesisUtterance;
  let spoken;
  const speech={getVoices:()=>[{localService:true,lang:'en-US'}],cancel(){},speak(value){spoken=value;}};
  globalThis.window={...oldWindow,speechSynthesis:speech};
  globalThis.speechSynthesis=speech;
  globalThis.SpeechSynthesisUtterance=class {constructor(text){this.text=text;}};
  t.after(()=>{globalThis.window=oldWindow;globalThis.speechSynthesis=oldSpeech;globalThis.SpeechSynthesisUtterance=oldUtterance;});
  return ()=>spoken;
}

test('manual microphone turn keeps a conversation active after speech without wake mode', async t => {
  const utterance=localSpeech(t);
  const {session,calls}=fixture(()=>({message:'First answer'}));
  session.wakeEnabled=false;
  await session.finish(capture(session,'turn'));
  assert.equal(session.phase,'speaking');
  assert.equal(utterance().text,'First answer');
  utterance().onend();
  assert.equal(session.interactive,true);
  assert.equal(session.wakeEnabled,false);
  assert.equal(session.phase,'awake');
  assert.notEqual(session.wakeTimer,null);
  assert.deepEqual(calls.map(call=>call.method),['voice_turn']);
  await session.cancel();
});

test('manual muted turn also arms follow-up instead of returning to standby', async () => {
  const {session}=fixture(()=>({message:'Muted answer'}));
  session.wakeEnabled=false;
  session.muted=true;
  await session.finish(capture(session,'turn'));
  assert.equal(session.interactive,true);
  assert.equal(session.phase,'awake');
  assert.notEqual(session.wakeTimer,null);
  await session.cancel();
});

test('manual conversation survives temporary focus pause without enabling wake detection', async () => {
  const {session}=fixture(()=>({}));
  session.wakeEnabled=false;
  session.interactive=true;
  await session.pause();
  assert.equal(session.phase,'paused');
  session.resume();
  assert.equal(session.phase,'awake');
  assert.equal(session.wakeEnabled,false);
  await session.cancel();
});

function fakeMicrophone(t) {
  const saved=new Map();
  const assign=(name,value)=>{saved.set(name,Object.getOwnPropertyDescriptor(globalThis,name));Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});};
  let opened=0,stopped=0;
  class Recorder {
    static isTypeSupported(){return true;}
    constructor(stream,options){this.mimeType=options.mimeType;this.state='inactive';}
    start(){this.state='recording';}
    stop(){this.state='inactive';queueMicrotask(()=>this.onstop?.());}
  }
  class Context {
    constructor(){this.state='running';}
    async resume(){}
    async close(){this.state='closed';}
    createAnalyser(){return {fftSize:512,getFloatTimeDomainData(samples){samples.fill(0);}};}
    createMediaStreamSource(){return {connect(){},disconnect(){}};}
  }
  assign('navigator',{mediaDevices:{async getUserMedia(){opened++;return {getTracks:()=>[{stop(){stopped++;}}]};}}});
  assign('MediaRecorder',Recorder);
  assign('AudioContext',Context);
  assign('window',{...globalThis.window,MediaRecorder:Recorder,AudioContext:Context});
  t.after(()=>{for(const[name,descriptor]of saved){if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete globalThis[name];}});
  return {get opened(){return opened;},get stopped(){return stopped;}};
}

test('automatic follow-up opens a turn capture even when wake detection is disabled', async t => {
  const media=fakeMicrophone(t);
  const {session}=fixture(()=>({}));
  session.wakeEnabled=false;
  session.interactive=true;
  try {
    await session.start({kind:'turn',automatic:true,followup:true});
    assert.equal(media.opened,1);
    assert.equal(session.phase,'listening');
    assert.equal(session.capture.kind,'turn');
    assert.equal(session.capture.followup,true);
    assert.equal(session.wakeEnabled,false);
  } finally {await session.cancel();}
  assert.equal(media.stopped,1);
});

test('manual conversation times out quietly after idle instead of uploading silence', {timeout:1500}, async t => {
  const media=fakeMicrophone(t);
  let now=0;
  t.mock.method(performance,'now',()=>now);
  const {session,calls}=fixture(()=>({}));
  session.wakeEnabled=false;
  session.interactive=true;
  try {
    await session.start({kind:'turn',automatic:true,followup:true});
    const idle=new Promise(resolve=>{const previous=session.onStatus;session.onStatus=phase=>{previous(phase);if(phase==='idle')resolve();};});
    now=7000;
    await idle;
    assert.equal(session.interactive,false);
    assert.equal(session.capture,null);
    assert.equal(session.wakeEnabled,false);
    assert.equal(media.stopped,1);
    assert.deepEqual(calls,[]);
  } finally {await session.cancel();}
});

test('wake-triggered reply also keeps the follow-up window after playback', async t => {
  const utterance=localSpeech(t);
  const {session}=fixture(method=>method==='wake_probe'?{awake:true,command:'question'}:{message:'Answer'});
  await session.finish(capture(session,'wake'));
  assert.equal(session.phase,'speaking');
  utterance().onend();
  assert.equal(session.phase,'awake');
  assert.equal(session.interactive,true);
  assert.equal(session.wakeEnabled,true);
  await session.cancel();
});
