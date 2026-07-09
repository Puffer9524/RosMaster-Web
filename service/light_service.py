"""
灯带控制服务

对照 app_sim2.py:
    toggle_light() → self.g_bot.set_light(0/1)   — 仅开关照明灯

Web 方案增强:
    - 支持 RGB 颜色设置 (全体灯珠)
    - 支持 6 种灯效切换
"""


class LightService:
    """灯带控制服务"""

    def __init__(self, driver):
        self._driver = driver

    def set_rgb(self, r: int, g: int, b: int) -> None:
        """设置全体灯珠颜色
        对照: self.g_bot.set_light(0/1) — Web 增强为 RGB
        """
        self._driver.set_rgb(led_id=255, r=r, g=g, b=b)

    def set_effect(self, effect: int, speed: int = 5) -> None:
        """设置灯带特效
        Args:
            effect: 0=关 1=流水 2=跑马 3=呼吸 4=渐变 5=星光 6=电量
            speed:  1~10, 越小越快
        """
        self._driver.set_rgb_effect(effect, speed)

    def turn_off(self) -> None:
        """关闭所有灯光"""
        self._driver.set_rgb(led_id=255, r=0, g=0, b=0)
        self._driver.set_rgb_effect(0)
