"""Local geometric handwriting completion; no learned model or network calls.

Groups are limited to character-sized regions. Completion uses the original
marquee hits, so it cannot flood through an entire neighboring line of text.
"""
import math
from collections import defaultdict
from PyQt6.QtCore import QRectF,QPointF


def _gap(a,b):
    return math.hypot(max(a[0]-b[2],b[0]-a[2],0.),max(a[1]-b[3],b[1]-a[3],0.))


def _union(a,b):
    return min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3])


def _rect(box):return QRectF(box[0],box[1],box[2]-box[0],box[3]-box[1])


def _length_fraction(stroke,rect):
    """Fraction of centreline length inside the rectangle, independent of zoom."""
    inside=total=0.
    for a,b in zip(stroke.points,stroke.points[1:]):
        length=math.dist(a,b)
        if length<1e-10:continue
        total+=length;lo=0.;hi=1.
        if not isinstance(rect,QRectF):
            steps=min(64,max(1,math.ceil(length/2)))
            inside+=length*sum(rect.contains(QPointF(a[0]+(b[0]-a[0])*(j+.5)/steps,
                                                       a[1]+(b[1]-a[1])*(j+.5)/steps)) for j in range(steps))/steps
            continue
        for start,end,minimum,maximum in ((a[0],b[0],rect.left(),rect.right()),
                                         (a[1],b[1],rect.top(),rect.bottom())):
            delta=end-start
            if abs(delta)<1e-12:
                if not minimum<=start<=maximum:hi=-1.;break
            else:
                t0,t1=sorted(((minimum-start)/delta,(maximum-start)/delta))
                lo=max(lo,t0);hi=min(hi,t1)
        inside+=length*max(0.,hi-lo)
    return inside/total if total else float(rect.contains(_rect(stroke.bounds)))


class InkSelectionIndex:
    def __init__(self,strokes):
        self.strokes=strokes
        self.boxes=[]
        for s in strokes:
            x0,y0,x1,y1=s.bounds;r=0. if s.outline is not None else s.width/2
            self.boxes.append((x0-r,y0-r,x1+r,y1+r))
        self._groups={}
        self.rectangles=[_rect(box) for box in self.boxes]

    def character_size(self,rect):
        near=rect.adjusted(-20,-20,20,20)
        extents=[max(b[2]-b[0],b[3]-b[1]) for s,b in zip(self.strokes,self.boxes)
                 if not s.is_highlighter and near.intersects(_rect(b))
                 and 1.<max(b[2]-b[0],b[3]-b[1])<96.]
        if not extents:return 10.
        extents.sort()
        # Most Chinese pen strokes span only a part of a character.
        return max(3.,min(64.,extents[int((len(extents)-1)*.75)]*1.6))

    def groups(self,size):
        key=round(size*2)/2
        if key in self._groups:return self._groups[key]
        size=key;parents=list(range(len(self.strokes)));boxes=list(self.boxes)
        text={i for i,(s,b) in enumerate(zip(self.strokes,boxes))
              if not s.is_highlighter and b[2]-b[0]<=1.7*size and b[3]-b[1]<=1.7*size}
        def root(i):
            while parents[i]!=i:
                parents[i]=parents[parents[i]];i=parents[i]
            return i
        cells=defaultdict(list);edges=[];seen=set();cell=size*1.7;reach=size*.16
        for i in sorted(text):
            b=boxes[i]
            for x in range(math.floor((b[0]-reach)/cell),math.floor((b[2]+reach)/cell)+1):
                for y in range(math.floor((b[1]-reach)/cell),math.floor((b[3]+reach)/cell)+1):
                    for j in cells[x,y]:
                        if (j,i) in seen:continue
                        seen.add((j,i));distance=_gap(b,boxes[j])
                        if distance<=reach:
                            # Prefer overlapping stroke boxes before bridging a gap.
                            centre=math.hypot(b[0]+b[2]-boxes[j][0]-boxes[j][2],
                                              b[1]+b[3]-boxes[j][1]-boxes[j][3])
                            edges.append((distance+centre*.015,j,i))
                    cells[x,y].append(i)
        for _,i,j in sorted(edges):
            a,b=root(i),root(j)
            if a==b:continue
            box=_union(boxes[a],boxes[b])
            if box[2]-box[0]<=1.55*size and box[3]-box[1]<=1.7*size:
                parents[b]=a;boxes[a]=box
        groups=defaultdict(set)
        for i in text:groups[root(i)].add(i)
        result=[(members,boxes[r]) for r,members in groups.items()]
        if len(self._groups)>=4:self._groups.clear()
        self._groups[key]=result
        return result

    def select(self,rect,mode,touches,outline,region=None):
        hits={i for i,s in enumerate(self.strokes) if rect.intersects(self.rectangles[i]) and touches(s,rect)}
        if mode=='contained':
            return {i for i in hits if (region.contains(outline(self.strokes[i])) if region is not None
                                       else rect.contains(outline(self.strokes[i]).boundingRect()))}
        if mode!='smart' or not hits:return hits
        size=self.character_size(rect)
        # A brace/arrow crossing a word is a separate object. Merely grazing it
        # must not enlarge a small word selection to the entire annotation tree.
        hits={i for i in hits if max(self.boxes[i][2]-self.boxes[i][0],self.boxes[i][3]-self.boxes[i][1])<=2.2*size
              or _length_fraction(self.strokes[i],region if region is not None else rect)>=.55}
        result=set(hits);groups=self.groups(size);anchors=[]
        for members,box in groups:
            if not members&hits:continue
            bounds=_rect(box);overlap=bounds.intersected(rect)
            area=max(bounds.width()*bounds.height(),1e-8)
            if overlap.width()*overlap.height()/area>=.18:
                result.update(members);anchors.append(box)
        # Complete a nearby closing/opening parenthesis, but do not recursively
        # add a neighboring character, sentence, column or long connector.
        for members,box in groups:
            if members&result:continue
            width,height=box[2]-box[0],box[3]-box[1]
            if not (.55*size<=height<=1.9*size and width<.48*height):continue
            if not rect.adjusted(-size*.55,0,size*.55,0).intersects(_rect(box)):continue
            for anchor in anchors:
                overlap=min(box[3],anchor[3])-max(box[1],anchor[1])
                if overlap>=.5*min(height,anchor[3]-anchor[1]) and _gap(box,anchor)<=size*.18:
                    result.update(members);break
        return result
