"""In-memory sliding-window rate limiter + concurrency gate (N013).

N046 aggiunge il budget **per principal**: il tetto globale da solo non impedisce
a una sola app di occupare tutti gli slot e far rifiutare le altre, che e' un
diniego di servizio con la CI verde e le metriche a posto.
"""
from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from threading import Lock

ANONYMOUS_PRINCIPAL = "-"


def principal_key(peer: str, api_key: str | None) -> str:
    """Identita' stabile e non reversibile per la contabilita' del budget (N046).

    Il rate limiter N013 tronca la chiave ai primi 8 caratteri: due chiavi con lo
    stesso prefisso finiscono nello stesso secchio. Il budget non puo' permetterselo
    — due app diverse diventerebbero lo stesso principal — quindi qui si passa il
    digest della chiave intera. Il digest non e' un segreto e non ricostruisce la
    chiave, quindi puo' comparire in metriche e audit; la chiave no, mai.
    """
    if api_key and api_key.strip():
        digest = hashlib.sha256(api_key.strip().encode("utf-8")).hexdigest()[:16]
        return f"key:{digest}"
    if peer and peer.strip():
        return f"peer:{peer.strip()}"
    return ANONYMOUS_PRINCIPAL


class RateLimiter:
    def __init__(self, limit_per_minute: int = 120) -> None:
        self.limit = limit_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = 60.0
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60.0:
                q.popleft()
            return max(0, self.limit - len(q))


class ConcurrencyGate:
    """Tetto globale di richieste in volo (N013) + quota per principal (N046).

    Due tetti, non uno: quello globale protegge il processo, quello per principal
    protegge gli altri chiamanti dal chiamante rumoroso. Un principal che esaurisce
    la sua quota riceve 429 mentre gli altri continuano a essere serviti.

    ``release`` va chiamata con lo **stesso** principal di ``try_acquire``, sempre
    da un `finally`: uno slot non rilasciato non torna piu' indietro e il tetto si
    consuma fino a negare tutto.
    """

    def __init__(self, limit: int = 32, per_principal_limit: int | None = None) -> None:
        self.limit = max(1, int(limit))
        # Una quota piu' alta del tetto globale non e' una quota: viene ridotta.
        if per_principal_limit is None:
            self.per_principal_limit: int | None = None
        else:
            self.per_principal_limit = min(self.limit, max(1, int(per_principal_limit)))
        self._in_flight = 0
        self._per_principal: dict[str, int] = {}
        self._lock = Lock()

    @staticmethod
    def _key(principal: str | None) -> str:
        """Identita' assente o vuota => un unico secchio condiviso.

        Restituire una chiave diversa per ogni chiamante anonimo darebbe a chi non
        si identifica una quota illimitata: basterebbe non mandare la chiave.
        """
        if principal is None:
            return ANONYMOUS_PRINCIPAL
        cleaned = principal.strip()
        return cleaned or ANONYMOUS_PRINCIPAL

    def try_acquire(self, principal: str | None = None) -> bool:
        key = self._key(principal)
        with self._lock:
            if self._in_flight >= self.limit:
                return False
            if (
                self.per_principal_limit is not None
                and self._per_principal.get(key, 0) >= self.per_principal_limit
            ):
                return False
            self._in_flight += 1
            self._per_principal[key] = self._per_principal.get(key, 0) + 1
            return True

    def release(self, principal: str | None = None) -> None:
        key = self._key(principal)
        with self._lock:
            if self._in_flight > 0:
                self._in_flight -= 1
            current = self._per_principal.get(key, 0)
            if current <= 1:
                # Un principal a zero esce dalla mappa: senza questo la memoria
                # cresce con il numero di chiavi viste, non con quelle attive.
                self._per_principal.pop(key, None)
            else:
                self._per_principal[key] = current - 1

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    def in_flight_for(self, principal: str | None = None) -> int:
        key = self._key(principal)
        with self._lock:
            return self._per_principal.get(key, 0)

    def principals(self) -> dict[str, int]:
        """Snapshot dei soli principal con richieste in volo."""
        with self._lock:
            return dict(self._per_principal)
