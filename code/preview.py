"""
视频检测预览工具
用法：python3 preview.py [视频路径]

操作：
  空格   — 暂停 / 继续
  →      — 逐帧前进（暂停时）
  R      — 从头重播
  B      — 切换显示鸟瞰图调试视图
  Q/ESC  — 退出
"""

import sys
import cv2
import numpy as np
from detector import Detector

VIDEO_PATH = sys.argv[1] if len(sys.argv) > 1 else "/Users/hua/Desktop/2025毕业设计/Lane.mp4"

cap = cv2.VideoCapture(VIDEO_PATH)
if not cap.isOpened():
    print(f"无法打开视频：{VIDEO_PATH}")
    sys.exit(1)

fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
det   = Detector(pixels_per_meter=15.0, fps=fps)

cv2.namedWindow("preview", cv2.WINDOW_NORMAL)
cv2.resizeWindow("preview", 1280, 720)

paused    = False
debug     = False   # B 键切换
frame_idx = 0
last_display = None

def make_display(frame, result, det, debug):
    h, w = frame.shape[:2]
    annotated = result.annotated_frame

    # 进度条
    out = annotated.copy()
    progress = int(w * frame_idx / max(total, 1))
    cv2.rectangle(out, (0, h - 6), (progress, h), (0, 200, 100), -1)
    cv2.putText(out, f"{frame_idx}/{total}", (w - 140, h - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

    if not debug:
        return out

    # 调试视图：左=标注帧，右=二值鸟瞰图
    binary = det._binary_warped(frame)
    bird = cv2.cvtColor((binary * 255), cv2.COLOR_GRAY2BGR)

    # 在鸟瞰图上画多项式曲线
    if det._left_poly is not None or det._right_poly is not None:
        ys = np.linspace(0, h - 1, 100).astype(int)
        for poly, color in [(det._left_poly, (255,120,0)), (det._right_poly, (0,120,255))]:
            if poly is not None:
                xs = np.clip(np.polyval(poly, ys).astype(int), 0, w - 1)
                pts = np.stack([xs, ys], axis=1).reshape(-1, 1, 2)
                cv2.polylines(bird, [pts], False, color, 2)

    out_resized  = cv2.resize(out,  (w // 2, h // 2))
    bird_resized = cv2.resize(bird, (w // 2, h // 2))
    return np.hstack([out_resized, bird_resized])


while True:
    if not paused:
        ret, frame = cap.read()
        if not ret:
            paused = True
            cv2.waitKey(30)
            continue

        result = det.process_frame(frame)
        last_display = make_display(frame, result, det, debug)
        frame_idx += 1
        cv2.imshow("preview", last_display)
        delay = max(1, int(1000 / fps))
    else:
        delay = 30

    key = cv2.waitKey(delay) & 0xFF
    if key in (ord('q'), 27):
        break
    elif key == ord(' '):
        paused = not paused
    elif key == ord('r'):
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        det.reset()
        frame_idx = 0
        paused = False
    elif key == ord('b'):
        debug = not debug
        if last_display is not None and paused:
            # 暂停时切换立即刷新
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_idx - 1))
            ret, frame = cap.read()
            if ret:
                result = det.process_frame(frame)
                last_display = make_display(frame, result, det, debug)
                cv2.imshow("preview", last_display)
    elif key == 0x27 and paused:   # → 右箭头逐帧
        ret, frame = cap.read()
        if ret:
            result = det.process_frame(frame)
            last_display = make_display(frame, result, det, debug)
            cv2.imshow("preview", last_display)
            frame_idx += 1

cap.release()
cv2.destroyAllWindows()
