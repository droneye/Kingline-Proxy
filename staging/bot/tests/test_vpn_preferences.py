import importlib.util,os,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from types import SimpleNamespace
import vpn_preferences as pref


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'DB_PATH':self.folder.name+'/business.db','KINGLINE_XHTTP_READY':'1'});self.env.start()
        with sqlite3.connect(os.environ['DB_PATH']) as c:c.executescript('CREATE TABLE users(tg_id INTEGER PRIMARY KEY,is_blocked INTEGER);INSERT INTO users VALUES(101,0),(102,0),(103,1)')
    def tearDown(self):self.env.stop();self.folder.cleanup()
    def test_default_and_isolation_and_repeated_save(self):
        self.assertEqual(pref.read(101)['transport'],'raw')
        self.assertEqual(pref.save(101,'xhttp')['transport'],'xhttp')
        self.assertEqual(pref.save(101,'xhttp')['transport'],'xhttp')
        self.assertEqual(pref.read(102)['transport'],'raw')
        self.assertEqual(pref.save(101,'raw')['transport'],'raw')
    def test_unknown_blocked_and_not_ready(self):
        for user in (999,103):
            with self.assertRaises(ValueError):pref.save(user,'xhttp')
        for value in ('tls','XHTTP',None,[],{'transport':'raw'}):
            with self.assertRaises(ValueError):pref.save(101,value)
        with patch.dict(os.environ,{'KINGLINE_XHTTP_READY':'0'}):
            with self.assertRaises(ValueError):pref.save(101,'xhttp')
            self.assertEqual(pref.save(101,'raw')['transport'],'raw')
    def test_linking_accounts_keeps_latest_explicit_choice(self):
        pref.save(101,'xhttp');pref.save(102,'raw')
        with sqlite3.connect(os.environ['DB_PATH']) as c:
            c.execute('UPDATE vpn_preferences SET updated_at=100 WHERE tg_id=101')
            c.execute('UPDATE vpn_preferences SET updated_at=50 WHERE tg_id=102')
            pref.merge(c,101,102)
        self.assertEqual(pref.read(102)['transport'],'xhttp')
        with sqlite3.connect(os.environ['DB_PATH']) as c:
            self.assertIsNone(c.execute('SELECT 1 FROM vpn_preferences WHERE tg_id=101').fetchone())
            pref.merge(c,102,102)
        self.assertEqual(pref.read(102)['transport'],'xhttp')
    def test_bot_names_real_transports_and_never_claims_immunity(self):
        file=Path(__file__).parents[1]/'handlers/vpn_mode.py';spec=importlib.util.spec_from_file_location('mode_ui',file);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        text,keyboard=module.screen(101)
        self.assertIn('TCP/RAW',text);self.assertIn('XHTTP',text);self.assertIn('iOS',text);self.assertIn('Android',text)
        self.assertIn('прежнюю подписку',text);self.assertNotIn('более защищ',text)
        callbacks=[b.callback_data for row in keyboard.inline_keyboard for b in row]
        self.assertEqual(callbacks,['vpn_mode_raw','vpn_mode_xhttp'])

if __name__=='__main__':unittest.main()
