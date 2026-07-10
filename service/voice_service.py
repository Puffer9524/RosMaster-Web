"""
语音识别服务 — arecord 录音 + 科大讯飞 WebSocket API 转文字

对照 TonyPi CustomFunctions/STT_Control.py:
    - record_pcm()        → arecord 子进程录音
    - xunfei_transcribe() → 讯飞 WebSocket API 实时流式识别
    - 中文关键词 → 运动指令映射

交互方式:
    点「开始录音」→ 后台 arecord 开始录
    点「停止录音」→ 终止录音 → 讯飞识别 → 显示结果

硬件: card 2: XFM-DP-V0.0.18 USB 麦克风阵列
依赖: websocket-client (pip install websocket-client)
"""
import os
import json
import base64
import hashlib
import hmac
import subprocess
import threading
import time
import ssl
from datetime import datetime
from time import mktime
from wsgiref.handlers import format_date_time
from urllib.parse import urlencode
from queue import Queue, Empty
from typing import Optional, Callable

import websocket

# ── 讯飞 WebSocket 帧状态常量 (对照 STT_Control.py:57-59) ──
STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2

# ── 录音文件保存目录 ──
_PCM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recordings")


class VoiceService:
    """语音识别服务 (科大讯飞)

    使用方式:
        voice = VoiceService(alsa_device="plughw:2,0", motion_callback=callback)
        voice.start_recording()    # 开始录音
        voice.stop_and_transcribe()  # 停止录音并识别
        result = voice.get_result()
    """

    # ── 中文关键词 → 运动动作映射 (对照 STT_Control._xunfei_keywords) ──
    KEYWORD_MAP = {
        # 运动控制
        "往前走": ("forward", 5.0), "前进": ("forward", 5.0),
        "直走": ("forward", 5.0), "向前走": ("forward", 5.0),
        "往后退": ("backward", 5.0), "后退": ("backward", 5.0),
        "向后走": ("backward", 5.0), "倒车": ("backward", 5.0),
        "左转": ("turn_left", 5.0), "向左转": ("turn_left", 5.0),
        "往左转": ("turn_left", 5.0),
        "右转": ("turn_right", 5.0), "向右转": ("turn_right", 5.0),
        "往右转": ("turn_right", 5.0),
        "向左移": ("strafe_left", 5.0), "左移": ("strafe_left", 5.0),
        "往左移": ("strafe_left", 5.0),
        "向右移": ("strafe_right", 5.0), "右移": ("strafe_right", 5.0),
        "往右移": ("strafe_right", 5.0),
        "停止": ("stop", 0), "停": ("stop", 0),
        "停下": ("stop", 0), "刹车": ("stop", 0),
        "加速": ("speed_up", 0), "快点": ("speed_up", 0),
        "减速": ("speed_down", 0), "慢点": ("speed_down", 0),

        # 火情监测开关 (对照 app_sim2.py toggle_fire_check)
        "开启火情监测": ("fire_check_on", 0),
        "打开火情监测": ("fire_check_on", 0),
        "火情监测": ("fire_check_on", 0),
        "火焰检测": ("fire_check_on", 0),
        "开始火情监测": ("fire_check_on", 0),
        "关闭火情监测": ("fire_check_off", 0),
        "停止火情监测": ("fire_check_off", 0),
        "关火情监测": ("fire_check_off", 0),

        # 手势控制开关 (对照 app_sim2.py toggle_hand_ctrl)
        "开启手势控制": ("hand_ctrl_on", 0),
        "打开手势控制": ("hand_ctrl_on", 0),
        "手势控制": ("hand_ctrl_on", 0),
        "手势识别": ("hand_ctrl_on", 0),
        "开始手势控制": ("hand_ctrl_on", 0),
        "关闭手势控制": ("hand_ctrl_off", 0),
        "停止手势控制": ("hand_ctrl_off", 0),
        "关手势控制": ("hand_ctrl_off", 0),
    }

    def __init__(
        self,
        alsa_device: str = "plughw:2,0",
        xunfei_appid: str = "",
        xunfei_api_key: str = "",
        xunfei_api_secret: str = "",
        auto_exec: bool = True,
        motion_callback: Optional[Callable] = None,
        debug: bool = False,
    ):
        self._alsa_device = alsa_device
        self._xunfei_appid = xunfei_appid
        self._xunfei_api_key = xunfei_api_key
        self._xunfei_api_secret = xunfei_api_secret
        self._auto_exec = auto_exec
        self._motion_callback = motion_callback
        self._debug = debug

        # 录音状态
        self._recording = False
        self._recording_proc: Optional[subprocess.Popen] = None
        self._pcm_path: Optional[str] = None

        # 最新识别结果
        self._lock = threading.Lock()
        self._latest_text = ""
        self._latest_action = ""
        self._result_count = 0

        # 确保录音目录存在
        os.makedirs(_PCM_DIR, exist_ok=True)

    # ── 公开接口 ──

    def start_recording(self) -> bool:
        """开始录音 (后台 arecord 子进程)"""
        if self._recording:
            return True

        # 生成带时间戳的文件名，方便调试
        ts = time.strftime("%Y%m%d_%H%M%S")
        self._pcm_path = os.path.join(_PCM_DIR, f"voice_{ts}.pcm")

        cmd = [
            "arecord",
            "-D", self._alsa_device,
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1",
            "-t", "raw",
            self._pcm_path,
        ]

        if self._debug:
            print(f"[VoiceService] 开始录音 → {self._pcm_path}")
            print(f"[VoiceService] 命令: {' '.join(cmd)}")

        try:
            self._recording_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._recording = True
            return True
        except Exception as e:
            print(f"[VoiceService] arecord 启动失败: {e}")
            return False

    def stop_and_transcribe(self) -> dict:
        """停止录音 → 讯飞识别 → 返回结果

        Returns:
            {"text": "识别文字", "action": "匹配动作", "file": "录音路径"}
        """
        if self._recording and self._recording_proc:
            # 终止 arecord
            self._recording_proc.terminate()
            try:
                self._recording_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._recording_proc.kill()
            self._recording = False
            self._recording_proc = None

        if self._debug:
            print(f"[VoiceService] 录音已停止")

        # 检查文件
        if not self._pcm_path or not os.path.exists(self._pcm_path):
            return {
                "text": "",
                "action": "",
                "file": self._pcm_path or "",
                "error": "录音文件不存在",
            }

        file_size = os.path.getsize(self._pcm_path)
        actual_sec = file_size / (16000 * 2)
        if self._debug:
            print(f"[VoiceService] 录音文件: {self._pcm_path} "
                  f"({file_size} bytes, {actual_sec:.1f}秒)")

        if file_size < 1600:  # < 0.05 秒, 基本没声音
            return {
                "text": "",
                "action": "",
                "file": self._pcm_path,
                "error": f"录音过短 ({file_size} bytes)",
            }

        # 讯飞识别
        text = self._xunfei_transcribe(self._pcm_path)
        action = ""

        if text:
            action = self._match_keyword(text)

            if action and self._auto_exec and self._motion_callback:
                # 找 duration
                duration = 0
                for kw, (act, dur) in sorted(
                    self.KEYWORD_MAP.items(), key=lambda x: -len(x[0])
                ):
                    if kw in text and act == action:
                        duration = dur
                        break
                self._motion_callback(action, duration)

        with self._lock:
            self._latest_text = text
            self._latest_action = action
            self._result_count += 1

        return {
            "text": text,
            "action": action,
            "file": self._pcm_path,
        }

    def get_result(self) -> dict:
        """获取最新状态和结果"""
        with self._lock:
            return {
                "recording": self._recording,
                "text": self._latest_text,
                "action": self._latest_action,
                "count": self._result_count,
            }

    def is_recording(self) -> bool:
        return self._recording

    def stop(self) -> None:
        """清理: 如果还在录就先停止"""
        if self._recording:
            self.stop_and_transcribe()
        if self._debug:
            print("[VoiceService] 已停止")

    # ── 关键词匹配 (对照 STT_Control._match_xunfei_action) ──

    def _match_keyword(self, text: str) -> str:
        """匹配关键词, 返回动作名或空串"""
        for keyword, (action, _) in sorted(
            self.KEYWORD_MAP.items(), key=lambda x: -len(x[0])
        ):
            if keyword in text:
                if self._debug:
                    print(f"[VoiceService] 关键词 '{keyword}' → 动作 '{action}'")
                return action
        return ""

    # ══════════════════════════════════════════
    # 科大讯飞 WebSocket API (完全参照 TonyPi STT_Control.py)
    # ══════════════════════════════════════════

    def _create_xunfei_url(self) -> str:
        """生成讯飞 WebSocket 鉴权 URL"""
        url = 'wss://ws-api.xfyun.cn/v2/iat'
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))

        signature_origin = (
            "host: ws-api.xfyun.cn\n"
            "date: " + date + "\n"
            "GET /v2/iat HTTP/1.1"
        )
        signature_sha = hmac.new(
            self._xunfei_api_secret.encode('utf-8'),
            signature_origin.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        signature_sha = base64.b64encode(signature_sha).decode(encoding='utf-8')

        authorization_origin = (
            'api_key="%s", algorithm="%s", headers="%s", signature="%s"'
            % (self._xunfei_api_key, "hmac-sha256",
               "host date request-line", signature_sha)
        )
        authorization = base64.b64encode(
            authorization_origin.encode('utf-8')
        ).decode(encoding='utf-8')

        v = {
            "authorization": authorization,
            "date": date,
            "host": "ws-api.xfyun.cn",
        }
        return url + '?' + urlencode(v)

    def _xunfei_transcribe(self, pcm_path: str) -> str:
        """读取 PCM 文件，调用讯飞 API 转文字

        对照 STT_Control.py xunfei_transcribe() (行 204-343)
        """
        if not os.path.exists(pcm_path):
            print(f"[VoiceService] 文件不存在: {pcm_path}")
            return ""

        file_size = os.path.getsize(pcm_path)
        if self._debug:
            print(f"[VoiceService] 发送讯飞识别: {file_size} bytes")

        ws_url = self._create_xunfei_url()
        result_queue = Queue()

        def on_message(ws, message):
            try:
                resp = json.loads(message)
                code = resp.get("code", -1)
                if code != 0:
                    print(f"[VoiceService] 讯飞错误: "
                          f"{resp.get('message','')} (code={code})")
                    return
                data = resp.get("data", {})
                result_data = data.get("result", {})
                ws_data = result_data.get("ws", [])
                result = ""
                for seg in ws_data:
                    for w in seg.get("cw", []):
                        result += w.get("w", "")
                if result.strip():
                    result_queue.put(result)
            except Exception as e:
                print(f"[VoiceService] 解析响应异常: {e}")

        def on_error(ws, error):
            print(f"[VoiceService] WebSocket 错误: {error}")

        def on_close(ws, close_status_code, close_msg):
            pass

        def on_open(ws):
            def run():
                frame_size = 16000
                interval = 0.04
                status = STATUS_FIRST_FRAME

                with open(pcm_path, "rb") as fp:
                    while True:
                        buf = fp.read(frame_size)
                        if not buf:
                            status = STATUS_LAST_FRAME

                        if status == STATUS_FIRST_FRAME:
                            d = {
                                "common": {"app_id": self._xunfei_appid},
                                "business": {
                                    "domain": "iat",
                                    "language": "zh_cn",
                                    "accent": "mandarin",
                                    "vinfo": 1,
                                    "vad_eos": 10000,
                                },
                                "data": {
                                    "status": 0,
                                    "format": "audio/L16;rate=16000",
                                    "audio": str(base64.b64encode(buf), 'utf-8'),
                                    "encoding": "raw",
                                },
                            }
                            ws.send(json.dumps(d))
                            status = STATUS_CONTINUE_FRAME

                        elif status == STATUS_CONTINUE_FRAME:
                            d = {
                                "data": {
                                    "status": 1,
                                    "format": "audio/L16;rate=16000",
                                    "audio": str(base64.b64encode(buf), 'utf-8'),
                                    "encoding": "raw",
                                }
                            }
                            ws.send(json.dumps(d))

                        elif status == STATUS_LAST_FRAME:
                            d = {
                                "data": {
                                    "status": 2,
                                    "format": "audio/L16;rate=16000",
                                    "audio": str(base64.b64encode(buf), 'utf-8'),
                                    "encoding": "raw",
                                }
                            }
                            ws.send(json.dumps(d))
                            time.sleep(1)
                            break

                        time.sleep(interval)

                ws.close()

            thread = threading.Thread(target=run)
            thread.start()

        websocket.enableTrace(False)
        ws = websocket.WebSocketApp(
            ws_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws.on_open = on_open
        ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

        try:
            result = result_queue.get(timeout=30)
        except Empty:
            result = ""

        result = result.strip()
        if result and self._debug:
            print(f"[VoiceService] 讯飞识别: '{result}'")
        return result
