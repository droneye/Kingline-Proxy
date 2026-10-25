import ast
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import compact_metrics as m

class CompactAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=str(Path(self.tmp.name)/'business.db');self.logs=str(Path(self.tmp.name)/'metrics.db')
        self.inbox=Path(self.tmp.name)/'inbox';self.inbox.mkdir()
        with closing(sqlite3.connect(self.db)) as c,c:
            c.execute('CREATE TABLE users(username TEXT,balance INTEGER)');c.execute("INSERT INTO users VALUES('alice',1800)")
            for node in ast.walk(ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())):
                if isinstance(node,ast.Constant) and isinstance(node.value,str) and 'CREATE TABLE IF NOT EXISTS web_xui_' in node.value:
                    c.execute(node.value)
            m.prepare(self.logs,c)
        self.record={'t':int(time.time()),'u':'native-a','ip':'192.0.2.1','s':'DE','d':'example.org','p':443,'c':12}
    def deliver(self,name,records):
        bid=hashlib.sha256(name.encode()).hexdigest();p=self.inbox/(bid+'.json')
        p.write_text(json.dumps({'id':bid,'records':records}));return p
    def test_replayed_batch_is_exactly_once_and_does_not_touch_balance(self):
        self.deliver('a',[self.record]);m.ingest(self.logs,self.inbox,{'native-a':'alice'})
        self.deliver('a',[self.record]);m.ingest(self.logs,self.inbox,{'native-a':'alice'})
        with m.connect(self.db,self.logs) as c:
            self.assertEqual(c.execute('SELECT SUM(count) FROM web_xui_access_events').fetchone()[0],12)
            self.assertEqual(c.execute('SELECT balance FROM users').fetchone()[0],1800)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM web_xui_access_events').fetchone()[0],0)
    def test_repeated_connections_are_compacted_and_unknown_user_dropped(self):
        self.deliver('a',[self.record]*100+[dict(self.record,u='unknown')]);m.ingest(self.logs,self.inbox,{'native-a':'alice'})
        with m.connect(self.db,self.logs) as c:
            self.assertEqual(tuple(c.execute('SELECT count(*),SUM(count) FROM web_xui_access_events').fetchone()),(1,1200))
            self.assertEqual(c.execute('SELECT count(*) FROM web_xui_ip_history').fetchone()[0],1)
    def test_legacy_upsert_and_delete_work_in_separate_database(self):
        self.deliver('a',[self.record]);m.ingest(self.logs,self.inbox,{'native-a':'alice'})
        with m.connect(self.db,self.logs) as c:
            for label in ('Phone','Tablet'):
                c.execute("INSERT INTO web_xui_device_profiles(username,device_label,updated_at) VALUES('alice',?,?) ON CONFLICT(username) DO UPDATE SET device_label=excluded.device_label",(label,m.iso(time.time())))
            self.assertEqual(c.execute('SELECT device_label FROM web_xui_device_profiles').fetchone()[0],'Tablet')
            c.execute("DELETE FROM web_xui_access_events WHERE username='alice'")
            self.assertEqual(c.execute('SELECT count(*) FROM web_xui_access_events').fetchone()[0],0)
    def test_retention_preserves_daily_summary_and_rejects_urls(self):
        self.deliver('a',[self.record,dict(self.record,d='example.org/path?token=private')]);m.ingest(self.logs,self.inbox,{'native-a':'alice'})
        with closing(sqlite3.connect(self.logs)) as c,c:
            c.execute('UPDATE access_buckets SET last_at=?',(int(time.time())-8*86400,))
        m.maintain(self.logs)
        with closing(sqlite3.connect(self.logs)) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM access_buckets').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT SUM(count) FROM domain_daily').fetchone()[0],12)
    def test_counters_baseline_reset_and_repeated_sampling(self):
        with closing(sqlite3.connect(self.logs)) as c,c:
            self.assertEqual(m.traffic_delta(c,10,5000000000),0)
            self.assertEqual(m.traffic_delta(c,10,5000001000),1000)
            self.assertEqual(m.traffic_delta(c,10,5000001000),1000)
            self.assertEqual(m.traffic_delta(c,10,0),1000)
            self.assertEqual(m.traffic_delta(c,10,25),1025)
            self.assertEqual(m.traffic_delta(c,11,9000000),0)

    def test_mixed_case_panel_username_matches_legacy_normalized_queries(self):
        self.deliver('a',[self.record]);m.ingest(self.logs,self.inbox,{'native-a':'MiXeD'})
        with m.connect(self.db,self.logs) as c:
            self.assertEqual(c.execute("SELECT SUM(count) FROM web_xui_access_events WHERE username='mixed'").fetchone()[0],12)
            self.assertEqual(c.execute("SELECT seen_count FROM web_xui_ip_history WHERE username='mixed'").fetchone()[0],12)

    def test_context_closes_connections(self):
        with m.connect(self.db,self.logs) as c:c.execute('SELECT 1')
        with self.assertRaises(sqlite3.ProgrammingError):c.execute('SELECT 1')

if __name__=='__main__':unittest.main()
