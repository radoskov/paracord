# Credential Recovery Runbook

Credential recovery must be performed from the server PC or inside the backend container by an operator with database access.

Planned command:

```bash
python scripts/reset_admin_password.py
```

Expected behavior:

1. Prompt for account username.
2. Prompt for new password twice without echo.
3. Hash password.
4. Invalidate active sessions/tokens.
5. Write an `auth.password_reset_cli` audit event with the revocation count.
6. Print a success/failure message.

No unauthenticated web password-reset route should be implemented.
