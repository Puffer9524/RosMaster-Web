"""
手势控制服务 (对照 app_sim2.py)

对照 app_sim2.py:
    toggle_hand_ctrl():
        if self.hand_ctrl.get() == 1:
            print("start hand control...")
            self.playSound("hand_ctrl_open")
        else:
            print("stop hand control...")
            self.playSound("hand_ctrl_close")

    update_camera_frame() 循环中的手势识别:
        self.handGestureDetector = HandGestureDetector()
        self.handGestureDetector.setLogger(print)

        hand_ctrls = [
            ("0",         "car_forward",      [sp/300, 0., 0.],          ...),  # 前进
            ("Spider-Man","car_backward",     [-sp/300, 0., 0.],         ...),  # 后退
            ("1",         "car_shift_right",  [0., -sp/300, 0.],         ...),  # 右平移
            ("2",         "car_shift_left",   [0., sp/300, 0.],          ...),  # 左平移
            ("3",         "car_turn_right",   [0., 0., -sp*3.2/400.],    ...),  # 右转
            ("4",         "car_turn_left",    [0., 0., sp*3.2/400.],     ...),  # 左转
            ("5",         "car_stop",         [0., 0., 0.],              ...),  # 停止
        ]

        if self.hand_ctrl.get() == 1:
            frame, event = self.handGestureDetector.detect(frame)
            # 连续 5 帧相同手势 + 1秒冷却 → 执行命令

注意:
    HandGestureDetector 是编译好的 ARM64 .so 文件 (handGesture.cpython-38-aarch64-linux-gnu.so),
    仅在 Jetson (aarch64 Linux, Python 3.8) 上可用。
    在其他平台上, 手势控制服务会以降级模式运行 (不执行检测).
"""
import threading
import time

# ── 尝试导入 HandGestureDetector (仅 ARM64 Linux / Python 3.8 可用) ──
try:
    from handGesture import HandGestureDetector
    _HAS_GESTURE_DETECTOR = True
except ImportError:
    HandGestureDetector = None
    _HAS_GESTURE_DETECTOR = False


class GestureService:
    """手势控制服务

    对照 app_sim2.py RobotControlApp 手势控制部分:
        - hand_ctrl (tk.IntVar)
        - handGestureDetector (HandGestureDetector)
        - hand_ctrls 手势→运动映射表
        - 连续 5 帧确认 + 1 秒冷却机制
    """

    # 手势 → (声音, 运动命令) 映射 (对照 app_sim2.py hand_ctrls)
    # 速度由外部 motion_callback 按当前 speed 百分比计算
    GESTURE_MAP = {
        "0":          ("car_forward",      "forward"),
        "Spider-Man": ("car_backward",     "backward"),
        "1":          ("car_shift_right",  "strafe_right"),
        "2":          ("car_shift_left",   "strafe_left"),
        "3":          ("car_turn_right",   "turn_right"),
        "4":          ("car_turn_left",    "turn_left"),
        "5":          ("car_stop",         "stop"),
    }

    # 连续帧确认阈值 (对照 app_sim2.py: gesture_cnt >= 5)
    GESTURE_CONFIRM_COUNT = 5

    # 动作冷却时间 [秒] (对照 app_sim2.py: now - last_gesture_time > 1)
    ACTION_COOLDOWN = 1.0

    # 跳帧: 每 N 帧检测一次手势 (节省 CPU)
    FRAME_SKIP = 2

    def __init__(self, motion_callback=None, sound_callback=None,
                 debug: bool = False):
        """
        Args:
            motion_callback: 运动执行回调
                            签名: motion_callback(action: str) -> None
                            action ∈ {"forward","backward","turn_left","turn_right",
                                       "strafe_left","strafe_right","stop"}
            sound_callback: 播放音效的回调 (对照 app_sim2.py playSound)
                            签名: sound_callback(sound_name: str) -> None
            debug: 是否打印调试信息
        """
        self._motion_callback = motion_callback
        self._sound_callback = sound_callback
        self._debug = debug

        # 功能开关 (对照 app_sim2.py self.hand_ctrl)
        self._enabled = False

        # 手势检测器 (仅 ARM64 平台可用)
        self._detector = None
        if _HAS_GESTURE_DETECTOR:
            try:
                self._detector = HandGestureDetector()
                self._detector.setLogger(print if debug else lambda _: None)
                if self._debug:
                    print("[GestureService] HandGestureDetector 初始化成功")
            except Exception as e:
                if self._debug:
                    print(f"[GestureService] HandGestureDetector 初始化失败: {e}")
                self._detector = None
        else:
            if self._debug:
                print("[GestureService] HandGestureDetector 不可用 (非 ARM64 平台), "
                      "手势控制以降级模式运行")

        # 手势识别状态 (对照 app_sim2.py update_camera_frame 局部变量)
        self._lock = threading.RLock()  # 可重入锁 (process_frame 和 _execute_gesture 嵌套使用)
        self._last_gesture = "Unknown"
        self._gesture_count = 0
        self._last_action_time = 0.0

        # 最新手势 (供前端轮询)
        self._latest_gesture = ""
        self._latest_action = ""

        # 帧计数
        self._frame_count = 0

    # ── 公开接口 ──

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def has_detector(self) -> bool:
        """是否加载了手势检测器"""
        return self._detector is not None

    def start(self) -> None:
        """开启手势控制

        对照 app_sim2.py toggle_hand_ctrl():
            if self.hand_ctrl.get() == 1:
                print("start hand control...")
                self.playSound("hand_ctrl_open")
        """
        if self._enabled:
            return
        self._enabled = True
        self._frame_count = 0
        with self._lock:
            self._last_gesture = "Unknown"
            self._gesture_count = 0
            self._last_action_time = 0.0
            self._latest_gesture = ""
            self._latest_action = ""
        if self._debug:
            print("[GestureService] 手势控制已开启"
                  + (" (降级模式: 无检测器)" if not self._detector else ""))
        self._play_sound("hand_ctrl_open")

    def stop(self) -> None:
        """关闭手势控制

        对照 app_sim2.py toggle_hand_ctrl():
            else:
                print("stop hand control...")
                self.playSound("hand_ctrl_close")
        """
        if not self._enabled:
            return
        self._enabled = False
        # 停车
        if self._motion_callback:
            try:
                self._motion_callback("stop")
            except Exception:
                pass
        if self._debug:
            print("[GestureService] 手势控制已关闭")
        self._play_sound("hand_ctrl_close")

    def get_status(self) -> dict:
        """获取当前手势状态 (供前端轮询)

        Returns:
            {"enabled": bool, "gesture": str, "action": str, "has_detector": bool}
        """
        with self._lock:
            return {
                "enabled": self._enabled,
                "gesture": self._latest_gesture,
                "action": self._latest_action,
                "has_detector": self.has_detector,
            }

    # ── 帧处理器 (注册到 camera_driver.process_frame) ──

    def process_frame(self, frame, frame_count: int = 0):
        """帧处理回调 (对照 app_sim2.py update_camera_frame 中的手势识别部分)

        由 camera_driver.process_frame() 在视频流生成器中调用。

        Args:
            frame: BGR numpy 数组
            frame_count: 全局帧序号
        Returns:
            处理后的帧 (可能绘制了手部关键点)
        """
        if not self._enabled:
            return frame

        if self._detector is None:
            return frame  # 无检测器, 降级模式

        self._frame_count = frame_count

        # 跳帧: 每 FRAME_SKIP 帧检测一次
        if frame_count % self.FRAME_SKIP != 0:
            return frame

        now = time.time()

        # 手势检测 (对照 app_sim2.py: frame, event = self.handGestureDetector.detect(frame))
        try:
            frame, event = self._detector.detect(frame)
        except Exception as e:
            if self._debug:
                print(f"[GestureService] 手势检测异常: {e}")
            return frame

        # 手势状态机 (对照 app_sim2.py 手势确认逻辑)
        with self._lock:
            if event != self._last_gesture:
                # 手势变化, 重置计数
                self._last_gesture = event
                self._gesture_count = 0
            else:
                self._gesture_count += 1

            # 连续 N 帧相同 + 冷却时间 → 执行动作
            if (self._gesture_count >= self.GESTURE_CONFIRM_COUNT
                    and now - self._last_action_time > self.ACTION_COOLDOWN):
                self._execute_gesture(event)
                self._last_action_time = now

            self._latest_gesture = event

        return frame

    # ── 手势执行 (对照 app_sim2.py 手势→命令映射) ──

    def _execute_gesture(self, gesture: str) -> None:
        """执行手势对应的动作

        对照 app_sim2.py:
            for gesture, voice, base_command1, x, y in hand_ctrls:
                if gesture == event:
                    self.playSound(voice)
                    last_gesture_time = now
                    command = base_command1
                    if text != "5":
                        self.execute_command_with_duration(command)
                    else:
                        self.execute_command(command)
        """
        if gesture not in self.GESTURE_MAP:
            if self._debug:
                print(f"[GestureService] 未映射的手势: '{gesture}'")
            return

        sound_name, action = self.GESTURE_MAP[gesture]

        if self._debug:
            print(f"[GestureService] 手势 '{gesture}' → 播放 '{sound_name}' → 动作 '{action}'")

        # 播放音效
        self._play_sound(sound_name)

        # 执行运动
        if self._motion_callback:
            try:
                self._motion_callback(action)
            except Exception as e:
                if self._debug:
                    print(f"[GestureService] 运动回调失败: {e}")

        with self._lock:
            self._latest_action = action

    # ── 内部工具 ──

    def _play_sound(self, name: str) -> None:
        """播放音效 (对照 app_sim2.py playSound)"""
        if self._sound_callback:
            try:
                self._sound_callback(name)
            except Exception as e:
                if self._debug:
                    print(f"[GestureService] 播放音效失败: {e}")
