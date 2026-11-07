'use strict';
window.TabletFiles = {connect({modal,toast,closeFloatingMenus,acceptNote}) {
  const $=id=>document.getElementById(id);
  $('open-file').onclick=()=>{closeFloatingMenus();$('ntb-input').value='';$('ntb-input').click();};
  $('ntb-input').onchange=async e=>{
    const file=e.target.files?.[0];e.target.value='';if(!file)return;
    if(!/\.ntb$/i.test(file.name)){toast('平板版仅支持打开 .ntb 笔记');return;}
    if(!file.size){toast('所选 NTB 文件为空');return;}
    if(file.size>2*1024*1024*1024){toast('NTB 文件超过 2 GB 限制');return;}
    const trigger=$('home-open');trigger.disabled=true;toast('正在解析 NTB 笔记…');
    try{const note=await TabletBackend.open(file);await acceptNote(note);toast('NTB 已打开，编辑会自动保存');}
    catch(error){modal('无法打开笔记','<p class="modal-copy" id="ntb-error" role="alert"></p>');$('ntb-error').textContent=error.message;}
    finally{trigger.disabled=false;}
  };
}};
