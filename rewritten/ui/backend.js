'use strict';
// Local processing transport. No document bytes are sent to an external service.
window.TabletBackend = (() => {
  let token;
  const queues = new Map();
  const ready = (async () => {
    if(location.protocol !== 'http:' || location.hostname !== '127.0.0.1') throw Error('请通过项目的 tablet_server.py 启动本地处理服务');
    const response = await fetch('/api/bootstrap');
    if(!response.ok)throw Error('本地处理服务未就绪');
    token=(await response.json()).token;
  })();
  // UI can load its homepage even if the transport is unavailable.
  ready.catch(()=>{});
  async function request(path, body, headers={}) {
    await ready;
    const response=await fetch(path,{method:'POST',headers:{'X-Tablet-Token':token,...headers},body});
    const value=await response.json();if(!response.ok)throw Error(value.error||'本地处理失败');return value;
  }
  const json=(path,value)=>request(path,JSON.stringify(value),{'Content-Type':'application/json'});
  function ordered(id, operation) {
    const next=(queues.get(id)||Promise.resolve()).catch(()=>{}).then(operation);
    queues.set(id,next);
    // An earlier operation must not remove a newer operation's queue tail.
    const release=()=>{if(queues.get(id)===next)queues.delete(id);};
    next.then(release,release);
    return next;
  }
  async function snapshot(note) {
    const frozen=structuredClone(note);
    return ordered(note.id,()=>json('/api/snapshot/'+frozen.id,{note:frozen,revision:frozen.backend.revision}));
  }
  async function save(note) {
    const frozen=structuredClone(note);
    return ordered(note.id,async()=>{
      // Persist the recovery journal before beginning the native writer.
      await json('/api/snapshot/'+frozen.id,{note:frozen,revision:frozen.backend.revision});
      return json('/api/save/'+frozen.id,{note:frozen,revision:frozen.backend.revision});
    });
  }
  async function download(id, format='ntb') {
    await ready;
    const response=await fetch(`/api/${format==='pdf'?'pdf':'download'}/${id}`);
    if(!response.ok){const error=await response.json();throw Error(error.error||'文件生成失败');}
    return response.blob();
  }
  function finish(note) {
    if(!token||!note?.backend?.dirty)return;
    // Small beacon signals a durable journal flush. The note itself has
    // already been cached synchronously and snapshotted while editing.
    navigator.sendBeacon('/api/finish',new Blob([JSON.stringify({token,id:note.id})],{type:'application/json'}));
  }
  return {ready,snapshot,save,download,finish,shutdown:()=>json('/api/shutdown',{}),
    open:file=>request('/api/open',file,{'X-Filename':encodeURIComponent(file.name)}),
    create:(title,background)=>json('/api/new',{title,background}),
    resume:id=>ordered(id,()=>json('/api/resume/'+id,{}))
  };
})();
