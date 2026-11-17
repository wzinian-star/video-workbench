"""Offline evidence for reference audio; subjective listening is intentionally separate."""
import array
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class AudioReferenceError(ValueError):
    pass


_ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
_CLIP = re.compile(r"[0-9a-f]{20}\Z")
_CLOCK = re.compile(r"\d+(?::[0-5]?\d){0,2}(?:\.\d{1,3})?\Z")
_ALLOWED_FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,mp3,wav,flac,ogg,aac,avi,aiff,mpegts"
_SAMPLE_RATE = 16000
_MAX_SEC = 60.0
_CACHE_VERSION = 2


def _exe(name):
    found = shutil.which(name)
    if not found:
        found = next((str(Path(d)/name) for d in ["/opt/homebrew/bin", "/usr/local/bin"]
                      if os.access(Path(d)/name, os.X_OK)), None)
    return found


def _local_media(root, source):
    root = Path(root).resolve()
    source = Path(source)
    source = (source if source.is_absolute() else root/source).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise AudioReferenceError("本地素材不可用。请重新导入视频或单独音频。")
    return source


def _clip_dir(root, asset_id):
    if not isinstance(asset_id, str) or not _ID.fullmatch(asset_id):
        raise AudioReferenceError("素材编号无效。")
    root = Path(root).resolve()
    dest = (root/".reference_audio"/asset_id).resolve()
    if not dest.is_relative_to(root):
        raise AudioReferenceError("音频缓存目录无效。")
    return dest


def reference_clip_path(root, asset_id, clip_id):
    if not isinstance(clip_id, str) or not _CLIP.fullmatch(clip_id):
        raise AudioReferenceError("片段编号无效。")
    base = _clip_dir(root, asset_id)
    p = (base/(clip_id+".mp3")).resolve()
    if not p.is_relative_to(base) or not p.is_file():
        raise AudioReferenceError("音频片段不存在，请重新提取。")
    return p


def _run(cmd, seconds):
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=seconds, check=True)
    except subprocess.TimeoutExpired:
        raise AudioReferenceError("处理音频超时，请缩短参考时间段。") from None
    except (subprocess.CalledProcessError, OSError):
        raise AudioReferenceError("无法读取该音轨。请导入可正常播放的 MP4、MOV、MP3 或 WAV。") from None


def _signed_clock(value):
    if not _CLOCK.fullmatch(value):
        raise AudioReferenceError("时间段格式用 00:03–00:09，或 3–9。")
    t = 0
    for item in value.split(":"):
        t = t*60 + float(item)
    return t


def _span(segment, total):
    if segment is None or segment == "":
        return 0.0, min(total, _MAX_SEC), total > _MAX_SEC
    if not isinstance(segment, str) or len(segment) > 50:
        raise AudioReferenceError("时间段格式用 00:03–00:09，或 3–9。")
    points = re.split(r"\s*(?:-|–|—|~|～|至)\s*", segment.strip())
    if len(points) != 2:
        raise AudioReferenceError("请填写两个时间点，例如 00:03–00:09。")
    start, end = map(_signed_clock, points)
    if start < 0 or end <= start or end > total + .05:
        raise AudioReferenceError(f"时间段须由早到晚，且不超过素材时长 {total:.2f} 秒。")
    end = min(end, total)
    if end-start > _MAX_SEC:
        raise AudioReferenceError("单次最多分析 60 秒，可分段分别提取。")
    return start, end, False


def _level(amplitude):
    return round(20*math.log10(max(1/32768, amplitude/32768)), 1)


def _metrics(data, offset):
    pcm = array.array("h")
    pcm.frombytes(data)
    if sys.byteorder != "little":
        pcm.byteswap()
    if not pcm:
        raise AudioReferenceError("所选时间段没有可解码音频，请换一段。")
    peak = max(map(abs, pcm))
    rms = math.sqrt(sum(x*x for x in pcm)/len(pcm))
    threshold = max(-43.0, _level(peak)-32)
    block = _SAMPLE_RATE//50
    pauses, run = [], None
    for i in range(0, len(pcm), block):
        part = pcm[i:i+block]
        quiet = _level((sum(x*x for x in part)/len(part))**.5) < threshold
        if quiet and run is None:
            run = i
        if not quiet and run is not None:
            if i-run >= .22*_SAMPLE_RATE:
                pauses.append({"start_sec":round(offset+run/_SAMPLE_RATE, 3),
                               "end_sec":round(offset+i/_SAMPLE_RATE, 3)})
            run = None
    if run is not None and len(pcm)-run >= .22*_SAMPLE_RATE:
        pauses.append({"start_sec":round(offset+run/_SAMPLE_RATE, 3),
                       "end_sec":round(offset+len(pcm)/_SAMPLE_RATE, 3)})

    points = []
    step, n = 3_200, 640
    effective_rate = 8000
    for i in range(0, len(pcm)-n+1, step):
        signal = list(pcm[i:i+n:2])
        dc = sum(signal)/len(signal)
        wave = [x-dc for x in signal]
        power = sum(x*x for x in wave)
        if _level(math.sqrt(power/len(wave))) < max(-40, threshold):
            continue
        # Local ACF peaks are candidates. Prefer the shortest period whose
        # normalized score is nearly as good as the strongest candidate.
        scores = {}
        for lag in range(17, 125):
            a, b = wave[:-lag], wave[lag:]
            denom = math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
            scores[lag] = sum(x*y for x, y in zip(a,b))/denom if denom else 0.0
        peaks = [k for k in range(18,124) if scores[k] >= scores[k-1]
                 and scores[k] >= scores[k+1] and scores[k] >= .7]
        if peaks:
            top = max(scores[k] for k in peaks)
            lag = next(k for k in peaks if scores[k] >= top*.9)
            center = scores[lag]
            left, right = scores[lag-1], scores[lag+1]
            curve = left-2*center+right
            fractional = .5*(left-right)/curve if curve else 0
            points.append({"time_sec":round(offset+(i+n/2)/_SAMPLE_RATE, 3),
                           "hz":round(effective_rate/(lag+fractional), 1)})
    return {"rms_dbfs":_level(rms), "peak_dbfs":_level(peak),
            "pause_threshold_dbfs":round(threshold, 1), "pauses":pauses,
            "pitch_estimates":points,
            "method_note":"时间为原素材时间；停顿按相对安静区估算，配乐可能掩盖停顿；音高为可检测周期估算，可能命中配乐或八度，不等同于听感。零信号按约 -90.3 dBFS 显示。"}


def _whisper_setup(root):
    tools_dir = Path(root).resolve()/".tools"/"whisper"
    possibilities = [tools_dir/"bin"/"whisper-cli", tools_dir/"build"/"bin"/"whisper-cli"]
    engine = next((str(p) for p in possibilities if p.is_file()
                   and os.access(p, os.X_OK)), None) or _exe("whisper-cli")
    if not engine:
        return None
    home = Path.home()
    supplied = os.environ.get("VIDEO_WORKBENCH_WHISPER_MODEL")
    roots = [tools_dir/"models", home/".cache/whisper.cpp", home/".local/share/whisper.cpp/models",
             home/".cache/whisper", Path("/opt/homebrew/share/whisper-cpp/models")]
    models = [Path(supplied).expanduser()] if supplied else [
        p for d in roots if d.is_dir() for p in sorted(d.glob("ggml-*.bin"))
    ]
    model = next((p.resolve() for p in models if p.is_file()
                  and not re.search(r"\.en(?:\.|-|$)", p.name)), None)
    return (engine, model) if model else None


def _transcribe(setup, ffmpeg, clip, offset, language="auto", duration=None):
    if not setup:
        return {"status":"unavailable", "segments":[],
                "message":"没有检测到本机 Whisper CLI 和多语模型。音频可直接播放和对听；安装工作台本地转写组件和多语模型后，重新提取即可得到时间码字幕。"}
    engine, model = setup
    try:
        with tempfile.TemporaryDirectory() as d:
            wave, output = Path(d)/"input.wav", Path(d)/"transcript"
            _run([ffmpeg, "-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe",
                  "-i",str(clip),"-ac","1","-ar","16000","-c:a","pcm_s16le",str(wave)], 20)
            _run([engine, "-m", str(model), "-f", str(wave), "-l", language, "-oj", "-of", str(output)], 180)
            raw = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
        if raw.get("model", {}).get("multilingual") is False:
            return {"status":"failed","segments":[],
                    "message":"此转写模型只支持英语。请配置不带 .en 的多语 Whisper 模型。"}
        segments, unrecognized = [], []
        for row in raw.get("transcription", []):
            a = float(row["offsets"]["from"])/1000
            b = float(row["offsets"]["to"])/1000
            if math.isfinite(a) and math.isfinite(b) and 0 <= a < b:
                if duration is not None:
                    b = min(b, duration)
                    if a >= b:
                        continue
                span = {"start_sec":round(offset+a, 3), "end_sec":round(offset+b, 3)}
                snippet = str(row["text"]).strip()
                if any(c.isalnum() for c in snippet):
                    segments.append({**span, "text":snippet})
                else:
                    unrecognized.append(span)
        if unrecognized and language == "auto":
            note = "部分片段未识别。遇到中西混说，可选中该段并指定中文或西语重跑。"
        elif segments:
            note = "本机转写；重音、标点与断句仍须结合原音核对。"
        else:
            note = "本机模型没有识别出可用文字，请切换语言或播放片段核对。"
        return {"status":"ready" if segments else "unavailable", "segments":segments,
                "language":language, "unrecognized":unrecognized, "message":note}
    except (AudioReferenceError, OSError, ValueError, KeyError, TypeError):
        return {"status":"failed","segments":[],
                "message":"本机转写失败，音频仍可播放。请确认多语模型适用于当前 whisper-cli 并重试。"}


def analyze_reference(root, asset_id, source_path, segment=None, language="auto"):
    if not isinstance(language, str) or language not in {"auto", "zh", "es"}:
        raise AudioReferenceError("转写语言仅支持自动、中文或西语。")
    base = _clip_dir(root, asset_id)
    source = _local_media(root, source_path)
    ffmpeg, ffprobe = _exe("ffmpeg"), _exe("ffprobe")
    if not ffmpeg or not ffprobe:
        raise AudioReferenceError("本机没有找到 FFmpeg/FFprobe，无法提取。")
    media_options = ["-protocol_whitelist", "file,pipe", "-format_whitelist", _ALLOWED_FORMATS]
    probe = _run([ffprobe, "-v","error", *media_options, "-select_streams","a:0","-show_entries",
                 "stream=duration:format=duration","-of","json",str(source)], 20)
    probe_data = json.loads(probe.stdout)
    tracks = probe_data.get("streams", [])
    if not tracks:
        raise AudioReferenceError("这个文件没有音轨，请导入带声音的版本或单独音频。")
    total = None
    for candidate in [probe_data.get("format",{}).get("duration"), tracks[0].get("duration")]:
        try:
            value = float(candidate)
            if math.isfinite(value) and value > 0:
                total = value
                break
        except (ValueError, TypeError):
            continue
    if total is None:
        raise AudioReferenceError("无法确定素材时长，请改用常见格式的完整文件。")
    start, end, capped = _span(segment, total)
    stat = source.stat()
    setup = _whisper_setup(root)
    key = [str(source), stat.st_size, stat.st_mtime_ns, start, end, capped, _CACHE_VERSION, language]
    if setup:
        key += [str(setup[0]), str(setup[1]), setup[1].stat().st_mtime_ns]
    clip_id = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:20]
    base.mkdir(parents=True, exist_ok=True)
    clip, metadata = base/(clip_id+".mp3"), base/(clip_id+".json")
    if clip.is_file() and metadata.is_file() and not clip.is_symlink() and not metadata.is_symlink():
        try:
            old = json.loads(metadata.read_text(encoding="utf-8"))
            if old.get("clip_id") == clip_id and old.get("transcript", {}).get("status") != "failed":
                return old
        except (OSError, ValueError, AttributeError):
            pass
    common = [ffmpeg,"-v","error","-nostdin", *media_options,"-ss",f"{start:.4f}","-i",str(source),
              "-t",f"{end-start:.4f}","-map","0:a:0","-vn","-sn","-dn"]
    raw = _run(common+["-ac","1","-ar",str(_SAMPLE_RATE),"-f","s16le","pipe:1"], 30).stdout
    details = _metrics(raw, start)
    actual = min(end-start, len(raw)/2/_SAMPLE_RATE)
    with tempfile.TemporaryDirectory(dir=base) as d:
        draft = Path(d)/"clip.mp3"
        _run(common+["-map_metadata","-1","-c:a","libmp3lame","-q:a","3",str(draft)], 30)
        transcript = _transcribe(setup, ffmpeg, draft, start, language, actual)
        report = {"clip_id":clip_id,"segment_start_sec":round(start,3),
                  "segment_end_sec":round(start+actual,3),"duration_sec":round(actual,3),
                  "source_duration_sec":round(total,3),"default_segment_capped":capped,
                  **details,"transcript":transcript}
        draft_metadata = Path(d)/"clip.json"
        draft_metadata.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        draft.replace(clip)
        draft_metadata.replace(metadata)
    return report
