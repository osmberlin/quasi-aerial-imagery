"""route_compare.py OLD_DIR NEW_DIR: compare two ground products window by window (too large to load whole).
Windows of 1024 px at the centre of every new tile; seams on all tile edges."""
import sys, glob, json, numpy as np, rasterio, cv2
from rasterio.windows import Window
OLD, NEW = sys.argv[1:3]
tiles = [f for f in sorted(glob.glob(NEW + "/tiles-ground_nearest/*.tif")) if f.rsplit("/", 1)[1].count(".") == 1]
boxes = [rasterio.open(f).bounds for f in tiles]
res = {"tiles": len(tiles)}
for prod in ("base", "ground_nearest", "ground_twoband"):
    A, B = rasterio.open(f"{OLD}/{prod}.tif"), rasterio.open(f"{NEW}/{prod}.tif")
    R = []; Sh = []
    for bx in boxes:
        cx, cy = (bx.left + bx.right) / 2, (bx.top + bx.bottom) / 2
        ra, ca = A.index(cx, cy); rb, cb = B.index(cx, cy)
        x = A.read(window=Window(ca - 512, ra - 512, 1024, 1024), boundless=True, fill_value=0); y = B.read(window=Window(cb - 512, rb - 512, 1024, 1024), boundless=True, fill_value=0)
        ok = (x[3] > 0) & (y[3] > 0)
        if ok.mean() < .3: continue
        gx, gy = x[:3].astype(np.float32).mean(0), y[:3].astype(np.float32).mean(0)
        w = ok.astype(np.float32); bl = lambda v: cv2.GaussianBlur(v * w, (0, 0), 25) / np.maximum(cv2.GaussianBlur(w, (0, 0), 25), 1e-3)
        core = cv2.erode(ok.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        lap = lambda g: float(np.abs(cv2.Laplacian(g, cv2.CV_32F))[core].mean())
        d = np.abs(x[:3].astype(int) - y[:3].astype(int)).max(0)[ok]
        R.append(((d > 0).mean(), (d > 8).mean(), np.abs(gx - gy)[ok].mean(), np.abs(bl(gx) - bl(gy))[ok].mean(), lap(gx), lap(gy)))
        if ok.mean() > .95:
            (dx, dy), q = cv2.phaseCorrelate(gx.astype(np.float64), gy.astype(np.float64), cv2.createHanningWindow((1024, 1024), cv2.CV_64F))
            if q > .05: Sh.append(np.hypot(dx, dy))
    R = np.array(R)
    res[prod] = {"windows": len(R), "changed": round(float(R[:, 0].mean()), 4), "gt8": round(float(R[:, 1].mean()), 4), "mean_abs_grey": round(float(R[:, 2].mean()), 3),
                 "lowpass50cm_mean_abs": round(float(R[:, 3].mean()), 3), "lowpass50cm_worst_window": round(float(R[:, 3].max()), 3), "laplacian_old_new": [round(float(R[:, 4].mean()), 3), round(float(R[:, 5].mean()), 3)],
                 "shift_px_p50_p90": [round(float(q), 3) for q in np.percentile(Sh, [50, 90])] if Sh else None, "size_old_new": [A.width, A.height, B.width, B.height]}
    sa, sb = (A.height // 16, A.width // 16), (B.height // 16, B.width // 16)
    res[prod]["valid_share_1_16_old_new"] = [int((A.read(4, out_shape=sa) > 0).sum()), int((B.read(4, out_shape=sb) > 0).sum())]
    # seams
    rat, rat_old = [], []
    for bx in boxes:
        for vertical in (False, True):
            xx, yy = (bx.right, (bx.top + bx.bottom) / 2) if vertical else ((bx.left + bx.right) / 2, bx.bottom)
            r, c = B.index(xx, yy)
            win = Window(c - 8, r - 1250, 16, 2500) if vertical else Window(c - 1250, r - 8, 2500, 16)
            for D, out in ((B, rat), (A, rat_old)):
                z = D.read(window=win, boundless=True, fill_value=0).astype(np.float32)
                if vertical: z = z.transpose(0, 2, 1)
                g, v = z[:3].mean(0), z[3] > 0
                st = lambda k: (np.abs(g[k - 1] - g[k])[v[k - 1] & v[k]].mean() if (v[k - 1] & v[k]).sum() > 300 else None)
                e = st(8); ref = [s for s in (st(5), st(2), st(11), st(14)) if s]
                if e and ref: out.append(e / np.mean(ref))
    res[prod]["seam_ratio_mean_max_edges"] = [round(float(np.mean(rat)), 3), round(float(np.max(rat)), 3), len(rat)]
    res[prod]["old_on_same_lines"] = [round(float(np.mean(rat_old)), 3), round(float(np.max(rat_old)), 3), len(rat_old)]
print(json.dumps(res))
