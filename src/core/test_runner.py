"""Lanceur de tests qui INTERDIT tout accès réseau sortant.

Un test ne doit jamais contacter GitHub, Discord, Notion ou Gemini : il enverrait de vraies requêtes (avec de faux
jetons, ou pire, de vrais), et son résultat dépendrait d'Internet. Ici, toute connexion hors de la machine locale
lève une erreur claire. La base PostgreSQL de test (127.0.0.1) reste autorisée.
"""

import socket
from typing import Any

from django.test.runner import DiscoverRunner

_LOCAL = {"localhost", "127.0.0.1", "::1", ""}
_real_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo


class NetworkBlocked(RuntimeError):
    pass


def _is_local(host: Any) -> bool:
    return isinstance(host, str) and (host in _LOCAL or host.startswith("127."))


def _guarded_connect(self, address):
    # Les sockets Unix (chaîne ou octets) et le bouclage restent permis ; tout le reste est bloqué.
    if isinstance(address, tuple) and not _is_local(address[0]):
        raise NetworkBlocked(f"accès réseau interdit dans les tests : {address[0]!r}")
    return _real_connect(self, address)


def _guarded_getaddrinfo(host, *args, **kwargs):
    if host is not None and not _is_local(host):
        raise NetworkBlocked(f"résolution DNS interdite dans les tests : {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


class OfflineRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        socket.socket.connect = _guarded_connect
        socket.getaddrinfo = _guarded_getaddrinfo

    def teardown_test_environment(self, **kwargs):
        socket.socket.connect = _real_connect
        socket.getaddrinfo = _real_getaddrinfo
        super().teardown_test_environment(**kwargs)
