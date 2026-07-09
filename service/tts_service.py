"""
TTS 服务 — 文字转语音业务层

调度 TTSDriver 进行文本 → 语音合成 → 播放。
"""
import threading
from typing import Optional


class TTSService:
    """文字转语音服务"""

    def __init__(self, tts_driver, debug: bool = False):
        self._driver = tts_driver
        self._debug = debug
        self._busy = False

    def speak(self, text: str, block: bool = True) -> bool:
        """朗读文字

        Args:
            text: 要朗读的文字
            block: True=阻塞等待播放完成, False=后台播放

        Returns:
            是否成功提交
        """
        if not text or not text.strip():
            return False

        if block:
            return self._driver.speak(text)
        else:
            threading.Thread(
                target=self._driver.speak, args=(text,), daemon=True
            ).start()
            return True

    @property
    def busy(self) -> bool:
        return self._busy
