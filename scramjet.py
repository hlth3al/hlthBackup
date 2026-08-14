# -*- coding: utf-8 -*-
import json
import math
import os
import random
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = 8080

PW_FILE = "scramjet_pw.txt"
NOTE_FILE = "scramjet_note.txt"
STRINGS_FILE = "dashboard_strings.json"

lock = threading.Lock()
sessions = {}
latest = {}
log_lines = []
note_text = ""
PAGE = b""

DEFAULT_STRINGS = {
    "title": "سامانه پایش و کنترل احتراق هیدروژن",
    "subtitle": "ANFIS + یادگیری فدرال (FedAvg) | به‌روزرسانی هر ۲ ثانیه",
    "temp_title": "دمای محفظه",
    "press_title": "فشار محفظه",
    "flow_title": "نرخ تزریق هیدروژن",
    "safety_title": "شاخص پایداری ANFIS",
    "chart_sec": "روند ۶۰ ثانیه اخیر",
    "log_sec": "گزارش زنده سیستم",
    "nodes_sec": "وضعیت گره‌های لبه (Edge Nodes)",
    "fed_sec": "همگرایی فدرال (FedAvg)",
    "eval_sec": "ارزیابی هوشمند",
    "mgmt_sec": "مدیریت",
    "login_btn": "ورود برای ویرایش",
    "edit_btn": "ویرایش همه متن‌ها",
    "pw_btn": "تغییر رمز",
    "sound_btn": "فعال‌سازی صدا",
    "save_btn": "ذخیره تغییرات",
    "note_label": "یادداشت مدیریتی",
    "temp_ok": "محدوده ایمن",
    "temp_hi": "بیش از حد مجاز",
    "press_ok": "محدوده ایمن",
    "press_hi": "بیش از حد مجاز",
    "flow_ok": "Nominal",
    "flow_hi": "بالاتر از حد",
    "alert_msg": "⚠ هشدار: شرایط احتراق در محدوده خطر است!",
    "eval_loading": "در حال بارگذاری...",
    "status_login_ok": "وضعیت ورود: فعال",
    "status_login_no": "وضعیت ورود: غیرفعال",
}

strings = dict(DEFAULT_STRINGS)

def now_ts():
    return time.time()

def ts_str():
    return time.strftime("%H:%M:%S")

def push_log(msg):
    with lock:
        log_lines.append("[" + ts_str() + "] " + msg)
        if len(log_lines) > 120:
            del log_lines[:-120]

def load_strings():
    global strings
    if os.path.exists(STRINGS_FILE):
        try:
            with open(STRINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            merged = dict(DEFAULT_STRINGS)
            merged.update(saved)
            strings = merged
        except Exception:
            strings = dict(DEFAULT_STRINGS)
    else:
        strings = dict(DEFAULT_STRINGS)

def save_strings(new_strs):
    global strings
    merged = dict(DEFAULT_STRINGS)
    merged.update(new_strs)
    strings = merged
    with open(STRINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

def load_password():
    if not os.path.exists(PW_FILE):
        with open(PW_FILE, "w", encoding="utf-8") as f:
            f.write("123456")
        return "123456"
    with open(PW_FILE, "r", encoding="utf-8") as f:
        pw = f.read().strip()
    return pw or "123456"

def save_password(new_pw):
    with open(PW_FILE, "w", encoding="utf-8") as f:
        f.write(new_pw)

def load_note():
    global note_text
    if os.path.exists(NOTE_FILE):
        try:
            with open(NOTE_FILE, "r", encoding="utf-8") as f:
                note_text = f.read()
        except Exception:
            note_text = ""
    else:
        note_text = ""

def save_note(text):
    global note_text
    note_text = text
    with open(NOTE_FILE, "w", encoding="utf-8") as f:
        f.write(text)

def gauss(x, mu, sigma):
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2)

def anfis(t, p, f):
    mt = gauss(t, 2100.0, 350.0)
    mp = gauss(p, 1.80, 0.45)
    mf = gauss(f, 0.73, 0.12)
    safety = (mt * 0.45 + mp * 0.35 + mf * 0.20) * 100.0
    if safety >= 70.0:
        verdict = "SAFE"
    elif safety >= 40.0:
        verdict = "CAUTION"
    else:
        verdict = "DANGER"
    return round(safety, 1), verdict

def alarm_for(t, p, f, safety):
    return safety < 55.0 or t > 2600.0 or p > 2.5 or f > 1.0

CSS_PART = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Tahoma,Vazirmatn,sans-serif;background:#0b0f1a;color:#e8edf7;padding:14px;line-height:1.6}
h1{font-size:18px;text-align:center;margin-bottom:6px;color:#7dd3fc}
.sub{font-size:12px;text-align:center;color:#94a3b8;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.card{background:#111827;border:1px solid #1f2937;border-radius:12px;padding:12px}
.card h3{font-size:12px;color:#93c5fd;margin-bottom:6px}
.card .val{font-size:24px;font-weight:bold}
.card .tag{font-size:11px;color:#64748b}
.good{color:#22c55e}.warn{color:#f59e0b}.bad{color:#ef4444}
.big{grid-column:1/-1}
canvas{width:100%;height:180px;background:#0f172a;border-radius:8px;display:block}
.logbox{background:#0a0f1a;border:1px solid #1f2937;border-radius:8px;height:150px;overflow-y:auto;padding:8px;font-family:monospace;font-size:11px;direction:ltr;text-align:left;white-space:pre-wrap}
.node{display:inline-block;background:#1e293b;border-radius:8px;padding:6px 10px;margin:4px;font-size:12px}
.alertbar{display:none;background:#7f1d1d;border:1px solid #ef4444;color:#fecaca;border-radius:8px;padding:10px;margin-bottom:12px;font-size:14px;font-weight:bold;text-align:center}
button{background:#0ea5e9;color:#082f49;border:0;border-radius:8px;padding:8px 12px;font-size:13px;font-weight:bold;cursor:pointer;margin-top:8px;width:100%}
button.off{background:#64748b;color:#f1f5f9}
.sec{font-size:13px;color:#7dd3fc;margin:14px 0 6px;border-right:3px solid #0ea5e9;padding-right:8px}
.small{font-size:11px;color:#94a3b8;margin-top:4px}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
textarea{width:100%;min-height:110px;background:#0a0f1a;color:#e8edf7;border:1px solid #334155;border-radius:8px;padding:10px;resize:vertical}
.hidden{display:none}
.editable{cursor:pointer;border-radius:4px}
.editable:hover{outline:1px dashed #0ea5e9;background:rgba(14,165,233,0.08)}
.editing{outline:2px solid #0ea5e9;background:rgba(14,165,233,0.12)}
.editbar{position:fixed;bottom:12px;left:12px;right:12px;background:#111827;border:1px solid #0ea5e9;border-radius:12px;padding:10px;display:none;z-index:999;box-shadow:0 4px 20px rgba(0,0,0,0.5)}
"""

def make_html():
    S = strings
    js = build_js()
    html = "<!DOCTYPE html>\n"
    html += '<html lang="fa" dir="rtl">\n'
    html += "<head>\n"
    html += '<meta charset="utf-8">\n'
    html += '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
    html += "<title>" + S["title"] + "</title>\n"
    html += "<style>\n" + CSS_PART + "\n</style>\n"
    html += "</head>\n"
    html += "<body>\n"

    html += '<div id="alertbar" class="alertbar">' + S["alert_msg"] + "</div>\n"

    html += '<h1 class="editable" id="st_title">' + S["title"] + "</h1>\n"
    html += '<div class="sub editable" id="st_subtitle">' + S["subtitle"] + "</div>\n"

    html += '<div class="grid">\n'
    html += '  <div class="card"><h3 class="editable" id="st_temp_title">' + S["temp_title"] + '</h3><div class="val" id="temp">--</div><div class="tag" id="tempTag">...</div></div>\n'
    html += '  <div class="card"><h3 class="editable" id="st_press_title">' + S["press_title"] + '</h3><div class="val" id="press">--</div><div class="tag" id="pressTag">...</div></div>\n'
    html += '  <div class="card"><h3 class="editable" id="st_flow_title">' + S["flow_title"] + '</h3><div class="val" id="flow">--</div><div class="tag" id="flowTag">...</div></div>\n'
    html += '  <div class="card"><h3 class="editable" id="st_safety_title">' + S["safety_title"] + '</h3><div class="val" id="safety">--</div><div class="tag" id="verdict">--</div></div>\n'
    html += "</div>\n"

    html += '<div class="sec editable" id="st_chart_sec">' + S["chart_sec"] + '</div>\n'
    html += '<div class="card big"><canvas id="chart"></canvas></div>\n'

    html += '<div class="sec editable" id="st_log_sec">' + S["log_sec"] + '</div>\n'
    html += '<div class="card big"><div class="logbox" id="log"></div></div>\n'

    html += '<div class="sec editable" id="st_nodes_sec">' + S["nodes_sec"] + '</div>\n'
    html += '<div class="card big" id="nodes">...</div>\n'

    html += '<div class="sec editable" id="st_fed_sec">' + S["fed_sec"] + '</div>\n'
    html += '<div class="card big">\n'
    html += '  <div class="val" id="fed">--</div>\n'
    html += '  <div class="small" id="fedDetail">...</div>\n'
    html += "</div>\n"

    html += '<div class="sec editable" id="st_eval_sec">' + S["eval_sec"] + '</div>\n'
    html += '<div class="card big" id="eval">' + S["eval_loading"] + "</div>\n"

    html += '<div class="sec editable" id="st_mgmt_sec">' + S["mgmt_sec"] + '</div>\n'
    html += '<div class="card big">\n'
    html += '  <div class="row">\n'
    html += '    <button id="loginBtn">' + S["login_btn"] + "</button>\n"
    html += '    <button id="editBtn" class="off">' + S["edit_btn"] + "</button>\n"
    html += '    <button id="pwBtn" class="off">' + S["pw_btn"] + "</button>\n"
    html += '    <button id="soundBtn" class="off">' + S["sound_btn"] + "</button>\n"
    html += "  </div>\n"
    html += "</div>\n"

    html += '<div id="editPanel" class="card big hidden">\n'
    html += '  <h3 class="editable" id="st_note_label">' + S["note_label"] + "</h3>\n"
    html += '  <textarea id="noteBox" placeholder="یادداشت مدیریت..."></textarea>\n'
    html += '  <div class="small" id="adminState">' + S["status_login_no"] + "</div>\n"
    html += "</div>\n"

    html += '<div class="editbar" id="editBar">\n'
    html += '  <div class="row">\n'
    html += '    <button id="saveAllBtn">ذخیره همه تغییرات</button>\n'
    html += '    <button id="cancelEditBtn" class="off">انصراف</button>\n'
    html += "  </div>\n"
    html += "</div>\n"

    html += "<script>\n" + js + "\n</script>\n"
    html += "</body>\n"
    html += "</html>\n"
    return html

def build_js():
    js = r"""
(function(){
  "use strict";

  var temp=[],press=[],flow=[],safety=[],max=60;
  var cv=document.getElementById("chart");
  var cctx=cv.getContext("2d");
  var logEl=document.getElementById("log");
  var soundOn=false,alarm=false,timer=null,audio=null;
  var adminLoggedIn=false;
  var editMode=false;
  var pendingEdits = {};

  var EDITABLE_KEYS = [
    "st_title","st_subtitle","st_temp_title","st_press_title","st_flow_title","st_safety_title",
    "st_chart_sec","st_log_sec","st_nodes_sec","st_fed_sec","st_eval_sec","st_mgmt_sec",
    "st_note_label"
  ];
  var KEY_MAP = {
    st_title:"title", st_subtitle:"subtitle", st_temp_title:"temp_title", st_press_title:"press_title",
    st_flow_title:"flow_title", st_safety_title:"safety_title", st_chart_sec:"chart_sec",
    st_log_sec:"log_sec", st_nodes_sec:"nodes_sec", st_fed_sec:"fed_sec", st_eval_sec:"eval_sec",
    st_mgmt_sec:"mgmt_sec", st_note_label:"note_label"
  };

  function sizeCanvas(){var w=cv.clientWidth||600;cv.width=w;cv.height=180;}
  window.addEventListener("resize",sizeCanvas);
  sizeCanvas();

  function stopBeep(){if(timer!==null){clearInterval(timer);timer=null;}}
  function closeAudio(){stopBeep();if(audio){try{audio.close();}catch(e){}}audio=null;}

  function playOne(){
    if(!soundOn||!alarm) return;
    try{
      if(!audio) audio=new (window.AudioContext||window.webkitAudioContext)();
      if(audio.state==="suspended") audio.resume();
      var o=audio.createOscillator(),g=audio.createGain();
      o.type="sine";o.frequency.value=1000;g.gain.value=0.06;
      o.connect(g);g.connect(audio.destination);o.start();
      setTimeout(function(){try{o.stop();}catch(e){}try{o.disconnect();g.disconnect();}catch(e){}},180);
    }catch(e){}
  }

  function startBeepLoop(){
    if(!soundOn||!alarm||timer!==null)return;
    playOne();
    timer=setInterval(function(){
      if(!soundOn||!alarm){stopBeep();return;}
      playOne();
    },1000);
  }

  var soundBtn=document.getElementById("soundBtn");
  soundBtn.onclick=function(){
    soundOn=!soundOn;
    if(soundOn){
      soundBtn.innerText="قطع صدا";
      soundBtn.classList.remove("off");
      try{
        if(!audio) audio=new (window.AudioContext||window.webkitAudioContext)();
        if(audio.state==="suspended") audio.resume();
        var o=audio.createOscillator(),g=audio.createGain();
        o.type="sine";o.frequency.value=880;g.gain.value=0.05;
        o.connect(g);g.connect(audio.destination);o.start();
        setTimeout(function(){try{o.stop();o.disconnect();g.disconnect();}catch(e){}},180);
      }catch(e){}
      if(alarm) startBeepLoop();
    }else{
      closeAudio();
      soundBtn.innerText="فعال‌سازی صدا";
      soundBtn.classList.add("off");
    }
  };

  function renderLogs(list){
    logEl.innerHTML="";
    list.slice(-50).forEach(function(line){
      var div=document.createElement("div");
      div.textContent=line;
      logEl.appendChild(div);
    });
    logEl.scrollTop=logEl.scrollHeight;
  }

  function draw(){
    var w=cv.width,h=cv.height;
    cctx.clearRect(0,0,w,h);
    cctx.strokeStyle="#334155";
    cctx.lineWidth=1;
    cctx.beginPath();
    for(var i=0;i<=4;i++){var y=h-(h/4)*i;cctx.moveTo(0,y);cctx.lineTo(w,y);}
    cctx.stroke();
    function range(arr){if(arr.length<2)return[0,1];var mn=arr[0],mx=arr[0];for(var i=1;i<arr.length;i++){if(arr[i]<mn)mn=arr[i];if(arr[i]>mx)mx=arr[i];}if(mx-mn<1e-9)mx=mn+1;return[mn,mx];}
    function series(arr,color){
      if(arr.length<2)return;
      var rg=range(arr),mn=rg[0],mx=rg[1];
      cctx.strokeStyle=color;
      cctx.lineWidth=2;
      cctx.beginPath();
      for(var i=0;i<arr.length;i++){
        var x=(i/(max-1))*w;
        var y=h-((arr[i]-mn)/(mx-mn))*(h-24)-12;
        if(i===0)cctx.moveTo(x,y);
        else cctx.lineTo(x,y);
      }
      cctx.stroke();
    }
    series(safety,"#22c55e");
    series(temp,"#f97316");
    series(press,"#38bdf8");
  }

  function postJSON(url, body){
    return fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})});
  }

  function setAdminUI(ok){
    adminLoggedIn=!!ok;
    document.getElementById("editBtn").classList.toggle("off",!adminLoggedIn);
    document.getElementById("pwBtn").classList.toggle("off",!adminLoggedIn);
    document.getElementById("adminState").textContent = adminLoggedIn ? "وضعیت ورود: فعال" : "وضعیت ورود: غیرفعال";
    document.getElementById("editPanel").classList.toggle("hidden",!adminLoggedIn);
  }

  function enableEditing(){
    editMode=true;
    document.getElementById("editBar").style.display="block";
    EDITABLE_KEYS.forEach(function(id){
      var el=document.getElementById(id);
      if(el){
        el.classList.add("editing");
        el.contentEditable="true";
        el.addEventListener("blur",function(){
          var key=KEY_MAP[id];
          pendingEdits[key]=el.innerText.trim();
        });
      }
    });
  }

  function disableEditing(){
    editMode=false;
    document.getElementById("editBar").style.display="none";
    EDITABLE_KEYS.forEach(function(id){
      var el=document.getElementById(id);
      if(el){el.contentEditable="false";el.classList.remove("editing");}
    });
    pendingEdits={};
  }

  document.getElementById("loginBtn").onclick=async function(){
    var pw=prompt("رمز ورود مدیریت را وارد کنید:");
    if(pw===null)return;
    try{
      var r=await postJSON("/login",{password:pw});
      var j=await r.json();
      if(r.ok&&j.ok){
        setAdminUI(true);
        document.getElementById("noteBox").value=j.note||"";
      }else{alert("رمز اشتباه است");}
    }catch(e){alert("خطا در ورود");}
  };

  document.getElementById("editBtn").onclick=function(){
    if(!adminLoggedIn){alert("ابتدا وارد شوید.");return;}
    if(editMode){disableEditing();}else{enableEditing();}
  };

  document.getElementById("cancelEditBtn").onclick=function(){
    disableEditing();
    location.reload();
  };

  document.getElementById("saveAllBtn").onclick=async function(){
    if(!editMode){return;}
    EDITABLE_KEYS.forEach(function(id){
      var el=document.getElementById(id);
      if(el){
        var key=KEY_MAP[id];
        pendingEdits[key]=el.innerText.trim();
      }
    });
    try{
      var r=await postJSON("/save_strings",{strings:pendingEdits});
      var j=await r.json();
      if(r.ok&&j.ok){
        alert("تغییرات روی سرور ذخیره شد.");
        disableEditing();
        location.reload();
      }else{alert("خطا در ذخیره: "+(j.error||""));}
    }catch(e){alert("خطا در ارتباط با سرور");}
  };

  document.getElementById("pwBtn").onclick=async function(){
    if(!adminLoggedIn){alert("ابتدا وارد شوید.");return;}
    var oldpw=prompt("رمز فعلی را وارد کنید:");
    if(oldpw===null)return;
    var newpw=prompt("رمز جدید را وارد کنید:");
    if(newpw===null)return;
    if(!newpw.trim()){alert("رمز جدید خالی است");return;}
    try{
      var r=await postJSON("/change_pw",{old:oldpw,new:newpw});
      var j=await r.json();
      if(r.ok&&j.ok){alert("رمز تغییر کرد");}
      else{alert(j.error||"تغییر رمز ناموفق");}
    }catch(e){alert("خطا در تغییر رمز");}
  };

  async function checkAuth(){
    try{
      var r=await fetch("/auth",{cache:"no-store"});
      var j=await r.json();
      setAdminUI(!!j.ok);
      if(j.ok&&typeof j.note==="string"){
        document.getElementById("noteBox").value=j.note;
      }
    }catch(e){}
  }

  async function tick(){
    try{
      var r=await fetch("/data?t="+Date.now(),{cache:"no-store"});
      if(!r.ok)return;
      var s=await r.json();

      document.getElementById("temp").textContent=s.t.toFixed(0)+" K";
      document.getElementById("press").textContent=s.p.toFixed(2)+" bar";
      document.getElementById("flow").textContent=s.f.toFixed(2)+" kg/s";
      document.getElementById("safety").textContent=s.safety.toFixed(0)+" %";

      var v=document.getElementById("verdict");
      v.textContent=s.verdict;
      v.className=s.verdict==="SAFE"?"good":(s.verdict==="CAUTION"?"warn":"bad");

      var tt=document.getElementById("tempTag");
      tt.textContent=s.t>2600?"بیش از حد مجاز":"محدوده ایمن";
      tt.className=s.t>2600?"bad":"good";

      var pt=document.getElementById("pressTag");
      pt.textContent=s.p>2.5?"بیش از حد مجاز":"محدوده ایمن";
      pt.className=s.p>2.5?"bad":"good";

      var ft=document.getElementById("flowTag");
      ft.textContent=s.f>1.0?"بالاتر از حد":"Nominal";
      ft.className=s.f>1.0?"warn":"good";

      temp.push(s.t);
      press.push(s.p);
      flow.push(s.f);
      safety.push(s.safety);
      if(temp.length>max){
        temp.shift();press.shift();flow.shift();safety.shift();
      }
      draw();

      var nodesHtml="";
      s.nodes.forEach(function(n){
        var cls=n.status==="OK"?"good":(n.status==="WARN"?"warn":"bad");
        nodesHtml+='<span class="node">'+n.id+' — <span class="'+cls+'">'+n.status+'</span> ('+n.score.toFixed(1)+'%)</span>';
      });
      document.getElementById("nodes").innerHTML=nodesHtml;

      document.getElementById("fed").textContent="همگرایی: "+s.fed.round+" — دقت: "+s.fed.acc.toFixed(2)+"%";
      document.getElementById("fedDetail").textContent=s.fed.detail;
      document.getElementById("eval").innerHTML=s.eval;

      document.getElementById("alertbar").style.display=s.alert?"block":"none";
      alarm=!!s.alert;
      if(alarm&&soundOn)startBeepLoop();
      if(!alarm)stopBeep();

      if(s.log&&s.log.length){renderLogs(s.log);}
    }catch(e){}
  }

  checkAuth();
  setInterval(tick,2000);
  tick();
})();
"""
    return js

def build_page():
    global PAGE
    PAGE = make_html().encode("utf-8")

def make_snapshot():
    with lock:
        return dict(latest)

def sim_loop():
    global latest
    push_log("سامانه راه‌اندازی شد.")
    while True:
        t=1900.0+random.uniform(0,400)
        p=1.5+random.uniform(0,0.8)
        f=0.6+random.uniform(0,0.4)
        if random.random()<0.08:
            t+=random.uniform(250,550)
        if random.random()<0.05:
            p+=random.uniform(0.6,1.0)
        if random.random()<0.05:
            f+=random.uniform(0.3,0.55)
        safety,verdict=anfis(t,p,f)
        alert=alarm_for(t,p,f,safety)
        nodes=[]
        for i in range(1,5):
            score=max(0.0,min(100.0,safety+random.uniform(-6,6)))
            status="OK"
            if score<55:
                status="WARN"
            if score<40:
                status="BAD"
            nodes.append({"id":"EDGE-"+str(i).zfill(2),"status":status,"score":score})
        acc=min(99.9,78.0+random.uniform(0,20))
        fed={"round":random.randint(1,60),"acc":acc,"detail":"تجمیع وزن‌های ۴ گره لبه با FedAvg انجام شد؛ وزن‌های محلی در حال همگرایی هستند."}
        if verdict=="SAFE":
            ev='<div class="good">شرایط احتراق در محدوده ایمن است؛ روند پایداری قابل قبول.</div>'
        elif verdict=="CAUTION":
            ev='<div class="warn">شرایط مرزی است؛ کاهش نرخ تزریق یا خنک‌سازی پیشنهاد می‌شود.</div>'
        else:
            ev='<div class="bad">هشدار: شرایط ناپایدار است؛ تزریق هیدروژن کاهش یابد.</div>'
        log_entry="T="+format(t,".0f")+"K | P="+format(p,".2f")+"bar | F="+format(f,".2f")+"kg/s | ANFIS="+format(safety,".1f")+"% | "+verdict+" | alert="+str(alert).lower()
        push_log(log_entry)
        with lock:
            latest={"t":t,"p":p,"f":f,"safety":safety,"verdict":verdict,"alert":alert,"nodes":nodes,"fed":fed,"eval":ev,"note":note_text,"log":list(log_lines)}
        time.sleep(2.0)

def session_ok(cookie_header):
    if not cookie_header:
        return False
    sid=None
    for part in cookie_header.split(";"):
        part=part.strip()
        if part.startswith("sid="):
            sid=part[4:]
            break
    if not sid:
        return False
    with lock:
        exp=sessions.get(sid)
        if not exp:
            return False
        if exp<now_ts():
            sessions.pop(sid,None)
            return False
    return True

def read_json_body(handler):
    try:
        length=int(handler.headers.get("Content-Length","0"))
    except Exception:
        length=0
    raw=handler.rfile.read(length) if length>0 else b"{}"
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}

class H(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):
        pass

    def send_json(self,obj,code=200,cookies=None):
        data=json.dumps(obj,ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(data)))
        if cookies:
            for c in cookies:
                self.send_header("Set-Cookie",c)
        self.end_headers()
        self.wfile.write(data)

    def send_html(self,body):
        self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path=self.path.split("?",1)[0]
        if path=="/data":
            self.send_json(make_snapshot())
            return
        if path=="/auth":
            ok=session_ok(self.headers.get("Cookie",""))
            self.send_json({"ok":ok,"note":note_text if ok else ""})
            return
        self.send_html(PAGE)

    def do_POST(self):
        path=self.path.split("?",1)[0]
        body=read_json_body(self)
        cookie=self.headers.get("Cookie","")
        current_pw=load_password()

        if path=="/login":
            pw=str(body.get("password",""))
            if pw==current_pw:
                sid=secrets.token_hex(16)
                with lock:
                    sessions[sid]=now_ts()+24*3600
                self.send_json({"ok":True,"note":note_text},cookies=["sid="+sid+"; Path=/; SameSite=Strict"])
                push_log("ورود مدیریت موفق بود.")
            else:
                self.send_json({"ok":False,"error":"رمز اشتباه است"},code=403)
            return

        if not session_ok(cookie):
            self.send_json({"ok":False,"error":"عدم دسترسی"},code=403)
            return

        if path=="/change_pw":
            old_pw=str(body.get("old",""))
            new_pw=str(body.get("new",""))
            if old_pw!=current_pw:
                self.send_json({"ok":False,"error":"رمز فعلی اشتباه است"},code=403)
                return
            if not new_pw.strip():
                self.send_json({"ok":False,"error":"رمز جدید خالی است"},code=400)
                return
            save_password(new_pw.strip())
            push_log("رمز مدیریت تغییر کرد.")
            self.send_json({"ok":True})
            return

        if path=="/save_note":
            note=str(body.get("note",""))
            save_note(note)
            push_log("یادداشت مدیریتی ذخیره شد.")
            self.send_json({"ok":True})
            return

        if path=="/save_strings":
            new_strs={}
            try:
                incoming=body.get("strings",{})
                if isinstance(incoming,dict):
                    new_strs=incoming
            except Exception:
                new_strs={}
            save_strings(new_strs)
            build_page()
            push_log("متون داشبورد به‌روزرسانی و روی سرور ذخیره شد.")
            self.send_json({"ok":True})
            return

        self.send_json({"ok":False,"error":"مسیر نامعتبر"},code=404)

if __name__=="__main__":
    load_strings()
    load_note()
    build_page()
    threading.Thread(target=sim_loop,daemon=True).start()
    print("داشبورد با ویرایش متون اجرا شد: http://141.11.107.95:8080")
    ThreadingHTTPServer((HOST,PORT),H).serve_forever()
