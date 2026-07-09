#!/usr/bin/env python3
"""
Rosmaster Web 遥控系统 — 启动入口 (HTTP API 版)

对照 app_sim2.py:
    root = tk.Tk()
    app = RobotControlApp(root)
    root.mainloop()

通信方式: HTTP REST API (无 WebSocket，避免 flask_sock/gevent 兼容问题)
    GET  /api/sensors   → 传感器轮询
    POST /api/motion    → 运动控制
    POST /api/rgb       → RGB 灯带
    ...

运行:
    python main.py [串口] [车型]
    python main.py                          # 使用默认 /dev/myserial
    python main.py /dev/ttyUSB0 1
"""
import sys
import os
import time
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    SERIAL_PORT, CAR_TYPE, SERVER_HOST, SERVER_PORT,
    CAMERA_ID, FRAME_WIDTH, FRAME_HEIGHT, JPEG_QUALITY,
    MOTION_TIMEOUT, SENSOR_INTERVAL, DEFAULT_SPEED, ROTATION_FACTOR, DEBUG,
    VOICE_ALSA_DEVICE,
    XUNFEI_APPID, XUNFEI_API_KEY, XUNFEI_API_SECRET, VOICE_AUTO_EXEC,
)
from driver.serial_driver import SerialDriver      # 对照: self.g_bot = Rosmaster()
from driver.camera_driver import CameraDriver       # 对照: self.cap = cv2.VideoCapture(0)
from service.motion_service import MotionService    # 对照: execute_command_with_duration
from service.light_service import LightService      # 对照: toggle_light
from service.sensor_service import SensorService    # 对照: update_iot_labels
from service.voice_service import VoiceService    # 语音识别
from web.routes import bp, register_video_route, inject_services
from utils.logger import get_logger

logger = get_logger("main")


def _voice_motion(action: str, duration: float, motion_svc):
    """语音指令 → 运动执行 (main.py 桥接函数)

    对照 VoiceService.COMMAND_MAP 中的 action 字段。
    duration > 0 时执行 duration 秒后自动停止。
    """
    import time as _time
    # 速度因子 (取当前 motion_svc.speed 百分比)
    sp = motion_svc.speed / 100.0
    rot = sp * ROTATION_FACTOR

    # speed_up / speed_down 调整档位
    if action == "speed_up":
        motion_svc.speed = min(100, motion_svc.speed + 10)
        logger.info(f"🎤 语音: 加速 → {motion_svc.speed}%")
        return
    if action == "speed_down":
        motion_svc.speed = max(10, motion_svc.speed - 10)
        logger.info(f"🎤 语音: 减速 → {motion_svc.speed}%")
        return

    commands = {
        "forward":      (sp, 0.0, 0.0),
        "backward":     (-sp, 0.0, 0.0),
        "turn_left":    (0.0, 0.0, rot),
        "turn_right":   (0.0, 0.0, -rot),
        "strafe_left":  (0.0, sp, 0.0),
        "strafe_right": (0.0, -sp, 0.0),
        "stop":         (0.0, 0.0, 0.0),
    }

    vx, vy, vz = commands.get(action, (0.0, 0.0, 0.0))
    motion_svc.execute(vx, vy, vz)
    logger.info(f"🎤 语音指令: {action} → vx={vx:.2f} vy={vy:.2f} vz={vz:.2f} (持续 {duration}s)")

    if duration > 0:
        def _auto_stop():
            _time.sleep(duration)
            motion_svc.execute(0.0, 0.0, 0.0)
        import threading
        threading.Thread(target=_auto_stop, daemon=True).start()


def create_app():
    """创建 Flask 应用"""
    app = Flask(__name__, static_folder="static", static_url_path="")
    app.register_blueprint(bp)
    return app


def main():
    port = sys.argv[1] if len(sys.argv) >= 2 else SERIAL_PORT
    car_type = int(sys.argv[2]) if len(sys.argv) >= 3 else CAR_TYPE

    logger.info("=" * 55)
    logger.info("Rosmaster Web 遥控系统 启动中...")
    logger.info(f"  串口: {port}  车型: {car_type}")
    logger.info(f"  服务地址: http://0.0.0.0:{SERVER_PORT}")
    logger.info("=" * 55)

    # ── 1. 硬件驱动 ──
    logger.info("[1/4] 初始化串口驱动...")
    serial_drv = SerialDriver(port, car_type, debug=DEBUG)

    logger.info("[2/4] 初始化摄像头...")
    camera_drv = CameraDriver(CAMERA_ID, FRAME_WIDTH, FRAME_HEIGHT, debug=DEBUG)
    if not camera_drv.is_opened():
        logger.warning("摄像头未打开，视频流将不可用")

    # ── 2. 业务服务 ──
    logger.info("[3/4] 初始化服务...")
    motion_svc = MotionService(serial_drv, timeout=MOTION_TIMEOUT,
                               speed=DEFAULT_SPEED)
    light_svc = LightService(serial_drv)
    sensor_svc = SensorService(serial_drv, interval=SENSOR_INTERVAL)

    logger.info("    初始化语音识别服务...")
    voice_svc = VoiceService(
        alsa_device=VOICE_ALSA_DEVICE,
        xunfei_appid=XUNFEI_APPID,
        xunfei_api_key=XUNFEI_API_KEY,
        xunfei_api_secret=XUNFEI_API_SECRET,
        auto_exec=VOICE_AUTO_EXEC,
        motion_callback=lambda action, dur: _voice_motion(action, dur, motion_svc),
        debug=DEBUG,
    )

    # ── 3. Flask 应用 ──
    app = create_app()

    # 注入服务到路由 (供 REST API 使用)
    inject_services(motion_svc, light_svc, sensor_svc, serial_drv, voice_svc)

    # 注册 /video_feed MJPEG 路由
    register_video_route(app, camera_drv, JPEG_QUALITY)

    # ── 4. 启动 ──
    logger.info("=" * 55)
    logger.info("系统就绪! API 端点:")
    logger.info("  GET  /api/sensors    传感器数据")
    logger.info("  POST /api/motion     运动控制 {vx,vy,vz}")
    logger.info("  POST /api/direction  方向控制 {direction}")
    logger.info("  POST /api/rgb        RGB灯带 {r,g,b}")
    logger.info("  POST /api/rgb_effect 灯带特效 {effect,speed}")
    logger.info("  POST /api/light      照明灯 {enable}")
    logger.info("  POST /api/beep       蜂鸣器 {enable}")
    logger.info("  POST /api/follow_line 巡线 {enable}")
    logger.info("  POST /api/servo       PWM舵机 {id,angle}")
    logger.info("  POST /api/arm         机械臂 {id,angle,time}")
    logger.info("  POST /api/voice/start  开启语音识别")
    logger.info("  POST /api/voice/stop   关闭语音识别")
    logger.info("  GET  /api/voice         语音识别状态")
    logger.info(f"  浏览器访问: http://<上位机IP>:{SERVER_PORT}")
    logger.info("=" * 55)

    try:
        app.run(
            host=SERVER_HOST,
            port=SERVER_PORT,
            threaded=True,
            debug=False,
        )
    except KeyboardInterrupt:
        logger.info("正在关闭...")
    finally:
        motion_svc.stop()
        camera_drv.release()
        voice_svc.stop()
        logger.info("已停止。")


if __name__ == "__main__":
    main()
