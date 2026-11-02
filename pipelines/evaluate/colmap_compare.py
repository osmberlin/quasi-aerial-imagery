"""colmap_compare.py A_SCENE B_SCENE: stage times, GPS residual and camera centre differences of two COLMAP runs.
Models are aligned with a similarity fit, so the numbers say how well poses agree, not how accurate they are."""
import sys, sqlite3, re, numpy as np, datetime as dt
from pathlib import Path
from pyproj import Transformer

def model(scene):
    lines = [l for l in open(Path(scene) / 'colmap/run/sparse/0/txt/images.txt') if not l.startswith('#')]
    out = {}
    for l in lines[::2]:
        p = l.split()
        w, x, y, z, tx, ty, tz = map(float, p[1:8])
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
        out[p[9]] = -R.T @ np.array([tx, ty, tz])
    return out

def priors(scene, crs):
    db = sqlite3.connect(Path(scene) / 'colmap/run/database.db')
    names = dict(db.execute('select image_id,name from images'))
    T = Transformer.from_crs(4326, crs, always_xy=True)
    res = {}
    for i, pos in db.execute('select corr_data_id,position from pose_priors'):
        if i in names:
            lat, lon, alt = np.frombuffer(pos, 'f8')
            res[names[i]] = np.array([*T.transform(lon, lat), alt])
    return res

def stages(scene):
    res = {}
    for s in ('features', 'matching', 'mapper'):
        t = re.findall(r'^(\S+ \S+) (?:start|end).*?(?:peak_rss_mb=(\d+))?$', open(Path(scene) / f'colmap/run/{s}/monitor.log').read(), re.M)
        f = lambda v: dt.datetime.strptime(v, '%Y-%m-%d %H:%M:%S')
        res[s] = {'seconds': (f(t[-1][0]) - f(t[0][0])).seconds, 'peak_mb': t[-1][1]}
    return res

def fit(X, Y):
    mx, my = X.mean(0), Y.mean(0); x, y = X - mx, Y - my
    U, S, Vt = np.linalg.svd(y.T @ x); D = np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))])
    R = U @ D @ Vt; s = (S * np.diag(D)).sum() / (x ** 2).sum()
    return lambda Z: s * (Z - mx) @ R.T + my

def stats(v):
    return 'mean %.3f p90 %.3f max %.3f' % (v.mean(), np.percentile(v, 90), v.max())

a, b = sys.argv[1:3]
crs = int(sys.argv[3]) if len(sys.argv) > 3 else 25832
A, B = model(a), model(b)
for name, m in ((a, A), (b, B)):
    g = priors(name, crs); k = [x for x in m if x in g]
    X, Y = np.array([m[x] for x in k]), np.array([g[x] for x in k])
    r = fit(X, Y)(X) - Y
    print(name, 'registered', len(m), 'of', len(g), stages(name))
    print('  residual to GPS 3D', stats(np.linalg.norm(r, axis=1)), 'xy', stats(np.hypot(r[:, 0], r[:, 1])))
k = sorted(set(A) & set(B))
X, Y = np.array([A[x] for x in k]), np.array([B[x] for x in k])
r = np.linalg.norm(fit(X, Y)(X) - Y, axis=1)
print('common', len(k), 'centre difference', stats(r), 'only A', len(set(A) - set(B)), 'only B', len(set(B) - set(A)))
print('worst', [(k[j], round(float(r[j]), 2)) for j in np.argsort(-r)[:5]])
