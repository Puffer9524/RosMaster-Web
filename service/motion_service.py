"""
运动控制服务 — 匹配 app_sim2.py 的控制模式

对照 app_sim2.py:
    # 速度控制
    self.speed = 50  # 默认速度百分比
    speeds = [10, 20, 30, 50]

    # 方向按钮
    buttons = [
        ("前进", [self.speed*1.0/100.0, 0., 0.], ...),
        ("后退", [-self.speed*1.0/100.0, 0., 0.], ...),
        ("左平移", [0., self.speed*1.0/100.0, 0.], ...),
        ("右平移", [0., -self.speed*1.0/100.0, 0.], ...),
        ("左转", [0., 0., self.speed*3.2/100.0], ...),
        ("右转", [0., 0., -self.speed*3.2/100.0], ...),
        ("停止", [0., 0., 0.], ...)
    ]

    # 执行模式
    def execute_command_with_duration(self, command):
        if self.last_time == "持续":
            self.g_bot.set_car_motion(*command)  # 持续运动
        else:
            self.g_bot.set_car_motion(*command)  # 运动一段时间后自动停止
            self.cmd_timer_id = self.root.after(int(self.last_time*1000), self.stop_move)

    def stop_move(self):
        for _ in range(3):
            self.g_bot.set_car_motion(0., 0., 0.)
"""
import threading


class MotionService:
    """
    运动控制 + 超时看门狗, 匹配 app_sim2.py 模式

    对照 app_sim2.py:
        execute_command_with_duration() → self.g_bot.set_car_motion(*command)
        stop_move() → 连发3次零速度
    """

    # 预设速度档位 (匹配 app_sim2.py speeds = [10, 20, 30, 50])
    SPEED_LEVELS = [0.3, 0.6, 1.0]  # 对应于慢/中/快
    SPEED_PERCENTS = [10, 20, 30, 50, 60, 80, 100]
    DEFAULT_SPEED = 50

    # 旋转速度系数 (匹配 app_sim2.py: self.speed*3.2/100.0)
    ROTATION_FACTOR = 3.2

    def __init__(self, driver, timeout: float = 0.5, speed: int = 50):
        """
        Args:
            driver: SerialDriver 实例
            timeout: 超时无新指令 → 自动停车 [秒]
            speed: 默认速度百分比 [0-100]
        """
        self._driver = driver
        self._timeout = timeout
        self._speed = speed
        self._duration_mode = "continuous"  # "continuous" | "timed"
        self._duration = 1.0  # 定时模式下的持续时间 [秒]
        self._timer = None
        self._lock = threading.Lock()

    # ── 速度管理 (匹配 app_sim2.py update_speed) ──

    @property
    def speed(self) -> int:
        """当前速度百分比 [0-100]"""
        return self._speed

    @speed.setter
    def speed(self, value: int) -> None:
        """设置速度百分比, 自动裁剪到 [0, 100]"""
        self._speed = max(0, min(100, int(value)))

    # ── 持续时间模式 (匹配 app_sim2.py last_time) ──

    @property
    def duration_mode(self) -> str:
        return self._duration_mode

    @duration_mode.setter
    def duration_mode(self, mode: str) -> None:
        """设置持续时间模式: "continuous" | 浮点数秒数"""
        if mode == "continuous":
            self._duration_mode = "continuous"
        else:
            self._duration_mode = "timed"
            self._duration = float(mode)

    # ── 方向运动 (匹配 app_sim2.py 按钮命令) ──

    def _compute_command(self, direction: str) -> tuple:
        """根据方向和当前速度计算运动命令

        匹配 app_sim2.py:
            ("前进", [self.speed*1.0/100.0, 0., 0.])
            ("后退", [-self.speed*1.0/100.0, 0., 0.])
            ("左平移", [0., self.speed*1.0/100.0, 0.])
            ("右平移", [0., -self.speed*1.0/100.0, 0.])
            ("左转", [0., 0., self.speed*3.2/100.0])
            ("右转", [0., 0., -self.speed*3.2/100.0])
            ("停止", [0., 0., 0.])
        """
        s = self._speed / 100.0  # 百分比转比例
        commands = {
            "forward":     ( s,  0.0,  0.0),
            "backward":    (-s,  0.0,  0.0),
            "left":        (0.0,  s,   0.0),
            "right":       (0.0, -s,   0.0),
            "turn_left":   (0.0, 0.0,  s * self.ROTATION_FACTOR),
            "turn_right":  (0.0, 0.0, -s * self.ROTATION_FACTOR),
            "stop":        (0.0, 0.0,  0.0),
        }
        return commands.get(direction, (0.0, 0.0, 0.0))

    def move(self, direction: str) -> None:
        """按方向移动 (匹配 app_sim2.py 按钮)

        Args:
            direction: "forward"/"backward"/"left"/"right"/
                       "turn_left"/"turn_right"/"stop"
        """
        vx, vy, vz = self._compute_command(direction)
        self.execute(vx, vy, vz)

    def execute(self, vx: float, vy: float, vz: float) -> None:
        """执行运动指令并重置看门狗

        匹配 app_sim2.py execute_command_with_duration():
            self.g_bot.set_car_motion(*command)
        """
        self._driver.set_motion(vx, vy, vz)
        self._reset_watchdog()

    # ── 状态控制 (匹配 app_sim2.py rosmain_main.py set_car_run 方式) ──

    def execute_state(self, state: int, speed: int = None) -> None:
        """方向状态控制 (按钮模式)

        匹配 Rosmaster-App rosmaster_main.py ctrl_car_x3():
            self.g_bot.set_car_run(state, speed)

        Args:
            state: 0=停止, 1=前进, 2=后退, 3=向左, 4=向右,
                   5=左旋, 6=右旋, 7=停车
            speed: 速度百分比, 默认使用当前速度
        """
        if speed is None:
            speed = self._speed
        self._driver.set_car_run(state, speed)
        if state == 0:
            self._cancel_watchdog()
        else:
            self._reset_watchdog()

    # ── 麦克纳姆轮独立控制 (匹配 Rosmaster-App rosmaster_main.py cmd=0x20/0x21) ──

    def execute_motor(self, m1: int, m2: int, m3: int, m4: int) -> None:
        """四轮独立 PWM 控制

        Args:
            m1~m4: 各轮速度 [-100, 100]
        """
        self._driver.set_motor(m1, m2, m3, m4)
        self._reset_watchdog()

    # ── 舵机控制 ──

    def set_servo(self, servo_id: int, angle: float) -> None:
        """PWM 舵机控制 [0-180]"""
        self._driver.set_pwm_servo(servo_id, angle)

    def set_arm_servo(self, servo_id: int, angle: float,
                      run_time: int = 500) -> None:
        """机械臂单关节控制"""
        self._driver.set_uart_servo_angle(servo_id, angle, run_time)

    def set_arm_servo_array(self, angles: list, run_time: int = 500) -> None:
        """机械臂整体姿态控制

        匹配 Rosmaster-App rosmaster_main.py cmd=0x43 姿态:
            防撞姿态: [90, 180, 0, 0, 90, 30]
            跳舞姿态: [90, 90, 90, 90, 90, 90] + sequence
            巡线姿态: [90, 140, 0, 0, 90, 30]
        """
        self._driver.set_uart_servo_angle_array(angles, run_time)

    # ── 停止 ──

    def stop(self) -> None:
        """立即停车并取消看门狗

        匹配 app_sim2.py stop_move():
            for _ in range(3):
                self.g_bot.set_car_motion(0., 0., 0.)
        """
        self._cancel_watchdog()
        for _ in range(3):
            self._driver.set_motion(0.0, 0.0, 0.0)

    def emergency_stop(self) -> None:
        """紧急停车 (同时关蜂鸣器)

        匹配 app_sim2.py: set_beep(0) + set_car_motion(0,0,0)
        """
        self._cancel_watchdog()
        self._driver.emergency_stop()

    # ── 看门狗 (匹配 app_sim2.py root.after 定时器) ──

    def _reset_watchdog(self) -> None:
        """重置超时计时器

        匹配 app_sim2.py:
            self.cmd_timer_id = self.root.after(int(self.last_time*1000), self.stop_move)
        """
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._timeout, self._on_timeout)
            self._timer.daemon = True
            self._timer.start()

    def _cancel_watchdog(self) -> None:
        """取消看门狗"""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _on_timeout(self) -> None:
        """看门狗触发 → 自动停车

        匹配 app_sim2.py stop_move()
        """
        for _ in range(3):
            self._driver.set_motion(0.0, 0.0, 0.0)
