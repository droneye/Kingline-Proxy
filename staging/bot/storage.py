"""SQLite transaction contexts release their file handles deterministically."""
import sqlite3

class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try: return super().__exit__(*args)
        finally: self.close()

def connect(*args, **kwargs):
    kwargs.setdefault('timeout',15)
    kwargs.setdefault('factory',ClosingConnection)
    return sqlite3.connect(*args,**kwargs)
