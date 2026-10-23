"""Versioned, bilateral recruitment rules and immutable proposal agreements."""
MAP_MODES = {
    'map.zero_dam': ['difficulty.normal', 'difficulty.confidential', 'difficulty.confidential_night'],
    'map.layali_grove': ['difficulty.normal', 'difficulty.confidential'],
    'map.brakkesh': ['difficulty.confidential', 'difficulty.top_secret'],
    'map.space_city': ['difficulty.confidential', 'difficulty.top_secret'],
    'map.tide_prison': ['difficulty.confidential', 'difficulty.top_secret'],
    'map.az3': ['difficulty.normal', 'difficulty.confidential'],
}
TASKS = ['task.investigate', 'task.place', 'task.extract_items', 'task.eliminate_operators', 'task.eliminate_specials', 'task.session_missions', 'task.benchmark', 'task.fate_contract']
GOALS = ['goal.missions', 'goal.budget_run', 'goal.loot_extract', 'goal.aggressive']
GEAR = ['helmet', 'armor', 'ammo']


def common_maps(a, b):
    other = {(m['map'], m['difficulty']) for m in b.get('maps', [])}
    shared = [m for m in a.get('maps', []) if (m['map'], m['difficulty']) in other]
    return sorted(shared, key=lambda m: (list(MAP_MODES).index(m['map']), MAP_MODES[m['map']].index(m['difficulty'])))


def common_tasks(a, b):
    return [t for t in TASKS if t in a.get('tasks', []) and t in b.get('tasks', [])]


def assessment_v4(a, b):
    hard=['conflict.'+key for key in ('listing_language','region','platform','voice','slot') if a.get(key,'zh' if key=='listing_language' else None)!=b.get(key,'zh' if key=='listing_language' else None)]
    checks = [
        {'label':'ui.same_session_goal','ok':a['goal']==b['goal']},
        {'label':'ui.shared_map_and_difficulty','ok':bool(common_maps(a,b))},
        {'label':'ui.their_skill_fits_your_range_360','ok':a['strength_min'] <= b['strength_score'] <= a['strength_max']},
        {'label':'ui.your_skill_fits_their_range_361','ok':b['strength_min'] <= a['strength_score'] <= b['strength_max']},
        {'label':'ui.their_playstyle_fits_your_range_362','ok':a['partner_style_min'] <= b['style'] <= a['partner_style_max']},
        {'label':'ui.your_playstyle_fits_their_range_363','ok':b['partner_style_min'] <= a['style'] <= b['partner_style_max']},
    ]
    if a['goal'] == b['goal'] == 'goal.missions':
        checks.append({'label':'ui.shared_missions_355','ok':bool(common_tasks(a,b))})
    if a['goal'] != 'goal.budget_run' and b['goal'] != 'goal.budget_run':
        checks.extend([
            {'label':'ui.their_gear_meets_your_minimum_356','ok':all(b[k] >= a['partner_'+k+'_min'] for k in GEAR)},
            {'label':'ui.your_gear_meets_their_minimum_357','ok':all(a[k] >= b['partner_'+k+'_min'] for k in GEAR)},
        ])
    return {'eligible':not hard and all(c['ok'] for c in checks),'hard_conflicts':hard,'checks':checks,
            'skill_gap':abs(a['strength_score']-b['strength_score'])}


def agreement_v4(a,b):
    pairs=common_maps(a,b)
    return {'schema_version':4,'listing_language':a['listing_language'],'goal':a['goal'],'map':pairs[0]['map'],'difficulty':pairs[0]['difficulty'],
            'tasks':common_tasks(a,b) if a['goal']=='goal.missions' else [],
            'common_maps':pairs,'budget_min':0,'budget_max':1000,'warnings':[],
            'scope':'ui.disable_auto_fill_in_the_game'}
