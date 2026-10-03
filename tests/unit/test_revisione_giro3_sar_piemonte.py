# SPDX-License-Identifier: EUPL-1.2
"""Giro 3 di revisione esterna (03/10/2026), SAR Piemonte: residuo del giro 2 N5 (eccezioni fuori
contratto su risposte del servizio token malformate). Rapporto kit-mmg-review/2026-10-02-giro3/5-sar-piemonte.md.

La promessa (docs/SAR_PIEMONTE.md): un JWT o una risposta che non passano sono un ErroreAutorizzazione.
Prima uscivano AttributeError, OverflowError, TypeError, ValueError. Le aspettative sulle intestazioni
(giro 2 N3 = giro 3 N2) stanno in test_revisione_giro3_conformita.py.
"""

from __future__ import annotations

import json
import math

import pytest

from test_piemonte import GESTIONALE, REDIRECT, TITOLARE, UUID_PROVA, _TrasportoToken

from varco.trasporto import Risposta
from varco.trasporto.piemonte import leggi_jwt
from varco.trasporto.piemonte_oauth2 import ClientOAuth2Piemonte, ErroreAutorizzazione
from varco import ConfigurazioneNonValida

PAYLOAD = {"sub": TITOLARE, "aud": "VARCO_301", "exp": 4102444800, "scope": "prescrizione",
           "userData": {"cfutente": TITOLARE, "idSessione": UUID_PROVA}}


class _Risposta(_TrasportoToken):
    """Il servizio token risponde con i campi dati (JSON anche non standard: NaN, Infinity)."""

    def __init__(self, **campi):
        super().__init__()
        self.campi = campi

    def invia(self, richiesta):
        if richiesta.url.endswith("/.well-known/jwks.json"):
            return super().invia(richiesta)
        dati = {"access_token": self.srv._firma(PAYLOAD), "token_type": "Bearer", "scope": "prescrizione",
                "expires_in": 7199} | self.campi
        return Risposta(200, json.dumps(dati).encode(), {}, 0.0)


def _scambia(t):
    client = ClientOAuth2Piemonte("http://127.0.0.1:9/reloauthserver", GESTIONALE, REDIRECT, trasporto=t)
    return client.scambia_codice("code", client.autorizzazione())


@pytest.mark.parametrize("campi", [
    {"access_token": {"a": 1}},
    {"access_token": 123},
    {"access_token": ["token"]},
    {"expires_in": math.inf},
    {"scope": 42},
    {"expires_in": math.nan},
], ids=["token_dict", "token_int", "token_list", "expires_inf", "scope_int", "expires_nan"])
def test_g3_g2n5_controesempi_del_revisore(campi):
    """Residuo G2-5: «access_token={'a': 1} AttributeError … expires_in=NaN ValueError» (le ultime tre con JWT
    firmato correttamente)."""
    with pytest.raises(ErroreAutorizzazione):
        _scambia(_Risposta(**campi))


@pytest.mark.parametrize("campi", [
    {"access_token": None}, {"access_token": True}, {"access_token": ""}, {"access_token": "   "},
    {"access_token": 1.5}, {"access_token": "non-un-jwt"}, {"access_token": "a.b.c"},
    {"token_type": 5}, {"token_type": None}, {"token_type": ["Bearer"]}, {"token_type": "MAC"},
    {"scope": {"a": 1}}, {"scope": [1, 2]}, {"scope": True}, {"scope": 3.5},
    {"client_id": 7}, {"client_id": ["x"]},
    {"expires_in": -math.inf}, {"expires_in": 1e400}, {"expires_in": "abc"}, {"expires_in": [1]},
], ids=lambda c: "-".join(f"{k}={v!r}" for k, v in c.items()))
def test_g3_g2n5_famiglia_tipi_e_numeri(campi):
    """Famiglia: ogni campo della risposta con un tipo sbagliato o un numero non finito dà ErroreAutorizzazione,
    oppure (expires_in illeggibile) è ignorato come assente; mai un'altra eccezione."""
    try:
        _scambia(_Risposta(**campi))
    except ErroreAutorizzazione:
        pass


@pytest.mark.parametrize("valore", [{"a": 1}, 123, ["t"], 1.5, b"x.y.z"])
def test_g3_g2n5_leggi_jwt_tipi_sbagliati(valore):
    """Famiglia, a valle: leggi_jwt su un valore che non è una stringa dà ConfigurazioneNonValida."""
    with pytest.raises(ConfigurazioneNonValida):
        leggi_jwt(valore)


def test_g3_g2n5_gruppo_di_controllo_risposta_buona():
    """Gruppo di controllo: la risposta corretta dà il token, con scope e scadenza."""
    token = _scambia(_Risposta())
    assert token.scope == ("prescrizione",) and token.expires_in == 7199 and token.contenuto.cf_utente == TITOLARE


@pytest.mark.parametrize("campi", [{"scope": 0}, {"scope": 0.0}, {"scope": False}, {"scope": {}},
                                   {"expires_in": False}, {"expires_in": True}, {"expires_in": {}},
                                   {"expires_in": []}, {"expires_in": "abc"}, {"expires_in": "NaN"},
                                   {"expires_in": "Infinity"}],
                         ids=lambda c: "-".join(f"{k}={v!r}" for k, v in c.items()))
def test_g4_g2n5_valori_falsi_e_non_numerici_rifiutati(campi):
    """Verifica giro 4: `scope` 0, 0.0, False, {} erano accettati come scope vuoto (`or ""` prima del controllo
    del tipo); `expires_in` non numerico era preso per assente. Ora: ErroreAutorizzazione."""
    with pytest.raises(ErroreAutorizzazione):
        _scambia(_Risposta(**campi))


def test_g4_g2n5_campi_assenti_restano_ammessi():
    """Gruppo di controllo: scope ed expires_in ASSENTI (o null) non sono errori."""
    t = _Risposta()
    t.campi = {"scope": None, "expires_in": None}
    token = _scambia(t)
    assert token.scope == () and token.expires_in is None
