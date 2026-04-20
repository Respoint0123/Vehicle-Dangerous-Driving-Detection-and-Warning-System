"""
透视变换标定工具
用法：python3 warp_calibrator.py [视频路径] [帧号(可选)]

操作说明：
  鼠标拖拽  — 移动红色控制点（4个顶点）
  A / D     — 切换到上一帧 / 下一帧（步长30）
  S         — 保存坐标到 detector.py 并退出
  Q / ESC   — 退出不保存
"""

import sys
import re
import cv2
import numpy as np

VIDEO_PATH = sys.argv[1] if len(sys.argv) > 1 else "/Users/hua/Desktop/2025毕业设计/project_video.mp4"
FRAME_IDX  = int(sys.argv[2]) if len(sys.argv) > 2 else 90
DETECTOR_PATH = "/Users/hua/Desktop/2025毕业设计/code/detector.py"

# ── 读取指定帧 ──────────────────────────────────────────────
cap = cv2.VideoCapture(VIDEO_PATH)
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

def read_frame(idx):
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(idx, total - 1)))
    ret, f = cap.read()
    return f if ret else None

frame = read_frame(FRAME_IDX)
H, W = frame.shape[:2]

# ── 初始控制点（像素坐标）──────────────────────────────────
pts = np.array([
    [int(0.15 * W), int(1.00 * H)],   # 0 左下
    [int(0.44 * W), int(0.63 * H)],   # 1 左上
    [int(0.58 * W), int(0.63 * H)],   # 2 右上
    [int(0.88 * W), int(1.00 * H)],   # 3 右下
], dtype=np.float32)

COLORS = [(0,0,255),(0,255,0),(255,128,0),(255,0,255)]
LABELS = ["0:左下","1:左上","2:右上","3:右下"]
RADIUS = 10
PAD = 100   # 画布四周留白，让控制点出框时仍可见

drag_idx = -1

def warp_preview(img, p):
    dst = np.float32([
        [0.20 * W, H],
        [0.20 * W, 0],
        [0.80 * W, 0],
        [0.80 * W, H],
    ])
    M = cv2.getPerspectiveTransform(p, dst)
    return cv2.warpPerspective(img, M, (W, H))

def draw(img, p):
    # 在原图四周加 PAD 灰色留白
    out = cv2.copyMakeBorder(img, PAD, PAD, PAD, PAD,
                             cv2.BORDER_CONSTANT, value=(40, 40, 40))
    # 控制点坐标偏移到 padded 画布上
    pp = p.astype(np.int32) + PAD
    cv2.polylines(out, [pp], True, (0,255,255), 2)
    for i, (x, y) in enumerate(pp):
        cv2.circle(out, (int(x), int(y)), RADIUS, COLORS[i], -1)
        cv2.putText(out, LABELS[i], (int(x)+12, int(y)+6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLORS[i], 2)
    cv2.putText(out, "拖拽控制点  A/D换帧  S保存  Q退出",
                (PAD + 10, PAD + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255,255,255), 2)
    return out

def on_mouse(event, x, y, flags, _):
    global drag_idx, pts
    # 鼠标坐标减去 PAD 得到原图坐标系
    ox, oy = x - PAD, y - PAD
    if event == cv2.EVENT_LBUTTONDOWN:
        for i, (px, py) in enumerate(pts.astype(int)):
            if abs(ox - px) < RADIUS + 5 and abs(oy - py) < RADIUS + 5:
                drag_idx = i
                break
    elif event == cv2.EVENT_MOUSEMOVE and drag_idx >= 0:
        pts[drag_idx] = [ox, oy]
    elif event == cv2.EVENT_LBUTTONUP:
        drag_idx = -1

cv2.namedWindow("calibrator", cv2.WINDOW_NORMAL)
cv2.resizeWindow("calibrator", min((W + 2*PAD)*2, 1800), min(H + 2*PAD, 920))
cv2.setMouseCallback("calibrator", on_mouse)

while True:
    left  = draw(frame, pts)
    right = warp_preview(frame, pts)
    # 右侧画辅助线（鸟瞰图中间竖线，帮助判断左右对称）
    cv2.line(right, (W//2, 0), (W//2, H), (0,200,200), 1)
    # 右侧加同样的 padding 使高度匹配
    right_padded = cv2.copyMakeBorder(right, PAD, PAD, PAD, PAD,
                                      cv2.BORDER_CONSTANT, value=(40, 40, 40))
    combined = np.hstack([left, right_padded])
    cv2.imshow("calibrator", combined)

    key = cv2.waitKey(30) & 0xFF
    if key in (ord('q'), 27):
        print("退出，未保存。")
        break
    elif key == ord('a'):
        FRAME_IDX = max(0, FRAME_IDX - 30)
        frame = read_frame(FRAME_IDX)
    elif key == ord('d'):
        FRAME_IDX = min(total - 1, FRAME_IDX + 30)
        frame = read_frame(FRAME_IDX)
    elif key == ord('s'):
        # 转为比例坐标
        src_ratio = [(round(x/W, 3), round(y/H, 3)) for x, y in pts.astype(float)]
        new_val = (
            f"    WARP_SRC = [\n"
            f"        ({src_ratio[0][0]}, {src_ratio[0][1]}),   # 左下\n"
            f"        ({src_ratio[1][0]}, {src_ratio[1][1]}),   # 左上\n"
            f"        ({src_ratio[2][0]}, {src_ratio[2][1]}),   # 右上\n"
            f"        ({src_ratio[3][0]}, {src_ratio[3][1]}),   # 右下\n"
            f"    ]"
        )
        with open(DETECTOR_PATH, "r") as f:
            code = f.read()
        code = re.sub(
            r"    WARP_SRC = \[.*?\]",
            new_val,
            code,
            flags=re.DOTALL,
        )
        with open(DETECTOR_PATH, "w") as f:
            f.write(code)
        print("已保存 WARP_SRC 到 detector.py：")
        for i, (rx, ry) in enumerate(src_ratio):
            print(f"  {LABELS[i]}: ({rx}, {ry})  →  像素 ({int(rx*W)}, {int(ry*H)})")
        break

cap.release()
cv2.destroyAllWindows()
