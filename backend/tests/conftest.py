import os

# Tests must not talk to a real retrieval database. Force the local store even
# when the developer's .env points at Supabase, so the suite is deterministic
# and cannot read or delete production rows.
os.environ["RETRIEVAL_BACKEND"] = "local"
os.environ["DATABASE_URL"] = ""

# Same for authentication: tests run with auth off (like local dev), even
# when the developer's .env points at a real Supabase project.
os.environ["SUPABASE_AUTH_URL"] = ""
os.environ["SUPABASE_AUTH_JWKS_URL"] = ""
