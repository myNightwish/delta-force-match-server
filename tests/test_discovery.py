import concurrent.futures
import tempfile
import unittest
from app.services.pilot import Pilot
from test_pilot import P

PUBLIC = {**P, 'public': True, 'discovery': 'browse'}

class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Pilot(self.tmp.name + '/discovery.db')
    def tearDown(self):
        self.tmp.cleanup()
    def player(self, **updates):
        t = self.p.register()['token']
        self.p.join(t, {**PUBLIC, **updates})
        return t
    def invite(self, a, b):
        tid = self.p.state(b)['ticket']['id']
        return self.p.invite(a, tid)
    def test_opt_in_and_private_fields(self):
        a = self.player(); self.player(public=False); b = self.player()
        rows = self.p.candidates(a)['items']
        self.assertEqual(len(rows), 1)
        self.assertNotIn('game_id', rows[0]['profile'])
        self.assertNotIn('token', str(rows))
        self.invite(a,b)
        self.assertEqual(len(self.p.candidates(self.p.register()['token'],False)['items']),0)
    def test_browse_before_submission(self):
        self.player(); guest=self.p.register()['token']
        rows=self.p.candidates(guest,False)
        self.assertEqual(len(rows['items']),1)
        self.assertFalse(rows['items'][0]['can_invite'])
        self.assertFalse(self.p.candidates(guest)['can_recommend'])
    def test_mutual_gap_and_all_public_conflicts(self):
        a=self.player(skill=3,gap=2);b=self.player(skill=4,gap=0)
        self.assertEqual(self.p.candidates(a)['total'],0)
        card=self.p.candidates(a,False)['items'][0]
        self.assertTrue(card['assessment']['checks'][2]['ok'])
        self.assertFalse(card['assessment']['checks'][3]['ok'])
        with self.assertRaises(ValueError):self.invite(a,b)
    def test_manual_cross_arm_is_separate(self):
        a=self.player();b=self.player()
        s=self.invite(a,b)
        self.assertEqual(s['squad']['source'],'manual')
        m=self.p.metrics()['groups']
        self.assertEqual(m['manual']['squads'],1)
        self.assertEqual(m['A']['squads']+m['B']['squads'],0)
    def test_deadline_releases_and_preserves_queue_order(self):
        a=self.player();b=self.player();start=self.p.state(a)['ticket']['created']
        s=self.invite(a,b)
        with self.p.db() as db:db.execute("UPDATE squads SET deadline='2000-01-01T00:00:00+00:00' WHERE id=?",(s['squad']['id'],))
        state=self.p.state(a)
        self.assertIsNone(state['squad'])
        self.assertEqual(state['ticket']['status'],'waiting')
        self.assertEqual(state['ticket']['created'],start)
        self.assertEqual(self.p.state(b)['ticket']['status'],'waiting')
        with self.assertRaises(ValueError):self.p.action(a,'confirm',expected_squad_id=s['squad']['id'])
        self.assertEqual(self.p.metrics()['groups']['manual']['timed_out_squads'],1)
    def test_rejection_private_aggregate_reason_and_cooldown(self):
        a=self.player();b=self.player();s=self.invite(a,b)
        self.p.action(a,'reject',{'reason':'skill'},s['squad']['id'])
        state=self.p.state(b)
        self.assertEqual(state['ticket']['status'],'waiting')
        self.assertNotIn('实力',state['notice'])
        self.assertEqual(self.p.candidates(a)['total'],0)
        self.assertEqual(self.p.metrics()['groups']['manual']['decline_reasons']['skill'],1)
        with self.p.db() as db:db.execute("UPDATE pilot_exclusions SET until_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.p.candidates(a)['total'],1)
    def test_skip_does_not_reset_queue(self):
        a=self.player();b=self.player();before=self.p.state(a)['ticket']
        self.p.skip(a,[self.p.state(b)['ticket']['id']])
        self.assertEqual(self.p.candidates(a)['total'],0)
        self.assertEqual(self.p.state(a)['ticket']['id'],before['id'])
        self.assertEqual(self.p.state(a)['ticket']['created'],before['created'])
    def test_concurrent_invites_cannot_double_book(self):
        a=self.player();b=self.player();c=self.player()
        tid=self.p.state(b)['ticket']['id']
        def apply(t):
            try:self.p.invite(t,tid);return True
            except ValueError:return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results=list(pool.map(apply,[a,c]))
        self.assertEqual(sum(results),1)
        self.assertEqual(self.p.metrics()['groups']['manual']['squads'],1)
    def test_stale_confirmation_does_not_accept_new_candidate(self):
        a=self.player();b=self.player();c=self.player()
        old=self.invite(a,b)['squad']['id']
        self.p.action(a,'reject',{'reason':'other'},old)
        current=self.invite(a,c)['squad']['id']
        with self.assertRaises(ValueError):self.p.action(a,'confirm',expected_squad_id=old)
        self.assertFalse(any(m['confirmed'] for m in self.p.state(a)['squad']['members']))
        self.p.action(a,'confirm',expected_squad_id=current)
    def test_pagination(self):
        a=self.player()
        for i in range(8):self.player(nickname=str(i))
        self.assertEqual(len(self.p.candidates(a,page=0)['items']),6)
        self.assertEqual(len(self.p.candidates(a,page=1)['items']),2)

    def test_nonmember_cannot_confirm_someone_elses_proposal(self):
        a=self.player();b=self.player();c=self.player()
        squad=self.invite(a,b)['squad']['id']
        with self.assertRaises(ValueError):self.p.action(c,'confirm',expected_squad_id=squad)
        self.assertFalse(any(m['confirmed'] for m in self.p.state(a)['squad']['members']))

    def test_demo_players_and_squads_excluded_from_metrics(self):
        a=self.player();b=self.player(is_demo=True)
        self.invite(a,b)
        groups=self.p.metrics()['groups']
        self.assertEqual(groups['manual']['squads'],0)
        self.assertEqual(groups['manual']['requests'],0)
        self.assertEqual(groups['A']['assigned']+groups['B']['assigned'],1)

    def test_real_users_sort_before_demo_on_first_page(self):
        a=self.player()
        for i in range(7):self.player(is_demo=True,nickname=f'测试{i}')
        b=self.player(nickname='真实B')
        cards=self.p.candidates(a,False)['items']
        self.assertEqual(cards[0]['profile']['nickname'],'真实B')
        self.assertEqual(len(cards),6)
        self.assertEqual(self.p.candidates(a,False,0,'真实B')['total'],1)

    def test_two_browser_identities_see_each_other_across_assignment(self):
        a=self.player(nickname='真实A');b=self.player(nickname='真实B')
        a_id=self.p.state(a)['ticket']['id'];b_id=self.p.state(b)['ticket']['id']
        self.assertIn(b_id,[c['id'] for c in self.p.candidates(a,False)['items']])
        self.assertIn(a_id,[c['id'] for c in self.p.candidates(b,False)['items']])

    def test_normal_auto_matching_does_not_split_users_into_experiment_pools(self):
        a=self.p.register()['token'];b=self.p.register()['token']
        with self.p.db() as db:
            db.execute("UPDATE participants SET arm='A' WHERE token=?",(a,))
            db.execute("UPDATE participants SET arm='B' WHERE token=?",(b,))
        self.p.join(a,{**PUBLIC,'discovery':'auto'})
        state=self.p.join(b,{**PUBLIC,'discovery':'auto'})
        self.assertEqual(state['squad']['source'],'auto')
        self.assertEqual(self.p.metrics()['groups']['auto']['squads'],1)
