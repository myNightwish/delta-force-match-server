"""Versioned, bilateral recruitment rules and immutable proposal agreements."""
MAP_MODES = {
    '零号大坝': ['普通', '机密', '机密·黑夜'],
    '长弓溪谷': ['普通', '机密'],
    '巴克什': ['机密', '绝密'],
    '航天基地': ['机密', '绝密'],
    '潮汐监狱': ['机密', '绝密'],
    'AZ3': ['普通', '机密'],
}
TASKS = ['调查', '放置', '带出撤离任务', '击杀干员', '击杀特殊兵种', '普通局内任务', '标杆任务', '命运契约']
GOALS = ['33任务', '卡战备跑刀', '搜打撤', '猛攻']
GEAR = ['helmet', 'armor', 'ammo']


def common_maps(a, b):
    other = {(m['map'], m['difficulty']) for m in b.get('maps', [])}
    shared = [m for m in a.get('maps', []) if (m['map'], m['difficulty']) in other]
    return sorted(shared, key=lambda m: (list(MAP_MODES).index(m['map']), MAP_MODES[m['map']].index(m['difficulty'])))


def common_tasks(a, b):
    return [t for t in TASKS if t in a.get('tasks', []) and t in b.get('tasks', [])]


def assessment_v4(a, b):
    hard = [label+'不一致' for key, label in [('region','区服'),('platform','游戏端'),('language','语言'),('voice','语音方式'),('slot','开局时段')] if a[key] != b[key]]
    checks = [
        {'label':'本局目标一致','ok':a['goal']==b['goal']},
        {'label':'地图与难度有交集','ok':bool(common_maps(a,b))},
        {'label':'对方实力符合我的范围','ok':a['strength_min'] <= b['strength_score'] <= a['strength_max']},
        {'label':'我的实力符合对方范围','ok':b['strength_min'] <= a['strength_score'] <= b['strength_max']},
        {'label':'对方打法符合我的期望','ok':a['partner_style_min'] <= b['style'] <= a['partner_style_max']},
        {'label':'我的打法符合对方期望','ok':b['partner_style_min'] <= a['style'] <= b['partner_style_max']},
    ]
    if a['goal'] == b['goal'] == '33任务':
        checks.append({'label':'有共同任务','ok':bool(common_tasks(a,b))})
    if a['goal'] != '卡战备跑刀' and b['goal'] != '卡战备跑刀':
        checks.extend([
            {'label':'对方装备达到我的门槛','ok':all(b[k] >= a['partner_'+k+'_min'] for k in GEAR)},
            {'label':'我的装备达到对方门槛','ok':all(a[k] >= b['partner_'+k+'_min'] for k in GEAR)},
        ])
    return {'eligible':not hard and all(c['ok'] for c in checks),'hard_conflicts':hard,'checks':checks,
            'skill_gap':abs(a['strength_score']-b['strength_score'])}


def agreement_v4(a,b):
    pairs=common_maps(a,b)
    return {'schema_version':4,'goal':a['goal'],'map':pairs[0]['map'],'difficulty':pairs[0]['difficulty'],
            'tasks':common_tasks(a,b) if a['goal']=='33任务' else [],
            'common_maps':pairs,'budget_min':0,'budget_max':1000,'warnings':[],
            'scope':'在游戏内关闭自动补队'}
