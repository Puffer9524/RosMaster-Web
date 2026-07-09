"""
Flask HTTP 路由 + REST API

对照 app_sim2.py:
    无 HTTP 层 — app_sim2.py 是本地桌面 GUI

Web 方案:
    - MJPEG 视频流: /video_feed
    - 传感器轮询:   GET  /api/sensors
    - 运动控制:     POST /api/motion (摇杆) /api/direction (按钮)
    - 灯光:         POST /api/rgb, /api/rgb_effect, /api/light
    - 蜂鸣器:       POST /api/beep, /api/buzzer
    - 功能开关:     POST /api/follow_line
    - 舵机/机械臂:  POST /api/servo, /api/arm, /api/arm_array
    - 速度:         POST /api/speed
"""
import json
import time
from flask import Blueprint, Response, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import os

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

bp = Blueprint("routes", __name__)

# 模块级引用，由 main.py 注入
_services = {}


def inject_services(motion_svc, light_svc, sensor_svc, serial_drv, voice_svc=None):
    """注入业务服务引用 (main.py 调用)"""
    _services["motion"] = motion_svc
    _services["light"] = light_svc
    _services["sensor"] = sensor_svc
    _services["driver"] = serial_drv
    _services["voice"] = voice_svc


# =================================================================
# 静态页面
# =================================================================

@bp.route("/")
def index():
    """返回遥控页面"""
    return send_from_directory(STATIC_DIR, "index.html")


@bp.route("/<path:filename>")
def static_files(filename):
    """CSS / JS 等静态资源 (禁用缓存，确保前端总是最新版本)"""
    resp = send_from_directory(STATIC_DIR, filename)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# =================================================================
# 传感器 API (对照 app_sim2.py update_iot_labels)
# =================================================================

@bp.route("/api/sensors")
def api_sensors():
    """返回完整传感器快照

    对照 app_sim2.py update_iot_labels():
        self.iot_label_texts[0].set(f"{self.g_bot.get_temperature_data()}")
        self.iot_label_texts[1].set(f"{self.g_bot.get_humidity_data()}")
        ... 共9项传感器
    """
    driver = _services.get("driver")
    if not driver:
        return jsonify({"error": "not initialized"}), 503
    try:
        data = driver.get_sensor_snapshot()
        data["ts"] = int(time.time())
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =================================================================
# 运动控制 API (对照 app_sim2.py execute_command_with_duration)
# =================================================================

@bp.route("/api/motion", methods=["POST"])
def api_motion():
    """速度矢量运动控制 (摇杆/WASD)

    请求: {"vx": 0.5, "vy": 0.0, "vz": 0.0}

    对照 app_sim2.py:
        ("前进", [self.speed*1.0/100.0, 0., 0.])
        → self.g_bot.set_car_motion(vx, vy, vz)
    """
    motion = _services.get("motion")
    if not motion:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    vx = float(data.get("vx", 0))
    vy = float(data.get("vy", 0))
    vz = float(data.get("vz", 0))
    motion.execute(vx, vy, vz)
    return jsonify({"ok": True, "vx": vx, "vy": vy, "vz": vz})


@bp.route("/api/direction", methods=["POST"])
def api_direction():
    """方向按钮控制

    请求: {"direction": "forward"}
    可选方向: forward/backward/left/right/turn_left/turn_right/stop

    对照 app_sim2.py 方向按钮:
        ("前进", [self.speed*1.0/100.0, 0., 0.])
        ("左转", [0., 0., self.speed*3.2/100.0])
    """
    motion = _services.get("motion")
    if not motion:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    direction = data.get("direction", "stop")
    motion.move(direction)
    return jsonify({"ok": True, "direction": direction})


@bp.route("/api/motor", methods=["POST"])
def api_motor():
    """四轮独立 PWM 控制

    请求: {"m1": 50, "m2": 50, "m3": 50, "m4": 50}

    对照 Rosmaster-App cmd=0x20: self.g_bot.set_motor(m1, m2, m3, m4)
    """
    motion = _services.get("motion")
    if not motion:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    motion.execute_motor(
        int(data.get("m1", 0)), int(data.get("m2", 0)),
        int(data.get("m3", 0)), int(data.get("m4", 0)),
    )
    return jsonify({"ok": True})


# =================================================================
# 速度控制
# =================================================================

@bp.route("/api/speed", methods=["POST"])
def api_speed():
    """速度百分比设置

    请求: {"value": 50}

    对照 app_sim2.py update_speed():
        self.speed = self.speed_var.get()
    """
    motion = _services.get("motion")
    if not motion:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    speed = int(data.get("value", 50))
    motion.speed = max(0, min(100, speed))
    return jsonify({"ok": True, "speed": motion.speed})


# =================================================================
# 灯光 API (对照 app_sim2.py toggle_light)
# =================================================================

@bp.route("/api/light", methods=["POST"])
def api_light():
    """照明灯开关

    请求: {"enable": true}

    对照 app_sim2.py toggle_light():
        self.g_bot.set_light(0/1)
    """
    driver = _services.get("driver")
    if not driver:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    enable = bool(data.get("enable", False))
    driver.set_light(enable)
    return jsonify({"ok": True, "enabled": enable})


@bp.route("/api/rgb", methods=["POST"])
def api_rgb():
    """RGB 灯带颜色

    请求: {"r": 255, "g": 0, "b": 0}

    对照 Rosmaster-App cmd=0x30:
        self.g_bot.set_colorful_lamps(0xFF, r, g, b)
    """
    light = _services.get("light")
    if not light:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    light.set_rgb(
        int(data.get("r", 0)),
        int(data.get("g", 0)),
        int(data.get("b", 0)),
    )
    return jsonify({"ok": True})


@bp.route("/api/rgb_effect", methods=["POST"])
def api_rgb_effect():
    """RGB 灯带特效

    请求: {"effect": 3, "speed": 5}
    effect: 0=关 1=流水 2=跑马 3=呼吸 4=渐变 5=星光 6=电量

    对照 Rosmaster-App cmd=0x31:
        self.g_bot.set_colorful_effect(effect, speed)
    """
    light = _services.get("light")
    if not light:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    light.set_effect(
        int(data.get("effect", 0)),
        int(data.get("speed", 5)),
    )
    return jsonify({"ok": True})


# =================================================================
# 蜂鸣器 API (对照 app_sim2.py toggle_beep)
# =================================================================

@bp.route("/api/beep", methods=["POST"])
def api_beep():
    """蜂鸣器开关

    请求: {"enable": true}

    对照 app_sim2.py toggle_beep():
        self.g_bot.set_beep(100)  # 100ms
        self.g_bot.set_beep(0)    # 关闭
    """
    driver = _services.get("driver")
    if not driver:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    enable = bool(data.get("enable", False))
    driver.set_beep(100 if enable else 0)
    return jsonify({"ok": True, "enabled": enable})


@bp.route("/api/buzzer", methods=["POST"])
def api_buzzer():
    """蜂鸣器独立控制 (指定时长)

    请求: {"time": 100}
    """
    driver = _services.get("driver")
    if not driver:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    driver.set_beep(int(data.get("time", 0)))
    return jsonify({"ok": True})


# =================================================================
# 巡线 API (对照 app_sim2.py toggle_follow_line)
# =================================================================

@bp.route("/api/follow_line", methods=["POST"])
def api_follow_line():
    """巡线功能开关

    请求: {"enable": true}

    对照 app_sim2.py toggle_follow_line():
        self.g_bot.set_follow_line(1/0)
    """
    driver = _services.get("driver")
    motion = _services.get("motion")
    if not driver:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    enable = bool(data.get("enable", False))
    driver.set_follow_line(enable)
    if not enable and motion:
        motion.stop()
    return jsonify({"ok": True, "enabled": enable})


# =================================================================
# 舵机 / 机械臂 API
# =================================================================

@bp.route("/api/servo", methods=["POST"])
def api_servo():
    """PWM 舵机控制

    请求: {"id": 1, "angle": 90}

    对照 Rosmaster-App cmd=0x11:
        self.g_bot.set_pwm_servo(num_id, num_angle)
    """
    motion = _services.get("motion")
    if not motion:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    motion.set_servo(int(data.get("id", 1)), float(data.get("angle", 90)))
    return jsonify({"ok": True})


@bp.route("/api/arm", methods=["POST"])
def api_arm():
    """机械臂单关节控制

    请求: {"id": 1, "angle": 90, "time": 500}

    对照 Rosmaster-App cmd=0x12:
        self.g_bot.set_uart_servo_angle(num_id, angle)
    """
    motion = _services.get("motion")
    if not motion:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    motion.set_arm_servo(
        int(data.get("id", 1)),
        float(data.get("angle", 90)),
        int(data.get("time", 500)),
    )
    return jsonify({"ok": True})


@bp.route("/api/arm_array", methods=["POST"])
def api_arm_array():
    """机械臂整体姿态

    请求: {"angles": [90, 90, 90, 90, 90, 180], "time": 500}

    对照 Rosmaster-App cmd=0x43:
        self.g_bot.set_uart_servo_angle_array(angle_array)
    """
    motion = _services.get("motion")
    if not motion:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    angles = data.get("angles", [90, 90, 90, 90, 90, 180])
    run_time = int(data.get("time", 500))
    motion.set_arm_servo_array(angles, run_time)
    return jsonify({"ok": True})


# =================================================================
# 音频播放 API (对照 app_sim2.py playSound → UDP 10001)
# =================================================================

# 允许的音频扩展名
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma"}


def _get_sound_dir():
    """获取音效目录 (优先 config.SOUND_DIR, 默认 /home/jetson/sound)"""
    try:
        from config import SOUND_DIR
        return SOUND_DIR
    except ImportError:
        return "/home/jetson/sound"


def _scan_sound_dir():
    """扫描音频目录下所有文件"""
    d = _get_sound_dir()
    result = []
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        return result
    try:
        for fname in sorted(os.listdir(d)):
            full = os.path.join(d, fname)
            if not os.path.isfile(full):
                continue
            name, ext = os.path.splitext(fname)
            if ext.lower() not in _AUDIO_EXTS:
                continue
            size = os.path.getsize(full)
            result.append({
                "id": name,
                "name": fname,
                "size": size,
                "ext": ext.lower(),
                "group": "自定义",
            })
    except Exception:
        pass
    return result


@bp.route("/api/sounds")
def api_sounds():
    """返回 /home/jetson/sound/ 下所有音频文件"""
    driver = _services.get("driver")

    # 内置音效列表 (仅作为参考)
    builtin = list(driver.SOUND_LIST) if driver else []
    builtin_ids = {s["id"] for s in builtin}

    # 以磁盘上实际文件为准
    files = _scan_sound_dir()

    if files:
        # 有文件 → 只返回文件列表 (按 group 标记: 匹配内置ID的归类, 不匹配的归"自定义")
        for f in files:
            if f["id"] in builtin_ids:
                # 找到对应的内置条目名称
                match = next((s for s in builtin if s["id"] == f["id"]), None)
                f["name"] = match["name"] if match else f["name"]
                f["group"] = match["group"] if match else "自定义"
        return jsonify(files)
    else:
        # 没有文件 → 返回内置列表供选择 (但播放可能无效)
        return jsonify(builtin)


@bp.route("/api/sound", methods=["POST"])
def api_sound():
    """播放歌曲 (UDP → 127.0.0.1:10001)

    请求: {"id": "song_1"}   # id 与 /home/jetson/sound/ 下的文件名前缀匹配
    """
    driver = _services.get("driver")
    if not driver:
        return jsonify({"error": "not initialized"}), 503
    data = request.get_json(silent=True) or {}
    sound_id = data.get("id", "").strip()
    if sound_id:
        driver.play_sound(sound_id)
    return jsonify({"ok": True, "id": sound_id})


@bp.route("/api/sound/upload", methods=["POST"])
def api_sound_upload():
    """上传音频文件

    表单: file=音频文件, id=音效标识(可选, 默认用文件名)
    """
    driver = _services.get("driver")
    if not driver:
        return jsonify({"error": "not initialized"}), 503

    file = request.files.get("file")
    if not file or file.filename == "":
        return jsonify({"error": "未选择文件"}), 400

    orig = secure_filename(file.filename)
    name, ext = os.path.splitext(orig)
    if ext.lower() not in _AUDIO_EXTS:
        return jsonify({"error": f"不支持 {ext}, 支持: {', '.join(sorted(_AUDIO_EXTS))}"}), 400

    sound_id = (request.form.get("id") or "").strip()
    if not sound_id:
        sound_id = name

    d = _get_sound_dir()
    os.makedirs(d, exist_ok=True)
    save_path = os.path.join(d, f"{sound_id}_{orig}")

    try:
        file.save(save_path)
    except Exception as e:
        return jsonify({"error": f"保存失败: {e}"}), 500

    # 上传成功自动播放
    driver.play_sound(sound_id)

    return jsonify({
        "ok": True,
        "id": sound_id,
        "filename": f"{sound_id}_{orig}",
        "size": os.path.getsize(save_path),
    })


@bp.route("/api/sound/delete", methods=["POST"])
def api_sound_delete():
    """删除自定义上传的音频文件

    请求: {"id": "song_4"}
    """
    data = request.get_json(silent=True) or {}
    sound_id = (data.get("id") or "").strip()
    if not sound_id:
        return jsonify({"error": "缺少 id"}), 400

    d = _get_sound_dir()
    deleted = []
    try:
        for fname in os.listdir(d):
            if fname.startswith(sound_id):
                full = os.path.join(d, fname)
                if os.path.isfile(full):
                    os.remove(full)
                    deleted.append(fname)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "deleted": deleted})


# =================================================================
# MJPEG 视频流
# =================================================================

def create_video_feed(camera_driver, quality: int = 65):
    """创建 MJPEG 视频流生成器

    对照 app_sim2.py update_camera_frame():
        ret, frame = self.cap.read()
        frame = cv2.resize(frame, (400, 300))
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(frame)
        photo = ImageTk.PhotoImage(image=image)
        self.image_label.config(image=photo)
    """

    def generate():
        t_start = time.time()
        frame_count = 0
        while True:
            success, jpeg = camera_driver.get_jpeg(quality)
            if not success:
                camera_driver.reconnect()
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n\r\n")
                time.sleep(0.5)
                continue

            frame_count += 1
            elapsed = time.time() - t_start
            if elapsed >= 1.0:
                t_start = time.time()
                frame_count = 0

            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
            time.sleep(0.03)  # 控制帧率, 避免撑爆摄像头

    return generate


def register_video_route(app, camera_driver, quality: int = 65):
    """注册 /video_feed 路由"""

    @app.route("/video_feed")
    def video_feed():
        generate = create_video_feed(camera_driver, quality)
        return Response(
            generate(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )


# =================================================================
# 语音识别 API
# =================================================================

@bp.route("/api/voice/start", methods=["POST"])
def api_voice_start():
    """开启语音识别监听"""
    voice = _services.get("voice")
    if voice is None:
        return jsonify({"error": "语音服务未初始化"}), 503
    voice._enabled = True
    if not voice._running:
        voice.start()
    return jsonify({"ok": True, "listening": True})


@bp.route("/api/voice/stop", methods=["POST"])
def api_voice_stop():
    """关闭语音识别监听 (不退出线程, 只暂停识别)"""
    voice = _services.get("voice")
    if voice is None:
        return jsonify({"error": "语音服务未初始化"}), 503
    voice._enabled = False
    return jsonify({"ok": True, "listening": False})


@bp.route("/api/voice")
def api_voice():
    """获取语音识别最新结果"""
    voice = _services.get("voice")
    if voice is None:
        return jsonify({"listening": False, "text": "", "error": "未初始化"})
    return jsonify(voice.get_result())
