"""
串口驱动 — Rosmaster_Lib 的完整线程安全封装

对照 app_sim2.py:
    from Rosmaster_Lib import Rosmaster  # 直接导入（已通过 setup.py 安装）
    self.g_bot = Rosmaster(debug=True)
    self.g_bot.create_receive_threading()
    self.soundSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.soundAddr = ('0.0.0.0', 10001)
    self.playSound("car_awake")

本模块将 Rosmaster_Lib 的全部能力通过线程安全的封装暴露出来，
供上层 service 调用。
"""
import sys
import os
import subprocess
import threading

# 与 app_sim2.py 保持一致：优先使用已安装的 Rosmaster_Lib
try:
    from Rosmaster_Lib import Rosmaster
except ImportError:
    # 如果未安装，尝试相对路径查找
    SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
    LIB_DIR = os.path.join(PROJECT_DIR, "py_install_V3.3.1", "Rosmaster_Lib")
    if os.path.isdir(LIB_DIR) and LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    from Rosmaster_Lib import Rosmaster


class SerialDriver:
    """
    Rosmaster_Lib 的完整线程安全封装

    对照 Rosmaster-App:
        rosmaster_main.py MyRosmasterApp.__init__:
            self.g_bot = Rosmaster(debug=self.g_debug)
            self.g_bot.create_receive_threading()
    """

    # ── 车型常量 (与 Rosmaster_Lib 保持一致) ──
    CARTYPE_X3 = 0x01
    CARTYPE_X3_PLUS = 0x02
    CARTYPE_X1 = 0x04
    CARTYPE_R2 = 0x05

    # ── 运动方向常量 (set_car_run 使用) ──
    RUN_STOP = 0
    RUN_FORWARD = 1
    RUN_BACKWARD = 2
    RUN_LEFT = 3
    RUN_RIGHT = 4
    RUN_SPIN_LEFT = 5
    RUN_SPIN_RIGHT = 6
    RUN_PARK = 7

    def __init__(self, port: str, car_type: int = 1, debug: bool = False):
        self._port = port
        self._car_type = car_type
        self._write_lock = threading.Lock()
        self._debug = debug

        # 对照: self.g_bot = Rosmaster(debug=True)
        self._bot = Rosmaster(car_type=car_type, com=port, debug=debug)

        # 对照: self.g_bot.create_receive_threading()
        self._bot.create_receive_threading()

        # 音频播放 — 用系统命令 (ffplay/mplayer/aplay), 最可靠
        self._player_cmd = self._detect_player()
        self._current_player = None  # 正在播放的子进程
        self._player_lock = threading.Lock()

    # =================================================================
    # 传感器读取 (对照 Rosmaster_Lib get_xxx 方法)
    # =================================================================

    def get_temperature(self) -> float:
        """温度 [°C]"""
        return self._bot.get_temperature_data()

    def get_humidity(self) -> float:
        """湿度 [%RH]"""
        return self._bot.get_humidity_data()

    def get_atmosphere(self) -> float:
        """大气压 [kPa]"""
        return self._bot.get_atmosphere_data()

    def get_light(self) -> float:
        """光照强度 [Lux]"""
        return self._bot.get_light_data()

    def get_gas(self) -> float:
        """气体传感器"""
        return self._bot.get_gas_data()

    def get_pm25(self) -> float:
        """PM2.5 [μg/m³]"""
        return self._bot.get_pm25_data()

    def get_latitude(self) -> float:
        """GPS 纬度 [度]"""
        return self._bot.get_latitude_data()

    def get_longitude(self) -> float:
        """GPS 经度 [度]"""
        return self._bot.get_longitude_data()

    def get_battery(self) -> float:
        """电池电压 [V]"""
        return self._bot.get_battery_voltage()

    def get_version(self) -> float:
        """MCU 固件版本号"""
        return self._bot.get_version()

    # ── IMU 数据 ──

    def get_accelerometer(self) -> dict:
        """加速度计三轴 [m/s²]"""
        ax, ay, az = self._bot.get_accelerometer_data()
        return {"x": ax, "y": ay, "z": az}

    def get_gyroscope(self) -> dict:
        """陀螺仪三轴 [rad/s]"""
        gx, gy, gz = self._bot.get_gyroscope_data()
        return {"x": gx, "y": gy, "z": gz}

    def get_magnetometer(self) -> dict:
        """磁力计三轴 [μT]"""
        mx, my, mz = self._bot.get_magnetometer_data()
        return {"x": mx, "y": my, "z": mz}

    def get_imu_attitude(self) -> dict:
        """IMU 姿态角 [度] roll, pitch, yaw"""
        roll, pitch, yaw = self._bot.get_imu_attitude_data(ToAngle=True)
        return {"roll": roll, "pitch": pitch, "yaw": yaw}

    def get_imu_data(self) -> dict:
        """获取完整 IMU 数据"""
        accel = self.get_accelerometer()
        gyro = self.get_gyroscope()
        mag = self.get_magnetometer()
        att = self.get_imu_attitude()
        return {
            "accel": accel,
            "gyro": gyro,
            "mag": mag,
            "attitude": att,
        }

    # ── 环境传感器 ──

    def get_environment_data(self) -> dict:
        """获取完整环境传感器数据"""
        return {
            "atmosphere": self.get_atmosphere(),
            "light": self.get_light(),
            "gas": self.get_gas(),
            "pm25": self.get_pm25(),
            "latitude": self.get_latitude(),
            "longitude": self.get_longitude(),
        }

    # ── 编码器 / 速度 ──

    def get_motor_encoder(self) -> dict:
        """四路电机编码器"""
        m1, m2, m3, m4 = self._bot.get_motor_encoder()
        return {"m1": m1, "m2": m2, "m3": m3, "m4": m4}

    def get_motion_data(self) -> dict:
        """当前运动速度 [m/s, rad/s]"""
        vx, vy, vz = self._bot.get_motion_data()
        return {"vx": vx, "vy": vy, "vz": vz}

    # ── 舵机角度 ──

    def get_uart_servo_angle(self, servo_id: int) -> int:
        """读取单个总线舵机角度 [°]"""
        return self._bot.get_uart_servo_angle(servo_id)

    def get_uart_servo_angle_array(self) -> list:
        """读取全部6个总线舵机角度 [°]"""
        return self._bot.get_uart_servo_angle_array()

    # ── 完整传感器快照 ──

    def get_sensor_snapshot(self) -> dict:
        """获取全部传感器数据快照

        对照 Rosmaster-App rosmaster_main.py return_sensor_data():
            num_temp = self.g_bot.get_temperature_data()
            num_humidity = self.g_bot.get_humidity_data()
            num_atomosphere = self.g_bot.get_atmosphere_data()
            num_light = self.g_bot.get_light_data()
            num_gas = self.g_bot.get_gas_data()
            num_pm25 = self.g_bot.get_pm25_data()
            num_latitude = self.g_bot.get_latitude_data()
            num_longitude = self.g_bot.get_longitude_data()
        """
        return {
            "temperature": self.get_temperature(),
            "humidity": self.get_humidity(),
            "battery": self.get_battery(),
            "atmosphere": self.get_atmosphere(),
            "light": self.get_light(),
            "gas": self.get_gas(),
            "pm25": self.get_pm25(),
            "latitude": self.get_latitude(),
            "longitude": self.get_longitude(),
            "imu": self.get_imu_data(),
            "encoder": self.get_motor_encoder(),
            "speed": self.get_motion_data(),
        }

    # =================================================================
    # 运动控制 (对照 Rosmaster_Lib 运动相关方法)
    # =================================================================

    def set_motion(self, vx: float, vy: float, vz: float) -> None:
        """全向速度矢量控制

        对照 Rosmaster-App rosmaster_main.py parse_data cmd=0x10:
            self.g_bot.set_car_motion(speed_x, speed_y, 0)

        输入范围 (按车型):
            X3:      vx=[-1.0, 1.0], vy=[-1.0, 1.0], vz=[-5.0, 5.0]
            X3_PLUS: vx=[-0.7, 0.7], vy=[-0.7, 0.7], vz=[-3.2, 3.2]
            R2:      vx=[-1.8, 1.8], vy=[-0.045, 0.045], vz=[-3.0, 3.0]
        """
        with self._write_lock:
            self._bot.set_car_motion(vx, vy, vz)

    def set_car_run(self, state: int, speed: int, adjust: bool = False) -> None:
        """方向状态控制 (按钮模式)

        对照 Rosmaster-App rosmaster_main.py parse_data cmd=0x15:
            ctrl_car_x3(state) → self.g_bot.set_car_run(state, speed, ...)

        Args:
            state: 0=停止, 1=前进, 2=后退, 3=向左, 4=向右,
                   5=左旋, 6=右旋, 7=停车
            speed: 速度百分比 [0-100]
            adjust: 是否开启陀螺仪辅助
        """
        with self._write_lock:
            self._bot.set_car_run(state, speed, adjust)

    def set_motor(self, m1: int, m2: int, m3: int, m4: int) -> None:
        """独立四轮 PWM 控制 (麦克纳姆轮模式)

        对照 Rosmaster-App rosmaster_main.py parse_data cmd=0x20/0x21:
            self.g_bot.set_motor(self.g_motor_speed[0], ...)

        Args:
            m1~m4: 各轮速度 [-100, 100]
        """
        with self._write_lock:
            self._bot.set_motor(m1, m2, m3, m4)

    def emergency_stop(self) -> None:
        """紧急停车

        对照 Rosmaster-App rosmaster_main.py:
            self.g_bot.set_car_run(0, self.g_car_stabilize_state)
            or
            self.g_bot.set_car_motion(0, 0, 0)
        """
        with self._write_lock:
            # 先发 set_car_run(7) 停车，再发零速度确保停止
            self._bot.set_car_run(7, 0)
            self._bot.set_car_motion(0.0, 0.0, 0.0)

    # =================================================================
    # PWM 舵机控制
    # =================================================================

    def set_pwm_servo(self, servo_id: int, angle: float) -> None:
        """PWM 舵机角度控制

        对照 Rosmaster-App rosmaster_main.py parse_data cmd=0x11:
            self.g_bot.set_pwm_servo(num_id, num_angle)

        Args:
            servo_id: 舵机 ID [1-4]
            angle: 角度 [0-180]
        """
        with self._write_lock:
            self._bot.set_pwm_servo(servo_id, angle)

    def set_pwm_servo_all(self, a1: float, a2: float, a3: float, a4: float) -> None:
        """同时控制四路 PWM 舵机"""
        with self._write_lock:
            self._bot.set_pwm_servo_all(a1, a2, a3, a4)

    # =================================================================
    # 总线舵机 (机械臂) 控制
    # =================================================================

    def set_uart_servo_angle(self, servo_id: int, angle: float,
                             run_time: int = 500) -> None:
        """总线舵机角度控制 (机械臂单关节)

        对照 Rosmaster-App rosmaster_main.py parse_data cmd=0x12:
            self.g_bot.set_uart_servo_angle(num_id, uart_servo_angle)

        Args:
            servo_id: 舵机 ID [1-6]
            angle: 角度, 1-4号=[0, 180], 5号=[0, 270], 6号=[0, 180]
            run_time: 运行时间 [ms], 越小越快, 最大2000
        """
        with self._write_lock:
            self._bot.set_uart_servo_angle(servo_id, angle, run_time)

    def set_uart_servo_angle_array(self, angles: list,
                                   run_time: int = 500) -> None:
        """机械臂所有舵机同步控制

        对照 Rosmaster-App rosmaster_main.py cmd=0x43:
            angle_array = [90, 180-0, 180-180, 180-180, 90, 30]
            self.g_bot.set_uart_servo_angle_array(angle_array)

        Args:
            angles: 6个角度值 [s1, s2, s3, s4, s5, s6]
            run_time: 运行时间 [ms]
        """
        with self._write_lock:
            self._bot.set_uart_servo_angle_array(angles, run_time)

    def set_uart_servo_torque(self, enable: bool) -> None:
        """总线舵机扭矩开关

        对照 Rosmaster-App rosmaster_main.py cmd=0x42:
            self.g_bot.set_uart_servo_torque(True/False)

        enable=True: 上电 (命令可控, 不可手拧)
        enable=False: 卸力 (可手拧, 命令不可控)
        """
        with self._write_lock:
            self._bot.set_uart_servo_torque(1 if enable else 0)

    def set_uart_servo_offset(self, servo_id: int) -> int:
        """设置总线舵机中位偏差

        对照 Rosmaster-App rosmaster_main.py cmd=0x40:
            state = self.g_bot.set_uart_servo_offset(id)

        Returns:
            校准状态 (0=失败, 1=成功)
        """
        with self._write_lock:
            return self._bot.set_uart_servo_offset(servo_id)

    # =================================================================
    # 其他控制
    # =================================================================

    def set_beep(self, on_time: int) -> None:
        """蜂鸣器控制

        对照 Rosmaster-App rosmaster_main.py parse_data cmd=0x13:
            self.g_bot.set_beep(delay_ms)

        Args:
            on_time: 0=关闭, 1=一直响, >=10=响xx毫秒(10的倍数)
        """
        with self._write_lock:
            self._bot.set_beep(on_time)

    def set_car_type(self, car_type: int) -> None:
        """切换车型

        对照 Rosmaster-App rosmaster_main.py parse_data:
            self.g_bot.set_car_type(self.g_car_type)

        Args:
            car_type: 1=X3, 2=X3_PLUS, 4=X1, 5=R2
        """
        with self._write_lock:
            self._bot.set_car_type(car_type)
        self._car_type = car_type

    def set_follow_line(self, enable: bool) -> None:
        """巡线功能开关

        对照 Rosmaster-App rosmaster_main.py cmd=0x63/0x64:
            self.g_bot.set_follow_line(1) / set_follow_line(0)
        """
        with self._write_lock:
            self._bot.set_follow_line(1 if enable else 0)

    def set_light(self, enable: bool) -> None:
        """照明灯开关

        对照 Rosmaster-App app_sim2.py toggle_light():
            self.g_bot.set_light(0/1)
        """
        with self._write_lock:
            self._bot.set_light(1 if enable else 0)

    def set_auto_report_state(self, enable: bool, forever: bool = False) -> None:
        """设置 MCU 自动上报状态

        enable=True: 开启自动上报 (默认), MCU 每10ms发送一包数据
        enable=False: 关闭自动上报, 影响部分读取功能
        """
        with self._write_lock:
            self._bot.set_auto_report_state(enable, forever)

    # =================================================================
    # RGB 灯带
    # =================================================================

    def set_rgb(self, led_id: int, r: int, g: int, b: int) -> None:
        """设置 RGB 灯带颜色

        对照 Rosmaster-App rosmaster_main.py cmd=0x30:
            self.g_bot.set_colorful_lamps(num_id, num_r, num_g, num_b)

        Args:
            led_id: 0-13 单灯控制, 255 全体控制
            r/g/b: 颜色值 [0-255]
        """
        with self._write_lock:
            self._bot.set_colorful_lamps(led_id, r, g, b)

    def set_rgb_effect(self, effect: int, speed: int = 5,
                       parm: int = 255) -> None:
        """设置 RGB 灯带特效

        对照 Rosmaster-App rosmaster_main.py cmd=0x31:
            self.g_bot.set_colorful_effect(num_effect, num_speed, 255)

        Args:
            effect: 0=关, 1=流水, 2=跑马, 3=呼吸, 4=渐变, 5=星光, 6=电量
            speed: 1-10, 越小越快
            parm: 附加参数 (呼吸灯颜色 [0-6])
        """
        with self._write_lock:
            self._bot.set_colorful_effect(effect, speed, parm)

    # =================================================================
    # 阿克曼 (R2) 专用
    # =================================================================

    def get_akm_default_angle(self) -> int:
        """读取阿克曼前轮默认角度"""
        return self._bot.get_akm_default_angle()

    def set_akm_default_angle(self, angle: int, forever: bool = False) -> None:
        """设置阿克曼前轮默认角度 [60-120]"""
        with self._write_lock:
            self._bot.set_akm_default_angle(angle, forever)

    def set_akm_steering_angle(self, angle: int, ctrl_car: bool = False) -> None:
        """阿克曼转向角 [-45, 45], 相对默认角度"""
        with self._write_lock:
            self._bot.set_akm_steering_angle(angle, ctrl_car)

    # =================================================================
    # PID 参数
    # =================================================================

    def set_pid_param(self, kp: float, ki: float, kd: float,
                      forever: bool = False) -> None:
        """设置运动 PID 参数 [0-10.00]"""
        with self._write_lock:
            self._bot.set_pid_param(kp, ki, kd, forever)

    def get_motion_pid(self) -> list:
        """获取运动 PID 参数 [kp, ki, kd]"""
        return self._bot.get_motion_pid()

    # =================================================================
    # 系统方法
    # =================================================================

    def reset_car_state(self) -> None:
        """重置小车状态: 停车 + 关灯 + 关蜂鸣器

        对照 Rosmaster_Lib.reset_car_state()
        """
        with self._write_lock:
            self._bot.reset_car_state()

    def reset_flash_value(self) -> None:
        """恢复出厂设置 (清除 flash 保存的 PID 等参数)"""
        with self._write_lock:
            self._bot.reset_flash_value()

    def get_car_type_from_machine(self) -> int:
        """从 MCU 读取当前车型"""
        return self._bot.get_car_type_from_machine()

    @property
    def car_type(self) -> int:
        return self._car_type

    @property
    def port(self) -> str:
        return self._port

    # =================================================================
    # 音频播放 (对照 app_sim2.py playSound)
    # =================================================================

    # 小车内置歌曲/音效列表
    # 名称为小车音频播放器监听 127.0.0.1:10001 接收的字符串标识
    # 可根据实际音频文件增删
    SOUND_LIST = [
        {"id": "car_awake",         "name": "小车启动",     "group": "系统"},
        {"id": "follow_line_start", "name": "巡线开始",     "group": "巡线"},
        {"id": "follow_line_stop",  "name": "巡线停止",     "group": "巡线"},
        {"id": "fire_check_open",   "name": "火检开启",     "group": "传感器"},
        {"id": "fire_check_close",  "name": "火检关闭",     "group": "传感器"},
        {"id": "fire_alarm",        "name": "🔥 火情警报",   "group": "传感器"},
        {"id": "hand_ctrl_open",    "name": "手势控制开",   "group": "手势"},
        {"id": "hand_ctrl_close",   "name": "手势控制关",   "group": "手势"},
        {"id": "video_start",       "name": "录像开始",     "group": "系统"},
        {"id": "video_stop",        "name": "录像停止",     "group": "系统"},
        {"id": "car_forward",       "name": "前进",        "group": "手势"},
        {"id": "car_backward",      "name": "后退",        "group": "手势"},
        {"id": "car_shift_right",   "name": "右移",        "group": "手势"},
        {"id": "car_shift_left",    "name": "左移",        "group": "手势"},
        {"id": "car_turn_right",    "name": "右转",        "group": "手势"},
        {"id": "car_turn_left",     "name": "左转",        "group": "手势"},
        {"id": "car_stop",          "name": "停车",        "group": "手势"},
        {"id": "song_1",            "name": "歌曲 1",      "group": "音乐"},
        {"id": "song_2",            "name": "歌曲 2",      "group": "音乐"},
        {"id": "song_3",            "name": "歌曲 3",      "group": "音乐"},
    ]

    @staticmethod
    def _detect_player() -> list:
        """检测系统可用的命令行播放器"""
        for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
                     ["mplayer", "-really-quiet", "-nogui"],
                     ["aplay"],
                     ["paplay"]):
            try:
                subprocess.run([cmd[0], "--help"], capture_output=True, timeout=2)
                return cmd
            except Exception:
                pass
        return ["aplay"]

    def play_sound(self, sound: str) -> None:
        """播放音频文件

        每首歌用独立子进程播放，自动清理僵尸进程。
        """
        SOUND_DIR = "/home/jetson/sound"
        if not os.path.isdir(SOUND_DIR):
            print(f"play_sound: 目录不存在 {SOUND_DIR}")
            return

        path = ""
        try:
            for fname in os.listdir(SOUND_DIR):
                if fname.startswith(sound):
                    path = os.path.join(SOUND_DIR, fname)
                    break
        except Exception as e:
            print(f"play_sound: 扫描失败 {e}")
            return

        if not path:
            print(f"play_sound: 未找到匹配 '{sound}' 的文件")
            return

        # 杀掉上一首 + 收割僵尸
        with self._player_lock:
            # 先收割所有已死的子进程
            prev = self._current_player
            if prev is not None:
                if prev.poll() is None:
                    prev.kill()
                try:
                    prev.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    prev.kill()  # 再杀一次确保
                self._current_player = None

            # 启动新歌 (独立进程组, 防止信号传播)
            try:
                cmd = self._player_cmd + [path]
                self._current_player = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,   # 独立会话, 不受 Flask 信号影响
                )
                print(f"play_sound: ▶ '{sound}' ({os.path.basename(path)})")
            except Exception as e:
                self._current_player = None
                print(f"play_sound: 播放失败 {e}")
