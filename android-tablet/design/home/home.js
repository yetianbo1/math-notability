'use strict';
window.TabletHome.connect = api => {
  const $ = id => document.getElementById(id);
  const {state,uid,saveNow,showEditor,createDialog,render,modal,updateHeader} = api;
  function categoryFor(n) { return state.categories.find(c => c.id === n.categoryId); }
  function activeCategory() { return state.categories.find(c => 'category:' + c.id === state.libraryFilter)?.id || null; }
  function populateCategorySelect(id, selected) {
    const select = $(id); select.replaceChildren(new Option('未分类', ''));
    for (const c of state.categories) select.add(new Option(c.name, c.id));
    select.value = state.categories.some(c => c.id === selected) ? selected : '';
  }
  function renderCategories() {
    const root = $('home-categories'); root.replaceChildren();
    for (const c of [{id:'', name:'未分类'}, ...state.categories]) {
      const row = document.createElement('div'); row.className = 'home-category-row';
      const button = document.createElement('button'), name = document.createElement('span'), count = document.createElement('span');
      const filter = c.id ? 'category:' + c.id : 'uncategorized';
      button.dataset.categoryFilter = filter; button.className = 'category-filter';
      name.textContent = c.name; count.textContent = state.notes.filter(n => c.id ? n.categoryId === c.id : !categoryFor(n)).length;
      count.className = 'category-count'; button.append(name, count);
      button.classList.toggle('selected', state.libraryFilter === filter);
      if (state.libraryFilter === filter) button.setAttribute('aria-current', 'page');
      button.onclick = () => { state.libraryFilter = filter; $('home-search').value = ''; refreshHome(); saveNow(); };
      row.append(button);
      if (c.id) {
        const more = document.createElement('button'); more.className = 'category-more'; more.textContent = '···'; more.setAttribute('aria-label', '管理分类：' + c.name);
        more.onclick = () => categoryDialog(c); row.append(more);
      }
      root.append(row);
    }
  }
  function categoryDialog(category = null) {
    modal(category ? '管理分类' : '新建分类', `<label class="form-label" for="category-name">分类名称</label><input class="text-input" id="category-name" maxlength="40" placeholder="例如：课程笔记、工作、生活"><p id="category-error" class="form-error" role="alert"></p><div class="modal-actions">${category ? '<button class="button danger" id="delete-category">删除分类</button>' : ''}<button class="button" id="cancel-category">取消</button><button class="button primary" id="save-category">保存分类</button></div>`);
    $('category-name').value = category?.name || '';
    $('cancel-category').onclick = () => $('modal').close();
    $('save-category').onclick = () => {
      const name = $('category-name').value.trim();
      const error = !name ? '请输入分类名称' : state.categories.some(c => c !== category && c.name.toLocaleLowerCase() === name.toLocaleLowerCase()) ? '这个分类名称已存在' : !category && state.categories.length >= 50 ? '最多创建 50 个分类' : '';
      if (error) { $('category-error').textContent = error; return; }
      if (category) category.name = name;
      else { category = {id: uid(), name}; state.categories.push(category); state.libraryFilter = 'category:' + category.id; $('home-search').value = ''; }
      saveNow(); refreshHome(); $('modal').close();
    };
    if (category) $('delete-category').onclick = () => {
      modal('删除分类', '<p class="modal-copy">分类中的笔记会保留，并移至“未分类”。</p><div class="modal-actions"><button class="button" id="cancel-delete-category">取消</button><button class="button danger" id="confirm-delete-category">删除分类</button></div>');
      $('cancel-delete-category').onclick = () => $('modal').close();
      $('confirm-delete-category').onclick = () => {
        for (const n of state.notes) if (n.categoryId === category.id) n.categoryId = null;
        state.categories = state.categories.filter(c => c.id !== category.id); state.libraryFilter = 'uncategorized'; $('home-search').value = '';
        saveNow(); refreshHome(); $('modal').close();
      };
    };
  }
  function manageNotebook(n) {
    modal('管理笔记', '<label class="form-label" for="library-note-name">笔记名称</label><input class="text-input" id="library-note-name" maxlength="60"><label class="form-label" for="note-category-select">笔记分类</label><select class="text-input" id="note-category-select"></select><div class="modal-actions"><button class="button danger" id="delete-notebook">删除笔记</button><button class="button" id="cancel-note-details">取消</button><button class="button primary" id="save-note-details">保存</button></div>');
    $('library-note-name').value = n.title; populateCategorySelect('note-category-select', n.categoryId);
    $('delete-notebook').onclick=()=>confirmDeleteNotebook(n);
    $('cancel-note-details').onclick = () => $('modal').close();
    $('save-note-details').onclick = () => { n.title = $('library-note-name').value.trim() || n.title; n.categoryId = $('note-category-select').value || null; n.updated = Date.now(); if(n.backend){n.backend.dirty=true;n.backend.editToken=uid();n.backend.editSessionId||=uid();} if(n === state.note) updateHeader(); saveNow(); refreshHome(); $('modal').close(); };
  }
  function confirmDeleteNotebook(n) {
    modal('删除笔记', '<p class="modal-copy">确定删除以下笔记？</p><p id="delete-notebook-name" class="delete-notebook-name"></p><p class="modal-copy">笔记将从本地笔记库中移除，包含其中的页面和批注，无法撤销。设备存储中的原始 NTB 文件不会被删除。</p><div class="modal-actions"><button class="button" id="cancel-delete-notebook">取消</button><button class="button danger" id="confirm-delete-notebook">确认删除</button></div>');
    $('delete-notebook-name').textContent=n.title;
    $('cancel-delete-notebook').onclick=()=>$('modal').close();
    $('confirm-delete-notebook').onclick=()=>{api.removeNotebook(n.id);refreshHome();$('modal').close();};
  }
  function refreshHome(){
    const query=$('home-search').value.trim().toLocaleLowerCase(),sort=$('home-sort').value;
    const names={all:'所有笔记',recent:'最近编辑',bookmarked:'含书签的笔记',uncategorized:'未分类'};
    for(const cat of state.categories)names['category:'+cat.id]=cat.name;
    if(!names[state.libraryFilter])state.libraryFilter='all';
    renderCategories();
    $('home-title').textContent=names[state.libraryFilter];$('home-results-title').textContent=query?'搜索结果':state.libraryFilter==='all'?'全部笔记':names[state.libraryFilter];
    $('home-total').textContent=state.notes.length;
    document.querySelectorAll('[data-library-filter]').forEach(b=>{const active=b.dataset.libraryFilter===state.libraryFilter;b.classList.toggle('selected',active);active?b.setAttribute('aria-current','page'):b.removeAttribute('aria-current');});
    const found=state.notes.filter(n=>(state.libraryFilter!=='bookmarked'||n.pages.some(p=>p.bookmarked))&&(state.libraryFilter!=='uncategorized'||!categoryFor(n))&&(!state.libraryFilter.startsWith('category:')||n.categoryId===state.libraryFilter.slice(9))&&(!query||`${n.title} ${categoryFor(n)?.name||'未分类'}`.toLocaleLowerCase().includes(query)));
    found.sort((a,b)=>sort==='name'&&state.libraryFilter!=='recent'?a.title.localeCompare(b.title,'zh-CN'):(b.updated||0)-(a.updated||0));
    $('home-result-count').textContent=`${found.length} 份`;$('home-empty').hidden=!!found.length;$('home-notes').replaceChildren();
    for(const n of found){
      const button=document.createElement('button');button.className='note-open';button.setAttribute('aria-label',`打开笔记：${n.title}`);
      const cover=document.createElement('div');cover.className='home-cover';const canvas=document.createElement('canvas');canvas.width=225;canvas.height=318;
      const c=canvas.getContext('2d');c.scale(225/900,318/(n.pages[0].viewHeight||1272));render(c,n.pages[0]);cover.append(canvas);
      if(n.pages.some(p=>p.bookmarked)){const mark=document.createElementNS('http://www.w3.org/2000/svg','svg');mark.classList.add('cover-bookmark');const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#i-bookmark');mark.append(use);cover.append(mark);}
      const title=document.createElement('h3');title.textContent=n.title;const meta=document.createElement('p'),pages=document.createElement('span'),date=document.createElement('span');pages.textContent=`${n.pages.length} 页`;
      date.textContent=`更新于 ${new Date(n.updated||Date.now()).toLocaleDateString('zh-CN',{month:'short',day:'numeric'})}`;meta.append(pages,date);button.append(cover,title,meta);button.onclick=()=>showEditor(n);
      const card=document.createElement('article');card.className='home-note-card';const manage=document.createElement('button');manage.className='note-category';manage.setAttribute('aria-label',`管理笔记：${n.title}`);manage.textContent=categoryFor(n)?.name||'未分类';manage.title='管理笔记';manage.onclick=()=>manageNotebook(n);const actions=document.createElement('div');actions.className='home-note-actions';const more=document.createElement('button');more.className='notebook-more';more.setAttribute('aria-label',`更多笔记操作：${n.title}`);more.title='管理笔记';more.innerHTML='<svg><use href="#i-more"/></svg>';more.onclick=()=>manageNotebook(n);actions.append(manage,more);card.append(button,actions);$('home-notes').append(card);
    }
    if(!query&&state.libraryFilter!=='bookmarked'){
      const add=document.createElement('button');add.className='home-create-tile';add.innerHTML='<svg><use href="#i-plus"/></svg><span>新建一份笔记</span>';add.onclick=()=>createDialog();$('home-notes').append(add);
    }
  }
  $('home-add-category').onclick=()=>categoryDialog();
  $('home-new').onclick=()=>createDialog();
  $('home-open').onclick=api.openFile;
  $('home-search').oninput=refreshHome;$('home-sort').onchange=refreshHome;
  document.querySelectorAll('[data-library-filter]').forEach(b=>b.onclick=()=>{state.libraryFilter=b.dataset.libraryFilter;refreshHome();saveNow();});
  document.querySelector('.home-brand').onclick=e=>{e.preventDefault();api.showHome();};
  return {refresh:refreshHome,activeCategory,populateCategorySelect,show() {
    document.body.classList.add('home-mode');$('home-screen').hidden=false;refreshHome();window.history.replaceState(null,'','#home');
  }};
};
