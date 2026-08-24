# -*- coding: utf-8 -*-
import json, math, random, threading, time
from flask import Flask, jsonify, render_template_string, request, send_file

app = Flask(__name__)
db_lock = threading.Lock()

state = {
    "satellite": {"active": True, "name": "SAT-IR-GEO-04", "signal_quality": 98.4, "gps_lock": True, "snr_db": 42.1, "orbit_deg": 142.5},
    "stealth_drone": {"id": "DRONE-01-RECON", "name": "پهپاد شناسایی رادارگریز", "rcs_m2": 0.0035, "stealth_active": True, "x": 210, "y": 170, "alt_m": 420.0, "speed_kmh": 145.0, "mineral_anomaly_ppm": 784.2},
    "dive_drone": {"id": "DRONE-02-DIVER", "name": "پهپاد شیرجه‌زن نقطه زن", "dive_engaged": False, "stealth_active": True, "x": 620, "y": 380, "alt_m": 1250.0, "speed_kmh": 260.0, "g_force": 1.1},
    "target": {"name": "هدف معدنی", "x": 700, "y": 470},
    "gcs": {"station_name": "ایستگاه مرکزی", "uplink_rate_kbps": 124.5, "downlink_rate_kbps": 890.2, "tx_packets": 145020, "rx_packets": 298410, "latency_ms": 14.2},
    "telemetry": {"distance_to_target_m": 3412.8, "positioning_accuracy_cm": 1.85, "mopso_cost": 0.0142},
    "custom_texts": {
        "header_title": "سامانه فرماندهی و پایش پهپادهای معدنی و عملیاتی لبه‌ای",
        "sub_title": "معماری توزیع‌شده HIL - MOPSO / NSGA-II / ANFIS",
        "sat_panel_title": "سامانه ناوبری و ماهواره پشتیبان",
        "recon_panel_title": "پهپاد اکتشافی پنهان‌کار (MOPSO-ANFIS)",
        "diver_panel_title": "پهپاد شیرجه‌زن نقطه زن",
        "gcs_panel_title": "ایستگاه کنترل و تبادل تله‌متری زمین‌پایه",
        "alert_text": "سیستم‌های تله‌متری کامپیوتر لبه‌ای و لینک رادیویی پایدار است."
    }
}
ADMIN_PASS = "admin123"

def sim_thread():
    while True:
        with db_lock:
            state["satellite"]["orbit_deg"] = (state["satellite"]["orbit_deg"] + 0.5) % 360
            if state["satellite"]["active"]:
                state["satellite"]["signal_quality"] = round(max(85.0, min(100.0, state["satellite"]["signal_quality"] + random.uniform(-0.8, 0.8))), 1)
                state["satellite"]["snr_db"] = round(max(35.0, min(48.0, state["satellite"]["snr_db"] + random.uniform(-0.3, 0.3))), 1)
            else:
                state["satellite"]["signal_quality"] = 0.0
                state["satellite"]["snr_db"] = 0.0
            
            state["stealth_drone"]["x"] = round(200 + 40 * math.cos(time.time() * 0.4), 1)
            state["stealth_drone"]["y"] = round(160 + 35 * math.sin(time.time() * 0.3), 1)
            state["stealth_drone"]["alt_m"] = round(420 + 15 * math.sin(time.time() * 0.2), 1)
            state["stealth_drone"]["mineral_anomaly_ppm"] = round(780 + random.uniform(-15, 25), 1)

            if state["dive_drone"]["dive_engaged"]:
                # VISUAL_UPGRADE_V2_OK: حرکت پهپاد به سمت هدف واقعی
                dd = state["dive_drone"]
                target = state["target"]
                vx = target["x"] - dd["x"]
                vy = target["y"] - dd["y"]
                planar_distance = math.hypot(vx, vy)
                if planar_distance > 0.01:
                    step = min(38.0, planar_distance)
                    dd["x"] = round(dd["x"] + (vx / planar_distance) * step, 1)
                    dd["y"] = round(dd["y"] + (vy / planar_distance) * step, 1)
                state["dive_drone"]["speed_kmh"] = round(min(680.0, state["dive_drone"]["speed_kmh"] + 25.0), 1)
                state["dive_drone"]["alt_m"] = round(max(80.0, state["dive_drone"]["alt_m"] - 55.0), 1)
                state["dive_drone"]["g_force"] = 4.8
                if state["dive_drone"]["alt_m"] <= 100.0:
                    state["dive_drone"]["dive_engaged"] = False
            else:
                state["dive_drone"]["speed_kmh"] = round(max(240.0, state["dive_drone"]["speed_kmh"] - 8.0), 1)
                state["dive_drone"]["alt_m"] = round(min(1250.0, state["dive_drone"]["alt_m"] + 15.0), 1)
                state["dive_drone"]["g_force"] = 1.1
                state["dive_drone"]["x"] = round(600 + 60 * math.sin(time.time() * 0.25), 1)
                state["dive_drone"]["y"] = round(360 + 40 * math.cos(time.time() * 0.2), 1)

            state["gcs"]["tx_packets"] += random.randint(12, 35)
            state["gcs"]["rx_packets"] += random.randint(25, 65)
            state["gcs"]["uplink_rate_kbps"] = round(120 + random.uniform(-5, 12), 1)
            state["gcs"]["downlink_rate_kbps"] = round(880 + random.uniform(-20, 30), 1)

            dd = state["dive_drone"]
            target = state["target"]
            state["telemetry"]["distance_to_target_m"] = round(
                math.hypot(dd["x"] - target["x"], dd["y"] - target["y"]) * 5.0, 1
            )
            acc_noise = (1.0 - (state["satellite"]["signal_quality"] / 100.0)) * 4.0
            state["telemetry"]["positioning_accuracy_cm"] = round(max(0.8, 1.45 + acc_noise + random.uniform(-0.1, 0.1)), 2)
        time.sleep(1.0)

threading.Thread(target=sim_thread, daemon=True).start()

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>{{ custom_texts.header_title }}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        :root {
            --bg: #050b14; --panel: rgba(9, 20, 32, 0.88);
            --cyan: #00e5ff; --green: #00ffaa; --red: #ff3366; --amber: #ffaa00;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: Tahoma, Segoe UI, sans-serif; }
        body { background: var(--bg); color: #d8f0ff; padding: 12px; height: 100vh; overflow: hidden; }
        header { display: flex; justify-content: space-between; align-items: center; padding: 10px 18px; background: var(--panel); border: 1px solid var(--cyan); border-radius: 8px; margin-bottom: 10px; }
        h1 { font-size: 1.15rem; color: var(--cyan); }
        .grid { display: grid; grid-template-columns: 310px 1fr 310px; gap: 10px; height: calc(100vh - 85px); }
        .col { display: flex; flex-direction: column; gap: 10px; overflow-y: auto; }
        .card { background: var(--panel); border: 1px solid rgba(0, 229, 255, 0.25); border-radius: 6px; padding: 10px; }
        .card-t { font-weight: bold; color: var(--cyan); border-bottom: 1px solid rgba(0, 229, 255, 0.15); padding-bottom: 4px; margin-bottom: 6px; font-size: 0.82rem; display: flex; justify-content: space-between; }
        .row { display: flex; justify-content: space-between; font-size: 0.78rem; margin: 3px 0; color: #a4c8e0; }
        .val { font-weight: bold; color: #fff; font-family: monospace; }
        .btn { width: 100%; padding: 7px; margin-top: 6px; background: rgba(0, 229, 255, 0.15); color: var(--cyan); border: 1px solid var(--cyan); border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 0.75rem; }
        .btn:hover { background: var(--cyan); color: #000; }
        .btn-red { border-color: var(--red); color: var(--red); background: rgba(255, 51, 102, 0.15); }
        .btn-red:hover { background: var(--red); color: #fff; }
        .canvas-box { position: relative; flex: 1; background: #02070d; border: 1px solid var(--cyan); border-radius: 6px; overflow: hidden; }
        canvas { width: 100%; height: 100%; display: block; }
        .t-bar { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; height: 60px; }
        .t-box { background: var(--panel); border: 1px solid rgba(0,229,255,0.25); border-radius: 6px; padding: 6px; text-align: center; }
        .t-lbl { font-size: 0.68rem; color: #88a8c0; }
        .t-val { font-size: 0.95rem; font-weight: bold; color: var(--green); font-family: monospace; }
        .modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.85); z-index: 1000; justify-content: center; align-items: center; }
        .modal-c { background: #0c1c2e; border: 1px solid var(--cyan); padding: 18px; border-radius: 8px; width: 440px; }
        .modal-c input, .modal-c textarea { width: 100%; background: #040d17; border: 1px solid var(--cyan); color: #fff; padding: 5px; margin: 4px 0 8px; border-radius: 4px; font-size: 0.78rem; }
    </style>
</head>
<body>
    <header>
        <div>
            <h1 id="txt_header_title">{{ custom_texts.header_title }}</h1>
            <p id="txt_sub_title" style="font-size:0.75rem; color:#88a8c0;">{{ custom_texts.sub_title }}</p>
        </div>
        <button class="btn" style="width:auto; padding:5px 12px;" onclick="openModal()">⚙ ویرایش متون و عناوین</button>
    </header>

    <div class="grid">
        <!-- ستون راست -->
        <div class="col">
            <div class="card">
                <div class="card-t"><span id="txt_sat_panel_title">{{ custom_texts.sat_panel_title }}</span><span id="sat_status">● فعال</span></div>
                <div class="row"><span>شناسه ماهواره:</span><span class="val" id="sat_name">---</span></div>
                <div class="row"><span>سیگنال GPS:</span><span class="val" id="sat_sig">---</span></div>
                <div class="row"><span>نسبت SNR:</span><span class="val" id="sat_snr">---</span></div>
                <div class="row"><span>موقعیت مداری:</span><span class="val" id="sat_orb">---</span></div>
                <button class="btn" onclick="toggleSat()">⚡ تغییر اتصال ماهواره</button>
            </div>
            <div class="card">
                <div class="card-t"><span id="txt_recon_panel_title">{{ custom_texts.recon_panel_title }}</span><span style="color:var(--cyan);">پنهان‌کار</span></div>
                <div class="row"><span>سطح مقطع RCS:</span><span class="val" id="rcs_val">0.0035 m²</span></div>
                <div class="row"><span>وضعیت استتار:</span><span class="val" id="stealth_status" style="color:var(--green);">رادارگریز فعال</span></div>
                <div class="row"><span>ارتفاع / سرعت:</span><span class="val" id="recon_tele">---</span></div>
                <div class="row"><span>حسگر ژئوفیزیک:</span><span class="val" id="recon_ppm" style="color:var(--amber);">---</span></div>
                <button class="btn" onclick="toggleStealth()">🛡 تغییر وضعیت رادارگریزی</button>
            </div>
        </div>

        <!-- ستون وسط -->
        <div style="display:flex; flex-direction:column; gap:10px;">
            <div class="canvas-box"><canvas id="radar"></canvas></div>
            <div class="t-bar">
                <div class="t-box"><div class="t-lbl">فاصله تا هدف (Distance)</div><div class="t-val" id="disp_dist">---</div></div>
                <div class="t-box"><div class="t-lbl">دقت موقعیت‌یابی (Accuracy)</div><div class="t-val" id="disp_acc">---</div></div>
                <div class="t-box"><div class="t-lbl">همگرایی بهینه‌ساز MOPSO</div><div class="t-val" id="disp_mopso">---</div></div>
                <div class="t-box"><div class="t-lbl">پیوند شبیه‌ساز HIL</div><div class="t-val" style="color:var(--cyan);">SYNCED</div></div>
            </div>
        </div>

        <!-- ستون چپ -->
        <div class="col">
            <div class="card">
                <div class="card-t"><span id="txt_diver_panel_title">{{ custom_texts.diver_panel_title }}</span><span id="diver_st" style="color:var(--green);">آماده</span></div>
                <div class="row"><span>ارتفاع لحظه‌ای:</span><span class="val" id="diver_alt">---</span></div>
                <div class="row"><span>سرعت برداری:</span><span class="val" id="diver_spd">---</span></div>
                <div class="row"><span>فشار دینامیکی:</span><span class="val" id="diver_g">---</span></div>
                <button class="btn btn-red" onclick="triggerDive()">🔻 اجرای مانور شیرجه تهاجمی</button>
            </div>
            <div class="card">
                <div class="card-t"><span id="txt_gcs_panel_title">{{ custom_texts.gcs_panel_title }}</span><span style="color:var(--green);">ONLINE</span></div>
                <div class="row"><span>نرخ TX (ارسال):</span><span class="val" id="gcs_tx">---</span></div>
                <div class="row"><span>نرخ RX (دریافت):</span><span class="val" id="gcs_rx">---</span></div>
                <div class="row"><span>بسته‌های تله‌متری:</span><span class="val" id="gcs_pk">---</span></div>
                <div class="row"><span>تأخیر پینگ شبکه:</span><span class="val" id="gcs_lat">---</span></div>
            </div>
            <div class="card">
                <div class="card-t"><span>پیام وضعیت و هشدار</span></div>
                <p id="txt_alert_text" style="font-size:0.75rem; color:#88a8c0; line-height:1.4;">{{ custom_texts.alert_text }}</p>
            </div>
        </div>
    </div>

    <!-- مودال ویرایش متون -->
    <div class="modal" id="editModal">
        <div class="modal-c">
            <h3 style="color:var(--cyan); margin-bottom:8px; font-size:0.95rem;">ویرایش متون و عناوین</h3>
            <label style="font-size:0.7rem;">رمز عبور ادمین:</label>
            <input type="password" id="m_pass" placeholder="رمز...">
            <label style="font-size:0.7rem;">عنوان اصلی:</label>
            <input type="text" id="m_h_title" value="{{ custom_texts.header_title }}">
            <label style="font-size:0.7rem;">عنوان ماهواره:</label>
            <input type="text" id="m_sat_title" value="{{ custom_texts.sat_panel_title }}">
            <label style="font-size:0.7rem;">عنوان پهپاد شناسایی:</label>
            <input type="text" id="m_recon_title" value="{{ custom_texts.recon_panel_title }}">
            <label style="font-size:0.7rem;">عنوان پهپاد شیرجه‌زن:</label>
            <input type="text" id="m_diver_title" value="{{ custom_texts.diver_panel_title }}">
            <label style="font-size:0.7rem;">عنوان ایستگاه زمینی:</label>
            <input type="text" id="m_gcs_title" value="{{ custom_texts.gcs_panel_title }}">
            <label style="font-size:0.7rem;">متن هشدار:</label>
            <textarea id="m_alert_txt" rows="2">{{ custom_texts.alert_text }}</textarea>
            <div style="display:flex; gap:6px;">
                <button class="btn" onclick="saveTexts()">ذخیره</button>
                <button class="btn btn-red" onclick="closeModal()">انصراف</button>
            </div>
        </div>
    </div>

    <script>
        const canvas = document.getElementById('radar');
        const ctx = canvas.getContext('2d');
        let cd = null;
        let diveAlarmWasNear = false;
        let diveAlarmAudio = null;

        // RELIABLE_DIVE_ALARM_V3
        // مرورگر صدا را فقط پس از تعامل کاربر مجاز می‌داند.
        // این بخش هنگام کلیک روی دکمهٔ مانور، صدا را صرفاً آماده می‌کند؛
        // پخش واقعی فقط در فاصلهٔ <= 100 متر رخ می‌دهد.
        let reliableAlarmArmed = false;
        let reliableAlarmWasNear = false;

        document.addEventListener('click', function (event) {
            const button = event.target && event.target.closest ? event.target.closest('button') : null;
            if (!button) return;

            const label = (button.innerText || button.textContent || '').trim();
            if (!label.includes('شیرجه') && !label.includes('مانور')) return;

            if (!diveAlarmAudio) {
                diveAlarmAudio = new Audio('/dive_alarm.wav');
                diveAlarmAudio.preload = 'auto';
            }

            // باز کردن مجوز صوتی در خودِ کلیک کاربر، بدون شنیده‌شدن صدا
            diveAlarmAudio.volume = 0;
            const unlock = diveAlarmAudio.play();
            if (unlock && typeof unlock.then === 'function') {
                unlock.then(function () {
                    diveAlarmAudio.pause();
                    diveAlarmAudio.currentTime = 0;
                    diveAlarmAudio.volume = 0.9;
                    reliableAlarmArmed = true;
                    reliableAlarmWasNear = false;
                }).catch(function () {
                    reliableAlarmArmed = true;
                });
            } else {
                reliableAlarmArmed = true;
            }
        }, true);

        function reliableDiveAlarm(isNearTarget) {
            if (!isNearTarget) {
                reliableAlarmWasNear = false;
                return;
            }

            // فقط در لحظهٔ ورود به محدودهٔ تعریف‌شده، نه زودتر و نه تکراری
            if (reliableAlarmArmed && !reliableAlarmWasNear) {
                if (!diveAlarmAudio) diveAlarmAudio = new Audio('/dive_alarm.wav');
                diveAlarmAudio.pause();
                diveAlarmAudio.currentTime = 0;
                diveAlarmAudio.volume = 0.9;
                diveAlarmAudio.play().catch(function () {});
            }
            reliableAlarmWasNear = true;
        }

        function updateDiveAlarm() {
            if (!cd || !cd.telemetry) return;
            const distance = Number(cd.telemetry.distance_to_target_m);
            const nearTarget = Number.isFinite(distance) && distance <= 100;

            // آژیر فقط یک‌بار، در لحظه ورود به محدوده 100 متر پخش می‌شود.
            if (nearTarget && !diveAlarmWasNear) {
                if (!diveAlarmAudio) {
                    diveAlarmAudio = new Audio('/dive_alarm.wav');
                    diveAlarmAudio.volume = 0.9;
                }
                diveAlarmAudio.currentTime = 0;
                diveAlarmAudio.play().catch(() => {});
            }
            diveAlarmWasNear = nearTarget;
        }

        function resize() {
            canvas.width = canvas.parentElement.clientWidth;
            canvas.height = canvas.parentElement.clientHeight;
        }
        window.addEventListener('resize', resize);
        resize();

        function draw() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            const w = canvas.width, h = canvas.height;

            // رادار
            ctx.strokeStyle = 'rgba(0, 229, 255, 0.15)';
            for(let r = 40; r < Math.max(w, h); r += 70) {
                ctx.beginPath(); ctx.arc(w/2, h/2, r, 0, Math.PI*2); ctx.stroke();
            }
            ctx.beginPath(); ctx.moveTo(0, h/2); ctx.lineTo(w, h/2); ctx.moveTo(w/2, 0); ctx.lineTo(w/2, h); ctx.stroke();

            // پویشگر
            const a = (Date.now() * 0.0012) % (Math.PI * 2);
            ctx.fillStyle = 'rgba(0, 255, 170, 0.12)';
            ctx.beginPath(); ctx.moveTo(w/2, h/2); ctx.arc(w/2, h/2, 350, a, a + 0.35); ctx.closePath(); ctx.fill();

            if (!cd) return;

            // ایستگاه زمینی
            const gx = 60, gy = h - 50;
            ctx.fillStyle = '#00e5ff'; ctx.fillRect(gx-8, gy-8, 16, 16);
            ctx.fillStyle = '#fff'; ctx.font = '10px Tahoma'; ctx.fillText("GCS مرکزی", gx-20, gy+20);

            // ماهواره
            if (cd.satellite.active) {
                const sa = (cd.satellite.orbit_deg * Math.PI) / 180;
                const sx = w/2 + 220 * Math.cos(sa), sy = h/2 + 120 * Math.sin(sa);
                ctx.fillStyle = '#ffaa00'; ctx.fillRect(sx-6, sy-6, 12, 12);
                ctx.fillStyle = '#00e5ff'; ctx.fillRect(sx-16, sy-3, 8, 6); ctx.fillRect(sx+8, sy-3, 8, 6);
                ctx.fillText("SAT-IR (GPS)", sx-30, sy-10);
                ctx.strokeStyle = 'rgba(0, 255, 170, 0.2)'; ctx.setLineDash([3,3]);
                ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(gx, gy); ctx.stroke(); ctx.setLineDash([]);
            }

            // پهپاد شناسایی
            const rx = (cd.stealth_drone.x / 800) * w, ry = (cd.stealth_drone.y / 600) * h;
            ctx.fillStyle = cd.stealth_drone.stealth_active ? 'rgba(0, 229, 255, 0.4)' : '#00e5ff';
            ctx.beginPath(); ctx.arc(rx, ry, 10, 0, Math.PI*2); ctx.fill();
            ctx.fillText("Recon (Stealth)", rx-30, ry-12);

            // پهپاد شیرجه‌زن
            const dx = (cd.dive_drone.x / 800) * w, dy = (cd.dive_drone.y / 600) * h;
            ctx.fillStyle = cd.dive_drone.dive_engaged ? '#ff3366' : '#ffaa00';
            ctx.beginPath(); ctx.moveTo(dx, dy-10); ctx.lineTo(dx+8, dy+8); ctx.lineTo(dx-8, dy+8); ctx.closePath(); ctx.fill();
            ctx.fillText(cd.dive_drone.dive_engaged ? "شیرجه تهاجمی" : "Diver", dx-20, dy+18);
            // === VISUAL_UPGRADE_V2_OVERLAY ===
            const diveDistanceM = Number(cd.telemetry && cd.telemetry.distance_to_target_m);
            const diveNearTarget = Number.isFinite(diveDistanceM) && diveDistanceM <= 100;
            reliableDiveAlarm(diveNearTarget);
            // مختصات هدف واقعی
            const targetX = (cd.target.x / 800) * w;
            const targetY = (cd.target.y / 600) * h;

            // خط ممتد: از پهپاد شیرجه‌زن تا خود هدف
            ctx.save();
            ctx.setLineDash([]);
            ctx.lineWidth = 2;
            ctx.strokeStyle = 'rgba(255, 51, 102, 0.96)';
            ctx.beginPath();
            ctx.moveTo(dx, dy);
            ctx.lineTo(targetX, targetY);
            ctx.stroke();

            // نمایش هدف معدنی
            ctx.fillStyle = '#ff3366';
            ctx.beginPath();
            ctx.arc(targetX, targetY, 7, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = 'rgba(255, 51, 102, 0.60)';
            ctx.lineWidth = 1.2;
            ctx.beginPath();
            ctx.arc(targetX, targetY, 16, 0, Math.PI * 2);
            ctx.stroke();
            ctx.fillStyle = '#ffffff';
            ctx.font = 'bold 11px Tahoma';
            ctx.fillText('هدف معدنی', targetX - 28, targetY + 31);

            // رادار زمینی؛ یک نماد مستقل با برچسب زیر آن
            const radarX = targetX - 92;
            const radarY = targetY + 2;
            ctx.strokeStyle = 'rgba(0, 229, 255, 0.96)';
            ctx.fillStyle = 'rgba(0, 229, 255, 0.18)';
            ctx.lineWidth = 1.4;
            ctx.beginPath();
            ctx.arc(radarX, radarY, 18, Math.PI, Math.PI * 2);
            ctx.lineTo(radarX, radarY);
            ctx.closePath();
            ctx.fill();
            ctx.stroke();
            ctx.beginPath();
            ctx.arc(radarX, radarY, 11, Math.PI, Math.PI * 2);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(radarX, radarY);
            ctx.lineTo(radarX + 14, radarY - 13);
            ctx.stroke();
            ctx.fillStyle = '#ffffff';
            ctx.font = 'bold 11px Tahoma';
            ctx.fillText('رادار', radarX - 16, radarY + 35);

            // برچسب فارسی ایستگاه زمینی
            ctx.fillStyle = '#ffffff';
            ctx.font = 'bold 11px Tahoma';
            ctx.fillText('ایستگاه زمینی', gx - 30, gy + 36);

            // برچسب فارسی پهپاد پنهان‌کار
            ctx.fillText('پهپاد پنهان‌کار', rx - 42, ry + 28);
            ctx.fillStyle = '#00ffaa';
            ctx.font = '10px Tahoma';
            ctx.fillText('در حال استتار', rx - 30, ry + 43);

            // موقعیت ماهواره و برچسب آن
            let linkSatX = null, linkSatY = null;
            if (cd.satellite && cd.satellite.active) {
                const orbit = (cd.satellite.orbit_deg * Math.PI) / 180;
                linkSatX = w / 2 + 220 * Math.cos(orbit);
                linkSatY = h / 2 + 120 * Math.sin(orbit);
                ctx.fillText('ماهواره پشتیبان (۱)', linkSatX - 54, linkSatY + 38);
            }

            // ===== LINKS_V5: سه لینک ردوبدل اطلاعات از پهپاد پنهان‌کار =====
            (function () {
                function metersPerPx() {
                    var t = null, dp = 0, tx2 = null, ty2 = null;
                    if (typeof cd !== 'undefined' && cd && cd.telemetry && typeof cd.telemetry.distance_to_target_m === 'number') {
                        t = cd.telemetry.distance_to_target_m;
                        if (typeof target !== 'undefined' && target && typeof target.x === 'number') { tx2 = target.x; ty2 = target.y; }
                        else if (cd.target && typeof cd.target.x === 'number') { tx2 = cd.target.x; ty2 = cd.target.y; }
                        if (tx2 !== null) { dp = Math.hypot(dy - ty2, dx - tx2); }
                        if (dp > 1 && isFinite(t)) { return Math.min(20, Math.max(0.5, t / dp)); }
                    }
                    return 3;
                }
                function drawLink(x1, y1, x2, y2) {
                    var mpp = metersPerPx();
                    var d = Math.hypot(x2 - x1, y2 - y1) * mpp;
                    ctx.save();
                    ctx.setLineDash([6, 5]);
                    ctx.strokeStyle = 'rgba(0,229,255,0.55)';
                    ctx.lineWidth = 1.4;
                    ctx.beginPath();
                    ctx.moveTo(x1, y1);
                    ctx.lineTo(x2, y2);
                    ctx.stroke();
                    ctx.setLineDash([]);
                    var mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
                    var label = 'لینک ردوبدل اطلاعات | فاصله: ' + d.toFixed(1) + ' متر';
                    ctx.font = '9px Tahoma';
                    var tw = ctx.measureText(label).width;
                    ctx.fillStyle = 'rgba(5,11,20,0.78)';
                    ctx.fillRect(mx - tw / 2 - 4, my - 9, tw + 8, 15);
                    ctx.fillStyle = '#7fffd4';
                    ctx.textAlign = 'center';
                    ctx.fillText(label, mx, my + 3);
                    ctx.textAlign = 'left';
                    ctx.restore();
                }
                if (typeof rx !== 'undefined' && typeof ry !== 'undefined' && typeof dx !== 'undefined' && typeof dy !== 'undefined') {
                    drawLink(rx, ry, dx, dy);
                }
                if (typeof linkSatX === 'number' && typeof linkSatY === 'number') {
                    drawLink(rx, ry, linkSatX, linkSatY);
                }
                if (typeof gx !== 'undefined' && typeof gy !== 'undefined') {
                    drawLink(rx, ry, gx, gy);
                }
            })();
            ctx.setLineDash([]);

            // هالهٔ استتار پهپاد شیرجه‌زن
            if (cd.dive_drone.stealth_active === true) {
                ctx.strokeStyle = 'rgba(0, 255, 170, 0.86)';
                ctx.lineWidth = 1.5;
                ctx.setLineDash([4, 3]);
                ctx.beginPath();
                ctx.arc(dx, dy, 19, 0, Math.PI * 2);
                ctx.stroke();
                ctx.setLineDash([]);
            }

            // برچسب‌های پهپاد شیرجه‌زن
            ctx.fillStyle = '#ffffff';
            ctx.font = 'bold 11px Tahoma';
            ctx.fillText('پهپاد شیرجه‌زن', dx - 42, dy + 33);
            ctx.fillStyle = '#00ffaa';
            ctx.font = '10px Tahoma';
            ctx.fillText(
                diveNearTarget ? 'در حال شیرجه زدن' : 'در حال رادارگریزی',
                dx - 42,
                dy + 47
            );
            ctx.restore();

        }

        async function fetchTel() {
            try {
                const res = await fetch('/api/telemetry');
                cd = await res.json();
                updateDiveAlarm();
                document.getElementById('sat_name').innerText = cd.satellite.name;
                document.getElementById('sat_sig').innerText = cd.satellite.signal_quality + ' %';
                document.getElementById('sat_snr').innerText = cd.satellite.snr_db + ' dB';
                document.getElementById('sat_orb').innerText = cd.satellite.orbit_deg.toFixed(1) + '°';
                document.getElementById('sat_status').innerText = cd.satellite.active ? '● قفل' : '○ قطع';
                document.getElementById('sat_status').style.color = cd.satellite.active ? 'var(--green)' : 'var(--red)';

                document.getElementById('rcs_val').innerText = cd.stealth_drone.rcs_m2 + ' m²';
                document.getElementById('stealth_status').innerText = cd.stealth_drone.stealth_active ? 'رادارگریز فعال' : 'آشکار';
                document.getElementById('stealth_status').style.color = cd.stealth_drone.stealth_active ? 'var(--green)' : 'var(--amber)';
                document.getElementById('recon_tele').innerText = cd.stealth_drone.alt_m + 'm | ' + cd.stealth_drone.speed_kmh + 'km/h';
                document.getElementById('recon_ppm').innerText = cd.stealth_drone.mineral_anomaly_ppm + ' PPM';

                document.getElementById('diver_alt').innerText = cd.dive_drone.alt_m + ' m';
                document.getElementById('diver_spd').innerText = cd.dive_drone.speed_kmh + ' km/h';
                document.getElementById('diver_g').innerText = cd.dive_drone.g_force + ' G';
                document.getElementById('diver_st').innerText = cd.dive_drone.dive_engaged ? 'شیرجه تهاجمی' : 'آماده';
                document.getElementById('diver_st').style.color = cd.dive_drone.dive_engaged ? 'var(--red)' : 'var(--green)';

                document.getElementById('gcs_tx').innerText = cd.gcs.uplink_rate_kbps + ' kbps';
                document.getElementById('gcs_rx').innerText = cd.gcs.downlink_rate_kbps + ' kbps';
                document.getElementById('gcs_pk').innerText = cd.gcs.tx_packets + ' / ' + cd.gcs.rx_packets;
                document.getElementById('gcs_lat').innerText = cd.gcs.latency_ms + ' ms';

                document.getElementById('disp_dist').innerText = cd.telemetry.distance_to_target_m.toFixed(1) + ' m';
                document.getElementById('disp_acc').innerText = '± ' + cd.telemetry.positioning_accuracy_cm.toFixed(2) + ' cm';
                document.getElementById('disp_mopso').innerText = cd.telemetry.mopso_cost.toFixed(4);

                draw();
            } catch(e) {}
        }

        setInterval(fetchTel, 1000);
        setInterval(draw, 50);

        async function toggleSat() { await fetch('/api/toggle_satellite', {method:'POST'}); fetchTel(); }
        async function toggleStealth() { await fetch('/api/toggle_stealth', {method:'POST'}); fetchTel(); }
        async function triggerDive() {
    try {
        const snd = new Audio('/dive_alarm.wav');
        snd.volume = 0.9;
        snd.play().catch(function(){});
    } catch(e) {}
    await fetch('/api/trigger_dive', {method:'POST'});
    fetchTel();
}
        function openModal() { document.getElementById('editModal').style.display = 'flex'; }
        function closeModal() { document.getElementById('editModal').style.display = 'none'; }

        async function saveTexts() {
            const pass = document.getElementById('m_pass').value;
            const payload = {
                password: pass,
                texts: {
                    header_title: document.getElementById('m_h_title').value,
                    sat_panel_title: document.getElementById('m_sat_title').value,
                    recon_panel_title: document.getElementById('m_recon_title').value,
                    diver_panel_title: document.getElementById('m_diver_title').value,
                    gcs_panel_title: document.getElementById('m_gcs_title').value,
                    alert_text: document.getElementById('m_alert_txt').value
                }
            };
            const res = await fetch('/api/update_text', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const r = await res.json();
            if (r.status === 'ok') { alert('با موفقیت ذخیره شد'); location.reload(); }
            else { alert('خطا: ' + (r.msg || 'رمز عبور نادرست است')); }
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, custom_texts=state["custom_texts"])

@app.route('/api/telemetry', methods=['GET'])
def get_tel():
    with db_lock:
        return jsonify(state)

@app.route('/api/toggle_satellite', methods=['POST'])
def toggle_sat():
    with db_lock:
        state["satellite"]["active"] = not state["satellite"]["active"]
    return jsonify({"status": "ok"})

@app.route('/api/toggle_stealth', methods=['POST'])
def toggle_stealth():
    with db_lock:
        state["stealth_drone"]["stealth_active"] = not state["stealth_drone"]["stealth_active"]
    return jsonify({"status": "ok"})

@app.route('/api/trigger_dive', methods=['POST'])
def trigger_dive():
    with db_lock:
        state["dive_drone"]["dive_engaged"] = True
    return jsonify({"status": "ok"})

@app.route('/api/update_text', methods=['POST'])
def update_text():
    req = request.get_json(force=True)
    if req.get("password") != ADMIN_PASS:
        return jsonify({"status": "error", "msg": "رمز عبور نادرست است (admin123)"}), 403
    with db_lock:
        for k, v in req.get("texts", {}).items():
            if k in state["custom_texts"]:
                state["custom_texts"][k] = v
    return jsonify({"status": "ok"})

import os, wave, struct
ALARM_PATH = "/opt/dive_alarm.wav"

def _ensure_alarm_wav():
    if os.path.exists(ALARM_PATH):
        return
    import math as _m
    sr = 22050
    dur = 3.0
    n = int(sr * dur)
    frames = bytearray()
    for i in range(n):
        t = i / sr
        seg = int(t * 4) % 2
        freq = 900.0 if seg == 0 else 1450.0
        env = min(1.0, t / 0.05) * min(1.0, (dur - t) / 0.15)
        val = int(0.38 * 32767 * _m.sin(2 * _m.pi * freq * t) * env)
        frames += struct.pack("<h", val)
    with wave.open(ALARM_PATH, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(frames))

@app.route("/dive_alarm.wav")
def dive_alarm():
    _ensure_alarm_wav()
    return send_file(ALARM_PATH, mimetype="audio/wav")

_ensure_alarm_wav()

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8181, debug=False)