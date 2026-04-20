# detector.py
# 车道线检测（透视变换 + 直方图滑窗）+ LK 光流测速 + 消失点偏移急转弯检测

import cv2
import numpy as np


class Detector:

    ROI_TOP_Y = 0.58  # 光流 ROI 顶边（比例），与 WARP_SRC 顶边 y 对齐

    # 透视变换源点（前视图梯形，比例坐标，顺序：左下、左上、右上、右下）
    WARP_SRC = [
        (0, 0.953),   # 左下
        (0.414, 0.622),   # 左上
        (0.514, 0.623),   # 右上
        (1, 0.963),   # 右下
    ]
    # 透视变换目标点（鸟瞰图矩形，比例坐标）
    WARP_DST = [
        (0.20, 1.00),   # 左下
        (0.20, 0.00),   # 左上
        (0.80, 0.00),   # 右上
        (0.80, 1.00),   # 右下
    ]

    def __init__(self, pixels_per_meter=500.0, fps=30.0):
        self.pixels_per_meter = pixels_per_meter
        self.fps = fps

        self._prev_gray = None
        self._prev_pts = None

        self._speed_buf = []
        self._angle_buf = []

        # 透视变换矩阵（首帧时按实际分辨率初始化）
        self._M = None
        self._Minv = None
        self._warp_size = (0, 0)

        # 定期重置计数器：每隔 fps 帧（约1秒）强制从中间重搜
        self._frame_count = 0
        # 上一帧拟合结果
        self._left_poly = None
        self._right_poly = None

        # 车道线缓冲：检测失败时保持上一个有效结果
        self._last_valid_left = None
        self._last_valid_right = None

        # LK 光流参数
        self._lk_params = dict(
            winSize=(21, 21),
            maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.01),
        )
        self._shi_params = dict(
            maxCorners=80,
            qualityLevel=0.01,
            minDistance=10,
            blockSize=7,
        )

    # 主入口
    def process_frame(self, frame):
        h, w = frame.shape[:2]
        annotated = frame.copy()

        # 1. 车道线检测
        left_line, right_line = self._detect_lanes(frame)
        lanes_detected = (left_line is not None) or (right_line is not None)

        # 2. 消失点 → 转向角（带平滑）
        raw_angle = 0.0
        if left_line is not None and right_line is not None:
            lx_t = left_line[2]
            rx_t = right_line[2]
            vp_x = (lx_t + rx_t) / 2.0
            offset = (vp_x - w / 2) / (w / 2)
            raw_angle = float(np.clip(offset * 45.0, -90.0, 90.0))

        self._angle_buf.append(raw_angle)
        if len(self._angle_buf) > 8:
            self._angle_buf.pop(0)
        turn_angle = float(np.mean(self._angle_buf))

        # 3. LK 光流 → 车速
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        speed_kmh = self._estimate_speed(gray, h)

        # 4. 绘制标注
        if lanes_detected:
            self._draw_lanes(annotated, left_line, right_line, h, w)
        self._draw_hud(annotated, speed_kmh, turn_angle, lanes_detected)

        return {"frame": annotated, "speed": speed_kmh, "angle": turn_angle, "lanes": lanes_detected}

    def reset(self):
        self._prev_gray = None
        self._prev_pts = None
        self._speed_buf.clear()
        self._left_poly = None
        self._right_poly = None
        self._last_valid_left = None
        self._last_valid_right = None
        self._angle_buf.clear()

    # 车道线检测（透视变换 + 直方图滑窗）
    def _binary_warped(self, frame):
        h, w = frame.shape[:2]
        # 按当前分辨率初始化透视变换矩阵
        if self._warp_size != (h, w):
            src = np.float32([[x * w, y * h] for x, y in self.WARP_SRC])
            dst = np.float32([[x * w, y * h] for x, y in self.WARP_DST])
            self._M = cv2.getPerspectiveTransform(src, dst)
            self._Minv = cv2.getPerspectiveTransform(dst, src)
            self._warp_size = (h, w)

        # HLS S 通道
        hls = cv2.cvtColor(frame, cv2.COLOR_BGR2HLS)
        s = hls[:, :, 2]
        s_bin = ((s >= 120) & (s <= 255)).astype(np.uint8)

        # Sobel x
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        abs_sx = np.absolute(sobelx)
        scaled = np.uint8(255 * abs_sx / (np.max(abs_sx) + 1e-6))
        sx_bin = ((scaled >= 20) & (scaled <= 100)).astype(np.uint8)

        combined = np.zeros_like(s_bin)
        combined[(s_bin == 1) | (sx_bin == 1)] = 1

        warped = cv2.warpPerspective(combined, self._M, (w, h), flags=cv2.INTER_LINEAR)
        return warped

    def _sliding_window(self, binary, h, w, hint_left_x=-1, hint_right_x=-1):
        mid = w // 2

        # 有上帧 hint 时直接用，否则从直方图找；左右独立判断
        if hint_left_x >= 0 and hint_right_x >= 0:
            left_x, right_x = hint_left_x, hint_right_x
        else:
            histogram = np.sum(binary[h // 2:, :], axis=0)
            histogram = np.convolve(histogram, np.ones(40) / 40, mode='same')
            threshold = histogram.max() * 0.1

            if hint_left_x >= 0:
                left_x = hint_left_x
            else:
                left_half = histogram[:mid].copy()
                left_half[left_half < threshold] = 0
                left_x = mid - 1
                for x in range(mid - 1, -1, -1):
                    if left_half[x] > 0:
                        left_x = x
                        break

            if hint_right_x >= 0:
                right_x = hint_right_x
            else:
                right_half = histogram[mid:].copy()
                right_half[right_half < threshold] = 0
                right_x = mid
                for x in range(0, mid):
                    if right_half[x] > 0:
                        right_x = mid + x
                        break

        n_windows = 9
        win_h = h // n_windows
        margin = 80
        min_pixels = 15

        left_inds, right_inds = [], []
        nonzero = binary.nonzero()
        nz_y, nz_x = np.array(nonzero[0]), np.array(nonzero[1])

        cur_left_x, cur_right_x = left_x, right_x
        for i in range(n_windows):
            y_lo = h - (i + 1) * win_h
            y_hi = h - i * win_h
            for cur_x, inds in [(cur_left_x, left_inds), (cur_right_x, right_inds)]:
                good = ((nz_y >= y_lo) & (nz_y < y_hi) &
                        (nz_x >= cur_x - margin) & (nz_x < cur_x + margin)).nonzero()[0]
                inds.append(good)
                if len(good) >= min_pixels:
                    if inds is left_inds:
                        cur_left_x = int(np.mean(nz_x[good]))
                    else:
                        cur_right_x = int(np.mean(nz_x[good]))

        left_inds = np.concatenate(left_inds)
        right_inds = np.concatenate(right_inds)

        left_poly = right_poly = None
        if len(left_inds) >= 10:
            left_poly = np.polyfit(nz_y[left_inds], nz_x[left_inds], 2)
        if len(right_inds) >= 10:
            right_poly = np.polyfit(nz_y[right_inds], nz_x[right_inds], 2)
        return left_poly, right_poly

    def _detect_lanes(self, frame):
        """返回 (left_line, right_line)，格式：(x_b, y_b, x_t, y_t, poly) 或 None。"""
        h, w = frame.shape[:2]
        binary = self._binary_warped(frame)

        # 每帧都从直方图中间重搜，避免累积漂移
        lp, rp = self._sliding_window(binary, h, w, -1, -1)

        # 曲率过大则丢弃
        MAX_CURVATURE = 0.003
        if lp is not None and abs(lp[0]) > MAX_CURVATURE:
            lp = None
        if rp is not None and abs(rp[0]) > MAX_CURVATURE:
            rp = None

        # 两条线重合检测：底部 x 间距过小则同时丢弃
        MIN_LANE_WIDTH = w * 0.15  # 车道宽度至少占图宽 15%
        if lp is not None and rp is not None:
            left_bottom  = np.polyval(lp, h - 1)
            right_bottom = np.polyval(rp, h - 1)
            if right_bottom - left_bottom < MIN_LANE_WIDTH:
                lp = None
                rp = None

        if lp is not None:
            self._left_poly = lp
        if rp is not None:
            self._right_poly = rp

        # 将多项式转为端点 tuple
        def poly_to_line(poly):
            y_top = int(h * 0.1)
            y_bottom = h - 1
            x_bottom = int(np.polyval(poly, y_bottom))
            x_top = int(np.polyval(poly, y_top))
            return (x_bottom, y_bottom, x_top, y_top, poly)

        left_line = poly_to_line(self._left_poly) if self._left_poly is not None else None
        right_line = poly_to_line(self._right_poly) if self._right_poly is not None else None
        return left_line, right_line

    # 速度估算（LK 光流）
    def _estimate_speed(self, gray, h):
        roi_top = int(h * self.ROI_TOP_Y)

        if self._prev_gray is None:
            self._prev_gray = gray
            self._refresh_points(gray, roi_top, h)
            self._speed_buf.append(0.0)
            if len(self._speed_buf) > 5:
                self._speed_buf.pop(0)
            return float(np.mean(self._speed_buf))

        if self._prev_pts is None or len(self._prev_pts) < 5:
            self._refresh_points(gray, roi_top, h)
            self._prev_gray = gray
            self._speed_buf.append(0.0)
            if len(self._speed_buf) > 5:
                self._speed_buf.pop(0)
            return float(np.mean(self._speed_buf))

        next_pts, st, _ = cv2.calcOpticalFlowPyrLK(
            self._prev_gray, gray, self._prev_pts, None, **self._lk_params
        )

        speed_kmh = 0.0
        if next_pts is not None and st is not None:
            ok = st.ravel() == 1
            good_prev = self._prev_pts[ok]
            good_next = next_pts[ok]

            if len(good_prev) >= 3:
                gp = good_prev.reshape(-1, 2)
                gn = good_next.reshape(-1, 2)
                dy = gn[:, 1] - gp[:, 1]
                dy_fwd = dy[dy > 0]
                if len(dy_fwd) > 0:
                    avg_dy = float(np.median(dy_fwd))
                    speed_ms = avg_dy * self.fps / self.pixels_per_meter
                    speed_kmh = min(speed_ms * 3.6, 250.0)

            if len(good_next) >= 10:
                self._prev_pts = good_next.reshape(-1, 1, 2)
            else:
                self._refresh_points(gray, roi_top, h)

        self._prev_gray = gray
        self._speed_buf.append(speed_kmh)
        if len(self._speed_buf) > 5:
            self._speed_buf.pop(0)
        return float(np.mean(self._speed_buf))

    def _refresh_points(self, gray, roi_top, h):
        roi_gray = gray[roi_top:h, :]
        pts = cv2.goodFeaturesToTrack(roi_gray, **self._shi_params)
        if pts is not None:
            pts[:, 0, 1] += roi_top
            self._prev_pts = pts
        else:
            self._prev_pts = None

    # 绘制
    def _draw_lanes(self, frame, left_line, right_line, h, w):
        if self._Minv is None:
            return
        overlay = np.zeros((h, w, 3), dtype=np.uint8)

        if left_line is not None and right_line is not None:
            lp, rp = left_line[4], right_line[4]
            ys = np.linspace(0, h - 1, 30).astype(int)
            lxs = np.clip(np.polyval(lp, ys).astype(int), 0, w - 1)
            rxs = np.clip(np.polyval(rp, ys).astype(int), 0, w - 1)
            pts_l = np.stack([lxs, ys], axis=1)
            pts_r = np.stack([rxs, ys], axis=1)[::-1]
            pts = np.vstack([pts_l, pts_r]).reshape(-1, 1, 2).astype(np.float32)
            pts_orig = cv2.perspectiveTransform(pts, self._Minv).astype(np.int32)
            cv2.fillPoly(overlay, [pts_orig], (0, 200, 0))

        for line, color in [
            (left_line,  (255, 120,   0)),
            (right_line, (  0, 120, 255)),
        ]:
            if line is not None:
                x_b, y_b, x_t, y_t, _ = line
                pts_w = np.array([[[x_b, y_b]], [[x_t, y_t]]], dtype=np.float32)
                pts_o = cv2.perspectiveTransform(pts_w, self._Minv).astype(int)
                cv2.line(overlay, tuple(pts_o[0][0]), tuple(pts_o[1][0]), color, 4)

        cv2.addWeighted(overlay, 0.4, frame, 1.0, 0, frame)

    def _draw_hud(self, frame, speed_kmh, turn_angle, lanes_detected):
        def put(text, pos, color=(255, 255, 255), scale=0.9):
            cv2.putText(frame, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)

        put(f"Speed : {speed_kmh:5.1f} km/h", (20, 40))

        direction = "R" if turn_angle > 1 else ("L" if turn_angle < -1 else "-")
        put(f"Angle : {abs(turn_angle):5.1f} deg  {direction}", (20, 75))

        if lanes_detected:
            put("Lanes : OK", (20, 110), color=(0, 255, 0))
        else:
            put("Lanes : --", (20, 110), color=(0, 100, 255))
