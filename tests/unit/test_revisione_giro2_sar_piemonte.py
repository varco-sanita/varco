# SPDX-License-Identifier: EUPL-1.2
"""Controesempi del giro 2 di revisione esterna, area SAR Piemonte (kit-mmg-review/2026-10-02-giro2/5-sar-piemonte.md).

Ogni test riproduce il controesempio del revisore: falliva sul codice precedente, passa ora.
Fixture e aiuti (server finto, Authorization Server in memoria, ricette) vengono da test_piemonte.py.
Pagine citate: specifiche/piemonte/REL-STC-01-V04.pdf (lette con pdftotext).
"""

from __future__ import annotations

import base64
import json
import math
import time
from pathlib import Path

import pytest

from test_piemonte import (  # noqa: F401 - server e regione sono fixture
    GESTIONALE,
    REDIRECT,
    SERVER,
    SOSTITUTO,
    TITOLARE,
    UUID_PROVA,
    UTENTI,
    TrasportoLocale,
    _autorizza,
    _b64,
    _cifratore,
    _ricetta,
    _TrasportoToken,
    regione,
    server,
)

from varco import ConfigurazioneNonValida
from varco.conformita.motore import Motore
from varco.ricetta import RicettaPiemonte
from varco.trasporto.piemonte import CanalePiemonte, ModalitaPiemonte, leggi_jwt
from varco.trasporto.piemonte_oauth2 import ClientOAuth2Piemonte, ErroreAutorizzazione

RADICE = Path(__file__).resolve().parents[2]
PAYLOAD = {"sub": TITOLARE, "aud": "VARCO_301", "exp": 4102444800, "scope": "prescrizione",
           "userData": {"cfutente": TITOLARE, "idSessione": UUID_PROVA}}


# ------------------------------------------------------------------ N1 nbf nel futuro


def test_g2_n1_jwt_non_ancora_valido_rifiutato_dallo_scambio():
    """N1: JWT firmato con nbf = adesso + 3600 (REL-STC-01 p. 30: nbf «Istante in cui il token diventa
    valido»). Prima: scambio_accettato True."""
    t = _TrasportoToken()
    client = ClientOAuth2Piemonte("http://127.0.0.1:9/reloauthserver", GESTIONALE, REDIRECT, trasporto=t)
    ra = client.autorizzazione()
    t.token = t.srv._firma(PAYLOAD | {"nbf": int(time.time()) + 3600})
    with pytest.raises(ErroreAutorizzazione, match="jwt_non_valido"):
        client.scambia_codice("code", ra)
    t.token = t.srv._firma(PAYLOAD | {"nbf": int(time.time()) - 5})  # gruppo di controllo
    assert client.scambia_codice("code", ra).contenuto.cf_utente == TITOLARE


def test_g2_n1_jwt_non_ancora_valido_fermato_dal_canale_e_dal_server(server, regione):
    """N1: lo stesso token passato al canale per callback, e al server finto. Prima: invio_ok True."""
    client = ClientOAuth2Piemonte(server.url_oauth, GESTIONALE, REDIRECT, trasporto=TrasportoLocale())
    ra, ritorno = _autorizza(server, client)
    buono = client.scambia_codice(client.leggi_callback(ritorno, ra), ra)
    futuro = server._firma(leggi_jwt(buono.access_token).payload | {"nbf": int(time.time()) + 3600})
    can = CanalePiemonte(ModalitaPiemonte.OAUTH2, token_jwt=lambda: futuro, cf_medico=TITOLARE,
                         gestionale_di_prova=GESTIONALE, base_url=server.url, trasporto=TrasportoLocale())
    with pytest.raises(ConfigurazioneNonValida, match="nbf"):
        RicettaPiemonte(can, _cifratore(regione)).invia(_ricetta())
    # il server finto, raggiunto direttamente: rifiuta anche lui
    codice, corpo = server._autentica_sar({"X-OAuth2-Authorization": "Bearer " + futuro}, {"pinCode": ""})
    assert codice is None and corpo is not None
    assert server._autentica_sar({"X-OAuth2-Authorization": "Bearer " + buono.access_token}, {"pinCode": ""})[0] == TITOLARE


# ------------------------------------------------------------------ N2 sessione non legata al JWT


def test_g2_n2_jwt_di_a_con_la_sessione_di_b_rifiutato(server):
    """N2: JWT firmato dal simulatore per A con l'Id-Sessione di B, rilasciato a ALTRO_301.
    Prima: SAR_accetta True, verifica 0, revoca 200 e la sessione di B revocata."""
    server.gestionali["ALTRO_301"] = {"prescrizione"}
    client = ClientOAuth2Piemonte(server.url_oauth, GESTIONALE, REDIRECT, trasporto=TrasportoLocale())
    ra, ritorno = _autorizza(server, client)
    payload_a = leggi_jwt(client.scambia_codice(client.leggi_callback(ritorno, ra), ra).access_token).payload
    altra = server._nuova_sessione(SOSTITUTO, "ALTRO_301", ("prescrizione",))
    raw = server._firma(payload_a | {"userData": payload_a["userData"] | {"idSessione": altra.valore}})
    cf, errore = server._autentica_sar({"X-OAuth2-Authorization": "Bearer " + raw}, {"pinCode": ""})
    assert cf is None and errore is not None
    assert client.verifica_sessione(raw, TITOLARE).stato_http == 401
    assert client.revoca_sessione(raw, TITOLARE) == 401
    assert altra.revocato_alle is None


def test_g2_n2_sessione_scaduta_con_jwt_valido_rifiutata_dal_sar(server):
    """N2, seconda prova: sessione scaduta, JWT ancora valido. Prima: verify stato 2 ma SAR_accetta True."""
    client = ClientOAuth2Piemonte(server.url_oauth, GESTIONALE, REDIRECT, trasporto=TrasportoLocale())
    ra, ritorno = _autorizza(server, client)
    token = client.scambia_codice(client.leggi_callback(ritorno, ra), ra)
    sess = server.stato.sessioni[leggi_jwt(token.access_token).id_sessione]
    assert server._autentica_sar({"X-OAuth2-Authorization": "Bearer " + token.access_token}, {"pinCode": ""})[0] == TITOLARE
    sess.fine = server.adesso() - 1  # scadenza della sessione nel passato, il JWT resta valido
    assert client.verifica_sessione(token.access_token, TITOLARE).stato == 2
    cf, errore = server._autentica_sar({"X-OAuth2-Authorization": "Bearer " + token.access_token}, {"pinCode": ""})
    assert cf is None and errore is not None and b"scadut" in errore.lower()


# ------------------------------------------------------------------ N3 falsi verdi nel runner


def _caso_pie007() -> dict:
    return json.loads((RADICE / "conformita" / "casi" / "PIE-007.json").read_text(encoding="utf-8"))


def test_g2_n3_booleano_atteso_non_vale_lo_zero_osservato():
    """N3: PIE-007 con stato_token atteso false invece di 0. Prima: SUPERATO (False == 0)."""
    caso = _caso_pie007()
    assert Motore().esegui(caso).stato == "SUPERATO"  # gruppo di controllo
    caso["passi"][0]["atteso"]["campi"]["stato_token"] = False
    assert Motore().esegui(caso).stato == "FALLITO"


def test_g2_n3_campo_inesistente_atteso_null_non_passa():
    """N3: campo inesistente «statoToken» atteso a null. Prima: SUPERATO (oss.get(...) is None)."""
    caso = _caso_pie007()
    campi = caso["passi"][0]["atteso"]["campi"]
    campi.pop("stato_token")
    campi["statoToken"] = None
    assert Motore().esegui(caso).stato != "SUPERATO"


# ------------------------------------------------------------------ N4 redirect_uri con query


def test_g2_n4_redirect_uri_con_query_preesistente(regione):
    """N4: redirect_uri registrata http://localhost:8081/callback?tenant=301. Prima: «tenant=301?code=...»
    e leggi_callback «callback senza code»."""
    redirect = "http://localhost:8081/callback?tenant=301"
    with SERVER.ServerPiemonte(chiave_cifratura_pem=regione.chiave_cifratura_pem, utenti=UTENTI,
                               gestionali={"VARCO_301": {"prescrizione"}}, redirect_uri={redirect},
                               utente_oauth2=TITOLARE) as srv:
        client = ClientOAuth2Piemonte(srv.url_oauth, GESTIONALE, redirect, trasporto=TrasportoLocale())
        ra, ritorno = _autorizza(srv, client)
        assert "?code=" not in ritorno.split("?", 1)[1]
        code = client.leggi_callback(ritorno, ra)
        assert code and client.scambia_codice(code, ra).contenuto.cf_utente == TITOLARE


# ------------------------------------------------------------------ N5 eccezioni fuori contratto


def test_g2_n5_alg_array_da_errore_di_autorizzazione():
    """N5: intestazione {"alg": []}. Prima: TypeError: cannot use 'list' as a dict key."""
    t = _TrasportoToken()
    client = ClientOAuth2Piemonte("http://127.0.0.1:9/reloauthserver", GESTIONALE, REDIRECT, trasporto=t)
    vero = t.srv._firma(PAYLOAD)
    t.token = _b64({"alg": [], "typ": "JWT"}) + "." + vero.split(".", 1)[1]
    with pytest.raises(ErroreAutorizzazione, match="jwt_non_valido"):
        client.scambia_codice("code", client.autorizzazione())


def test_g2_n5_jwks_rsa_invalido_da_errore_di_autorizzazione():
    """N5: chiave {"kty":"RSA","e":"AQAB","n":"!!!"}. Prima: ValueError: n must be >= 3."""
    t = _TrasportoToken()
    client = ClientOAuth2Piemonte("http://127.0.0.1:9/reloauthserver", GESTIONALE, REDIRECT, trasporto=t)
    t.token = t.srv._firma(PAYLOAD)
    with pytest.raises(ErroreAutorizzazione, match="jwt_non_valido"):
        client.scambia_codice("code", client.autorizzazione(), jwks={"keys": [{"kty": "RSA", "e": "AQAB", "n": "!!!"}]})


def test_g2_n5_exp_infinito_da_errore_di_autorizzazione_e_nel_canale():
    """N5: JWT firmato con exp infinito. Prima: OverflowError: cannot convert float infinity to integer."""
    t = _TrasportoToken()
    client = ClientOAuth2Piemonte("http://127.0.0.1:9/reloauthserver", GESTIONALE, REDIRECT, trasporto=t)
    t.token = t.srv._firma(PAYLOAD | {"exp": math.inf})  # json.dumps scrive Infinity
    assert "Infinity" in base64.urlsafe_b64decode(t.token.split(".")[1] + "==").decode()
    with pytest.raises(ErroreAutorizzazione, match="jwt_non_valido"):
        client.scambia_codice("code", client.autorizzazione())
    can = CanalePiemonte(ModalitaPiemonte.OAUTH2, token_jwt=lambda: t.token, cf_medico=TITOLARE,
                         gestionale_di_prova=GESTIONALE, base_url="http://127.0.0.1:9", trasporto=TrasportoLocale())
    with pytest.raises(ConfigurazioneNonValida, match="exp"):
        can.jwt()
