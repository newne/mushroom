"""L3：菇体（幼菇/菌杆）实例分割——纯 numpy，不训练。

做法：**top-hat**（灰度 − 形态学开）把"长在亮菌包上的细亮凸起"提出来，
再连通域 + 距离变换峰值拆实例。与 `measure.baseline` 的整块兜底不同：
这里要的是**每根杆的竖直跨度**，所以实例必须拆开、不能整块算。

诚实边界：
- 帽阶段**样本=0**（第 13 天矮菇无帽）⇒ 本模块**不区分杆与帽**，只出"菇体实例"；
  杆/帽分离（L4）等长高后样本；
- 阈值全在 `FruitConfig` 里，**没有内置猜测的物理常数**（mm 换算不在这里）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from measure.imageops import (
    bbox_of,
    block_downscale,
    distance_transform,
    label_components,
    paint_seeds,
    pick_seeds,
    shift,
)


@dataclass
class FruitInstance:
    """一个菇体实例（像素量）。"""

    cx: float
    cy: float
    x0: int
    y0: int
    x1: int
    y1: int
    height_px: int          # 竖直跨度（= 投影杆长的像素口径）
    width_px: int
    area_px: int
    base_y: int             # 底部（靠菌包那端）
    tip_y: int              # 顶部（尖端）
    on_bag: bool            # 是否贴着菌包（L2 ROI 先验）
    touching: bool = False  # 与邻实例粘连（拆分不完全）


@dataclass
class FruitConfig:
    #: **工作分辨率降采样倍数**（1=原图；2=1/2）。菇体跨度在真机上是几十像素，
    #: 降到 1/2 仍有 ~20px，够量；全分辨率是十分钟量级（见票 15 性能一节）。
    downscale: int = 2
    #: **袋子肩部 ROI**（原始坐标 ``(y0, y1, x0, x1)``；None = 全帧）。
    #: 菇只长在袋肩附近：裁掉上半屏（货架底面）能省一大半，而且是**语义上**该做的
    #: ——远排本来就要标 `unreliable`。
    roi: tuple[int, int, int, int] | None = None
    #: top-hat 结构元半径（工作分辨率像素）
    tophat_radius: int = 9
    #: top-hat 阈值：None = 自适应（均值 + 2σ，**真机推荐**）；给值则固定
    tophat_threshold: float | None = None
    #: 最小面积（工作分辨率像素）：小于它当纹理噪点
    min_area_px: int = 25
    #: **只有足够大的组件才拆**（拆要跑一次距离变换，逐组件 DT 是 Python 循环：
    #: 上千个小碎块会把单帧拖到数秒）。小于它的直接当一个实例。
    split_min_area_px: int = 400
    #: 单帧最多处理的组件数（按面积从大到小取；防病态画面）
    max_components: int = 800
    #: 最大面积（工作分辨率像素）：超过当"大块"丢弃（那不是单根杆）
    max_area_px: int = 3000
    #: 实例拆分的种子间距
    seed_min_distance_px: int = 8
    #: 与菌包掩膜的距离小于它算 on_bag
    bag_proximity_px: int = 25
    #: 与菌包掩膜的膨胀倍数（用于 on_bag 判定）
    bag_dilate_px: int = 2
    #: 峰值候选上限（防病态画面炸内存/时间）
    max_candidates: int = 20000


def _gray_erode(g: np.ndarray, r: int) -> np.ndarray:
    out = g
    for dy, dx in ((r, 0), (-r, 0), (0, r), (0, -r)):
        out = np.minimum(out, shift(g, dy, dx, fill=255.0))
    return out


def _gray_dilate(g: np.ndarray, r: int) -> np.ndarray:
    out = g
    for dy, dx in ((r, 0), (-r, 0), (0, r), (0, -r)):
        out = np.maximum(out, shift(g, dy, dx, fill=0.0))
    return out


def top_hat(gray: np.ndarray, radius: int) -> np.ndarray:
    """白 top-hat：细亮结构（长在亮面上的凸起）。"""
    return gray - _gray_dilate(_gray_erode(gray, radius), radius)


def find_fruitbodies(gray: np.ndarray, bag_mask: np.ndarray | None = None,
                     cfg: FruitConfig | None = None, *,
                     origin: tuple[int, int] = (0, 0)) -> tuple[list[FruitInstance], dict]:
    """一帧灰度图 → 菇体实例列表 + 诊断 dict。

    **返回到原图坐标**：``origin`` 是 ``gray`` 左上角在原图中的 ``(y0, x0)``，
    ``cfg.downscale`` 会内部降采样后把坐标乘回去。这样调用方可以只喂 ROI。

    性能：全分辨率全帧是**十分钟量级**（top-hat 位移 + 峰值 NMS + 逐组件 DT），
    所以默认 ``downscale=2``；配合 ``cfg.roi``（袋肩带）后是秒级/帧。
    """
    cfg = cfg or FruitConfig()
    if gray.ndim != 2:
        raise ValueError("需要 2D 灰度图")
    ds = max(1, int(cfg.downscale))
    work = block_downscale(gray.astype(np.float32), ds) if ds > 1 else gray.astype(np.float32)
    y_off, x_off = int(origin[0]), int(origin[1])

    th = top_hat(work, max(3, int(cfg.tophat_radius)))
    if cfg.tophat_threshold is None:
        thr = float(th.mean() + 2.0 * th.std())
        thr_src = "mean+2sigma"
    else:
        thr, thr_src = float(cfg.tophat_threshold), "fixed"
    mask = th > thr
    # 候选上限：掩膜太碎时先只留最强的那些（防病态画面）
    lab, sizes = label_components(mask)
    diag: dict = {"tophat_threshold": thr, "thr_source": thr_src, "downscale": ds,
                  "n_components": int(len(sizes) - 1), "n_instances": 0}

    bag_near = bag_mask
    if bag_mask is not None and cfg.bag_dilate_px > 0:
        bag_near = _gray_dilate(bag_mask.astype(np.float32), max(1, cfg.bag_dilate_px)) > 0

    out: list[FruitInstance] = []
    min_area = cfg.min_area_px
    max_area = cfg.max_area_px
    # 按面积从大到小、最多处理 max_components 个（大块更可能是真菇体；碎块多为袋面纹理）
    order = [i for i in np.argsort(-sizes[1:]) + 1 if min_area <= sizes[i] <= max_area]
    diag["n_candidates"] = len(order)
    order = order[: max(1, int(cfg.max_components))]
    for i in order:
        box = bbox_of(lab == i, pad=1)
        if box is None:
            continue
        comp = (lab[box[0]:box[2], box[1]:box[3]] == i)
        ys_c, xs_c = np.nonzero(comp)
        h_c = int(ys_c.max() - ys_c.min() + 1)
        w_c = int(xs_c.max() - xs_c.min() + 1)
        area_c = int(comp.sum())
        if h_c >= 1.5 * w_c or area_c < cfg.split_min_area_px:
            # 细长竖直结构就是一根杆；小碎块也不值得拆（拆一次 DT 太贵）
            parts = [(comp, box)]
        else:
            seeds = pick_seeds(distance_transform(comp),
                               min_distance=max(2, cfg.seed_min_distance_px),
                               abs_min=max(1.0, cfg.seed_min_distance_px * 0.3),
                               max_seeds=16)
            if len(seeds) <= 1:
                parts = [(comp, box)]
            else:
                owner = paint_seeds(comp.shape, seeds)
                parts = [(comp & (owner == k), box) for k in range(len(seeds))]
        for part, (by0, bx0, _, _) in parts:
            ys, xs = np.nonzero(part)
            if len(ys) < min_area:
                continue
            # 工作分辨率坐标 → 原图坐标
            ys_full = (ys + by0) * ds + y_off
            xs_full = (xs + bx0) * ds + x_off
            y0, y1 = int(ys_full.min()), int(ys_full.max())
            x0, x1 = int(xs_full.min()), int(xs_full.max())
            cy, cx = float(ys_full.mean()), float(xs_full.mean())
            on_bag = True
            if bag_near is not None:
                yy = int(np.clip((cy - y_off) / ds, 0, bag_near.shape[0] - 1))
                xx = int(np.clip((cx - x_off) / ds, 0, bag_near.shape[1] - 1))
                on_bag = bool(bag_near[max(0, yy - 5):yy + 6, max(0, xx - 5):xx + 6].any())
            out.append(FruitInstance(
                cx=cx, cy=cy, x0=x0, y0=y0, x1=x1, y1=y1,
                height_px=int(round((y1 - y0 + 1))), width_px=int(round(x1 - x0 + 1)),
                area_px=int(len(ys)) * ds * ds, base_y=y1, tip_y=y0, on_bag=on_bag,
                touching=h_c < 1.5 * w_c and len(parts) > 1,
            ))
    diag["n_instances"] = len(out)
    return out, diag


def median_height_px(instances: list[FruitInstance]) -> float | None:
    """实例竖直跨度的中位数（投影杆长的像素口径）；无实例返回 None。"""
    if not instances:
        return None
    return float(np.median([i.height_px for i in instances]))
