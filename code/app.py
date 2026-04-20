import base64
import os
import re
import sqlite3
import threading
import time
from datetime import datetime

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request

from detector import Detector

app = Flask(__name__)

# 全局变量
cap = cv2.VideoCapture(0)
fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
latest_frame = None

detector = Detector()

speed_limit = 80.0
angle_limit = 15.0
cooldown = 3.0
_last_speed_t = 0.0
_last_turn_t = 0.0

status = {"speed_kmh": 0.0, "turn_angle": 0.0, "lanes": False, "alert": []}

frame_lock = threading.Lock()
latest_jpeg = None
latest_raw_frame = None  # 未标注的原始帧，用于标定预览

# 数据库
DB_PATH = os.path.join(os.path.dirname(__file__), "alerts.db")
db_lock = threading.Lock()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT    NOT NULL,
            alert_type TEXT    NOT NULL,
            speed_kmh  REAL,
            angle_deg  REAL,
            threshold  REAL
        )
    """)
    conn.commit()
    conn.close()


def insert_alert(alert_type, speed_kmh, angle_deg, threshold):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO alerts (timestamp, alert_type, speed_kmh, angle_deg, threshold) VALUES (?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), alert_type, speed_kmh, angle_deg, threshold),
        )
        conn.commit()
        conn.close()


def get_alerts(limit=50):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, timestamp, alert_type, speed_kmh, angle_deg, threshold FROM alerts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def check_alert(speed_kmh, turn_angle):
    global _last_speed_t, _last_turn_t
    now = time.time()
    triggered = []

    if speed_kmh > speed_limit:
        if now - _last_speed_t > cooldown:
            insert_alert("speed", speed_kmh, turn_angle, speed_limit)
            _last_speed_t = now
            triggered.append("speed")

    if abs(turn_angle) > angle_limit:
        if now - _last_turn_t > cooldown:
            insert_alert("turn", speed_kmh, turn_angle, angle_limit)
            _last_turn_t = now
            triggered.append("turn")

    return triggered


# 读帧线程
def read_frames():
    global latest_frame
    while True:
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue
        latest_frame = frame


# 处理循环
def process_loop():
    global latest_jpeg, latest_raw_frame
    while True:
        frame = latest_frame
        if frame is None:
            time.sleep(0.01)
            continue
        latest_raw_frame = frame.copy()
        result = detector.process_frame(frame)
        alerts = check_alert(result["speed"], result["angle"])
        status["speed_kmh"] = result["speed"]
        status["turn_angle"] = result["angle"]
        status["lanes"] = result["lanes"]
        status["alert"] = alerts
        _, jpeg = cv2.imencode(".jpg", result["frame"], [cv2.IMWRITE_JPEG_QUALITY, 80])
        with frame_lock:
            latest_jpeg = jpeg.tobytes()


# MJPEG 生成器
def gen_frames():
    while True:
        with frame_lock:
            frame = latest_jpeg
        if frame is None:
            time.sleep(0.03)
            continue
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )
        time.sleep(0.03)


# 路由
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/video_feed")
def video_feed():
    return Response(
        gen_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/api/status")
def api_status():
    return jsonify(status)


@app.route("/api/alerts")
def api_alerts():
    limit = request.args.get("limit", 50, type=int)
    return jsonify(get_alerts(limit))


@app.route("/api/config", methods=["POST"])
def api_config():
    global speed_limit, angle_limit, cooldown
    data = request.get_json(force=True)
    try:
        if "speed_limit" in data:
            speed_limit = float(data["speed_limit"])
        if "angle_limit" in data:
            angle_limit = float(data["angle_limit"])
        if "cooldown" in data:
            cooldown = float(data["cooldown"])
    except (TypeError, ValueError):
        return jsonify({"error": "invalid value"}), 400
    return jsonify({
        "speed_limit": speed_limit,
        "angle_limit": angle_limit,
        "cooldown": cooldown,
    })


@app.route("/api/source", methods=["POST"])
def api_source():
    global cap, fps
    data = request.get_json(force=True)
    source = data.get("source", 0)
    try:
        source = int(source)
    except (ValueError, TypeError):
        pass
    cap.release()
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"无法打开视频源: {source}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    detector.fps = fps
    detector.reset()
    return jsonify({"source": str(source)})


# 标定辅助函数
def _build_warp_src_str(points):
    labels = ["左下", "左上", "右上", "右下"]
    lines = ["    WARP_SRC = [\n"]
    for i, (x, y) in enumerate(points):
        lines.append(f"        ({round(x,3)}, {round(y,3)}),   # {labels[i]}\n")
    lines.append("    ]")
    return "".join(lines)


def _save_warp_src_to_file(points):
    path = os.path.join(os.path.dirname(__file__), "detector.py")
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()
    new_code = re.sub(
        r"    WARP_SRC = \[.*?\]",
        _build_warp_src_str(points),
        code, flags=re.DOTALL,
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_code)


# 标定路由
@app.route("/api/calibration/warp_src")
def api_calib_warp_src():
    return jsonify({"warp_src": detector.WARP_SRC})


@app.route("/api/calibration/snapshot")
def api_calib_snapshot():
    frame = latest_raw_frame
    if frame is None:
        return Response("no frame", status=503)
    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Response(jpeg.tobytes(), mimetype="image/jpeg")


@app.route("/api/calibration/preview", methods=["POST"])
def api_calib_preview():
    frame = latest_raw_frame
    if frame is None:
        return jsonify({"error": "no frame"}), 503
    data = request.get_json(force=True)
    points = data.get("warp_src", [])
    if len(points) != 4:
        return jsonify({"error": "need 4 points"}), 400
    h, w = frame.shape[:2]
    src = np.float32([[x * w, y * h] for x, y in points])
    dst_w, dst_h = 320, 320
    dst = np.float32([
        [dst_w * 0.20, dst_h * 1.00],
        [dst_w * 0.20, dst_h * 0.00],
        [dst_w * 0.80, dst_h * 0.00],
        [dst_w * 0.80, dst_h * 1.00],
    ])
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(frame, M, (dst_w, dst_h))
    cx = dst_w // 2
    cv2.line(warped, (cx, 0), (cx, dst_h), (0, 255, 255), 1)
    _, jpeg = cv2.imencode(".jpg", warped, [cv2.IMWRITE_JPEG_QUALITY, 80])
    b64 = base64.b64encode(jpeg.tobytes()).decode()
    return jsonify({"image": f"data:image/jpeg;base64,{b64}"})


@app.route("/api/calibration/save", methods=["POST"])
def api_calib_save():
    data = request.get_json(force=True)
    points = data.get("warp_src", [])
    if len(points) != 4:
        return jsonify({"error": "need 4 points"}), 400
    for x, y in points:
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return jsonify({"error": "coordinates out of [0,1]"}), 400
    detector.WARP_SRC = points
    detector._warp_size = (0, 0)
    detector._left_poly = None
    detector._right_poly = None
    _save_warp_src_to_file(points)
    return jsonify({"ok": True})


@app.route("/api/debug_frame")
def api_debug_frame():
    """返回调试图：左=融合二值鸟瞰图，右=透视变换彩色鸟瞰图，拼成一张横图。"""
    frame = latest_raw_frame
    if frame is None:
        return Response("no frame", status=503)

    h, w = frame.shape[:2]
    src = np.float32([[x * w, y * h] for x, y in detector.WARP_SRC])
    dst = np.float32([[x * w, y * h] for x, y in detector.WARP_DST])
    M = cv2.getPerspectiveTransform(src, dst)

    # 彩色鸟瞰图
    warped_color = cv2.warpPerspective(frame, M, (w, h))
    cx = w // 2
    cv2.line(warped_color, (cx, 0), (cx, h), (0, 220, 220), 1)

    # 融合二值鸟瞰图（复用 detector 内部逻辑）
    hls = cv2.cvtColor(frame, cv2.COLOR_BGR2HLS)
    s_bin = ((hls[:, :, 2] >= 120) & (hls[:, :, 2] <= 255)).astype(np.uint8)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    sx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    abs_sx = np.absolute(sx)
    scaled = np.uint8(255 * abs_sx / (np.max(abs_sx) + 1e-6))
    sx_bin = ((scaled >= 20) & (scaled <= 100)).astype(np.uint8)
    combined = np.zeros_like(s_bin)
    combined[(s_bin == 1) | (sx_bin == 1)] = 255
    warped_bin = cv2.warpPerspective(combined, M, (w, h))
    warped_bin_bgr = cv2.cvtColor(warped_bin, cv2.COLOR_GRAY2BGR)

    # 缩小到一半高度再拼接，避免图太大
    th = h // 2
    tw = w // 2
    left_img  = cv2.resize(warped_bin_bgr, (tw, th))
    right_img = cv2.resize(warped_color,   (tw, th))

    # 加标签
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(left_img,  "Binary Warped", (8, 22), font, 0.55, (80, 220, 80), 1)
    cv2.putText(right_img, "Color Warped",  (8, 22), font, 0.55, (80, 220, 220), 1)

    combined_img = np.hstack([left_img, right_img])
    _, jpeg = cv2.imencode(".jpg", combined_img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return Response(jpeg.tobytes(), mimetype="image/jpeg")


if __name__ == "__main__":
    init_db()
    detector.fps = fps
    threading.Thread(target=read_frames, daemon=True).start()
    threading.Thread(target=process_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8765, debug=False)
