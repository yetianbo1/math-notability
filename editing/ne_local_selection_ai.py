"""Offline PP-OCRv5 text-region inference for ink selection.

Only local rasterized ink enters the model. There are no downloads, network
clients, recognition services or notebook writes in this module.
"""
from dataclasses import replace
from pathlib import Path
from functools import lru_cache
import hashlib,math,sys,threading
import numpy as np
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QImage,QPainter,QPainterPathStroker
from editing.ne_edits import paint_strokes

MODEL_HASH='4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae'
_LOCK=threading.Lock()


def model_path():
    root=Path(sys._MEIPASS) if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    return root/'assets'/'ink_text_detector.onnx'


@lru_cache(maxsize=1)
def session():
    path=model_path()
    if not path.is_file():raise RuntimeError('缺少内置本地 AI 模型')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=MODEL_HASH:raise RuntimeError('本地 AI 模型校验失败')
    from infrastructure.ne_ai_runtime import runtime as ort,load_error
    if ort is None:raise RuntimeError('本地 AI 运行库不可用') from load_error
    options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
    options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider'])


def _components(probability):
    """Connected text heatmap runs, without an OpenCV/SciPy dependency."""
    mask=probability>=.22
    padded=np.pad(mask,1)
    mask=np.logical_or.reduce([padded[y:y+mask.shape[0],x:x+mask.shape[1]] for y in range(3) for x in range(3)])
    parents=[];stats=[];previous=[]
    def root(i):
        while parents[i]!=i:parents[i]=parents[parents[i]];i=parents[i]
        return i
    for y,row in enumerate(mask):
        edges=np.flatnonzero(np.diff(np.pad(row.astype(np.int8),(1,1))))
        current=[];cursor=0
        for left,right in zip(edges[::2],edges[1::2]):
            left,right=int(left),int(right);i=len(parents);parents.append(i)
            values=probability[y,left:right];values=values[values>=.22]
            stats.append([left,y,right,y+1,len(values),float(values.sum())])
            while cursor<len(previous) and previous[cursor][1]<left-1:cursor+=1
            k=cursor
            while k<len(previous) and previous[k][0]<=right+1:
                a,b=root(i),root(previous[k][2])
                if a!=b:parents[b]=a
                k+=1
            current.append((left,right,i))
        previous=current
    combined={}
    for i,s in enumerate(stats):
        r=root(i)
        if r not in combined:combined[r]=list(s)
        else:
            b=combined[r];b[0]=min(b[0],s[0]);b[1]=min(b[1],s[1]);b[2]=max(b[2],s[2]);b[3]=max(b[3],s[3]);b[4]+=s[4];b[5]+=s[5]
    return [b for b in combined.values() if b[4]>=8 and b[5]/b[4]>=.45 and b[2]-b[0]>=3 and b[3]-b[1]>=3]


def text_regions(index,rect,size,smooth=True):
    roi=rect.adjusted(-size*2,-size*1.5,size*2,size*1.5)
    scale=min(4.,960/max(roi.width(),roi.height(),1.))
    width=max(32,math.ceil(roi.width()*scale/32)*32)
    height=max(32,math.ceil(roi.height()*scale/32)*32)
    image=QImage(width,height,QImage.Format.Format_RGB888);image.fill(0xffffff)
    painter=QPainter(image)
    try:
        painter.scale(scale,scale);painter.translate(-roi.left(),-roi.top())
        strokes=[replace(s,color='#101010',opacity=1.) for s,b in zip(index.strokes,index.boxes)
                 if not s.is_highlighter and roi.intersects(QRectF(b[0],b[1],b[2]-b[0],b[3]-b[1]))]
        paint_strokes(painter,strokes,smooth=smooth)
    finally:painter.end()
    data=np.frombuffer(image.constBits().asstring(image.sizeInBytes()),dtype=np.uint8).reshape(height,image.bytesPerLine())[:,:width*3].reshape(height,width,3)
    tensor=data.astype(np.float32)/255.
    tensor=(tensor-np.array([.485,.456,.406],dtype=np.float32))/np.array([.229,.224,.225],dtype=np.float32)
    tensor=np.ascontiguousarray(tensor.transpose(2,0,1)[None])
    with _LOCK:
        engine=session()
        probability=engine.run(None,{engine.get_inputs()[0].name:tensor})[0][0,0]
    sy=height/probability.shape[0]/scale;sx=width/probability.shape[1]/scale
    regions=[]
    for x0,y0,x1,y1,_,_ in _components(probability):
        # DB predicts shrunken text cores. Recover a small surrounding border.
        pad=min(size*.4,(y1-y0)*sy*.4)
        regions.append(QRectF(roi.left()+x0*sx-pad,roi.top()+y0*sy-pad,
                              (x1-x0)*sx+2*pad,(y1-y0)*sy+2*pad))
    return regions


def complete(index,rect,region,seeds,smooth=True):
    if not seeds:return set(),0
    size=index.character_size(rect)
    regions=text_regions(index,rect,size,smooth)
    stroker=QPainterPathStroker();stroker.setWidth(size*1.1)
    allowed=region.united(stroker.createStroke(region));result=set(seeds);accepted=0
    for line in regions:
        members={i for i,b in enumerate(index.boxes)
                 if max(b[2]-b[0],b[3]-b[1])<=size*2.2 and not index.strokes[i].is_highlighter
                 and line.contains(QRectF(b[0],b[1],b[2]-b[0],b[3]-b[1]).center())}
        if not members&seeds:continue
        accepted+=1
        for i in members-seeds:
            s=index.strokes[i];outline=s.selection_outline if smooth else s.linear_selection_outline
            if allowed.intersects(outline):result.add(i)
    return result,accepted
