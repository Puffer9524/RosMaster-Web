"""
Rosmaster Web 遥控系统 — 配置常量

对照 app_sim2.py:
    self.speed = 50
    speeds = [10, 20, 30, 50]
    durations = ["持续", "0.5s", "1s"]
    self.cap = cv2.VideoCapture(0)
    self.g_bot = Rosmaster(debug=True)
"""
import os

# ── 串口 (对照 app_sim2.py: self.g_bot = Rosmaster(debug=True), 不传 com 参数,
#      Rosmaster_Lib 默认 com="/dev/myserial"，Jetson 上 /dev/myserial 是 udev 软链接) ──
SERIAL_PORT = os.environ.get("ROSMASTER_PORT", "/dev/myserial")
SERIAL_BAUD = 115200
CAR_TYPE = int(os.environ.get("ROSMASTER_CAR_TYPE", "1"))  # 1=X3, 2=X3_PLUS, 4=X1, 5=R2

# ── 服务器 (对照 app_sim2.py: root.mainloop()) ──
SERVER_HOST = "0.0.0.0"
SERVER_PORT = int(os.environ.get("ROSMASTER_WEB_PORT", "8765"))

# ── 摄像头 (对照 app_sim2.py: self.cap = cv2.VideoCapture(0)) ──
CAMERA_ID = int(os.environ.get("ROSMASTER_CAMERA_ID", "0"))  # 0=/dev/video0, 1=/dev/video1, 或 0x50/0x51/0x52
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
JPEG_QUALITY = 65

# ── 运动控制 (对照 app_sim2.py) ──
# 速度档位: speeds = [10, 20, 30, 50]
SPEED_LEVELS = [10, 20, 30, 50, 60, 80, 100]
DEFAULT_SPEED = 50
# 旋转速度系数: self.speed*3.2/100.0
ROTATION_FACTOR = 3.2
# 超时: 0.5秒无新指令 → 自动停车
MOTION_TIMEOUT = 0.5

# 速度限制 (按车型, 对应 Rosmaster_Lib.set_car_motion 注释)
SPEED_LIMITS = {
    1:  {"vx": 1.0, "vy": 1.0, "vz": 5.0},       # X3
    2:  {"vx": 0.7, "vy": 0.7, "vz": 3.2},       # X3 Plus
    4:  {"vx": 1.0, "vy": 1.0, "vz": 5.0},       # X1
    5:  {"vx": 1.8, "vy": 0.045, "vz": 3.0},     # R2
}

# ── 传感器 (对照 app_sim2.py: root.after(3000, update_iot_labels)) ──
SENSOR_INTERVAL = float(os.environ.get("ROSMASTER_SENSOR_INTERVAL", "0.5"))

# ── 音频 (对照 SerialService.py: SOUND_PATH = '/home/jetson/sound/') ──
SOUND_DIR = os.environ.get("ROSMASTER_SOUND_DIR", "/home/jetson/sound")

# ── 语音识别 (参照 TonyPi STT_Control: arecord 录音 + 科大讯飞 WebSocket API) ──
# 录音设备: arecord -l → card 2: XFMDPV0018 (XFM-DP-V0.0.18 麦克风阵列)
# ALSA 设备名: plughw:<card>,<device>
VOICE_ALSA_DEVICE = os.environ.get("ROSMASTER_VOICE_ALSA", "plughw:2,0")
# 是否自动执行识别到的运动指令
VOICE_AUTO_EXEC = os.environ.get("ROSMASTER_VOICE_AUTO_EXEC", "1") == "1"

# ── 科大讯飞 API (对照 TonyPi config.yaml → xunfei) ──
XUNFEI_APPID = os.environ.get("XUNFEI_APPID", "5f750c99")
XUNFEI_API_KEY = os.environ.get("XUNFEI_API_KEY", "2a0f2ee33f09efbe904aa7689a8c9d4e")
XUNFEI_API_SECRET = os.environ.get("XUNFEI_API_SECRET", "NzBmZjNmZmY4YjFkMWM3NTU5NzMwZDJj")

# ── 文字转语音 TTS (对照 TonyPi TTS_Control: 科大讯飞 TTS WebSocket API + aplay 播放) ──
# 注意: TTS 使用独立的讯飞应用 (与 STT 不同账号)
TTS_APPID = os.environ.get("TTS_APPID", "9c724cae")
TTS_API_KEY = os.environ.get("TTS_API_KEY", "e3b51df80911bfed156cccc85bf87609")
TTS_API_SECRET = os.environ.get("TTS_API_SECRET", "OGM0YmU0MDk1MWMzYzViODhjM2YyOWQx")
TTS_VOICE = os.environ.get("TTS_VOICE", "x4_yezi")       # 发音人
TTS_SPEED = int(os.environ.get("TTS_SPEED", "50"))        # 语速 0-100
TTS_VOLUME = int(os.environ.get("TTS_VOLUME", "100"))     # 音量 0-100
TTS_PLAY_DEVICE = os.environ.get("TTS_PLAY_DEVICE", "")   # aplay -D 设备, 空=自动探测

# ── 大模型 LLM (对照 TonyPi LLM_Control: DeepSeek Chat API) ──
LLM_API_KEY = os.environ.get("LLM_API_KEY", "sk-619cde7eb0b04dc896c3b2014e03da71")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

# ── 调试 ──
DEBUG = os.environ.get("ROSMASTER_DEBUG", "0") == "1"
