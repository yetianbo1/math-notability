"""Selected-image action availability, independent of Qt widgets.

Every editable image remains above the immutable page background.
"""


def action_states(canvas, editable):
    """Return (available operations, selected image locked flag)."""
    states={name:False for name in ('toggle_lock','up','down','front','back','delete')}
    if not editable or canvas is None or getattr(canvas,'edit_tool',None)=='select':return states,False
    selection=getattr(canvas,'selected_image',None)
    layers=getattr(canvas,'image_layers',None)
    edits=getattr(canvas,'edits',None)
    if not selection or layers is None or edits is None:return states,False
    if str(selection[1]).startswith('native:'):
        # T01/T02/T03 verify native type-23 lock and unlock events.
        item=canvas.editableImageItem(*selection) if hasattr(canvas,'editableImageItem') else None
        if item and item.get('lock_editable'):
            states['toggle_lock']=True
            states['delete']=not item['locked']
            if not item['locked']:
                page=canvas.nativeImagePage(selection[0])
                edits=canvas.official_image_edits
                ordered=edits.ordered(page)
                position=next(i for i,v in enumerate(ordered) if v['id']==selection[1])
                states.update(
                    up=edits.visual_target(page,selection[1],'up') is not None,
                    down=edits.visual_target(page,selection[1],'down') is not None,
                    front=position<len(ordered)-1,
                    back=position>0,
                )
        return states,bool(item and item.get('locked'))
    try:
        page,identifier=selection
        key=edits.page_key(page) if hasattr(edits,'page_key') else page
        item=layers.item(key,identifier)
        if item is None:return states,False
        stack=layers._page(key)['stack']
        if identifier=='ink' or identifier not in stack:return states,False
        position=stack.index(identifier)
        if item['locked']:
            states['toggle_lock']=True
            return states,True
        states.update(toggle_lock=True,up=position<len(stack)-1,
                      down=position>0,front=position<len(stack)-1,
                      back=position>0,delete=True)
        return states,bool(item['locked'])
    except (AttributeError,IndexError,KeyError,TypeError,ValueError):
        return states,False
