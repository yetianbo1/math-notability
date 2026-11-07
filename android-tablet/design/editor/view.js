'use strict';
window.TabletEditor = {
  mount(root) {
    root.innerHTML = `
  <div class="workspace">
    <aside class="pages-panel" id="pages-panel" aria-label="页面列表">
      <label class="page-search"><svg><use href="#i-search"/></svg><input id="page-search" type="search" placeholder="搜索页面" aria-label="搜索页码或页面名称"></label>
      <div class="panel-heading"><select id="page-filter" aria-label="页面筛选"><option value="all">所有页面</option><option value="bookmarked">已加书签</option></select><span id="page-count">3 页</span><button class="icon-button" id="close-pages" aria-label="收起页面"><svg><use href="#i-pages"/></svg></button></div>
      <div class="note-info"><span class="mini-book">∑</span><div>学习手记<small>一点一滴，记录思考</small></div></div>
      <div id="page-list" class="page-list"></div>
      <button class="add-page" id="add-page"><svg><use href="#i-plus"/></svg>添加页面</button>
      <div class="sidebar-footer"><span class="local-dot"></span>保存在此设备<button id="about" aria-label="关于本软件">ⓘ</button></div>
    </aside>
    <main class="editor">
  <header class="app-header">
    <button class="icon-button library-button" id="library" aria-label="笔记库" title="笔记库"><svg><use href="#i-back"/></svg></button>
    <span class="brand-mark"><svg><use href="#i-pen"/></svg></span>
    <div class="document-heading"><button id="rename" class="document-title">未命名笔记 <span>⌄</span></button><span class="document-state" id="save-status">NTB · 自动保存</span></div>
    <div class="document-controls" role="toolbar" aria-label="笔记操作">
      <button id="undo" class="icon-button" aria-label="撤销" title="撤销 Ctrl+Z" disabled><svg><use href="#i-undo"/></svg></button>
      <button id="redo" class="icon-button" aria-label="回退" title="回退 Ctrl+Y" disabled><svg><use href="#i-redo"/></svg></button>
      <button id="more" class="icon-button" aria-label="更多笔记操作" title="更多" aria-expanded="false"><svg class="more-icon"><rect x="3" y="3" width="18" height="18" rx="3"/><use href="#i-more"/></svg></button>
      <button id="toggle-pages" class="icon-button" aria-label="展开或收起页面" title="页面" aria-expanded="false"><svg><use href="#i-copy"/></svg></button>
    </div>
      <div class="tool-dock" role="toolbar" aria-label="书写工具">
        <div class="tools">
          <button class="tool active" data-tool="pen" aria-label="画笔" title="画笔 B"><svg><use href="#i-pen"/></svg><span>画笔</span></button>
          <button class="tool" data-tool="highlight" aria-label="荧光笔" title="荧光笔 H"><svg><use href="#i-highlight"/></svg><span>荧光笔</span></button>
          <button class="tool" data-tool="eraser" aria-label="橡皮擦" title="橡皮擦 E"><svg><use href="#i-eraser"/></svg><span>橡皮擦</span></button>
          <button class="tool" data-tool="lasso" id="selection-tool" aria-label="选择" title="选择 S"><svg><use href="#i-lasso"/></svg><span>选择</span></button>
          <button class="tool" data-tool="image" aria-label="图片" title="图片 I"><svg><use href="#i-image"/></svg><span>图片</span></button>
          <button class="tool" data-tool="hand" aria-label="漫游" title="漫游 V"><svg><use href="#i-hand"/></svg><span>漫游</span></button>
        </div>
        <div class="tool-options">
          <div class="image-options" id="image-options" hidden><button id="insert-image" class="segment"><svg><use href="#i-plus"/></svg>插入图片</button><button id="lock-image" class="segment" disabled>锁定</button><button id="delete-image" class="segment" disabled><svg><use href="#i-trash"/></svg>删除</button><span id="image-status" role="status">点击图片，拖动或缩放</span></div>
          <div class="ink-options" id="ink-options"><div class="colors" aria-label="笔迹颜色"><button class="swatch selected" data-color="#273d55" style="--swatch:#273d55" aria-label="墨蓝"></button><button class="swatch" data-color="#111111" style="--swatch:#111111" aria-label="黑色"></button><button class="swatch" data-color="#428676" style="--swatch:#428676" aria-label="松绿色"></button><button class="swatch" data-color="#f39c18" style="--swatch:#f39c18" aria-label="橙色"></button><button class="swatch" data-color="#356ce6" style="--swatch:#356ce6" aria-label="蓝色"></button><button class="swatch" data-color="#da7062" style="--swatch:#da7062" aria-label="珊瑚红"></button><button class="swatch" data-color="#eccd66" style="--swatch:#eccd66" aria-label="黄色"></button><label class="custom-color" title="自定义颜色"><span>＋</span><input id="custom-color" type="color" value="#273d55" aria-label="自定义笔迹颜色"></label></div><span class="divider"></span><div class="width-presets" aria-label="笔画粗细"><button data-width="1.5" title="细" aria-label="细笔画"><i style="--dot:3px"></i></button><button data-width="2.5" class="chosen" title="中" aria-label="中等笔画"><i style="--dot:6px"></i></button><button data-width="5" title="粗" aria-label="粗笔画"><i style="--dot:10px"></i></button></div><button class="width-button" id="width-button" aria-label="自定义笔画粗细"><small id="width-label">2.5</small></button></div>
          <div class="selection-menu" id="selection-menu" hidden><button data-shape="lasso" class="chosen"><svg><use href="#i-lasso"/></svg>自由形状<span hidden>✓</span></button><button data-shape="rectangle"><svg><use href="#i-rect"/></svg>矩形<span hidden></span></button></div>
          <div class="eraser-options" id="eraser-options" hidden><div class="eraser-sizes" aria-label="橡皮擦大小"><button data-eraser-size="12" aria-label="小橡皮擦"><i style="--dot:5px"></i></button><button data-eraser-size="24" class="chosen" aria-label="中橡皮擦"><i style="--dot:12px"></i></button><button data-eraser-size="44" aria-label="大橡皮擦"><i style="--dot:21px"></i></button></div><span class="divider"></span><button data-erase-mode="whole" class="segment chosen"><svg><path d="M2 14c4-14 12 12 20-3"/></svg>完整笔迹</button><button data-erase-mode="partial" class="segment"><svg><path d="M2 14c3-10 7-1 10 0m5 0 4-3"/></svg>局部擦除</button></div>
          <div class="hand-options" id="hand-options" hidden><svg><use href="#i-hand"/></svg><span>拖动漫游 · 双指缩放</span><button id="fit-paper">适合宽度</button></div>
        </div>
      </div>
  </header>
      <div class="editor-topline" hidden><div class="breadcrumb"><span>学习手记</span><span class="crumb-slash">/</span><span id="page-label">01 · 向量与线性组合</span></div></div>
      <div class="selection-actions" id="selection-actions" hidden><span id="selection-count"></span><button id="delete-selection"><svg><use href="#i-trash"/></svg>删除</button><button id="deselect">取消选择</button></div>
      <div class="viewport" id="viewport"><div class="paper-space" id="paper-space"><div class="paper" id="paper"><canvas id="ink" width="900" height="1272"></canvas><canvas id="overlay" width="900" height="1272" aria-label="笔记书写画布"></canvas></div></div></div>
      <div class="canvas-footer"><span class="preview-label">NTB 笔记<span id="input-hint">画笔 · 鼠标 / 手写笔均可书写</span></span><div class="view-controls"><button id="prev-page" aria-label="上一页">‹</button><span id="page-position">1 / 3</span><button id="next-page" aria-label="下一页">›</button><span class="divider"></span><button id="zoom-out" aria-label="缩小">−</button><button id="zoom-fit" title="适合宽度">100%</button><button id="zoom-in" aria-label="放大">＋</button></div></div>
      <button id="exit-focus" class="focus-exit">退出专注</button>
    </main>
  </div>
  <div class="popover more-menu" id="more-menu" hidden>
    <nav class="file-actions menu-group" aria-label="文件操作"><button id="new-note"><svg><use href="#i-plus"/></svg>新建笔记</button><button id="open-file"><svg><use href="#i-folder"/></svg>打开笔记</button><button id="import-pdf"><span class="pdf-icon">PDF</span>导入 PDF</button><button id="export"><svg><use href="#i-export"/></svg>导出为…</button></nav>
    <div class="menu-group"><button id="quick-export"><span>快速导出<small>当前页 · PNG 图片</small></span><svg><use href="#i-export"/></svg></button></div>
    <div class="menu-group"><button id="paper-settings"><span>模板设置</span><svg><use href="#i-pages"/></svg></button><button id="settings"><span>书写与查看设置</span><svg><use href="#i-settings"/></svg></button><button class="focus-button" id="focus"><span>专注书写</span><span>↗</span></button></div>
    <div class="menu-group"><button id="note-details"><span>笔记信息<small id="note-details-title"></small></span><span>›</span></button></div>
    <div class="more-footer"><button id="menu-about" aria-label="关于本软件">ⓘ</button><span>NotabilityEditor</span><button id="exit-app">保存并退出</button></div>
  </div>
  <div class="popover page-context-menu" id="page-context-menu" hidden><button data-page-action="add"><svg><use href="#i-plus"/></svg>添加页面</button><button data-page-action="cut"><svg><use href="#i-eraser"/></svg>剪切页面</button><button data-page-action="copy"><svg><use href="#i-copy"/></svg>拷贝页面</button><button data-page-action="duplicate"><svg><use href="#i-pages"/></svg>复制页面</button><button data-page-action="paste" hidden><svg><use href="#i-plus"/></svg>粘贴页面</button><button data-page-action="template"><svg><use href="#i-pages"/></svg>纸张模板</button><button data-page-action="clear"><svg><use href="#i-eraser"/></svg>清除页面内容</button><button data-page-action="delete" class="danger"><svg><use href="#i-trash"/></svg>删除页面</button></div>
  <div class="popover export-menu" id="export-menu" hidden><span class="menu-label">导出当前笔记</span><button id="save-ntb">保存笔记 <small>.ntb</small></button><button id="export-png">当前页图片 <small>.png</small></button><button id="export-print">导出全部页面 PDF</button><p>编辑自动保存快照，返回主页时写入 NTB。</p></div>
<input type="file" id="image-input" accept="image/png,image/jpeg,image/webp" hidden>`;
  }
};
