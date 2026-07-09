"""
TTS 驱动 — 科大讯飞 TTS WebSocket API → PCM 音频 → WAV → aplay 播放

对照 TonyPi CustomFunctions/TTS_Control.py (讯飞模式):
    - _build_ws_url()     → HMAC-SHA256 鉴权 URL
    - _speak_xunfei()     → WebSocket 发送文本 → 接收 PCM 流
    - _write_wav()        → 写入 44 字节 WAV 头
    - _play()             → aplay 自动探测播放设备

API: wss://tts-api.xfyun.cn/v2/tts
依赖: websocket-client
"""
import os
import json
import base64
import hashlib
import hmac
import struct
import subprocess
import threading
import time
import ssl
from datetime import datetime
from time import mktime
from wsgiref.handlers import format_date_time
from urllib.parse import urlencode
from typing import Optional

import websocket


class TTSDriver:
    """科大讯飞 TTS 驱动 (云端合成 → aplay 播放)"""

    # aplay 自动探测设备优先级列表 (对照 TonyPi TTS_Control._play)
    _PLAY_DEVICES = [
        None, "default", "sysdefault",
        "plughw:0,0", "plughw:1,0", "plughw:2,0",
        "hw:0,0", "hw:1,0", "hw:2,0",
    ]

    def __init__(
        self,
        appid: str = "",
        api_key: str = "",
        api_secret: str = "",
        voice: str = "x4_yezi",
        speed: int = 50,
        volume: int = 100,
        play_device: str = "",
        debug: bool = False,
    ):
        self._appid = appid
        self._api_key = api_key
        self._api_secret = api_secret
        self._voice = voice
        self._speed = speed
        self._volume = volume
        self._play_device = play_device
        self._debug = debug

    # ── 公开接口 ──

    def speak(self, text: str) -> bool:
        """合成并播放文字

        Returns: True 成功, False 失败
        """
        if not text or not text.strip():
            return False

        if self._debug:
            print(f"[TTS] 合成: '{text[:50]}{'...' if len(text)>50 else ''}'")

        # 1. 调用讯飞 TTS API → PCM 音频数据
        pcm_data = self._synthesize(text)
        if not pcm_data:
            return False

        # 2. PCM → WAV → 写入临时文件
        wav_path = self._save_wav(pcm_data)
        if not wav_path:
            return False

        # 3. aplay 播放
        ok = self._play(wav_path)

        # 4. 清理临时文件
        try:
            os.unlink(wav_path)
        except OSError:
            pass

        return ok

    # ══════════════════════════════════════════
    # 讯飞 TTS WebSocket API (对照 TonyPi TTS_Control._speak_xunfei)
    # ══════════════════════════════════════════

    def _synthesize(self, text: str) -> Optional[bytes]:
        """调用讯飞 TTS API 合成语音, 返回 PCM 音频字节"""
        ws_url = self._build_ws_url()
        audio_chunks = []
        done_event = threading.Event()
        error_msg = [None]

        def on_message(ws, message):
            try:
                resp = json.loads(message)
                code = resp.get("code", -1)
                if code != 0:
                    error_msg[0] = resp.get("message", f"code={code}")
                    done_event.set()
                    return
                data = resp.get("data", {})
                audio_b64 = data.get("audio", "")
                if audio_b64:
                    audio_chunks.append(base64.b64decode(audio_b64))
                if data.get("status") == 2:  # 最后一帧
                    done_event.set()
            except Exception as e:
                error_msg[0] = str(e)
                done_event.set()

        def on_error(ws, error):
            error_msg[0] = str(error)
            done_event.set()

        def on_close(ws, a, b):
            done_event.set()

        def on_open(ws):
            # 构建请求参数 (对照 TonyPi TTS_Control 行 261-278)
            payload = {
                "common": {"app_id": self._appid},
                "business": {
                    "aue": "raw",
                    "auf": "audio/L16;rate=16000",
                    "vcn": self._voice,
                    "speed": self._speed,
                    "volume": self._volume,
                    "tte": "utf8",
                },
                "data": {
                    "status": 2,
                    "text": base64.b64encode(text.encode("utf-8")).decode(),
                },
            }
            ws.send(json.dumps(payload))

        websocket.enableTrace(False)
        ws = websocket.WebSocketApp(
            ws_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws.on_open = on_open
        # 在后台线程中运行 WebSocket
        thread = threading.Thread(
            target=ws.run_forever,
            kwargs={"sslopt": {"cert_reqs": ssl.CERT_NONE}},
        )
        thread.start()

        # 等待完成 (最多 30 秒)
        done_event.wait(timeout=30)
        ws.close()

        if error_msg[0]:
            print(f"[TTS] 合成失败: {error_msg[0]}")
            return None

        if not audio_chunks:
            print("[TTS] 未收到音频数据")
            return None

        return b"".join(audio_chunks)

    # ── 鉴权 URL (对照 TonyPi TTS_Control._build_ws_url) ──

    def _build_ws_url(self) -> str:
        """生成讯飞 TTS WebSocket 鉴权 URL"""
        url = 'wss://tts-api.xfyun.cn/v2/tts'
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))

        signature_origin = (
            "host: ws-api.xfyun.cn\n"
            "date: " + date + "\n"
            "GET /v2/tts HTTP/1.1"
        )
        signature_sha = hmac.new(
            self._api_secret.encode('utf-8'),
            signature_origin.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        signature_sha = base64.b64encode(signature_sha).decode(encoding='utf-8')

        authorization_origin = (
            'api_key="%s", algorithm="%s", headers="%s", signature="%s"'
            % (self._api_key, "hmac-sha256",
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

    # ── WAV 保存 (对照 TonyPi TTS_Control._write_wav) ──

    def _save_wav(self, pcm_data: bytes) -> Optional[str]:
        """将 PCM 数据写入带 WAV 头的临时文件"""
        import tempfile
        wav_path = tempfile.mktemp(suffix=".wav")

        sample_rate = 16000
        num_channels = 1
        bits_per_sample = 16
        byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
        block_align = num_channels * (bits_per_sample // 8)
        data_size = len(pcm_data)
        header_size = 44

        try:
            with open(wav_path, "wb") as f:
                # RIFF header
                f.write(b"RIFF")
                f.write(struct.pack("<I", header_size - 8 + data_size))
                f.write(b"WAVE")
                # fmt chunk
                f.write(b"fmt ")
                f.write(struct.pack("<I", 16))           # chunk size
                f.write(struct.pack("<H", 1))            # PCM format
                f.write(struct.pack("<H", num_channels))
                f.write(struct.pack("<I", sample_rate))
                f.write(struct.pack("<I", byte_rate))
                f.write(struct.pack("<H", block_align))
                f.write(struct.pack("<H", bits_per_sample))
                # data chunk
                f.write(b"data")
                f.write(struct.pack("<I", data_size))
                f.write(pcm_data)
        except OSError as e:
            print(f"[TTS] 写 WAV 文件失败: {e}")
            return None

        return wav_path

    # ── 播放 (对照 TonyPi TTS_Control._play) ──

    def _play(self, wav_path: str) -> bool:
        """用 aplay 播放 WAV 文件, 自动探测可用设备"""
        if self._play_device:
            cmd = ["aplay", "-D", self._play_device, wav_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return True
            if self._debug:
                print(f"[TTS] aplay -D {self._play_device} 失败: {result.stderr.strip()}")

        # 自动探测设备
        for dev in self._PLAY_DEVICES:
            if dev:
                cmd = ["aplay", "-D", dev, wav_path]
            else:
                cmd = ["aplay", wav_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                if self._debug:
                    print(f"[TTS] 播放成功 (device={dev or 'default'})")
                return True

        # 尝试 paplay
        pr = subprocess.run(
            ["paplay", wav_path], capture_output=True, text=True
        )
        if pr.returncode == 0:
            if self._debug:
                print("[TTS] 播放成功 (pulseaudio)")
            return True

        print("[TTS] 所有播放设备均失败, 请检查音频输出")
        return False
