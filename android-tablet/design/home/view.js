'use strict';
window.TabletHome = {
  mount(root) {
    root.innerHTML = `
  <section class="home-screen" id="home-screen" aria-label="笔记主页" hidden>
    <aside class="home-sidebar">
      <a class="home-brand" href="#home" aria-label="NotabilityEditor 主页"><span><svg><use href="#i-pen"/></svg></span><strong>NotabilityEditor<small>记录 · 思考 · 整理</small></strong></a>
      <p class="home-nav-label">笔记库</p>
      <nav class="home-nav" aria-label="笔记分类">
        <button data-library-filter="all" class="selected" aria-current="page"><svg><use href="#i-folder"/></svg>所有笔记<span id="home-total">1</span></button>
        <button data-library-filter="recent"><svg><path d="M4 6H1V3M2 6a10 10 0 1 1 0 12M12 6v6l4 2"/></svg>最近编辑</button>
        <button data-library-filter="bookmarked"><svg><use href="#i-bookmark"/></svg>含书签的笔记</button>
      </nav>
      <div class="home-category-heading"><span>笔记分类</span><button id="home-add-category" aria-label="新建笔记分类" title="新建分类"><svg><use href="#i-plus"/></svg></button></div>
      <div class="home-categories" id="home-categories" aria-label="自定义笔记分类"></div>
      <div class="home-local"><span class="local-dot"></span><div>保存在此设备<small>笔记文件格式：NTB</small></div></div>
    </aside>
    <main class="home-main">
      <header class="home-heading"><div><span class="home-eyebrow">我的笔记库</span><h1 id="home-title">所有笔记</h1><p>手写、批注与整理，从这里开始。</p></div><div class="home-actions"><button class="button" id="home-open"><svg><use href="#i-folder"/></svg>打开笔记</button><button class="button primary" id="home-new"><svg><use href="#i-plus"/></svg>新建笔记</button></div></header>
      <div class="home-search-row"><label class="home-search"><svg><use href="#i-search"/></svg><input type="search" id="home-search" placeholder="搜索笔记名称或分类" aria-label="搜索笔记名称或分类"></label><select id="home-sort" aria-label="笔记排序"><option value="updated">最近更新</option><option value="name">名称排序</option></select></div>
      <div class="home-section-heading"><h2 id="home-results-title">全部笔记</h2><span id="home-result-count"></span></div>
      <div class="home-notes" id="home-notes"></div>
      <div class="home-empty" id="home-empty" hidden><svg><use href="#i-search"/></svg><h2>没有找到笔记</h2><p>试试其他名称，或切换到“所有笔记”。</p></div>
      <footer class="home-footer">笔记文件仅支持 NTB · 支持手写笔与触控输入</footer>
    </main>
  </section>
`;
  }
};
