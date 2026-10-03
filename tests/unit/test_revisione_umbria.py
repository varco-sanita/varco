# SPDX-License-Identifier: EUPL-1.2
"""Revisione esterna del modulo Umbria (GPT-6 Astra, 03/10/2026): i quattro bug ALTI.

Ogni test riproduce il controesempio del revisore (rosso prima della correzione, verde dopo), più
qualche variante dello stesso principio. Rapporto: kit-mmg-review/2026-10-03-dopo-pubblicazione/
revisione-umbria/revisione.md. Nessuna chiamata ai sistemi umbri: gli opener sono in memoria.
"""

from __future__ import annotations

import http.client
import io
import json
import urllib.request

import pytest

from varco import AmbienteBloccato
from varco.ambienti import HOST_UMBRIA_TEST
from varco.errori import ConfigurazioneNonValida, ErroreTrasporto
from varco.ricetta import RicettaUmbria, json_umbria
from varco.ricetta.json_umbria import RispostaNonConforme
from varco.trasporto import RegistratoreFile
from varco.trasporto.http import Risposta, consegna
from varco.trasporto.registro import Redattore
from varco.trasporto.umbria import CanaleUmbria, InvioIncertoUmbria, ServizioUmbria

from test_umbria import (  # noqa: F401  (fixture)
    APPLICATIVO,
    ASSISTITO,
    TITOLARE,
    TrasportoLocale,
    _firmatario,
    _lotto,
    _servizio,
    _spec,
    materiale,
    server,
)

LOCALE = "https://127.0.0.1:12345/sar"
URL_TEST = f"https://{HOST_UMBRIA_TEST}/sar/v1/servizi-prescrittore/richiesta-lotto-nre"


# ------------------------------------------------------------------ strumenti in memoria


class _RispostaFinta(io.BytesIO):
    def __init__(self, url: str, stato: int, corpo: bytes, intestazioni: dict | None = None):
        super().__init__(corpo)
        self.url, self.status, self.code = url, stato, stato
        self.headers = http.client.HTTPMessage()
        for k, v in (intestazioni or {"Content-Type": "application/json"}).items():
            self.headers[k] = v
        self.msg = "OK"

    def info(self):
        return self.headers

    def geturl(self):
        return self.url


class _HandlerInMemoria(urllib.request.BaseHandler):
    """Al posto di HTTPSHandler: la prima richiesta riceve un 302 verso il test umbro, la seconda 200.
    Nessun socket: se la guardia non ferma il redirect, la richiesta arriva QUI con i JWT."""

    codice = 302

    def __init__(self, verso: str):
        self.verso, self.visti = verso, []

    def https_open(self, req):
        self.visti.append((req.full_url, req.get_header("Authorization"), req.get_header("Fse-jwt-signature")))
        if len(self.visti) == 1:
            return _RispostaFinta(req.full_url, self.codice, b"", {"Location": self.verso})
        return _RispostaFinta(req.full_url, 200, json.dumps({"codEsito": "99", "esito": "x"}).encode())


class _TrasportoCheSegueRedirect:
    """Un trasporto proprio dell'integratore, col flag del collaudo regionale, che segue i redirect."""

    consenti_collaudo_regionale = True

    def __init__(self, verso: str):
        self.handler = _HandlerInMemoria(verso)
        self.opener = urllib.request.OpenerDirector()
        for h in (self.handler, urllib.request.HTTPRedirectHandler(), urllib.request.HTTPErrorProcessor()):
            self.opener.add_handler(h)

    def invia(self, richiesta):
        req = urllib.request.Request(richiesta.url, data=richiesta.corpo, method=richiesta.metodo,
                                     headers=richiesta.intestazioni)
        with self.opener.open(req) as r:
            return Risposta(r.status, r.read(), dict(r.headers.items()), 0.0, url_finale=r.url)


class _TrasportoFisso:
    """Risponde sempre con lo stesso corpo (risposte fuori dall'OpenAPI)."""

    def __init__(self, corpo: dict, stato: int = 200):
        self.corpo, self.stato = corpo, stato

    def invia(self, richiesta):
        return Risposta(self.stato, json.dumps(self.corpo).encode(), {}, 0.0)


def _canale_locale(materiale, trasporto) -> CanaleUmbria:
    return CanaleUmbria(TITOLARE, _firmatario(materiale), azienda="100201", base_url=LOCALE,
                        applicativo_di_prova=APPLICATIVO, trasporto=trasporto)


# ------------------------------------------------------------------ B1: redirect senza adesione


def test_b1_redirect_da_localhost_al_test_umbro_senza_adesione_bloccato(materiale):
    t = _TrasportoCheSegueRedirect(URL_TEST)
    s = RicettaUmbria(_canale_locale(materiale, t))
    with pytest.raises(AmbienteBloccato):
        s.richiedi_lotto_nre()
    assert [u for u, *_ in t.handler.visti if HOST_UMBRIA_TEST in u] == [], "i JWT sono arrivati al test umbro"


@pytest.mark.parametrize("verso", [
    f"https://{HOST_UMBRIA_TEST.upper()}/sar/v1/x",
    f"https://{HOST_UMBRIA_TEST}./sar/v1/x",
    "https://demtest.sanita.fvg.it/SARWs/x",  # un altro collaudo regionale, stesso flag
])
def test_b1_famiglia_senza_adesione_nessun_collaudo_regionale(materiale, verso):
    t = _TrasportoCheSegueRedirect(verso)
    s = RicettaUmbria(_canale_locale(materiale, t))
    with pytest.raises(AmbienteBloccato):
        s.richiedi_lotto_nre()
    assert len(t.handler.visti) == 1


def test_b1_consegna_solo_locale_rifiuta_anche_l_url_iniziale():
    from varco.trasporto.http import Richiesta

    class T:
        consenti_collaudo_regionale = True

        def invia(self, r):  # pragma: no cover - non deve arrivarci
            raise AssertionError("trasporto raggiunto")

    with pytest.raises(AmbienteBloccato):
        consegna(T(), Richiesta(servizio="x", url=URL_TEST, corpo=b"{}"), solo_locale=True)


# ------------------------------------------------------------------ B2: registro


def test_b2_registro_redatto_senza_componenti_del_lotto(server, materiale, tmp_path):
    t = TrasportoLocale(materiale, registratore=RegistratoreFile(tmp_path))
    s = _servizio(server, materiale, trasporto=t)
    lotto = _lotto(s)
    e = s.invia(_spec(lotto.nre(0)))
    assert e.ok
    from varco.ricetta import CriteriNreUtilizzati
    s.interroga_nre_utilizzati(CriteriNreUtilizzati("100", nre=e.nre))
    testo = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    for chiaro in (lotto.prefisso, lotto.codice_raggruppamento + lotto.identificativo_lotto + lotto.codice_lotto,
                   f'"codLotto": "{lotto.codice_lotto}"', f'"codRagLotto": "{lotto.codice_raggruppamento}"'):
        assert chiaro not in testo, chiaro


@pytest.mark.parametrize("chiave", ["esito", "tipoErrore", "nota", "lotto", "codLotto", "codRagLotto"])
def test_b2_testo_libero_e_lotto_redatti_nel_json(chiave):
    valore = "Prenotazione per MARIO ROSSI, tel 3331234567, mario@example.org"
    corpo = json.dumps({"codEsitoInserimento": "9999",
                        "elencoErroriRicette": {"erroreRicetta": [{"codEsito": "5000", chiave: valore}]}}).encode()
    fuori = Redattore().corpo(corpo).decode("utf-8")
    for pezzo in ("MARIO ROSSI", "3331234567", "mario@example.org"):
        assert pezzo not in fuori, (chiave, fuori)
    assert '"codEsito": "5000"' in fuori  # i codici restano leggibili


def test_b2_gruppo_di_controllo_in_chiaro_identico():
    corpo = json.dumps({"codEsito": "00", "esito": "ok", "codLotto": "000001"}).encode()
    assert Redattore().corpo(corpo, redigi=False) == corpo


# ------------------------------------------------------------------ B3: risposta troncata


class _OpenerTroncato:
    def open(self, req, timeout=None):
        r = _RispostaFinta(req.full_url, 200, b"")

        def read(*a):
            raise http.client.IncompleteRead(b'{"nre":', 100)
        r.read = read
        return r


def test_b3_invio_con_risposta_troncata_diventa_invio_incerto(server, materiale):
    t = TrasportoLocale(materiale)
    s = _servizio(server, materiale, trasporto=t)
    nre = _lotto(s).nre(0)
    t._opener = _OpenerTroncato()
    with pytest.raises(InvioIncertoUmbria) as e:
        s.invia(_spec(nre))
    assert e.value.nre == nre and e.value.cf_assistito == ASSISTITO


def test_b3_trasporto_http_converte_la_lettura_troncata_in_errore_di_trasporto(materiale):
    from varco.trasporto.http import Richiesta
    t = TrasportoLocale(materiale)
    t._opener = _OpenerTroncato()
    with pytest.raises(ErroreTrasporto):
        t.invia(Richiesta(servizio="x", url="https://127.0.0.1:1/x", corpo=b"{}"))


def test_b3_eccezione_qualsiasi_del_trasporto_proprio_su_invio_e_incerta(server, materiale):
    class Rotto:
        def invia(self, r):
            raise ConnectionResetError("connessione chiusa dopo l'invio")

    s = RicettaUmbria(_canale_locale(materiale, Rotto()))
    with pytest.raises(InvioIncertoUmbria):
        s.invia(_spec("100123456789000"))


def test_b3_la_guardia_resta_un_blocco_non_un_invio_incerto(materiale):
    class Fuori:
        consenti_collaudo_regionale = True

        def invia(self, r):
            urllib.request.OpenerDirector().open(urllib.request.Request(URL_TEST))

    s = RicettaUmbria(_canale_locale(materiale, Fuori()))
    with pytest.raises(AmbienteBloccato):
        s.invia(_spec("100123456789000"))


# ------------------------------------------------------------------ B4: risposte fuori dall'OpenAPI


@pytest.mark.parametrize("lettore,corpo", [
    ("leggi_ricevuta_invio", {"codEsitoInserimento": "0000", "elencoErroriRicette": {}}),
    ("leggi_ricevuta_invio", {"codEsitoInserimento": "0000", "nre": None}),
    ("leggi_ricevuta_invio", {"codEsitoInserimento": "0000", "flagPromemoria": 42}),
    ("leggi_ricevuta_visualizza", {"codEsitoVisualizzazione": "0000",
                                   "elencoDettagliPrescrizioni": {"dettaglioPrescrizione": [{}]}}),
    ("leggi_ricevuta_sostituzione", {"codEsitoInserimento": "0000", "dataInserimento": 42}),
    ("leggi_ricevuta_annulla", {"codEsitoAnnullamento": "0000", "nre": "100123456789000",
                                "elencoComunicazioni": {"comunicazione": [{"codice": "1"}]}}),
    ("leggi_ricevuta_interroga_nre", {"codEsitoInterrogaNreUtilizzati": "0000",
                                      "elencoNreUtilRecord": {"nreUtilRecord": [{"nre": 1}]}}),
    ("leggi_ricevuta_lotto", {"codEsito": 0}),
    ("leggi_ricevuta_invio", {"codEsitoInserimento": "0000", "elencoNota": {"nota": "x"}}),
])
def test_b4_risposta_fuori_dall_openapi_rifiutata(lettore, corpo):
    with pytest.raises(RispostaNonConforme):
        getattr(json_umbria, lettore)(corpo)


def test_b4_risposte_conformi_ancora_lette():
    e = json_umbria.leggi_ricevuta_invio({"codEsitoInserimento": "0000", "nre": "100123456789000",
                                          "elencoErroriRicette": {"erroreRicetta": []}, "altroCampo": 1})
    assert e.codice == "0000" and e.nre == "100123456789000"


def test_b4_risposta_non_conforme_non_e_un_errore_di_configurazione(materiale):
    s = RicettaUmbria(_canale_locale(materiale, _TrasportoFisso({"codEsitoAnnullamento": "0000", "nre": None})))
    with pytest.raises(ErroreTrasporto) as e:
        s.annulla("100123456789000", cf_assistito=ASSISTITO)
    assert not isinstance(e.value, ConfigurazioneNonValida)


def test_b4_invio_con_ricevuta_non_conforme_e_un_invio_incerto(materiale):
    s = RicettaUmbria(_canale_locale(materiale, _TrasportoFisso({"codEsitoInserimento": "0000", "nre": None})))
    with pytest.raises(InvioIncertoUmbria) as e:
        s.invia(_spec("100123456789000"))
    assert e.value.nre == "100123456789000"


def test_b4_invio_con_corpo_non_json_e_un_invio_incerto(materiale):
    class T:
        def invia(self, r):
            return Risposta(200, b"<html>gateway</html>", {}, 0.0)

    s = RicettaUmbria(_canale_locale(materiale, T()))
    with pytest.raises(InvioIncertoUmbria):
        s.invia(_spec("100123456789000"))


def test_b4_canale_puro_nessun_cambio_per_servizi_diversi_dall_invio(materiale):
    c = _canale_locale(materiale, _TrasportoFisso({"x": 1}, 502))
    with pytest.raises(Exception) as e:
        c.chiama(ServizioUmbria.LOTTO_NRE, {"codRegione": "100"})
    assert not isinstance(e.value, InvioIncertoUmbria)


def test_b4_schemi_delle_risposte_uguali_all_openapi_scaricata():
    from test_umbria import OPENAPI
    if not OPENAPI.exists():
        pytest.skip("specifiche Umbria non scaricate (strumenti/scarica_specifiche.py --gruppi umbria)")
    yaml = pytest.importorskip("yaml")
    schemi = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))["components"]["schemas"]
    for nome, (obbligatori, campi) in json_umbria.SCHEMI_RISPOSTE.items():
        s = schemi[nome]
        assert s.get("type") == "object" and "additionalProperties" not in s, nome
        assert set(s.get("required", [])) == set(obbligatori), nome
        attesi = {}
        for k, v in s["properties"].items():
            if "$ref" in v:
                attesi[k] = ("o", v["$ref"].rsplit("/", 1)[1])
            elif v.get("type") == "array":
                attesi[k] = ("a", v["items"]["$ref"].rsplit("/", 1)[1])
            else:
                assert v.get("type") == "string", (nome, k)
                attesi[k] = "s"
        assert campi == attesi, nome
    # e ogni risposta 200 dei servizi coperti punta a uno di questi schemi
    percorsi = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))["paths"]
    for servizio in ServizioUmbria:
        risposta = percorsi["/v1/servizi-prescrittore/" + servizio.value]["post"]["responses"]["200"]["content"]
        rif = next(iter(risposta.values()))["schema"]["$ref"].rsplit("/", 1)[1]
        assert rif in json_umbria.SCHEMI_RISPOSTE, (servizio, rif)


# ------------------------------------------------------------------ verifica mirata 1 (B1 e B2 PARZIALI)


@pytest.mark.parametrize("verso", [
    "https://example.org/sar",
    "https://demservicetest.sanita.finanze.it/x",  # il test del MEF: pubblico, ma non è localhost
])
@pytest.mark.parametrize("codice", [301, 302, 303])
def test_v1_b1_senza_adesione_nessun_host_fuori_da_localhost(materiale, verso, codice):
    t = _TrasportoCheSegueRedirect(verso)
    t.handler.codice = codice
    s = RicettaUmbria(_canale_locale(materiale, t))
    with pytest.raises(AmbienteBloccato):
        s.richiedi_lotto_nre()
    assert len(t.handler.visti) == 1, "i JWT sono usciti da localhost"


class _TrasportoHTTPCheSegue:
    """TrasportoHTTP vero (con la sua guardia interna) e un opener che segue i redirect, in memoria."""

    def __new__(cls, materiale, verso):
        t = TrasportoLocale(materiale, consenti_collaudo_regionale=True)
        h = _HandlerInMemoria(verso)
        o = urllib.request.OpenerDirector()
        for x in (h, urllib.request.HTTPRedirectHandler(), urllib.request.HTTPErrorProcessor()):
            o.add_handler(x)
        t._opener, t.handler = o, h
        return t


@pytest.mark.parametrize("verso", [
    URL_TEST,
    f"https://{HOST_UMBRIA_TEST.upper()}./sar/v1/x",
    "https://demtest.sanita.fvg.it/SARWs/x",
    "https://example.org/sar",
])
def test_v1_b1_la_guardia_interna_di_trasportohttp_non_rimette_i_permessi(materiale, verso):
    t = _TrasportoHTTPCheSegue(materiale, verso)
    s = RicettaUmbria(_canale_locale(materiale, t))
    with pytest.raises((AmbienteBloccato, ErroreTrasporto)):
        s.richiedi_lotto_nre()
    assert len(t.handler.visti) == 1, "il secondo salto è arrivato all'handler con i JWT"


def test_v1_b1_guardie_annidate_valgono_i_permessi_piu_stretti():
    from varco.trasporto.http import guardia_di_rete
    with guardia_di_rete((False, False, frozenset(), True)):
        with guardia_di_rete((True, True, frozenset({"x"}))) as g:
            assert g.permessi == (False, False, frozenset(), True)


def test_v1_b1_gruppo_di_controllo_con_adesione_il_flag_vale_ancora():
    from varco.trasporto.http import Richiesta

    class T:
        consenti_collaudo_regionale = True
        visto = None

        def invia(self, r):
            T.visto = r.url
            return Risposta(200, b"{}", {}, 0.0)

    consegna(T(), Richiesta(servizio="x", url=URL_TEST, corpo=b"{}"))
    assert T.visto == URL_TEST


@pytest.mark.parametrize("corpo", [
    {"title": "Prenotazione per MARIO ROSSI, tel 3331234567, mario@example.org"},
    {"esito": {"value": "MARIO ROSSI 3331234567"}},
    {"lotto": {"value": "1000A1000001"}},
    {"codLotto": {"value": "000001"}},
    {"esito": "3331234567"},
    {"esito": "06123456"},
])
def test_v1_b2_varianti_redatte(corpo):
    fuori = Redattore().corpo(json.dumps(corpo).encode()).decode("utf-8")
    for pezzo in ("MARIO ROSSI", "3331234567", "mario@example.org", "1000A1000001", "000001", "06123456"):
        assert pezzo not in fuori, fuori


def test_v1_b2_i_codici_corti_restano():
    fuori = Redattore().corpo(json.dumps({"esito": "0000", "codEsito": "5005"}).encode()).decode("utf-8")
    assert '"esito": "0000"' in fuori and '"codEsito": "5005"' in fuori


# ------------------------------------------------------------------ verifica mirata 2 (residui di B1 e B2)


def test_v2_b1_thread_nato_in_una_guardia_annidata_resta_della_chiamata(materiale):
    import threading

    from varco.trasporto.http import guardia_di_rete

    class Trasporto:
        consenti_collaudo_regionale = True

        def __init__(self):
            self.handler = _HandlerInMemoria("https://example.org/sar")
            self.opener = urllib.request.OpenerDirector()
            for h in (self.handler, urllib.request.HTTPRedirectHandler(), urllib.request.HTTPErrorProcessor()):
                self.opener.add_handler(h)

        def invia(self, richiesta):
            via, esito = threading.Event(), {}

            def lavoro():
                via.wait(5)
                try:
                    req = urllib.request.Request(richiesta.url, data=richiesta.corpo, headers=richiesta.intestazioni)
                    with self.opener.open(req) as r:
                        esito["r"] = Risposta(r.status, r.read(), {}, 0.0, url_finale=r.url)
                except Exception as e:  # noqa: BLE001
                    esito["e"] = e

            with guardia_di_rete((False, True, frozenset())):
                t = threading.Thread(target=lavoro)
                t.start()
            via.set()  # la guardia annidata è finita, quella di consegna no
            t.join(5)
            if "e" in esito:
                raise esito["e"]
            return esito["r"]

    tr = Trasporto()
    s = RicettaUmbria(_canale_locale(materiale, tr))
    with pytest.raises(AmbienteBloccato):
        s.richiedi_lotto_nre()
    assert len(tr.handler.visti) == 1, "il thread ha portato i JWT fuori da localhost"


@pytest.mark.parametrize("corpo", [
    {"email": "mario@example.org", "numeroTelefono": "3331234567", "message": "MARIO ROSSI",
     "prefissoLotto": "1000A1000001"},
    {"codEsitoInserimento": "0000", "nre": "1000A1000001000", "email": "mario@example.org",
     "numeroTelefono": "3331234567", "prefissoLotto": "1000A1000001"},
    {"codEsitoInserimento": "9999", "elencoErroriRicette": {"erroreRicetta": [
        {"codEsito": "MARIO ROSSI", "progPresc": "3331234567"}]}},
])
def test_v2_b2_allowlist_nel_json_umbro(corpo):
    from varco.trasporto.registro import CHIAVI_JSON_LEGGIBILI_UMBRIA
    fuori = Redattore().corpo(json.dumps(corpo).encode(), chiavi_json_leggibili=CHIAVI_JSON_LEGGIBILI_UMBRIA)
    fuori = fuori.decode("utf-8")
    for pezzo in ("mario@example.org", "3331234567", "MARIO ROSSI", "1000A1000001"):
        assert pezzo not in fuori, fuori


def test_v2_b2_allowlist_gruppo_di_controllo():
    from varco.trasporto.registro import CHIAVI_JSON_LEGGIBILI_UMBRIA
    corpo = {"codEsitoInserimento": "0000", "dataInserimento": "2026-10-03 11:00:01", "statoProcesso": "3",
             "elencoErroriRicette": {"erroreRicetta": [{"codEsito": "5005", "progPresc": "1"}]}}
    fuori = json.loads(Redattore().corpo(json.dumps(corpo).encode(), chiavi_json_leggibili=CHIAVI_JSON_LEGGIBILI_UMBRIA))
    assert fuori == corpo
    # in chiaro l'allowlist non vale: il corpo resta identico
    b = json.dumps({"email": "mario@example.org"}).encode()
    assert Redattore().corpo(b, redigi=False, chiavi_json_leggibili=CHIAVI_JSON_LEGGIBILI_UMBRIA) == b


def test_v2_b2_registro_su_disco_con_campi_in_piu(materiale, tmp_path):
    corpo = {"codEsitoInserimento": "0000", "nre": "100123456789000", "email": "mario@example.org",
             "numeroTelefono": "3331234567", "prefissoLotto": "1000A1000001"}

    class T:
        def __init__(self):
            self.registratore = RegistratoreFile(tmp_path)

        def invia(self, r):
            risposta = Risposta(200, json.dumps(corpo).encode(), {}, 0.0)
            self.registratore(r, risposta, None)
            return risposta

    s = RicettaUmbria(_canale_locale(materiale, T()))
    s.invia(_spec("100123456789000"))
    testo = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    for pezzo in ("mario@example.org", "3331234567", "1000A1000001", ASSISTITO):
        assert pezzo not in testo, pezzo
