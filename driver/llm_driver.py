"""
LLM 驱动 — DeepSeek Chat API (OpenAI 兼容)

对照 TonyPi CustomFunctions/LLM_Control.py:
    - _call_api() → POST {base_url}/chat/completions
    - 标准 OpenAI 请求格式

API: https://api.deepseek.com/chat/completions
依赖: requests
"""
import json
from typing import Optional


class LLMDriver:
    """DeepSeek 大模型驱动"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        timeout: int = 30,
        debug: bool = False,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._debug = debug

    def chat(
        self,
        messages: list,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> Optional[str]:
        """发送对话请求, 返回模型回复文本

        Args:
            messages: [{"role":"system","content":"..."}, {"role":"user","content":"..."}]
            temperature: 0.0-1.0
            max_tokens: 最大输出 token 数

        Returns:
            模型回复内容, 失败返回 None
        """
        import requests

        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if self._debug:
            print(f"[LLM] 请求: {url} (model={self._model})")

        try:
            resp = requests.post(
                url, headers=headers, json=payload, timeout=self._timeout
            )
            if resp.status_code != 200:
                print(f"[LLM] API 错误 ({resp.status_code}): {resp.text[:300]}")
                return None

            data = resp.json()
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            if self._debug:
                preview = content[:200] + "..." if len(content) > 200 else content
                print(f"[LLM] 回复: {preview}")
            return content

        except requests.exceptions.Timeout:
            print("[LLM] 请求超时")
            return None
        except Exception as e:
            print(f"[LLM] 请求异常: {e}")
            return None
