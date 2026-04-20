"""
visualize_pipeline.py
从真实视频中抽取一帧，逐步可视化车道线检测的完整流程：
  Step 1 - 原始帧
  Step 2 - HLS S通道二值图 + Sobel边缘二值图 + 融合二值图
  Step 3 - 透视变换（鸟瞰图）
  Step 4 - 直方图 + 滑窗搜索
  Step 5 - 多项式拟合结果（鸟瞰图）
  Step 6 - 反投影到原图（最终结果）

输出：ppt_assets/pipeline_visualization.png
用法：python visualize_pipeline.py [视频文件] [帧号]
"""

import sys
import cv2
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────────────────────────────
VIDEO   = sys.argv[1] if len(sys.argv) > 1 else "project_video.mp4"
FRAME_N = int(sys.argv[2]) if len(sys.argv) > 2 else 180
OUT_DIR = Path("ppt_assets")
OUT_DIR.mkdir(exist_ok=True)

plt.rcParams['font.family'] = ['STHeiti', 'Hiragino Sans GB', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

C_BG   = '#F5F7FA'
C_DARK = '#1A3A6B'
C_GOLD = '#B8922A'
C_MUTED= '#5A6A82'

# ── 从 detector.py 动态读取 WARP_SRC ─────────────────────────────────────────
import importlib.util, os
_spec = importlib.util.spec_from_file_location(
    "detector",
    os.path.join(os.path.dirname(__file__), "code", "detector.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
WARP_SRC = _mod.Detector.WARP_SRC
WARP_DST = _mod.Detector.WARP_DST
print(f"WARP_SRC loaded: {WARP_SRC}")

# ── 读取帧 ────────────────────────────────────────────────────────────────────
cap = cv2.VideoCapture(VIDEO)
cap.set(cv2.CAP_PROP_POS_FRAMES, FRAME_N)
ret, frame_bgr = cap.read()
cap.release()
if not ret:
    raise RuntimeError(f"无法读取视频 {VIDEO} 第 {FRAME_N} 帧")

h, w = frame_bgr.shape[:2]
frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

# ── Step 1: 原始帧 ────────────────────────────────────────────────────────────
orig = frame_rgb.copy()

# ── Step 2: 二值化 ────────────────────────────────────────────────────────────
hls   = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HLS)
s_ch  = hls[:, :, 2]
s_bin = ((s_ch >= 120) & (s_ch <= 255)).astype(np.uint8) * 255

gray  = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
sx    = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
abs_sx = np.absolute(sx)
scaled = np.uint8(255 * abs_sx / (np.max(abs_sx) + 1e-6))
sx_bin = ((scaled >= 20) & (scaled <= 100)).astype(np.uint8) * 255

combined = np.zeros_like(s_bin)
combined[(s_bin > 0) | (sx_bin > 0)] = 255

# ── Step 3: 透视变换 ──────────────────────────────────────────────────────────
src_pts = np.float32([[x * w, y * h] for x, y in WARP_SRC])
dst_pts = np.float32([[x * w, y * h] for x, y in WARP_DST])
M    = cv2.getPerspectiveTransform(src_pts, dst_pts)
Minv = cv2.getPerspectiveTransform(dst_pts, src_pts)

warped_bin = cv2.warpPerspective(combined, M, (w, h), flags=cv2.INTER_LINEAR)

# 原图上画梯形ROI
orig_with_roi = orig.copy()
roi_pts = src_pts.astype(np.int32)
cv2.polylines(orig_with_roi, [roi_pts], True, (184, 146, 42), 3)
for pt in roi_pts:
    cv2.circle(orig_with_roi, tuple(pt), 8, (184, 146, 42), -1)

# ── Step 4: 直方图 + 滑窗 ─────────────────────────────────────────────────────
histogram = np.sum(warped_bin[h // 2:, :], axis=0).astype(float)
histogram = np.convolve(histogram, np.ones(40) / 40, mode='same')

mid = w // 2
threshold = histogram.max() * 0.1

# 左半：从中间往左扫，找第一个超过阈值的峰（与 detector.py 一致）
left_half = histogram[:mid].copy()
left_half[left_half < threshold] = 0
left_x = mid - 1
for x in range(mid - 1, -1, -1):
    if left_half[x] > 0:
        left_x = x
        break

# 右半：从中间往右扫，找第一个超过阈值的峰
right_half = histogram[mid:].copy()
right_half[right_half < threshold] = 0
right_x = mid
for x in range(0, mid):
    if right_half[x] > 0:
        right_x = mid + x
        break

n_windows = 9
win_h  = h // n_windows
margin = 80
min_px = 15

nz     = warped_bin.nonzero()
nz_y   = np.array(nz[0])
nz_x   = np.array(nz[1])

left_inds, right_inds = [], []
cur_lx, cur_rx = left_x, right_x
win_boxes = []  # (x1,y1,x2,y2, side)

for i in range(n_windows):
    y_lo = h - (i + 1) * win_h
    y_hi = h - i * win_h
    for cur_x, inds, side in [(cur_lx, left_inds, 'L'), (cur_rx, right_inds, 'R')]:
        good = ((nz_y >= y_lo) & (nz_y < y_hi) &
                (nz_x >= cur_x - margin) & (nz_x < cur_x + margin)).nonzero()[0]
        inds.append(good)
        win_boxes.append((cur_x - margin, y_lo, cur_x + margin, y_hi, side))
        if len(good) >= min_px:
            if side == 'L':
                cur_lx = int(np.mean(nz_x[good]))
            else:
                cur_rx = int(np.mean(nz_x[good]))

left_inds  = np.concatenate(left_inds)
right_inds = np.concatenate(right_inds)

# ── Step 5: 多项式拟合 ────────────────────────────────────────────────────────
left_poly = right_poly = None
if len(left_inds) >= 10:
    left_poly  = np.polyfit(nz_y[left_inds],  nz_x[left_inds],  2)
if len(right_inds) >= 10:
    right_poly = np.polyfit(nz_y[right_inds], nz_x[right_inds], 2)

# 滑窗可视化图（彩色）
win_vis = cv2.cvtColor(warped_bin, cv2.COLOR_GRAY2RGB)
win_vis[nz_y[left_inds],  nz_x[left_inds]]  = [255, 80,  80]
win_vis[nz_y[right_inds], nz_x[right_inds]] = [80,  80, 255]
for x1, y1, x2, y2, side in win_boxes:
    color = (255, 80, 80) if side == 'L' else (80, 80, 255)
    cv2.rectangle(win_vis, (x1, y1), (x2, y2), color, 2)

# 拟合曲线可视化
fit_vis = win_vis.copy()
ys = np.linspace(0, h - 1, 300).astype(int)
if left_poly is not None:
    lxs = np.clip(np.polyval(left_poly, ys).astype(int), 0, w - 1)
    for y, x in zip(ys, lxs):
        cv2.circle(fit_vis, (x, y), 3, (255, 220, 0), -1)
if right_poly is not None:
    rxs = np.clip(np.polyval(right_poly, ys).astype(int), 0, w - 1)
    for y, x in zip(ys, rxs):
        cv2.circle(fit_vis, (x, y), 3, (255, 220, 0), -1)

# ── Step 6: 反投影到原图 ──────────────────────────────────────────────────────
result = frame_rgb.copy()
if left_poly is not None and right_poly is not None:
    overlay = np.zeros((h, w, 3), dtype=np.uint8)
    lxs = np.clip(np.polyval(left_poly,  ys).astype(int), 0, w - 1)
    rxs = np.clip(np.polyval(right_poly, ys).astype(int), 0, w - 1)
    pts_l = np.stack([lxs, ys], axis=1)
    pts_r = np.stack([rxs, ys], axis=1)[::-1]
    pts   = np.vstack([pts_l, pts_r]).reshape(-1, 1, 2).astype(np.float32)
    pts_o = cv2.perspectiveTransform(pts, Minv).astype(np.int32)
    cv2.fillPoly(overlay, [pts_o], (0, 200, 0))
    result_bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
    cv2.addWeighted(overlay, 0.4, result_bgr, 1.0, 0, result_bgr)
    result = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
    # 车道线
    for poly, color in [(left_poly, (255, 120, 0)), (right_poly, (0, 120, 255))]:
        lxs2 = np.clip(np.polyval(poly, ys).astype(int), 0, w - 1)
        pts_w = np.array([[[x, y]] for x, y in zip(lxs2, ys)], dtype=np.float32)
        pts_o2 = cv2.perspectiveTransform(pts_w, Minv).astype(np.int32)
        for i in range(len(pts_o2) - 1):
            cv2.line(result, tuple(pts_o2[i][0]), tuple(pts_o2[i+1][0]), color, 4)

# ── 绘图布局 ──────────────────────────────────────────────────────────────────
# 布局：2行 × 4列（第4列第1行放直方图，其余放图像）
# 行1: 原图+ROI | S通道二值 | Sobel二值 | 融合二值
# 行2: 鸟瞰图   | 滑窗搜索  | 多项式拟合 | 最终结果

fig = plt.figure(figsize=(18, 9.5), facecolor=C_BG)
fig.subplots_adjust(left=0.02, right=0.98, top=0.91, bottom=0.04,
                    hspace=0.28, wspace=0.06)

gs = gridspec.GridSpec(2, 4, figure=fig)

panels = [
    (gs[0, 0], orig_with_roi,              "① 原始帧（梯形ROI）",    False),
    (gs[0, 1], s_bin,                      "② HLS S通道二值图",      True),
    (gs[0, 2], sx_bin,                     "③ Sobel x方向边缘",      True),
    (gs[0, 3], combined,                   "④ 融合二值图",            True),
    (gs[1, 0], cv2.cvtColor(cv2.warpPerspective(frame_bgr, M, (w, h)), cv2.COLOR_BGR2RGB),
                                           "⑤ 透视变换（鸟瞰图）",    False),
    (gs[1, 1], win_vis,                    "⑥ 滑动窗口搜索",          False),
    (gs[1, 2], fit_vis,                    "⑦ 二阶多项式拟合",        False),
    (gs[1, 3], result,                     "⑧ 反投影至原图",          False),
]

for spec, img, title, is_gray in panels:
    ax = fig.add_subplot(spec)
    if is_gray:
        ax.imshow(img, cmap='gray', vmin=0, vmax=255)
    else:
        ax.imshow(img)
    ax.set_title(title, fontsize=12, color=C_DARK, fontweight='bold', pad=5)
    ax.axis('off')
    for spine in ax.spines.values():
        spine.set_edgecolor(C_MUTED)
        spine.set_linewidth(0.8)

fig.suptitle(f'车道线检测完整流程可视化  —  {Path(VIDEO).name}  第 {FRAME_N} 帧',
             fontsize=15, color=C_DARK, fontweight='bold', y=0.97)

out_path = OUT_DIR / "pipeline_visualization.png"
plt.savefig(out_path, dpi=150, bbox_inches='tight', facecolor=C_BG)
plt.close()
print(f"✓ 已保存：{out_path}")
