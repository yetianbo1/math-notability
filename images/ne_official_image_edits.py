"""Editable geometry for original Notability type-22 image events.

The overlay references the same per-page records that the renderer uses.
Geometry uses native display origin and display size. Lock/unlock uses native
type-23 target-reference events observed in T01/T02/T03. Image stacking uses
the nullable uint64 zIndex in type-23. Type-25 supports non-destructive hiding.
"""
import math
import json
from images.ne_image_resize import has_original_aspect

PREFIX='native:'
FIELDS=('x','y','width','height')

class OfficialImageEdits:
    def __init__(self, images):
        self.items={}
        self.original={}
        self.original_z={}
        self._saved_digest=None
        for page,records in images.items():
            for raw in records:
                try:
                    event_id=int(raw['data-event-id'])
                    values=tuple(float(raw['data-'+k]) for k in FIELDS)
                    if event_id<0 or any(not math.isfinite(v) for v in values) or min(values[2:])<=0:
                        continue
                    if event_id in self.items:
                        raise ValueError('原生图片事件编号重复')
                except (KeyError,TypeError,ValueError):
                    continue
                locked=str(raw.get('data-locked','false')).lower() in ('true','1')
                source=raw.get('data-lock_source',raw.get('data-lock-source','unmarked'))
                self.items[event_id]={'id':PREFIX+str(event_id),'page':int(page),'initial_page':int(page),'raw':raw,
                                      'locked':locked,'initial_locked':locked,
                                      'lock_source':source,'deleted':False,'initial_deleted':False,
                                      'kind':'image','bounds':(values[0],values[1],
                                                               values[0]+values[2],values[1]+values[3])}
                self.original[event_id]=values
                self.original_z[event_id]=int(raw.get('data-z-index',0))

    def register_layers(self,records):
        self.layer_items=dict(self.items)
        for page,ident,z,kind,bounds in records:
            if ident in self.layer_items:
                self.layer_items[ident].update(kind=kind,bounds=bounds)
                continue
            self.layer_items[ident]=dict(id=PREFIX+str(ident),page=page,
                                         raw={"data-z-index":str(z)},
                                         deleted=False,kind=kind,bounds=bounds)
            self.original_z[ident]=z

    def ordered(self,page):
        return sorted((info for info in getattr(self,'layer_items',self.items).values() if info['page']==page and not info['deleted']),
                      key=lambda i:(int(i['raw'].get('data-z-index',0)),int(i['id'][len(PREFIX):])))

    def layer(self,page,identifier,direction):
        item=self.item(page,identifier)
        if item is None or item['locked']:return False
        ordered=self.ordered(page);old=next(i for i,v in enumerate(ordered) if v['id']==identifier)
        if direction in ('up','down'):
            target=self.visual_target(page,identifier,direction)
            if target is None:return False
            target_z=int(target['raw'].get('data-z-index',0))
            desired=target_z+1 if direction=='up' else max(0,target_z-1)
            if desired>=2**64:raise ValueError('图片层级编号已达上限')
            selected=self.items[int(identifier[len(PREFIX):])]
            # Make room when another object already occupies the destination
            # z value. Shift that group and higher groups, preserving order.
            if any(info is not selected and int(info['raw'].get('data-z-index',0))==desired
                   for info in self.layer_items.values() if info['page']==page and not info['deleted']):
                if max(int(info['raw'].get('data-z-index',0))
                       for info in self.layer_items.values()
                       if info['page']==page and not info['deleted'])>=2**64-1:
                    raise ValueError('图片层级编号已达上限')
                for info in self.layer_items.values():
                    if info['page']!=page or info['deleted'] or info is selected:continue
                    z=int(info['raw'].get('data-z-index',0))
                    if z>=desired:info['raw']['data-z-index']=str(z+1)
            selected['raw']['data-z-index']=str(desired)
            return True
        targets={'up':min(len(ordered)-1,old+1),'down':max(0,old-1),'front':len(ordered)-1,'back':0}
        if direction not in targets:raise ValueError('未知图片层级操作')
        new=targets[direction]
        if new==old:return False
        desired=list(ordered);desired.insert(new,desired.pop(old))
        lower=int(desired[new-1]['raw'].get('data-z-index',0)) if new else -1
        upper=int(desired[new+1]['raw'].get('data-z-index',0)) if new+1<len(desired) else 2**64
        # Prefer changing only the selected image. Renumber neighbours solely
        # when equal/adjacent uint64 values leave no free integer between them.
        if upper-lower>1:
            desired[new]['raw']['data-z-index']=str(lower+1)
            return True
        # Preserve existing distinct layer values; resolve ties with the nearest
        # increasing integers so the requested order does not depend on IDs.
        values=[]
        for info in ordered:
            z=int(info['raw'].get('data-z-index',0))
            values.append(max(z,values[-1]+1) if values else z)
        if values[-1]>=2**64:raise ValueError('图片层级编号已达上限')
        ordered.insert(new,ordered.pop(old))
        for info,z in zip(ordered,values):info['raw']['data-z-index']=str(z)
        return True

    def visual_target(self,page,identifier,direction):
        """Return the nearest overlapping object above or below the image."""
        item=self.item(page,identifier)
        if item is None or direction not in ('up','down'):return None
        info=self.layer_items[int(identifier[len(PREFIX):])]
        def current_bounds(layer):
            if layer.get('kind')=='image' and layer.get('raw'):
                raw=layer['raw']
                try:
                    x,y,width,height=(float(raw['data-'+key]) for key in FIELDS)
                    return (x,y,x+width,y+height)
                except (KeyError,TypeError,ValueError):
                    return None
            return layer.get('bounds')
        bounds=current_bounds(info)
        if bounds is None:return None
        ordered=self.ordered(page)
        try:old=next(index for index,other in enumerate(ordered) if other['id']==identifier)
        except StopIteration:return None
        indexes=range(old+1,len(ordered)) if direction=='up' else range(old-1,-1,-1)
        for index in indexes:
            candidate=ordered[index]
            other=current_bounds(candidate)
            if other is None:continue
            if (max(bounds[0],other[0])<min(bounds[2],other[2])
                    and max(bounds[1],other[1])<min(bounds[3],other[3])):
                return candidate
        return None

    @property
    def layer_changes(self):
        return {ident:dict(original=self.original_z[ident],current=int(info['raw'].get('data-z-index',0)))
                for ident,info in getattr(self,'layer_items',self.items).items() if int(info['raw'].get('data-z-index',0))!=self.original_z[ident]}

    def item(self,page,identifier):
        if not isinstance(identifier,str) or not identifier.startswith(PREFIX):return None
        try:event_id=int(identifier[len(PREFIX):])
        except ValueError:return None
        info=self.items.get(event_id)
        if info is None or info['page']!=page or info['deleted']:return None
        result={k:float(info['raw']['data-'+k]) for k in FIELDS}
        return dict(result,id=identifier,locked=info['locked'],native=True,lock_source=info['lock_source'],
                    lock_editable=True)

    def move(self,page,identifier,x,y):
        item=self.item(page,identifier)
        if not item or item['locked'] or not all(math.isfinite(v) for v in (x,y)):
            return False
        return self.resize(page,identifier,x,y,item['width'],item['height'])

    def resize(self,page,identifier,x,y,width,height,*,page_size=None):
        item=self.item(page,identifier)
        if not item or item['locked']:return False
        values=(float(x),float(y),float(width),float(height))
        if not all(math.isfinite(v) and abs(v)<=100000 for v in values):return False
        if values[0]<0 or values[1]<0 or min(values[2:])<0.1:return False
        # Native events may have historical geometry transforms; preserve the
        # currently displayed ratio, not just the original type-22 base size.
        if not has_original_aspect(item['width'], item['height'],width,height):
            return False
        if page_size is not None:
            pw,ph=page_size
            if values[0]+values[2]>pw+0.001 or values[1]+values[3]>ph+0.001:return False
        if all(abs(item[k]-v)<1e-8 for k,v in zip(FIELDS,values)):return False
        info=self.items[int(identifier[len(PREFIX):])]
        for field,value in zip(FIELDS,values):info['raw']['data-'+field]=repr(value)
        return True

    def transfer(self,page,target,identifier,x,y):
        item=self.item(page,identifier)
        if item is None or item['locked'] or target is None:return False
        if not all(math.isfinite(v) and 0<=v<=100000 for v in (x,y)):return False
        if page==target:return self.move(page,identifier,x,y)
        info=self.items[int(identifier[len(PREFIX):])]
        info['page']=target
        info['raw'].update({'data-page':str(target),'data-x':repr(float(x)),'data-y':repr(float(y))})
        return True


    def lock(self,page,identifier,locked):
        item=self.item(page,identifier)
        if item is None:return False
        info=self.items[int(identifier[len(PREFIX):])]
        locked=bool(locked)
        if info['locked']==locked:return False
        info['locked']=locked
        info['raw']['data-locked']='True' if locked else 'False'
        return True

    def remove(self,page,identifier):
        """Mark one selected original picture hidden; preserve its native asset."""
        item=self.item(page,identifier)
        if item is None or item['locked']:return False
        ident=int(identifier[len(PREFIX):])
        self.items[ident]['deleted']=True
        return True

    @property
    def deletion_changes(self):
        return {ident for ident,info in self.items.items() if info['deleted'] and not info['initial_deleted']}

    @property
    def lock_changes(self):
        return {ident:{'original':info['initial_locked'],'current':info['locked']}
                for ident,info in self.items.items()
                if info['initial_locked']!=info['locked']}

    @property
    def changes(self):
        updated={}
        for event_id,info in self.items.items():
            current=tuple(float(info['raw']['data-'+k]) for k in FIELDS)
            original=self.original[event_id]
            if any(abs(a-b)>1e-7 for a,b in zip(current,original)) or info['page']!=info['initial_page']:
                updated[event_id]={'original':original,'current':current}
                if info['page']!=info['initial_page']:
                    updated[event_id].update(original_page=info['initial_page'],current_page=info['page'])
        return updated

    @property
    def dirty(self):
        if self._saved_digest is None:
            return bool(self.changes or self.lock_changes or self.deletion_changes or self.layer_changes)
        return self.save_digest()!=self._saved_digest

    def save_digest(self):
        return json.dumps([self.changes,self.lock_changes,sorted(self.deletion_changes),self.layer_changes],
                          sort_keys=True,separators=(',',':'))

    def mark_saved(self,digest=None):
        # Keep deltas relative to the immutable session baseline. Rebasing the
        # native IDs/geometry here would lose cumulative changes on next save.
        self._saved_digest=self.save_digest() if digest is None else digest
