"""
WebSocket 消息处理 — 匹配 app_sim2.py 的所有功能

对照 app_sim2.py:
    按钮事件:
        execute_command_with_duration() → self.g_bot.set_car_motion(*cmd)
        toggle_follow_line()           → self.g_bot.set_follow_line(0/1)
        toggle_light()                 → self.g_bot.set_light(0/1)
        toggle_beep()                  → self.g_bot.set_beep(0/100)
        toggle_video_save()            → self.g_capture_video = True/False
        toggle_fire_check()            → 启动/停止火情监测
        toggle_hand_ctrl()             → 启动/停止手势控制
        update_iot_labels()            → 回传9项传感器数据
        update_speed()                 → self.speed = speed_var.get()

Web 方案:
    浏览器 → WebSocket JSON → 调用 service → 串口
"""
import json
import time


class WsHandler:
    """
    WebSocket 消息处理器

    对照 app_sim2.py RobotControlApp:
        - 方向按钮 → execute_command_with_duration / execute_command
        - Checkbutton → toggle_xxx 方法
        - update_iot_labels → 定时回传传感器
    """

    def __init__(self, motion_service, light_service, sensor_service,
                 serial_driver):
        self._motion = motion_service
        self._light = light_service
        self._sensor = sensor_service
        self._driver = serial_driver
        self._ws = None
        self._last_motion_time = 0

        # 功能开关状态 (匹配 app_sim2.py Checkbutton 状态)
        self._follow_line_enabled = False
        self._light_enabled = False
        self._beep_enabled = False
        self._video_save_enabled = False
        self._fire_check_enabled = False

    def register(self, app):
        """注册 WebSocket 端点到 Flask 应用

        兼容 flask_sock 新旧版本:
            - 新版: from flask_sock import Sock; sock = Sock(app)
            - 旧版: from flask_sock import Server; sock = Server(app)
            - 更旧版: sock = Server(); sock.init_app(app)
        """
        try:
            # flask_sock >= 0.6
            from flask_sock import Sock
            sock = Sock(app)
        except ImportError:
            try:
                # flask_sock < 0.6: Server(app)
                from flask_sock import Server
                sock = Server(app)
            except TypeError:
                # flask_sock 最旧版: Server() + init_app
                from flask_sock import Server
                sock = Server()
                sock.init_app(app)

        @sock.route("/ws")
        def handler(ws):
            self._ws = ws
            # 启动传感器推送 (对照 app_sim2.py root.after(3000, update_iot_labels))
            self._sensor.start(self._push_sensor)

            try:
                while True:
                    raw = ws.receive()
                    if raw is None:
                        break
                    self._handle(raw)
            except Exception:
                pass
            finally:
                self._sensor.stop()
                self._motion.stop()
                self._ws = None

    def _handle(self, raw: str) -> None:
        """消息路由

        对照 app_sim2.py 各事件处理方法
        """
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")

        # ── 运动控制 ──
        if msg_type == "motion":
            # 对照: execute_command_with_duration()
            self._handle_motion(msg)
        elif msg_type == "direction":
            # 对照: 方向按钮 → set_car_motion
            self._handle_direction(msg)
        elif msg_type == "motor":
            # 对照: 麦克纳姆轮独立控制
            self._handle_motor(msg)

        # ── 速度控制 ──
        elif msg_type == "speed":
            # 对照: update_speed() → self.speed = speed_var.get()
            self._handle_speed(msg)

        # ── 功能开关 (对照 app_sim2.py Checkbutton) ──
        elif msg_type == "follow_line":
            # 对照: toggle_follow_line()
            self._handle_follow_line(msg)
        elif msg_type == "light":
            # 对照: toggle_light()
            self._handle_light(msg)
        elif msg_type == "beep":
            # 对照: toggle_beep()
            self._handle_beep(msg)
        elif msg_type == "video_save":
            # 对照: toggle_video_save()
            self._handle_video_save(msg)
        elif msg_type == "fire_check":
            # 对照: toggle_fire_check()
            self._handle_fire_check(msg)

        # ── RGB 灯带 ──
        elif msg_type == "rgb":
            self._handle_rgb(msg)
        elif msg_type == "rgb_effect":
            self._handle_rgb_effect(msg)

        # ── 舵机控制 ──
        elif msg_type == "servo":
            self._handle_servo(msg)
        elif msg_type == "arm":
            self._handle_arm(msg)
        elif msg_type == "arm_array":
            self._handle_arm_array(msg)

        # ── 蜂鸣器 ──
        elif msg_type == "buzzer":
            self._handle_buzzer(msg)

        # ── 心跳 ──
        elif msg_type == "ping":
            self._send_json({"type": "pong"})

    # =================================================================
    # 运动控制
    # =================================================================

    def _handle_motion(self, msg: dict) -> None:
        """速度矢量运动控制 (摇杆模式), 50ms 节流

        对照 app_sim2.py:
            ("前进", [self.speed*1.0/100.0, 0., 0.])
            → self.g_bot.set_car_motion(vx, vy, vz)
        """
        now = time.time()
        if now - self._last_motion_time < 0.05:
            return
        self._last_motion_time = now

        vx = float(msg.get("vx", 0))
        vy = float(msg.get("vy", 0))
        vz = float(msg.get("vz", 0))
        self._motion.execute(vx, vy, vz)

    def _handle_direction(self, msg: dict) -> None:
        """方向按钮控制

        对照 app_sim2.py 按钮:
            ("前进", [self.speed*1.0/100.0, 0., 0.])
            ("左转", [0., 0., self.speed*3.2/100.0])
            等

        Args:
            msg.direction: "forward"/"backward"/"left"/"right"/
                          "turn_left"/"turn_right"/"stop"
        """
        direction = msg.get("direction", "stop")
        self._motion.move(direction)

    def _handle_motor(self, msg: dict) -> None:
        """四轮独立控制 (麦克纳姆轮)

        对照 Rosmaster-App cmd=0x20/0x21:
            self.g_bot.set_motor(m1, m2, m3, m4)
        """
        m1 = int(msg.get("m1", 0))
        m2 = int(msg.get("m2", 0))
        m3 = int(msg.get("m3", 0))
        m4 = int(msg.get("m4", 0))
        self._motion.execute_motor(m1, m2, m3, m4)

    # =================================================================
    # 速度控制
    # =================================================================

    def _handle_speed(self, msg: dict) -> None:
        """速度百分比设置

        对照 app_sim2.py update_speed():
            self.speed = self.speed_var.get()
            speeds = [10, 20, 30, 50]
        """
        speed = int(msg.get("value", 50))
        self._motion.speed = max(0, min(100, speed))
        self._send_json({"type": "ack", "cmd": "speed",
                         "value": self._motion.speed})

    # =================================================================
    # 功能开关 (对照 app_sim2.py toggle_xxx)
    # =================================================================

    def _handle_follow_line(self, msg: dict) -> None:
        """巡线功能开关

        对照 app_sim2.py toggle_follow_line():
            if self.follow_line.get() == 1:
                self.g_bot.set_follow_line(1)
            else:
                self.g_bot.set_follow_line(0)
                self.g_bot.set_car_motion(0., 0., 0.)
        """
        enable = bool(msg.get("enable", False))
        self._follow_line_enabled = enable
        self._driver.set_follow_line(enable)
        if not enable:
            self._motion.stop()
        self._send_json({"type": "ack", "cmd": "follow_line",
                         "enabled": enable})

    def _handle_light(self, msg: dict) -> None:
        """照明灯开关

        对照 app_sim2.py toggle_light():
            self.g_bot.set_light(0/1)
        """
        enable = bool(msg.get("enable", False))
        self._light_enabled = enable
        self._driver.set_light(enable)
        self._send_json({"type": "ack", "cmd": "light",
                         "enabled": enable})

    def _handle_beep(self, msg: dict) -> None:
        """蜂鸣器控制

        对照 app_sim2.py toggle_beep():
            if self.beep.get() == 1:
                self.g_bot.set_beep(100)
            else:
                self.g_bot.set_beep(0)
        """
        enable = bool(msg.get("enable", False))
        self._beep_enabled = enable
        if enable:
            self._driver.set_beep(100)  # 100ms 蜂鸣
        else:
            self._driver.set_beep(0)
        self._send_json({"type": "ack", "cmd": "beep",
                         "enabled": enable})

    def _handle_video_save(self, msg: dict) -> None:
        """视频录制开关

        对照 app_sim2.py toggle_video_save():
            self.g_capture_video = True/False
        """
        enable = bool(msg.get("enable", False))
        self._video_save_enabled = enable
        self._send_json({"type": "ack", "cmd": "video_save",
                         "enabled": enable})

    def _handle_fire_check(self, msg: dict) -> None:
        """火情监测开关

        对照 app_sim2.py toggle_fire_check():
            启动/停止 AI 火情检测
        """
        enable = bool(msg.get("enable", False))
        self._fire_check_enabled = enable
        self._send_json({"type": "ack", "cmd": "fire_check",
                         "enabled": enable})

    # =================================================================
    # RGB 灯带
    # =================================================================

    def _handle_rgb(self, msg: dict) -> None:
        """RGB 灯带颜色

        对照 Rosmaster-App cmd=0x30:
            self.g_bot.set_colorful_lamps(num_id, num_r, num_g, num_b)
        """
        r = int(msg.get("r", 0))
        g = int(msg.get("g", 0))
        b = int(msg.get("b", 0))
        self._light.set_rgb(r, g, b)
        self._send_json({"type": "ack", "cmd": "rgb", "ok": True})

    def _handle_rgb_effect(self, msg: dict) -> None:
        """RGB 灯带特效

        对照 Rosmaster-App cmd=0x31:
            self.g_bot.set_colorful_effect(effect, speed, 255)
        """
        effect = int(msg.get("effect", 0))
        speed = int(msg.get("speed", 5))
        self._light.set_effect(effect, speed)
        self._send_json({"type": "ack", "cmd": "rgb_effect", "ok": True})

    # =================================================================
    # 舵机 / 机械臂控制
    # =================================================================

    def _handle_servo(self, msg: dict) -> None:
        """PWM 舵机控制

        对照 Rosmaster-App cmd=0x11:
            self.g_bot.set_pwm_servo(num_id, num_angle)
        """
        servo_id = int(msg.get("id", 1))
        angle = float(msg.get("angle", 90))
        self._motion.set_servo(servo_id, angle)
        self._send_json({"type": "ack", "cmd": "servo",
                         "id": servo_id, "angle": angle})

    def _handle_arm(self, msg: dict) -> None:
        """机械臂单关节控制

        对照 Rosmaster-App cmd=0x12:
            self.g_bot.set_uart_servo_angle(num_id, uart_servo_angle)
        """
        servo_id = int(msg.get("id", 1))
        angle = float(msg.get("angle", 90))
        run_time = int(msg.get("time", 500))
        self._motion.set_arm_servo(servo_id, angle, run_time)
        self._send_json({"type": "ack", "cmd": "arm",
                         "id": servo_id, "angle": angle})

    def _handle_arm_array(self, msg: dict) -> None:
        """机械臂整体姿态

        对照 Rosmaster-App cmd=0x43:
            angle_array = [90, 180-0, 180-180, 180-180, 90, 30]
            self.g_bot.set_uart_servo_angle_array(angle_array)
        """
        angles = msg.get("angles", [90, 90, 90, 90, 90, 180])
        run_time = int(msg.get("time", 500))
        self._motion.set_arm_servo_array(angles, run_time)
        self._send_json({"type": "ack", "cmd": "arm_array"})

    # =================================================================
    # 蜂鸣器 (独立控制)
    # =================================================================

    def _handle_buzzer(self, msg: dict) -> None:
        """蜂鸣器独立控制

        对照 app_sim2.py toggle_beep():
            self.g_bot.set_beep(100) 或 set_beep(0)
        """
        on_time = int(msg.get("time", 0))
        self._driver.set_beep(on_time)
        self._send_json({"type": "ack", "cmd": "buzzer",
                         "time": on_time})

    # =================================================================
    # 传感器推送
    # =================================================================

    def _push_sensor(self, data: dict) -> None:
        """推送传感器数据到浏览器

        对照 app_sim2.py update_iot_labels():
            self.iot_label_texts[0].set(f"{temperature}")
            self.iot_label_texts[1].set(f"{humidity}")
            self.iot_label_texts[2].set(f"{atmosphere}")
            self.iot_label_texts[3].set(f"{light}")
            self.iot_label_texts[4].set(f"{gas}")
            self.iot_label_texts[5].set(f"{pm25}")
            self.iot_label_texts[6].set(f"{latitude}")
            self.iot_label_texts[7].set(f"{longitude}")
            self.iot_label_texts[8].set(f"{battery}")
        """
        self._send_json({
            "type": "sensor",
            "ts": int(time.time()),
            "data": data,
        })

    # =================================================================
    # 工具方法
    # =================================================================

    def _send_json(self, obj: dict) -> None:
        """发送 JSON 给当前客户端"""
        if self._ws is not None:
            try:
                self._ws.send(json.dumps(obj))
            except Exception:
                pass
