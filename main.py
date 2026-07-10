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
    TTS_APPID, TTS_API_KEY, TTS_API_SECRET,
    TTS_VOICE, TTS_SPEED, TTS_VOLUME, TTS_PLAY_DEVICE,
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL,
)
from driver.serial_driver import SerialDriver      # 对照: self.g_bot = Rosmaster()
from driver.camera_driver import CameraDriver       # 对照: self.cap = cv2.VideoCapture(0)
from driver.camera_driver import add_frame_processor, remove_frame_processor  # 帧处理器注册
from driver.tts_driver import TTSDriver             # TTS: 文字→语音
from driver.llm_driver import LLMDriver             # LLM: DeepSeek
from service.motion_service import MotionService    # 对照: execute_command_with_duration
from service.light_service import LightService      # 对照: toggle_light
from service.sensor_service import SensorService    # 对照: update_iot_labels
from service.voice_service import VoiceService      # 语音识别
from service.tts_service import TTSService          # TTS 业务
from service.llm_service import LLMService          # LLM 业务
from service.fire_service import FireService        # 火情监测 (对照 toggle_fire_check)
from service.gesture_service import GestureService  # 手势控制 (对照 toggle_hand_ctrl)
from web.routes import bp, register_video_route, inject_services
from utils.logger import get_logger

logger = get_logger("main")


def _voice_motion(action: str, duration: float, motion_svc,
                  fire_svc=None, gesture_svc=None):
    """语音指令 → 运动执行 / 功能开关 (main.py 桥接函数)

    对照 VoiceService.KEYWORD_MAP 中的 action 字段。
    duration > 0 时执行 duration 秒后自动停止。
    每 0.3s 续命看门狗，避免被 MotionService 的 0.5s 超时截断。

    扩展支持 fire_check_on/off, hand_ctrl_on/off (对照 app_sim2.py toggle_xxx)
    """
    import time as _time

    # ── 火情监测开关 (对照 app_sim2.py toggle_fire_check) ──
    if action == "fire_check_on":
        if fire_svc:
            fire_svc.start()
            logger.info("🎤 语音: 开启火情监测")
        return
    if action == "fire_check_off":
        if fire_svc:
            fire_svc.stop()
            logger.info("🎤 语音: 关闭火情监测")
        return

    # ── 手势控制开关 (对照 app_sim2.py toggle_hand_ctrl) ──
    if action == "hand_ctrl_on":
        if gesture_svc:
            gesture_svc.start()
            logger.info("🎤 语音: 开启手势控制")
        return
    if action == "hand_ctrl_off":
        if gesture_svc:
            gesture_svc.stop()
            logger.info("🎤 语音: 关闭手势控制")
        return

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
    logger.info(f"🎤 语音指令: {action} → vx={vx:.2f} vy={vy:.2f} vz={vz:.2f} (持续 {duration}s)")

    if duration > 0:
        def _timed_move():
            """后台线程: 每 0.3s 续命看门狗，到时间后停止"""
            elapsed = 0.0
            tick = 0.3  # 小于看门狗 0.5s 超时
            while elapsed < duration:
                motion_svc.execute(vx, vy, vz)
                sleep_time = min(tick, duration - elapsed)
                _time.sleep(sleep_time)
                elapsed += sleep_time
            motion_svc.execute(0.0, 0.0, 0.0)
        import threading
        threading.Thread(target=_timed_move, daemon=True).start()
    else:
        motion_svc.execute(vx, vy, vz)


def _execute_llm_plan(plan: dict, motion_svc, light_svc, serial_drv, tts_svc,
                       fire_svc=None, gesture_svc=None):
    """执行 LLM 返回的计划 (main.py 桥接)

    对照 TonyPi step_executor.execute():
        遍历 plan["steps"], 逐个执行 action

    扩展支持 fire_check_on/off, hand_ctrl_on/off (对照 app_sim2.py toggle_xxx)
    """
    import time as _time
    steps = plan.get("steps", [])

    # 先播 TTS
    tts_text = plan.get("tts_response", "").strip()
    if tts_text:
        logger.info(f"🔊 TTS: '{tts_text[:80]}'")
        tts_svc.speak(tts_text, block=False)

    sp = motion_svc.speed / 100.0
    rot = sp * ROTATION_FACTOR

    for i, step in enumerate(steps):
        action = step.get("action", "")
        params = step.get("params", {})
        desc = step.get("description", action)
        logger.info(f"  Step {i+1}/{len(steps)}: {desc}")

        try:
            if action in ("forward", "backward", "turn_left", "turn_right",
                          "strafe_left", "strafe_right", "stop"):
                # 默认时长: 统一 5.0s
                if action == "stop":
                    default_dur = 0
                else:
                    default_dur = 5.0
                duration = float(params.get("duration", default_dur))
                cmd = {
                    "forward":      (sp, 0.0, 0.0),
                    "backward":     (-sp, 0.0, 0.0),
                    "turn_left":    (0.0, 0.0, rot),
                    "turn_right":   (0.0, 0.0, -rot),
                    "strafe_left":  (0.0, sp, 0.0),
                    "strafe_right": (0.0, -sp, 0.0),
                    "stop":         (0.0, 0.0, 0.0),
                }
                vx, vy, vz = cmd.get(action, (0.0, 0.0, 0.0))
                if duration > 0:
                    # 每 0.3s 续命看门狗, 避免被 0.5s 超时截断
                    elapsed = 0.0
                    tick = 0.3
                    while elapsed < duration:
                        motion_svc.execute(vx, vy, vz)
                        sleep_time = min(tick, duration - elapsed)
                        _time.sleep(sleep_time)
                        elapsed += sleep_time
                motion_svc.execute(0.0, 0.0, 0.0)
                _time.sleep(0.2)

            elif action == "speed_up":
                motion_svc.speed = min(100, motion_svc.speed + 10)
                sp = motion_svc.speed / 100.0
                rot = sp * ROTATION_FACTOR

            elif action == "speed_down":
                motion_svc.speed = max(10, motion_svc.speed - 10)
                sp = motion_svc.speed / 100.0
                rot = sp * ROTATION_FACTOR

            elif action in ("led_red", "led_green", "led_blue", "led_white", "led_off"):
                rgb_map = {
                    "led_red":   (255, 0, 0),
                    "led_green": (0, 255, 0),
                    "led_blue":  (0, 0, 255),
                    "led_white": (255, 255, 255),
                    "led_off":   (0, 0, 0),
                }
                r, g, b = rgb_map.get(action, (0, 0, 0))
                light_svc.set_rgb(r, g, b)

            elif action == "led_breath":
                serial_drv.set_colorful_effect(3, 5)

            elif action in ("beep_alarm", "beep_sos"):
                serial_drv.set_buzzer(True)
                _time.sleep(0.3)
                serial_drv.set_buzzer(False)

            elif action == "speak":
                text = params.get("text", "")
                if text:
                    tts_svc.speak(text, block=True)

            elif action == "wait":
                secs = float(params.get("seconds", 1.0))
                _time.sleep(secs)

            # ── 火情监测 / 手势控制开关 (对照 app_sim2.py toggle_xxx) ──
            elif action == "fire_check_on":
                if fire_svc:
                    fire_svc.start()
                    logger.info("  LLM: 开启火情监测")
            elif action == "fire_check_off":
                if fire_svc:
                    fire_svc.stop()
                    logger.info("  LLM: 关闭火情监测")
            elif action == "hand_ctrl_on":
                if gesture_svc:
                    gesture_svc.start()
                    logger.info("  LLM: 开启手势控制")
            elif action == "hand_ctrl_off":
                if gesture_svc:
                    gesture_svc.stop()
                    logger.info("  LLM: 关闭手势控制")

            else:
                logger.warning(f"  未知动作: {action}")

        except Exception as e:
            logger.error(f"  执行失败: {e}")


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

    # 火情监测服务 (对照 app_sim2.py toggle_fire_check)
    logger.info("    初始化火情监测服务...")
    fire_svc = FireService(
        sound_callback=serial_drv.play_sound,  # 对照 app_sim2.py playSound
        debug=DEBUG,
    )

    # 手势控制服务 (对照 app_sim2.py toggle_hand_ctrl)
    logger.info("    初始化手势控制服务...")
    # 手势运动回调: 手势→运动, 速度使用手势专用的低速档 (对照 app_sim2.py speed/300)
    def _gesture_motion(action: str):
        """手势指令 → 运动执行 (对照 app_sim2.py hand_ctrls)

        手势速度调节:
            平移: speed/150 (按钮为 speed/100, 手势约 2/3 按钮速度)
            旋转: speed*3.2/200 (按钮为 speed*3.2/100, 手势约 1/2 按钮速度)
        数字越小速度越快, 可根据需要调整
        """
        sp = motion_svc.speed / 150.0       # 手势平移速度 (可调: 数字越小越快)
        rot = motion_svc.speed * 3.2 / 200.0  # 手势旋转速度 (可调: 数字越小越快)
        cmd = {
            "forward":      (sp, 0.0, 0.0),
            "backward":     (-sp, 0.0, 0.0),
            "turn_left":    (0.0, 0.0, rot),
            "turn_right":   (0.0, 0.0, -rot),
            "strafe_left":  (0.0, sp, 0.0),
            "strafe_right": (0.0, -sp, 0.0),
            "stop":         (0.0, 0.0, 0.0),
        }
        vx, vy, vz = cmd.get(action, (0.0, 0.0, 0.0))
        logger.info(f"✋ 手势: {action} → vx={vx:.3f} vy={vy:.3f} vz={vz:.3f}")
        motion_svc.execute(vx, vy, vz)
        # "停止"以外的动作持续 1.0s 后自动停车 (手势不保持, 每次触发只走一段)
        if action != "stop":
            import time as _time
            def _auto_stop():
                _time.sleep(1.0)
                motion_svc.execute(0.0, 0.0, 0.0)
            import threading
            threading.Thread(target=_auto_stop, daemon=True).start()

    gesture_svc = GestureService(
        motion_callback=_gesture_motion,
        sound_callback=serial_drv.play_sound,  # 对照 app_sim2.py playSound
        debug=DEBUG,
    )

    # 注册帧处理器到视频流 (对照 app_sim2.py update_camera_frame 中的检测逻辑)
    # 顺序: 火情先处理 (画火焰框), 手势后处理 (画手部关键点)
    add_frame_processor(fire_svc.process_frame)
    add_frame_processor(gesture_svc.process_frame)
    logger.info("    帧处理器已注册: 火情监测 + 手势控制")

    logger.info("    初始化语音识别服务...")
    voice_svc = VoiceService(
        alsa_device=VOICE_ALSA_DEVICE,
        xunfei_appid=XUNFEI_APPID,
        xunfei_api_key=XUNFEI_API_KEY,
        xunfei_api_secret=XUNFEI_API_SECRET,
        auto_exec=VOICE_AUTO_EXEC,
        motion_callback=lambda action, dur: _voice_motion(
            action, dur, motion_svc, fire_svc, gesture_svc),
        debug=DEBUG,
    )

    # TTS (文字转语音)
    logger.info("    初始化 TTS 驱动...")
    tts_drv = TTSDriver(
        appid=TTS_APPID,
        api_key=TTS_API_KEY,
        api_secret=TTS_API_SECRET,
        voice=TTS_VOICE,
        speed=TTS_SPEED,
        volume=TTS_VOLUME,
        play_device=TTS_PLAY_DEVICE,
        debug=DEBUG,
    )
    tts_svc = TTSService(tts_drv, debug=DEBUG)

    # LLM (大模型)
    logger.info("    初始化 LLM 驱动...")
    llm_drv = LLMDriver(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        model=LLM_MODEL,
        debug=DEBUG,
    )
    llm_svc = LLMService(llm_drv, debug=DEBUG)

    # ── 3. Flask 应用 ──
    app = create_app()

    # 注入服务到路由 (供 REST API 使用)
    inject_services(motion_svc, light_svc, sensor_svc, serial_drv,
                    voice_svc, tts_svc, llm_svc,
                    plan_executor=lambda plan: _execute_llm_plan(
                        plan, motion_svc, light_svc, serial_drv, tts_svc,
                        fire_svc, gesture_svc),
                    fire_svc=fire_svc, gesture_svc=gesture_svc)

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
    logger.info("  POST /api/fire_check  火情监测 {enable}")
    logger.info("  GET  /api/fire_check  火情状态+告警")
    logger.info("  POST /api/hand_ctrl   手势控制 {enable}")
    logger.info("  GET  /api/hand_ctrl   手势状态")
    logger.info("  POST /api/servo       PWM舵机 {id,angle}")
    logger.info("  POST /api/arm         机械臂 {id,angle,time}")
    logger.info("  POST /api/voice/start  开始录音")
    logger.info("  POST /api/voice/stop   停止录音并识别")
    logger.info("  GET  /api/voice         语音识别状态")
    logger.info("  POST /api/smart/start   智能语音 (STT→LLM→TTS)")
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
        # 停止火情监测 + 手势控制 (停止帧处理器)
        fire_svc.stop()
        gesture_svc.stop()
        remove_frame_processor(fire_svc.process_frame)
        remove_frame_processor(gesture_svc.process_frame)
        motion_svc.stop()
        camera_drv.release()
        voice_svc.stop()
        logger.info("已停止。")


if __name__ == "__main__":
    main()
