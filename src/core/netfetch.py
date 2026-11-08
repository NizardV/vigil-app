"""Téléchargement HTTP sûr d'une URL fournie par un utilisateur (flux RSS, etc.). Protège contre le SSRF.

Une URL saisie par l'utilisateur ne doit jamais permettre d'atteindre le réseau interne (127.0.0.1, services
Docker, métadonnées cloud 169.254.169.254…). Garanties :
- schémas http/https uniquement, ports 80/443/8080/8443, pas d'identifiants dans l'URL ;
- le nom d'hôte est résolu, et la connexion est **épinglée sur l'adresse validée** (pas de seconde résolution
  qui permettrait un « DNS rebinding ») ; toute adresse non publique est refusée ;
- redirections suivies à la main (3 max), chacune revalidée ; taille et durée bornées ;
- jamais de décompression (`Accept-Encoding: identity`) : pas de bombe de décompression.
"""

import http.client
import ipaddress
import socket
import ssl
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
ALLOWED_PORTS = {80, 443, 8080, 8443}
USER_AGENT = "Vigil/1.0 (veille personnelle)"
_REDIRECTS = {301, 302, 303, 307, 308}

Resolver = Callable[..., list]


class FetchError(Exception):
    """URL refusée ou téléchargement impossible. Le message est lisible et ne contient aucun secret."""


@dataclass(frozen=True)
class Target:
    scheme: str
    hostname: str
    port: int
    path: str
    ip: str


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:  # ::ffff:127.0.0.1 = 127.0.0.1 déguisé
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def validate_target(url: str, *, resolver: Resolver = socket.getaddrinfo) -> Target:
    """Valide l'URL et renvoie la cible avec l'adresse IP publique sur laquelle se connecter."""
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        port = parsed.port
    except ValueError:
        raise FetchError("URL invalide") from None
    if parsed.scheme not in ("http", "https"):
        raise FetchError("seuls les schémas http et https sont autorisés")
    if parsed.username or parsed.password:
        raise FetchError("les identifiants dans l'URL sont refusés")
    hostname = parsed.hostname
    if not hostname:
        raise FetchError("URL sans nom d'hôte")
    port = port or (443 if parsed.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise FetchError(f"port {port} non autorisé")
    try:
        infos = resolver(hostname, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise FetchError("nom d'hôte introuvable") from None
    addresses = list(dict.fromkeys(info[4][0].split("%")[0] for info in infos))
    if not addresses:
        raise FetchError("nom d'hôte introuvable")
    # Une seule adresse non publique suffit à refuser : le nom pourrait pointer vers le réseau interne.
    if not all(_is_public(a) for a in addresses):
        raise FetchError("l'adresse n'est pas publique (réseau interne refusé)")
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return Target(parsed.scheme, hostname, port, path, addresses[0])


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Se connecte à l'IP validée, sans nouvelle résolution DNS."""

    def __init__(self, target: Target, timeout: float):
        super().__init__(target.hostname, target.port, timeout=timeout)
        self._ip = target.ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Se connecte à l'IP validée, mais vérifie le certificat pour le NOM d'hôte (SNI correct)."""

    def __init__(self, target: Target, timeout: float):
        context = ssl.create_default_context()
        # Sans ALPN, certains pare-feux (ex. schneier.com) répondent 403 (http.client l'annonce, pas ce contexte).
        context.set_alpn_protocols(["http/1.1"])
        super().__init__(target.hostname, target.port, timeout=timeout, context=context)
        self._ip = target.ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _request(target: Target, timeout: float, max_bytes: int) -> tuple[int, dict[str, str], bytes]:
    cls = _PinnedHTTPSConnection if target.scheme == "https" else _PinnedHTTPConnection
    connection = cls(target, timeout)
    try:
        connection.request(
            "GET",
            target.path,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity", "Accept": "*/*"},
        )
        response = connection.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}
        body = response.read(max_bytes + 1)
        return response.status, headers, body
    except (OSError, http.client.HTTPException, ssl.SSLError):
        raise FetchError("échec de la connexion") from None
    finally:
        connection.close()


def fetch_public(
    url: str,
    *,
    timeout: float = 10.0,
    max_bytes: int = MAX_BYTES,
    resolver: Resolver = socket.getaddrinfo,
    request: Callable[..., tuple[int, dict[str, str], bytes]] | None = None,
) -> bytes:
    """Télécharge une URL publique et renvoie son contenu (au plus `max_bytes`)."""
    do_request: Any = request or _request
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        target = validate_target(current, resolver=resolver)
        status, headers, body = do_request(target, timeout, max_bytes)
        if status in _REDIRECTS and headers.get("location"):
            current = urllib.parse.urljoin(current, headers["location"])  # chaque redirection est revalidée
            continue
        if status != 200:
            raise FetchError(f"le serveur a répondu {status}")
        if len(body) > max_bytes:
            raise FetchError("contenu trop volumineux")
        return body
    raise FetchError("trop de redirections")
