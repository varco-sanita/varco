# SPDX-License-Identifier: EUPL-1.2
"""Issue #1, #2, #12 (limiti noti della 0.1.0): registro e guardia di rete. Controesempi delle issue,
rossi prima e verdi dopo."""

from __future__ import annotations

import concurrent.futures
import json
import socket
import threading

import pytest

from varco.errori import AmbienteBloccato
from varco.trasporto.http import Richiesta, Risposta, consegna, esegui_nella_guardia
from varco.trasporto.registro import RegistratoreFile, Redattore

SEGRETO = "segreto-sintetico"
URL_TEST = "https://demservicetest.sanita.finanze.it/x"


# ------------------------------------------------------------------ #1 namespace e commenti


@pytest.mark.parametrize("redigi", [True, False], ids=["redatto", "in_chiaro"])
def test_issue1_credenziale_in_una_dichiarazione_di_namespace(redigi):
    out = Redattore().corpo(f'<x xmlns:d="https://localhost/?password={SEGRETO}"/>'.encode(), redigi=redigi)
    assert SEGRETO.encode() not in out and b"xmlns:d=" in out


@pytest.mark.parametrize("corpo", [f"<x/><!-- password={SEGRETO} -->", f"<x><!-- password={SEGRETO} -->a</x>",
                                   f"<x><?istruzione password={SEGRETO}?>a</x>"])
@pytest.mark.parametrize("redigi", [True, False], ids=["redatto", "in_chiaro"])
def test_issue1_credenziale_in_un_commento_o_in_una_pi(corpo, redigi):
    out = Redattore().corpo(corpo.encode(), redigi=redigi)
    assert SEGRETO.encode() not in out


def test_issue1_credenziale_in_un_xml_annidato_come_testo():
    annidato = f'&lt;y xmlns:d="https://localhost/?password={SEGRETO}"/&gt;'
    out = Redattore().corpo(f"<x>{annidato}</x>".encode(), redigi=False)
    assert SEGRETO.encode() not in out


def test_issue1_gruppo_di_controllo_in_chiaro_senza_credenziali_byte_identici():
    b = b'<?xml version="1.0" encoding="UTF-8"?>\n<a:x xmlns:a="http://esempio.xsd.dem.sanita.finanze.it"><a:y>1</a:y></a:x>'
    assert Redattore().corpo(b, redigi=False) is b or Redattore().corpo(b, redigi=False) == b


# ------------------------------------------------------------------ #12 `code` nel JSON OAuth2


def _scrivi(tmp_path, servizio: str, risposta: bytes) -> str:
    reg = RegistratoreFile(tmp_path)
    reg(Richiesta(servizio, "http://127.0.0.1:9/token", b"code=CODICE-FORM&code_verifier=VERIFIER-FORM"),
        Risposta(200, risposta, {}, 0), None)
    return "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())


def test_issue12_code_nel_json_oauth2_redatto(tmp_path):
    testo = _scrivi(tmp_path, "piemonte.oauth2.token", b'{"code":"CODICE-JSON-SEGRETO","access_token":"ACCESS-SEGRETO"}')
    for chiaro in ("CODICE-JSON-SEGRETO", "ACCESS-SEGRETO", "CODICE-FORM", "VERIFIER-FORM"):
        assert chiaro not in testo, chiaro


def test_issue12_gruppo_di_controllo_code_d_errore_fuori_da_oauth2_resta_leggibile(tmp_path):
    testo = _scrivi(tmp_path, "piemonte.sar.invio", b'{"code":"9999","descrizione":"errore"}')
    assert json.dumps("9999") in testo


# ------------------------------------------------------------------ #2 thread estranei e guardia


class _TrasportoCheAspetta:
    """Il trasporto risponde 200 dopo che il thread estraneo ha fatto il suo accesso alla rete."""

    def __init__(self, dentro: threading.Event, fatto: threading.Event, azione=None):
        self.dentro, self.fatto, self.azione = dentro, fatto, azione

    def invia(self, r: Richiesta) -> Risposta:
        self.dentro.set()
        if self.azione:
            self.azione()
        assert self.fatto.wait(10)
        return Risposta(200, b"<ok/>", {}, 0.0)


def _thread_estraneo(dentro, fatto, host: str, esiti: list):
    def lavoro():
        dentro.wait(10)
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, flags=socket.AI_NUMERICHOST)
            esiti.append("risolto")
        except AmbienteBloccato:
            esiti.append("AmbienteBloccato")
        except OSError as e:  # niente rete vera: un nome non numerico non si risolve con AI_NUMERICHOST
            esiti.append(f"OSError {e.errno}")
        finally:
            fatto.set()

    t = threading.Thread(target=lavoro, daemon=True)
    t.start()
    return t


def test_issue2_thread_estraneo_con_un_ip_non_fa_fallire_la_risposta():
    """Controesempio: «THREAD INDIPENDENTE: AmbienteBloccato / ESITO CONSEGNA SAC: AmbienteBloccato»."""
    dentro, fatto, esiti = threading.Event(), threading.Event(), []
    t = _thread_estraneo(dentro, fatto, "203.0.113.7", esiti)  # avviato PRIMA della chiamata
    r = consegna(_TrasportoCheAspetta(dentro, fatto), Richiesta("sac.prova", URL_TEST, b"<x/>"))
    t.join(10)
    assert r.stato_http == 200
    assert esiti == ["risolto"]  # un IP che nessuno ha risolto davanti alla guardia non è di nessuno


def test_issue2_thread_estraneo_verso_la_produzione_fermato_ma_la_chiamata_non_ne_risponde():
    dentro, fatto, esiti = threading.Event(), threading.Event(), []
    t = _thread_estraneo(dentro, fatto, "demservice.sanita.finanze.it", esiti)
    r = consegna(_TrasportoCheAspetta(dentro, fatto), Richiesta("sac.prova", URL_TEST, b"<x/>"))
    t.join(10)
    assert esiti == ["AmbienteBloccato"] and r.stato_http == 200


def test_issue2_gruppo_di_controllo_thread_avviato_dal_trasporto_ricade_sulla_chiamata():
    """Un pool creato DENTRO `invia` appartiene alla chiamata: un IP letterale vale produzione, e la
    violazione ricade sulla chiamata anche se il trasporto ingoia l'eccezione."""

    class NelPool:
        def invia(self, r):
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                try:
                    ex.submit(socket.getaddrinfo, "203.0.113.7", 443, 0, 0, 0, socket.AI_NUMERICHOST).result()
                except AmbienteBloccato:
                    pass  # ingoiata
            return Risposta(200, b"<ok/>", {}, 0.0)

    with pytest.raises(AmbienteBloccato):
        consegna(NelPool(), Richiesta("sac.prova", URL_TEST, b"<x/>"))


def test_issue2_pool_esistente_legato_con_esegui_nella_guardia():
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pool.submit(lambda: None).result()  # il thread del pool nasce PRIMA della chiamata

        class PoolEsistente:
            def invia(self, r):
                try:
                    pool.submit(esegui_nella_guardia(socket.getaddrinfo, "203.0.113.7", 443, 0, 0, 0,
                                                     socket.AI_NUMERICHOST)).result()
                except AmbienteBloccato:
                    pass
                return Risposta(200, b"<ok/>", {}, 0.0)

        with pytest.raises(AmbienteBloccato):
            consegna(PoolEsistente(), Richiesta("sac.prova", URL_TEST, b"<x/>"))


def test_issue2_legame_dei_thread_anche_senza_l_evento_di_audit(monkeypatch):
    """CI del 03/10/2026: Python 3.11 non ha l'evento di audit dell'avvio dei thread. Il legame deve
    reggere anche senza (threading.Thread.start): stessa prova del gruppo di controllo sopra."""
    from varco.trasporto import http as modulo_http

    monkeypatch.setattr(modulo_http, "_EVENTI_AVVIO_THREAD", frozenset())
    test_issue2_gruppo_di_controllo_thread_avviato_dal_trasporto_ricade_sulla_chiamata()
