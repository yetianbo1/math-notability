'use strict';
window.TabletLibrary = (() => {
  const W = 900, H = 1272, KEY = 'notability-editor-tablet-v4';
  const uid = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const blankPage = (background = 'grid') => ({ id: uid(), title: '空白页', background, lesson: null, strokes: [], images: [] });
  const stroke = (points, color = '#356ce6', width = 2.5) => ({ id: uid(), tool: 'pen', color, width, points: points.map(p => [p[0], p[1], .5]) });
  const arc = (cx, cy, rx, ry) => Array.from({ length: 81 }, (_, i) => [cx + rx * Math.cos(i / 80 * Math.PI * 2), cy + ry * Math.sin(i / 80 * Math.PI * 2)]);
  function demo() {
    const p = blankPage(); p.title = '向量与线性组合'; p.lesson = 1;
    p.strokes = [stroke([[110, 297], [170, 299], [236, 297], [309, 298]], '#eccd66', 15), stroke([[539, 685], [579, 665], [625, 639], [673, 603]], '#356ce6', 3), stroke([[649, 608], [673, 603], [666, 627]], '#356ce6', 3), stroke(arc(338, 875, 107, 32), '#da7062', 2), stroke([[115, 1026], [195, 1027], [281, 1025], [379, 1027]], '#428676', 2)];
    p.strokes[0].tool = 'highlight';
    const q = blankPage('lined'); q.title = '随堂练习'; q.lesson = 2;
    const r = blankPage('plain'); r.title = '自由思考';
    return { id: uid(), title: '线性代数 · 学习手记', pages: [p, q, r], updated: Date.now() };
  }
  function validateNote(n){
    if(!n||typeof n.id!=='string'||typeof n.title!=='string'||n.title.length>100||!Array.isArray(n.pages)||!n.pages.length||n.pages.length>500)throw Error('草稿结构不正确');
    let pointCount=0;
    for(const p of n.pages){
      if(typeof p.id!=='string'||typeof p.title!=='string'||p.title.length>100||!['grid','lined','plain','dotted'].includes(p.background)||![null,1,2].includes(p.lesson)||!Array.isArray(p.strokes)||p.strokes.length>20000||!Array.isArray(p.images)||p.images.length>30)throw Error('页面数据不正确');
      const ids=new Set();
      for(const s of p.strokes){if(typeof s.id!=='string'||ids.has(s.id)||!['pen','highlight'].includes(s.tool)||!/^#[0-9a-f]{6}$/i.test(s.color)||!Number.isFinite(s.width)||s.width<=0||s.width>2000||!Array.isArray(s.points)||!s.points.length||s.points.length>65535)throw Error('笔迹数据不正确');ids.add(s.id);pointCount+=s.points.length;if(pointCount>2000000)throw Error('笔迹采样数量超过当前上限');for(const q of s.points)if(!Array.isArray(q)||q.length!==3||q.some(v=>!Number.isFinite(v))||Math.abs(q[0])>100000||Math.abs(q[1])>100000||q[2]<0||q[2]>1)throw Error('笔迹坐标不正确');}
      for(const im of p.images)if(typeof im.data!=='string'||!/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(im.data)||im.data.length>8000000||![im.x,im.y,im.w,im.h].every(Number.isFinite)||im.w<=0||im.h<=0||im.w>10000||im.h>10000)throw Error('图片数据不正确');
    }
  }
  return {W,H,KEY,uid,blankPage,demo,validateNote,
    read() { return JSON.parse(localStorage.getItem(KEY)); },
    write(data) { localStorage.setItem(KEY, JSON.stringify(data)); }
  };
})();
