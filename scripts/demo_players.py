"""Local-only test peers. Run --serve to keep recruitment alive and confirm demo peers."""
import argparse
import copy
import json
import time
from app.services.pilot import Pilot

BASE = dict(schema_version=4,nickname='',region='NA',platform='PC',language='中文',voice='开麦',slot='现在起一小时',
            goal='搜打撤',tasks=[],maps=[{'map':'零号大坝','difficulty':'普通'}],helmet=4,armor=4,ammo=4,style=3,
            partner_style_min=1,partner_style_max=5,partner_helmet_min=2,partner_armor_min=2,partner_ammo_min=2,
            score_mode='manual',manual_score=100,strength_range_mode='auto',public=True,discovery='browse',is_demo=True)


def seed(p):
    with p.db() as db:
        rows = db.execute('SELECT * FROM tickets ORDER BY id DESC').fetchall()
        existing = {json.loads(r['profile']).get('demo_key'):r['token'] for r in reversed(rows)
                    if json.loads(r['profile']).get('is_demo')}
        real = next((json.loads(r['profile']) for r in rows if not json.loads(r['profile']).get('is_demo')), {})
    base=copy.deepcopy(BASE)
    if real.get('schema_version')==4:
        base.update({k:copy.deepcopy(real[k]) for k in BASE if k in real and k not in ('nickname','public','discovery','is_demo')})
        base.update(score_mode='manual',manual_score=real['strength_score'],strength_range_mode='auto',style=real['partner_style_min'],partner_style_min=real['style'],partner_style_max=real['style'])
    specs = [
        ('near-a','【测试】北岸·条件相近',{}),
        ('near-b','【测试】零号·条件相近',{}),
        ('goal','【测试】猎鹰·目标不同',{'goal':'猛攻' if base['goal']!='猛攻' else '搜打撤'}),
        ('budget','【测试】重装·高配玩家',{'helmet':6,'armor':6,'ammo':6,'partner_helmet_min':6,'partner_armor_min':6,'partner_ammo_min':6}),
        ('skill','【测试】精英·实力差距',{'score_mode':'manual','manual_score':10000 if base['manual_score']<1000 else 1}),
        ('region','【测试】远岸·不同区服',{'region':'EU' if base['region']!='EU' else 'NA'}),
        ('language','【测试】同行·另一语言',{'language':'中文' if base['language']!='中文' else 'English'}),
    ]
    peers=[]
    for key,name,patch in specs:
        token=existing.get(key) or p.register()['token']
        state=p.state(token)
        if state['ticket'] and state['ticket']['status']=='waiting' and state['ticket']['profile'].get('schema_version')!=4:
            state=p.action(token,'cancel')
        if not state['ticket'] or state['ticket']['status'] not in ('waiting','matched'):
            plan={**copy.deepcopy(base),**patch,'nickname':name,'is_demo':True,'demo_key':key}
            if plan['goal']!='卡战备跑刀':
                for gear in ('helmet','armor','ammo'):
                    plan[gear]=plan.get(gear) or 4
                    plan['partner_'+gear+'_min']=min(plan.get('partner_'+gear+'_min') or 2,plan[gear])
            p.join(token,plan)
        peers.append(token)
    print(f'已准备 {len(peers)} 位【测试】玩家；基本条件：{base["region"]} / {base["platform"]} / {base["language"]} / {base["maps"][0]["map"]} / {base["maps"][0]["difficulty"]}。',flush=True)
    return peers


def run(p,peers):
    print('测试队友运行中：被邀请后自动确认；不自动代填你的反馈。Ctrl+C 停止。',flush=True)
    while True:
        for token in peers:
            state=p.state(token)
            squad=state['squad']
            if squad and squad['status']=='proposed':
                try:p.action(token,'confirm',expected_squad_id=squad['id'])
                except ValueError:pass
            elif squad and squad['status']=='ready':
                # Return a test-only peer outcome so the human can finish and try another round.
                with p.db() as db:
                    other_done = any(db.execute('SELECT status FROM tickets WHERE id=?',(m['id'],)).fetchone()[0]=='done' for m in squad['members'] if not m['is_me'])
                if not squad['feedback_submitted'] and other_done:
                    p.action(token,'feedback',dict(played=False,again='unsure',goal_met=False,budget_met=False,skill_met=False,reason='测试机器人，未实际游玩'),squad['id'])
            elif state['ticket'] and state['ticket']['status'] in ('cancelled','expired','done'):
                # Do not re-open while its previous partner still has an active squad.
                if squad and squad['status']=='ready':continue
                p.join(token,state['ticket']['profile'])
        time.sleep(2)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--serve',action='store_true');args=parser.parse_args()
    p=Pilot();peers=seed(p)
    if args.serve:
        try:run(p,peers)
        except KeyboardInterrupt:print('测试队友已停止。',flush=True)
