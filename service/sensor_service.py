"""
传感器采集推送服务

对照 Rosmaster-App:
    rosmaster_main.py return_sensor_data():
        num_temp = self.g_bot.get_temperature_data()
        num_humidity = self.g_bot.get_humidity_data()
        num_atomosphere = self.g_bot.get_atmosphere_data()
        num_light = self.g_bot.get_light_data()
        num_gas = self.g_bot.get_gas_data()
        num_pm25 = self.g_bot.get_pm25_data()
        num_latitude = self.g_bot.get_latitude_data()
        num_longitude = self.g_bot.get_longitude_data()

本服务将上述 8 项传感器 + IMU + 编码器 + 速度数据整合推送。
"""
import time
import threading


class SensorService:
    """传感器采集推送服务

    对照 Rosmaster-App:
        app_sim2.py: self.timer_id = self.root.after(3000, self.update_iot_labels)
        → Tkinter 定时器, 每 3s 刷新

    Web 方案改进:
        - 定时扫描全部传感器
        - 通过 WebSocket 主动推送 JSON
    """

    def __init__(self, driver, interval: float = 0.5):
        """
        Args:
            driver: SerialDriver 实例
            interval: 推送间隔 [秒], 默认 500ms
        """
        self._driver = driver
        self._interval = interval
        self._running = False
        self._thread = None
        self._callback = None

    def start(self, callback) -> None:
        """启动定时推送线程

        Args:
            callback: 推送回调函数, 签名 callback(data: dict) -> None

        对照 Rosmaster-App:
            app_sim2.py: root.after(3000, update_iot_labels)
        """
        self._callback = callback
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止推送线程"""
        self._running = False

    def _loop(self) -> None:
        """后台循环: 定时读取所有传感器并推送

        对照 Rosmaster-App rosmaster_main.py return_sensor_data():
            一次性读取 8 项传感器, 打包为 TCP 帧发送

        Web 方案: 调用 SerialDriver.get_sensor_snapshot() 获取完整快照
        """
        while self._running:
            try:
                snapshot = self._driver.get_sensor_snapshot()
                if self._callback:
                    self._callback(snapshot)
            except Exception:
                pass
            time.sleep(self._interval)
