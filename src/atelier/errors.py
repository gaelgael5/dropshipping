from __future__ import annotations


class StartupError(Exception):
    """Condition qui empêche l'atelier de démarrer.

    Son message est destiné à un humain : il est affiché tel quel, sans trace
    d'exception. Il ne doit donc jamais contenir de secret.
    """
