"""Stable page identities and a unified undo history for ink and page changes."""
import copy
import hashlib
from pages.ne_page_policy import is_known, resolve_paper_kind, validate_native_paper_choice


def ink_snapshot_digest(strokes):
    """Detect edits to a copied native page before event-level duplication."""
    digest=hashlib.sha256()
    for stroke in strokes:
        for field in (stroke.points,getattr(stroke,'color',None),getattr(stroke,'width',None),
                      getattr(stroke,'opacity',None),getattr(stroke,'tool',None),
                      getattr(stroke,'geometry_is_smoothed',None),
                      getattr(stroke,'geometry_is_linear',None),getattr(stroke,'outline',None),
                      getattr(stroke,'tape_pattern',3),getattr(stroke,'smart_interpolation',False)):
            digest.update(repr(field).encode('utf-8'))
            digest.update(b'\0')
    return digest.hexdigest()
from dataclasses import replace


def native_copy_ids(spec, strokes):
    """Only complete, byte-for-byte unchanged model groups may clone an op.

    One native op can produce several displayed subpaths. Comparing the whole
    group prevents partially erased ink from resurrecting its other subpaths.
    """
    groups={}
    for stroke in strokes:
        if stroke.source_id is not None:
            groups.setdefault(stroke.source_id,[]).append(stroke)
    return {ident for ident,digest in spec.get('native_ink_signatures',{}).items()
            if ident in groups and ink_snapshot_digest(groups[ident])==digest}


class PageEdits:
    def __init__(self,canvas):
        self.canvas=canvas;self.core=canvas.edits;self.initial=tuple(range(canvas.page_count));self.order=self.initial
        self.specs={i:dict(key=i,source=i,background_source=i,pdf=canvas.pdf_pages[i],width=w,height=h,images=canvas.images.get(i,[]),strokes=())
                    for i,(w,h) in enumerate(canvas.sizes)}
        self.next_key=canvas.page_count
        # The loader grants structural-write capability only AFTER validation.
        # Unsaved/in-memory documents have no opaque external NTB events;
        # original pages are locally constructed and therefore known.
        self.known_original_pages=(set(range(canvas.page_count))
                                   if not getattr(canvas,'archive_path',None) else set())
        if hasattr(canvas,'image_layers'):canvas.image_layers.set_order(self.order)
        self.min_pages=1;self.max_pages=None
        original=self.core.originals
        self.core.originals=lambda key:original(key) if key<len(self.initial) else self.specs[key]['strokes']

    def __getattr__(self,name):return getattr(self.core,name)
    def page_key(self,page):return self.order[page]
    def set_known_original_pages(self, indices):
        self.known_original_pages=set(indices)

    def is_known_page(self,page):
        key=self.page_key(page)
        return is_known(self.specs[key], key in self.known_original_pages)

    def can_delete_pages(self,pages):
        indices=tuple(pages)
        return (bool(indices) and len(self.order)-len(set(indices))>=self.min_pages
                and all(type(p) is int and 0<=p<len(self.order)
                        and self.is_known_page(p) for p in indices))

    def can_copy_page(self,page):
        return self.is_known_page(page)

    def can_replace_page(self,page):
        return self.is_known_page(page)

    def can_cut_page(self,page):
        return self.can_copy_page(page) and self.can_delete_pages((page,))

    def _background_for_new(self, selected):
        template=getattr(self.canvas,'paper_template',None) or {}
        existing=template.get('kind')
        # A PDF-backed notebook has no native paper template. Its inserted
        # empty sheet is white too; None must not be treated as a different
        # paper style and rejected by the per-page writer's follow policy.
        if existing is None:
            existing='blank'
        writer_kind=getattr(self.canvas,'page_edit_kind',None)
        if getattr(self.canvas,'document',None) is None:
            writer_kind='native_per_page' if writer_kind is None else writer_kind
        return validate_native_paper_choice(writer_kind,existing,selected)

    @property
    def pending(self):
        p=self.core.pending
        return dict(p,page=self.order.index(p['page'])) if p else None
    def state(self,page):return self.core.state(self.page_key(page))
    def begin(self,page,*args,**kwargs):return self.core.begin(self.page_key(page),*args,**kwargs)
    def change_selection(self,page,*args,**kwargs):return self.core.change_selection(self.page_key(page),*args,**kwargs)
    def transfer_selection(self,page,target,*args,**kwargs):
        return self.core.transfer_selection(self.page_key(page),self.page_key(target),*args,**kwargs)
    def edited_pages(self):
        edited=self.core.edited_pages()
        return {i for i,key in enumerate(self.order) if key in edited or key>=len(self.initial)}
    def snapshot(self,page):
        spec=copy.deepcopy(self.specs[self.page_key(page)])
        if spec.get('source') is not None and hasattr(self.canvas,'official_image_edits'):
            deleted=self.canvas.official_image_edits.deletion_changes
            spec['images']=[image for image in spec['images'] if int(image.get('data-event-id',-1)) not in deleted]
        original_strokes,changed=self.state(page)
        if spec.get('source') is not None:
            spec['native_model_ids']={stroke.source_id for stroke in self.core.originals(self.page_key(page))
                                      if stroke.source_id is not None}
            groups={}
            for stroke in original_strokes:
                if stroke.source_id is not None and stroke.source_id not in changed:
                    groups.setdefault(stroke.source_id,[]).append(stroke)
            spec['native_ink_signatures']={ident:ink_snapshot_digest(group)
                                           for ident,group in groups.items()}
        else:
            eligible=native_copy_ids(spec,original_strokes)
            spec['native_ink_signatures']={ident:digest for ident,digest in
                spec.get('native_ink_signatures',{}).items() if ident in eligible}
        spec.update(source=None,known_page=self.is_known_page(page),
                    strokes=tuple(original_strokes))
        if hasattr(self.canvas,'image_layers'):
            spec['viewer_images']=copy.deepcopy(self.canvas.image_layers._page(self.page_key(page)))
        if spec.get('background_source') is not None:
            spec['native_copy_signature']=ink_snapshot_digest(spec['strokes'])
            spec['native_copy_from_edited_page']=self.page_key(page) in self.core.edited_pages()
        return spec
    def _commit(self,order,focus):
        self.core.finish();before=self.order
        op=dict(kind='pages',page=None,before=before,after=tuple(order),focus_before=min(focus,len(before)-1),focus_after=min(focus,len(order)-1))
        del self.core.history[self.core.cursor:]
        self.core.next_token+=1;self.core.history.append((self.core.next_token,op));self.core.cursor+=1
        self._sync(op['focus_after']);return True
    def insert(self,index,snapshot=None,*,paper_kind='follow'):
        if self.max_pages is not None and len(self.order)>=self.max_pages:
            raise ValueError(f'此格式最多支持 {self.max_pages} 页；请先删除一页')
        index=max(0,min(len(self.order),index))
        if snapshot is None:
            # Only the document's existing paper template is used for a new sheet.
            background=self._background_for_new('follow')
            width,height=self.canvas.sizes[max(0,index-1)]
            spec=dict(width=width,height=height,background_source=None,pdf=None,
                      images=[],strokes=(),known_page=True,background_kind=background)
        else:
            spec=copy.deepcopy(snapshot)
            # A copied opaque source does not become validated merely because
            # the application gave the duplicate a new key.
            spec['known_page']=bool(snapshot.get('known_page',False))
        key=self.next_key;self.next_key+=1;spec.update(key=key,source=None);self.specs[key]=spec
        if hasattr(self.canvas,'image_layers'):
            if spec.get('viewer_images'):
                self.canvas.image_layers.pages[key]=copy.deepcopy(spec['viewer_images'])
                import uuid
                mapping={item['id']:uuid.uuid4().hex for item in self.canvas.image_layers.pages[key]['images']}
                for item in self.canvas.image_layers.pages[key]['images']:item['id']=mapping[item['id']]
                self.canvas.image_layers.pages[key]['stack']=[mapping.get(v,v) for v in self.canvas.image_layers.pages[key]['stack']]
            else:self.canvas.image_layers._page(key)
        order=list(self.order);order.insert(index,key);return self._commit(order,index)
    def replace_page(self,page,snapshot):
        """Replace an entire page as one undoable page-order operation.

        A new identity is crucial: mutating the old spec would also mutate the
        page restored by undo, and would break the saved original-page mapping.
        """
        if not 0<=page<len(self.order):raise IndexError('Page out of range')
        if snapshot is None:raise ValueError('Clipboard page is empty')
        if not self.can_replace_page(page):raise ValueError('未知页面不能被剪贴板页面覆盖')
        key=self.next_key;self.next_key+=1
        spec=copy.deepcopy(snapshot);spec.update(key=key,source=None)
        self.specs[key]=spec
        if hasattr(self.canvas,'image_layers'):
            self.canvas.image_layers.pages[key]=copy.deepcopy(spec.get('viewer_images',{'stack':['ink'],'images':[]}))
            import uuid
            mapping={item['id']:uuid.uuid4().hex for item in self.canvas.image_layers.pages[key]['images']}
            for item in self.canvas.image_layers.pages[key]['images']:item['id']=mapping[item['id']]
            self.canvas.image_layers.pages[key]['stack']=[mapping.get(v,v) for v in self.canvas.image_layers.pages[key]['stack']]
        order=list(self.order);order[page]=key
        return self._commit(order,page)
    def delete(self,page):
        if len(self.order)<=self.min_pages:return False
        if not self.is_known_page(page):raise ValueError('当前页面结构未知，禁止删除')
        order=list(self.order);del order[page];return self._commit(order,page)
    def delete_many(self,indices):
        """Delete selected display pages atomically (one Ctrl+Z restores all).

        Page identities, not indices, are retained so PDF backgrounds, attached
        images, and original stroke ownership stay aligned after reordering.
        """
        pages=set(indices)
        if not pages:return False
        if any(not isinstance(p,int) or isinstance(p,bool) or not 0<=p<len(self.order) for p in pages):
            raise IndexError('Selected page is out of range')
        if len(self.order)-len(pages)<self.min_pages:
            raise ValueError(f'此分组页面至少保留 {self.min_pages} 页；请减少所选页数')
        if not all(self.is_known_page(p) for p in pages):
            raise ValueError('所选页面包含未验证的原生页面；仅可批量删除已知页面')
        order=[key for index,key in enumerate(self.order) if index not in pages]
        focus=min(min(pages),len(order)-1)
        return self._commit(order,focus)
    def move(self,page,destination):
        """Move one stable page identity, undoable as a single operation."""
        if not 0<=page<len(self.order) or not 0<=destination<len(self.order):
            raise IndexError('页面移动目标超出范围')
        if page==destination:return False
        order=list(self.order);key=order.pop(page);order.insert(destination,key)
        return self._commit(order,destination)
    def _sync(self,focus):
        order=self.initial
        for _,op in self.core.history[:self.core.cursor]:
            if op['kind']=='pages':order=op['after']
        if order!=self.order:
            self.order=order
            if hasattr(self.canvas,'image_layers'):self.canvas.image_layers.set_order(order)
            if hasattr(self.canvas,'selected_image'):self.canvas.selected_image=None
            self.canvas.applyPageSpecs([self.specs[key] for key in order],focus)
    def undo(self):
        op=self.core.history[self.core.cursor-1][1] if self.core.can_undo else {}
        self.core.undo();self._sync(op.get('focus_before',0))
    def redo(self):
        op=self.core.history[self.core.cursor][1] if self.core.can_redo else {}
        self.core.redo();self._sync(op.get('focus_after',0))
    def native_changes(self):
        hidden=set();added=[];edited=self.core.edited_pages()
        for page,key in enumerate(self.order):
            if key not in edited and key<len(self.initial):continue
            strokes,changed=self.core.state(key)
            # A copied page shares provenance IDs, not ownership of the source
            # events. Editing it must never erase the original page's ink.
            if key<len(self.initial):hidden.update(changed)
            cloned=(native_copy_ids(self.specs[key],strokes)
                    if key>=len(self.initial) else set())
            for stroke in strokes:
                if stroke.source_id in cloned:continue
                if key>=len(self.initial) or stroke.source_id is None or stroke.source_id in changed:added.append((page,stroke))
        return hidden,added
    def page_plan(self):
        if self.order==self.initial:return None
        plan=[]
        for key in self.order:
            spec=copy.deepcopy(self.specs[key])
            # Grouped-PDF rebuild needs all VISIBLE ink on EVERY retained page,
            # including unchanged original events and copied/replaced pages.
            # The native per-page writer retains original opaque ink events
            # and applies edited strokes separately. Flattening every retained
            # page here blocks the GUI and copies ink that will never be used.
            retained_native=(getattr(self.canvas,'page_edit_kind',None)=='native_per_page'
                             and spec.get('source') is not None)
            spec['strokes']=() if retained_native else tuple(copy.deepcopy(self.core.state(key)[0]))
            spec['native_copy_ids']=native_copy_ids(spec,spec['strokes']) if spec.get('source') is None else set()
            plan.append(spec)
        return plan
