import sqlite3

con = sqlite3.connect("data/local_lab.sqlite")
cur = con.cursor()
cur.execute("UPDATE paper_trades SET status='CLOSED', close_reason='manual_reset' WHERE status='OPEN'")
con.commit()
print("closed", cur.rowcount)
con.close()
