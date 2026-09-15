"""
Authentication building blocks (Milestone 7).

    auth/credentials.py      email normalisation, the password policy, password hashing
    auth/session_tokens.py   the random cookie token and the hash of it that is stored

Pure functions only: no database and no HTTP. Storing users and sessions is
`services/auth.py`; turning a request into a signed-in user is `api/deps.py`.
"""
