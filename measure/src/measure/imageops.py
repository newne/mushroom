"""numpy 图像原语（无 cv2 / scipy / torch 依赖）。

为什么自己写：测量生产者在库房主机（`patrol` 侧）跑，那台机器只有 numpy 与 Pillow。
这里用 numpy 实现形态学、连通域、精确欧氏距离变换、孔洞填充与实例种子归属，
使 `measure` 能在现场那台机器上独立运行。

所有函数都是纯函数（不改入参、无随机、无隐式全局状态），便于单测与回放。
"""

from __future__ import annotations

import numpy as np

# 一维距离变换的"无穷大"哨兵。不能直接用 inf：(f + q²) 会变成 NaN。
_DT_INF = 1e20


# ---------- 基础形态学（方形结构元；按位移做与/或） ----------

def shift(arr: np.ndarray, dy: int, dx: int, *, fill: bool = False) -> np.ndarray:
    """平移数组，空出的边缘填 ``fill``。"""
    out = np.full_like(arr, fill)
    h, w = arr.shape
    ys0, ys1 = max(0, dy), min(h, h + dy)
    xs0, xs1 = max(0, dx), min(w, w + dx)
    yd0, yd1 = max(0, -dy), min(h, h - dy)
    xd0, xd1 = max(0, -dx), min(w, w - dx)
    if ys0 < ys1 and xs0 < xs1:
        out[ys0:ys1, xs0:xs1] = arr[yd0:yd1, xd0:xd1]
    return out


def bbox_of(mask: np.ndarray, pad: int = 0) -> tuple[int, int, int, int] | None:
    """掩膜外接框 ``(y0, x0, y1, x1)``（含端点）；全空返回 None。"""
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return None
    h, w = mask.shape
    return (max(0, int(ys.min()) - pad), max(0, int(xs.min()) - pad),
            min(h, int(ys.max()) + 1 + pad), min(w, int(xs.max()) + 1 + pad))


def crop(mask_or_gray: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    y0, x0, y1, x1 = box
    return mask_or_gray[y0:y1, x0:x1]


def block_downscale(arr: np.ndarray, factor: int) -> np.ndarray:
    """块平均降采样（``factor=1`` 原样返回）。

    为什么需要：真机帧 2880×1616 = 4.65 MPx，而距离变换/洪泛这些是逐行 Python 实现，
    全帧会是分钟级（实测 1/3 缩小都要 108 s）。**帧内标尺只需要袋级精度**，
    降到 1/2–1/4 后坐标再乘回来，误差远小于袋宽的不确定度。
    """
    if factor <= 1:
        return arr
    h, w = arr.shape[:2]
    h2, w2 = h // factor * factor, w // factor * factor
    core = arr[:h2, :w2]
    if arr.ndim == 2:
        return core.reshape(h2 // factor, factor, w2 // factor, factor).mean(axis=(1, 3))
    return core.reshape(h2 // factor, factor, w2 // factor, factor, -1).mean(axis=(1, 3))


def erode(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    """腐蚀；``radius <= 0`` 原样返回。"""
    out = mask
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dy or dx:
                out = out & shift(mask, dy, dx, fill=False)
    return out


def dilate(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    """膨胀；``radius <= 0`` 原样返回。"""
    out = mask
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dy or dx:
                out = out | shift(mask, dy, dx, fill=False)
    return out


def opening(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    """开运算：先腐蚀去孤立噪点，再膨胀回原尺寸。"""
    return dilate(erode(mask, radius), radius) if radius > 0 else mask


# ---------- 行程编码连通域（8 邻域） ----------

def _runs_of_row(row: np.ndarray) -> list[tuple[int, int]]:
    """一行的前景连续段 ``[(x0, x1), ...]``（闭区间）。"""
    xs = np.nonzero(row)[0]
    if len(xs) == 0:
        return []
    breaks = np.nonzero(np.diff(xs) > 1)[0]
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [len(xs) - 1]))
    return [(int(xs[s]), int(xs[e])) for s, e in zip(starts, ends)]


def _find(parent: list[int], x: int) -> int:
    root = x
    while parent[root] != root:
        root = parent[root]
    while parent[x] != root:
        parent[x], x = root, parent[x]
    return root


def label_components(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """8 邻域连通域标记，返回 ``(labels, sizes)``。

    ``sizes`` 与 ``labels`` 的编号一一对应（``sizes[k]`` = 分量 k 的像素数，下标 0 保留
    且恒为 0）——调用方可以安全地 ``sizes[labels[y, x]]``。

    只用 numpy：把每行转成行程，在**行程**上做并查集（真实帧几十万前景像素通常只有
    几百条行程），因此不依赖 cv2/scipy，也不逐像素做标量循环。
    """
    h, w = mask.shape
    parent: list[int] = [0]        # 并查集槽位（被并掉的槽位不再使用，但保留位置）
    fwd: list[int] = [0]           # 临时行程号 → parent 槽位
    nxt = 0
    per_row: list[list[tuple[int, int, int]]] = []   # [(x0, x1, 临时号)]

    for y in range(h):
        runs = _runs_of_row(mask[y])
        prev = per_row[-1] if per_row else []
        tagged: list[tuple[int, int, int]] = []
        for x0, x1 in runs:
            touching = [lab for (px0, px1, lab) in prev
                        if px0 <= x1 + 1 and x0 <= px1 + 1]
            if touching:
                lab = min(touching)
                for other in touching:
                    ra = _find(parent, fwd[lab])
                    rb = _find(parent, fwd[other])
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
            else:
                nxt += 1
                parent.append(nxt)
                fwd.append(nxt)
                lab = nxt
            tagged.append((x0, x1, lab))
        per_row.append(tagged)

    if nxt == 0:
        return np.zeros((h, w), np.int32), np.zeros(1, np.int64)

    # 临时号 → 根槽位 → 连续分量号（1..k）；未被使用的根不占号
    lab_root = np.zeros(nxt + 1, np.int64)
    for i in range(1, nxt + 1):
        lab_root[i] = _find(parent, fwd[i])
    counts = np.bincount(lab_root[1:], minlength=len(parent))
    used = np.nonzero(counts)[0]
    slot_to_comp = np.zeros(len(parent), np.int64)
    slot_to_comp[used] = np.arange(1, len(used) + 1)
    lab_comp = slot_to_comp[lab_root]

    labels = np.zeros((h, w), np.int32)
    for y in range(h):
        for (x0, x1, lab) in per_row[y]:
            labels[y, x0:x1 + 1] = lab_comp[lab]
    sizes = np.bincount(labels.ravel(), minlength=len(used) + 1)
    sizes[0] = 0
    return labels, sizes


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """填内部孔洞：菌盖高光留下的内部空洞会让"最宽行"量成两截。

    做法：从边界起做 4 邻域洪泛标记"外部背景"，**除外部背景以外的背景像素**就是
    内部孔洞。洪泛用一次性队列（对百万像素的帧也是线性时间）。

    结果必须是 ``mask | 孔洞``。曾经写成 ``~(background & ~outside)``：那返回的是
    "掩膜 + 外部背景"，小图上恰好还能过（掩膜全在里面），真实帧上直接吞掉整帧。
    """
    if not mask.any() or mask.all():
        return mask
    h, w = mask.shape
    background = ~mask
    outside = np.zeros_like(mask)
    queue: list[tuple[int, int]] = []
    for x in range(w):
        for y in (0, h - 1):
            if background[y, x] and not outside[y, x]:
                outside[y, x] = True
                queue.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if background[y, x] and not outside[y, x]:
                outside[y, x] = True
                queue.append((y, x))
    head = 0
    while head < len(queue):
        y, x = queue[head]
        head += 1
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and background[ny, nx] and not outside[ny, nx]:
                outside[ny, nx] = True
                queue.append((ny, nx))
    return mask | (background & ~outside)


# ---------- 精确欧氏距离变换（Felzenszwalb–Huttenlocher，可分离） ----------

def _dt_1d(f: np.ndarray) -> np.ndarray:
    """一维下包络 ``d(q) = min_p (q-p)² + f(p)``。"""
    n = len(f)
    d = np.empty(n)
    v = np.zeros(n, np.int32)
    z = np.empty(n + 1)
    k = 0
    v[0] = 0
    z[0] = -_DT_INF
    z[1] = _DT_INF
    for q in range(1, n):
        while True:
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
            if s <= z[k]:
                k -= 1
            else:
                break
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = _DT_INF
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        d[q] = (q - v[k]) ** 2 + f[v[k]]
    return d


def distance_transform(mask: np.ndarray) -> np.ndarray:
    """前景像素到最近背景像素的欧氏距离（float32）。

    全前景或全背景时返回全 0（调用方按"无法拆分"处理，而不是拿 0 当距离）。
    """
    h, w = mask.shape
    if not mask.any() or mask.all():
        return np.zeros((h, w), np.float32)
    grid = np.where(mask, _DT_INF, 0.0)
    buf = np.empty(max(h, w))
    for y in range(h):
        buf[:w] = grid[y]
        grid[y] = _dt_1d(buf[:w])
    for x in range(w):
        buf[:h] = grid[:, x]
        grid[:, x] = _dt_1d(buf[:h])
    return np.sqrt(grid).astype(np.float32)


# ---------- 距离峰值 → 实例种子 ----------

def local_maxima(a: np.ndarray, window: int = 3) -> np.ndarray:
    """窗口内极大（与邻居相等也算，平台不会被漏掉）。"""
    keep = np.ones(a.shape, bool)
    r = max(1, window // 2)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy or dx:
                keep &= a >= shift(a, dy, dx, fill=-1.0)
    return keep


def pick_seeds(dist: np.ndarray, *, min_distance: int = 12, abs_min: float = 4.0,
               max_seeds: int = 64) -> np.ndarray:
    """距离图峰值 → NMS 后的种子 ``[[y, x, dist], ...]``（按峰值降序）。

    NMS 用**网格哈希**（格子边长 = `min_distance`，只查相邻 9 格）而不是全对比较：
    真机帧的 top-hat 掩膜上峰值可上万，全对比较是 O(P²) ⇒ 分钟级（实测卡死过）；
    网格法把每步降到 O(1)，整体 O(P log P)（排序）。
    """
    peaks = local_maxima(dist, 3) & (dist >= abs_min)
    ys, xs = np.nonzero(peaks)
    if len(ys) == 0:
        return np.zeros((0, 3), np.float32)
    vals = dist[ys, xs]
    order = np.argsort(-vals)
    ys, xs, vals = ys[order], xs[order], vals[order]
    cell = max(1, int(min_distance))
    r2 = float(min_distance) ** 2
    occupied: dict[tuple[int, int], list[tuple[float, float]]] = {}
    chosen: list[tuple[float, float, float]] = []
    for y, x, v in zip(ys, xs, vals):
        cy, cx = int(y) // cell, int(x) // cell
        ok = True
        for gy in (cy - 1, cy, cy + 1):
            for gx in (cx - 1, cx, cx + 1):
                for (py, px) in occupied.get((gy, gx), ()):  # noqa: E501
                    if (py - y) ** 2 + (px - x) ** 2 < r2:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                break
        if ok:
            chosen.append((float(y), float(x), float(v)))
            occupied.setdefault((cy, cx), []).append((float(y), float(x)))
        if len(chosen) >= max_seeds:
            break
    return np.asarray(chosen, np.float32).reshape(-1, 3)


def paint_seeds(shape: tuple[int, int], seeds: np.ndarray) -> np.ndarray:
    """每个像素归到"最近的种子"（按列分块向量化）；无种子时全 -1。

    距离张量显式写成 ``(h, k, m)``。踩过两次广播坑：``(1,m)`` 与 ``(1,k)`` 在 k≠m 时
    根本不能广播，NumPy 会静默取到错的一维长度，报错位置还离现场很远。
    """
    h, w = shape
    owner = np.full((h, w), -1, np.int32)
    if len(seeds) == 0:
        return owner
    seed_y = np.asarray(seeds[:, 0], np.float32)
    seed_x = np.asarray(seeds[:, 1], np.float32)
    rows = np.arange(h, dtype=np.float32)[:, None]            # (h, 1)
    dy = (rows - seed_y[None, :]) ** 2                        # (h, k)
    chunk = max(1, min(w, 4_000_000 // max(1, len(seeds) * h)))
    for x0 in range(0, w, chunk):
        x1 = min(w, x0 + chunk)
        cols = np.arange(x0, x1, dtype=np.float32)[None, :]   # (1, m)
        dx = (cols - seed_x[:, None]) ** 2                    # (k, m)
        d2 = dy[:, :, None] + dx[None, :, :]                  # (h, k, m)
        owner[:, x0:x1] = np.argmin(d2, axis=1).astype(np.int32)
    return owner
