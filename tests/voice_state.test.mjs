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
