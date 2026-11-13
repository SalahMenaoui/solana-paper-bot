"""
Client HTTP partagé : une seule fonction get_json() qui
  1. respecte une pause minimale entre deux appels au même service (rate limit),
  2. réessaie avec un backoff exponentiel en cas d'erreur temporaire (429, 5xx, réseau),
  3. lève ApiError si ça échoue quand même -> l'appelant décide quoi faire.
"""
import time
from urllib.parse import urlparse

import requests

from bot import config


class ApiError(Exception):
    """Erreur d'API après toutes les tentatives (ou erreur non récupérable)."""


_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT

# Heure (time.monotonic) du dernier appel pour chaque service (hostname).
_last_call: dict[str, float] = {}


def _wait_turn(host: str, min_interval_s: float) -> None:
    """Dort juste ce qu'il faut pour respecter min_interval_s depuis le dernier appel."""
    elapsed = time.monotonic() - _last_call.get(host, 0.0)
    if elapsed < min_interval_s:
        time.sleep(min_interval_s - elapsed)
    _last_call[host] = time.monotonic()


def _retry_delay(attempt: int, response: requests.Response | None) -> float:
    """Durée d'attente avant le prochain essai. Utilise Retry-After si le serveur le donne."""
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), config.BACKOFF_MAX_S)
    return min(config.BACKOFF_BASE_S * (2 ** attempt), config.BACKOFF_MAX_S)


def get_json(url: str, min_interval_s: float, max_retries: int = config.MAX_RETRIES):
    """GET url et retourne le JSON décodé. Lève ApiError en cas d'échec définitif."""
    host = urlparse(url).hostname or ""
    last_problem = ""

    for attempt in range(max_retries + 1):
        _wait_turn(host, min_interval_s)
        response = None
        try:
            response = _session.get(url, timeout=config.HTTP_TIMEOUT_S)
        except requests.RequestException as exc:
            last_problem = f"réseau ({exc.__class__.__name__})"
        else:
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    raise ApiError(f"{host}: réponse non-JSON")
            # 429 = trop de requêtes, 5xx = problème côté serveur -> on réessaie.
            if response.status_code == 429 or response.status_code >= 500:
                last_problem = f"HTTP {response.status_code}"
            else:
                # 400, 404... : inutile de réessayer.
                raise ApiError(f"{host}: HTTP {response.status_code}")

        if attempt < max_retries:
            time.sleep(_retry_delay(attempt, response))

    raise ApiError(f"{host}: échec après {max_retries + 1} essais ({last_problem})")
