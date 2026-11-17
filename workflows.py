"""Local workbench workflow registry; plain rules, never executable uploads."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid

_LOCK = threading.RLock()
_BUILTIN = {
    'id':'pencil-drawing', 'title':'铅笔画', 'source':'builtin', 'base_skill':'pencil-drawing',
    'description':'西班牙西语情景短片。选已确认片段作参考，先做有声片头和第一幕。',
    'details':'线圈纸上逐笔画出场景，人物先做动作，再手写西语；字写完才读。中文先讲情境，别把中西语片头拼成两个人声。保留指定中文和西语音色；先试听片头和第一幕，通过后再做全片，已通过片段不重画、不重配。',
    'requires_voice': True,
}

class WorkflowError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status=status

def _read(root: Path):
    path=Path(root)/'workflows.json'
    if path.is_symlink():
        raise WorkflowError('工作流配置不是普通本地文件。',500)
    if not path.exists():
        return []
    try:
        items=json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError) as exc:
        raise WorkflowError('无法读取工作流配置。',500) from exc
    if not isinstance(items,list) or any(not isinstance(i,dict) for i in items):
        raise WorkflowError('工作流配置格式不正确。',500)
    return items

def list_workflows(root: Path) -> list[dict]:
    with _LOCK:
        return [dict(_BUILTIN),*_read(root)]

def resolve_workflow(root: Path, identifier: str) -> dict:
    record=next((item for item in list_workflows(root) if item.get('id')==identifier),None)
    if record is None:
        raise WorkflowError('找不到所选工作流，请刷新后重选。',422)
    return record

def create_workflow(root: Path, payload: dict) -> dict:
    def field(name,label,limit,required=False):
        value=payload.get(name,'')
        if not isinstance(value,str) or '\0' in value:
            raise WorkflowError(f'{label}必须是文字。')
        value=value.strip()
        if len(value)>limit or (required and not value):
            raise WorkflowError(f'请检查{label}（最多 {limit} 字）。')
        return value
    title=field('title','工作流名称',80,True)
    description=field('description','简介',180)
    details=field('details','制作规则',8000,True)
    record={'id':'workflow-'+uuid.uuid4().hex[:16], 'title':title,
            'description':description or '自定义工作流，按保存的制作规则执行。',
            'details':details, 'source':'custom', 'requires_voice':True, 'base_skill':''}
    path=Path(root)/'workflows.json'
    with _LOCK:
        current=_read(root)
        if len(current)>=120:
            raise WorkflowError('已达工作流数量上限。',409)
        if any(str(x.get('title','')).casefold()==title.casefold() for x in [_BUILTIN,*current]):
            raise WorkflowError('已有同名工作流，请换一个名称。',409)
        fd,tmp=tempfile.mkstemp(prefix='.workflows-',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as h:
                json.dump([*current,record],h,ensure_ascii=False,indent=2)
                h.write('\n')
            os.replace(tmp,path)
        finally:
            Path(tmp).unlink(missing_ok=True)
    return record
