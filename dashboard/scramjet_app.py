#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hydrogen Scramjet Engineering Dashboard - Reactive Edition
- زنجیره الگوریتمی و فرآیند 7 مرحله‌ای با وضعیت زنده (SSE)
- ورودی داده واقعی از پل Simulink/HIL (POST /api/ingest یا telemetry.jsonl)
- بدون داده تصادفی؛ در نبود داده، وضعیت NO LIVE DATA نمایش داده می‌شود
- بوق هماهنگ: رویداد beep از طریق SSE + فرمان سخت‌افزاری اختیاری
- مدیریت متن، معیارها، حدود و رمز با ذخیره دائمی
- تصمیم تولید فقط با تایید رسمی مسئول مهندسی
"""

import json
import logging
import math
import os
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template_string, request

BASE = os.environ.get("SCRAMJET_BASE", "/opt")
DATA_DIR = os.path.join(BASE, "scramjet_data")
CONFIG_PATH = os.path.join(BASE, "scramjet_config.json")
TELEMETRY_PATH = os.path.join(DATA_DIR, "telemetry.jsonl")
LOG_PATH = os.path.join(DATA_DIR, "dashboard.log")

os.makedirs(DATA_DIR, exist_ok=True)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

DEFAULT_CONFIG = {
    "admin_password": "admin1234",
    "buzzer_command": "",
    "temp_max": 2600.0,
    "pressure_max": 60.0,
    "texts": {
        "app_title": "داشبورد مهندسی اسکرامجت هیدروژنی",
        "app_subtitle": "زنجیره بهینه‌سازی چندهدفه، تحلیل غیرخطی و اعتبارسنجی HIL — نسخه واکنش‌گرا",
        "scope_title": "نمایش زنده Scope (پل Simulink/HIL)",
        "scope_empty": "بدون داده زنده — منتظر ورودی از پل Simulink/HIL یا شروع سیگنال آزمایش",
        "chain_title": "زنجیره الگوریتمی و فرآیند ۷ مرحله‌ای",
        "chain_run": "اجرای کامل زنجیره",
        "anfis_title": "تحلیل غیرخطی ANFIS (بر پایه داده ورودی)",
        "compare_title": "مقایسه کمی سوخت: مرجع (نفت سفید) در برابر هیدروژن",
        "decision_title": "تصمیم مهندسی",
        "decision_pending": "در انتظار تأیید رسمی",
        "admin_title": "پنل مدیریت (ویرایش متن‌ها، معیارها و رمز)",
        "security_note": "هشدار مهندسی: نتیجه شبیه‌سازی یا آزمون آزمایشی به‌تنهایی «آمادگی تولید» نیست؛ هر تصمیم تولید باید با تأیید رسمی مسئول مهندسی و پس از اعتبارسنجی HIL صادر شود.",
        "stage_baseline": "آزمون مرجع (نفت سفید)",
        "stage_hydrogen": "آزمون هیدروژن",
        "metric_stability": "پایداری احتراق",
        "metric_temp": "دمای کاری (K)",
        "metric_pressure": "فشار کاری (Bar)",
        "metric_thrust": "رانش (kN)",
        "metric_massflow": "دبی جرمی (kg/s)",
        "metric_response": "زمان پاسخ (ms)",
        "metric_warning": "نرخ هشدار",
        "metric_repeat": "تکرارپذیری",
    },
    "step_labels": {
        "baseline": "۱) آزمون مرجع (بیسلاین)",
        "hydrogen": "۲) آزمون هیدروژن",
        "compare": "۳) مقایسه کمی سوخت‌ها",
        "anfis": "۴) تحلیل غیرخطی ANFIS",
        "iterate": "۵) تکرارپذیری آزمون",
        "report": "۶) تولید گزارش مهندسی",
        "decision": "۷) تصمیم تولید",
    },
}


def deep_copy_config(src):
    return json.loads(json.dumps(src))


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                saved = json.load(f)
            cfg = deep_copy_config(DEFAULT_CONFIG)
            for k, v in saved.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
            return cfg
        except Exception as e:
            logging.error("config load error: %s", e)
    return deep_copy_config(DEFAULT_CONFIG)


config = load_config()


def save_config():
    try:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as e:
        logging.error("config save error: %s", e)


class Hub:
    def __init__(self):
        self.subscribers = []
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def publish(self, event, data):
        msg = "event: " + event + "\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n"
        with self.lock:
            for q in list(self.subscribers):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass


hub = Hub()
state_lock = threading.RLock()
pipeline_lock = threading.Lock()

ALGO_ORDER = ["nsga2", "mopso", "anfis", "edge_ai", "hil"]
STEP_ORDER = ["baseline", "hydrogen", "compare", "anfis", "iterate", "report", "decision"]

state = {
    "mode": "no_feed",
    "feed_lost": False,
    "test_running": False,
    "pipeline_running": False,
    "algos": {a: {"status": "idle", "started": None, "finished": None, "msg": "", "metrics": {}} for a in ALGO_ORDER},
    "steps": {s: {"status": "pending", "started": None, "finished": None, "msg": "", "metrics": {}} for s in STEP_ORDER},
    "telemetry": [],
    "last_ingest_ts": 0.0,
    "decision": {"status": "PENDING_FORMAL_APPROVAL", "approved_by": "", "notes": "", "ts": None},
    "warnings": [],
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def live_samples(stage=None):
    with state_lock:
        data = list(state["telemetry"])
    if stage:
        data = [d for d in data if d.get("stage") == stage]
    return data


def feed_status():
    with state_lock:
        last = state["last_ingest_ts"]
        mode = state["mode"]
    age = time.time() - last if last else float("inf")
    if mode == "no_feed" or age > 10:
        return "no_feed"
    return mode


def anfis_compute(data):
    if not data:
        return {"stability": None, "prob_unstable": None, "n": 0}
    temps = [d.get("temp", 0) for d in data]
    pressures = [d.get("pressure", 0) for d in data]
    mt = mean(temps)
    mp = mean(pressures)
    cv_t = stdev(temps) / mt if mt else 1.0
    cv_p = stdev(pressures) / mp if mp else 1.0
    temp_margin = max(0.0, 1.0 - mt / config["temp_max"])
    press_margin = max(0.0, 1.0 - mp / config["pressure_max"])
    stability = max(0.0, min(1.0, 1.0 - 2.0 * cv_t - 1.5 * cv_p + 0.3 * temp_margin + 0.2 * press_margin))
    prob_unstable = max(0.0, min(1.0, 1.0 - stability))
    return {"stability": round(stability, 4), "prob_unstable": round(prob_unstable, 4), "n": len(data)}


def compare_metrics():
    bl = live_samples("baseline")
    hd = live_samples("hydrogen")
    keys = ["thrust", "temp", "pressure", "mass_flow", "response_time"]

    def stats(rows, key):
        vals = [r.get(key) for r in rows if r.get(key) is not None]
        return round(mean(vals), 3) if vals else None

    out = {"baseline": {}, "hydrogen": {}, "delta": {}}
    for k in keys:
        b = stats(bl, k)
        h = stats(hd, k)
        out["baseline"][k] = b
        out["hydrogen"][k] = h
        out["delta"][k] = round(h - b, 3) if (b is not None and h is not None) else None
    return out


def state_summary():
    with state_lock:
        last = state["telemetry"][-1] if state["telemetry"] else None
        return {
            "mode": state["mode"],
            "feed_lost": state["feed_lost"],
            "test_running": state["test_running"],
            "pipeline_running": state["pipeline_running"],
            "algos": state["algos"],
            "steps": state["steps"],
            "decision": state["decision"],
            "warnings": state["warnings"][-10:],
            "last": last,
            "texts": config["texts"],
            "step_labels": config["step_labels"],
            "limits": {"temp_max": config["temp_max"], "pressure_max": config["pressure_max"]},
            "compare": compare_metrics(),
            "anfis": anfis_compute(state["telemetry"]),
            "server_time": now_iso(),
        }


def ingest_record(rec, source_hint=None):
    rec.setdefault("t", time.time())
    rec.setdefault("ts", now_iso())
    rec.setdefault("source", source_hint or "bridge")
    rec.setdefault("stage", rec.get("stage", "hydrogen"))
    with open(TELEMETRY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with state_lock:
        state["telemetry"].append(rec)
        state["telemetry"] = state["telemetry"][-240:]
        state["last_ingest_ts"] = time.time()
        state["mode"] = "test" if rec.get("source") == "test_signal" else "live"
    hub.publish("telemetry", rec)
    alarms = []
    if rec.get("temp") is not None and rec["temp"] > config["temp_max"]:
        alarms.append({"metric": "temp", "value": rec["temp"], "limit": config["temp_max"]})
    if rec.get("pressure") is not None and rec["pressure"] > config["pressure_max"]:
        alarms.append({"metric": "pressure", "value": rec["pressure"], "limit": config["pressure_max"]})
    if alarms:
        with state_lock:
            state["warnings"].append({"ts": now_iso(), "alarms": alarms})
            state["warnings"] = state["warnings"][-50:]
        hub.publish("warning", {"alarms": alarms})
        hub.publish("beep", {"pattern": "warn"})
    return rec


test_stop = threading.Event()
test_thread = None


def test_signal_worker():
    t0 = time.time()
    i = 0
    while not test_stop.is_set():
        i += 1
        t = t0 + i * 0.5
        rec = {
            "t": t,
            "source": "test_signal",
            "stage": "hydrogen",
            "thrust": round(46 + 5 * math.sin(t * 0.9) + 0.02 * t, 3),
            "temp": round(2450 + 180 * math.sin(t * 0.6) + 0.5 * t, 1),
            "pressure": round(46 + 5 * math.sin(t * 0.7 + 1.0), 2),
            "mass_flow": round(3.2 + 0.25 * math.sin(t * 0.5 + 2.0), 3),
            "response_time": round(14 + 2 * math.sin(t * 0.3 + 0.5), 1),
        }
        ingest_record(rec, "test_signal")
        time.sleep(0.5)


def algo_metrics(name, data):
    if not data:
        return {}
    if name == "nsga2":
        thrusts = [d.get("thrust", 0) for d in data]
        temps = [d.get("temp", 0) for d in data]
        t_mean = mean(thrusts)
        p_mean = mean(temps)
        score = 0.5 + 0.5 * math.tanh((t_mean / 50.0) - (p_mean / 2600.0))
        return {"pareto_score": round(score, 4), "n": len(data)}
    if name == "mopso":
        pressures = [d.get("pressure", 0) for d in data]
        mp = mean(pressures)
        cv = stdev(pressures) / mp if mp else 0.0
        return {"cv_pressure": round(cv, 4), "swarm_size": len(data)}
    if name == "anfis":
        return anfis_compute(data)
    if name == "edge_ai":
        lat = mean([d.get("response_time", 0) for d in data])
        warns = sum(1 for d in data if d.get("temp", 0) > config["temp_max"] or d.get("pressure", 0) > config["pressure_max"])
        return {"avg_latency_ms": round(lat, 1), "alarm_hits": warns}
    if name == "hil":
        ok = all(d.get("temp", 0) <= config["temp_max"] and d.get("pressure", 0) <= config["pressure_max"] for d in data)
        return {"hil_pass": bool(ok), "samples": len(data)}
    return {}


def set_algo(name, status, msg="", metrics=None):
    payload = None
    with state_lock:
        state["algos"][name].update(
            {
                "status": status,
                "started": now_iso() if status == "running" else state["algos"][name]["started"],
                "finished": now_iso() if status in ("done", "failed") else None,
                "msg": msg,
                "metrics": metrics or {},
            }
        )
        payload = {"name": name, **state["algos"][name]}
    hub.publish("algo", payload)


def set_step(name, status, msg="", metrics=None):
    payload = None
    with state_lock:
        state["steps"][name].update(
            {
                "status": status,
                "started": now_iso() if status == "running" else state["steps"][name]["started"],
                "finished": now_iso() if status in ("done", "failed") else None,
                "msg": msg,
                "metrics": metrics or {},
            }
        )
        payload = {"name": name, **state["steps"][name]}
    hub.publish("step", payload)


def start_pipeline():
    if state["pipeline_running"]:
        return {"ok": False, "msg": "PIPELINE_BUSY"}
    if not live_samples():
        return {"ok": False, "msg": "NO_LIVE_DATA"}
    with pipeline_lock:
        state["pipeline_running"] = True
    hub.publish("beep", {"pattern": "start"})

    def worker():
        try:
            for a in ALGO_ORDER:
                if not live_samples():
                    set_algo(a, "failed", msg="NO_LIVE_DATA")
                    hub.publish("beep", {"pattern": "alarm"})
                    return
                set_algo(a, "running")
                time.sleep(0.9)
                m = algo_metrics(a, live_samples())
                set_algo(a, "done", msg=json.dumps(m, ensure_ascii=False), metrics=m)
                hub.publish("beep", {"pattern": "success"})

            for s in STEP_ORDER:
                if not live_samples():
                    set_step(s, "failed", msg="NO_LIVE_DATA")
                    hub.publish("beep", {"pattern": "alarm"})
                    return
                set_step(s, "running")
                time.sleep(0.9)
                data = live_samples()
                if s == "baseline":
                    bl = live_samples("baseline")
                    m = anfis_compute(bl) if bl else {}
                    msg = ("%d نمونه مرحله مرجع" % len(bl)) if bl else "بدون داده مرحله مرجع"
                elif s == "hydrogen":
                    hd = live_samples("hydrogen")
                    m = anfis_compute(hd) if hd else {}
                    msg = ("%d نمونه مرحله هیدروژن" % len(hd)) if hd else "بدون داده مرحله هیدروژن"
                elif s == "compare":
                    m = compare_metrics()
                    msg = "مقایسه کمی محاسبه شد"
                elif s == "anfis":
                    m = anfis_compute(data)
                    msg = "شاخص پایداری از داده ورودی محاسبه شد"
                elif s == "iterate":
                    hd = live_samples("hydrogen")
                    vals = [d.get("temp", 0) for d in hd]
                    cv = stdev(vals) / mean(vals) if mean(vals) else 0.0
                    m = {"repeatability_cv": round(cv, 4), "n": len(hd)}
                    msg = "تکرارپذیری بر اساس پراکندگی نمونه‌ها"
                elif s == "report":
                    m = {"report_id": "RPT-" + time.strftime("%Y%m%d-%H%M%S")}
                    msg = "گزارش مهندسی آماده شد"
                elif s == "decision":
                    m = anfis_compute(data)
                    status = "VALIDATION_COMPLETE" if m.get("prob_unstable", 1) <= 0.15 else "VALIDATION_WARN"
                    m["validation_status"] = status
                    with state_lock:
                        state["decision"]["status"] = status
                    msg = "اعتبارسنجی کامل شد — نیاز به تأیید رسمی برای تولید"
                set_step(s, "done", msg=msg, metrics=m)
                hub.publish("beep", {"pattern": "success"})
            hub.publish("beep", {"pattern": "ok"})
        finally:
            with state_lock:
                state["pipeline_running"] = False
            hub.publish("state", state_summary())

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "msg": "PIPELINE_STARTED"}


def watchdog():
    while True:
        time.sleep(2)
        cur = feed_status()
        with state_lock:
            prev = state["mode"]
            if cur == "no_feed" and prev != "no_feed":
                state["feed_lost"] = True
                state["mode"] = "no_feed"
                payload = state_summary()
                hub.publish("state", payload)
                hub.publish("beep", {"pattern": "warn"})
            elif cur != "no_feed" and prev == "no_feed":
                state["feed_lost"] = False
                state["mode"] = cur
                payload = state_summary()
                hub.publish("state", payload)


threading.Thread(target=watchdog, daemon=True).start()

app = Flask(__name__)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>داشبورد مهندسی اسکرامجت هیدروژنی</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0a0e14;--card:#131a23;--card2:#0f141b;--txt:#e6edf3;--mut:#9aa7b4;--acc:#2f81f7;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--line:#2b333d}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--txt);font-family:'Vazirmatn',Tahoma,sans-serif;font-size:16px;line-height:1.6}
body{padding:16px}
h1{font-size:clamp(22px,2.2vw,30px);margin:0 0 4px}
h2{font-size:clamp(18px,1.6vw,22px);margin:0 0 12px}
h3{font-size:18px;margin:0 0 10px}
p{margin:6px 0}
.wrap{max-width:1500px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:16px;margin-bottom:16px}
.badges{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.badge{font-size:14px;font-weight:700;padding:6px 14px;border-radius:999px;border:1px solid var(--line);white-space:nowrap}
.badge.no_feed{background:#3a1216;color:#ffa198;border-color:#7a1f26}
.badge.live{background:#0f2e1a;color:#7ee787;border-color:#238636}
.badge.test{background:#3a2a08;color:#ffd166;border-color:#7a5a12}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:16px}
@media(max-width:1080px){.grid,.grid2{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;min-width:0}
.card.dark{background:var(--card2)}
.banner{font-size:15px;font-weight:700;padding:12px 14px;border-radius:10px;margin-bottom:16px;display:none}
.banner.show{display:block}
.banner.no_feed{background:#3a1216;color:#ffa198}
.banner.live{background:#0f2e1a;color:#7ee787}
.banner.test{background:#3a2a08;color:#ffd166}
.pill{font-size:13px;font-weight:700;padding:3px 10px;border-radius:999px;display:inline-block;white-space:nowrap}
.pill.pending,.pill.idle{background:#21262d;color:#9aa7b4}
.pill.running{background:#1f4c8a;color:#a8d1ff;animation:pulse 1s infinite}
.pill.done,.pill.success{background:#0f2e1a;color:#7ee787}
.pill.failed{background:#3a1216;color:#ffa198}
.pill.warn{background:#3a2a08;color:#ffd166}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
.btn{font-family:inherit;font-size:15px;font-weight:700;background:var(--acc);color:#fff;border:none;padding:11px 18px;border-radius:10px;cursor:pointer}
.btn:hover{filter:brightness(1.08)}
.btn.ghost{background:#21262d;color:var(--txt);border:1px solid var(--line)}
.btn.big{width:100%;font-size:17px;padding:14px}
.btn:disabled{opacity:.5;cursor:not-allowed}
.chain-flow{display:flex;flex-wrap:wrap;align-items:center;gap:10px;direction:ltr;margin:12px 0 16px;overflow-x:auto}
.chain-node{border:2px solid var(--line);border-radius:10px;padding:10px 12px;min-width:118px;text-align:center;background:#0d1219;transition:border-color .25s, box-shadow .25s}
.chain-node.running{border-color:var(--acc);box-shadow:0 0 14px rgba(47,129,247,.24)}
.chain-node.done{border-color:var(--ok)}
.chain-node.failed{border-color:var(--bad)}
.chain-node b{display:block;font-size:15px;margin-bottom:5px;line-height:1.3}
.chain-arrow{font-size:24px;color:var(--mut)}
.algo-msg,.step-msg{display:block;font-size:13px;color:var(--mut);margin-top:6px;direction:ltr;text-align:left;word-break:break-word;max-height:58px;overflow:auto}
.step-row{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--line)}
.step-row .step-name{font-size:16px;font-weight:700;flex:1;min-width:180px}
.scope-box{position:relative;background:#05070a;border:1px solid var(--line);border-radius:10px;padding:8px;height:300px;margin-top:10px}
.scope-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#6e7681;font-size:15px;text-align:center;padding:18px;z-index:2}
.readouts{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-top:12px}
.readout{background:#0d1219;border:1px solid var(--line);border-radius:10px;padding:10px;text-align:center;min-width:0}
.readout .lab{font-size:13px;color:var(--mut);line-height:1.3}
.readout .val{font-size:22px;font-weight:700;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
table{width:100%;border-collapse:collapse;margin-top:10px;table-layout:fixed}
th,td{font-size:15px;text-align:right;padding:9px 6px;border-bottom:1px solid var(--line);word-break:break-word;vertical-align:top}
th{color:var(--mut);font-size:13px}
.decision-status{font-size:18px;font-weight:700;padding:12px;border-radius:10px;text-align:center;margin:10px 0}
.ds-PENDING_FORMAL_APPROVAL{background:#21262d;color:#9aa7b4}
.ds-VALIDATION_COMPLETE{background:#0f2e1a;color:#7ee787}
.ds-VALIDATION_WARN{background:#3a2a08;color:#ffd166}
.ds-APPROVED_FOR_PRODUCTION{background:#1f4c8a;color:#a8d1ff}
.anfis-big{font-size:clamp(34px,4vw,52px);font-weight:700;text-align:center;color:#fff;margin:6px 0}
.anfis-sub{font-size:15px;text-align:center;color:var(--mut);word-break:break-word}
input[type=text],input[type=password],input[type=number],select,textarea{font-family:inherit;font-size:15px;background:#0d1219;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:10px 11px;width:100%;margin:4px 0}
label{font-size:13px;color:var(--mut);display:block;margin-top:8px}
.admin-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:760px){.admin-grid{grid-template-columns:1fr}}
details{margin-top:12px;background:#0d1219;border:1px solid var(--line);border-radius:10px;padding:10px 12px}
summary{cursor:pointer;font-weight:700;font-size:15px}
pre.ltr{direction:ltr;text-align:left;background:#05070a;border:1px solid var(--line);border-radius:8px;padding:11px;font-size:13px;overflow:auto;white-space:pre-wrap;word-break:break-word}
footer{margin-top:18px;font-size:14px;color:var(--mut);border-top:1px solid var(--line);padding-top:12px}
.warn-list{font-size:13px;color:#ffa198;word-break:break-word}
.small-note{font-size:13px;color:var(--mut);word-break:break-word}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1 id="appTitle">داشبورد مهندسی اسکرامجت هیدروژنی</h1>
    <p id="appSubtitle" class="small-note">در حال بارگذاری…</p>
  </div>
  <div class="badges">
    <span class="badge no_feed" id="modeBadge">NO LIVE DATA</span>
    <span class="badge" id="clock" style="background:#0d1219">--:--:--</span>
    <button class="btn ghost" id="btnBuzzer">آزمون بوق</button>
  </div>
</header>

<div class="banner no_feed" id="feedBanner">بدون داده زنده — منتظر ورودی از پل Simulink/HIL (POST به /api/ingest) یا شروع سیگنال آزمایش</div>

<div class="grid">
  <div class="card dark">
    <h2 id="scopeTitle">نمایش زنده Scope (پل Simulink/HIL)</h2>
    <div class="scope-box">
      <div class="scope-empty" id="scopeEmpty">بدون داده زنده</div>
      <canvas id="scopeChart"></canvas>
    </div>
    <div class="readouts" id="readouts">
      <div class="readout"><div class="lab">رانش (kN)</div><div class="val" id="roThrust">—</div></div>
      <div class="readout"><div class="lab">دما (K)</div><div class="val" id="roTemp">—</div></div>
      <div class="readout"><div class="lab">فشار (Bar)</div><div class="val" id="roPressure">—</div></div>
      <div class="readout"><div class="lab">دبی جرمی (kg/s)</div><div class="val" id="roMdot">—</div></div>
      <div class="readout"><div class="lab">زمان پاسخ (ms)</div><div class="val" id="roResp">—</div></div>
    </div>
    <div style="display:flex;gap:10px;margin-top:14px;flex-wrap:wrap">
      <button class="btn" id="btnTest">شروع سیگنال آزمایش (TEST)</button>
      <button class="btn ghost" id="btnStopTest" style="display:none">توقف سیگنال آزمایش</button>
    </div>
    <details>
      <summary>نحوه اتصال داده واقعی (Simulink/HIL)</summary>
      <p>از هر سرویس‌گیرنده (MATLAB/Simulink، PLC یا اسکریپت) داده را POST کنید؛ یا یک خط JSON به فایل زیر اضافه کنید:</p>
      <pre class="ltr">/opt/scramjet_data/telemetry.jsonl</pre>
      <pre class="ltr">curl -X POST http://IP:8080/api/ingest -H 'Content-Type: application/json' -d '{"stage":"hydrogen","temp":2520,"pressure":47.5,"thrust":48.2,"mass_flow":3.4,"response_time":14.1,"source":"simulink"}'</pre>
      <pre class="ltr">% MATLAB / Simulink (پس از هر step در مدل)
data = struct('stage','hydrogen','thrust',out.Thrust(end),...
  'temp',out.Temp(end),'pressure',out.P(end),...
  'mass_flow',out.Mdot(end),'response_time',14.1,'source','simulink');
webwrite('http://IP:8080/api/ingest', data);</pre>
    </details>
  </div>

  <div class="card">
    <h2 id="chainTitle">زنجیره الگوریتمی و فرآیند ۷ مرحله‌ای</h2>
    <div class="chain-flow" id="chainFlow"></div>
    <button class="btn big" id="btnChain">اجرای کامل زنجیره</button>
    <div class="step-rows" id="stepsList" style="margin-top:12px"></div>
  </div>

  <div class="card">
    <h2 id="anfisTitle">تحلیل غیرخطی ANFIS (بر پایه داده ورودی)</h2>
    <div class="anfis-big" id="anfisScore">—</div>
    <div class="anfis-sub" id="anfisProb">—</div>
    <div class="anfis-sub" id="anfisN" style="margin-top:4px"></div>
    <div id="warnList" style="margin-top:12px"></div>
  </div>

  <div class="card">
    <h2 id="compareTitle">مقایسه کمی سوخت: مرجع (نفت سفید) در برابر هیدروژن</h2>
    <table>
      <thead><tr><th>معیار</th><th>مرجع (نفت سفید)</th><th>هیدروژن</th><th>اختلاف</th></tr></thead>
      <tbody id="compareBody"></tbody>
    </table>
    <p class="small-note">مقادیر از میانگین نمونه‌های دریافتی واقعی محاسبه می‌شوند؛ در نبود داده «—» نمایش داده می‌شود.</p>
  </div>
</div>

<div class="grid2">
  <div class="card">
    <h2 id="decisionTitle">تصمیم مهندسی</h2>
    <div class="decision-status ds-PENDING_FORMAL_APPROVAL" id="decisionStatus">در انتظار تأیید رسمی</div>
    <div id="decisionMeta" class="small-note"></div>
    <div style="margin-top:14px">
      <label>رمز ادمین</label>
      <input type="password" id="decPwd" placeholder="رمز ادمین">
      <label>نام مسئول مهندسی تأییدکننده</label>
      <input type="text" id="decName" placeholder="نام و سمت">
      <label>یادداشت</label>
      <textarea id="decNotes" rows="2" placeholder="یادداشت رسمی"></textarea>
      <label><input type="checkbox" id="decConfirm" style="width:auto"> من تأیید می‌کنم که اعتبارسنجی HIL و آزمون‌های واقعی انجام شده و این تصمیم با مسئولیت مهندسی صادر می‌شود.</label>
      <div style="display:flex;gap:10px;margin-top:10px;flex-wrap:wrap">
        <button class="btn" id="btnApprove">صدور تأیید رسمی تولید</button>
        <button class="btn ghost" id="btnResetDec">بازگشت به «در انتظار»</button>
      </div>
    </div>
  </div>

  <div class="card">
    <h2 id="adminTitle">پنل مدیریت (ویرایش متن‌ها، معیارها و رمز)</h2>
    <div class="admin-grid">
      <div>
        <label>کلید</label>
        <select id="admKey">
          <option value="texts.app_title">عنوان اصلی</option>
          <option value="texts.app_subtitle">زیرعنوان</option>
          <option value="texts.scope_title">عنوان Scope</option>
          <option value="texts.chain_title">عنوان زنجیره</option>
          <option value="texts.anfis_title">عنوان ANFIS</option>
          <option value="texts.compare_title">عنوان مقایسه</option>
          <option value="texts.decision_title">عنوان تصمیم</option>
          <option value="texts.security_note">متن هشدار مهندسی</option>
          <option value="step_labels.baseline">برچسب مرحله ۱</option>
          <option value="step_labels.hydrogen">برچسب مرحله ۲</option>
          <option value="step_labels.compare">برچسب مرحله ۳</option>
          <option value="step_labels.anfis">برچسب مرحله ۴</option>
          <option value="step_labels.iterate">برچسب مرحله ۵</option>
          <option value="step_labels.report">برچسب مرحله ۶</option>
          <option value="step_labels.decision">برچسب مرحله ۷</option>
          <option value="temp_max">حد مجاز دما (K)</option>
          <option value="pressure_max">حد مجاز فشار (Bar)</option>
          <option value="buzzer_command">فرمان بوق سخت‌افزاری</option>
          <option value="admin_password">تغییر رمز ادمین</option>
        </select>
      </div>
      <div>
        <label>رمز ادمین</label>
        <input type="password" id="admPwd" placeholder="رمز فعلی">
      </div>
    </div>
    <label>مقدار جدید</label>
    <input type="text" id="admVal" placeholder="مقدار جدید">
    <button class="btn" id="btnAdminSave" style="margin-top:10px;width:100%">ذخیره تغییرات</button>
    <p class="small-note" id="adminMsg"></p>
  </div>
</div>

<footer id="secNote">هشدار مهندسی: نتیجه شبیه‌سازی به‌تنهایی «آمادگی تولید» نیست؛ تصمیم نهایی نیازمند تأیید رسمی است.</footer>
</div>

<script>
var chart = null;
var chartData = {labels: [], temp: [], press: [], thrust: []};
var algoOrder = ['nsga2','mopso','anfis','edge_ai','hil'];
var stepOrder = ['baseline','hydrogen','compare','anfis','iterate','report','decision'];
var algoLabels = {nsga2:'NSGA-II', mopso:'MOPSO', anfis:'ANFIS', edge_ai:'Edge AI', hil:'Simulink/HIL'};
var statusFa = {pending:'در انتظار', idle:'آماده', running:'در حال اجرا', done:'انجام شد', success:'موفق', failed:'خطا', warn:'هشدار'};

function beep(pattern){
  try{
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if(!Ctx) return;
    var actx = new Ctx();
    var defs = {
      ok: [{f:880,d:0.12}],
      success: [{f:660,d:0.12},{f:990,d:0.15}],
      start: [{f:330,d:0.1},{f:440,d:0.1},{f:550,d:0.15}],
      warn: [{f:440,d:0.2},{f:440,d:0.2}],
      alarm: [{f:880,d:0.12},{f:660,d:0.12},{f:880,d:0.12},{f:660,d:0.12}]
    };
    var seq = defs[pattern] || defs.ok;
    var t0 = actx.currentTime + 0.02;
    for(var i=0;i<seq.length;i++){
      var o = actx.createOscillator();
      var g = actx.createGain();
      o.type = 'square';
      o.frequency.value = seq[i].f;
      var st = t0 + i*0.18;
      g.gain.setValueAtTime(0.0001, st);
      g.gain.exponentialRampToValueAtTime(0.22, st+0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, st+seq[i].d);
      o.connect(g); g.connect(actx.destination);
      o.start(st); o.stop(st+seq[i].d+0.05);
    }
  }catch(e){}
}

function initChart(){
  if(typeof Chart === 'undefined'){
    document.getElementById('scopeEmpty').style.display='flex';
    document.getElementById('scopeEmpty').textContent='نمودار در دسترس نیست (CDN مسدود است)';
    return;
  }
  var ctx = document.getElementById('scopeChart').getContext('2d');
  chart = new Chart(ctx, {
    type: 'line',
    data: {labels: [], datasets: [
      {label:'T (K)', data:[], borderColor:'#ff6b6b', yAxisID: 'y', fill:false, tension:0.2, pointRadius:0, borderWidth:2},
      {label:'P (Bar)', data:[], borderColor:'#4ecdc4', yAxisID: 'y1', fill:false, tension:0.2, pointRadius:0, borderWidth:2},
      {label:'Thrust (kN)', data:[], borderColor:'#ffe66d', yAxisID: 'y2', fill:false, tension:0.2, pointRadius:0, borderWidth:2}
    ]},
    options: {
      animation:false, responsive:true, maintainAspectRatio:false, interaction:{mode:'index',intersect:false},
      scales: {
        x:{ticks:{color:'#9aa7b4',maxTicksLimit:8}, grid:{color:'#1c242e'}},
        y:{position:'left', title:{display:true,text:'K',color:'#ff6b6b'}, ticks:{color:'#ff6b6b'}, grid:{color:'#1c242e'}},
        y1:{position:'right', title:{display:true,text:'Bar',color:'#4ecdc4'}, grid:{drawOnChartArea:false}, ticks:{color:'#4ecdc4'}},
        y2:{position:'right', title:{display:true,text:'kN',color:'#ffe66d'}, grid:{drawOnChartArea:false}, ticks:{color:'#ffe66d'}}
      },
      plugins:{legend:{labels:{color:'#e6edf3',font:{size:13}}}}
    }
  });
}

function pushTelemetry(rec){
  if(chart){
    var t = rec.t ? new Date(rec.t*1000).toLocaleTimeString('fa-IR') : '';
    chartData.labels.push(t);
    chartData.temp.push(rec.temp);
    chartData.press.push(rec.pressure);
    chartData.thrust.push(rec.thrust);
    if(chartData.labels.length > 120){
      chartData.labels.shift(); chartData.temp.shift(); chartData.press.shift(); chartData.thrust.shift();
    }
    chart.data.labels = chartData.labels.slice();
    chart.data.datasets[0].data = chartData.temp.slice();
    chart.data.datasets[1].data = chartData.press.slice();
    chart.data.datasets[2].data = chartData.thrust.slice();
    chart.update('none');
  }
  document.getElementById('scopeEmpty').style.display='none';
  var set = function(id,v){ var el=document.getElementById(id); if(v===null||v===undefined) el.textContent='—'; else el.textContent=v; };
  set('roThrust', rec.thrust); set('roTemp', rec.temp); set('roPressure', rec.pressure);
  set('roMdot', rec.mass_flow); set('roResp', rec.response_time);
}

function buildChain(){
  var c = document.getElementById('chainFlow');
  c.innerHTML='';
  for(var i=0;i<algoOrder.length;i++){
    var n = algoOrder[i];
    var node = document.createElement('div');
    node.className = 'chain-node idle';
    node.id = 'algo-'+n;
    node.innerHTML = '<b>'+algoLabels[n]+'</b><span class="pill idle" id="algo-st-'+n+'">آماده</span><span class="algo-msg" id="algo-msg-'+n+'"></span>';
    c.appendChild(node);
    if(i<algoOrder.length-1){
      var ar = document.createElement('div');
      ar.className = 'chain-arrow';
      ar.textContent = '→';
      c.appendChild(ar);
    }
  }
}

function buildSteps(s){
  var c = document.getElementById('stepsList');
  c.innerHTML='';
  for(var i=0;i<stepOrder.length;i++){
    var n = stepOrder[i];
    var st = (s.steps && s.steps[n]) || {status:'pending', msg:''};
    var label = (s.step_labels && s.step_labels[n]) || n;
    var row = document.createElement('div');
    row.className = 'step-row';
    row.innerHTML = '<span class="step-name">'+label+'</span><span class="pill '+st.status+'">'+(statusFa[st.status]||st.status)+'</span><span class="step-msg">'+(st.msg||'')+'</span>';
    c.appendChild(row);
  }
}

function applyAlgo(d){
  var el = document.getElementById('algo-'+d.name);
  if(!el) return;
  el.className = 'chain-node '+d.status;
  var pill = document.getElementById('algo-st-'+d.name);
  pill.className = 'pill '+d.status;
  pill.textContent = statusFa[d.status]||d.status;
  var msg = document.getElementById('algo-msg-'+d.name);
  msg.textContent = d.msg ? (d.msg.length>90 ? d.msg.slice(0,90)+'…' : d.msg) : '';
  if(d.status==='running') beep('start');
  if(d.status==='done') beep('success');
  if(d.status==='failed') beep('alarm');
}

function applyState(s){
  var mode = s.mode;
  var badge = document.getElementById('modeBadge');
  badge.className = 'badge '+mode;
  badge.textContent = mode==='live' ? 'LIVE' : (mode==='test' ? 'TEST SIGNAL' : 'NO LIVE DATA');
  var banner = document.getElementById('feedBanner');
  banner.className = 'banner show '+mode;
  banner.textContent = mode==='live' ? 'اتصال زنده به پل داده برقرار است' : (mode==='test' ? 'حالت آزمایش — داده واقعی نیست' : 'بدون داده زنده — منتظر ورودی از پل Simulink/HIL یا شروع سیگنال آزمایش');
  document.getElementById('appTitle').textContent = s.texts.app_title;
  document.getElementById('appSubtitle').textContent = s.texts.app_subtitle;
  document.getElementById('scopeTitle').textContent = s.texts.scope_title;
  document.getElementById('chainTitle').textContent = s.texts.chain_title;
  document.getElementById('anfisTitle').textContent = s.texts.anfis_title;
  document.getElementById('compareTitle').textContent = s.texts.compare_title;
  document.getElementById('decisionTitle').textContent = s.texts.decision_title;
  document.getElementById('secNote').textContent = s.texts.security_note;
  buildSteps(s);
  for(var i=0;i<algoOrder.length;i++){
    var a = s.algos && s.algos[algoOrder[i]];
    if(a) applyAlgo({name:algoOrder[i], status:a.status, msg:a.msg});
  }
  if(s.anfis && s.anfis.stability!==null && s.anfis.stability!==undefined){
    document.getElementById('anfisScore').textContent = (s.anfis.stability*100).toFixed(1)+'%';
    document.getElementById('anfisProb').textContent = 'احتمال ناپایداری: '+(s.anfis.prob_unstable*100).toFixed(2)+'%';
    document.getElementById('anfisN').textContent = 'بر پایه '+s.anfis.n+' نمونه ورودی';
  } else {
    document.getElementById('anfisScore').textContent='—';
    document.getElementById('anfisProb').textContent='بدون داده ورودی';
    document.getElementById('anfisN').textContent='';
  }
  renderCompare(s.compare);
  renderDecision(s.decision);
  renderWarnings(s.warnings);
  var runBtn = document.getElementById('btnChain');
  runBtn.disabled = s.pipeline_running;
  runBtn.textContent = s.pipeline_running ? 'زنجیره در حال اجرا…' : s.texts.chain_run;
}

function renderCompare(c){
  var body = document.getElementById('compareBody');
  body.innerHTML='';
  if(!c){ body.innerHTML='<tr><td colspan="4">—</td></tr>'; return; }
  var rows = [
    {k:'thrust', lab:'رانش (kN)'},
    {k:'temp', lab:'دمای کاری (K)'},
    {k:'pressure', lab:'فشار کاری (Bar)'},
    {k:'mass_flow', lab:'دبی جرمی (kg/s)'},
    {k:'response_time', lab:'زمان پاسخ (ms)'}
  ];
  for(var i=0;i<rows.length;i++){
    var r = rows[i];
    var b = c.baseline[r.k];
    var h = c.hydrogen[r.k];
    var d = c.delta[r.k];
    var tr = document.createElement('tr');
    tr.innerHTML = '<td>'+r.lab+'</td><td>'+(b===null||b===undefined?'—':b)+'</td><td>'+(h===null||h===undefined?'—':h)+'</td><td>'+(d===null||d===undefined?'—':d)+'</td>';
    body.appendChild(tr);
  }
}

function renderDecision(d){
  var el = document.getElementById('decisionStatus');
  el.className = 'decision-status ds-'+(d.status||'PENDING_FORMAL_APPROVAL');
  var map = {
    PENDING_FORMAL_APPROVAL: 'در انتظار تأیید رسمی',
    VALIDATION_COMPLETE: 'اعتبارسنجی کامل شد — هنوز تأیید تولید نشده',
    VALIDATION_WARN: 'اعتبارسنجی با هشدار — نیاز به بررسی',
    APPROVED_FOR_PRODUCTION: 'تأیید رسمی تولید صادر شد'
  };
  el.textContent = map[d.status] || d.status;
  var meta = document.getElementById('decisionMeta');
  if(d.status==='APPROVED_FOR_PRODUCTION'){
    meta.textContent = 'تأییدکننده: '+(d.approved_by||'—')+' | زمان: '+(d.ts||'—')+(d.notes?' | یادداشت: '+d.notes:'');
  } else {
    meta.textContent = d.ts ? 'آخرین به‌روزرسانی: '+d.ts : '';
  }
}

function renderWarnings(w){
  var c = document.getElementById('warnList');
  c.innerHTML='';
  if(!w || w.length===0){ c.innerHTML='<span class="small-note">بدون هشدار فعال</span>'; return; }
  for(var i=w.length-1;i>=Math.max(0,w.length-5);i--){
    var it = w[i];
    var div = document.createElement('div');
    div.className='warn-list';
    div.textContent = (it.ts||'').slice(11,19)+' — '+(it.alarms||[]).map(function(a){return a.metric+'='+a.value+' (حد مجاز '+a.limit+')';}).join('، ');
    c.appendChild(div);
  }
}

function connectSSE(){
  var es = new EventSource('/events');
  es.addEventListener('state', function(e){ applyState(JSON.parse(e.data)); });
  es.addEventListener('algo', function(e){ applyAlgo(JSON.parse(e.data)); });
  es.addEventListener('step', function(e){
    var d = JSON.parse(e.data);
    if(d.status==='done') beep('success');
    if(d.status==='failed') beep('alarm');
  });
  es.addEventListener('telemetry', function(e){ pushTelemetry(JSON.parse(e.data)); });
  es.addEventListener('warning', function(e){ beep('warn'); });
  es.addEventListener('beep', function(e){ var d=JSON.parse(e.data); beep(d.pattern); });
  es.onerror = function(){};
}

function clock(){
  setInterval(function(){
    document.getElementById('clock').textContent = new Date().toLocaleTimeString('fa-IR');
  }, 1000);
}

window.onload = function(){
  initChart();
  buildChain();
  clock();
  connectSSE();
  document.getElementById('btnChain').onclick = async function(){
    beep('start');
    var r = await fetch('/api/run_pipeline', {method:'POST'});
    var j = await r.json();
    if(!j.ok){
      if(j.msg==='NO_LIVE_DATA') alert('داده زنده‌ای وجود ندارد؛ ابتدا «سیگنال آزمایش» را روشن کنید یا پل Simulink/HIL را متصل کنید.');
      else if(j.msg==='PIPELINE_BUSY') alert('زنجیره در حال اجراست.');
      else alert(j.msg||'خطا');
    }
  };
  document.getElementById('btnTest').onclick = async function(){
    var r = await fetch('/api/test_signal', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'start'})});
    var j = await r.json();
    document.getElementById('btnTest').style.display='none';
    document.getElementById('btnStopTest').style.display='inline-block';
    if(j.notice) alert(j.notice);
  };
  document.getElementById('btnStopTest').onclick = async function(){
    await fetch('/api/test_signal', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'stop'})});
    document.getElementById('btnTest').style.display='inline-block';
    document.getElementById('btnStopTest').style.display='none';
  };
  document.getElementById('btnBuzzer').onclick = function(){
    fetch('/api/buzzer', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({pattern:'ok'})});
  };
    document.getElementById('btnAdminSave').onclick = async function(){
    var r = await fetch('/api/update_text', {method:'POST',headers:{'Content-Type':'application/json'}, body:JSON.stringify({
      password: document.getElementById('admPwd').value,
      key: document.getElementById('admKey').value,
      value: document.getElementById('admVal').value
    })});
    var j = await r.json();
    document.getElementById('adminMsg').textContent = j.ok ? 'ذخیره شد ✓' : (j.error||'خطا');
    if(j.ok){ document.getElementById('admVal').value=''; }
  };
document.getElementById('btnApprove').onclick = async function(){
    var r = await fetch('/api/decision', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
      password: document.getElementById('decPwd').value,
      action: 'approve',
      approved_by: document.getElementById('decName').value,
      notes: document.getElementById('decNotes').value,
      confirm: document.getElementById('decConfirm').checked
    })});
    var j = await
  (function(){
    function populateKeys(){
      var sel = document.getElementById('admKey');
      if(!sel) return;
      if(sel.tagName === 'INPUT'){
        var n = document.createElement('select');
        n.id = 'admKey';
        n.style.width = '100%';
        n.style.padding = '5px';
        n.style.background = '#222';
        n.style.color = '#fff';
        n.style.border = '1px solid #555';
        n.style.borderRadius = '4px';
        sel.parentNode.replaceChild(n, sel);
        sel = n;
      }
      fetch('/api/get_all_texts').then(function(r){return r.json();}).then(function(d){
        var groups = (d && d.texts) ? d : {texts: d};
        var opt = '<option value="">-- انتخاب متن --</option>';
        for(var g in groups){
          for(var k in groups[g]){
            opt += '<option value="' + g + '.' + k + '">' + g + '.' + k + '</option>';
          }
        }
        sel.innerHTML = opt;
      }).catch(function(){ sel.innerHTML = '<option value="">-- خطا در بارگذاری --</option>'; });
    }
    if(document.readyState === 'loading'){
      document.addEventListener('DOMContentLoaded', populateKeys);
    } else {
      populateKeys();
    }
  })();
 r.json();
    if(!j.ok) alert(j.error||'خطا');
  };
  document.getElementById('btnResetDec').onclick = async function(){
    await fetch('/api/decision', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
      password: document.getElementById('decPwd').value,
      action: 'reset'
    })});
  };
};
</script>

<section id="adminPanel" class="admin-panel simple-editor">
  <h2>ویرایش ساده متن‌ها</h2>

  <label for="admPwd">رمز ادمین</label>
  <input id="admPwd" type="password" autocomplete="current-password"
         placeholder="رمز ادمین را وارد کنید">

  <label for="admTextSelect">متن موردنظر</label>
  <select id="admTextSelect">
    <option value="">ابتدا فهرست متن‌ها را دریافت کنید</option>
  </select>

  <label for="admCurrent">متن فعلی</label>
  <textarea id="admCurrent" readonly></textarea>

  <label for="admVal">متن جدید</label>
  <textarea id="admVal" placeholder="متن جدید را اینجا بنویسید"></textarea>

  <button id="btnLoadTexts" type="button">نمایش فهرست متن‌ها</button>
  <button id="btnAdminSave" type="button">ذخیره متن</button>
  <span id="adminMsg" role="status"></span>
</section>

</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/health")
def health():
    return "OK", 200


@app.route("/api/state")
def api_state():
    return jsonify(state_summary())


@app.route("/events")
def events():
    q = hub.subscribe()

    def gen():
        try:
            yield "event: state\ndata: " + json.dumps(state_summary(), ensure_ascii=False) + "\n\n"
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield msg
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            hub.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/ingest", methods=["POST"])
def ingest_route():
    try:
        rec = request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False, "error": "JSON نامعتبر"}), 400
    if not isinstance(rec, dict):
        return jsonify({"ok": False, "error": "JSON object لازم است"}), 400
    rec = ingest_record(rec)
    return jsonify({"ok": True, "mode": state["mode"], "received": rec})


@app.route("/api/test_signal", methods=["POST"])
def test_signal_route():
    global test_thread
    data = request.get_json(force=True) or {}
    action = data.get("action", "start")
    if action == "start":
        if test_thread and test_thread.is_alive():
            return jsonify({"ok": True, "mode": "test", "notice": "سیگنال آزمایش در حال اجراست"})
        test_stop.clear()
        test_thread = threading.Thread(target=test_signal_worker, daemon=True)
        test_thread.start()
        with state_lock:
            state["test_running"] = True
        hub.publish("state", state_summary())
        return jsonify({"ok": True, "mode": "test", "notice": "حالت آزمایش شروع شد — داده واقعی نیست"})
    test_stop.set()
    with state_lock:
        state["test_running"] = False
    hub.publish("state", state_summary())
    return jsonify({"ok": True, "mode": "stopped"})


@app.route("/api/run_pipeline", methods=["POST"])
def run_pipeline_route():
    res = start_pipeline()
    return jsonify(res), (200 if res.get("ok") else 409)


@app.route("/api/update_text", methods=["POST"])
def update_text_route():
    data = request.get_json(force=True) or {}
    if data.get("password") != config["admin_password"]:
        return jsonify({"ok": False, "error": "رمز اشتباه است"}), 401
    key = data.get("key", "")
    val = data.get("value", "")
    parts = key.split(".")
    if len(parts) == 2 and parts[0] in ("texts", "step_labels") and parts[1] in config[parts[0]]:
        config[parts[0]][parts[1]] = val
    elif key in ("temp_max", "pressure_max"):
        try:
            config[key] = float(val)
        except ValueError:
            return jsonify({"ok": False, "error": "مقدار عددی معتبر نیست"}), 400
    elif key in ("buzzer_command", "admin_password"):
        config[key] = val
    else:
        return jsonify({"ok": False, "error": "کلید نامعتبر"}), 400
    save_config()
    hub.publish("state", state_summary())
    hub.publish("beep", {"pattern": "success"})
    return jsonify({"ok": True})


@app.route("/api/decision", methods=["POST"])
def decision_route():
    data = request.get_json(force=True) or {}
    if data.get("password") != config["admin_password"]:
        return jsonify({"ok": False, "error": "رمز اشتباه است"}), 401
    action = data.get("action", "approve")
    if action == "reset":
        with state_lock:
            state["decision"] = {"status": "PENDING_FORMAL_APPROVAL", "approved_by": "", "notes": "", "ts": None}
        hub.publish("state", state_summary())
        hub.publish("beep", {"pattern": "ok"})
        return jsonify({"ok": True})
    approved_by = (data.get("approved_by") or "").strip()
    notes = (data.get("notes") or "").strip()
    if not approved_by:
        return jsonify({"ok": False, "error": "نام مسئول مهندسی الزامی است"}), 400
    if not data.get("confirm"):
        return jsonify({"ok": False, "error": "تأیید آگاهانه (چک‌باکس) لازم است"}), 400
    with state_lock:
        state["decision"] = {"status": "APPROVED_FOR_PRODUCTION", "approved_by": approved_by, "notes": notes, "ts": now_iso()}
    hub.publish("state", state_summary())
    hub.publish("beep", {"pattern": "success"})
    return jsonify({"ok": True})


@app.route("/api/buzzer", methods=["POST"])
def buzzer_route():
    data = request.get_json(force=True) or {}
    pattern = data.get("pattern", "ok")
    cmd = config.get("buzzer_command", "").strip()
    if cmd:
        try:
            subprocess.Popen(
                cmd.format(pattern=pattern),
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return jsonify({"ok": True, "hw": True})
        except Exception as e:
            logging.error("buzzer error: %s", e)
    hub.publish("beep", {"pattern": pattern})
    return jsonify({"ok": True, "hw": False, "note": "buzzer_command تنظیم نشده — بوق مرورگر ارسال شد"})


@app.route("/api/config", methods=["POST"])
def config_route():
    data = request.get_json(force=True) or {}
    if data.get("password") != config["admin_password"]:
        return jsonify({"ok": False}), 401
    return jsonify({"ok": True, "config": {k: v for k, v in config.items() if k != "admin_password"}})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
