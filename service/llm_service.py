"""
LLM 服务 — 小车专用的系统提示词 + 对话历史 + 计划解析

对照 TonyPi LLM_Control:
    - 系统提示词嵌入可用动作列表
    - 要求输出严格 JSON
    - 维护对话历史

流程:
    用户语音 → STT 转文字 → LLMService.parse() → DeepSeek → JSON 计划 → 执行
"""
import json
from typing import Optional


class LLMService:
    """小车 LLM 服务 (任务规划 + 对话)"""

    # ── 小车专用系统提示词 ──
    SYSTEM_PROMPT = """你是 Rosmaster 机器人小车的智能助手。你接收用户的语音指令，规划执行步骤。

## 可用动作
- forward: 前进 (params: duration=秒数，默认1.0，建议0.5-3.0)
- backward: 后退 (params: duration=秒数，默认1.0，建议0.5-3.0)
- turn_left: 左转 (params: duration=秒数，默认1.5，建议0.5-3.0)
- turn_right: 右转 (params: duration=秒数，默认1.5，建议0.5-3.0)
- strafe_left: 左移/向左平移 (params: duration=秒数，默认1.0，建议0.5-3.0)
- strafe_right: 右移/向右平移 (params: duration=秒数，默认1.0，建议0.5-3.0)
- stop: 停止
- speed_up: 加速
- speed_down: 减速
- led_red: 亮红灯
- led_green: 亮绿灯
- led_blue: 亮蓝灯
- led_white: 亮白灯
- led_off: 关灯
- led_breath: 呼吸灯特效
- beep_alarm: 警报声
- beep_sos: SOS求救声
- speak: 语音回复 (params: text=要说的话)
- wait: 等待 (params: seconds=秒数)

## 输出格式 (严格 JSON, 不要输出其他内容)
{
  "intent": "用户的意图简述",
  "tts_response": "你要对用户说的话 (自然口语, 1-2句, 如果不想说则留空)",
  "steps": [
    {"action": "forward", "params": {"duration": 1.0}, "description": "前进1秒"},
    {"action": "turn_left", "params": {}, "description": "左转"},
    {"action": "speak", "params": {"text": "已到达目的地"}, "description": "告知用户"}
  ]
}

## 规则
1. 短指令（如"前进"、"左转"）直接返回单个 step
2. 复杂指令（如"往前走再左转然后后退"）拆成多个 step
3. 非运动指令（如"你好"、"今天天气怎么样"）可以只回复 speak 不执行动作
4. tts_response 要简洁自然，像人和人对话一样
5. 如果完全不理解用户意图，tts_response 中礼貌地说明"""

    def __init__(
        self,
        llm_driver,
        max_history: int = 6,
        debug: bool = False,
    ):
        self._driver = llm_driver
        self._max_history = max_history
        self._debug = debug
        self._history: list = []  # [{"role":"user","content":...}, {"role":"assistant","content":...}, ...]

    def parse(self, text: str) -> Optional[dict]:
        """将用户输入解析为执行计划

        Args:
            text: 用户说的话 (中文)

        Returns:
            {"intent":"...", "tts_response":"...", "steps":[...]} 或 None
        """
        if not text or not text.strip():
            return None

        # 构建消息
        messages = [{"role": "system", "content": self.SYSTEM_PROMPT}]

        # 最近 N 轮历史
        recent = self._history[-(self._max_history * 2):]
        messages.extend(recent)

        # 当前用户输入
        messages.append({"role": "user", "content": text})

        if self._debug:
            print(f"[LLM] 用户输入: '{text}'")
            print(f"[LLM] 历史轮数: {len(self._history)//2}")

        raw = self._driver.chat(messages, temperature=0.1, max_tokens=2000)
        if not raw:
            return None

        # 解析 JSON
        plan = self._parse_json(raw)
        if plan is None:
            if self._debug:
                print(f"[LLM] JSON 解析失败, 原始回复: {raw[:300]}")
            return None

        # 保存历史
        self._history.append({"role": "user", "content": text})
        self._history.append({"role": "assistant", "content": raw})

        # 裁剪历史
        if len(self._history) > self._max_history * 2:
            self._history = self._history[-(self._max_history * 2):]

        return plan

    def _parse_json(self, raw: str) -> Optional[dict]:
        """从 LLM 回复中提取 JSON"""
        raw = raw.strip()
        # 去掉可能的 markdown 代码块标记
        if raw.startswith("```"):
            lines = raw.split("\n")
            # 去掉首行的 ```json 和末行的 ```
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            raw = "\n".join(lines)

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # 尝试用正则提 {} 块
        import re
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass

        return None
