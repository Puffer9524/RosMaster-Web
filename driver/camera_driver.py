"""
摄像头驱动 — OpenCV 封装 (匹配 app_sim2.py)

对照 app_sim2.py:
    self.cap = cv2.VideoCapture(0)
    ret, frame = self.cap.read()

Jetson 深度摄像头走 GStreamer/Argus pipeline, 只用整数索引 0/1/2,
不要传设备路径 (/dev/videoX), 否则 V4L2 和 Argus 冲突导致 segfault.

帧处理器 (Frame Processor) 机制:
    对照 app_sim2.py update_camera_frame() 中的火情检测 + 手势识别:
        if self.fire_check.get() == 1: ...   # 处理每一帧
        if self.hand_ctrl.get() == 1: ...    # 处理每一帧
    外部服务通过 add_frame_processor / remove_frame_processor 注册回调,
    回调签名: proc(frame, frame_count) -> frame (可修改并返回新帧)
"""
import threading
import time
import cv2

# ── 帧处理器注册表 (对照 app_sim2.py 摄像头循环内的火情/手势处理) ──
_frame_processors = []
_processor_lock = threading.Lock()


def add_frame_processor(proc):
    """注册帧处理器 (对照 app_sim2.py update_camera_frame 中的检测逻辑)

    Args:
        proc: callable(frame, frame_count) -> frame
              接收 BGR numpy 数组, 返回处理后的帧 (可以为原帧或新帧)
    """
    with _processor_lock:
        if proc not in _frame_processors:
            _frame_processors.append(proc)


def remove_frame_processor(proc):
    """移除帧处理器"""
    with _processor_lock:
        if proc in _frame_processors:
            _frame_processors.remove(proc)


def process_frame(frame, frame_count: int = 0):
    """运行所有已注册的帧处理器

    对照 app_sim2.py update_camera_frame() 循环:
        if self.fire_check.get() == 1: ...
        if self.hand_ctrl.get() == 1: ...

    Args:
        frame: BGR numpy 数组
        frame_count: 当前帧序号 (用于跳帧)
    Returns:
        处理后的帧
    """
    with _processor_lock:
        procs = list(_frame_processors)
    for proc in procs:
        try:
            result = proc(frame, frame_count)
            if result is not None:
                frame = result
        except Exception:
            pass
    return frame


class CameraDriver:
    """OpenCV 摄像头封装 (线程安全)

    对照 app_sim2.py:
        self.cap = cv2.VideoCapture(0)
        ret, frame = self.cap.read()
    """

    def __init__(self, video_id: int = 0, width: int = 640, height: int = 480,
                 debug: bool = False):
        self._video_id = video_id
        self._width = width
        self._height = height
        self._debug = debug
        self._cap = None
        self._state = False
        self._read_lock = threading.Lock()
        self._open()

    def _open(self) -> bool:
        """打开摄像头"""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

        # 只用整数索引, 让 OpenCV 选择后端 (Jetson 上走 GStreamer, 普通 Linux 走 V4L2)
        self._cap = cv2.VideoCapture(self._video_id)
        if not self._cap.isOpened():
            # 重试一次
            time.sleep(0.5)
            self._cap.open(self._video_id)

        if not self._cap.isOpened():
            if self._debug:
                print(f"Camera init error! id={self._video_id}")
            self._state = False
            return False

        # Jetson GStreamer 摄像头不要设 FOURCC, 只设分辨率
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)

        self._state = True
        if self._debug:
            print(f"Camera {self._video_id} OK")
        return True

    def is_opened(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def get_frame(self):
        """读取一帧 (线程安全)"""
        with self._read_lock:
            if not self.is_opened():
                return False, None
            success, frame = self._cap.read()
            return success, frame

    def get_jpeg(self, quality: int = 65):
        success, frame = self.get_frame()
        if not success:
            return False, None
        _, jpeg = cv2.imencode('.jpg', frame,
                               [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return True, jpeg.tobytes()

    def reconnect(self) -> bool:
        if self._debug:
            print("Camera reconnecting...")
        self.release()
        time.sleep(1)
        return self._open()

    def release(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._state = False
