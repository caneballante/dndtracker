'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { CaptureReliabilityController } = require('../capture-reliability.js');
const html = fs.readFileSync(require('node:path').join(__dirname, '../dnd-audio.html'), 'utf8').replace(/\r\n/g, '\n');
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };
function stream() { const track = { readyState: 'live', stop() { this.readyState='ended'; }, addEventListener() {} }; return { active:true, getTracks:()=>[track], getAudioTracks:()=>[track], addEventListener(){} }; }
function harness(options={}) {
  let now=1000, frames=0, timerId=0, starts=0; const timers=new Map(), rafs=new Map(), saved=[], events=[], rows=new Map(), buttons={};
  const button = id => buttons[id] ||= { disabled:false, addEventListener(_,fn){this.click=fn;} };
  class AudioContext { constructor(){this.state='running';} createMediaStreamSource(){return {connect(){}};} createAnalyser(){return {fftSize:1024,getByteTimeDomainData(a){a.fill(144);frames++;}};} close(){return Promise.resolve();} addEventListener(){} }
  const ctx=vm.createContext({ Blob, WeakMap, Uint8Array, Date, Math, Number, String,
    stream:stream(), recorder:null, recorderStarting:false, recorderStopReasons:new WeakMap(), chunkTimer:null,
    chunkMs:120000, baseMimeType:'audio/webm', sessionId:'1234567890123', chunkIndex:0, highestEmittedChunkIndex:-1,
    pendingChunkUploads:0, sessionInitPending:false, recordingSessionMeta:{}, deferTranscription:true,
    paused:false, stopRequested:false, recoveryAttempt:null, captureOperationId:0, selectedDeviceId:'',
    meterCtx:null,meterAnalyser:null,meterRaf:null,meterGeneration:0,micLevelBarEl:{style:{},setAttribute(){}},
    startBtn:button('start'),stopBtn:button('stop'),pauseBtn:button('pause'),resumeBtn:button('resume'),
    recordAudioOnlyEl:{checked:true},chunkCounterEl:{},
    localStorage:{getItem:key=>rows.get(key),setItem:(key,value)=>rows.set(key,value)},
    recordingQueueReady:Promise.resolve(),recordingQueue:{ready:true,highestIndex:()=>-1,
      checkpoint:options.checkpoint || (async()=>true),enqueue:async item=>{saved.push(item);return true;}},
    navigator:{mediaDevices:{getUserMedia:options.microphone || (async()=>stream())}},window:{AudioContext},
    localFetch:options.status || (async()=>({response:{ok:true},json:{ok:true,status:{chunks:[]}}})),
    attachCaptureStreamHandlers(){},pickMimeType:()=> 'audio/webm',setStatus(){},log(){},
    startCountdown(){},stopCountdown(){},stopChunkTimer(){ if(ctx.chunkTimer)timers.delete(ctx.chunkTimer);ctx.chunkTimer=null;},
    finalizeStoppedSession(){ctx.captureController.beginFinalization(ctx.highestEmittedChunkIndex);},finishPendingChunkUpload(){},
    setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);},
    requestAnimationFrame(fn){const id=++timerId;rafs.set(id,fn);return id;},cancelAnimationFrame(id){rafs.delete(id);},
    MediaRecorder:class { constructor(s){this.stream=s;this.state='inactive';this.mimeType='audio/webm';}start(){starts++;if(options.failStart?.(starts)||this.stream.active===false||this.stream.getTracks().every(t=>t.readyState==='ended'))throw new Error('Synthetic recorder start rejected');this.state='recording';if(!options.delayStart)this.onstart();}stop(){this.state='inactive';} }
  });
  for(const [a,b] of [['  function captureSignals()','  function primeCaptureAlert()'],['  function startMeter(s)','  // Convert recorded chunk to WAV'],['  function cancelPendingRecovery(reason)','  async function finishPendingFinalization()'],['  function startRecorderCycle()','  // START BUTTON HANDLER'],["  pauseBtn.addEventListener('click'",'  setStatus(\'idle\');\n  bindMicEvents();']]) {
    vm.runInContext(html.slice(html.indexOf(a),html.indexOf(b,html.indexOf(a))),ctx);
  }
  const capture = new CaptureReliabilityController({now:()=>now,signalProvider:()=>ctx.captureSignals(),sendDiagnostic:async(event,details)=>events.push({event,details}),sendHeartbeat:options.heartbeat || (async()=>({ok:true})),setInterval:()=>1,clearInterval(){}});
  ctx.captureController=capture;
  return {ctx,capture,events,timers,rafs,saved,rows,frames:()=>frames,advance:ms=>now+=ms,
    async init(){await capture.start({sessionId:ctx.sessionId,chunkIntervalMs:120000});capture.updateSaveQueue({ready:true});},
    async emit(rec=ctx.recorder){await rec.ondataavailable({data:new Blob(['synthetic audio'])});await flush();},
    rollover(){timers.get(ctx.chunkTimer).fn();},
  };
}

function finalizationHarness(h) {
 vm.runInContext(html.slice(html.indexOf('  function waitForPendingChunkUploads()'),html.indexOf('  function stopChunkTimer()')),h.ctx);
 h.ctx.uploadDrainWaiters=[];h.ctx.stopFinalizationPromise=null;h.ctx.pendingFinalizations={};h.ctx.persistFinalizations=()=>{};
 h.ctx.settlePendingFinalizations=async()=>{};
}

test('ended track before rollover preserves delayed tail, recovers sequentially and finalizes after replacement rollover',async()=>{
 const h=harness();await h.init();finalizationHarness(h);h.ctx.startRecorderCycle();
 const first=h.ctx.recorder;h.rollover();await h.emit(first);first.onstop();await flush();
 const old=h.ctx.recorder;h.ctx.stream.getTracks()[0].stop();h.ctx.stream.active=false;h.capture.observe();
 h.rollover();const writing=old.ondataavailable({data:new Blob(['synthetic interrupted tail'])});
 assert.doesNotThrow(()=>old.onstop());assert.equal(h.ctx.chunkIndex,2);
 await h.ctx.recoverCapture();await writing;await flush();await h.emit();
 assert.equal(h.ctx.sessionId,'1234567890123');assert.equal(h.capture.snapshot().recovery.stage,'durably_checkpointed');
 const replacement=h.ctx.recorder;h.rollover();h.capture.observe();await h.emit(replacement);replacement.onstop();await flush();
 assert.equal(h.ctx.chunkIndex,4);assert.equal(h.capture.snapshot().state,'recovered_with_gap');
 const last=h.ctx.recorder;h.ctx.stopBtn.click();await h.emit(last);last.onstop();await flush();await h.ctx.stopFinalizationPromise;
 assert.deepEqual(h.saved.map(x=>x.index),[0,1,2,3]);assert.equal(h.ctx.pendingFinalizations[h.ctx.sessionId].finalExpectedChunkIndex,3);
 assert.equal(await h.saved[1].blob.text(),'synthetic interrupted tail');
});

test('synchronous rejected start creates no durable index, retry uses the same unused candidate',async()=>{
 const h=harness({failStart:n=>n===1});await h.init();
 assert.throws(()=>h.ctx.startRecorderCycle(),/start rejected/);
 assert.equal(h.ctx.chunkIndex,0);assert.equal(Number(h.rows.get(`dungeontracker.next.${h.ctx.sessionId}`)||0),0);
 h.capture.interrupt('synthetic');await h.ctx.recoverCapture();await h.emit();assert.equal(h.ctx.chunkIndex,1);
 const active=h.ctx.recorder;h.ctx.stopBtn.click();await h.emit(active);active.onstop();await flush();assert.equal(h.saved[0].index,0);
});

test('accepted recorder without audio remains required at Stop and after browser restoration',async()=>{
 const h=harness({delayStart:true});await h.init();finalizationHarness(h);h.ctx.startRecorderCycle();
 assert.equal(h.rows.get(`dungeontracker.next.${h.ctx.sessionId}`),'1');
 const active=h.ctx.recorder;h.ctx.stopBtn.click();active.onstop();await flush();await h.ctx.stopFinalizationPromise;
 assert.equal(h.ctx.pendingFinalizations[h.ctx.sessionId].finalExpectedChunkIndex,0);assert.equal(h.saved.length,0);
 // A fresh page has no in-memory emitted index; the accepted reservation stays durable.
 h.ctx.chunkIndex=0;h.ctx.highestEmittedChunkIndex=-1;h.ctx.stopFinalizationPromise=null;
 await h.ctx.finalizeStoppedSession();assert.equal(h.ctx.pendingFinalizations[h.ctx.sessionId].finalExpectedChunkIndex,0);
});

test('rollover start rejection reports interruption and recovery retains the unused candidate',async()=>{
 const h=harness({failStart:n=>n===2});await h.init();h.ctx.startRecorderCycle();const old=h.ctx.recorder;
 h.rollover();await h.emit(old);assert.doesNotThrow(()=>old.onstop());await flush();
 assert.equal(h.ctx.chunkIndex,1);assert.equal(h.capture.snapshot().state,'interrupted');
 await h.ctx.recoverCapture();await h.emit();assert.equal(h.ctx.chunkIndex,2);assert.equal(h.capture.snapshot().state,'recovered_with_gap');
 assert.equal(h.rows.get(`dungeontracker.next.${h.ctx.sessionId}`),'2');await h.ctx.stopMeter();
});

test('repeated failed recovery starts allocate no phantom indexes and retain late old audio',async()=>{
 const h=harness({failStart:n=>n===2||n===3});await h.init();finalizationHarness(h);h.ctx.startRecorderCycle();const old=h.ctx.recorder;
 h.capture.interrupt('synthetic');await h.ctx.recoverCapture();assert.equal(h.capture.snapshot().state,'interrupted');assert.equal(h.ctx.chunkIndex,1);
 await h.ctx.recoverCapture();assert.equal(h.capture.snapshot().state,'interrupted');assert.equal(h.ctx.chunkIndex,1);
 await h.ctx.recoverCapture();assert.equal(h.ctx.chunkIndex,2);
 const late=old.ondataavailable({data:new Blob(['late original audio'])});old.onstop();await late;await flush();await h.emit();
 const current=h.ctx.recorder;h.ctx.stopBtn.click();await h.emit(current);current.onstop();await flush();await h.ctx.stopFinalizationPromise;
 assert.deepEqual(h.saved.map(x=>x.index),[0,1]);assert.equal(h.ctx.pendingFinalizations[h.ctx.sessionId].finalExpectedChunkIndex,1);
});

test('restored accepted reservation remains missing when recovered capture advances',async()=>{
 const h=harness();await h.init();finalizationHarness(h);h.rows.set(`dungeontracker.next.${h.ctx.sessionId}`,'1');
 h.capture.interrupt('page_reloaded_during_capture');await h.ctx.recoverCapture();await h.emit();const current=h.ctx.recorder;
 h.ctx.stopBtn.click();await h.emit(current);current.onstop();await flush();await h.ctx.stopFinalizationPromise;
 assert.deepEqual(h.saved.map(x=>x.index),[1]);assert.equal(h.ctx.pendingFinalizations[h.ctx.sessionId].finalExpectedChunkIndex,1);
 // Backend's contiguous barrier must still require unsaved accepted index 0.
});

test('delayed ordinary onstart after Stop cannot revive accepted capture or discard its late tail',async()=>{
 const h=harness({delayStart:true});await h.init();finalizationHarness(h);h.ctx.startRecorderCycle();const old=h.ctx.recorder;
 h.ctx.stopBtn.click();old.onstart();assert.equal(old.state,'inactive');assert.equal(h.ctx.stopRequested,true);
 const writing=old.ondataavailable({data:new Blob(['accepted late tail'])});old.onstop();await writing;await flush();await h.ctx.stopFinalizationPromise;
 assert.equal(h.saved.length,1);assert.equal(h.saved[0].index,0);assert.equal(await h.saved[0].blob.text(),'accepted late tail');
 assert.equal(h.ctx.pendingFinalizations[h.ctx.sessionId].finalExpectedChunkIndex,0);
});

for (const delay of [100,9000]) test(`delayed onstop ${delay} ms remains bounded and saves old audio`,async()=>{
 const h=harness();await h.init();h.ctx.startRecorderCycle();const old=h.ctx.recorder;h.rollover();h.advance(delay);h.capture.observe();await h.capture.heartbeat();assert.notEqual(h.capture.snapshot().state,'interrupted');
 await h.emit(old);old.onstop();await flush();assert.equal(h.ctx.recorder.state,'recording');assert.equal(h.saved.length,1);assert.equal(h.capture.snapshot().recorderTransition,null);
});
test('missing onstop alarms after ten seconds, without waiting for a two-minute watchdog',async()=>{
 const h=harness();await h.init();h.ctx.startRecorderCycle();h.rollover();h.advance(10000);h.capture.observe();assert.equal(h.capture.snapshot().interruptionReason,'recorder_rollover_stalled');
});
test('replacement constructed but missing onstart also times out',async()=>{
 const h=harness({delayStart:true});await h.init();h.ctx.startRecorderCycle();h.ctx.recorder.onstart();const old=h.ctx.recorder;h.rollover();await h.emit(old);old.onstop();h.advance(10000);h.capture.observe();assert.equal(h.capture.snapshot().interruptionReason,'recorder_rollover_stalled');
});
test('a real ended microphone is detected during expected rollover',async()=>{
 const h=harness();await h.init();h.ctx.startRecorderCycle();h.rollover();h.ctx.stream.getTracks()[0].stop();h.capture.observe();assert.equal(h.capture.snapshot().interruptionReason,'track_ended');
});
for(const reject of [false,true]) test(`meter replacement survives ${reject?'rejected':'delayed'} old context close and stale frames`,async()=>{
 const h=harness();await h.init();h.ctx.startMeter(h.ctx.stream);const old=h.ctx.meterCtx;const close=deferred();old.close=()=>close.promise;const stale=[...h.rafs.values()][0];
 h.ctx.startMeter(h.ctx.stream);const current=h.ctx.meterAnalyser;const raf=h.ctx.meterRaf;const before=h.frames();stale();assert.equal(h.frames(),before);
 if(reject)close.reject(new Error('synthetic close failure'));else close.resolve();await flush();assert.equal(h.ctx.meterAnalyser,current);assert.equal(h.ctx.meterRaf,raf);
 h.rafs.get(raf)();assert.ok(h.frames()>before);h.ctx.startRecorderCycle();h.rollover();const oldRecorder=h.ctx.recorder;await h.emit(oldRecorder);oldRecorder.onstop();assert.equal(h.ctx.meterAnalyser,current);
 await h.ctx.stopMeter();assert.equal(h.ctx.meterAnalyser,null);
});
test('rapid meter restarts cannot erase the last meter',async()=>{
 const h=harness();await h.init();const closures=[];for(let i=0;i<4;i++){h.ctx.startMeter(h.ctx.stream);const d=deferred();h.ctx.meterCtx.close=()=>d.promise;closures.push(d);}const current=h.ctx.meterAnalyser;for(const d of closures.slice(0,-1).reverse())d.resolve();await flush();assert.equal(h.ctx.meterAnalyser,current);closures.at(-1).resolve();await h.ctx.stopMeter();
});
test('recovery onstart remains pending until its own new durable checkpoint',async()=>{
 const cp=deferred(),h=harness({checkpoint:()=>cp.promise});await h.init();h.capture.interrupt('synthetic');await h.ctx.recoverCapture();
 assert.equal(h.capture.snapshot().state,'recovering');assert.equal(h.capture.snapshot().recovery.stage,'recorder_started');
 h.capture.chunkLocallySaved(999);assert.equal(h.capture.snapshot().state,'recovering');const writing=h.emit();await flush();cp.resolve(true);await writing;
 assert.equal(h.capture.snapshot().recovery.stage,'durably_checkpointed');assert.equal(h.capture.snapshot().state,'recovered_with_gap');assert.equal(h.ctx.recoveryAttempt,null);assert.equal(h.ctx.sessionId,'1234567890123');
 assert.ok(h.frames()>0);await h.ctx.stopMeter();
});
test('failed checkpoint never claims durable recovery and allows timeout/retry',async()=>{
 const h=harness({checkpoint:async()=>false});await h.init();h.capture.interrupt('synthetic');await h.ctx.recoverCapture();await h.emit();assert.equal(h.capture.snapshot().state,'recovering');h.advance(30000);h.capture.observe();assert.match(h.capture.snapshot().recovery.error,/checkpoint/);assert.equal(h.capture.snapshot().state,'interrupted');await h.ctx.recoverCapture();assert.equal(h.capture.snapshot().state,'recovering');await h.ctx.stopMeter();
});
test('microphone failure reports its stage and permits same-session retry',async()=>{
 let calls=0;const h=harness({microphone:async()=>{if(++calls===1)throw new Error('Synthetic device unavailable');return stream();}});await h.init();h.capture.interrupt('synthetic');await h.ctx.recoverCapture();assert.equal(h.capture.snapshot().recovery.stage,'microphone_reconnecting');assert.match(h.capture.snapshot().recovery.error,/device unavailable/);assert.equal(h.ctx.startBtn.disabled,true);await h.ctx.recoverCapture();await h.emit();assert.equal(calls,2);assert.equal(h.ctx.sessionId,'1234567890123');assert.equal(h.capture.snapshot().state,'recovered_with_gap');await h.ctx.stopMeter();
});
for(const control of ['stop','pause']) test(`${control} during microphone recovery rejects late streams and repeated clicks`,async()=>{
 const mic=deferred();let requests=0;const h=harness({microphone:()=>{requests++;return mic.promise;}});await h.init();h.capture.interrupt('synthetic');const recovering=h.ctx.recoverCapture();await flush();await h.ctx.recoverCapture();assert.equal(requests,1);
 h.ctx[control+'Btn'].click();const late=stream();mic.resolve(late);await recovering;await flush();assert.equal(late.getTracks()[0].readyState,'ended');assert.equal(h.ctx.recorder,null);assert.equal(h.ctx.recoveryAttempt,null);assert.equal(h.ctx.stream,null);assert.equal(h.ctx.sessionId,'1234567890123');
});
test('Stop during status lookup cannot later tear down or restart capture',async()=>{
 const status=deferred();let requests=0;const h=harness({status:()=>status.promise,microphone:async()=>{requests++;return stream();}});await h.init();h.capture.interrupt('synthetic');const pending=h.ctx.recoverCapture();await flush();h.ctx.stopBtn.click();status.resolve({response:{ok:true},json:{ok:true,status:{chunks:[]}}});await pending;assert.equal(requests,0);assert.equal(h.ctx.recorder,null);
});
test('local-server outage does not interrupt healthy recording or prevent same-session recovery',async()=>{
 const h=harness({status:async()=>{throw new Error('synthetic offline');},heartbeat:async()=>{throw new Error('synthetic offline');}});await h.init();h.ctx.startRecorderCycle();for(let i=0;i<4;i++){h.advance(5000);await h.emit();await h.capture.heartbeat();h.capture.observe();}assert.notEqual(h.capture.snapshot().state,'interrupted');h.capture.interrupt('synthetic');const next=h.ctx.chunkIndex;await h.ctx.recoverCapture();await h.emit();assert.equal(h.ctx.sessionId,'1234567890123');assert.ok(h.ctx.chunkIndex>next);assert.equal(h.capture.snapshot().state,'recovered_with_gap');await h.ctx.stopMeter();
});
test('delayed recovery onstart is not allowed to revive a stopped session',async()=>{
 const h=harness({delayStart:true});await h.init();h.capture.interrupt('synthetic');await h.ctx.recoverCapture();const abandoned=h.ctx.recorder;h.ctx.stopBtn.click();abandoned.onstart();assert.equal(abandoned.state,'inactive');assert.notEqual(h.capture.snapshot().state,'recovering');
 // Real final callback still preserves any pre-stop audio and completes local Stop.
 await h.emit(abandoned);abandoned.onstop();await flush();assert.equal(h.ctx.stream,null);assert.equal(h.capture.snapshot().sessionOpen,false);
});
test('microphone timeout rejects late permission results and permits retry',async()=>{
 const mic=deferred();const h=harness({microphone:()=>mic.promise});await h.init();h.capture.interrupt('synthetic');const pending=h.ctx.recoverCapture();await flush();const timeout=[...h.timers.values()].find(t=>t.delay===15000);timeout.fn();await pending;assert.match(h.capture.snapshot().recovery.error,/timed out/);assert.equal(h.capture.snapshot().recovery.stage,'microphone_reconnecting');const late=stream();mic.resolve(late);await flush();assert.equal(late.getTracks()[0].readyState,'ended');assert.equal(h.ctx.stream,null);
});
test('missing recovery onstart reports the recorder_starting stage',async()=>{
 const h=harness({delayStart:true});await h.init();h.capture.interrupt('synthetic');await h.ctx.recoverCapture();h.advance(15000);h.capture.observe();assert.equal(h.capture.snapshot().state,'interrupted');assert.equal(h.capture.snapshot().recovery.stage,'recorder_starting');h.ctx.recorder.onstart();assert.equal(h.ctx.recorder.state,'inactive');await h.ctx.stopMeter();
});
test('late durable-confirmation diagnostic cannot restart timers after Stop',async()=>{
 const diagnostic=deferred();let timersStarted=0;const h=harness();await h.init();h.capture.sendDiagnostic=async event=>event==='capture_recovered'?diagnostic.promise:{};h.capture.setInterval=()=>{timersStarted++;return 1;};h.capture.interrupt('synthetic');timersStarted=0;await h.ctx.recoverCapture();await h.emit();assert.equal(h.capture.snapshot().state,'recovered_with_gap');h.ctx.stopBtn.click();h.ctx.recorder.onstop();await flush();diagnostic.resolve({});await flush();assert.equal(h.capture.snapshot().sessionOpen,false);assert.equal(timersStarted,0);
});
test('meter context and analyser failures do not stop recovery audio capture',async()=>{
 for(const failure of ['context','analyser']){
  const h=harness();await h.init();if(failure==='context')h.ctx.window.AudioContext=class{constructor(){throw new Error('synthetic meter failure');}};
  else h.ctx.window.AudioContext.prototype.createAnalyser=()=>({fftSize:1024,getByteTimeDomainData(){throw new Error('synthetic analyser failure');}});
  h.capture.interrupt('synthetic');await h.ctx.recoverCapture();await h.emit();assert.equal(h.ctx.recorder.state,'recording');assert.equal(h.capture.snapshot().state,'recovered_with_gap');assert.ok(h.events.some(e=>e.event==='meter_error'));await h.ctx.stopMeter();
 }
});
test('interruption panel shows recovery progress, durable proof and the actual failed stage',async()=>{
 const h=harness();await h.init();const renderer=html.slice(html.indexOf('  function renderCaptureHealth(state)'),html.indexOf('  const CaptureController ='));
 for(const name of new Set(renderer.match(/capture\w+(?:El|Btn)/g)))h.ctx[name]={textContent:'',hidden:false,disabled:false,classList:{toggle(){}}};
 h.ctx.captureTime=()=> 'synthetic time';vm.runInContext(renderer,h.ctx);
 h.capture.interrupt('synthetic');h.capture.beginRecovery();h.capture.recoveryStage('microphone_reconnecting',7);h.ctx.renderCaptureHealth(h.capture.snapshot());
 assert.match(h.ctx.captureRecoveryProgressEl.textContent,/Microphone reconnecting/);assert.equal(h.ctx.captureInterruptionTitleEl.textContent,'RECOVERY IN PROGRESS');assert.match(h.ctx.captureGlobalWarningTextEl.textContent,/RECOVERY IN PROGRESS/);assert.equal(h.ctx.captureRecoverBtn.disabled,true);
 h.capture.recoveryStage('recorder_started',7);h.ctx.renderCaptureHealth(h.capture.snapshot());assert.match(h.ctx.captureRecoveryProgressEl.textContent,/waiting for new durable/);
 h.capture.chunkLocallySaved(7);h.ctx.renderCaptureHealth(h.capture.snapshot());assert.match(h.ctx.captureRecoveryProgressEl.textContent,/durably checkpointed/);assert.equal(h.ctx.captureInterruptionTitleEl.textContent,'RECOVERY CHECKPOINT SAVED');assert.equal(h.ctx.captureGlobalWarningEl.hidden,true);
 assert.match(h.ctx.captureRecoveryHistoryEl.textContent,/Requested.*Reconnecting microphone.*Recorder started.*Durable audio checkpoint/);
 h.capture.interrupt('synthetic again');h.capture.beginRecovery();h.capture.recoveryStage('microphone_reconnecting',8);h.capture.recoveryFailed(new Error('Synthetic device denied'));h.ctx.renderCaptureHealth(h.capture.snapshot());assert.match(h.ctx.captureRecoveryProgressEl.textContent,/Recovery failed at Microphone reconnecting.*Synthetic device denied/);assert.equal(h.ctx.captureRecoverBtn.disabled,false);
 h.capture.beginFinalization(1);h.ctx.renderCaptureHealth(h.capture.snapshot());assert.equal(h.ctx.captureInterruptionPanelEl.hidden,true);
});
test('browser final-data handler need not finish checkpointing before onstop starts the next recorder',async()=>{
 const cp=deferred();const h=harness({checkpoint:()=>cp.promise});await h.init();h.ctx.startRecorderCycle();const old=h.ctx.recorder;h.rollover();h.capture.observe();
 // Browsers do not await asynchronous dataavailable handlers before delivering stop.
 const writing=old.ondataavailable({data:new Blob(['synthetic tail'])});old.onstop();await flush();assert.notEqual(h.ctx.recorder,old);assert.equal(h.ctx.recorder.state,'recording');assert.equal(h.saved.length,0);
 cp.resolve(true);await writing;await flush();assert.equal(h.saved.length,1);assert.equal(await h.saved[0].blob.text(),'synthetic tail');assert.notEqual(h.capture.snapshot().state,'interrupted');
});
test('Stop during recovery waits for the old final callback before choosing the final chunk boundary',async()=>{
 const mic=deferred();const h=harness({microphone:()=>mic.promise});await h.init();h.ctx.startRecorderCycle();const old=h.ctx.recorder;
 // Execute the actual save drain barrier and finalizer rather than the fixture's Stop stub.
 vm.runInContext(html.slice(html.indexOf('  function waitForPendingChunkUploads()'),html.indexOf('  function stopChunkTimer()')),h.ctx);
 h.ctx.uploadDrainWaiters=[];h.ctx.stopFinalizationPromise=null;h.ctx.pendingFinalizations={};h.ctx.persistFinalizations=()=>{};
 let submitted;h.ctx.settlePendingFinalizations=async()=>{submitted=h.ctx.pendingFinalizations[h.ctx.sessionId];};
 h.capture.interrupt('synthetic');const pending=h.ctx.recoverCapture();await flush();h.ctx.stopBtn.click();await flush();assert.equal(submitted,undefined);assert.equal(h.ctx.startBtn.disabled,true);
 // Final data arrives only after Stop; it still belongs to the original session.
 const writing=old.ondataavailable({data:new Blob(['synthetic recovery tail'])});old.onstop();await writing;await flush();await h.ctx.stopFinalizationPromise;
 assert.equal(submitted.finalExpectedChunkIndex,0);assert.equal(h.saved.length,1);assert.equal(h.saved[0].sessionId,'1234567890123');assert.equal(await h.saved[0].blob.text(),'synthetic recovery tail');
 const late=stream();mic.resolve(late);await pending;assert.equal(late.getTracks()[0].readyState,'ended');
});
