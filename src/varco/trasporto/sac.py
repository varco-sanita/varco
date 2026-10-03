# SPDX-License-Identifier: EUPL-1.2
"""Canale verso il SAC (Sistema di Accoglienza Centrale, Sogei/MEF).

Aggiunge a un `Trasporto` generico ciò che è specifico del SAC:
  - URL base dell'ambiente;
  - Basic authentication forzata (preemptive) — par. 3.7 delle specifiche;
  - header Authorization2F (id di sessione del secondo fattore);
  - SOAPAction dal WSDL.

Header Authorization2F: in ambiente di TEST il kit MEF (infoAutenticazione2Fattori.txt)
indica il formato  "Bearer <utenza>-<anno>-<mese>-RICETTA-DEM-PRESCRITTORE".
In produzione l'id di sessione arriva da un vero flusso a due fattori, che questo
modulo NON implementa: va passato `sessione_2f` dall'esterno.
"""

from __future__ import annotations

import base64
import datetime as _dt
import xml.etree.ElementTree as ET
from typing import Callable

from ..ambienti import BASE_URL, Ambiente
from ..credenziali import Credenziali
from ..errori import ConfigurazioneNonValida
from .http import Richiesta, Trasporto, TrasportoHTTP, consegna
from .soap import imbusta, sbusta


def sessione_2f_test(utente: str, ruolo: str = "PRESCRITTORE", adesso: _dt.datetime | None = None) -> str:
    adesso = adesso or _dt.datetime.now()
    return f"{utente}-{adesso:%Y}-{adesso:%m}-RICETTA-DEM-{ruolo}"


class CanaleSAC:
    def __init__(
        self,
        credenziali: Credenziali,
        *,
        ambiente: Ambiente = Ambiente.TEST,
        base_url: str | None = None,
        trasporto: Trasporto | None = None,
        sessione_2f: Callable[[], str] | None = None,
    ):
        self.credenziali = credenziali
        self.ambiente = ambiente
        self.base_url = (base_url or BASE_URL[ambiente]).rstrip("/")
        self.trasporto = trasporto or TrasportoHTTP()
        if sessione_2f is None:
            if ambiente is not Ambiente.TEST:
                raise ConfigurazioneNonValida(
                    "Fuori dall'ambiente di test serve un id di sessione 2FA reale (parametro sessione_2f)"
                )
            sessione_2f = lambda: sessione_2f_test(credenziali.utente)  # noqa: E731
        self._sessione_2f = sessione_2f

    def _intestazioni(self, soap_action: str) -> dict[str, str]:
        token = base64.b64encode(f"{self.credenziali.utente}:{self.credenziali.password}".encode()).decode()
        return {
            "Content-Type": "text/xml;charset=UTF-8",
            "SOAPAction": f'"{soap_action}"',
            "Authorization": f"Basic {token}",
            "Authorization2F": f"Bearer {self._sessione_2f()}",
            "User-Agent": "varco/0.1 (+EUPL-1.2)",
        }

    def chiama(self, servizio: str, percorso: str, soap_action: str, corpo: ET.Element) -> tuple[ET.Element, "RispostaGrezza"]:
        richiesta = Richiesta(
            servizio=servizio,
            url=self.base_url + percorso,
            corpo=imbusta(corpo),
            intestazioni=self._intestazioni(soap_action),
        )
        risposta = consegna(self.trasporto, richiesta)  # guardia anche con un trasporto proprio
        grezza = RispostaGrezza(richiesta.corpo, risposta.corpo, risposta.stato_http, risposta.durata_s)
        return sbusta(risposta.corpo, risposta.stato_http), grezza


class RispostaGrezza:
    """XML esatto scambiato col servizio: per audit, prove e suite di conformità."""

    __slots__ = ("xml_richiesta", "xml_risposta", "stato_http", "durata_s")

    def __init__(self, xml_richiesta: bytes, xml_risposta: bytes, stato_http: int, durata_s: float):
        self.xml_richiesta = xml_richiesta
        self.xml_risposta = xml_risposta
        self.stato_http = stato_http
        self.durata_s = durata_s
