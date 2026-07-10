"""
火情监测服务 — AI 火焰检测 (对照 app_sim2.py)

对照 app_sim2.py:
    toggle_fire_check():
        if self.fire_check.get() == 1:
            self.playSound("fire_check_open")
        else:
            self.playSound("fire_check_close")

    update_camera_frame() 循环中的火情检测:
        if self.fire_check.get() == 1:
            if self.aisock is None:
                # 连接 AI 服务器 127.0.0.1:12345
            reply = self.send_frame(frame, self.aisock)
            if reply is not None:
                result = reply.decode('utf-8')
                if result != "0":
                    # 解析: x,y,w,h,id,time
                    self.playSound("fire_alarm")
                    self.__event_id = 1
                    # 画框 + 文字

通信协议:
    客户端 → 服务器: 16字节大端长度头 + JPEG 数据
    服务器 → 客户端: "0" (无事件) 或 "x,y,w,h,id,time;..." (检测结果)
"""
import socket
import threading
import time
import cv2
import numpy as np


class FireService:
    """火情监测服务

    对照 app_sim2.py RobotControlApp 火情监测部分:
        - fire_check (tk.IntVar)
        - aisock (TCP socket to AI server)
        - __event_id, __event_time, __event_box
        - send_frame()
        - playSound("fire_alarm")
    """

    # 火情检测 AI 服务器地址 (对照 app_sim2.py: server_address = ('127.0.0.1', 12345))
    AI_SERVER_HOST = "127.0.0.1"
    AI_SERVER_PORT = 12345

    # 跳帧: 每 N 帧发送一次到 AI 服务器 (减轻网络负担)
    FRAME_SKIP = 3

    def __init__(self, sound_callback=None, debug: bool = False):
        """
        Args:
            sound_callback: 播放音效的回调 (对照 app_sim2.py playSound)
                           签名: sound_callback(sound_name: str) -> None
            debug: 是否打印调试信息
        """
        self._sound_callback = sound_callback
        self._debug = debug

        # 功能开关 (对照 app_sim2.py self.fire_check)
        self._enabled = False

        # AI 服务器连接 (对照 app_sim2.py self.aisock)
        self._aisock = None
        self._sock_lock = threading.Lock()

        # 检测事件状态 (对照 app_sim2.py __event_id/__event_time/__event_box)
        self._event_id = 0
        self._event_time = 0.0
        self._event_box = []  # [x1, y1, x2, y2]

        # 最新检测告警 (供前端轮询)
        self._lock = threading.Lock()
        self._latest_alert = None  # {"x": ..., "y": ..., "w": ..., "h": ..., "time": ...}

        # 帧计数 (用于跳帧)
        self._frame_count = 0

        print(f"[FireService] ✓ 初始化完成 (AI服务器 {self.AI_SERVER_HOST}:{self.AI_SERVER_PORT})")

    # ── 公开接口 ──

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        """开启火情监测

        对照 app_sim2.py toggle_fire_check():
            if self.fire_check.get() == 1:
                print("start fire check ...")
                self.playSound("fire_check_open")
        """
        if self._enabled:
            return
        self._enabled = True
        self._frame_count = 0
        if self._debug:
            print("[FireService] 火情监测已开启")
        self._play_sound("fire_check_open")

    def stop(self) -> None:
        """关闭火情监测

        对照 app_sim2.py toggle_fire_check():
            else:
                print("stop fire check ...")
                self.playSound("fire_check_close")
        """
        if not self._enabled:
            return
        self._enabled = False
        self._disconnect()
        # 清空事件
        with self._lock:
            self._event_id = 0
            self._event_time = 0.0
            self._event_box = []
            self._latest_alert = None
        if self._debug:
            print("[FireService] 火情监测已关闭")
        self._play_sound("fire_check_close")

    def get_latest_alert(self) -> dict:
        """获取最新火情告警 (供前端轮询)

        Returns:
            None 或 {"x": int, "y": int, "w": int, "h": int, "time": float}
        """
        with self._lock:
            alert = self._latest_alert
            # 只返回一次，返回后清空 (避免重复告警)
            self._latest_alert = None
            return alert

    # ── 帧处理器 (注册到 camera_driver.process_frame) ──

    def process_frame(self, frame, frame_count: int = 0):
        """帧处理回调 (对照 app_sim2.py update_camera_frame 中的火情检测部分)

        由 camera_driver.process_frame() 在视频流生成器中调用。
        每 FRAME_SKIP 帧发送一次到 AI 服务器。

        Args:
            frame: BGR numpy 数组
            frame_count: 全局帧序号
        Returns:
            处理后的帧 (可能绘制了火焰边界框)
        """
        if not self._enabled:
            return frame

        self._frame_count = frame_count

        # 跳帧: 每 FRAME_SKIP 帧处理一次
        if frame_count % self.FRAME_SKIP != 0:
            # 如果最近检测到火焰 (0.5s 内), 继续画框
            now = time.time()
            with self._lock:
                eid = self._event_id
                etime = self._event_time
            if eid > 0 and now - etime < 0.5:
                return self._draw_fire_box(frame)
            return frame

        now = time.time()

        # 如果刚刚检测到火焰 (0.5s 内), 继续显示上次的框, 不发送新帧
        with self._lock:
            eid = self._event_id
            etime = self._event_time
        if eid > 0 and now - etime < 0.5:
            return self._draw_fire_box(frame)

        # 连接 AI 服务器
        if self._aisock is None:
            self._connect()

        # 单次读取, 避免竞态 (stop() 可能在另一个线程中关闭连接)
        sock = self._aisock
        if sock is None:
            return frame  # 连接失败, 跳过

        # 发送帧到 AI 服务器
        reply = self._send_frame(frame, sock)
        if reply is None:
            # 连接断开
            self._disconnect()
            with self._lock:
                self._event_id = 0
                self._event_time = 0.0
                self._event_box = []
            return frame

        # 解析结果
        result = reply.decode("utf-8", errors="ignore").strip()
        if self._debug:
            print(f"[FireService] AI回复: '{result}'")

        if result == "0":
            # 无事件
            return frame

        # 有检测结果, 解析
        # 格式: "x,y,w,h,id,time;..." (分号分隔多个检测)
        tmp_arr = result.split(";")
        if len(tmp_arr) > 0:
            elem = tmp_arr[0].split(",")
            if len(elem) >= 6:
                x = int(elem[0])
                y = int(elem[1])
                w = int(elem[2])
                h = int(elem[3])
                event_time = float(elem[5])
                x1 = int(x - w / 2.0)
                y1 = int(y - h / 2.0)
                x2 = int(x + w / 2.0)
                y2 = int(y + h / 2.0)

                with self._lock:
                    self._event_id = 1
                    self._event_time = event_time
                    self._event_box = [x1, y1, x2, y2]
                    self._latest_alert = {
                        "x": x, "y": y, "w": w, "h": h,
                        "time": event_time,
                    }

                # 播放火情告警音效
                self._play_sound("fire_alarm")

                if self._debug:
                    print(f"[FireService] 🔥 检测到火焰! "
                          f"x={x} y={y} w={w} h={h}")

                # 在帧上绘制边界框
                return self._draw_fire_box(frame)

        return frame

    def _draw_fire_box(self, frame):
        """在帧上绘制火焰边界框

        对照 app_sim2.py:
            cv2.rectangle(frame, (x1,y1), (x2,y2), (0,0,255), 2)
            cv2.putText(frame, "fire", (x,y), FONT_HERSHEY_TRIPLEX, 1, (0,255,0), 1)
        """
        with self._lock:
            box = list(self._event_box) if self._event_box else []
        if not box or len(box) < 4:
            return frame
        x1, y1, x2, y2 = box
        # 边界检查
        h, w = frame.shape[:2]
        x1 = max(0, min(x1, w - 1))
        y1 = max(0, min(y1, h - 1))
        x2 = max(0, min(x2, w - 1))
        y2 = max(0, min(y2, h - 1))
        # 红色矩形框
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
        # 标签
        label_x = max(0, x1)
        label_y = max(20, y1 - 8)
        cv2.putText(frame, "FIRE", (label_x, label_y),
                    cv2.FONT_HERSHEY_TRIPLEX, 1, (0, 255, 0), 2)
        return frame

    # ── AI 服务器通信 (对照 app_sim2.py send_frame) ──

    def _connect(self) -> None:
        """连接 AI 火情检测服务器

        对照 app_sim2.py:
            self.aisock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.aisock.settimeout(3)
            server_address = ('127.0.0.1', 12345)
            self.aisock.connect(server_address)
        """
        with self._sock_lock:
            if self._aisock is not None:
                return
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                sock.connect((self.AI_SERVER_HOST, self.AI_SERVER_PORT))
                self._aisock = sock
                if self._debug:
                    print(f"[FireService] 已连接 AI 服务器 "
                          f"{self.AI_SERVER_HOST}:{self.AI_SERVER_PORT}")
            except socket.timeout:
                self._aisock = None
                print("[FireService] 连接 AI 服务器超时 (127.0.0.1:12345)")
            except ConnectionRefusedError:
                self._aisock = None
                print("[FireService] 连接被拒绝, AI 服务器 (127.0.0.1:12345) 未启动")
            except Exception as e:
                self._aisock = None
                print(f"[FireService] 连接错误: {e}")

    def _disconnect(self) -> None:
        """断开 AI 服务器连接"""
        with self._sock_lock:
            if self._aisock is not None:
                try:
                    self._aisock.close()
                except Exception:
                    pass
                self._aisock = None

    def _send_frame(self, frame, sock) -> bytes:
        """发送帧到 AI 服务器并接收结果

        对照 app_sim2.py send_frame():
            _, encoded_image = cv2.imencode('.jpg', frame, [quality, 80])
            data = np.array(encoded_image).tobytes()
            s.sendall(len(data).to_bytes(16, 'big'))
            s.sendall(data)
            reply = s.recv(128)

        Args:
            frame: BGR numpy 数组
            sock: TCP socket
        Returns:
            服务器回复的 bytes, 或 None (错误)
        """
        try:
            # 编码为 JPEG
            _, encoded = cv2.imencode(".jpg", frame,
                                      [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            data = np.array(encoded).tobytes()

            # 发送: 16字节大端长度头 + JPEG 数据
            sock.sendall(len(data).to_bytes(16, "big"))
            sock.sendall(data)

            # 接收回复
            reply = sock.recv(128)
            return reply

        except socket.timeout:
            print("[FireService] 等待 AI 服务器回复超时 (非致命)")
            return None
        except Exception as e:
            print(f"[FireService] ✗ 发送帧异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    # ── 内部工具 ──

    def _play_sound(self, name: str) -> None:
        """播放音效 (对照 app_sim2.py playSound)

        Args:
            name: 音效标识, 如 "fire_check_open", "fire_alarm" 等
        """
        if self._sound_callback:
            try:
                self._sound_callback(name)
            except Exception as e:
                if self._debug:
                    print(f"[FireService] 播放音效失败: {e}")
