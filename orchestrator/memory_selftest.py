from memory_store import ensure_doc, add_chunk, build_context, DB_PATH
import sqlite3
import time

DOC_ID = "phase_d_selftest"

# Ensure doc exists (stable id so we can re-run to prove persistence)
ensure_doc(source="selftest", meta={"phase":"D"}, doc_id=DOC_ID)

# Add a tiny exchange each run (proves append + recall across restarts)
ts = int(time.time()*1000)
add_chunk(DOC_ID, "user",      f"[{ts}] Remember this: my favorite color is green.")
add_chunk(DOC_ID, "assistant", f"[{ts}] Noted. Favorite color is green.")

# Build retrieval context for a query
ctx = build_context("favorite color", k=6, max_chars=400)

# Inspect DB to count rows for this doc
con = sqlite3.connect(DB_PATH)
n = con.execute("SELECT COUNT(*) FROM chunks WHERE doc_id=?", (DOC_ID,)).fetchone()[0]
latest = con.execute("SELECT role,text FROM chunks WHERE doc_id=? ORDER BY ts DESC LIMIT 2", (DOC_ID,)).fetchall()
con.close()

print("doc_id:", DOC_ID)
print("chunks_for_doc:", n)
print("last_two:", [(r[0], r[1][:60]) for r in latest])
print("context:", ctx)
