# SPDX-License-Identifier: EUPL-1.2
"""Passi della famiglia `piemonte` (SIRPED, Regione Piemonte) per l'esecutore Python dei casi.

La semantica sta in `conformita/schema/caso.schema.json` ($defs opCodificaPiemonte,
opLeggiPiemonteA2f, opIntestazioniPiemonte, opPkcePiemonte, opLeggiPiemonteJwt, attesoPiemonte):
questo modulo è UNA implementazione. Nessuna chiamata di rete.
"""

from __future__ import annotations

import base64
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..credenziali import Credenziali
from ..errori import ConfigurazioneNonValida, ErroreSOAP, RicettaNonValida
from ..ricetta.json import criteri_da_dict, ricetta_da_dict
from ..trasporto.soap import sbusta

CIFRATO = "CIFRATO=="


class RifiutoLocale(Exception):
    """L'implementazione ha rifiutato prima di produrre la richiesta (controlli locali)."""


def _cifra(_valore: str) -> str:
    return CIFRATO


def _modalita(p: dict):
    from ..trasporto.piemonte import ModalitaPiemonte

    return ModalitaPiemonte(p.get("modalita", "mail"))


def testo_in(radice: ET.Element, percorso: str) -> str | None:
    """Testo del primo elemento lungo 'nome/nome/...' (nomi locali) sotto la radice."""
    el = radice
    for nome in filter(None, percorso.split("/")):
        el = next((c for c in el if c.tag.rsplit("}", 1)[-1] == nome), None)
        if el is None:
            return None
    return el.text or ""


def codifica(p: dict) -> tuple[ET.Element, bool]:
    """(richiesta, è_A2F). Solleva RifiutoLocale per i rifiuti prima della codifica."""
    from ..ricetta import piemonte as rp
    from ..trasporto import piemonte_a2f as a2f

    servizio = p["servizio"]
    try:
        if servizio in ("invio", "visualizza", "annulla", "interroga_nre"):
            kw: dict[str, Any] = {}
            if servizio == "invio":
                ricetta = ricetta_da_dict(p["ricetta"])
                problemi = ricetta.problemi()
                if problemi:
                    raise RicettaNonValida(problemi)
                kw["ricetta"] = ricetta
            elif servizio == "interroga_nre":
                criteri = criteri_da_dict(p["criteri"])
                problemi = criteri.problemi()
                if problemi:
                    raise RicettaNonValida(problemi)
                kw.update(criteri=criteri, cf_medico=p["cf_medico"])
            else:
                kw.update(nre=p["nre"], cf_medico=p["cf_medico"])
            return rp.richiesta(servizio, _modalita(p), _cifra, **kw), False
        app = f'{p["gestionale"]["codice"]}_{p["gestionale"]["azienda"]}'
        if servizio == "crea_id_sessione":
            utente = a2f.UtenteA2F(p["cf_utente"], p["codice_asl"], p.get("codice_ssa"))
            return a2f.richiesta_crea(p["utente_rupar"], "0000000000", _cifra, utente, app,
                                      p.get("permessi", ["prescrizione"])), True
        f = a2f.richiesta_verifica if servizio == "verifica_id_sessione" else a2f.richiesta_revoca
        return f(p["utente_rupar"], "0000000000", _cifra, p["cf_utente"], p["id_sessione"], app), True
    except (RicettaNonValida, ConfigurazioneNonValida, ValueError) as e:
        raise RifiutoLocale(str(e)) from e


def osserva_a2f(xml: bytes) -> dict:
    from ..trasporto.piemonte_a2f import leggi_esito

    try:
        el = sbusta(xml, 200)
    except ErroreSOAP as e:
        return {"fault": e.faultstring}
    esito = leggi_esito(el)
    return {
        "codice": esito.codice,
        "ok": esito.ok,
        "errori": [{"codice": e.codice, "tipo": e.tipo, "bloccante": e.bloccante} for e in esito.errori],
        "info": dict(esito.info),
        "comunicazioni": dict(esito.comunicazioni),
        "ambiente_test": esito.ambiente_test,
        "id_sessione_di_test": esito.id_sessione_di_test,
        "permessi": list(esito.permessi),
        "fine_validita": esito.fine_validita,
        "stato_token": esito.stato_token.stato if esito.stato_token else None,
    }


def jwt_da_payload(payload: dict, intestazione: dict | None = None) -> str:
    """Un JWT con una firma finta: il canale lo LEGGE soltanto, la firma non la verifica."""
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{b64(intestazione or {'alg': 'RS256', 'kid': 'rel-oauth2-key'})}.{b64(payload)}.ZmlybWEtbm9uLXZlcmlmaWNhdGE"


_SERVIZI = {
    "invio": ("INVIO", None),
    "visualizza": ("VISUALIZZA", None),
    "annulla": ("ANNULLA", None),
    "interroga_nre": ("INTERROGA_NRE", None),
    "crea_id_sessione": ("ID_SESSIONE", "create"),
    "verifica_id_sessione": ("ID_SESSIONE", "checkToken"),
    "revoca_id_sessione": ("ID_SESSIONE", "revoke"),
}


def osserva_intestazioni(p: dict) -> dict:
    from ..trasporto.piemonte import (
        SOAP_ACTION,
        SOAP_ACTION_A2F,
        CanalePiemonte,
        GestionalePiemonte,
        ModalitaPiemonte,
        ServizioPiemonte,
    )

    try:
        gestionale = GestionalePiemonte(p["gestionale"]["codice"], p["gestionale"]["azienda"])
        modalita = _modalita(p)
        comuni = dict(gestionale_di_prova=gestionale, base_url="http://127.0.0.1:9")  # nessuna chiamata parte
        if modalita is ModalitaPiemonte.MAIL:
            c = p["credenziali"]
            ids = p.get("id_sessione")
            canale = CanalePiemonte(modalita, credenziali=Credenziali(c["utente"], c["password"], c["pincode"], c["cf"]),
                                    id_sessione=(lambda: ids) if ids is not None else None, **comuni)
        else:
            token = jwt_da_payload(p["jwt_payload"])
            canale = CanalePiemonte(modalita, token_jwt=lambda: token, cf_medico=p["cf_medico"], **comuni)
        nome, a2f = _SERVIZI[p["servizio"]]
        servizio = ServizioPiemonte[nome]
        if servizio is ServizioPiemonte.ID_SESSIONE and modalita is not ModalitaPiemonte.MAIL:
            raise ConfigurazioneNonValida("CreateAuth/CheckToken/RevokeAuth valgono solo nella modalità MAIL")
        azione = SOAP_ACTION_A2F[a2f] if a2f else SOAP_ACTION[servizio]
        return {"intestazioni": canale.intestazioni(servizio, azione)}
    except ConfigurazioneNonValida as e:
        raise RifiutoLocale(str(e)) from e


def osserva_pkce(p: dict) -> dict:
    from ..trasporto.piemonte_oauth2 import challenge_s256

    try:
        return {"code_challenge": challenge_s256(p["code_verifier"])}
    except ConfigurazioneNonValida as e:
        raise RifiutoLocale(str(e)) from e


def osserva_jwt(cartella: Path, p: dict) -> dict:
    from ..trasporto.piemonte import leggi_jwt
    from ..trasporto.piemonte_oauth2 import verifica_firma

    token = cartella.joinpath("risposte", p["token"]).read_text(encoding="ascii").strip()
    jwks = json.loads(cartella.joinpath("risposte", p["jwks"]).read_text(encoding="utf-8"))
    try:
        contenuto, firma_valida = verifica_firma(token, jwks), True
    except ConfigurazioneNonValida:
        contenuto, firma_valida = leggi_jwt(token), False
    return {
        "firma_valida": firma_valida,
        "alg": contenuto.intestazione.get("alg"),
        "kid": contenuto.intestazione.get("kid"),
        "sub": contenuto.sub,
        "cf_utente": contenuto.cf_utente,
        "aud": contenuto.payload.get("aud"),
        "id_sessione": contenuto.id_sessione,
        "scope": list(contenuto.scope),
    }


def stesso_valore(osservato, atteso) -> bool:
    """Uguaglianza che non confonde i booleani con i numeri: in Python False == 0 e True == 1, e un
    `false` atteso al posto dello stato 0 restava verde (revisione esterna giro 2, N3). Vale anche
    dentro liste e oggetti."""
    if isinstance(osservato, bool) or isinstance(atteso, bool):
        return isinstance(osservato, bool) and isinstance(atteso, bool) and osservato is atteso
    if isinstance(osservato, (list, tuple)) and isinstance(atteso, (list, tuple)):
        return len(osservato) == len(atteso) and all(stesso_valore(o, a) for o, a in zip(osservato, atteso))
    if isinstance(osservato, dict) and isinstance(atteso, dict):
        return osservato.keys() == atteso.keys() and all(stesso_valore(osservato[k], atteso[k]) for k in atteso)
    return osservato == atteso


def verifica_piemonte(atteso: dict, oss: dict) -> list[str]:
    """Aspettative non rispettate. Le incoerenze (chiavi sconosciute o non applicabili all'operazione)
    le scarta prima il motore (motore.incoerenze); il rifiuto locale lo confronta il motore."""
    if oss.get("fault") is not None and "fault" not in atteso.get("campi", {}):
        return [f"SOAP Fault inatteso: {oss['fault']}"]
    ko: list[str] = []
    if "codice" in atteso and oss.get("codice") not in atteso["codice"]:
        ko.append(f"codice {oss.get('codice')!r} non in {atteso['codice']}")
    if "ok" in atteso and oss.get("ok") is not atteso["ok"]:
        ko.append(f"ok = {oss.get('ok')!r} invece di {atteso['ok']!r}")
    for campo, val in atteso.get("campi", {}).items():
        if campo not in oss:
            # assente ≠ null: un nome di campo sbagliato atteso a null passava (revisione esterna giro 2, N3)
            ko.append(f"{campo}: campo non osservato (atteso {val!r})")
        elif not stesso_valore(oss[campo], val):
            ko.append(f"{campo} = {oss.get(campo)!r} invece di {val!r}")
    errori = oss.get("errori") or []
    if "errore_codice" in atteso and not any(e["codice"] == atteso["errore_codice"] for e in errori):
        ko.append(f"manca l'errore {atteso['errore_codice']}")
    if atteso.get("errore_bloccante") and not any(e["bloccante"] for e in errori):
        ko.append("nessun errore bloccante")
    if atteso.get("nessun_bloccante") and any(e["bloccante"] for e in errori):
        ko.append("c'è un errore bloccante")
    h = oss.get("intestazioni", {})
    for nome, val in atteso.get("intestazioni", {}).items():
        if h.get(nome) != val:
            ko.append(f"header {nome} = {h.get(nome)!r} invece di {val!r}")
    for nome, pref in atteso.get("intestazioni_prefisso", {}).items():
        if nome not in h:
            # assente ≠ vuoto: il prefisso "" passava anche senza l'header (revisione esterna giro 3, N2)
            ko.append(f"header {nome} assente: doveva cominciare con {pref!r}")
        elif not str(h[nome]).startswith(pref):
            ko.append(f"header {nome} = {h.get(nome)!r}: doveva cominciare con {pref!r}")
    for nome in atteso.get("assenti", []):
        if nome in h:
            ko.append(f"header {nome} presente, doveva mancare")
    return ko
