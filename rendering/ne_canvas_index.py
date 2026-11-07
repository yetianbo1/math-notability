"""Transfer geometry from the loader to the canvas without GUI path parsing."""
from collections import defaultdict
from dataclasses import dataclass
import json
from lxml import etree
from core.ne_page_geometry import native_sheet_height
from rendering.ne_mixed_layers import collect_layers


@dataclass
class CanvasIndex:
    svg: bytes
    ink: dict
    images: dict
    texts: dict
    layers: list
    path_bounds: dict
    render_bounds: dict
    page_count: int | None


def prepare_canvas_index(svg,height,cancelled=lambda:False):
    root=etree.fromstring(svg)
    ink=defaultdict(list);images=defaultdict(list);texts=defaultdict(list)
    for el in root.iter():
        tag=el.tag.rsplit('}',1)[-1]
        if tag=='path':ink[int(el.get('data-page','0'))].append(etree.tostring(el))
        elif tag=='image':images[int(el.get('data-page'))].append(dict(el.attrib))
        elif tag=='text' and el.get('data-native-text'):
            record=json.loads(el.get('data-native-text'));texts[record['page']].append(record)
    layers,bounds,render_bounds=collect_layers(ink,texts,root.get('data-local-ink')=='1',
                                              native_sheet_height(root,height),cancelled)
    layout=json.loads(root.get('data-pages','[]'))
    return CanvasIndex(svg,ink,images,texts,layers,bounds,render_bounds,len(layout) if layout else None)
