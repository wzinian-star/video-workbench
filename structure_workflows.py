"""仅抽象所选维度，规则草案不复制来源字幕、画面或声音。"""
import math
import re

DIMENSIONS = {"hook":"钩子", "pace":"节奏", "captions":"字幕", "transitions":"转场", "audio":"声音节奏"}


def compose_structure(cards, selections):
    if not isinstance(selections,list) or not 1<=len(selections)<=8:
        raise ValueError("请选择 1–8 条已分析素材及要借鉴的维度。")
    result = {"sources":[],"beats":[],"rules":[],"hook_seconds":3,"hook_pattern":"question","reuse_pace":False}
    used = set()
    for choice in selections:
        if not isinstance(choice,dict): raise ValueError("参考选择格式无效。")
        identifier, dimensions = choice.get("asset_id"),choice.get("dimensions")
        if identifier not in cards or not isinstance(dimensions,list) or not dimensions or any(not isinstance(d,str) or d not in DIMENSIONS for d in dimensions):
            raise ValueError("素材需完成分析，并至少勾选一个有效维度。")
        if used.intersection(dimensions): raise ValueError("每个维度请选择一个来源，以免规则冲突。")
        used.update(dimensions)
        card = cards[identifier]
        result["sources"].append({"asset_id":identifier,"dimensions":list(dict.fromkeys(dimensions))})
        beats = card.get("beats",[])
        if "hook" in dimensions:
            hook = next((b for b in beats if b["role"]=="hook"),beats[0] if beats else None)
            if hook: result["hook_seconds"]=round(hook["end"]-hook["start"],2)
            result["hook_pattern"]=card.get("hook_pattern","question")
            pattern={"question":"提问制造知识缺口","contrast":"从熟悉做法转向新方法","action":"先动作再解释","story":"先给一个具体情境"}.get(result["hook_pattern"],"提问制造知识缺口")
            result["rules"].append(f"开头借鉴「{pattern}」，参考时长 {result['hook_seconds']} 秒；用新主题重写，不复用原片台词。")
        if "pace" in dimensions:
            result["reuse_pace"]=True
            result["beats"]=[{"duration":round(b["end"]-b["start"],3),"role":b["role"]} for b in beats]
        if "captions" in dimensions:
            count=sum(e["kind"]=="caption" for e in card.get("events",[]))
            result["rules"].append(f"参考片有 {count} 次抽帧文字观察；新片只突出自己的关键词，实际样式与时长需人工核对。")
        if "transitions" in dimensions:
            count=sum(e["kind"]=="transition" for e in card.get("events",[]))
            result["rules"].append(f"参考片检出 {count} 处黑场候选；只在新主题段落切换处考虑转场，不照搬镜头。")
        if "audio" in dimensions:
            if card.get("audio",{}).get("status")!="ok": raise ValueError("该素材没有可用声音证据，请取消声音维度。")
            pauses=card["audio"].get("pauses",[])
            lengths=[p["end_sec"]-p["start_sec"] for p in pauses]
            result["rules"].append(f"参考相对安静区约 {sum(lengths)/len(lengths):.2f} 秒；用自己的音轨安排停顿，勿复制声线。" if lengths else "未检出明显停顿；用自己的音轨，勿复制声线。")
    if not result["beats"]:
        result["beats"]=[{"duration":result["hook_seconds"],"role":"hook"},{"duration":5,"role":"example"},{"duration":4,"role":"takeaway"}]
    return result


def draft_script(structure, topic, points):
    if not isinstance(topic,str) or not topic.strip() or len(topic)>160:
        raise ValueError("请填写新主题（最多 160 字）。")
    if not isinstance(points,list) or not 1<=len(points)<=12 or any(not isinstance(x,str) or not x.strip() or len(x)>600 for x in points):
        raise ValueError("请写 1–12 条自己的内容要点，每条最多 600 字。")
    beats=structure.get("beats",[])
    if not isinstance(beats,list) or not beats: raise ValueError("工作流没有可用结构。")
    # 用户可直接写「动作｜台词」，把不可猜测的画面意图留给作者。
    content=[]
    for point in points:
        pieces=re.split(r"[｜|]",point.strip(),maxsplit=1)
        content.append({"visual":pieces[0].strip() if len(pieces)==2 else "画面待补："+point.strip(),
                        "line":pieces[-1].strip(),"specific":len(pieces)==2})
    pattern=structure.get("hook_pattern","question")
    hook={"contrast":f"{topic.strip()}，换个说法。", "question":f"{topic.strip()}，怎么开口？",
          "action":f"{topic.strip()}，先看这个动作。", "story":f"今天试试：{topic.strip()}。"}.get(pattern,topic.strip())
    storyboard=[]
    clock=0
    for i,beat in enumerate(beats):
        duration=beat.get("duration",0)
        if not isinstance(duration,(int,float)) or not math.isfinite(duration) or not .01<duration<=60:
            raise ValueError("流程时长无效，请重新保存结构。")
        role=beat.get("role","example")
        # 逐条保留作者的新内容，多余要点合并到最后区段，不静默丢弃。
        selected=content[i:i+1] if i<len(beats)-1 else content[i:]
        if not selected: selected=[{"visual":"补充这一段的新画面动作","line":"[待补台词]","specific":False}]
        text=" ".join(c["line"] for c in selected)
        if i==0: text=hook+" "+text
        units=len(re.findall(r"[\u3400-\u9fff]|[A-Za-zÀ-ÿ]+",text))
        suggested=round(max(2,units/3.8+.6),1)
        original_duration=duration
        if not structure.get("reuse_pace"): duration=max(duration,suggested)
        warning=f"原结构 {original_duration:g}s，台词粗估需 {suggested:g}s；请缩句或延长区段。" if structure.get("reuse_pace") and suggested>duration else "按约 3.8 字/词单位每秒粗估；请用本集音轨校准。"
        visual=" → ".join(c["visual"] for c in selected)
        storyboard.append({"start":round(clock,3),"end":round(clock+duration,3),"role":role,"line":text,
                           "visual":visual,"caption":" / ".join(c["line"] for c in selected),
                           "review":warning,"needs_visual":any(not c["specific"] for c in selected)})
        clock+=duration
    return {"topic":topic.strip(),"method":"本地规则草案：使用结构时长与自己的主题要点；不是 AI 成片。",
            "script":"\n".join(f"[{r['start']:.2f}–{r['end']:.2f}s] {r['line']}\n画面：{r['visual']}" for r in storyboard),
            "storyboard":storyboard,"rules":structure.get("rules",[]),
            "timing_note":"时长是草案估计，不是实际配音对齐。"+("已保留勾选来源的节奏；超长台词须修改。" if structure.get("reuse_pace") else "未勾选节奏，按新台词粗估延长区段。"),
            "warning":"这里只生成可编辑脚本与分镜；有声样片需自带本集音轨，并由已登录的 Codex 执行现有制作流程。"}
