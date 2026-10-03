# SPDX-License-Identifier: EUPL-1.2
"""Eccezioni del kit.

Regola: un errore di *trasporto* o di *protocollo* (rete, SOAP Fault, credenziali)
è un'eccezione; un rifiuto *di merito* del servizio (es. codice esito 9999 con la
lista degli errori) NON è un'eccezione, è un esito restituito al chiamante.
Così chi integra distingue "non ho parlato col SAC" da "il SAC ha detto no".
"""


class ErroreKit(Exception):
    """Radice di tutte le eccezioni del kit."""


class ConfigurazioneNonValida(ErroreKit):
    """Credenziali, certificati o parametri mancanti o incoerenti."""


class AmbienteBloccato(ErroreKit):
    """Tentativo di chiamare un ambiente non consentito (es. PRODUZIONE senza flag)."""


class RicettaNonValida(ErroreKit):
    """La ricetta non passa i controlli strutturali locali (prima dell'invio)."""

    def __init__(self, problemi: list[str]):
        self.problemi = list(problemi)
        super().__init__("Ricetta non valida: " + "; ".join(self.problemi))


class ErroreTrasporto(ErroreKit):
    """Errore di rete/HTTP senza una risposta SOAP interpretabile."""

    def __init__(self, messaggio: str, stato_http: int | None = None, corpo: bytes | None = None):
        self.stato_http = stato_http
        self.corpo = corpo
        super().__init__(messaggio)


class ErroreSOAP(ErroreKit):
    """Il servizio ha risposto con un SOAP Fault (es. credenziali invalide, errore di schema)."""

    def __init__(self, faultcode: str, faultstring: str, stato_http: int | None = None, corpo: bytes | None = None):
        self.faultcode = faultcode
        self.faultstring = faultstring
        self.stato_http = stato_http
        self.corpo = corpo
        super().__init__(f"SOAP Fault [{faultcode}] {faultstring} (HTTP {stato_http})")

    @property
    def credenziali_rifiutate(self) -> bool:
        s = self.faultstring.lower()
        return any(k in s for k in ("credenziali", "password scaduta", "utente disabilitato"))
