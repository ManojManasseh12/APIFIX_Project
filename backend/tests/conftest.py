import os

# Tests must not talk to a real retrieval database. Force the local store even
# when the developer's .env points at Supabase, so the suite is deterministic
# and cannot read or delete production rows.
os.environ["RETRIEVAL_BACKEND"] = "local"
os.environ["DATABASE_URL"] = ""
