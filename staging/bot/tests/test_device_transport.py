import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import patch

import vpn_preferences as pref


def at(day,hour,minute=0):
    return datetime(2026,10,day,hour,minute,tzinfo=pref.MOSCOW).timestamp()


class DeviceTransportTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'DB_PATH':self.folder.name+'/business.db','KINGLINE_XHTTP_READY':'1'})
        self.env.start()
        with sqlite3.connect(os.environ['DB_PATH']) as c:
            c.executescript('CREATE TABLE users(tg_id INTEGER PRIMARY KEY,is_blocked INTEGER);INSERT INTO users VALUES(101,0),(102,0),(103,1)')
    def tearDown(self):
        self.env.stop();self.folder.cleanup()
    def observe(self,actor=101,os_name='android',now=None,source='site'):
        pref.observe(actor,platform_hint=os_name,source=source,now=at(23,12) if now is None else now)
    def row(self,actor=101):
        with sqlite3.connect(os.environ['DB_PATH']) as c:
            c.row_factory=sqlite3.Row
            return dict(c.execute('SELECT * FROM vpn_preferences WHERE tg_id=?',(actor,)).fetchone())

    def test_platforms_website_telegram_and_ipad_desktop_mode(self):
        cases=[('Mozilla/5.0 (Linux; Android 15; Pixel)','','','android'),
               ('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)','','','ios'),
               ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Mobile/15E148','','','ios'),
               ('Mozilla/5.0 (Windows NT 10.0)','','','windows'),
               ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)','','','macos'),
               ('Mozilla/5.0 (X11; Linux x86_64)','','','linux'),
               ('','"Android"','','android'),('opaque-webview','','ios','ios'),
               ('','','android','android'),('robot','','bad-value','unknown')]
        for ua,hint,app,expected in cases:
            with self.subTest(expected=expected,ua=ua):self.assertEqual(pref.platform(ua,hint,app),expected)

    def test_android_starts_raw_and_switches_only_next_night(self):
        self.observe()
        self.assertEqual(pref.read(101)['transport'],'raw')
        self.assertEqual(self.row()['auto_due_at'],at(24,3,30))
        self.assertEqual(pref.apply_due(at(23,23))['changed'],0)
        self.assertEqual(pref.apply_due(at(24,3,29))['changed'],0)
        self.assertEqual(pref.apply_due(at(24,3,30))['xhttp'],1)
        self.assertEqual(pref.read(101)['transport'],'xhttp')
        self.assertEqual(pref.apply_due(at(24,3,31))['changed'],0)
        self.assertEqual(self.row()['selection_source'],'auto')

    def test_new_observations_do_not_postpone_pending_change(self):
        self.observe();due=self.row()['auto_due_at']
        self.observe(now=at(24,3,35))
        self.assertEqual(self.row()['auto_due_at'],due)
        self.assertEqual(pref.apply_due(at(24,3,36))['changed'],1)

    def test_missed_night_waits_for_another_night(self):
        self.observe()
        for hour in (5,10,23):self.assertEqual(pref.apply_due(at(24,hour))['changed'],0)
        self.assertEqual(pref.read(101)['transport'],'raw')
        self.assertEqual(pref.apply_due(at(25,3,30))['changed'],1)

    def test_manual_even_same_mode_permanently_cancels_automatic(self):
        self.observe();pref.save(101,'raw')
        first=self.row()['manual_selected_at']
        self.assertIsNone(self.row()['auto_due_at'])
        self.observe(now=at(25,12))
        self.assertEqual(pref.apply_due(at(26,3,30))['changed'],0)
        self.assertEqual(pref.read(101)['transport'],'raw')
        pref.save(101,'xhttp');self.observe(os_name='ios',now=at(26,12))
        self.assertEqual(pref.apply_due(at(27,3,30))['changed'],0)
        self.assertEqual(pref.read(101)['transport'],'xhttp')
        self.assertEqual(self.row()['manual_selected_at'],first)

    def test_ios_and_android_keep_raw_in_either_order(self):
        for actor,order in [(101,['ios','android']),(102,['android','ios'])]:
            for os_name in order:self.observe(actor,os_name)
            self.assertIsNotNone(self.row(actor)['auto_due_at'])
        result=pref.apply_due(at(24,3,30))
        self.assertEqual(result['changed'],0);self.assertEqual(result['completed'],2)
        for actor in (101,102):self.assertIsNotNone(self.row(actor)['auto_completed_at'])

    def test_later_ios_never_repeats_completed_automatic_choice(self):
        self.observe();pref.apply_due(at(24,3,30))
        self.observe(os_name='ios',now=at(24,12))
        self.assertEqual(pref.read(101)['transport'],'xhttp')
        self.assertIsNone(self.row()['auto_due_at'])
        self.assertEqual(pref.apply_due(at(25,3,30))['completed'],0)
        self.assertEqual(pref.read(101)['transport'],'xhttp')

    def test_desktop_unknown_blocked_and_missing_account_do_not_switch(self):
        self.observe(os_name='windows');self.observe(102,'unknown')
        self.observe(103);self.observe(999)
        self.assertEqual(pref.apply_due(at(24,3,30))['changed'],0)
        self.assertFalse(pref.account_devices(103)['devices'])
        self.assertFalse(pref.account_devices(999)['devices'])

    def test_storage_is_bounded_per_os_and_preserves_both_login_sources(self):
        self.observe()
        for i in range(100):self.observe(now=at(23,12)+i,source='miniapp')
        data=pref.account_devices(101)
        self.assertEqual(len(data['devices']),1)
        self.assertEqual(data['devices'][0]['source'],'site_and_miniapp')
        self.assertNotIn('userAgent',data['devices'][0])
        self.assertEqual(self.row()['auto_due_at'],at(24,3,30))

    def test_old_explicit_choices_are_migrated_as_manual(self):
        with sqlite3.connect(os.environ['DB_PATH']) as c:
            c.executescript("CREATE TABLE vpn_preferences(tg_id INTEGER PRIMARY KEY,transport TEXT,updated_at REAL);INSERT INTO vpn_preferences VALUES(101,'raw',10),(102,'xhttp',20)")
        self.observe();self.observe(102,'ios')
        self.assertEqual(self.row()['manual_selected_at'],10)
        self.assertEqual(pref.apply_due(at(24,3,30))['changed'],0)
        self.assertEqual(pref.read(102)['transport'],'xhttp')

    def test_parallel_first_schema_migration_and_bot_manual_write(self):
        with sqlite3.connect(os.environ['DB_PATH']) as c:
            c.executescript("CREATE TABLE vpn_preferences(tg_id INTEGER PRIMARY KEY,transport TEXT,updated_at REAL);INSERT INTO vpn_preferences VALUES(101,'xhttp',10)")
        with ThreadPoolExecutor(2) as pool:
            tasks=[pool.submit(pref.save,101,'raw'),pool.submit(self.observe,102)]
            for task in tasks:task.result()
        self.assertEqual(pref.read(101)['transport'],'raw')
        self.assertTrue(pref.account_devices(101)['manual'])
        self.assertIsNotNone(self.row(102)['auto_due_at'])

    def test_manual_beats_newer_auto_when_accounts_merge(self):
        pref.save(101,'raw');self.observe(102)
        pref.apply_due(at(24,3,30))
        with sqlite3.connect(os.environ['DB_PATH']) as c:
            c.execute("UPDATE vpn_preferences SET updated_at=1 WHERE tg_id=101")
            pref.merge(c,101,102)
        self.assertEqual(pref.read(102)['transport'],'raw')
        self.assertTrue(pref.account_devices(102)['manual'])
        self.assertFalse(pref.account_devices(101)['devices'])

    def test_merge_combines_os_but_never_reopens_completed_automatic_choice(self):
        self.observe();pref.apply_due(at(24,3,30));self.observe(102,'ios',now=at(23,11))
        with patch.object(pref.time,'time',return_value=at(24,12)):
            with sqlite3.connect(os.environ['DB_PATH']) as c:pref.merge(c,101,102)
        self.assertEqual({d['family'] for d in pref.account_devices(102)['devices']},{'android','ios'})
        self.assertEqual(pref.apply_due(at(25,3,30))['completed'],0)
        self.assertEqual(pref.read(102)['transport'],'xhttp')
        self.assertIsNotNone(self.row(102)['auto_completed_at'])

    def test_ios_default_is_a_completed_decision_even_without_transport_change(self):
        self.observe(os_name='ios')
        result=pref.apply_due(at(24,3,30))
        self.assertEqual(result['changed'],0);self.assertEqual(result['completed'],1)
        self.observe(now=at(25,12));self.observe(os_name='ios',now=at(26,12))
        self.assertIsNone(self.row()['auto_due_at'])
        self.assertEqual(pref.apply_due(at(27,3,30))['completed'],0)

    def test_existing_automatic_selection_migrates_to_completed(self):
        with sqlite3.connect(os.environ['DB_PATH']) as c:
            c.executescript("""CREATE TABLE vpn_preferences(tg_id INTEGER PRIMARY KEY,transport TEXT,updated_at REAL,
                selection_source TEXT,manual_selected_at REAL,auto_due_at REAL);
                INSERT INTO vpn_preferences VALUES(101,'xhttp',123,'auto',NULL,456)""")
        self.observe(os_name='ios')
        self.assertEqual(self.row()['auto_completed_at'],123)
        self.assertIsNone(self.row()['auto_due_at'])
        self.assertEqual(pref.apply_due(at(24,3,30))['changed'],0)

    def test_only_cabinet_observations_can_choose_automatic_transport(self):
        with sqlite3.connect(os.environ['DB_PATH']) as c:
            pref.schema(c)
            c.execute("INSERT INTO vpn_preferences(tg_id,transport,updated_at,selection_source) VALUES(101,'raw',1,'default')")
            c.execute("INSERT INTO account_platforms VALUES(101,'android',1,1,0,0)")
            pref.schedule(c,101,at(23,12))
        self.assertIsNone(self.row()['auto_due_at'])

    def test_admin_can_prepare_blocked_user_without_unblocking_or_creating_user(self):
        pref.save(103,'xhttp',allow_blocked=True)
        with sqlite3.connect(os.environ['DB_PATH']) as c:self.assertEqual(c.execute('SELECT is_blocked FROM users WHERE tg_id=103').fetchone()[0],1)
        self.assertTrue(pref.account_devices(103)['manual'])
        with self.assertRaises(ValueError):pref.save(999,'raw',allow_blocked=True)

    def test_disabled_xhttp_does_not_apply_pending_change(self):
        self.observe()
        with patch.dict(os.environ,{'KINGLINE_XHTTP_READY':'0'}):self.assertEqual(pref.apply_due(at(24,3,30))['changed'],0)
        self.assertIsNotNone(self.row()['auto_due_at'])

    def test_forgetting_deleted_account_removes_only_its_metadata(self):
        self.observe();self.observe(102,'ios');pref.save(101,'raw')
        with sqlite3.connect(os.environ['DB_PATH']) as c:pref.forget(c.cursor(),101)
        self.assertFalse(pref.account_devices(101)['devices'])
        self.assertTrue(pref.account_devices(102)['devices'])

    def test_racing_manual_and_nightly_change_always_end_with_manual(self):
        for _ in range(8):
            with sqlite3.connect(os.environ['DB_PATH']) as c:
                pref.schema(c);c.execute('DELETE FROM vpn_preferences');c.execute('DELETE FROM account_platforms')
            self.observe()
            with ThreadPoolExecutor(2) as pool:
                tasks=[pool.submit(pref.apply_due,at(24,3,30)),pool.submit(pref.save,101,'raw')]
                for task in tasks:task.result()
            self.assertEqual(pref.read(101)['transport'],'raw')
            self.assertTrue(pref.account_devices(101)['manual'])


if __name__=='__main__':unittest.main()
