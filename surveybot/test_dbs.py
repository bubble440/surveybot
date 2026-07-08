import psycopg2

conn = psycopg2.connect(
    "postgres://surveybot_client:p%40ssw0rD%21123@surveybot-db.fly.dev:5432/postgres?sslmode=require"
)
conn.autocommit = False
cur = conn.cursor()
cur.execute("SELECT * FROM check_license(%s)", ("Wilfried",))
print(cur.fetchone())
conn.rollback()
conn.close()
print("OK - rollback effectue")