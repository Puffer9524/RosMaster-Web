/**
 * Rosmaster Web 遥控 — 前端逻辑 (HTTP API 版)
 *
 * 对照 app_sim2.py:
 *   - 方向按钮 → self.g_bot.set_car_motion(...)
 *   - 传感器更新 → update_iot_labels() 每3秒
 *   - 速度档位   → self.speed_var
 *   - 功能开关   → toggle_xxx()
 *
 * 通信方式: HTTP 轮询 (GET /api/sensors) + POST 命令
 * 不用 WebSocket, 避免 flask_sock/gevent 兼容问题
 */
(function () {
  "use strict";

  // ====== 状态 ======
  const speedScales = [0.3, 0.6, 1.0];
  let speedLevel = 1;          // 默认中速 = 60%
  let motionInterval = null;
  let activeKeys = new Set();
  let sensorTimer = null;

  // ====== DOM 引用 ======
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);

  const video      = $("#video");
  const valTemp    = $("#val-temp");
  const valHumi    = $("#val-humi");
  const valBatt    = $("#val-batt");

  // ====== HTTP 请求辅助 ======
  function post(url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }).catch(() => {});  // 静默失败
  }

  function getJSON(url) {
    return fetch(url)
      .then((r) => r.json())
      .catch(() => null);
  }

  // ====== 传感器轮询 (对照 app_sim2.py root.after(3000, update_iot_labels)) ======
  function startSensorPoll() {
    if (sensorTimer) return;
    sensorTimer = setInterval(() => {
      getJSON("/api/sensors").then((data) => {
        if (!data) return;
        // 核心传感器 (对照 app_sim2.py 9项)
        if (data.temperature !== undefined) valTemp.textContent = data.temperature.toFixed(1);
        if (data.humidity !== undefined)    valHumi.textContent = data.humidity.toFixed(1);
        if (data.battery !== undefined)     valBatt.textContent = data.battery.toFixed(2);
      });
    }, 500);  // 每500ms轮询
  }

  // ====== 运动控制 (对照 app_sim2.py execute_command_with_duration) ======
  function computeMotion() {
    const scale = speedScales[speedLevel];
    let vx = 0, vy = 0, vz = 0;
    if (activeKeys.has("W")) vx += 1.0;
    if (activeKeys.has("S")) vx -= 1.0;
    if (activeKeys.has("A")) vy += 1.0;
    if (activeKeys.has("D")) vy -= 1.0;
    if (activeKeys.has("Q")) vz += 3.2;
    if (activeKeys.has("E")) vz -= 3.2;

    return {
      vx: +(vx * scale).toFixed(3),
      vy: +(vy * scale).toFixed(3),
      vz: +(vz * scale).toFixed(3),
    };
  }

  function sendMotion() {
    const m = computeMotion();
    post("/api/motion", m);
  }

  function startMotion() {
    sendMotion();
    if (motionInterval) return;
    motionInterval = setInterval(sendMotion, 50);
  }

  function stopMotion() {
    if (motionInterval) { clearInterval(motionInterval); motionInterval = null; }
    // 连发 3 次零速度 (对照 app_sim2.py stop_move)
    post("/api/motion", { vx: 0, vy: 0, vz: 0 });
    post("/api/motion", { vx: 0, vy: 0, vz: 0 });
    post("/api/motion", { vx: 0, vy: 0, vz: 0 });
  }

  // ====== 键盘事件 ======
  const KEY_MAP = {
    w: "W", W: "W", ArrowUp:    "W",
    s: "S", S: "S", ArrowDown:  "S",
    a: "A", A: "A", ArrowLeft:  "A",
    d: "D", D: "D", ArrowRight: "D",
    q: "Q", Q: "Q",
    e: "E", E: "E",
  };

  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    const mapped = KEY_MAP[e.key];
    if (mapped) {
      e.preventDefault();
      if (!activeKeys.has(mapped)) {
        activeKeys.add(mapped);
        highlightBtn(mapped, true);
        if (activeKeys.size === 1) startMotion();
      }
    }
    if (e.key === " " || e.code === "Space") {
      e.preventDefault();
      emergencyStop();
    }
    if (e.key === "1") setSpeed(0);
    if (e.key === "2") setSpeed(1);
    if (e.key === "3") setSpeed(2);
  });

  document.addEventListener("keyup", (e) => {
    const mapped = KEY_MAP[e.key];
    if (mapped && activeKeys.has(mapped)) {
      activeKeys.delete(mapped);
      highlightBtn(mapped, false);
      if (activeKeys.size === 0) stopMotion();
    }
  });

  // ====== 按钮事件 (触摸 + 鼠标) ======
  function bindDirectionButton(id, key) {
    const btn = document.getElementById(id);
    if (!btn) return;

    const press = (e) => {
      e.preventDefault();
      if (!activeKeys.has(key)) {
        activeKeys.add(key);
        highlightBtn(key, true);
        if (activeKeys.size === 1) startMotion();
      }
    };
    const release = (e) => {
      e.preventDefault();
      if (activeKeys.has(key)) {
        activeKeys.delete(key);
        highlightBtn(key, false);
        if (activeKeys.size === 0) stopMotion();
      }
    };

    btn.addEventListener("mousedown", press);
    btn.addEventListener("touchstart", press, { passive: false });
    btn.addEventListener("mouseup", release);
    btn.addEventListener("mouseleave", release);
    btn.addEventListener("touchend", release);
    btn.addEventListener("touchcancel", release);
  }

  function highlightBtn(key, on) {
    const btn = document.querySelector(`[data-key="${key}"]`);
    if (btn) btn.classList.toggle("active", on);
  }

  ["W","S","A","D","Q","E"].forEach((k) => {
    bindDirectionButton("btn-" + k, k);
  });

  // 停车按钮
  const stopBtn = document.getElementById("btn-STOP");
  if (stopBtn) {
    stopBtn.addEventListener("mousedown", (e) => { e.preventDefault(); emergencyStop(); });
    stopBtn.addEventListener("touchstart", (e) => { e.preventDefault(); emergencyStop(); });
  }

  function emergencyStop() {
    activeKeys.clear();
    stopMotion();
    $$(".dir-btn").forEach((b) => b.classList.remove("active"));
  }

  // ====== 速度档位 ======
  function setSpeed(level) {
    speedLevel = Math.max(0, Math.min(2, level));
    $$(".speed-btn").forEach((b, i) => b.classList.toggle("active", i === speedLevel));
  }

  $$(".speed-btn").forEach((btn) => {
    btn.addEventListener("click", () => setSpeed(parseInt(btn.dataset.level)));
  });

  // ====== 灯光 (对照 app_sim2.py toggle_light → set_light(0/1)) ======
  // ====== 灯光特效按钮 ======
  // 所有特效均通过前端定时器开关 /api/light 来模拟 (小车只有白光照明灯)
  let effectTimer = null;

  function stopEffect() {
    if (effectTimer) {
      if (effectTimer._starActive !== undefined) {
        effectTimer._starActive = false;  // 停止星光递归
      } else {
        clearInterval(effectTimer);
      }
      effectTimer = null;
    }
  }

  // 各特效的实现 (返回定时器 ID)
  const EFFECTS = {
    // 1: 流水灯 — 快速 1s 周期: 亮0.5s → 灭0.5s
    1: (on, off) => setInterval(() => { on(); setTimeout(off, 500); }, 1000),

    // 2: 跑马灯 — 三连闪: 亮100ms三次, 间隔100ms, 停1s
    2: (on, off) => {
      let count = 0;
      let phase = 0; // 0=等待, 1=闪烁中
      return setInterval(() => {
        if (phase === 0) {
          phase = 1; count = 0;
          on(); setTimeout(() => {
            off();
            setTimeout(() => { on(); setTimeout(() => {
              off();
              setTimeout(() => { on(); setTimeout(() => {
                off(); phase = 0;
              }, 100); }, 100);
            }, 100); }, 100);
          }, 100);
        }
      }, 2000);
    },

    // 3: 呼吸灯 — 人体呼吸频率 ~4s: 亮2s → 灭2s
    3: (on, off) => setInterval(() => {
      on(); setTimeout(off, 2000);
    }, 4000),

    // 4: 渐变灯 — 模拟PWM渐变: 亮度由快到慢再由慢到快
    4: (onTimer, offTimer) => {
      // 用快速闪烁的不同占空比模拟渐变
      // 周期8秒: 前4秒渐亮(灭越来越短), 后4秒渐暗(亮越来越短)
      const CYCLE = 8000;
      const TICK = 100;  // 每100ms切换一次
      let start = Date.now();
      return setInterval(() => {
        const elapsed = (Date.now() - start) % CYCLE;
        const progress = elapsed / CYCLE;  // 0→1
        // 正弦波: 0=最暗, 0.5=最亮, 1=最暗
        const brightness = (Math.sin(progress * Math.PI * 2 - Math.PI / 2) + 1) / 2;
        // 占空比: 亮的时间比例
        const onDuration = Math.round(brightness * TICK);
        offTimer();
        if (onDuration > 0) {
          onTimer();
          setTimeout(offTimer, onDuration);
        }
      }, TICK);
    },

    // 5: 星光 — 随机闪烁: 随机0.5~3秒亮一次, 持续50~200ms
    5: (on, off) => {
      off();
      const state = { _starActive: true };
      const schedule = () => {
        if (!state._starActive) return;
        const delay = 500 + Math.random() * 2500;
        setTimeout(() => {
          if (!state._starActive) return;
          const duration = 50 + Math.random() * 150;
          on();
          setTimeout(() => { if (state._starActive) { off(); schedule(); } }, duration);
        }, delay);
      };
      schedule();
      return state;
    },

    // 6: 电量 — SOS模式: 快闪次数对应"电量"
    6: (on, off) => {
      let step = 0;
      return setInterval(() => {
        // 1次 → 2次 → 3次 → 4次快闪, 然后循环
        const blinks = (step % 4) + 1;
        let i = 0;
        const flash = () => {
          if (i >= blinks) { off(); return; }
          on();
          setTimeout(() => { off(); i++; setTimeout(flash, 200); }, 200);
        };
        flash();
        step++;
      }, 3000);
    },
  };

  $$(".effect-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const effect = parseInt(btn.dataset.effect);

      // 先停止之前的特效
      stopEffect();
      post("/api/light", { enable: false });

      if (effect === 0) return;  // 关灯特效 = 停止

      const on = () => post("/api/light", { enable: true });
      const off = () => post("/api/light", { enable: false });

      const fn = EFFECTS[effect];
      if (fn) {
        const result = fn(on, off);
        if (result._starActive !== undefined) {
          // 星光用特殊标记, 用 interval fallback 存储
          effectTimer = result;
        } else {
          effectTimer = result;
        }
      }
      // 高亮当前按钮
      $$(".effect-btn").forEach(b => b.classList.remove("active-effect"));
      btn.classList.add("active-effect");
    });
  });

  // 点颜色按钮也停止特效
  $$(".color-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      stopEffect();
      $$(".effect-btn").forEach(b => b.classList.remove("active-effect"));
      const r = parseInt(btn.dataset.r);
      const g = parseInt(btn.dataset.g);
      const b = parseInt(btn.dataset.b);
      if (r === 0 && g === 0 && b === 0) {
        post("/api/light", { enable: false });
      } else {
        post("/api/light", { enable: true });
      }
    });
  });

  // ====== 蜂鸣器音效 (通过 /api/beep 的 on/off 时序模拟各种声音) ======
  let beepOn = false;
  let soundTimer = null;

  function stopSound() {
    beepOn = false;
    if (soundTimer) {
      if (soundTimer._soundActive !== undefined) {
        soundTimer._soundActive = false;
      } else {
        clearInterval(soundTimer);
      }
      soundTimer = null;
    }
    post("/api/beep", { enable: false });
    const beepBtn = document.getElementById("btn-beep");
    if (beepBtn) beepBtn.classList.remove("active");
  }

  // 音效定义
  const SOUNDS = {
    // 1: 流水声 — 连续 2Hz 短促滴滴: 50ms响 → 450ms停
    1: (on, off) => setInterval(() => { on(); setTimeout(off, 50); }, 500),

    // 2: 马蹄声 — 三连击: 哒哒哒... 哒哒哒...
    2: (on, off) => {
      const tick = () => {
        for (let i = 0; i < 3; i++) {
          setTimeout(() => on(), i * 150);
          setTimeout(() => off(), i * 150 + 80);
        }
      };
      tick();
      return setInterval(tick, 800);
    },

    // 3: 呼吸声 — 4s周期: 响2s(吸气) → 停2s(呼气)
    3: (on, off) => setInterval(() => { on(); setTimeout(off, 2000); }, 4000),

    // 4: 警报声 — 急急促交替: 响150ms → 停150ms
    4: (on, off) => setInterval(() => { on(); setTimeout(off, 150); }, 300),

    // 5: 随机鸟鸣 — 随机间隔50~300ms, 每次10~40ms
    5: (on, off) => {
      off();
      const state = { _soundActive: true };
      const chirp = () => {
        if (!state._soundActive) return;
        const delay = 50 + Math.random() * 250;
        setTimeout(() => {
          if (!state._soundActive) return;
          const dur = 10 + Math.random() * 30;
          on();
          setTimeout(() => { if (state._soundActive) { off(); chirp(); } }, dur);
        }, delay);
      };
      chirp();
      return state;
    },

    // 6: SOS — ··· ——— ···
    6: (on, off) => {
      // S: 3短(各100ms), O: 3长(各300ms), 每段间隔200ms, 词间隔600ms
      const dot = (base, cb) => { on(); setTimeout(() => { off(); setTimeout(cb, 200); }, base); };
      const S = (cb) => dot(100, () => dot(100, () => dot(100, () => setTimeout(cb, 200))));
      const O = (cb) => dot(300, () => dot(300, () => dot(300, () => setTimeout(cb, 200))));
      const SOS = () => S(() => O(() => S(() => {})));
      SOS();
      return setInterval(SOS, 5000);
    },
  };

  // 蜂鸣器手动按钮 (点按切换)
  const beepBtn = document.getElementById("btn-beep");
  if (beepBtn) {
    beepBtn.addEventListener("click", () => {
      stopSound();
      beepOn = !beepOn;
      post("/api/beep", { enable: beepOn });
      beepBtn.classList.toggle("active", beepOn);
    });
  }

  // 音效按钮
  $$(".sound-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const sound = parseInt(btn.dataset.sound);

      stopSound();
      $$(".sound-btn").forEach(b => b.classList.remove("active-sound"));

      if (sound === 0) return;  // 静音

      const on = () => post("/api/beep", { enable: true });
      const off = () => post("/api/beep", { enable: false });

      const fn = SOUNDS[sound];
      if (fn) {
        const result = fn(on, off);
        soundTimer = result;
      }
      btn.classList.add("active-sound");
    });
  });

  // ====== 页面关闭 → 停车 + 关蜂鸣 + 关灯 + 停止特效 ======
  window.addEventListener("beforeunload", () => {
    stopEffect();
    stopSound();
    post("/api/motion", { vx: 0, vy: 0, vz: 0 });
    post("/api/beep", { enable: false });
    post("/api/light", { enable: false });
  });

  // ====== 车载歌曲播放 (对照 app_sim2.py playSound → UDP :10001) ======
  (function () {
    const select   = document.getElementById("music-select");
    const playBtn  = document.getElementById("btn-music-play");
    const delBtn   = document.getElementById("btn-music-del");
    const fileInp  = document.getElementById("upload-file");
    const idInp    = document.getElementById("upload-id");
    const uploadBtn = document.getElementById("btn-upload");
    const statusEl = document.getElementById("upload-status");

    // ── 加载歌曲列表 ──
    function loadSounds() {
      getJSON("/api/sounds").then((sounds) => {
        if (!sounds || !sounds.length) {
          select.innerHTML = '<option value="">-- 无可用歌曲 --</option>';
          return;
        }
        const groups = {};
        sounds.forEach((s) => {
          const g = s.group || "其他";
          if (!groups[g]) groups[g] = [];
          groups[g].push(s);
        });
        select.innerHTML = '<option value="">-- 选择歌曲 --</option>';
        Object.keys(groups).forEach((grp) => {
          const og = document.createElement("optgroup");
          og.label = grp;
          groups[grp].forEach((s) => {
            const opt = document.createElement("option");
            opt.value = s.id;
            opt.textContent = s.name + (s.size ? ` (${fmtSize(s.size)})` : "");
            opt.dataset.custom = s.group === "自定义" ? "1" : "";
            og.appendChild(opt);
          });
          select.appendChild(og);
        });
      });
    }

    function fmtSize(bytes) {
      if (bytes < 1024) return bytes + "B";
      if (bytes < 1048576) return (bytes / 1024).toFixed(0) + "KB";
      return (bytes / 1048576).toFixed(1) + "MB";
    }

    // ── 播放 ──
    if (playBtn) {
      playBtn.addEventListener("click", () => {
        const sid = select.value;
        if (!sid) return;
        post("/api/sound", { id: sid });
        flashBtn(playBtn, "⏸ 已发送", "#5a4a00", "#fff176", 600);
      });
    }

    // ── 删除 ──
    if (delBtn) {
      delBtn.addEventListener("click", () => {
        const sid = select.value;
        const opt = select.selectedOptions[0];
        if (!sid) return;
        if (opt && opt.dataset.custom !== "1") {
          setStatus("内置音效不可删除", true);
          return;
        }
        if (!confirm(`确定删除 "${sid}" 及其关联文件?`)) return;
        post("/api/sound/delete", { id: sid }).then(() => {
          setStatus("已删除");
          loadSounds();
        });
      });
    }

    // ── 上传 ──
    if (uploadBtn) {
      uploadBtn.addEventListener("click", () => {
        const file = fileInp.files[0];
        if (!file) { setStatus("请先选择文件", true); return; }

        const form = new FormData();
        form.append("file", file);
        const customId = idInp.value.trim();
        if (customId) form.append("id", customId);

        setStatus("上传中...");
        fetch("/api/sound/upload", { method: "POST", body: form })
          .then((r) => r.json())
          .then((res) => {
            if (res.ok) {
              setStatus(`✓ ${res.id}`);
              fileInp.value = "";
              idInp.value = "";
              loadSounds();
            } else {
              setStatus(res.error || "上传失败", true);
            }
          })
          .catch(() => setStatus("上传失败", true));
      });
    }

    function setStatus(msg, isErr) {
      if (!statusEl) return;
      statusEl.textContent = msg;
      statusEl.className = isErr ? "error" : "";
      if (!isErr) setTimeout(() => { statusEl.textContent = ""; statusEl.className = ""; }, 3000);
    }

    function flashBtn(btn, text, bg, color, dur) {
      const origText = btn.textContent, origBg = btn.style.background, origColor = btn.style.color;
      btn.textContent = text; btn.style.background = bg; btn.style.color = color;
      setTimeout(() => {
        btn.textContent = origText; btn.style.background = origBg; btn.style.color = origColor;
      }, dur);
    }

    // ── 初始加载 ──
    loadSounds();
  })();

  // ====== 语音识别控制 ======
  (function () {
    const toggleBtn  = document.getElementById("btn-voice-toggle");
    const resultEl   = document.getElementById("voice-result");
    let voiceActive  = false;

    if (!toggleBtn || !resultEl) return;

    toggleBtn.addEventListener("click", () => {
      if (voiceActive) {
        // 停止录音 → 识别
        toggleBtn.textContent = "识别中...";
        toggleBtn.disabled = true;
        post("/api/voice/stop").then((res) => {
          voiceActive = false;
          toggleBtn.textContent = "开始录音";
          toggleBtn.classList.remove("voice-on");
          toggleBtn.classList.add("voice-off");
          toggleBtn.disabled = false;

          if (res.text) {
            if (res.action) {
              resultEl.innerHTML = `<span class="voice-cmd">🗣 "${res.text}" → ${res.action}</span>`;
            } else {
              resultEl.innerHTML = `<span class="voice-text">🗣 "${res.text}" (未匹配)</span>`;
            }
          } else if (res.error) {
            resultEl.innerHTML = `<span class="voice-text">⚠ ${res.error}</span>`;
          } else {
            resultEl.innerHTML = `<span class="voice-text">未识别到语音</span>`;
          }
        });
      } else {
        // 开始录音
        post("/api/voice/start").then(() => {
          voiceActive = true;
          toggleBtn.textContent = "● 录音中... 点击停止";
          toggleBtn.classList.remove("voice-off");
          toggleBtn.classList.add("voice-on");
          resultEl.innerHTML = "";
        });
      }
    });

    // 页面关闭时停止录音
    window.addEventListener("beforeunload", () => {
      if (voiceActive) post("/api/voice/stop");
    });
  })();

  // ====== 启动 ======
  startSensorPoll();
})();
