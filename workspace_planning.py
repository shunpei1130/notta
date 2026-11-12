"""Private roadmap and recruitment preparation; no provider calls or writes."""
import copy
import os
import re

import server as s

TASK_FIELDS = {'title': 120, 'instructions': 3000, 'role': 120, 'estimate': 120, 'completion': 2000}
PUBLIC_FIELDS = {'title': 120, 'overview': 4000, 'schedule': 1500, 'conditions': 2000, 'recruitment': 3000}


def enabled():
    return os.getenv('NOTTA_PLANNING_ENABLED') == 'yes'


def identifier(value, blank=False):
    value = s.text(value, 24, blank=blank)
    s.require((blank and not value) or re.fullmatch(r'[a-f0-9]{24}', value), 400, 'タスク・段階のIDを確認してください。')
    return value


def fields(value, limits):
    s.require(isinstance(value, dict), 400, '入力の形式を確認してください。')
    return {k: s.text(value.get(k, ''), n, blank=True) for k, n in limits.items()}


def task_extras(task):
    deps = task.get('dependsOn', [])
    s.require(isinstance(deps, list) and len(deps) <= 50 and len(set(str(v) for v in deps)) == len(deps), 400, '前提タスクを確認してください。')
    result = {'stageId': identifier(task.get('stageId', ''), True), 'dependsOn': [identifier(v) for v in deps]}
    for key, default in (('recruitCandidate', task.get('recruit', False)), ('useCommonTerms', False)):
        value = task.get(key, default)
        s.require(type(value) is bool, 400)
        result[key] = value
    return result


def normalize(draft):
    raw = draft.get('roadmap')
    if raw is None:
        roadmap = None
    else:
        roadmap = fields(raw, {'goal': 1500, 'criteria': 3000})
        stages = raw.get('stages', [])
        s.require(isinstance(stages, list) and len(stages) <= 50, 400, '段階は50件までです。')
        roadmap['stages'], ids = [], set()
        for stage in stages:
            value = fields(stage, {'title': 120, 'outcome': 1500})
            value['id'] = identifier(stage.get('id'))
            s.require(value['id'] not in ids, 400, '同じ段階が重複しています。')
            ids.add(value['id']);roadmap['stages'].append(value)
        roadmap['confirmedAt'] = None  # Only the owner completion operation can set this.
    stage_ids = {v['id'] for v in roadmap['stages']} if roadmap else set()
    tasks = {v['id']: v for v in draft['tasks']}
    for task in draft['tasks']:
        task.update(task_extras(task))
        s.require(not task['stageId'] or task['stageId'] in stage_ids, 400, 'タスクの段階が見つかりません。')
        s.require(all(dep in tasks and dep != task['id'] for dep in task['dependsOn']), 400, '存在する別のタスクを前提にしてください。')
    visited, visiting = set(), set()
    def visit(tid):
        s.require(tid not in visiting, 400, '前提タスクが循環しています。作業のつながりを確認してください。')
        if tid in visited:
            return
        visiting.add(tid)
        for dep in tasks[tid]['dependsOn']:
            visit(dep)
        visiting.remove(tid);visited.add(tid)
    for tid in tasks:
        visit(tid)
    draft['roadmap'] = roadmap
    return draft


def progress(draft):
    roadmap = draft.get('roadmap')
    tasks = draft.get('tasks', [])
    missing = []
    if not roadmap:
        missing.append('ロードマップを用意してください')
    else:
        for key, label in (('goal', '企画のゴール'), ('criteria', '最終の完了条件')):
            if not roadmap.get(key): missing.append(label)
        if not roadmap.get('stages'): missing.append('段階')
        for stage in roadmap.get('stages', []):
            if not stage['title'] or not stage['outcome']: missing.append('段階の名前・到達点')
            if not any(t.get('stageId') == stage['id'] for t in tasks): missing.append('タスクのない段階')
        for task in tasks:
            if not task.get('stageId'): missing.append('「'+task['title']+'」の段階')
    done = {t['id'] for t in tasks if t.get('status') == '完了'}
    ready = [t['id'] for t in tasks if t.get('stageId') and t.get('status') != '完了' and all(d in done for d in t.get('dependsOn', []))]
    active = [t['id'] for t in tasks if t.get('status') == '進行中']
    next_ids = list(dict.fromkeys(active + ready))[:3] if not missing else []
    return {'ready': not missing, 'missing': list(dict.fromkeys(missing)), 'done': len(done), 'total': len(tasks),
            'nextTaskIds': next_ids, 'confirmedAt': roadmap.get('confirmedAt') if roadmap else None}


def carry(before, value):
    """An older UI omitting new properties must not erase them."""
    s.require(isinstance(value, dict), 400, '入力の形式を確認してください。')
    value = copy.deepcopy(value)
    if 'roadmap' not in value and 'roadmap' in before:
        value['roadmap'] = copy.deepcopy(before['roadmap'])
    old_tasks = {t.get('id'): t for t in before.get('tasks', [])}
    s.require(isinstance(value.get('tasks', []), list), 400, 'タスクの形式を確認してください。')
    for task in value.get('tasks', []):
        s.require(isinstance(task, dict) and (task.get('id') is None or isinstance(task['id'], str)), 400, 'タスクの形式を確認してください。')
        prior = old_tasks.get(task.get('id'), {})
        for key in ('stageId', 'dependsOn', 'recruitCandidate', 'useCommonTerms'):
            if key not in task and key in prior: task[key] = copy.deepcopy(prior[key])
    return value


def proof(draft):
    roadmap = copy.deepcopy(draft.get('roadmap'))
    if roadmap: roadmap.pop('confirmedAt', None)
    return {'roadmap': roadmap, 'tasks': draft.get('tasks', [])}


def initial(value, cleaned):
    if not enabled() and not any(k in value for k in ('roadmap', 'public')) and not any('id' in t for t in value.get('tasks', [])):
        return cleaned
    cleaned['roadmap'] = copy.deepcopy(value.get('roadmap'))
    cleaned['public'] = fields(value.get('public', {}), PUBLIC_FIELDS)
    ids = set()
    for raw, task in zip(value['tasks'], cleaned['tasks']):
        task['id'] = identifier(raw.get('id') or s.ident())
        s.require(task['id'] not in ids, 400, 'タスクIDが重複しています。');ids.add(task['id'])
        task.update(task_extras(raw))
        for key in ('recruit', 'termsConfirmed'):
            flag = raw.get(key, False);s.require(type(flag) is bool, 400);task[key] = flag
        capacity = raw.get('capacity', 1)
        s.require(type(capacity) is int and 1 <= capacity <= 50, 400);task['capacity'] = capacity
        task['offer'] = fields(raw.get('offer', task), {**TASK_FIELDS, 'terms': 2000})
    return normalize(cleaned)


def offer(task, common):
    return {**task['offer'], 'terms': common if task.get('useCommonTerms') else task['offer']['terms']}


def recruitment(draft):
    missing = []
    for key, label in (('title', '企画名'), ('overview', '公開する概要'), ('schedule', '日程・場所の案内'), ('conditions', '共通の応募条件'), ('recruitment', '募集文')):
        if not draft['public'].get(key): missing.append({'field': key, 'taskId': '', 'label': label})
    selected = [t for t in draft['tasks'] if t.get('recruit')]
    if not selected: missing.append({'field': 'roles', 'taskId': '', 'label': '募集する役割を1件以上選ぶ'})
    for task in selected:
        public = offer(task, draft['public']['conditions'])
        for key, label in (('title', '作業名'), ('instructions', '作業内容'), ('role', '役割'), ('estimate', '時間'), ('completion', '完了条件'), ('terms', '応募条件')):
            if not public.get(key): missing.append({'field': key, 'taskId': task['id'], 'label': task['title']+'：'+label})
    return {'ready': not missing, 'missing': missing, 'selected': len(selected)}
