"""
Authentication building blocks (Milestone 7).

    auth/credentials.py   email normalisation, the password policy, password hashing

Pure functions only: no database and no HTTP. Storing users and sessions is
`services/auth.py`; turning a request into a signed-in user is `api/deps.py`.
"""
