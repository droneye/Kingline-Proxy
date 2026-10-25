import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storage import connect

class StorageLifetimeTests(unittest.TestCase):
    def test_success_commits_and_closes(self):
        with tempfile.TemporaryDirectory() as root:
            path=os.path.join(root,'test.db')
            with connect(path) as c:
                c.execute('CREATE TABLE t(x)');c.execute('INSERT INTO t VALUES(5)')
            with self.assertRaises(sqlite3.ProgrammingError):c.execute('SELECT 1')
            with connect(path) as c:self.assertEqual(c.execute('SELECT x FROM t').fetchone()[0],5)
    def test_error_rolls_back_and_closes(self):
        with tempfile.TemporaryDirectory() as root:
            path=os.path.join(root,'test.db')
            with connect(path) as c:c.execute('CREATE TABLE t(x)')
            with self.assertRaises(RuntimeError):
                with connect(path) as c:
                    c.execute('INSERT INTO t VALUES(5)');raise RuntimeError('interrupted')
            with self.assertRaises(sqlite3.ProgrammingError):c.execute('SELECT 1')
            with connect(path) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM t').fetchone()[0],0)

if __name__=='__main__':unittest.main()
