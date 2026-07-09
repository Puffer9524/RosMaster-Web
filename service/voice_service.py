"""
语音识别服务 — arecord 录音 + 科大讯飞 WebSocket API 转文字

对照 TonyPi CustomFunctions/STT_Control.py:
    - record_to_wav()   → arecord 子进程录音
    - xunfei_transcribe() → 讯飞 WebSocket API 实时流式识别
    - 中文关键词 → 运动指令映射

硬件: card 2: XFM-DP-V0.0.18 USB 麦克风阵列
依赖: websocket-client (pip install websocket-client)
"""
import os
import sys
import time
import json
import base64
import hashlib
import hmac
import struct
import subprocess
import threading
import tempfile
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


class VoiceService:
    """语音识别服务 (科大讯飞)

    对照 TonyPi STT_Control 的架构:
        arecord → 原始 PCM 文件 → 讯飞 WebSocket → 中文文本 → 关键词匹配 → 运动

    使用方式:
        voice = VoiceService(alsa_device="plughw:2,0", motion_callback=callback)
        voice.start()                  # 启动后台线程
        voice.listen_once()            # 单次: 录音 → 识别 → 执行
        result = voice.get_result()    # 获取最新结果
        voice.stop()
    """

    # ── 中文关键词 → 运动动作 映射表 (对照 STT_Control._xunfei_keywords) ──
    KEYWORD_MAP = {
        # 前进
        "往前走": ("forward", 0.5), "前进": ("forward", 0.5),
        "直走": ("forward", 0.5), "向前走": ("forward", 0.5),
        # 后退
        "往后退": ("backward", 0.5), "后退": ("backward", 0.5),
        "向后走": ("backward", 0.5), "倒车": ("backward", 0.5),
        # 左转
        "左转": ("turn_left", 1.0), "向左转": ("turn_left", 1.0),
        "往左转": ("turn_left", 1.0),
        # 右转
        "右转": ("turn_right", 1.0), "向右转": ("turn_right", 1.0),
        "往右转": ("turn_right", 1.0),
        # 左移 (麦克纳姆轮)
        "向左移": ("strafe_left", 0.5), "左移": ("strafe_left", 0.5),
        "往左移": ("strafe_left", 0.5),
        # 右移
        "向右移": ("strafe_right", 0.5), "右移": ("strafe_right", 0.5),
        "往右移": ("strafe_right", 0.5),
        # 停止
        "停止": ("stop", 0), "停": ("stop", 0),
        "停下": ("stop", 0), "刹车": ("stop", 0),
        # 加速 / 减速
        "加速": ("speed_up", 0), "快点": ("speed_up", 0),
        "减速": ("speed_down", 0), "慢点": ("speed_down", 0),
    }

    def __init__(
        self,
        alsa_device: str = "plughw:2,0",
        record_seconds: float = 5.0,
        xunfei_appid: str = "",
        xunfei_api_key: str = "",
        xunfei_api_secret: str = "",
        auto_exec: bool = True,
        motion_callback: Optional[Callable] = None,
        debug: bool = False,
    ):
        self._alsa_device = alsa_device
        self._record_seconds = record_seconds
        self._xunfei_appid = xunfei_appid
        self._xunfei_api_key = xunfei_api_key
        self._xunfei_api_secret = xunfei_api_secret
        self._auto_exec = auto_exec
        self._motion_callback = motion_callback
        self._debug = debug

        self._running = False
        self._enabled = False
        self._busy = False              # 正在识别中，防止重复触发
        self._thread: Optional[threading.Thread] = None

        # 最新结果
        self._lock = threading.Lock()
        self._latest_text = ""
        self._latest_action = ""
        self._result_count = 0

    # ── 公开接口 ──

    def start(self) -> None:
        """启动后台监听循环"""
        if self._running:
            return
        self._running = True
        self._enabled = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        if self._debug:
            print(f"[VoiceService] 启动, device={self._alsa_device}, "
                  f"duration={self._record_seconds}s")

    def stop(self) -> None:
        """停止"""
        self._enabled = False
        self._running = False
        if self._debug:
            print("[VoiceService] 已停止")

    def is_listening(self) -> bool:
        return self._enabled and self._running

    def get_result(self) -> dict:
        """获取最新识别结果"""
        with self._lock:
            return {
                "listening": self._enabled,
                "busy": self._busy,
                "text": self._latest_text,
                "action": self._latest_action,
                "count": self._result_count,
            }

    def listen_once(self) -> Optional[str]:
        """同步: 录音 → 识别 → 匹配 → 执行 (一次性, 供外部手动调用)"""
        self._busy = True
        try:
            text = self._record_and_recognize()
            if text and self._debug:
                print(f"[VoiceService] 识别: '{text}'")
            if text:
                self._match_and_execute(text)
            return text
        finally:
            self._busy = False

    # ── 后台监听循环 ──

    def _listen_loop(self) -> None:
        """后台循环: 持续监听 → 识别 → 执行 (对照 TonyPi 的持续监听模式)"""
        while self._running:
            if not self._enabled:
                time.sleep(0.2)
                continue

            self._busy = True
            try:
                text = self._record_and_recognize()
                if text:
                    self._match_and_execute(text)
            except Exception as e:
                print(f"[VoiceService] 循环异常: {e}")
            finally:
                self._busy = False

            # 两次识别之间短暂停顿
            time.sleep(0.3)

    # ── 关键词匹配 & 执行 ──

    def _match_and_execute(self, text: str) -> None:
        """匹配关键词 → 回调执行 (对照 STT_Control._match_xunfei_action)"""
        text = text.strip()
        matched_action = None
        matched_duration = 0

        # 按关键词长度降序排列, 优先匹配长词
        for keyword, (action, duration) in sorted(
            self.KEYWORD_MAP.items(), key=lambda x: -len(x[0])
        ):
            if keyword in text:
                matched_action = action
                matched_duration = duration
                if self._debug:
                    print(f"[VoiceService] 关键词 '{keyword}' → 动作 '{action}'")
                break

        with self._lock:
            self._latest_text = text
            self._result_count += 1
            self._latest_action = matched_action or ""

        if matched_action and self._auto_exec and self._motion_callback:
            self._motion_callback(matched_action, matched_duration)

    # ══════════════════════════════════════════
    # 科大讯飞 WebSocket API (完全参照 TonyPi STT_Control.py)
    # ══════════════════════════════════════════

    def _create_xunfei_url(self) -> str:
        """生成讯飞 WebSocket 鉴权 URL

        对照 STT_Control.py _create_xunfei_url() (行 183-202):
            HMAC-SHA256 签名 → Base64 → WebSocket URL
        """
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

    def xunfei_transcribe(self, wav_path: str) -> str:
        """读取 PCM 文件，调用讯飞 API 转文字

        对照 STT_Control.py xunfei_transcribe() (行 204-343):
            逐帧读取 PCM → Base64 → WebSocket → JSON 解析 → 拼接文本
        """
        if not os.path.exists(wav_path):
            print(f"[VoiceService] 文件不存在: {wav_path}")
            return ""

        file_size = os.path.getsize(wav_path)
        if file_size < 100:
            print(f"[VoiceService] 音频文件过小 ({file_size} bytes)")
            return ""

        ws_url = self._create_xunfei_url()
        result_queue = Queue()

        # ── on_message: 解析讯飞 JSON 响应 ──
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

                with open(wav_path, "rb") as fp:
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

    # ══════════════════════════════════════════
    # 录音 (对照 TonyPi STT_Control.record_to_wav)
    # ══════════════════════════════════════════

    def record_pcm(self, save_path: Optional[str] = None,
                   duration: Optional[float] = None) -> Optional[str]:
        """arecord 录音 → 原始 PCM

        对照 STT_Control.py record_to_wav() (行 349-395):
            arecord -D plughw:2,0 -d 5 -f S16_LE -r 16000 -c 1 -t raw
        """
        if save_path is None:
            save_path = tempfile.mktemp(suffix=".pcm")
        if duration is None:
            duration = self._record_seconds

        if self._debug:
            print(f"[VoiceService] 录音中 ({duration}秒)...")

        cmd = [
            "arecord",
            "-D", self._alsa_device,
            "-d", str(int(duration)),
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1",
            "-t", "raw",
            save_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"[VoiceService] arecord 错误: {result.stderr.strip()}")
            try:
                os.unlink(save_path)
            except OSError:
                pass
            return None

        file_size = os.path.getsize(save_path)
        if file_size < 100:
            print(f"[VoiceService] 录音过小 ({file_size} bytes), 可能没收到声音")
            try:
                os.unlink(save_path)
            except OSError:
                pass
            return None

        if self._debug:
            actual_sec = file_size / (16000 * 2)
            print(f"[VoiceService] 录音完成 ({file_size} bytes, {actual_sec:.1f}秒)")

        return save_path

    def _record_and_recognize(self) -> str:
        """完整流程: 录音 → 讯飞识别 → 返回文字"""
        pcm_path = self.record_pcm()
        if pcm_path is None:
            return ""

        text = self.xunfei_transcribe(pcm_path)

        # 清理临时文件
        try:
            os.unlink(pcm_path)
        except OSError:
            pass

        return text
