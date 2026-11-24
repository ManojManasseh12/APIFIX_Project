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

# And for the GitHub connector: tests must never call GitHub with a real
# token. Individual tests set a fake token and mock the HTTP transport.
os.environ["GITHUB_PAT"] = ""
os.environ["GITHUB_DEFAULT_REPO"] = ""

# Google Workspace MCP: same rule. Tests that exercise the connector set
# these on the settings object and mock the network.
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["GOOGLE_CLIENT_SECRET"] = ""
os.environ["MCP_TOKEN_ENCRYPTION_KEY"] = ""

# LangSmith: tests must never send traces to a real project. The app treats
# tracing as off unless both the flag and a key are set.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGSMITH_API_KEY"] = ""
