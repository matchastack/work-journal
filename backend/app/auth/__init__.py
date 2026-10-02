"""Sign-in with GitHub for the accounts on an allowlist, and the browser sessions that follow.

Routes that act for the signed-in user take a `CurrentUser` argument (`sessions.py`), which also
checks the CSRF header on requests that change something.
"""
