"""本机结构证据：算法观察和可编辑解释分开，绝不请求网络。"""
from __future__ import annotations
import array
import copy
import json
import math
import os
import re
import signal
import statistics
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from audio_refs import _exe, _metrics, _whisper_setup, _ALLOWED_FORMATS

ROLES = {"hook", "context", "example", "takeaway"}
HOOK_PATTERNS = {"question", "contrast", "action", "story"}
OPTIONS = ["-protocol_whitelist", "file,pipe", "-format_whitelist", _ALLOWED_FORMATS]


class AnalysisCancelled(Exception):
    pass


def run_command(command, cancel_event=None, timeout=60):
    cancel_event = cancel_event or threading.Event()
    if cancel_event.is_set():
        raise AnalysisCancelled()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                       start_new_session=True)
        except OSError:
            raise ValueError("本机处理工具无法启动，请检查依赖。") from None
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancel_event.wait(.05):
                    raise AnalysisCancelled()
                if time.monotonic()-started > timeout:
                    raise ValueError("本机处理超时。可取消后重试，或改用较短的视频。")
            if cancel_event.is_set():
                raise AnalysisCancelled()
            stdout.seek(0); stderr.seek(0)
            out, err = stdout.read(), stderr.read()
            if process.returncode:
                raise ValueError("本机工具未能解码或分析此文件。可换用可播放的 MP4 后重试。")
            return subprocess.CompletedProcess(command, process.returncode, out, err)
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()


def transcribe_audio(wave, output_dir, cancel_event, language, duration, root):
    setup = _whisper_setup(root)
    engine = os.environ.get("VIDEO_WORKBENCH_WHISPER_CLI")
    model = os.environ.get("VIDEO_WORKBENCH_WHISPER_MODEL")
    if engine and model and Path(engine).is_file() and Path(model).expanduser().is_file():
        setup = (engine, Path(model).expanduser())
    if not setup:
        return {"status":"unavailable", "segments":[], "attempts":[],
                "message":"未找到本机 Whisper CLI 和多语模型；仍可回看、编辑视觉结构。不会下载模型或改用云端。"}
    attempts = []
    # 自动识别失败时再尝试中文；指定语言失败时退回自动识别。
    for lang in dict.fromkeys([language, "zh" if language == "auto" else "auto"]):
        attempts.append(lang)
        output = Path(output_dir)/("transcript-"+lang)
        try:
            run_command([str(setup[0]), "-m", str(setup[1]), "-f", str(wave), "-l", lang,
                         "-oj", "-of", str(output), "-t", "4"], cancel_event, 150)
            raw = json.loads(output.with_suffix(".json").read_text())
            if raw.get("model", {}).get("multilingual") is False:
                continue
            segments = []
            for row in raw.get("transcription", []):
                a, b = float(row["offsets"]["from"])/1000, min(duration, float(row["offsets"]["to"])/1000)
                text = str(row["text"]).strip()
                if math.isfinite(a) and math.isfinite(b) and 0 <= a < b and any(c.isalnum() for c in text):
                    units = len(re.findall(r"[\u3400-\u9fff]|[A-Za-zÀ-ÿ]+", text))
                    segments.append({"start":round(a,3), "end":round(b,3), "text":text,
                                     "units_per_second":round(units/(b-a),2)})
            if segments:
                return {"status":"ready", "segments":segments, "attempts":attempts, "language":lang,
                        "message":"本机自动转写，可能听错中外语混说。文字、时间及语速均需回听核对；不是逐字对齐。"}
        except (ValueError, OSError, KeyError, TypeError):
            continue
    return {"status":"failed", "segments":[], "attempts":attempts,
            "message":"本机转写及替代语言均未得到可用文字。原音仍可播放；请手动记录台词或切换语言重试。"}


def analyze_file(source, output_dir, progress=None, cancel_event=None, language="auto", root=None):
    source, output_dir = Path(source), Path(output_dir)
    cancel_event = cancel_event or threading.Event()
    progress = progress or (lambda percent, message: None)
    if language not in {"auto", "zh", "es", "en"}:
        raise ValueError("转写语言支持自动、中文、西语或英语。")
    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg, ffprobe = _exe("ffmpeg"), _exe("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ValueError("需要本机 FFmpeg 和 FFprobe。安装后可重试；不会自动安装。")
    run = lambda args, timeout=60: run_command(args, cancel_event, timeout)
    progress(4, "读取视频时长与音轨")
    probe = json.loads(run([ffprobe, "-v", "error", *OPTIONS, "-show_streams", "-show_format", "-of", "json", str(source)]).stdout)
    if not any(s.get("codec_type") == "video" for s in probe.get("streams", [])):
        raise ValueError("请选择含画面的视频文件。音频文件仍可在素材库单独试听。")
    duration = float(probe.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("无法取得有效视频时长。请换一个可播放的文件。")
    limit = min(duration, 60.0)
    card = {"schema_version":1, "duration":round(duration,3), "analyzed_duration":round(limit,3),
            "scope_note":"仅分析前 60 秒；播放器可回看全片。" if duration > 60 else "已分析整条视频。",
            "frames":[], "events":[], "beats":[], "warnings":[],
            "audio":{"status":"no_audio", "message":"视频没有音轨，未分析台词、停顿或重音。"},
            "transcript":{"status":"no_audio", "segments":[], "attempts":[], "message":"视频没有音轨。"}}
    events = card["events"]
    progress(10, "检测镜头变化与黑场候选")
    try:
        detection = run([ffmpeg, "-v", "info", "-nostdin", *OPTIONS, "-i", str(source), "-t", str(limit),
                         "-an", "-vf", "scale=320:-2,blackdetect=d=0.12:pix_th=0.10,select='gt(scene,0.28)',showinfo",
                         "-fps_mode", "vfr", "-f", "null", "-"])
        log = detection.stderr.decode(errors="replace")
        shots = sorted(set(round(float(t),3) for t in re.findall(r"pts_time:([\d.]+)", log)))
        for t in shots:
            if .15 < t < limit:
                events.append({"kind":"shot", "start":t, "end":t, "text":"画面变化候选", "method":"画面差异阈值 0.28；运动或字幕也可能触发"})
        for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log):
            events.append({"kind":"transition", "start":float(a), "end":min(limit,float(b)), "text":"黑场／淡出候选", "method":"黑像素检测；不能识别所有转场"})
    except ValueError:
        card["warnings"].append("镜头检测失败；仍提供抽帧和可编辑区段。")
    # OCR 只报告采样时刻，不能将两次采样之间的字幕存在性当作事实。
    ocr = _exe("tesseract")
    ocr_languages = "eng"
    if ocr:
        try:
            languages = run([ocr, "--list-langs"], 10).stdout.decode().splitlines()
            selected = [x for x in ["chi_sim", "eng", "spa"] if x in languages]
            ocr_languages = "+".join(selected) or "eng"
        except ValueError:
            ocr = None
    times = [round(i*max(2,limit/24),3) for i in range(max(1,math.ceil(limit/max(2,limit/24))))]
    previous_text = ""
    for index, t in enumerate(times):
        progress(18+round(36*index/len(times)), f"抽取画面与字幕 {index+1}/{len(times)}")
        name = f"frame-{index:03d}.jpg"
        run([ffmpeg,"-v","error","-nostdin",*OPTIONS,"-ss",str(t),"-i",str(source),"-frames:v","1",
             "-vf","scale='min(960,iw)':-2","-q:v","3","-y",str(output_dir/name)], 20)
        if not (output_dir/name).is_file():
            continue
        card["frames"].append({"time":t,"file":name})
        if ocr:
            try:
                text = run([ocr,str(output_dir/name),"stdout","-l",ocr_languages,"--psm","11"], 15).stdout.decode().strip()
                text = " ".join(text.split())[:600]
                if text and text != previous_text:
                    events.append({"kind":"caption", "start":t, "end":t, "text":text,
                                   "method":"抽帧 OCR，可能含招牌／误字；这是采样时刻，不是字幕起止"})
                previous_text = text
            except ValueError:
                card["warnings"].append("部分画面 OCR 失败；请回看原画面。")
    if not ocr:
        card["warnings"].append("未找到可用 Tesseract；没有识别画面文字。可结合缩略图手动补充。")
    if any(s.get("codec_type") == "audio" for s in probe.get("streams", [])):
        progress(57, "提取本机音轨")
        wave = output_dir/"analysis.wav"
        try:
            try:
                result = run([ffmpeg,"-v","error","-nostdin",*OPTIONS,"-i",str(source),"-t",str(limit),
                              "-map","0:a:0","-vn","-ac","1","-ar","16000","-f","s16le","pipe:1"], 30)
                pcm = result.stdout
                import wave as wave_module
                with wave_module.open(str(wave), "wb") as stream:
                    stream.setparams((1,2,16000,0,"NONE","not compressed")); stream.writeframes(pcm)
            except ValueError:
                run([ffmpeg,"-v","error","-nostdin",*OPTIONS,"-i",str(source),"-t",str(limit),
                     "-vn","-ac","1","-ar","16000","-c:a","pcm_s16le","-y",str(wave)],30)
                import wave as wave_module
                with wave_module.open(str(wave), "rb") as stream:
                    pcm = stream.readframes(stream.getnframes())
                card["warnings"].append("直接提取失败，已用 WAV 解码替代路径恢复。")
            progress(64, "计算停顿与音量变化")
            metrics = _metrics(pcm, 0)
            card["audio"] = {"status":"ok", "message":metrics["method_note"], **metrics}
            for pause in metrics["pauses"]:
                events.append({"kind":"pause", "start":pause["start_sec"], "end":pause["end_sec"],
                               "text":"相对安静区／停顿候选", "method":"能量阈值；可能是镜头空白而非说话停顿"})
            samples = array.array("h", pcm)
            if os.sys.byteorder != "little": samples.byteswap()
            levels = [math.sqrt(sum(v*v for v in samples[i:i+4000])/len(samples[i:i+4000])) for i in range(0,len(samples),4000)]
            active = [x for x in levels if x > 100]
            threshold = statistics.median(active)*1.5 if active else math.inf
            for i, value in enumerate(levels):
                if value > threshold and (i == 0 or levels[i-1] <= threshold):
                    events.append({"kind":"emphasis","start":i*.25,"end":min(limit,(i+1)*.25),
                                   "text":"音量突出候选","method":"250ms 能量相对中位数 >1.5 倍；不是语义重音，可能是音乐"})
            progress(72, "本机转写台词（可取消）")
            transcript = transcribe_audio(wave, output_dir, cancel_event, language, limit, root or output_dir.parent)
            card["transcript"] = transcript
            for segment in transcript["segments"]:
                events.append({"kind":"speech", **segment, "method":"Whisper 本机自动转写，需回听纠错"})
            rates = [s["units_per_second"] for s in transcript["segments"] if "units_per_second" in s]
            median = statistics.median(rates) if rates else 0
            for segment in transcript["segments"]:
                speed = segment.get("units_per_second",0)
                if median and (speed > median*1.25 or speed < median*.75):
                    events.append({"kind":"pace","start":segment["start"],"end":segment["end"],
                                   "text":f"较{'快' if speed > median else '慢'}：约 {speed} 字/词单位每秒",
                                   "method":"中文字／外语词计数除以识别区段时长，含停顿；不等同于标准语速"})
        except (ValueError, OSError):
            card["audio"] = {"status":"failed", "message":"音轨提取或度量失败，WAV 替代路径也未恢复。未推断听感。"}
            card["transcript"] = {"status":"failed", "segments":[], "attempts":[], "message":"没有可分析的音轨。"}
    progress(93, "整理结构区段，分开观察与推测")
    opening = " ".join(e["text"] for e in events if e["start"] < 3 and e["kind"] in {"speech","caption"})
    card["hook_pattern"] = "contrast" if any(word in opening for word in ["只会","不只","不是","却","only"]) else "question"
    card["hook_pattern_note"] = "由开头文字线索提出的手法候选，请回看后更改；未判断真实留存效果。"
    boundaries = [0.0] + [e["start"] for e in events if e["kind"] == "shot" and .6 < e["start"] < limit-.6] + [limit]
    boundaries = sorted(set(boundaries))
    if len(boundaries) < 3:
        boundaries = sorted(set([0.0, min(3.0,limit/3), round(limit*2/3,3), limit]))
    if len(boundaries) > 17:
        boundaries = [round(i*limit/12,3) for i in range(13)]
    for i, (a,b) in enumerate(zip(boundaries,boundaries[1:])):
        observed = [e for e in events if a <= e["start"] < b and e["kind"] in {"speech","caption","shot","transition"}]
        observation = "\n".join(f"{e['start']:.2f}s · {e['text']}" for e in observed[:6]) or "该区段有抽帧画面；未检测到可用文字或明显画面变化，请回看补充。"
        role = "hook" if i == 0 else "takeaway" if i == len(boundaries)-2 else "example"
        card["beats"].append({"start":round(a,3), "end":round(b,3), "role":role,
            "observation":observation,
            "interpretation":"开头可作为钩子候选；是否形成问题或反差，需要回看验证。" if i==0 else "用途尚未验证；当前角色仅按位置建议，可改成铺垫、示例或收束。"})
    card["events"] = sorted(events,key=lambda e:(e["start"],e["kind"]))
    card["warnings"] = list(dict.fromkeys(card["warnings"]))
    card["method"] = "本地抽帧、画面差异、OCR、声学度量与可选 ASR。区段角色和效果解释是待核对假设，未使用视觉大模型。"
    if cancel_event.is_set(): raise AnalysisCancelled()
    progress(100, "结构卡已就绪：先回看，再编辑和复用")
    return card


def validate_edits(card, payload):
    rows = payload.get("beats")
    if not isinstance(rows,list) or not 1 <= len(rows) <= 60:
        raise ValueError("结构卡需有 1–60 个区段。")
    result = copy.deepcopy(card)
    clean = []
    previous_end = 0
    for row in rows:
        if not isinstance(row,dict): raise ValueError("区段格式无效。")
        a, b = row.get("start"), row.get("end")
        if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in [a,b]):
            raise ValueError("时间必须是有效秒数。")
        if not 0 <= a < b <= card["analyzed_duration"]+.001 or a < previous_end-.001:
            raise ValueError("区段须按时间排序、不能重叠，且不能超出分析范围。")
        previous_end = b
        if row.get("role") not in ROLES: raise ValueError("请选择区段用途。")
        record = {"start":a,"end":b,"role":row["role"]}
        for key in ["observation","interpretation"]:
            text = row.get(key,"")
            if not isinstance(text,str) or len(text)>4000: raise ValueError("记录最多 4000 字。")
            record[key] = text
        clean.append(record)
    result.setdefault("machine_beats",copy.deepcopy(card["beats"]))
    result["beats"] = clean
    pattern = payload.get("hook_pattern",card.get("hook_pattern","question"))
    if pattern not in HOOK_PATTERNS: raise ValueError("开场手法无效。")
    result["hook_pattern"] = pattern
    result["edited"] = True
    return result


def write_json(path, value):
    temporary = path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(temporary,path)


class AnalysisManager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.lock = threading.RLock()
        self.tasks = {}

    def directory(self, identifier):
        if not isinstance(identifier,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}",identifier):
            raise ValueError("素材编号无效。")
        path = self.root/".analysis"/identifier
        if path.resolve() != path: raise ValueError("分析目录不能是外部链接。")
        return path

    def get(self, identifier):
        with self.lock:
            directory = self.directory(identifier)
            path = directory/"state.json"
            state = json.loads(path.read_text()) if path.exists() else {"status":"idle","progress":0,"message":"尚未分析"}
            if state["status"] in {"running","cancelling"} and identifier not in self.tasks:
                state.update(status="interrupted",message="上次分析已中断，可重新分析。")
            card_path = directory/"card.json"
            if card_path.exists(): state["card"] = json.loads(card_path.read_text())
            return state

    def start(self, asset, language="auto"):
        directory = self.directory(asset.get("id"))
        relative = asset.get("file")
        if not isinstance(relative,str): raise ValueError("请先导入本地视频。")
        source = (self.root/relative).resolve()
        if not source.is_relative_to(self.root/"素材") or not source.is_file():
            raise ValueError("只允许分析素材库内的本地视频。")
        if language not in {"auto","zh","en","es"}: raise ValueError("转写语言无效。")
        identifier = asset["id"]
        with self.lock:
            if identifier in self.tasks: raise ValueError("此素材仍在分析或取消中，请稍后重试。")
            if len(self.tasks)>=2: raise ValueError("已有两个分析任务，请等待或取消后再试。")
            directory.mkdir(parents=True,exist_ok=True)
            cancel = threading.Event()
            task = {"cancel":cancel,"generation":uuid.uuid4().hex,"started":time.monotonic()}
            self.tasks[identifier] = task
            def update(status, percent, message, **extra):
                with self.lock:
                    if self.tasks.get(identifier) is task:
                        if cancel.is_set() and status=="running": return
                        write_json(directory/"state.json",{"status":status,"progress":percent,"message":message,
                                   "elapsed_seconds":round(time.monotonic()-task["started"],2),**extra})
            def worker():
                try:
                    # 每轮独立输出，取消/重试不会覆盖上一张已保存的卡。
                    run_dir = directory/task["generation"]
                    card = analyze_file(source,run_dir,lambda p,m:update("running",p,m),cancel,language,self.root)
                    with self.lock:
                        if cancel.is_set(): raise AnalysisCancelled()
                        for frame in card["frames"]: frame["file"]=task["generation"]+"/"+frame["file"]
                        old = directory/"card.json"
                        if old.exists(): write_json(directory/("previous-"+task["generation"]+".json"),json.loads(old.read_text()))
                        write_json(old,card)
                        update("ready",100,"结构卡已就绪；自动识别请回看核对。")
                except AnalysisCancelled:
                    update("cancelled",0,"已取消；已有卡片与原视频保留。")
                except ValueError as exc:
                    update("failed",0,str(exc))
                except Exception:
                    update("failed",0,"分析失败。请检查 FFmpeg 和视频是否可播放，再重试。")
                finally:
                    with self.lock:
                        if self.tasks.get(identifier) is task: self.tasks.pop(identifier)
            update("running",0,"开始本机分析")
            task["thread"] = threading.Thread(target=worker,daemon=True)
            task["thread"].start()
            return self.get(identifier)

    def cancel(self, identifier):
        with self.lock:
            self.directory(identifier)
            task = self.tasks.get(identifier)
            if task:
                task["cancel"].set()
                write_json(self.directory(identifier)/"state.json",{"status":"cancelling","progress":0,"message":"正在停止本机处理…"})
            return self.get(identifier)

    def save(self, identifier, payload):
        with self.lock:
            if identifier in self.tasks: raise ValueError("请先取消或等待分析完成，再保存编辑。")
            card = self.get(identifier).get("card")
            if not card: raise ValueError("请先完成一次分析。")
            card = validate_edits(card,payload)
            write_json(self.directory(identifier)/"card.json",card)
            return card

    def shutdown(self):
        with self.lock:
            tasks=list(self.tasks.values())
            for task in tasks: task["cancel"].set()
        for task in tasks: task["thread"].join(timeout=3)
