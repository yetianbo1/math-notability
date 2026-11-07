'use strict';
window.TabletEditor.start = () => {
  const $ = id => document.getElementById(id);
  const {W,KEY,uid,blankPage,demo,validateNote} = TabletLibrary;
  let H=TabletLibrary.H, snapshotTimer=0, saving=false;
  const ink = $('ink'), overlay = $('overlay'), ctx = ink.getContext('2d'), ox = overlay.getContext('2d');
  const viewport = $('viewport'), paper = $('paper'), space = $('paper-space');
  let settings = { penOnly: false, leftHand: false, toolbox: 'top' };
  let notes = [], note = {id:uid(),title:'未命名笔记',pages:[blankPage()],updated:Date.now()}, pageIndex = 0, tool = 'pen', shape = 'lasso', color = '#273d55', width = 2.5;
  let zoom = .85, autoFit = true, selected = new Set(), polygon = [], action = null, live = null, frame = 0, history = [], future = [];
  let eraseMode = 'whole', eraserSize = 24, pageClipboard = null, contextPageId = null;
  let penStyle = 'pen', selectedImage = -1;
  let libraryFilter = 'all', categories = [];
  const inkPrefs = { pen: { color: '#273d55', width: 2.5 }, pencil: { color: '#273d55', width: 1.5 }, highlight: { color: '#eccd66', width: 2.5 }, marker: { color: '#356ce6', width: 5 } };
  let persistTimer = 0, toastTimer = 0, pinch = null, touchGesture = false, activePenId = null;
  const contacts = new Map(), imageCache = new Map();
  const page = () => note.pages[pageIndex];
  let homeView, editorBaseline = null, lastDraftContent = null;
  function activeCategory(){return homeView.activeCategory();}
  function populateCategorySelect(id,value){homeView.populateCategorySelect(id,value);}
  function noteContent(n){return JSON.stringify({title:n.title,categoryId:n.categoryId||null,pages:n.pages});}
  function finishHome(){
    editorBaseline=null;closeFloatingMenus();if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});document.body.classList.remove('focus-mode');
    if($('modal').open)$('modal').close();homeView.show();
  }
  function replaceNote(updated){
    const index=notes.findIndex(n=>n.id===updated.id);if(index>=0)notes[index]=updated;else notes.push(updated);
    note=updated;note.backend.editSessionId=uid();editorBaseline=structuredClone(updated);lastDraftContent=noteContent(updated);pageIndex=Math.min(pageIndex,note.pages.length-1);
    updateHeader();switchPage(pageIndex);saveNow();
  }
  async function persistNTB(){
    if(!note.backend)throw Error('请先打开或新建 NTB 笔记');
    if(saving)return false;
    clearTimeout(snapshotTimer);cancelAction();saving=true;$('editor-root').inert=true;
    $('save-status').textContent='正在写入 NTB…';
    try{
      const updated=await TabletBackend.save(note);updated.categoryId=note.categoryId||null;
      replaceNote(updated);$('save-status').textContent='NTB · 已自动保存';return true;
    }catch(error){
      saveNow();$('save-status').textContent='快照已保留 · NTB 保存失败';$('save-status').dataset.error='true';toast(error.message);return false;
    }finally{saving=false;$('editor-root').inert=false;}
  }
  async function showHome(){
    if(saving)return;
    cancelAction();clearTimeout(persistTimer);clearTimeout(snapshotTimer);saveNow();
    if(!document.body.classList.contains('home-mode')&&note.backend&&noteContent(note)!==noteContent(editorBaseline)){
      if(!await persistNTB())return;
    }
    finishHome();
  }
  async function showEditor(next=note){
    if(saving)return;
    cancelAction();closeFloatingMenus();clearTimeout(snapshotTimer);
    if(!next.backend){toast('这份草稿尚未关联 NTB，请重新打开原文件');return;}
    saving=true;$('home-root').inert=true;
    try{
      let loaded=await TabletBackend.resume(next.id);
      if(next.backend.dirty&&next.backend.editToken!==loaded.backend.lastEditToken)loaded=await TabletBackend.save(next);
      loaded.categoryId=next.categoryId||null;const index=notes.findIndex(n=>n.id===next.id);if(index>=0)notes[index]=loaded;else notes.push(loaded);
      note=loaded;note.backend.editSessionId=uid();pageIndex=0;editorBaseline=structuredClone(note);lastDraftContent=noteContent(note);
      document.body.classList.remove('home-mode');$('home-screen').hidden=true;switchPage(0);saveNow();
      window.history.replaceState(null,'',`#note=${encodeURIComponent(note.id)}`);requestAnimationFrame(fit);
    }catch(error){toast(error.message);}
    finally{saving=false;$('home-root').inert=false;}
  }
  try {
    const saved = TabletLibrary.read();
    if (saved?.schema === 1 && Array.isArray(saved.notes) && saved.notes.length <= 100) {
      saved.notes.forEach(validateNote); notes = saved.notes;
      if (Array.isArray(saved.categories)) {
        const ids = new Set(), names = new Set();
        categories = saved.categories.slice(0,50).filter(c => {
          if (!c || typeof c.id !== 'string' || !c.id.length || c.id.length > 80 || typeof c.name !== 'string' || !c.name.trim() || c.name.length > 40 || ids.has(c.id) || names.has(c.name.trim().toLocaleLowerCase())) return false;
          ids.add(c.id); names.add(c.name.trim().toLocaleLowerCase()); c.name = c.name.trim(); return true;
        });
      }
      if (typeof saved.libraryFilter === 'string') libraryFilter = saved.libraryFilter;
      note = notes.find(n => n.id === saved.currentId) || notes[0] || note;
      settings.penOnly = saved.settings?.penOnly === true; settings.leftHand = saved.settings?.leftHand === true;
      settings.toolbox = saved.settings?.toolbox === 'bottom' ? 'bottom' : 'top';
      for(const key of Object.keys(inkPrefs)){
        const p=saved.toolPreferences?.ink?.[key];
        if(p&&/^#[0-9a-f]{6}$/i.test(p.color)&&Number.isFinite(p.width)&&p.width>=1&&p.width<=8)inkPrefs[key]={color:p.color,width:p.width};
      }
      const ep=saved.toolPreferences?.eraser;
      if([12,24,44].includes(ep?.size))eraserSize=ep.size;
      if(['whole','partial'].includes(ep?.mode))eraseMode=ep.mode;
      if(['lasso','rectangle'].includes(saved.toolPreferences?.selection?.shape))shape=saved.toolPreferences.selection.shape;
      if(['pen','pencil','marker'].includes(saved.toolPreferences?.penStyle))penStyle=saved.toolPreferences.penStyle;
      ({color,width}=inkPrefs.pen);
    }
  } catch { /* A damaged browser cache never prevents opening another NTB. */ }
  function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 3200); }
  function saveSoon() {
    // Commit each completed edit synchronously to the recovery cache. Disk
    // snapshots use a short debounce; pointer motion itself does no I/O.
    saveNow();clearTimeout(snapshotTimer);
    if(note.backend&&editorBaseline&&noteContent(note)!==noteContent(editorBaseline)){
      const frozen=structuredClone(note);
      snapshotTimer=setTimeout(async()=>{
        try{await TabletBackend.snapshot(frozen);if(note.id===frozen.id)$('save-status').textContent='快照 · 已自动保存';}
        catch(error){$('save-status').textContent='本地草稿已保留 · 后台快照失败';$('save-status').dataset.error='true';}
      },350);
    }
  }
  function saveNow() {
    if(note.backend&&editorBaseline){const content=noteContent(note);note.backend.dirty=content!==noteContent(editorBaseline);if(note.backend.dirty&&content!==lastDraftContent){note.backend.editToken=uid();lastDraftContent=content;}}
    note.updated=Date.now();
    try {
      TabletLibrary.write({schema:1,notes,categories,libraryFilter,currentId:notes.some(n=>n.id===note.id)?note.id:null,settings,toolPreferences:{ink:inkPrefs,penStyle,eraser:{size:eraserSize,mode:eraseMode},selection:{shape}}});
      $('save-status').textContent=note.backend?.dirty?'自动快照 · 待写入 NTB':'NTB · 已保存';$('save-status').dataset.error='false';return true;
    }catch{$('save-status').textContent='本地缓存失败 · 请勿关闭';$('save-status').dataset.error='true';return false;}
  }
  function removeNotebook(id) {
    const index=notes.findIndex(n=>n.id===id);if(index<0)return;
    cancelAction();notes.splice(index,1);
    if(note.id===id){
      note=notes[0]||{id:uid(),title:'未命名笔记',pages:[blankPage()],updated:Date.now()};
      pageIndex=0;pageClipboard=null;contextPageId=null;history=[];future=[];
      resetSelection();updateHistory();updateHeader();refreshThumbs();drawPage();
    }
    // Save the empty library too; the detached editor placeholder is never stored.
    clearTimeout(persistTimer);saveNow();
  }
  function updateHeader() {
    $('rename').replaceChildren(document.createTextNode(note.title + ' '));
    const chevron = document.createElement('span'); chevron.textContent = '⌄'; $('rename').append(chevron);
    $('note-details-title').textContent=note.title;
    document.querySelector('.breadcrumb>span:first-of-type').textContent=note.title;
    document.querySelector('.note-info>div').firstChild.textContent=note.title;
    $('page-count').textContent = `${note.pages.length} 页`;
    $('page-label').textContent = `${String(pageIndex + 1).padStart(2, '0')} · ${page().title}`;
    $('page-position').textContent = `${pageIndex + 1} / ${note.pages.length}`;
    $('prev-page').disabled = pageIndex === 0; $('next-page').disabled = pageIndex === note.pages.length - 1;
  }
  function drawBackground(c, p) {
    const pageHeight=p.viewHeight||TabletLibrary.H;
    c.fillStyle = '#ffffff'; c.fillRect(0, 0, W, pageHeight);
    if(p.backdrop){const img=imageFor(p.backdrop);if(img.complete&&img.naturalWidth)c.drawImage(img,0,0,W,pageHeight);return;}
    c.strokeStyle = '#dfe8ee'; c.lineWidth = .65;
    if(p.background==='dotted'){c.fillStyle='#a3b7d3';for(let y=28;y<pageHeight;y+=28)for(let x=0;x<W;x+=28){c.beginPath();c.arc(x,y,1.5,0,Math.PI*2);c.fill();}return;}
    if (p.background !== 'plain') {
      c.beginPath(); for (let y = 28; y < H; y += 28) { c.moveTo(0, y); c.lineTo(W, y); }
      if (p.background === 'grid') for (let x = 28; x < W; x += 28) { c.moveTo(x, 0); c.lineTo(x, H); }
      c.stroke();
    }
    if (!p.lesson) return;
    const text = (str, x, y, size = 20, fill = '#31465e', font = 'TabletUI, sans-serif') => { c.font = `${size}px ${font}`; c.fillStyle = fill; c.fillText(str, x, y); };
    text('STUDY NOTES  /  LINEAR ALGEBRA', 98, 80, 11, '#8c9eae');
    text(p.lesson === 1 ? '向量与线性组合' : '随堂练习', 96, 145, 36, '#293e56');
    text(p.lesson === 1 ? '01' : '02', 742, 145, 47, '#d7e0e8', 'Georgia, serif');
    c.fillStyle = '#d9e3ec'; c.fillRect(98, 174, 704, 1);
    if (p.lesson === 2) {
      text('动手算一算，再画出你的理解。', 100, 245, 21);
      text('1.  已知 u = (1, 2)，v = (3, 1)，求 2u + v。', 100, 325, 21);
      text('2.  向量 (2, 4) 与 (1, 2) 是否线性相关？', 100, 550, 21);
      text('3.  画一个图，解释你对「张成空间」的理解。', 100, 775, 21);
      text('留白，是给思考的空间。', 100, 1160, 15, '#a0adba'); return;
    }
    text('把抽象的概念，变成看得见的方向。', 99, 218, 17, '#8596a7');
    text('一、向量是什么？', 100, 291, 23);
    text('有大小、有方向的量，可以用一个箭头来表示。', 101, 342, 20);
    text('向量的相加，也可以看作一次「位移的接力」。', 101, 383, 20);
    c.fillStyle = '#f0f5f8'; c.fillRect(99, 431, 703, 330);
    c.strokeStyle = '#cedbe5'; c.lineWidth = 1.5; c.beginPath(); c.moveTo(180, 701); c.lineTo(180, 471); c.moveTo(158, 678); c.lineTo(750, 678); c.stroke();
    const arrow = (x1, y1, x2, y2, col) => { const a = Math.atan2(y2 - y1, x2 - x1); c.strokeStyle = col; c.lineWidth = 3; c.beginPath(); c.moveTo(x1,y1); c.lineTo(x2,y2); c.moveTo(x2-14*Math.cos(a-.4),y2-14*Math.sin(a-.4)); c.lineTo(x2,y2); c.lineTo(x2-14*Math.cos(a+.4),y2-14*Math.sin(a+.4)); c.stroke(); };
    c.fillStyle='#dee9fb'; c.beginPath();c.moveTo(180,678);c.lineTo(461,603);c.lineTo(641,500);c.lineTo(360,575);c.closePath();c.fill();
    c.setLineDash([6,7]); c.strokeStyle='#a6bbd6';c.lineWidth=1.5;c.beginPath();c.moveTo(461,603);c.lineTo(641,500);c.moveTo(360,575);c.lineTo(641,500);c.stroke();c.setLineDash([]);
    arrow(180,678,461,603,'#386ceb'); arrow(180,678,360,575,'#428676'); arrow(180,678,641,500,'#da7062');
    text('u', 326, 651, 25, '#386ceb', 'Georgia, serif'); text('v', 246, 580, 25, '#428676', 'Georgia, serif'); text('u + v', 475, 533, 24, '#da7062', 'Georgia, serif');
    text('平行四边形法则', 547, 721, 15, '#738da4');
    text('二、线性组合', 100, 819, 23);
    text('w = au + bv', 237, 883, 31, '#293e56', 'Georgia, serif');
    text('系数 a、b 改变时，终点会落在哪里？', 101, 952, 20);
    text('先画图，再思考「张成」的含义。', 101, 1020, 20, '#428676');
    text('灵感区  /  用笔画下你的想法', 101, 1132, 14, '#92a0ad');
    text('NOTABILITY EDITOR', 100, 1216, 10, '#b2bcc5'); text('01', 765, 1216, 12, '#a2b0be');
  }
  function imageFor(data) {
    if (!imageCache.has(data)) { const img = new Image(); imageCache.set(data, img); img.onload = () => { drawPage(); refreshThumbs(); if(document.body.classList.contains('home-mode'))homeView.refresh(); }; img.src = data; }
    return imageCache.get(data);
  }
  function drawStroke(c, s, emphasis = false) {
    const pts = s.points; if (!pts.length) return;
    c.save(); c.lineCap = 'round'; c.lineJoin = 'round'; c.strokeStyle = s.color; c.fillStyle = s.color;
    c.globalAlpha = s.opacity ?? (s.tool === 'highlight' ? .35 : s.brush === 'pencil' ? .7 : 1);
    if(s.filled){c.beginPath();c.moveTo(pts[0][0],pts[0][1]);for(const p of pts.slice(1))c.lineTo(p[0],p[1]);c.closePath();c.fill();c.restore();return;}
    if (emphasis) { c.shadowColor = '#3e79fa'; c.shadowBlur = 4; }
    if (s.tool === 'highlight') {
      // Draw a single translucent path so joins do not accumulate opacity.
      c.lineWidth = s.width; c.beginPath(); c.moveTo(pts[0][0],pts[0][1]); for (const p of pts.slice(1)) c.lineTo(p[0],p[1]);
      if (pts.length === 1) { c.beginPath();c.arc(pts[0][0],pts[0][1],s.width/2,0,Math.PI*2);c.fill(); } else c.stroke();
    } else {
      if (pts.length === 1) { c.beginPath(); c.arc(pts[0][0],pts[0][1],s.width/2,0,Math.PI*2); c.fill(); }
      for (let i=1;i<pts.length;i++) { c.lineWidth=s.brush==='marker'?s.width:s.width*(.65+((pts[i][2]??.5)+(pts[i-1][2]??.5))*.35); c.beginPath();c.moveTo(pts[i-1][0],pts[i-1][1]);c.lineTo(pts[i][0],pts[i][1]);c.stroke(); }
    }
    c.restore();
  }
  function render(c, p, highlightSelection = false) {
    c.clearRect(0,0,W,p.viewHeight||TabletLibrary.H); drawBackground(c,p);
    for (const im of p.images) { const img=imageFor(im.data);if(img.complete && img.naturalWidth)c.drawImage(img,im.x,im.y,im.w,im.h); }
    for (const s of p.strokes) drawStroke(c,s,highlightSelection && selected.has(s.id));
  }
  function drawPage() { render(ctx,page(),true); drawOverlay(); }
  function selectionBounds() {
    let x=Infinity,y=Infinity,r=-Infinity,b=-Infinity;
    for (const s of page().strokes) if (selected.has(s.id)) for (const p of s.points) {const pad=s.width/2+6;x=Math.min(x,p[0]-pad);y=Math.min(y,p[1]-pad);r=Math.max(r,p[0]+pad);b=Math.max(b,p[1]+pad);}
    return Number.isFinite(x)?{x,y,r,b}:null;
  }
  function drawOverlay() {
    ox.clearRect(0,0,W,H);
    if (live) drawStroke(ox,live);
    if (action?.kind==='select' && polygon.length) {
      ox.beginPath();ox.moveTo(...polygon[0]);for(const p of polygon.slice(1))ox.lineTo(...p);ox.closePath();ox.fillStyle='#386ceb0d';ox.fill();ox.strokeStyle='#4e86f2';ox.lineWidth=1.4/zoom;ox.setLineDash([5/zoom,4/zoom]);ox.stroke();ox.setLineDash([]);
    } else {
      const im=tool==='image'?page().images[selectedImage]:null;
      const b=im?{x:im.x,y:im.y,r:im.x+im.w,b:im.y+im.h}:selectionBounds(); if(!b)return;
      ox.fillStyle='#386ceb08';ox.fillRect(b.x,b.y,b.r-b.x,b.b-b.y);ox.strokeStyle='#4e86f2';ox.lineWidth=1.4/zoom;ox.setLineDash([5/zoom,4/zoom]);ox.strokeRect(b.x,b.y,b.r-b.x,b.b-b.y);ox.setLineDash([]);
      for (const [x,y] of [[b.x,b.y],[b.r,b.y],[b.x,b.b],[b.r,b.b]]){ox.beginPath();ox.arc(x,y,3.8/zoom,0,Math.PI*2);ox.fillStyle='#fff';ox.fill();ox.stroke();}
    }
  }
  function scheduleDraw(base = false) { if(base)needsBase=true;if(frame)return;frame=requestAnimationFrame(()=>{frame=0;if(needsBase){needsBase=false;drawPage();}else drawOverlay();}); }
  let needsBase = false;
  function refreshThumbs() {
    const oldScroll=$('page-list').scrollTop; $('page-list').replaceChildren();
    const query=$('page-search').value.trim().toLocaleLowerCase(), bookmarked=$('page-filter').value==='bookmarked';
    note.pages.forEach((p,i)=>{
      if((bookmarked&&!p.bookmarked)||(query&&!`${i+1} ${p.title}`.toLocaleLowerCase().includes(query)))return;
      const card=document.createElement('div');card.className=`page-card${i===pageIndex?' selected':''}`;
      const btn=document.createElement('button');btn.className='page-open';btn.setAttribute('aria-label',`第 ${i+1} 页：${p.title}`);btn.setAttribute('aria-current',i===pageIndex?'page':'false');
      const preview=document.createElement('div');preview.className='page-preview';const canvas=document.createElement('canvas');canvas.width=150;canvas.height=212;
      const tc=canvas.getContext('2d');tc.scale(150/W,212/(p.viewHeight||TabletLibrary.H));render(tc,p);preview.append(canvas);
      const meta=document.createElement('div');meta.className='page-meta';meta.textContent=String(i+1);btn.append(preview,meta);btn.onclick=()=>switchPage(i);card.append(btn);
      const bookmark=document.createElement('button');bookmark.className=`page-bookmark${p.bookmarked?' bookmarked':''}`;bookmark.setAttribute('aria-label',`${p.bookmarked?'取消':'添加'}第 ${i+1} 页书签`);bookmark.setAttribute('aria-pressed',String(!!p.bookmarked));bookmark.innerHTML='<svg><use href="#i-bookmark"/></svg>';bookmark.onclick=()=>documentChange(()=>p.bookmarked=!p.bookmarked);card.append(bookmark);
      const more=document.createElement('button');more.className='page-more';more.setAttribute('aria-label',`第 ${i+1} 页操作`);more.innerHTML='<svg><use href="#i-more"/></svg>';more.onclick=()=>openPageMenu(p.id,more);card.append(more);$('page-list').append(card);
    });
    if(!$('page-list').children.length){const empty=document.createElement('p');empty.className='empty-pages';empty.textContent='没有匹配的页面';$('page-list').append(empty);}
    $('page-list').scrollTop=oldScroll;
  }
  function resetSelection(){selected.clear();selectedImage=-1;polygon=[];$('selection-actions').hidden=true;syncImageOptions();drawOverlay();}
  function syncImageOptions(){
    const im=page().images[selectedImage];
    $('lock-image').disabled=$('delete-image').disabled=!im;
    $('lock-image').textContent=im?.locked?'解锁':'锁定';
    $('lock-image').setAttribute('aria-pressed',String(!!im?.locked));
    $('image-status').textContent=im?(im.locked?'已锁定 · 解锁后可移动':'拖动图片 · 右下角等比缩放'):'点击图片，拖动或缩放';
  }
  function showSelection(){ $('selection-actions').hidden=!selected.size;$('selection-count').textContent=`已选 ${selected.size} 段笔迹`; }
  function switchPage(i){if(i<0||i>=note.pages.length||action)return;pageIndex=i;H=page().viewHeight||TabletLibrary.H;ink.height=overlay.height=Math.ceil(H);paper.style.height=H+'px';ink.style.height=overlay.style.height=H+'px';resetSelection();history=[];future=[];updateHistory();updateHeader();drawPage();refreshThumbs();viewport.scrollTo(0,0);}
  function snapshot(){return JSON.stringify(page());}
  function commit(before){if(before===snapshot())return;history.push(before);if(history.length>40)history.shift();future=[];updateHistory();refreshThumbs();saveSoon();}
  function updateHistory(){$('undo').disabled=!history.length;$('redo').disabled=!future.length;}
  function restore(s){note.pages[pageIndex]=JSON.parse(s);resetSelection();updateHistory();drawPage();refreshThumbs();saveSoon();}
  function documentState(){return {kind:'document',note:structuredClone(note),pageIndex};}
  function restoreDocument(entry){const i=notes.findIndex(n=>n.id===note.id);note=structuredClone(entry.note);notes[i]=note;pageIndex=entry.pageIndex;resetSelection();updateHeader();updateHistory();drawPage();refreshThumbs();saveSoon();}
  function documentChange(change){if(action)return;const before=documentState();change();history.push(before);if(history.length>40)history.shift();future=[];resetSelection();updateHeader();updateHistory();drawPage();refreshThumbs();saveSoon();}
  function undo(){if(!history.length||action)return;const entry=history.pop();future.push(typeof entry==='string'?snapshot():documentState());typeof entry==='string'?restore(entry):restoreDocument(entry);}
  function redo(){if(!future.length||action)return;const entry=future.pop();history.push(typeof entry==='string'?snapshot():documentState());typeof entry==='string'?restore(entry):restoreDocument(entry);}
  function applyZoom(value, anchor){
    const old=zoom;zoom=Math.max(.25,Math.min(2.5,value));paper.style.transform=`scale(${zoom})`;space.style.width=`${W*zoom}px`;space.style.height=`${H*zoom}px`;$('zoom-fit').textContent=`${Math.round(zoom*100)}%`;
    if(anchor){viewport.scrollLeft=(viewport.scrollLeft+anchor.x)*zoom/old-anchor.x;viewport.scrollTop=(viewport.scrollTop+anchor.y)*zoom/old-anchor.y;} drawOverlay();
  }
  function fit(){autoFit=true;applyZoom(Math.min(1.4,(viewport.clientWidth-32)/W));}
  function local(e){const r=overlay.getBoundingClientRect();return [(e.clientX-r.left)/zoom,(e.clientY-r.top)/zoom,e.pointerType==='pen'&&e.pressure>0?e.pressure:.5];}
  function pointSegment(p,a,b){const dx=b[0]-a[0],dy=b[1]-a[1],d=dx*dx+dy*dy;const t=d?Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/d)):0;return Math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy);}
  function erase(p, previous=p){
    const result=[];
    for(const s of page().strokes){
      const radius=eraserSize/(2*zoom)+s.width/2;
      const hit=s.points.some((q,i)=>pointSegment(q,previous,p)<radius||(i>0&&pointSegment(p,s.points[i-1],q)<radius));
      if(!hit){result.push(s);continue;}if(eraseMode==='whole')continue;
      const dense=[s.points[0]];
      for(let i=1;i<s.points.length;i++){const a=s.points[i-1],b=s.points[i],n=Math.max(1,Math.ceil(Math.hypot(b[0]-a[0],b[1]-a[1])/Math.min(3,radius/2)));for(let j=1;j<=n;j++){const t=j/n;dense.push([a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t,a[2]+(b[2]-a[2])*t]);}}
      let run=[],first=true;
      const flush=()=>{if(run.length){result.push({...s,id:first?s.id:uid(),points:run});first=false;run=[];}};
      for(const q of dense){if(pointSegment(q,previous,p)<radius)flush();else run.push(q);}flush();
    }
    page().strokes=result;scheduleDraw(true);
  }
  function inside(p,poly){let yes=false;for(let i=0,j=poly.length-1;i<poly.length;j=i++){const a=poly[i],b=poly[j];if(((a[1]>p[1])!==(b[1]>p[1]))&&(p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]))yes=!yes;}return yes;}
  function selectPolygon(poly){
    const result=new Set();
    for(const s of page().strokes){let total=0,hit=0;const pts=s.points;if(pts.length===1){if(inside(pts[0],poly))result.add(s.id);continue;}
      // Arc-length weighting keeps sparse long strokes from dominating through a bounding-box hit.
      for(let i=1;i<pts.length;i++){const a=pts[i-1],b=pts[i],len=Math.hypot(b[0]-a[0],b[1]-a[1]);const n=Math.max(1,Math.ceil(len/4));for(let j=0;j<n;j++){const t=(j+.5)/n;total+=len/n;if(inside([a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t],poly))hit+=len/n;}}
      if(total?hit/total>=.6:inside(pts[0],poly))result.add(s.id);
    }return result;
  }
  function distanceContacts(){const p=[...contacts.values()];return Math.hypot(p[0].x-p[1].x,p[0].y-p[1].y);}
  function cancelAction(){if(action?.before&&['move','erase','image-move','image-scale'].includes(action.kind)){note.pages[pageIndex]=JSON.parse(action.before);} action=null;live=null;polygon=[];overlay.classList.remove('dragging');drawPage();}
  overlay.addEventListener('pointerdown',e=>{
    if(e.button!==0 && !(e.pointerType==='pen'&&e.button===5))return;
    if(e.pointerType==='touch'){
      // Do not let incidental palm contacts interrupt an in-progress pen stroke.
      if(activePenId!==null)return;
      contacts.set(e.pointerId,{x:e.clientX,y:e.clientY});overlay.setPointerCapture(e.pointerId);
      if(contacts.size===2){cancelAction();touchGesture=true;const ps=[...contacts.values()],r=viewport.getBoundingClientRect();pinch={distance:distanceContacts(),zoom,x:(ps[0].x+ps[1].x)/2-r.left,y:(ps[0].y+ps[1].y)/2-r.top,lastX:(ps[0].x+ps[1].x)/2,lastY:(ps[0].y+ps[1].y)/2};return;}
      if(touchGesture || action)return;
    } else if(action)return;
    if(e.pointerType==='pen')activePenId=e.pointerId;
    e.preventDefault();overlay.setPointerCapture(e.pointerId);closeFloatingMenus();
    const p=local(e), b=selectionBounds(); const effective=e.pointerType==='pen'&&(e.button===5||(e.buttons&32))?'eraser':tool;
    if(effective==='hand'||(e.pointerType==='touch'&&settings.penOnly)){action={kind:'pan',id:e.pointerId,x:e.clientX,y:e.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};overlay.classList.add('dragging');return;}
    if(effective==='image'){
      const current=page().images[selectedImage];
      const scale=current&&!current.locked&&Math.hypot(p[0]-current.x-current.w,p[1]-current.y-current.h)<=16/zoom;
      if(!scale)selectedImage=page().images.findLastIndex(im=>p[0]>=im.x&&p[0]<=im.x+im.w&&p[1]>=im.y&&p[1]<=im.y+im.h);
      const im=page().images[selectedImage];syncImageOptions();
      if(im&&!im.locked){action={kind:scale?'image-scale':'image-move',id:e.pointerId,start:p,before:snapshot(),original:{x:im.x,y:im.y,w:im.w,h:im.h}};overlay.classList.add('dragging');}
      drawOverlay();return;
    }
    if(effective==='lasso'||effective==='rectangle'){
      if(b&&p[0]>=b.x&&p[0]<=b.r&&p[1]>=b.y&&p[1]<=b.b){action={kind:'move',id:e.pointerId,start:p,before:snapshot(),original:page().strokes.filter(s=>selected.has(s.id)).map(s=>({id:s.id,points:s.points.map(p=>p.slice())}))};overlay.classList.add('dragging');}
      else{const previous=e.shiftKey?new Set(selected):new Set();resetSelection();polygon=[[p[0],p[1]]];action={kind:'select',id:e.pointerId,start:p,previous,shape:effective};}scheduleDraw();return;
    }
    action={kind:effective==='eraser'?'erase':'draw',id:e.pointerId,before:snapshot(),previous:p};
    if(action.kind==='erase')erase(p);else{resetSelection();live={id:uid(),tool:effective==='highlight'?'highlight':'pen',brush:effective==='highlight'?'pen':effective,color,width:effective==='highlight'?width*6:width,points:[p]};scheduleDraw();}
  });
  overlay.addEventListener('pointermove',e=>{
    if(e.pointerType==='touch'&&contacts.has(e.pointerId)){
      contacts.set(e.pointerId,{x:e.clientX,y:e.clientY});
      if(pinch&&contacts.size===2){const ps=[...contacts.values()],x=(ps[0].x+ps[1].x)/2,y=(ps[0].y+ps[1].y)/2;autoFit=false;applyZoom(pinch.zoom*distanceContacts()/pinch.distance,{x:pinch.x,y:pinch.y});viewport.scrollLeft-=x-pinch.lastX;viewport.scrollTop-=y-pinch.lastY;pinch.lastX=x;pinch.lastY=y;return;}
    }
    if(!action||action.id!==e.pointerId)return;e.preventDefault();
    if(action.kind==='pan'){viewport.scrollLeft=action.left+action.x-e.clientX;viewport.scrollTop=action.top+action.y-e.clientY;return;}
    const samples=e.getCoalescedEvents?.()||[],events=samples.length?samples:[e];
    for(const sample of events){const p=local(sample);
      if(action.kind==='draw'){const prev=live.points.at(-1);if(Math.hypot(p[0]-prev[0],p[1]-prev[1])>=.35/zoom)live.points.push(p);}
      else if(action.kind==='erase'){erase(p,action.previous);action.previous=p;}
      else if(action.kind==='image-move'||action.kind==='image-scale'){
        const im=page().images[selectedImage],o=action.original,dx=p[0]-action.start[0],dy=p[1]-action.start[1];
        if(action.kind==='image-move'){im.x=o.x+dx;im.y=o.y+dy;}
        else{const ratio=Math.max(24/Math.min(o.w,o.h),Math.min(10000/Math.max(o.w,o.h),1+(dx*o.w+dy*o.h)/(o.w*o.w+o.h*o.h)));im.w=o.w*ratio;im.h=o.h*ratio;}
        scheduleDraw(true);
      }
      else if(action.kind==='select'){
        if(action.shape==='rectangle'){const a=action.start;polygon=[[a[0],a[1]],[p[0],a[1]],[p[0],p[1]],[a[0],p[1]]];}
        else{const prev=polygon.at(-1);if(Math.hypot(p[0]-prev[0],p[1]-prev[1])>=1/zoom)polygon.push([p[0],p[1]]);}
      }else if(action.kind==='move'){const dx=p[0]-action.start[0],dy=p[1]-action.start[1];for(const original of action.original){const s=page().strokes.find(s=>s.id===original.id);s.points=original.points.map(q=>[q[0]+dx,q[1]+dy,q[2]]);}scheduleDraw(true);}
    }scheduleDraw();
  });
  function finishPointer(e,canceled=false){
    if(activePenId===e.pointerId)activePenId=null;
    contacts.delete(e.pointerId);if(touchGesture){if(!contacts.size){touchGesture=false;pinch=null;}return;}
    if(!action||action.id!==e.pointerId)return;
    if(canceled){cancelAction();return;}
    const a=action;
    if(a.kind==='draw'&&live){const p=local(e),last=live.points.at(-1);if(Math.hypot(p[0]-last[0],p[1]-last[1])>.1)live.points.push(p);page().strokes.push(live);live=null;}
    if(a.kind==='select'){
      selected=new Set([...a.previous,...(polygon.length>=3?selectPolygon(polygon):[])]);
      if(polygon.length<3){const p=local(e);const hit=[...page().strokes].reverse().find(s=>s.points.some((q,i)=>pointSegment(p,s.points[Math.max(0,i-1)],q)<7/zoom+s.width/2));if(hit)selected.add(hit.id);}
      showSelection();if(!selected.size)toast('未圈中笔迹，可以扩大圈选范围');
    }
    action=null;polygon=[];overlay.classList.remove('dragging');drawPage();if(a.before)commit(a.before);
  }
  overlay.addEventListener('pointerup',e=>finishPointer(e));overlay.addEventListener('pointercancel',e=>finishPointer(e,true));overlay.addEventListener('lostpointercapture',e=>{if(action?.id===e.pointerId)finishPointer(e,true);});
  overlay.addEventListener('contextmenu',e=>e.preventDefault());
  function syncInkOptions(){document.documentElement.style.setProperty('--ink-color',color);document.querySelectorAll('[data-color]').forEach(x=>x.classList.toggle('selected',x.dataset.color===color));document.querySelectorAll('[data-width]').forEach(x=>x.classList.toggle('chosen',Number(x.dataset.width)===width));$('width-label').textContent=width;$('custom-color').value=color;}
  function chooseInkColor(next){color=next;if(inkPrefs[tool])inkPrefs[tool].color=color;syncInkOptions();saveSoon();}
  function chooseWidth(next){width=next;if(inkPrefs[tool])inkPrefs[tool].width=width;syncInkOptions();saveSoon();}
  function setTool(next){
    if(next==='pen')next=penStyle;
    if(['pen','pencil','marker'].includes(next))penStyle=next;
    cancelAction();if(inkPrefs[tool])inkPrefs[tool]={color,width};tool=next;if(inkPrefs[tool])({color,width}=inkPrefs[tool]);
    document.querySelectorAll('[data-tool]').forEach(b=>{const active=b.id==='selection-tool'?['lasso','rectangle'].includes(tool):b.dataset.tool==='pen'?['pen','pencil','marker'].includes(tool):b.dataset.tool===tool;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
    $('ink-options').hidden=!inkPrefs[tool];$('selection-menu').hidden=!['lasso','rectangle'].includes(tool);$('eraser-options').hidden=tool!=='eraser';$('hand-options').hidden=tool!=='hand';$('image-options').hidden=tool!=='image';
    document.querySelector('[data-tool="pen"] use').setAttribute('href',`#i-${penStyle}`);
    overlay.classList.toggle('hand',tool==='hand');const names={pen:'画笔',pencil:'铅笔',marker:'标记笔',highlight:'荧光笔',eraser:'橡皮擦',lasso:'自由形状 · 画圈选取笔迹',rectangle:'矩形 · 拖动框选笔迹',image:'图片 · 插入、移动与缩放',hand:'漫游 · 拖动画布'};$('input-hint').textContent=names[tool]+(settings.penOnly?' · 手指漫游':' · 鼠标 / 手写笔均可操作');if(!['lasso','rectangle'].includes(tool))resetSelection();syncInkOptions();drawPage();saveSoon();
  }
  document.querySelectorAll('[data-tool]').forEach(b=>b.onclick=()=>setTool(b.id==='selection-tool'?shape:b.dataset.tool));
  function syncSelectionOptions(){document.querySelectorAll('[data-shape]').forEach(x=>{x.classList.toggle('chosen',x.dataset.shape===shape);x.setAttribute('aria-pressed',String(x.dataset.shape===shape));});$('selection-tool').querySelector('use').setAttribute('href',shape==='lasso'?'#i-lasso':'#i-rect');}
  document.querySelectorAll('[data-shape]').forEach(b=>b.onclick=()=>{shape=b.dataset.shape;syncSelectionOptions();setTool(shape);saveSoon();});
  document.querySelectorAll('[data-color]').forEach(b=>b.onclick=()=>chooseInkColor(b.dataset.color));$('custom-color').oninput=e=>chooseInkColor(e.target.value);
  document.querySelectorAll('[data-width]').forEach(b=>b.onclick=()=>chooseWidth(Number(b.dataset.width)));
  function syncEraserOptions(){document.querySelectorAll('[data-eraser-size]').forEach(x=>x.classList.toggle('chosen',Number(x.dataset.eraserSize)===eraserSize));document.querySelectorAll('[data-erase-mode]').forEach(x=>{x.classList.toggle('chosen',x.dataset.eraseMode===eraseMode);x.setAttribute('aria-pressed',String(x.dataset.eraseMode===eraseMode));});}
  document.querySelectorAll('[data-eraser-size]').forEach(b=>b.onclick=()=>{eraserSize=Number(b.dataset.eraserSize);syncEraserOptions();saveSoon();});
  document.querySelectorAll('[data-erase-mode]').forEach(b=>b.onclick=()=>{eraseMode=b.dataset.eraseMode;syncEraserOptions();saveSoon();});
  $('undo').onclick=undo;$('redo').onclick=redo;
  $('delete-selection').onclick=()=>{if(!selected.size)return;const before=snapshot();page().strokes=page().strokes.filter(s=>!selected.has(s.id));resetSelection();drawPage();commit(before);};
  $('deselect').onclick=()=>{resetSelection();drawPage();};
  let marginPan=null;
  viewport.addEventListener('pointerdown',e=>{
    const r=overlay.getBoundingClientRect();
    if(e.button!==0||action||marginPan||(e.clientX>=r.left&&e.clientX<r.right&&e.clientY>=r.top&&e.clientY<r.bottom))return;
    marginPan={id:e.pointerId,x:e.clientX,y:e.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};
    viewport.setPointerCapture(e.pointerId);viewport.classList.add('margin-dragging');e.preventDefault();e.stopPropagation();
  },true);
  viewport.addEventListener('pointermove',e=>{if(marginPan?.id!==e.pointerId)return;viewport.scrollLeft=marginPan.left+marginPan.x-e.clientX;viewport.scrollTop=marginPan.top+marginPan.y-e.clientY;e.preventDefault();e.stopPropagation();},true);
  function finishMargin(e){if(marginPan?.id!==e.pointerId)return;marginPan=null;viewport.classList.remove('margin-dragging');if(viewport.hasPointerCapture(e.pointerId))viewport.releasePointerCapture(e.pointerId);e.preventDefault();e.stopPropagation();}
  for(const type of ['pointerup','pointercancel','lostpointercapture'])viewport.addEventListener(type,finishMargin,true);
  $('prev-page').onclick=()=>switchPage(pageIndex-1);$('next-page').onclick=()=>switchPage(pageIndex+1);
  function togglePages(){document.body.classList.toggle('pages-hidden');$('toggle-pages').setAttribute('aria-expanded',String(!document.body.classList.contains('pages-hidden')));requestAnimationFrame(()=>{if(autoFit)fit();});}
  $('toggle-pages').onclick=togglePages;$('close-pages').onclick=togglePages;
  function changeFocus(enabled){
    const oldZoom=zoom, center={x:(viewport.scrollLeft+viewport.clientWidth/2)/zoom,y:(viewport.scrollTop+viewport.clientHeight/2)/zoom};
    document.body.classList.toggle('focus-mode',enabled);
    $('focus').querySelector('span').textContent=enabled?'退出全屏编辑':'全屏编辑';
    $('exit-focus').textContent='退出全屏';
    requestAnimationFrame(()=>{if(autoFit)fit();else applyZoom(oldZoom);viewport.scrollLeft=center.x*zoom-viewport.clientWidth/2;viewport.scrollTop=center.y*zoom-viewport.clientHeight/2;});
  }
  $('focus').onclick=async()=>{
    if(action||marginPan){toast('请结束当前手势后切换全屏');return;}
    closeFloatingMenus();const entering=!document.body.classList.contains('focus-mode');changeFocus(entering);
    try{if(entering)await document.documentElement.requestFullscreen();else if(document.fullscreenElement)await document.exitFullscreen();}
    catch{if(entering)toast('已进入专注布局，浏览器未开启全屏');}
  };
  document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement&&document.body.classList.contains('focus-mode'))changeFocus(false);});
  $('exit-focus').onclick=()=>$('focus').click();
  $('zoom-in').onclick=()=>{autoFit=false;applyZoom(zoom*1.15,{x:viewport.clientWidth/2,y:viewport.clientHeight/2});};
  $('zoom-out').onclick=()=>{autoFit=false;applyZoom(zoom/1.15,{x:viewport.clientWidth/2,y:viewport.clientHeight/2});};$('zoom-fit').onclick=fit;
  $('fit-paper').onclick=fit;
  viewport.addEventListener('wheel',e=>{if(e.ctrlKey){e.preventDefault();autoFit=false;const r=viewport.getBoundingClientRect();applyZoom(zoom*Math.exp(-e.deltaY*.004),{x:e.clientX-r.left,y:e.clientY-r.top});}},{passive:false});
  function modal(title,html){closeFloatingMenus();$('modal-title').textContent=title;$('modal-body').innerHTML=html;$('modal').showModal();}
  $('modal-close').onclick=()=>$('modal').close();$('modal').addEventListener('click',e=>{if(e.target===$('modal')){const r=$('modal').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('modal').close();}});
  function paperPicker(){return '<label class="form-label">纸张样式</label><div class="paper-options"><button class="paper-option chosen" data-paper="dotted"><span class="paper-sample dotted"></span>点状</button><button class="paper-option" data-paper="lined"><span class="paper-sample lined"></span>横线</button><button class="paper-option" data-paper="plain"><span class="paper-sample"></span>空白</button></div>';}
  function wirePaper(initial='dotted'){let choice=initial;document.querySelectorAll('[data-paper]').forEach(b=>{b.classList.toggle('chosen',b.dataset.paper===choice);b.onclick=()=>{choice=b.dataset.paper;document.querySelectorAll('[data-paper]').forEach(x=>x.classList.toggle('chosen',x===b));};});return()=>choice;}
  function createDialog(isPage=false){
    modal(isPage?'添加页面':'新建笔记',`<label class="form-label" for="note-name">${isPage?'页面':'笔记'}名称</label><input id="note-name" class="text-input" maxlength="60" value="${isPage?'空白页':'未命名笔记'}">${isPage?'':'<label class="form-label" for="new-note-category">笔记分类</label><select class="text-input" id="new-note-category"></select>'}${isPage?'<p class="modal-copy">新增页面沿用笔记的原生纸张模板。</p>':paperPicker()}<p id="create-error" class="form-error" role="alert"></p><div class="modal-actions"><button class="button" id="cancel-create">取消</button><button class="button primary" id="confirm-create">${isPage?'添加页面':'创建笔记'}</button></div>`);
    const choice=isPage?()=> 'plain':wirePaper();if(!isPage)populateCategorySelect('new-note-category',activeCategory());
    $('cancel-create').onclick=()=>$('modal').close();
    $('confirm-create').onclick=async()=>{
      const title=$('note-name').value.trim()||(isPage?'空白页':'未命名笔记');const button=$('confirm-create');button.disabled=true;
      try{
        if(isPage){
          if(!note.backend?.pageWriter)throw Error(note.backend?.pageError||'当前页面结构不能安全添加页面');
          const p=blankPage(({ruled:'lined',dotted:'dotted'})[note.backend.paperKind]||'plain');p.title=title;p.viewHeight=page().viewHeight||H;
          documentChange(()=>{note.pages.push(p);pageIndex=note.pages.length-1;});switchPage(pageIndex);
        }else{
          const categoryId=$('new-note-category').value||null;
          const created=await TabletBackend.create(title,choice());created.categoryId=categoryId;
          await showEditor(created);
        }
        saveSoon();$('modal').close();
      }catch(error){$('create-error').textContent=error.message;}
      finally{button.disabled=false;}
    };
  }
  $('new-note').onclick=()=>createDialog();$('add-page').onclick=()=>createDialog(true);
  $('rename').onclick=()=>{modal('重命名笔记','<label class="form-label" for="rename-input">笔记名称</label><input id="rename-input" class="text-input" maxlength="60"><div class="modal-actions"><button class="button primary" id="confirm-rename">保存名称</button></div>');$('rename-input').value=note.title;$('confirm-rename').onclick=()=>{note.title=$('rename-input').value.trim()||note.title;updateHeader();saveSoon();$('modal').close();};};
  $('width-button').onclick=()=>{modal('笔画粗细','<p class="modal-copy">画笔使用所选粗细，荧光笔使用六倍宽度。</p><label class="form-label" for="width-range">粗细 <span id="width-value"></span></label><input id="width-range" type="range" min="1" max="8" step="0.5" style="width:100%;accent-color:#386ceb"><div class="modal-actions"><button class="button primary" id="width-done">完成</button></div>');$('width-range').value=width;$('width-value').textContent=width;$('width-range').oninput=e=>{chooseWidth(Number(e.target.value));$('width-value').textContent=width;};$('width-done').onclick=()=>$('modal').close();};
  $('settings').onclick=()=>{
    modal('书写设置',`<label class="setting-row"><span>仅手写笔书写<small>开启后，单指拖动画布；双指缩放。<br>鼠标仍可用于书写。</small></span><input type="checkbox" class="switch" id="pen-only" ${settings.penOnly?'checked':''}></label><label class="setting-row"><span>左手布局<small>将页面列表移到左侧。</small></span><input type="checkbox" class="switch" id="left-hand" ${settings.leftHand?'checked':''}></label><label class="setting-row"><span>工具条位置<small>放在顺手的一侧，留出书写空间。</small></span><select id="toolbox-position" class="text-input" style="width:100px"><option value="top">顶部</option><option value="bottom">底部</option></select></label><p class="modal-copy">手写笔压感会影响画笔粗细。笔身橡皮按钮需设备与浏览器传递对应事件。</p><div class="modal-actions"><button class="button primary" id="settings-done">完成</button></div>`);
    $('pen-only').onchange=e=>{settings.penOnly=e.target.checked;setTool(tool);saveSoon();};
    $('left-hand').onchange=e=>{settings.leftHand=e.target.checked;document.body.classList.toggle('left-hand',settings.leftHand);saveSoon();};
    $('toolbox-position').value=settings.toolbox;$('toolbox-position').onchange=e=>{settings.toolbox=e.target.value;document.body.classList.toggle('toolbox-bottom',settings.toolbox==='bottom');saveSoon();};
    $('settings-done').onclick=()=>$('modal').close();
  };
  $('library').onclick=showHome;
  $('exit-app').onclick=async()=>{
    closeFloatingMenus();if(!await persistNTB())return;
    try{await TabletBackend.shutdown();finishHome();$('home-root').inert=true;modal('已安全退出','<p class="modal-copy">快照已写入 NTB，后台已关闭。现在可以关闭此页面。</p>');}
    catch(error){toast(error.message);}
  };
  $('about').onclick=()=>modal('关于 NotabilityEditor','<p class="modal-copy">本地 NTB 编辑工作台。支持真实 NTB 打开、新建、笔迹编辑、自动快照、保存与 PDF 导出。</p><p class="modal-copy">原生图片与文字保留在原文件事件中，当前作为受保护内容显示；可在其上批注。界面尚未打包为 Android 安装包。</p>');
  $('import-pdf').onclick=()=>modal('导入 PDF','<p class="modal-copy">PDF 导入入口已预留。当前尚未接入 PDF 导入处理接口。</p><p class="modal-copy">此处暂不读取 PDF；可先通过图片工具插入页面图片，在图片上体验书写。</p><div class="modal-actions"><button class="button primary" id="pdf-image">插入图片体验</button></div>');
  $('modal-body').addEventListener('click',e=>{if(e.target.id==='pdf-image'){$('modal').close();showEditor();$('image-input').click();}});
  $('export').onclick=()=>{const open=$('export-menu').hidden;closeFloatingMenus();$('export-menu').hidden=!open;};
  function download(blob,name){const a=document.createElement('a'),url=URL.createObjectURL(blob);a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);$('export-menu').hidden=true;}
  function safeName(){return note.title.replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').slice(0,60)||'笔记';}
  $('save-ntb').onclick=async()=>{
    closeFloatingMenus();if(await persistNTB()){
      try{const blob=await TabletBackend.download(note.id);downloadBlob(blob,note.title+'.ntb');toast('NTB 已保存并下载');}catch(error){toast(error.message);}
    }
  };
  function downloadBlob(blob,name){const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}

  $('export-png').onclick=()=>{const c=document.createElement('canvas');c.width=W;c.height=H;render(c.getContext('2d'),page());c.toBlob(blob=>{if(blob)download(blob,`${safeName()}-${pageIndex+1}.png`);});};
  $('export-print').onclick=async()=>{closeFloatingMenus();if(await persistNTB()){try{downloadBlob(await TabletBackend.download(note.id,'pdf'),note.title+'.pdf');}catch(error){toast(error.message);}}};
  $('insert-image').onclick=()=>$('image-input').click();
  $('lock-image').onclick=()=>{const im=page().images[selectedImage];if(!im||action)return;const before=snapshot();im.locked=!im.locked;syncImageOptions();drawOverlay();commit(before);};
  $('delete-image').onclick=()=>{if(selectedImage<0||!page().images[selectedImage]||action)return;const before=snapshot();page().images.splice(selectedImage,1);resetSelection();drawPage();commit(before);};
  $('image-input').onchange=async e=>{const f=e.target.files[0];e.target.value='';if(!f)return;if(!['image/png','image/jpeg','image/webp'].includes(f.type)||f.size>5000000){toast('请选择不超过 5 MB 的 PNG、JPEG 或 WebP 图片');return;}if(page().images.length>=30){toast('本页图片达到上限');return;}const target=page();try{const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(f);});const img=new Image();img.src=data;await img.decode();if(page()!==target){toast('页面已切换，请重新插入图片');return;}const before=snapshot(),w=Math.min(680,img.naturalWidth),h=w*img.naturalHeight/img.naturalWidth;const ratio=Math.min(1,1000/h);target.images.push({data,x:110,y:200,w:w*ratio,h:h*ratio});imageCache.set(data,img);if(tool==='image')selectedImage=target.images.length-1;syncImageOptions();drawPage();commit(before);toast('图片已插入，画笔和荧光笔均位于图片上方');}catch{toast('无法读取此图片');}};
  function closeFloatingMenus(){ $('more-menu').hidden=true;$('page-context-menu').hidden=true;$('export-menu').hidden=true;$('more').setAttribute('aria-expanded','false'); }
  $('more').onclick=()=>{const open=$('more-menu').hidden;closeFloatingMenus();$('more-menu').hidden=!open;$('more').setAttribute('aria-expanded',String(open));};
  $('page-search').oninput=refreshThumbs;$('page-filter').onchange=refreshThumbs;
  $('menu-about').onclick=()=>$('about').click();$('note-details').onclick=()=>$('rename').click();
  $('quick-export').onclick=()=>{closeFloatingMenus();$('export-png').click();};
  function paperDialog(index){if(note.backend){toast('原生纸张模板沿用 NTB 设置，请在新建笔记时选择');return;}const target=note.pages[index];modal('纸张模板',`<p class="modal-copy">更改纸张样式，保留页面内容。</p>${paperPicker()}<div class="modal-actions"><button class="button primary" id="apply-paper">应用模板</button></div>`);const choice=wirePaper(target.background);$('apply-paper').onclick=()=>{documentChange(()=>target.background=choice());$('modal').close();};}
  $('paper-settings').onclick=()=>paperDialog(pageIndex);
  function openPageMenu(id,anchor){
    closeFloatingMenus();contextPageId=id;const menu=$('page-context-menu');menu.querySelector('[data-page-action="paste"]').hidden=!pageClipboard;menu.hidden=false;
    const a=anchor.getBoundingClientRect(),r=menu.getBoundingClientRect();menu.style.left=`${Math.max(12,Math.min(innerWidth-r.width-12,a.left-r.width+30))}px`;menu.style.top=`${Math.max(82,Math.min(innerHeight-r.height-12,a.top-r.height/2))}px`;
  }
  function duplicatePage(source){const p=structuredClone(source);p.id=uid();p.strokes.forEach(s=>s.id=uid());return p;}
  function removePage(index){note.pages.splice(index,1);if(pageIndex>index)pageIndex--;pageIndex=Math.min(pageIndex,note.pages.length-1);}
  document.querySelectorAll('[data-page-action]').forEach(button=>button.onclick=()=>{
    const index=note.pages.findIndex(p=>p.id===contextPageId);if(index<0)return;const target=note.pages[index],kind=button.dataset.pageAction;closeFloatingMenus();
    if(kind==='add'){createDialog(true);return;}if(kind==='template'){paperDialog(index);return;}
    if(kind==='copy'){pageClipboard=structuredClone(target);toast('页面已拷贝，可在页面菜单中粘贴');return;}
    if(kind==='duplicate'||kind==='paste'){
      if(target.backdrop||(kind==='paste'&&pageClipboard?.backdrop)){toast('原生页面复制尚未接入，请添加空白页');return;}
      if(note.pages.length>=500){toast('当前每份笔记最多 500 页');return;}
      const source=kind==='paste'?pageClipboard:target;if(!source)return;documentChange(()=>{note.pages.splice(index+1,0,duplicatePage(source));pageIndex=index+1;});return;
    }
    if(kind==='cut'){
      if(note.pages.length<=1){toast('至少保留一页，可以先添加页面');return;}
      pageClipboard=structuredClone(target);documentChange(()=>removePage(index));toast('页面已剪切，可粘贴或撤销');return;
    }
    if(kind==='delete'&&note.pages.length<=1){toast('至少保留一页，可以清除页面内容');return;}
    if(kind==='delete'&&!note.backend?.pageWriter){toast(note.backend?.pageError||'当前页面不能安全删除');return;}
    if(kind==='clear'&&target.protectedBackground){toast('本页含原生图片或文字，请仅使用橡皮擦清除笔迹');return;}
    const deleting=kind==='delete';modal(deleting?'删除页面':'清除页面内容',`<p class="modal-copy">${deleting?'删除这张页面？':'清除此页的笔迹、图片与示例内容？纸张样式会保留。'}可通过撤销恢复。</p><div class="modal-actions"><button class="button" id="cancel-page-action">取消</button><button class="button primary" id="confirm-page-action">${deleting?'删除页面':'清除内容'}</button></div>`);
    $('cancel-page-action').onclick=()=>$('modal').close();$('confirm-page-action').onclick=()=>{documentChange(()=>{if(deleting)removePage(index);else{target.strokes=[];target.images=[];target.lesson=null;}});$('modal').close();};
  });
  document.addEventListener('click',e=>{
    if(!e.target.closest('#more')&&!e.target.closest('#more-menu')){$('more-menu').hidden=true;$('more').setAttribute('aria-expanded','false');}
    if(!e.target.closest('.page-more')&&!e.target.closest('#page-context-menu'))$('page-context-menu').hidden=true;
    if(!e.target.closest('#export')&&!e.target.closest('#export-menu'))$('export-menu').hidden=true;
  });
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){if(action)cancelAction();else if(document.body.classList.contains('focus-mode')){changeFocus(false);if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});}else{resetSelection();drawPage();}closeFloatingMenus();return;}
    if($('modal').open||document.body.classList.contains('home-mode')||['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName))return;
    if(e.ctrlKey||e.metaKey){if(e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo();}if(e.key.toLowerCase()==='y'){e.preventDefault();redo();}if(e.key.toLowerCase()==='s'){e.preventDefault();persistNTB();}return;}
    const shortcuts={b:'pen',p:'pen',n:'pencil',m:'marker',h:'highlight',e:'eraser',s:shape,l:shape,i:'image',v:'hand'};if(shortcuts[e.key.toLowerCase()])setTool(shortcuts[e.key.toLowerCase()]);if(e.key==='Delete'||e.key==='Backspace'){if(tool==='image'&&selectedImage>=0){e.preventDefault();$('delete-image').click();}else if(selected.size){e.preventDefault();$('delete-selection').click();}}
  });
  window.addEventListener('resize',()=>{closeFloatingMenus();if(autoFit)fit();});window.addEventListener('beforeunload',saveNow);
  window.addEventListener('pagehide',()=>{saveNow();TabletBackend.finish(note);});
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&note.backend?.dirty){clearTimeout(snapshotTimer);saveNow();TabletBackend.snapshot(structuredClone(note)).catch(()=>{});}});
  TabletFiles.connect({modal,toast,closeFloatingMenus,acceptNote:async imported=>{notes.push(imported);saveNow();await showEditor(imported);}});
  const libraryState = {
    get notes(){return notes;}, get note(){return note;},
    get categories(){return categories;}, set categories(value){categories=value;},
    get libraryFilter(){return libraryFilter;}, set libraryFilter(value){libraryFilter=value;}
  };
  homeView=TabletHome.connect({state:libraryState,uid,saveNow,removeNotebook,showEditor,showHome,createDialog,render,modal,updateHeader,openFile:()=>$('open-file').click()});
  document.body.classList.toggle('left-hand',settings.leftHand);document.body.classList.toggle('toolbox-bottom',settings.toolbox==='bottom');document.body.classList.add('pages-hidden');
  syncSelectionOptions();syncEraserOptions();updateHeader();refreshThumbs();drawPage();setTool('pen');
  // Launch always begins in home. Opening or creating a notebook enters the editor.
  showHome();
};
