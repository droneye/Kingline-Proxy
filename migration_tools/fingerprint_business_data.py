"""Compare stable identities using digests, without exporting passwords or tokens."""
import hashlib
import json
import sqlite3
import sys
c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)
c.execute('PRAGMA query_only=ON')
queries={
 'users':'SELECT tg_id,sub_id,client_id1,client_id2,client_id3,created_at FROM users ORDER BY tg_id',
 'accounts':'SELECT email,password_hash,linked_tg_id,created_at FROM web_accounts ORDER BY email',
 'payments':'SELECT payment_id,tg_id,amount,created_at FROM payments ORDER BY payment_id',
}
result={}
for table,sql in queries.items():
 result[table]={hashlib.sha256(str(row[0]).encode()).hexdigest():hashlib.sha256(json.dumps(row,ensure_ascii=False).encode()).hexdigest() for row in c.execute(sql)}
print(json.dumps(result))
