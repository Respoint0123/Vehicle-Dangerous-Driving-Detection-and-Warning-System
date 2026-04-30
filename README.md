![Stone Badge](https://stone.professorlee.work/api/stone/Respoint0123/Vehicle-Dangerous-Driving-Detection-and-Warning-System)
# Vehicle Dangerous Driving Detection and Warning System

基于单目摄像头的车辆危险驾驶检测与预警系统。通过透视变换 + 滑动窗口检测车道线，结合 LK 光流法估算车速，利用消失点偏移判断急转弯，实时输出预警信息。

## 功能

- 车道线检测：HLS 颜色阈值 + Sobel 边缘 → 鸟瞰图 → 直方图滑窗 → 二次多项式拟合
- 车速估算：Lucas-Kanade 光流法，计算 ROI 区域像素位移换算为 km/h
- 急转弯检测：消失点横向偏移量映射为方向盘转角
- 实时预警：超速 / 急转弯触发告警，写入 SQLite，前端弹出提示
- 调试视图：可切换显示融合二值鸟瞰图 + 彩色透视鸟瞰图
- 透视标定：内置 `warp_calibrator.py` 工具，点选四点生成 `WARP_SRC` 参数

## 目录结构

```
├── code/
│   ├── app.py               # Flask 主应用，MJPEG 推流 + REST API
│   ├── detector.py          # 检测核心：车道线 / 测速 / 转角
│   ├── preview.py           # 独立预览窗口（不启动 Web）
│   ├── warp_calibrator.py   # 透视变换标定工具
│   ├── requirements.txt
│   ├── static/
│   │   ├── app.js           # 前端逻辑
│   │   └── style.css
│   └── templates/
│       └── index.html
```

## 快速开始

**安装依赖**

```bash
pip install -r code/requirements.txt
```

**启动 Web 应用**

```bash
cd code
python app.py
```

浏览器打开 `http://localhost:5000`，默认使用摄像头 0。

**使用视频文件**

启动后在页面「视频源」面板输入视频文件的绝对路径，点击切换。

**透视标定**

```bash
cd code
python warp_calibrator.py --video /path/to/video.mp4
```

在弹出窗口按顺序点击：左下 → 左上 → 右上 → 右下，按 `s` 保存，将输出的 `WARP_SRC` 坐标更新到 `detector.py`。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/video_feed` | MJPEG 视频流 |
| GET | `/api/status` | 当前速度 / 转角 / 车道线状态 |
| GET | `/api/alerts` | 历史告警记录 |
| POST | `/api/config` | 更新预警阈值（speed_limit / angle_limit / cooldown） |
| POST | `/api/source` | 切换视频源（摄像头 ID 或文件路径） |
| GET | `/api/debug_frame` | 调试帧（二值鸟瞰 + 彩色鸟瞰并排） |
| GET | `/api/calibration/warp_src` | 获取当前 WARP_SRC |
| POST | `/api/calibration/save` | 保存标定参数到 detector.py |

## 依赖

- Python 3.9+
- Flask >= 2.3
- OpenCV >= 4.8
- NumPy >= 1.24
