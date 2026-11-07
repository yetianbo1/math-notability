"""Incremental real-time stroke stabilizer for new pen/highlighter strokes.

Each incoming sample finalizes the preceding interior vertex using its two
neighbors. The newest point stays under the cursor/stylus: no buffered latency,
look-ahead timer, global re-smoothing, or modification of existing NTB strokes.
"""
import math


def stabilize_middle_point(before, middle, after, width, strength=0.85):
    """Return a gently smoothed previous vertex, bounded near its raw position.

    The input locations are successive *raw* samples. Unlike smoothing an
    entire stroke after release, this works with only three points and does
    not delay the latest tip sample.
    """
    try:
        a=tuple(float(v) for v in before)
        b=tuple(float(v) for v in middle)
        c=tuple(float(v) for v in after)
        width=float(width)
        strength=float(strength)
    except (TypeError, ValueError):
        return tuple(middle)
    if not all(math.isfinite(v) for v in (*a,*b,*c,width,strength)):
        return tuple(middle)
    strength=max(0.0,min(1.0,strength))
    if not strength:
        return b
    ux,uy=b[0]-a[0],b[1]-a[1]
    vx,vy=c[0]-b[0],c[1]-b[1]
    ulen=math.hypot(ux,uy);vlen=math.hypot(vx,vy)
    if ulen<1e-8 or vlen<1e-8:
        return b
    # Sharp, deliberate corners survive. Small jitter still gets reduced.
    cosine=max(-1.0,min(1.0,(ux*vx+uy*vy)/(ulen*vlen)))
    if cosine<0.5 and min(ulen,vlen)>max(2.,min(4.,abs(width)*.25)):
        return b
    # Uneven event spacing must not move a vertex along the stroke.
    # Correct only its normal deviation, with less filtering for sparse input.
    chord=(c[0]-a[0],c[1]-a[1]);length2=sum(v*v for v in chord)
    if length2<1e-12:return b
    t=max(0.,min(1.,sum((b[k]-a[k])*chord[k] for k in (0,1))/length2))
    gain=strength*min(1.,2./((ulen+vlen)*.5))
    dx=gain*(a[0]+t*chord[0]-b[0])
    dy=gain*(a[1]+t*chord[1]-b[1])
    cap=max(0.2,min(0.65,abs(width)*0.2))
    distance=math.hypot(dx,dy)
    if distance>cap:
        dx*=cap/distance
        dy*=cap/distance
    return (b[0]+dx,b[1]+dy)


class CaptureFilter:
    """Speed-adaptive normal filtering, with an exact raw leading tip.

    The derivative low-pass and adaptive cutoff follow One Euro's speed/lag
    principle. We filter the previous vertex's normal correction, not the
    pointer position, so the newest tip and endpoints never lag behind.
    """
    def __init__(self,tool,width):
        self.tool=tool;self.width=width;self.point=None;self.time=None;self.speed=0.
        self.strength={'pen':.50,'highlighter':.56,'tape':.60}.get(tool,.50)

    def observe(self,point,timestamp=None):
        from time import monotonic
        now=monotonic() if timestamp is None else float(timestamp)
        if not math.isfinite(now):now=monotonic()
        dt=now-self.time if self.time is not None else 1/120
        # Repeated device timestamps and changes of clock domain are benign.
        if not 0<dt<.25:dt=1/120
        dt=max(1/1000,min(1/20,dt))
        if self.point is not None:
            measured=math.dist(self.point,point)/dt
            derivative_alpha=(2*math.pi*1.5*dt)/(1+2*math.pi*1.5*dt)
            self.speed+=derivative_alpha*(measured-self.speed)
        self.point=tuple(point);self.time=now
        cutoff=1.5+.018*self.speed
        alpha=(2*math.pi*cutoff*dt)/(1+2*math.pi*cutoff*dt)
        return self.strength*(1-alpha)
