"""Local-only test peers. Run --serve to keep recruitment alive and confirm demo peers."""
import argparse
import json
import time
from app.services.pilot import Pilot

BASE = dict(nickname='',game_id='',region='NA',platform='PC',language='中文',map='零号大坝',
            difficulty='普通',voice='开麦',slot='现在起一小时',goal='33任务',task='调查',armor=3,ammo=3,style=3,partner_style=3,
            confidential_kd=1.5,top_secret_kd=2,combat_score=50,strength_min=0,strength_max=200,budget_min=20,
            budget_max=50,rank=4,skill=3,gap=1,public=True,discovery='browse',is_demo=True)


def seed(p):
    with p.db() as db:
        rows = db.execute('SELECT * FROM tickets ORDER BY id DESC').fetchall()
        existing = {json.loads(r['profile']).get('demo_key'):r['token'] for r in reversed(rows)
                    if json.loads(r['profile']).get('is_demo')}
        real = next((json.loads(r['profile']) for r in rows if not json.loads(r['profile']).get('is_demo')), {})
    # Mirror the latest real plan's basic conditions, so tests are immediately useful.
    base = {**BASE, **{k:real[k] for k in BASE if k in real and k not in ('nickname','game_id','public','discovery','is_demo')}}
    if real.get('goal') in ('33任务','跑刀','猛攻'):
        base['style'],base['partner_style'] = real['partner_style'],real['style']
    specs = [
        ('near-a','【测试】北岸·条件相近',{}),
        ('near-b','【测试】零号·条件相近',{}),
        ('goal','【测试】猎鹰·目标不同',{'goal':'猛攻' if base['goal']!='猛攻' else '跑刀'}),
        ('budget','【测试】重装·装备不同',{'armor':5 if base.get('armor')!=5 else 1,'ammo':5 if base.get('ammo')!=5 else 1}),
        ('skill','【测试】精英·实力差距',{'confidential_kd':10,'top_secret_kd':10,'combat_score':1000,'strength_min':9000,'strength_max':11000}),
        ('region','【测试】远岸·不同区服',{'region':'EU' if base['region']!='EU' else 'NA'}),
        ('language','【测试】同行·另一语言',{'language':'中文' if base['language']!='中文' else 'English'}),
    ]
    peers=[]
    for key,name,patch in specs:
        token=existing.get(key) or p.register()['token']
        state=p.state(token)
        if not state['ticket'] or state['ticket']['status'] not in ('waiting','matched'):
            p.join(token,{**base,**patch,'nickname':name,'game_id':'DEMO-NOT-REAL-'+key,'is_demo':True,'demo_key':key})
        peers.append(token)
    print(f'已准备 {len(peers)} 位【测试】玩家；基本条件：{base["region"]} / {base["platform"]} / {base["language"]} / {base["map"]} / {base["difficulty"]}。',flush=True)
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
