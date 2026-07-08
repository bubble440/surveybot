import psycopg2

conn = psycopg2.connect(
    host="surveybot-db.fly.dev",
    port=5432,
    dbname="postgres",
    user="surveybot_client",
    password="p@ssw0rD!123",
    sslmode="require",
)
conn.autocommit = False
cur = conn.cursor()
cur.execute("SELECT * FROM check_license(%s)", ("Wilfried",))
print(cur.fetchone())
conn.rollback()
conn.close()
print("OK - rollback effectue")
