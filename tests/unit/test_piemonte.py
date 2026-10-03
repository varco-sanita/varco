# SPDX-License-Identifier: EUPL-1.2
"""SIRPED, SAR della Regione Piemonte (CSI Piemonte): guardia, canale, Id-Sessione via mail
(CreateAuth/CheckToken/RevokeAuth), OAuth2 con PKCE e JWT, codifica contro gli XSD ufficiali,
registro redatto, giro completo contro il server finto (strumenti/piemonte_server_finto.py).

Nessuna chiamata alla Regione né al CSI: il server è su 127.0.0.1. I test che confrontano con gli
XSD UFFICIALI del kit A2F si saltano se le specifiche non sono scaricate
(strumenti/scarica_specifiche.py --gruppi piemonte): non stanno nel repository. Gli XSD del MEF per
la prescrizione sono inclusi nel kit e valgono sempre.

Scritto e verificato sulle specifiche, NON collaudato sul sistema regionale.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as _dt
import hashlib
import importlib.util
import inspect
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from varco import AmbienteBloccato, CifratoreSanitel, ConfigurazioneNonValida, Credenziali, ErroreSOAP
from varco.ambienti import e_collaudo_piemonte, e_collaudo_regionale, e_produzione, e_regione_piemonte, verifica_url_consentito
from varco.errori import RicettaNonValida
from varco.ricetta import (
    Assistito,
    CriteriNreUtilizzati,
    Prescrittore,
    Ricetta,
    RicettaPiemonte,
    Riga,
    TipoPrescrizione,
)
from varco.ricetta import piemonte as rp
from varco.schemi import errori_xsd
from varco.trasporto import RegistratoreFile, Richiesta, TrasportoHTTP
from varco.trasporto import piemonte_a2f as a2f
from varco.trasporto.piemonte import (
    PERCORSI_LOCALI,
    SOAP_ACTION,
    SOAP_ACTION_A2F,
    AdesionePiemonte,
    CanalePiemonte,
    GestionalePiemonte,
    ModalitaPiemonte,
    ServizioPiemonte,
    leggi_jwt,
)
from varco.trasporto.piemonte_oauth2 import (
    ClientOAuth2Piemonte,
    ErroreAutorizzazione,
    TokenPiemonte,
    challenge_s256,
    nuovo_verifier,
    verifica_firma,
)
from varco.trasporto.registro import Redattore
from varco.trasporto.soap import imbusta, sbusta

etree = pytest.importorskip("lxml.etree")

RADICE = Path(__file__).resolve().parents[2]
SPEC = RADICE / "specifiche" / "piemonte"
XSD_A2F = SPEC / "a2f" / "Kit per lo sviluppo - A2F SistemaTS - ver. 20250902" / "wsdl"
RISPOSTE = RADICE / "conformita" / "risposte" / "piemonte"
TITOLARE, SOSTITUTO, ASSISTITO = "PROVAX00X00X000Y", "PROVAX00X00X000Z", "PNIMRA70A01H501P"
GESTIONALE = GestionalePiemonte("VARCO", "301")
REDIRECT = "http://localhost:8081/callback"
UUID_PROVA = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def _modulo(nome: str):
    spec = importlib.util.spec_from_file_location(nome, RADICE / "strumenti" / f"{nome}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(nome, mod)
    spec.loader.exec_module(mod)
    return mod


SERVER = _modulo("piemonte_server_finto")
richiede_xsd_a2f = pytest.mark.skipif(not XSD_A2F.exists(), reason="specifiche Piemonte non scaricate "
                                      "(strumenti/scarica_specifiche.py --gruppi piemonte)")


@pytest.fixture
def rete_vietata(monkeypatch):
    def vietato(*a, **k):
        raise AssertionError("nessuna chiamata di rete doveva partire")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", vietato)


@pytest.fixture(scope="module")
def regione():
    return SERVER.materiale_regione()


def _cifratore(regione) -> CifratoreSanitel:
    return CifratoreSanitel(regione.certificato_cifratura_pem)


def _ricetta(cf=TITOLARE, sostituto=None, **kw) -> Ricetta:
    base = dict(
        prescrittore=Prescrittore(cf, "010", "301", "F", codice_fiscale_sostituto=sostituto),
        assistito=Assistito(ASSISTITO, provincia="TO", asl="301"),
        tipo=TipoPrescrizione.FARMACEUTICA,
        righe=(Riga(1, codice_gruppo_equivalenza="G3B", descrizione_gruppo_equivalenza="GRUPPO DI PROVA"),),
        data_compilazione=_dt.datetime(2026, 10, 1, 11, 0),
    )
    base.update(kw)
    return Ricetta(**base)


def _cred(cf=TITOLARE, utente="rupar-tit", password="pw-tit", pincode="1234567890") -> Credenziali:
    return Credenziali(utente, password, pincode, cf)


def _jwt(**payload) -> str:
    from varco.conformita.piemonte import jwt_da_payload

    base = {"sub": TITOLARE, "exp": 4102444800, "scope": "prescrizione",
            "userData": {"cfutente": TITOLARE, "idSessione": UUID_PROVA}}
    return jwt_da_payload(base | payload)


# ------------------------------------------------------------------ guardia anti-produzione


@pytest.mark.parametrize(
    "url,regione_,produzione,collaudo",
    [
        ("https://tst-rel-xxxx.csi.it/servizio", True, False, True),  # l'esempio del par. 4.3.6
        ("https://TST-REL-XXXX.csi.it./servizio", True, False, True),  # maiuscole e punto finale: stesso host
        ("https://test.sirped.csi.it/x", True, False, True),
        ("https://sirped-collaudo.regione.piemonte.it/x", True, False, True),
        ("https://rel.csi.it/servizio", True, True, False),  # nessun host pubblicato: ogni altro è produzione
        ("https://servizi.regione.piemonte.it/media/3354/download", True, True, False),
        ("https://attestazioni.regione.piemonte.it/x", True, True, False),  # «test» dentro una parola: non conta
        ("https://latest.csi.it/x", True, True, False),
        ("https://operatori.salutepiemonte.it/", True, True, False),
        ("https://www.csipiemonte.it/", True, True, False),
        ("https://www.sistemapiemonte.it/", True, True, False),
        ("https://tst-rel.csi.it.example.org/x", False, False, False),  # non è un host piemontese
        ("https://csi.it.example.org/x", False, False, False),
    ],
)
def test_host_piemontesi_riconosciuti(url, regione_, produzione, collaudo):
    assert e_regione_piemonte(url) is regione_
    assert e_produzione(url) is produzione
    assert e_collaudo_piemonte(url) is collaudo
    assert e_collaudo_regionale(url) is collaudo


def test_collaudo_piemonte_vuole_flag_E_host_dichiarato():
    url = "https://tst-rel-xxxx.csi.it/servizio"
    with pytest.raises(AmbienteBloccato, match="Piemonte"):
        verifica_url_consentito(url)
    with pytest.raises(AmbienteBloccato, match="collaudi_piemonte"):
        verifica_url_consentito(url, consenti_collaudo_regionale=True)  # flag senza dichiarazione
    with pytest.raises(AmbienteBloccato):
        verifica_url_consentito(url, collaudi_piemonte={"tst-rel-xxxx.csi.it"})  # dichiarazione senza flag
    with pytest.raises(AmbienteBloccato):
        verifica_url_consentito(url, consenti_collaudo_regionale=1, collaudi_piemonte={"tst-rel-xxxx.csi.it"})
    verifica_url_consentito(url, consenti_collaudo_regionale=True, collaudi_piemonte={"TST-REL-XXXX.csi.it."})
    # la produzione non passa né col flag del collaudo né dichiarandola come collaudo
    prod = "https://rel.csi.it/servizio"
    with pytest.raises(AmbienteBloccato, match="PRODUZIONE"):
        verifica_url_consentito(prod, consenti_collaudo_regionale=True, collaudi_piemonte={"rel.csi.it"})


@pytest.mark.parametrize("url", ["https://tst-rel-xxxx.csi.it/a", "https://rel.csi.it/a", "https://sirped.regione.piemonte.it/a"])
def test_trasporto_blocca_ogni_host_piemontese_prima_della_rete(rete_vietata, url):
    with pytest.raises(AmbienteBloccato):
        TrasportoHTTP().invia(Richiesta("x", url, b""))
    with pytest.raises(AmbienteBloccato):
        TrasportoHTTP(consenti_collaudo_regionale=True).invia(Richiesta("x", url, b""))


def test_con_adesione_resta_comunque_la_guardia(rete_vietata, regione):
    url = {s: f"https://tst-rel-xxxx.csi.it/{s.value}" for s in ServizioPiemonte}
    ades = AdesionePiemonte("RICHIESTA-PROVA-1", GESTIONALE)
    can = CanalePiemonte(ModalitaPiemonte.MAIL, credenziali=_cred(), id_sessione=lambda: UUID_PROVA, adesione=ades, url=url)
    with pytest.raises(AmbienteBloccato):
        RicettaPiemonte(can, _cifratore(regione)).invia(_ricetta())
    with pytest.raises(AmbienteBloccato):
        a2f.ServizioIdSessione(can, _cifratore(regione), a2f.UtenteA2F(TITOLARE, "301")).crea()
    client = ClientOAuth2Piemonte("https://tst-rel-xxxx.csi.it/reloauthserver", GESTIONALE, REDIRECT, adesione=ades)
    with pytest.raises(AmbienteBloccato):
        client.jwks()
    with pytest.raises(AmbienteBloccato):  # l'URL per il browser: la guardia scatta prima che il medico si autentichi
        client.autorizzazione()
    with pytest.raises(AmbienteBloccato):
        ClientOAuth2Piemonte("https://rel.csi.it/reloauthserver", GESTIONALE, REDIRECT, adesione=ades,
                             trasporto=TrasportoHTTP(consenti_collaudo_regionale=True)).jwks()
    with pytest.raises(AmbienteBloccato, match="PRODUZIONE"):
        ClientOAuth2Piemonte("https://rel.csi.it/reloauthserver", GESTIONALE, REDIRECT, adesione=ades).autorizzazione()
    # con le tre serrature il collaudo dichiarato si costruisce (nessuna rete: solo l'URL)
    aperto = ClientOAuth2Piemonte("https://tst-rel-xxxx.csi.it/reloauthserver", GESTIONALE, REDIRECT, adesione=ades,
                                  trasporto=TrasportoHTTP(consenti_collaudo_regionale=True,
                                                          collaudi_piemonte={"tst-rel-xxxx.csi.it"}))
    assert aperto.autorizzazione().url.startswith("https://tst-rel-xxxx.csi.it/reloauthserver/oauth2/authorize?")


def test_client_oauth2_verso_la_regione_vuole_l_adesione():
    with pytest.raises(ConfigurazioneNonValida, match="AdesionePiemonte"):
        ClientOAuth2Piemonte("https://tst-rel-xxxx.csi.it/reloauthserver", GESTIONALE, REDIRECT)
    with pytest.raises(ConfigurazioneNonValida, match="gestionale"):
        ClientOAuth2Piemonte("https://tst-rel-xxxx.csi.it/reloauthserver", GESTIONALE, REDIRECT,
                             adesione=AdesionePiemonte("R", GestionalePiemonte("ALTRO", "301")))
    ClientOAuth2Piemonte("http://127.0.0.1:9/reloauthserver", GESTIONALE, REDIRECT)  # localhost: basta il gestionale


# ------------------------------------------------------------------ canale


def test_canale_verso_la_regione_vuole_un_adesione_e_gli_url():
    url = {ServizioPiemonte.INVIO: "https://tst-rel-xxxx.csi.it/invio"}
    with pytest.raises(ConfigurazioneNonValida, match="AdesionePiemonte"):
        CanalePiemonte(ModalitaPiemonte.MAIL, credenziali=_cred(), url=url)
    with pytest.raises(ConfigurazioneNonValida, match="localhost"):
        CanalePiemonte(ModalitaPiemonte.MAIL, credenziali=_cred(), base_url="https://rel.csi.it", gestionale_di_prova=GESTIONALE)
    with pytest.raises(ConfigurazioneNonValida, match="localhost"):
        CanalePiemonte(ModalitaPiemonte.MAIL, credenziali=_cred(), url=url, adesione=AdesionePiemonte("R", GESTIONALE),
                       gestionale_di_prova=GESTIONALE)
    with pytest.raises(ConfigurazioneNonValida, match="riferimento"):
        AdesionePiemonte(" ", GESTIONALE)
    can = CanalePiemonte(ModalitaPiemonte.MAIL, credenziali=_cred(), url=url, adesione=AdesionePiemonte("R", GESTIONALE))
    with pytest.raises(ConfigurazioneNonValida, match="kit del CSI"):  # nessun URL pubblicato per la visualizzazione
        can.url_di(ServizioPiemonte.VISUALIZZA)


def test_gestionale_come_nell_esempio_della_specifica():
    assert GestionalePiemonte("MIOAPPLICATIVO", "301").valore == "MIOAPPLICATIVO_301"
    for codice, azienda in (("", "301"), ("MIO APP", "301"), ("MIO_APP", "301"), ("MIO", "30"), ("MIO", "3011")):
        with pytest.raises(ConfigurazioneNonValida):
            GestionalePiemonte(codice, azienda)


def test_modalita_e_credenziali_coerenti():
    loc = dict(gestionale_di_prova=GESTIONALE, base_url="http://127.0.0.1:9")
    with pytest.raises(ConfigurazioneNonValida, match="RUPAR"):
        CanalePiemonte(ModalitaPiemonte.MAIL, **loc)
    with pytest.raises(ConfigurazioneNonValida, match="Basic"):  # in OAuth2 la Basic va tolta
        CanalePiemonte(ModalitaPiemonte.OAUTH2, credenziali=_cred(), token_jwt=_jwt, cf_medico=TITOLARE, **loc)
    with pytest.raises(ConfigurazioneNonValida, match="token_jwt"):
        CanalePiemonte(ModalitaPiemonte.OAUTH2, cf_medico=TITOLARE, **loc)
    with pytest.raises(ConfigurazioneNonValida, match="16"):
        CanalePiemonte(ModalitaPiemonte.MAIL, credenziali=_cred(utente="u" * 17), **loc)
    with pytest.raises(ConfigurazioneNonValida, match="JWT"):
        CanalePiemonte(ModalitaPiemonte.MAIL, credenziali=_cred(), token_jwt=_jwt, **loc)


def test_intestazioni_delle_due_modalita():
    loc = dict(gestionale_di_prova=GESTIONALE, base_url="http://127.0.0.1:9")
    mail = CanalePiemonte(ModalitaPiemonte.MAIL, credenziali=_cred(), id_sessione=lambda: UUID_PROVA, **loc)
    h = mail.intestazioni(ServizioPiemonte.INVIO, SOAP_ACTION[ServizioPiemonte.INVIO])
    assert h["Authorization"] == "Basic " + base64.b64encode(b"rupar-tit:pw-tit").decode()
    assert h["X-idSessione"] == f"Bearer {UUID_PROVA}" and h["X-Gestionale"] == "VARCO_301"
    assert "Authorization2F" not in h and "X-OAuth2-Authorization" not in h
    h = mail.intestazioni(ServizioPiemonte.ID_SESSIONE, SOAP_ACTION_A2F["create"])
    assert "X-idSessione" not in h and h["SOAPAction"] == '"http://wsdl.auth.a2f.sts.sanita.finanze.it/create"'
    token = _jwt()
    oauth = CanalePiemonte(ModalitaPiemonte.OAUTH2, token_jwt=lambda: token, cf_medico=TITOLARE, **loc)
    h = oauth.intestazioni(ServizioPiemonte.INVIO, SOAP_ACTION[ServizioPiemonte.INVIO])
    assert h["X-OAuth2-Authorization"] == f"Bearer {token}"
    assert not {"Authorization", "X-idSessione", "X-Gestionale"} & set(h)
    with pytest.raises(ConfigurazioneNonValida, match="MAIL"):
        oauth.chiama(ServizioPiemonte.ID_SESSIONE, ET.Element("x"))


def test_jwt_scaduto_o_di_un_altro_medico_non_parte():
    loc = dict(gestionale_di_prova=GESTIONALE, base_url="http://127.0.0.1:9")
    for token, messaggio in ((_jwt(exp=1_000_000_000), "scaduto"),
                             (_jwt(sub=SOSTITUTO, userData={"cfutente": SOSTITUTO}), SOSTITUTO),
                             ("non.un-jwt", "non")):
        can = CanalePiemonte(ModalitaPiemonte.OAUTH2, token_jwt=lambda t=token: t, cf_medico=TITOLARE, **loc)
        with pytest.raises(ConfigurazioneNonValida, match=messaggio):
            can.intestazioni(ServizioPiemonte.INVIO, "x")


def test_contratto_comune():
    from varco.ricetta.servizio import ServizioRicetta

    for nome in ("invia", "visualizza", "annulla", "interroga_nre_utilizzati"):
        assert list(inspect.signature(getattr(RicettaPiemonte, nome)).parameters) == list(
            inspect.signature(getattr(ServizioRicetta, nome)).parameters), nome
    p = inspect.signature(RicettaPiemonte.visualizza).parameters["cf_assistito"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is None


def test_il_modello_non_e_cambiato():
    """SIRPED sta tutto sotto lo stesso modello: i campi sono quelli di prima del modulo Piemonte (01/10/2026)."""
    attesi = {
        Ricetta: ["prescrittore", "assistito", "tipo", "righe", "tipo_visita", "data_compilazione", "non_esente",
                  "codice_esenzione", "esente_reddito", "codice_diagnosi", "descrizione_diagnosi", "classe_priorita",
                  "indicazione", "altro", "ricetta_interna", "disposizioni_regionali", "testata1", "testata2", "nre"],
        Prescrittore: ["codice_fiscale", "codice_regione", "codice_asl", "codice_specializzazione", "codice_struttura",
                       "codice_fiscale_sostituto"],
        Assistito: ["codice_fiscale", "cognome_nome", "indirizzo", "provincia", "asl", "codice_regione", "oscura_dati",
                    "tipo_ricetta", "stato_estero", "istituzione_competente", "num_ident_personale", "num_ident_tessera",
                    "data_nascita_estero", "data_scadenza_tessera", "num_tessera_sasn", "societa_navigazione"],
        CriteriNreUtilizzati: ["codice_regione", "nre", "codice_lotto", "cf_assistito", "tipo", "dal", "al"],
    }
    for classe, campi in attesi.items():
        assert [f.name for f in dataclasses.fields(classe)] == campi, classe.__name__
    assert len(dataclasses.fields(Riga)) == 19


def test_cifratore_obbligatorio_e_niente_sanitel_di_default(regione):
    can = CanalePiemonte(ModalitaPiemonte.MAIL, credenziali=_cred(), gestionale_di_prova=GESTIONALE, base_url="http://127.0.0.1:9")
    with pytest.raises(ValueError, match="Regione Piemonte"):
        RicettaPiemonte(can, None)
    with pytest.raises(TypeError):
        RicettaPiemonte(can)  # nessun default: SanitelCF qui sarebbe sbagliato
    with pytest.raises(ConfigurazioneNonValida, match="cifratore"):
        a2f.ServizioIdSessione(can, None, a2f.UtenteA2F(TITOLARE, "301"))
    with pytest.raises(ConfigurazioneNonValida, match="cfUtente"):
        a2f.ServizioIdSessione(can, _cifratore(regione), a2f.UtenteA2F(SOSTITUTO, "301"))


# ------------------------------------------------------------------ codifica (XSD del MEF, sempre; A2F se scaricati)


def _cifra(_v):
    return "CIFRATO=="


@pytest.mark.parametrize("modalita", list(ModalitaPiemonte))
def test_richieste_di_prescrizione_validano_contro_gli_xsd_del_mef(modalita):
    pin_atteso = "" if modalita is ModalitaPiemonte.OAUTH2 else "CIFRATO=="
    richieste = [
        rp.richiesta("invio", modalita, _cifra, ricetta=_ricetta()),
        rp.richiesta("invio", modalita, _cifra, ricetta=_ricetta(sostituto=SOSTITUTO)),
        rp.richiesta("visualizza", modalita, _cifra, nre="0100A0000000001", cf_medico=TITOLARE),
        rp.richiesta("annulla", modalita, _cifra, nre="0100A0000000001", cf_medico=TITOLARE),
        rp.richiesta("interroga_nre", modalita, _cifra, criteri=CriteriNreUtilizzati("010", nre="0100A0000000001"),
                     cf_medico=TITOLARE),
    ]
    for el in richieste:
        assert errori_xsd(el) == [], el.tag
        pin = next(c for c in el if c.tag.endswith("}pinCode"))
        assert (pin.text or "") == pin_atteso
    # stessi namespace del MEF (l'esempio del par. 4.3.6 usa invioprescrittorichiesta.xsd.dem.sanita.finanze.it)
    assert richieste[0].tag == "{http://invioprescrittorichiesta.xsd.dem.sanita.finanze.it}InvioPrescrittoRichiesta"


def test_gli_xsd_del_mef_mordono_sulla_richiesta_piemontese():
    el = rp.richiesta("invio", ModalitaPiemonte.OAUTH2, _cifra, ricetta=_ricetta())
    el.remove(next(c for c in el if c.tag.endswith("}pinCode")))  # pinCode vuoto sì, assente no
    assert errori_xsd(el)


def _crea(**kw) -> ET.Element:
    base = dict(utente_rupar="rupar-tit", pincode="1234567890", cifra=_cifra, utente=a2f.UtenteA2F(TITOLARE, "301"),
                valore_app="VARCO_301")
    return a2f.richiesta_crea(**(base | kw))


def _schema_a2f():
    return etree.XMLSchema(etree.parse(str(XSD_A2F / "sts-a2f-service.v0.1.xsd")))


def _valida_a2f(el) -> list[str]:
    s = _schema_a2f()
    return [] if s.validate(etree.fromstring(ET.tostring(el))) else [e.message for e in s.error_log]


@richiede_xsd_a2f
def test_richieste_a2f_validano_contro_gli_xsd_ufficiali():
    for el in (
        _crea(),
        _crea(utente=a2f.UtenteA2F(TITOLARE, "301", "000000"), permessi=["prescrizione", "erogazione"]),
        a2f.richiesta_verifica("rupar-tit", "1234567890", _cifra, TITOLARE, UUID_PROVA, "VARCO_301"),
        a2f.richiesta_revoca("rupar-tit", "1234567890", _cifra, TITOLARE, UUID_PROVA, "VARCO_301"),
    ):
        assert _valida_a2f(el) == [], el.tag


@richiede_xsd_a2f
def test_gli_xsd_a2f_mordono_e_i_difetti_noti_ci_sono():
    """Controlli negativi, e i difetti fissati: se la specifica cambia, il test se ne accorge."""
    el = _crea()
    ET.SubElement(el, f"{{{a2f.NS_AUT}}}codSsa").text = ""  # codSsa vuoto: minLength 5
    assert _valida_a2f(el)
    # i tre permessi insieme (ammessi dal par. 4.2.1) non stanno nei 30 caratteri di applicazioneType
    el = _crea()
    next(c for c in el if c.tag.endswith("}applicazione")).text = "prescrizione erogazione presa_in_carico"
    assert any("maxLength" in m for m in _valida_a2f(el))
    with pytest.raises(ConfigurazioneNonValida, match="30 caratteri"):
        _crea(permessi=["prescrizione", "erogazione", "presa_in_carico"])
    # pincode cifrato con una chiave RSA a 2048 bit: 344 caratteri, valore ne ammette 256
    grande = CifratoreSanitel(SERVER.materiale_regione(2048).certificato_cifratura_pem)
    with pytest.raises(ConfigurazioneNonValida, match="1536 bit"):
        _crea(cifra=grande.cifra)
    el = _crea()
    next(e for e in el.iter() if e.tag == f"{{{a2f.NS_DAT}}}valore").text = grande.cifra("1234567890")
    assert any("maxLength" in m for m in _valida_a2f(el))
    # 1024 bit (come SanitelCF): ci sta
    assert _valida_a2f(_crea(cifra=CifratoreSanitel.incluso().cifra)) == []


@richiede_xsd_a2f
def test_namespace_e_soapaction_sono_quelli_del_kit_a2f():
    xsd = etree.parse(str(XSD_A2F / "sts-a2f-service.v0.1.xsd")).getroot()
    tipi = etree.parse(str(XSD_A2F / "sts-a2f-service-data-type.v0.1.xsd")).getroot()
    assert xsd.get("targetNamespace") == a2f.NS_AUT and tipi.get("targetNamespace") == a2f.NS_DAT
    wsdl = etree.parse(str(XSD_A2F / "sts-a2f-service.wsdl"))
    azioni = {op.getparent().get("name"): op.get("soapAction")
              for op in wsdl.iter("{http://schemas.xmlsoap.org/wsdl/soap/}operation")}
    assert azioni == SOAP_ACTION_A2F


def test_controlli_locali_a2f():
    for utente, messaggio in ((a2f.UtenteA2F("CORTO", "301"), "cfUtente"), (a2f.UtenteA2F(TITOLARE, "30"), "codAslAo"),
                              (a2f.UtenteA2F(TITOLARE, "301", ""), "codSsa")):
        with pytest.raises(ConfigurazioneNonValida, match=messaggio):
            _crea(utente=utente)
    with pytest.raises(ConfigurazioneNonValida, match="permessi"):
        _crea(permessi=["PRESCRITTORE"])
    with pytest.raises(ConfigurazioneNonValida, match="UUID"):
        a2f.richiesta_verifica("u", "1", _cifra, TITOLARE, "non-un-uuid", "VARCO_301")
    assert a2f.applicazione(["prescrizione", "prescrizione"]) == "prescrizione"


# ------------------------------------------------------------------ lettura delle risposte sintetiche


def test_risposte_sintetiche_dichiarate_e_rigenerabili():
    assert (RISPOSTE / "LEGGIMI.md").exists()
    for nome, dati in SERVER.risposte_sintetiche().items():
        assert (RISPOSTE / nome).read_bytes() == dati, nome
    for nome in ("jwks.json", "jwks_campo_v.json", "jwt_valido.txt", "jwt_alterato.txt"):
        assert (RISPOSTE / nome).exists(), nome


@richiede_xsd_a2f
@pytest.mark.parametrize("nome", sorted(SERVER.risposte_sintetiche()))
def test_risposte_sintetiche_contro_lo_xsd_a2f(nome):
    el = sbusta((RISPOSTE / nome).read_bytes(), 200)
    valida = _schema_a2f().validate(etree.fromstring(ET.tostring(el)))
    # l'unica che NON valida è quella nella forma dell'esempio del par. 4.2.4, apposta
    assert valida is (nome != "crea_errore_forma_esempio.xml")


def test_leggi_risposte_a2f():
    def leggi(nome):
        return a2f.leggi_esito(sbusta((RISPOSTE / nome).read_bytes(), 200))

    e = leggi("crea_ok_test.xml")
    assert e.ok and e.ambiente_test and e.id_sessione_di_test == UUID_PROVA and e.permessi == ("prescrizione",)
    assert leggi("crea_ok_produzione.xml").id_sessione_di_test is None
    assert leggi("crea_token_senza_working_mode.xml").id_sessione_di_test is None  # niente TEST, niente token
    assert not leggi("crea_fatale.xml").ok and leggi("crea_fatale.xml").errori[0].bloccante
    assert leggi("crea_avviso.xml").ok
    assert [leggi(f"verifica_{s}.xml").stato_token.stato for s in ("valido", "revocato", "scaduto")] == [0, 1, 2]
    assert leggi("revoca_gia_revocato.xml").info_di("lastRevokePreviousDate") == "30/05/2025 20:51:07"
    assert leggi("crea_errore_forma_esempio.xml").errori[0].codice == "9998"
    with pytest.raises(ValueError):
        a2f.leggi_esito(ET.fromstring("<InvioPrescrittoRicevuta/>"))


def test_ricevute_del_server_finto_validano_contro_gli_xsd_del_mef():
    r = SERVER.RicettaFinta("0100A0000000001", TITOLARE, None, "F", "2026-10-01 11:00:00", "0" * 30, "2026-10-01 11:00:01",
                            [[("codGruppoEquival", "G3B"), ("descrGruppoEquival", "X"), ("quantita", "1")]])
    for busta in (SERVER.ricevuta_invio("0000", r.nre, r.codice_aut, r.data_inserimento),
                  SERVER.ricevuta_invio("9999", errori=[("1020", "nota AIFA non valida", "1", "E")]),
                  SERVER.ricevuta_visualizza("0000", r), SERVER.ricevuta_annulla("0000", r.nre),
                  SERVER.ricevuta_interroga_nre("0000", [r])):
        assert errori_xsd(ET.tostring(sbusta(busta, 200))) == []


# ------------------------------------------------------------------ OAuth2: PKCE e JWT, senza rete


def test_pkce_come_la_rfc_7636_e_non_come_l_esempio_in_shell():
    assert challenge_s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    v = nuovo_verifier()
    assert 43 <= len(v) <= 128 and challenge_s256(v)
    for cattivo in ("a" * 42, "a" * 129, "a" * 42 + "+", "a" * 42 + "/"):
        with pytest.raises(ConfigurazioneNonValida):
            challenge_s256(cattivo)
    # L'esempio del par. 4.3.1: «openssl base64 | tr -d "=+/"» CANCELLA + e / invece di tradurli in - e _.
    # Con questo verifier la differenza si vede: il server che segue la RFC rifiuterebbe la challenge.
    verifier = "a" * 43
    rfc = challenge_s256(verifier)
    shell = base64.b64encode(hashlib.sha256(verifier.encode()).digest()).decode().replace("=", "").replace("+", "").replace("/", "")
    if "-" in rfc or "_" in rfc:
        assert shell != rfc
    # e su molti verifier casuali la differenza è la regola, non l'eccezione
    diversi = 0
    for i in range(200):
        v = hashlib.sha256(str(i).encode()).hexdigest()[:43]
        d = hashlib.sha256(v.encode()).digest()
        diversi += base64.b64encode(d).decode().strip("=").replace("+", "").replace("/", "") != challenge_s256(v)
    assert diversi > 100


def test_verifier_generato_come_l_esempio_spesso_e_troppo_corto():
    """«openssl rand -base64 32 | tr -d "=+/" | cut -c1-43»: se il base64 contiene + o /, restano meno di 43
    caratteri, sotto il minimo della RFC e della specifica stessa."""
    corti = sum(len(base64.b64encode(hashlib.sha256(str(i).encode()).digest()).decode().strip("=")
                    .replace("+", "").replace("/", "")[:43]) < 43 for i in range(200))
    assert corti > 100


def test_firma_del_jwt_con_il_jwks():
    token = (RISPOSTE / "jwt_valido.txt").read_text().strip()
    jwks = json.loads((RISPOSTE / "jwks.json").read_text())
    c = verifica_firma(token, jwks)
    assert c.cf_utente == TITOLARE and c.id_sessione == UUID_PROVA and c.scope == ("prescrizione",)
    assert verifica_firma(token, json.loads((RISPOSTE / "jwks_campo_v.json").read_text())).sub == TITOLARE
    with pytest.raises(ConfigurazioneNonValida, match="firma"):
        verifica_firma((RISPOSTE / "jwt_alterato.txt").read_text().strip(), jwks)
    # alg none e HMAC rifiutati, anche con un payload valido
    testa_none = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    _, corpo, firma = token.split(".")
    for testa in (testa_none, base64.urlsafe_b64encode(b'{"alg":"HS256","kid":"rel-oauth2-key"}').rstrip(b"=").decode()):
        with pytest.raises(ConfigurazioneNonValida, match="algoritmo"):
            verifica_firma(f"{testa}.{corpo}.{firma}", jwks)
    # kid diverso (come nell'esempio della specifica, un UUID) con una sola chiave: decide la firma
    altro_kid = dict(jwks["keys"][0], kid="d9d33261-8b20-4002-bed4-9c13aaaaaaaa")
    assert verifica_firma(token, {"keys": [altro_kid]}).sub == TITOLARE
    with pytest.raises(ConfigurazioneNonValida, match="kid"):
        verifica_firma(token, {"keys": [altro_kid, dict(altro_kid, kid="un-altro")]})


def test_expires_in_durata_o_istante():
    c = leggi_jwt(_jwt())
    assert c.scadenza == 4102444800
    senza_exp = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode() + "." + \
        base64.urlsafe_b64encode(b'{"sub":"X"}').rstrip(b"=").decode() + ".x"
    assert TokenPiemonte(senza_exp, "Bearer", (), None, 7199, 1000.0, ).scadenza == 1000.0 + 7199
    assert TokenPiemonte(senza_exp, "Bearer", (), None, 4102444800, 1000.0).scadenza == 4102444800


def test_url_di_autorizzazione_e_callback():
    client = ClientOAuth2Piemonte("https://rel.esempio.invalid/reloauthserver", GESTIONALE, REDIRECT,
                                  adesione=AdesionePiemonte("R", GESTIONALE))
    ra = client.autorizzazione(["prescrizione"])
    from urllib.parse import parse_qs, urlparse

    q = {k: v[0] for k, v in parse_qs(urlparse(ra.url).query).items()}
    assert urlparse(ra.url).path == "/reloauthserver/oauth2/authorize"
    assert q == {"client_id": "VARCO_301", "response_type": "code", "redirect_uri": REDIRECT, "scope": "prescrizione",
                 "state": ra.state, "code_challenge": challenge_s256(ra.code_verifier), "code_challenge_method": "S256"}
    assert client.leggi_callback(f"{REDIRECT}?code=ABC&state={ra.state}", ra) == "ABC"
    with pytest.raises(ErroreAutorizzazione, match="state"):
        client.leggi_callback(f"{REDIRECT}?code=ABC&state=altro", ra)
    with pytest.raises(ErroreAutorizzazione, match="access_denied"):
        client.leggi_callback(f"{REDIRECT}?error=access_denied&error_description=no&state={ra.state}", ra)
    with pytest.raises(ErroreAutorizzazione, match="redirect"):
        client.leggi_callback(f"http://localhost:9999/callback?code=ABC&state={ra.state}", ra)
    with pytest.raises(ConfigurazioneNonValida):
        client.autorizzazione(["presa_in_carico_citt"])  # lo scope del cittadino non è di un MMG
    with pytest.raises(ConfigurazioneNonValida, match="500"):
        client.autorizzazione(state="s" * 501)


def test_jwks_senza_kid_con_piu_chiavi_non_sceglie_a_caso():
    jwks = json.loads((RISPOSTE / "jwks.json").read_text())
    token = (RISPOSTE / "jwt_valido.txt").read_text().strip()
    testa = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    senza_kid = testa + "." + ".".join(token.split(".")[1:])
    due = {"keys": [jwks["keys"][0], dict(jwks["keys"][0], kid="altra")]}
    with pytest.raises(ConfigurazioneNonValida, match="senza kid"):
        verifica_firma(senza_kid, due)


def test_jwt_con_sub_e_cfutente_diversi_non_parte():
    loc = dict(gestionale_di_prova=GESTIONALE, base_url="http://127.0.0.1:9")
    token = _jwt(sub=TITOLARE, userData={"cfutente": SOSTITUTO, "idSessione": UUID_PROVA})
    can = CanalePiemonte(ModalitaPiemonte.OAUTH2, token_jwt=lambda: token, cf_medico=TITOLARE, **loc)
    with pytest.raises(ConfigurazioneNonValida, match="incoerente"):
        can.intestazioni(ServizioPiemonte.INVIO, "x")


def test_registro_reda_token_opachi_nel_json(tmp_path):
    from varco.trasporto.http import Risposta

    reg = RegistratoreFile(tmp_path)
    corpo = json.dumps({"access_token": "OPACO-SEGRETO-123", "refresh_token": "ALTRO-SEGRETO", "token_type": "Bearer"})
    reg(Richiesta("piemonte.oauth2.token", "http://127.0.0.1:9/t", b"", {}), Risposta(200, corpo.encode(), {}, 0.1), None)
    testo = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    assert "OPACO-SEGRETO" not in testo and "ALTRO-SEGRETO" not in testo and "Bearer" in testo



def test_registro_reda_token_opachi_lunghi_tutti_interi(tmp_path):
    """Un valore con >= 64 caratteri base64 e una coda diversa: la regola del base64 non deve
    lasciarne la coda in chiaro."""
    from varco.trasporto.http import Risposta

    reg = RegistratoreFile(tmp_path)
    lungo = "A" * 80 + "-CODA-SEGRETA-xyz"
    reg(Richiesta("piemonte.oauth2.token", "http://127.0.0.1:9/t", b"", {}),
        Risposta(200, json.dumps({"access_token": lungo}).encode(), {}, 0.1), None)
    testo = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    assert "CODA-SEGRETA" not in testo and "A" * 80 not in testo


def test_guardia_dell_url_di_autorizzazione_anche_con_un_trasporto_proprio():
    class TrasportoMio:
        def invia(self, richiesta):
            raise AssertionError("non doveva partire nulla")

    ades = AdesionePiemonte("R", GESTIONALE)
    client = ClientOAuth2Piemonte("https://rel.csi.it/reloauthserver", GESTIONALE, REDIRECT, adesione=ades,
                                  trasporto=TrasportoMio())
    with pytest.raises(AmbienteBloccato):
        client.autorizzazione()
    for chiamata in (client.jwks, lambda: client.verifica_sessione("x.y.z", TITOLARE),
                     lambda: client.revoca_sessione("x.y.z", TITOLARE)):
        with pytest.raises(AmbienteBloccato):
            chiamata()

    class TrasportoConNone(TrasportoMio):
        consenti_produzione = None
        consenti_collaudo_regionale = None
        collaudi_piemonte = None

    with pytest.raises(AmbienteBloccato):
        ClientOAuth2Piemonte("https://tst-rel-xxxx.csi.it/reloauthserver", GESTIONALE, REDIRECT, adesione=ades,
                             trasporto=TrasportoConNone()).jwks()


def test_expires_in_anche_come_stringa():
    from varco.trasporto.piemonte_oauth2 import _numero

    assert [_numero(v) for v in (7199, 7199.0, "7199", " 7199 ", True, "x", None)] == [7199, 7199, 7199, 7199, None, None, None]


def test_firma_malformata_e_un_errore_del_kit():
    token = (RISPOSTE / "jwt_valido.txt").read_text().strip()
    jwks = json.loads((RISPOSTE / "jwks.json").read_text())
    testa, corpo, _ = token.split(".")
    with pytest.raises(ConfigurazioneNonValida):
        verifica_firma(f"{testa}.{corpo}.a", jwks)


def test_callback_con_state_non_ascii_e_un_errore_del_kit():
    client = ClientOAuth2Piemonte("http://127.0.0.1:9/reloauthserver", GESTIONALE, REDIRECT)
    ra = client.autorizzazione()
    with pytest.raises(ErroreAutorizzazione, match="state"):
        client.leggi_callback(f"{REDIRECT}?code=ABC&state=%C3%A8", ra)


@pytest.mark.parametrize("cf", [None, "", "  "])
def test_prescrittore_senza_cf_e_un_rifiuto_del_kit(regione, cf):
    can = CanalePiemonte(ModalitaPiemonte.MAIL, credenziali=_cred(), id_sessione=lambda: UUID_PROVA,
                         gestionale_di_prova=GESTIONALE, base_url="http://127.0.0.1:9")
    for valida in (True, False):
        with pytest.raises(RicettaNonValida, match="mancante"):
            RicettaPiemonte(can, _cifratore(regione), valida_localmente=valida).invia(_ricetta(cf=cf))


# ------------------------------------------------------------------ registro redatto


def test_registro_reda_corpo_e_intestazioni_piemonte(tmp_path):
    reg = RegistratoreFile(tmp_path)
    token = _jwt()
    cifrato = CifratoreSanitel.incluso().cifra("1234567890")  # un cifrato vero: 172 caratteri
    busta = imbusta(_crea(cifra=lambda v: cifrato))
    intest = {"Authorization": "Basic cnVwYXI6cHc=", "X-idSessione": f"Bearer {UUID_PROVA}", "X-Gestionale": "VARCO_301",
              "X-OAuth2-Authorization": f"Bearer {token}"}
    risposta_test = (RISPOSTE / "crea_ok_test.xml").read_bytes()
    from varco.trasporto.http import Risposta

    reg(Richiesta("piemonte.id_sessione", "http://127.0.0.1:9/a2f", busta, intest), Risposta(200, risposta_test, {}, 0.1), None)
    corpo_token = f"grant_type=authorization_code&code=CODICE-SEGRETO&redirect_uri=x&client_id=VARCO_301&code_verifier={'v' * 43}"
    reg(Richiesta("piemonte.oauth2.token", f"http://127.0.0.1:9/t?client_id=VARCO_301&cfutente={TITOLARE}", corpo_token.encode(), {}),
        Risposta(200, json.dumps({"access_token": token, "token_type": "Bearer"}).encode(), {}, 0.1), None)
    testo = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    for chiaro in (TITOLARE, "rupar-tit", UUID_PROVA, token, token.split(".")[1], "CODICE-SEGRETO", "v" * 43, cifrato[:40]):
        assert chiaro not in testo, chiaro
    meta = json.loads(sorted(tmp_path.glob("*_meta.json"))[0].read_text(encoding="utf-8"))
    i = meta["intestazioni_richiesta"]
    assert i["X-idSessione"] == i["X-OAuth2-Authorization"] == i["Authorization"] == "***"
    assert i["X-Gestionale"] == "VARCO_301"  # il codice del software non è un dato personale
    assert "[REDATTO:id_sessione:" in testo and "[REDATTO:jwt:" in testo


# ------------------------------------------------------------------ giro completo contro il server finto


class TrasportoLocale(TrasportoHTTP):
    """Il trasporto vero (guardia, nessun redirect), senza attese: il server è su 127.0.0.1."""

    def __init__(self, **kw):
        super().__init__(intervallo_minimo_s=0.5, **kw)
        self.limitatore.intervallo = 0.0


UTENTI = {
    "rupar-tit": SERVER.UtenteRupar("pw-tit", "1234567890", TITOLARE),
    "rupar-sost": SERVER.UtenteRupar("pw-sost", "0987654321", SOSTITUTO),
}


@pytest.fixture
def server(regione):
    with SERVER.ServerPiemonte(chiave_cifratura_pem=regione.chiave_cifratura_pem, utenti=UTENTI,
                               gestionali={"VARCO_301": {"prescrizione"}, "SOLOEROG_301": {"erogazione"}},
                               redirect_uri={REDIRECT}, utente_oauth2=TITOLARE,
                               xsd_a2f=XSD_A2F if XSD_A2F.exists() else None) as s:
        yield s


class _Sessione:
    def __init__(self):
        self.valore = None

    def __call__(self):
        return self.valore


def _mail(server, regione, utente="rupar-tit", *, gestionale=GESTIONALE, registratore=None):
    u = UTENTI[utente]
    ids = _Sessione()
    can = CanalePiemonte(ModalitaPiemonte.MAIL, credenziali=Credenziali(utente, u.password, u.pincode, u.cf), id_sessione=ids,
                         gestionale_di_prova=gestionale, base_url=server.url, trasporto=TrasportoLocale(registratore=registratore))
    cif = _cifratore(regione)
    return RicettaPiemonte(can, cif), a2f.ServizioIdSessione(can, cif, a2f.UtenteA2F(u.cf, "301")), ids


def test_e2e_mail_crea_invia_visualizza_annulla_revoca(server, regione):
    s, gestione, ids = _mail(server, regione)
    with pytest.raises(ConfigurazioneNonValida, match="Id-Sessione"):
        s.invia(_ricetta())  # senza Id-Sessione non parte nemmeno
    e = gestione.crea()
    assert e.ok and e.ambiente_test and e.permessi == ("prescrizione",) and uuid.UUID(e.id_sessione_di_test)
    ids.valore = e.id_sessione_di_test
    inv = s.invia(_ricetta())
    assert inv.ok and inv.nre.startswith("0100A") and len(inv.codice_autenticazione) == 30
    r = {k.lower(): v for k, v in server.stato.richieste[-1]["intestazioni"].items()}  # urllib cambia le maiuscole
    assert r["x-idsessione"] == f"Bearer {ids.valore}" and r["x-gestionale"] == "VARCO_301"
    assert s.visualizza(inv.nre).stato_processo == "3"
    assert s.interroga_nre_utilizzati(CriteriNreUtilizzati("010", nre=inv.nre)).ricette[0].nre == inv.nre
    assert s.annulla(inv.nre).ok and s.annulla(inv.nre).errori[0].codice == "1120"
    assert not s.visualizza("0100A0000099999").ok
    assert gestione.verifica(ids.valore).stato_token.valido
    assert gestione.revoca(ids.valore).info_di("revokeStatus")
    assert gestione.verifica(ids.valore).stato_token.stato == 1
    gia = gestione.revoca(ids.valore)
    assert not gia.ok and gia.info_di("lastRevokePreviousDate")
    with pytest.raises(ErroreSOAP, match="revocato"):
        s.invia(_ricetta())


def test_e2e_mail_id_sessione_non_valido_scaduto_o_sostituito(server, regione):
    s, gestione, ids = _mail(server, regione)
    ids.valore = str(uuid.uuid4())  # a forma di UUID, ma non rilasciato
    with pytest.raises(ErroreSOAP, match="non rilasciato"):
        s.invia(_ricetta())
    primo = gestione.crea().id_sessione_di_test
    secondo = gestione.crea().id_sessione_di_test  # «ad ogni richiesta ... viene inibita la validità di quello precedente»
    ids.valore = primo
    with pytest.raises(ErroreSOAP, match="revocato"):
        s.visualizza("0100A0000000001")
    ids.valore = secondo
    server.spostamento_s = server.durata_s + 1
    assert gestione.verifica(secondo).stato_token.stato == 2
    with pytest.raises(ErroreSOAP, match="scaduto"):
        s.visualizza("0100A0000000001")
    server.spostamento_s = 0


def test_e2e_mail_errori_lato_server(server, regione):
    """I rifiuti che il kit non può prevedere li vede il server: credenziali, gestionale, pincode."""
    s, gestione, ids = _mail(server, regione)
    ids.valore = gestione.crea().id_sessione_di_test
    canale = s.canale
    canale.credenziali = Credenziali("rupar-tit", "password-errata", "1234567890", TITOLARE)
    with pytest.raises(ErroreSOAP, match="Credenziali invalide"):
        s.invia(_ricetta())
    canale.credenziali = Credenziali("rupar-tit", "pw-tit", "1111111111", TITOLARE)
    with pytest.raises(ErroreSOAP, match="Pincode"):
        s.invia(_ricetta())
    # pincode cifrato col certificato sbagliato (SanitelCF invece di quello della Regione)
    canale.credenziali = Credenziali("rupar-tit", "pw-tit", "1234567890", TITOLARE)
    with pytest.raises(ErroreSOAP, match="Pincode"):
        RicettaPiemonte(canale, CifratoreSanitel.incluso()).invia(_ricetta())
    # gestionale non censito: l'Id-Sessione non si ottiene
    _, gestione_ignota, _ = _mail(server, regione, gestionale=GestionalePiemonte("SCONOSCIUTO", "301"))
    e = gestione_ignota.crea()
    assert not e.ok and e.id_sessione_di_test is None and e.errori[0].bloccante
    _, gestione_erog, _ = _mail(server, regione, gestionale=GestionalePiemonte("SOLOEROG", "301"))
    assert not gestione_erog.crea().ok  # censito, ma senza diritto di prescrizione (A2F-MAIL-IDSES-N-03)


def test_e2e_mail_header_mancanti_visti_dal_server(server, regione):
    """Ciò che il canale non manderebbe mai (niente X-Gestionale, niente X-idSessione) il server lo rifiuta:
    lo si prova con richieste costruite a mano sul trasporto vero."""
    s, gestione, ids = _mail(server, regione)
    ids.valore = gestione.crea().id_sessione_di_test
    corpo = imbusta(rp.richiesta("visualizza", ModalitaPiemonte.MAIL, _cifratore(regione).cifra, nre="0100A0000000001",
                                 cf_medico=TITOLARE, pincode="1234567890"))
    url = server.url + PERCORSI_LOCALI[ServizioPiemonte.VISUALIZZA]
    h = s.canale.intestazioni(ServizioPiemonte.VISUALIZZA, SOAP_ACTION[ServizioPiemonte.VISUALIZZA])
    t = TrasportoLocale()
    for togli, messaggio in (("X-Gestionale", "gestionale non indicato"), ("X-idSessione", "richiede l'invio dell'Id-Sessione")):
        r = t.invia(Richiesta("x", url, corpo, {k: v for k, v in h.items() if k != togli}))
        with pytest.raises(ErroreSOAP, match=messaggio):
            sbusta(r.corpo, r.stato_http)
    # Basic insieme al JWT: vietato (par. 4.3.6)
    r = t.invia(Richiesta("x", url, corpo, h | {"X-OAuth2-Authorization": f"Bearer {server.jwt_di_prova()}"}))
    with pytest.raises(ErroreSOAP, match="basic"):
        sbusta(r.corpo, r.stato_http)


def test_e2e_sostituto_e_controllo_del_prescrittore(server, regione):
    tit, gest_tit, ids_tit = _mail(server, regione, "rupar-tit")
    sost, gest_sost, ids_sost = _mail(server, regione, "rupar-sost")
    ids_tit.valore = gest_tit.crea().id_sessione_di_test
    ids_sost.valore = gest_sost.crea().id_sessione_di_test
    con_sostituto = _ricetta(sostituto=SOSTITUTO)
    with pytest.raises(RicettaNonValida, match="sostituto"):
        tit.invia(con_sostituto)  # il kit lo ferma: invia il sostituto, con le sue credenziali
    e = sost.invia(con_sostituto)
    assert e.ok and server.stato.ricette[e.nre].sostituto == SOSTITUTO
    assert tit.annulla(e.nre).errori[0].codice == "1125" and sost.annulla(e.nre, cf_medico=TITOLARE).ok
    # in produzione SIRPED controlla che il prescrittore sia l'utente autenticato: il server finto lo imita
    tit.valida_localmente = False  # il controllo d'identità resta anche senza la validazione del tracciato
    with pytest.raises(RicettaNonValida, match="sostituto"):
        tit.invia(con_sostituto)
    # la stessa richiesta, costruita a mano e mandata col trasporto vero, la rifiuta il server finto
    corpo = imbusta(rp.richiesta("invio", ModalitaPiemonte.MAIL, _cifratore(regione).cifra, ricetta=con_sostituto,
                                 pincode="1234567890"))
    h = tit.canale.intestazioni(ServizioPiemonte.INVIO, SOAP_ACTION[ServizioPiemonte.INVIO])
    r = TrasportoLocale().invia(Richiesta("x", server.url + PERCORSI_LOCALI[ServizioPiemonte.INVIO], corpo, h))
    from varco.ricetta import xml_sac

    server.controllo_cf_prescrittore = False
    assert xml_sac.leggi_ricevuta_invio(sbusta(r.corpo, r.stato_http)).ok  # in TEST il controllo è spento
    server.controllo_cf_prescrittore = True
    r = TrasportoLocale().invia(Richiesta("x", server.url + PERCORSI_LOCALI[ServizioPiemonte.INVIO], corpo, h))
    rifiuto = xml_sac.leggi_ricevuta_invio(sbusta(r.corpo, r.stato_http))
    assert not rifiuto.ok and rifiuto.errori[0].codice == "1212"


def _autorizza(server, client, scope=("prescrizione",)):
    """Il «browser» del medico: apre l'URL di autorizzazione e prende il redirect di ritorno."""
    ra = client.autorizzazione(scope)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a):
            return None

    try:
        urllib.request.build_opener(_NoRedirect).open(ra.url, timeout=10)
    except urllib.error.HTTPError as e:
        assert e.code == 302
        return ra, e.headers["Location"]
    raise AssertionError("atteso un redirect verso la redirect_uri")


def test_e2e_oauth2_token_prescrizione_verifica_revoca(server, regione):
    client = ClientOAuth2Piemonte(server.url_oauth, GESTIONALE, REDIRECT, trasporto=TrasportoLocale())
    ra, ritorno = _autorizza(server, client)
    token = client.scambia_codice(client.leggi_callback(ritorno, ra), ra)
    assert token.scope == ("prescrizione",) and token.token_type == "Bearer"
    c = verifica_firma(token.access_token, client.jwks())
    assert c.cf_utente == TITOLARE and c.payload["aud"] == "VARCO_301" and c.id_sessione
    with pytest.raises(ErroreAutorizzazione, match="invalid_grant"):
        client.scambia_codice(client.leggi_callback(ritorno, ra), ra)  # il code vale una volta sola
    can = CanalePiemonte(ModalitaPiemonte.OAUTH2, token_jwt=lambda: token.access_token, cf_medico=TITOLARE,
                         gestionale_di_prova=GESTIONALE, base_url=server.url, trasporto=TrasportoLocale())
    s = RicettaPiemonte(can, _cifratore(regione))
    e = s.invia(_ricetta())
    assert e.ok
    h = {k.lower(): v for k, v in server.stato.richieste[-1]["intestazioni"].items()}
    assert "authorization" not in h and h["x-oauth2-authorization"] == f"Bearer {token.access_token}"
    assert client.verifica_sessione(token.access_token, TITOLARE).valido
    assert client.revoca_sessione(token.access_token, TITOLARE) == 200
    assert client.verifica_sessione(token.access_token, TITOLARE).stato == 1
    assert client.revoca_sessione(token.access_token, TITOLARE) == 401
    with pytest.raises(ErroreSOAP, match="revocato"):
        s.annulla(e.nre)


def test_e2e_oauth2_pkce_state_e_negazioni(server):
    client = ClientOAuth2Piemonte(server.url_oauth, GESTIONALE, REDIRECT, trasporto=TrasportoLocale())
    ra, ritorno = _autorizza(server, client)
    sbagliata = dataclasses.replace(ra, code_verifier=nuovo_verifier())  # un altro verifier: PKCE non torna
    with pytest.raises(ErroreAutorizzazione, match="invalid_client"):
        client.scambia_codice(client.leggi_callback(ritorno, ra), sbagliata)
    ignoto = ClientOAuth2Piemonte(server.url_oauth, GestionalePiemonte("SCONOSCIUTO", "301"), REDIRECT, trasporto=TrasportoLocale())
    ra, ritorno = _autorizza(server, ignoto)
    with pytest.raises(ErroreAutorizzazione, match="invalid_client"):
        ignoto.leggi_callback(ritorno, ra)
    server.utente_oauth2 = None  # il medico non ha le abilitazioni sul configuratore
    ra, ritorno = _autorizza(server, client)
    with pytest.raises(ErroreAutorizzazione, match="access_denied"):
        client.leggi_callback(ritorno, ra)


def test_e2e_oauth2_revoca_con_get_e_scadenza(server):
    client = ClientOAuth2Piemonte(server.url_oauth, GESTIONALE, REDIRECT, trasporto=TrasportoLocale())
    ra, ritorno = _autorizza(server, client)
    token = client.scambia_codice(client.leggi_callback(ritorno, ra), ra)
    assert client.revoca_sessione(token.access_token, TITOLARE, metodo="GET") == 200  # come l'esempio e il YAML
    with pytest.raises(ConfigurazioneNonValida):
        client.revoca_sessione(token.access_token, TITOLARE, metodo="POST")
    ra, ritorno = _autorizza(server, client)
    token = client.scambia_codice(client.leggi_callback(ritorno, ra), ra)
    server.spostamento_s = server.durata_s + 1
    assert client.verifica_sessione(token.access_token, TITOLARE).stato == 2
    server.spostamento_s = 0
    assert client.verifica_sessione(token.access_token, SOSTITUTO).stato_http == 401
    altro = ClientOAuth2Piemonte(server.url_oauth, GestionalePiemonte("SOLOEROG", "301"), REDIRECT, trasporto=TrasportoLocale())
    info = altro.verifica_sessione(token.access_token, TITOLARE)
    assert info.stato_http == 500 and info.errore["descrEsito"]


def test_e2e_registro_redatto(server, regione, tmp_path):
    s, gestione, ids = _mail(server, regione, registratore=RegistratoreFile(tmp_path))
    e = gestione.crea()
    ids.valore = e.id_sessione_di_test
    inv = s.invia(_ricetta())
    testo = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    for chiaro in (TITOLARE, ids.valore, inv.nre, inv.codice_autenticazione, "rupar-tit"):
        assert chiaro not in testo, chiaro


# ------------------------------------------------------------------ suite di conformità


def test_casi_piemonte_citano_file_che_esistono():
    from varco.conformita.motore import carica_casi

    casi = carica_casi("piemonte")
    assert len(casi) >= 41
    for caso in casi:
        for passo in caso["passi"]:
            for chiave in ("risposta", "token", "jwks"):
                if chiave in passo:
                    assert (RADICE / "conformita" / "risposte" / passo[chiave]).exists(), caso["id"]


def test_suite_piemonte_senza_xsd_a2f_salta_e_non_boccia(capsys, monkeypatch):
    from varco.conformita.esegui import main

    monkeypatch.delenv("VARCO_XSD_A2F", raising=False)
    assert main(["--famiglia", "piemonte"]) == 0
    out = capsys.readouterr().out
    assert "SALTATO   PIE-110" in out and "SUPERATO  PIE-101" in out and "SUPERATO  PIE-113" in out
    assert "falliti 0" in out and "errori 0" in out


@richiede_xsd_a2f
def test_suite_piemonte_con_xsd_tutto_verde(capsys):
    from varco.conformita.esegui import main
    from varco.conformita.motore import carica_casi

    assert main(["--famiglia", "piemonte", "--xsd-a2f", str(XSD_A2F)]) == 0
    assert f"superati {len(carica_casi('piemonte'))}" in capsys.readouterr().out


@pytest.mark.parametrize(
    "caso_id,cambia",
    [
        ("PIE-001", lambda p: p["atteso"]["campi"].update(id_sessione_di_test="altro")),
        ("PIE-002", lambda p: p["atteso"]["campi"].update(id_sessione_di_test="3f2504e0-4f89-41d3-9a0c-0305e82c3301")),
        ("PIE-004", lambda p: p["atteso"].update(ok=True)),
        ("PIE-005", lambda p: p["atteso"].update(errore_bloccante=True)),
        ("PIE-008", lambda p: p["atteso"]["campi"].update(stato_token=0)),
        ("PIE-101", lambda p: p["atteso"]["tag"].update(pinCode="")),
        ("PIE-102", lambda p: p["atteso"]["tag"].update(pinCode="CIFRATO==")),
        ("PIE-102", lambda p: p.update(modalita="mail")),
        ("PIE-110", lambda p: p["atteso"]["testi"].update({"infoAggiuntive/opzione/valore": "ALTRO_301"})),
        ("PIE-110", lambda p: p["atteso"].update(tag_assenti=["contesto"])),
        ("PIE-113", lambda p: p.update(permessi=["prescrizione"])),  # niente più rifiuto locale
        ("PIE-201", lambda p: p["atteso"]["intestazioni"].update({"X-Gestionale": "VARCO_302"})),
        ("PIE-202", lambda p: p["atteso"].update(assenti=["X-OAuth2-Authorization"])),
        # dal 02/10/2026 il canale vuole anche lo scope «prescrizione»: lo si aggiunge, così l'unica
        # differenza resta exp (il caso PIE-205 andrebbe aggiornato allo stesso modo, vedi la consegna)
        ("PIE-205", lambda p: p["jwt_payload"].update(exp=4102444800, scope="prescrizione")),
        ("PIE-301", lambda p: p["atteso"]["campi"].update(code_challenge="E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw_cM")),
        ("PIE-401", lambda p: p["atteso"]["campi"].update(firma_valida=False)),
        ("PIE-402", lambda p: p["atteso"]["campi"].update(firma_valida=True)),
    ],
)
def test_il_motore_piemonte_boccia_aspettative_sbagliate(caso_id, cambia):
    """Gruppo di controllo: ogni caso, con un dettaglio cambiato, deve risultare FALLITO."""
    from varco.conformita.motore import Motore

    caso = json.loads((RADICE / "conformita" / "casi" / f"{caso_id}.json").read_text(encoding="utf-8"))
    cambia(caso["passi"][0])
    assert Motore(xsd_a2f=XSD_A2F if XSD_A2F.exists() else None).esegui(caso).stato == "FALLITO"


@pytest.mark.parametrize(
    "guasto",
    [
        lambda c: c["passi"][0].update(risposta="crea_ok_test.xml"),  # le risposte stanno in risposte/piemonte/
        lambda c: c.update(id="PIE-01"),
        lambda c: c["passi"][0].update(atteso={"codice": ["00000"]}),
        lambda c: c["passi"][0].update(inventato=True),
    ],
)
def test_lo_schema_boccia_casi_piemonte_guasti(guasto):
    jsonschema = pytest.importorskip("jsonschema")
    from referencing import Registry, Resource

    schemi = RADICE / "conformita" / "schema"
    risorse = [(f"{n}.schema.json", Resource.from_contents(json.loads((schemi / f"{n}.schema.json").read_text(encoding="utf-8"))))
               for n in ("ricetta", "pss")]
    validatore = jsonschema.Draft202012Validator(json.loads((schemi / "caso.schema.json").read_text(encoding="utf-8")),
                                                 registry=Registry().with_resources(risorse))
    caso = json.loads((RADICE / "conformita" / "casi" / "PIE-001.json").read_text(encoding="utf-8"))
    assert list(validatore.iter_errors(caso)) == []
    guasto(caso)
    assert list(validatore.iter_errors(caso))


def test_niente_url_di_sirped_inventati_nel_codice():
    """Gli host di SIRPED non sono pubblicati: nel codice non ce n'è nessuno (l'unico nome è l'esempio con
    il segnaposto, usato solo nei test)."""
    sorgenti = [RADICE / "src" / "varco" / "trasporto" / f for f in ("piemonte.py", "piemonte_a2f.py", "piemonte_oauth2.py")]
    sorgenti += [RADICE / "src" / "varco" / "ricetta" / "piemonte.py"]
    import re

    for f in sorgenti:
        for url in re.findall(r"https://[^\s\"'<>)]+", f.read_text(encoding="utf-8")):
            assert not e_regione_piemonte(url) or "xxxx" in url, f"{f.name}: {url}"
    assert os.path.basename(__file__) == "test_piemonte.py"


# ------------------------------------------------------------------ revisione esterna 02/10/2026 (5-sar-piemonte.md)
# Ogni test riproduce un controesempio del revisore. Fallivano sul codice di prima (client e server
# finto): verificato su una copia, vedi la consegna. Il punto 1 (guardia SOAP con trasporto proprio)
# è corretto in trasporto/http.py::consegna; il punto 2 (motore di conformità) altrove.


def _jwt_senza_exp(**payload) -> str:
    from varco.conformita.piemonte import jwt_da_payload

    return jwt_da_payload({"sub": TITOLARE, "scope": "prescrizione",
                           "userData": {"cfutente": TITOLARE, "idSessione": UUID_PROVA}} | payload)


def test_rev3_scadenza_da_expires_in_arriva_al_canale():
    """Punto 3: JWT senza exp, scadenza solo in expires_in: il canale riceveva la stringa e partiva."""
    import time as _time

    from varco.trasporto.piemonte_oauth2 import TokenPiemonte

    loc = dict(gestionale_di_prova=GESTIONALE, base_url="http://127.0.0.1:9")
    scaduto = TokenPiemonte(_jwt_senza_exp(), "Bearer", ("prescrizione",), "VARCO_301", -3600, _time.time())
    assert scaduto.scadenza < _time.time()
    for token_jwt in (lambda: scaduto, lambda: scaduto.access_token):  # oggetto: scaduto; stringa: scadenza ignota
        can = CanalePiemonte(ModalitaPiemonte.OAUTH2, token_jwt=token_jwt, cf_medico=TITOLARE, **loc)
        with pytest.raises(ConfigurazioneNonValida, match="scaduto|senza exp"):
            can.intestazioni(ServizioPiemonte.INVIO, "x")
    for exp in ("1", None, True):  # exp presente ma non numerico: non vuol dire «mai scaduto»
        can = CanalePiemonte(ModalitaPiemonte.OAUTH2, token_jwt=lambda e=exp: _jwt(exp=e), cf_medico=TITOLARE, **loc)
        with pytest.raises(ConfigurazioneNonValida, match="exp"):
            can.intestazioni(ServizioPiemonte.INVIO, "x")
    # gruppo di controllo: lo stesso JWT senza exp, con expires_in ancora valido, parte
    valido = dataclasses.replace(scaduto, expires_in=3600)
    can = CanalePiemonte(ModalitaPiemonte.OAUTH2, token_jwt=lambda: valido, cf_medico=TITOLARE, **loc)
    assert can.intestazioni(ServizioPiemonte.INVIO, "x")["X-OAuth2-Authorization"] == f"Bearer {valido.access_token}"
    # e la scadenza è la più vicina tra exp ed expires_in
    corto = TokenPiemonte(_jwt(), "Bearer", ("prescrizione",), "VARCO_301", 10, _time.time() - 20)
    assert corto.scadenza < _time.time()


def _utente_solo_erogazione(server) -> str:
    cf = "PROVAX00X00X000W"
    server.utenti["rupar-erog"] = SERVER.UtenteRupar("pw-erog", "1111111111", cf, profili=frozenset({"erogazione"}))
    server.utente_oauth2 = cf
    return cf


def test_rev4_scope_concessi_solo_entro_i_profili(server, regione):
    """Punto 4: utente e gestionale solo «erogazione», scope «prescrizione»: il server lo concedeva."""
    _utente_solo_erogazione(server)
    erog = ClientOAuth2Piemonte(server.url_oauth, GestionalePiemonte("SOLOEROG", "301"), REDIRECT, trasporto=TrasportoLocale())
    ra, ritorno = _autorizza(server, erog, ("prescrizione",))
    with pytest.raises(ErroreAutorizzazione, match="access_denied"):
        erog.leggi_callback(ritorno, ra)
    # chiesti due permessi, ne ha uno: il token porta solo quello, e la prescrizione non parte
    ra, ritorno = _autorizza(server, erog, ("prescrizione", "erogazione"))
    token = erog.scambia_codice(erog.leggi_callback(ritorno, ra), ra)
    assert token.scope == ("erogazione",) and leggi_jwt(token.access_token).scope == ("erogazione",)
    cf = server.utente_oauth2
    can = CanalePiemonte(ModalitaPiemonte.OAUTH2, token_jwt=lambda: token, cf_medico=cf,
                         gestionale_di_prova=GestionalePiemonte("SOLOEROG", "301"), base_url=server.url, trasporto=TrasportoLocale())
    with pytest.raises(ConfigurazioneNonValida, match="prescrizione"):
        RicettaPiemonte(can, _cifratore(regione)).visualizza("0100A0000000001")
    # mandata lo stesso, a mano: il server la rifiuta
    corpo = imbusta(rp.richiesta("visualizza", ModalitaPiemonte.OAUTH2, _cifratore(regione).cifra, nre="0100A0000000001",
                                 cf_medico=cf))
    h = {"Content-Type": "text/xml;charset=UTF-8", "SOAPAction": f'"{SOAP_ACTION[ServizioPiemonte.VISUALIZZA]}"',
         "X-OAuth2-Authorization": f"Bearer {token.access_token}"}
    r = TrasportoLocale().invia(Richiesta("x", server.url + PERCORSI_LOCALI[ServizioPiemonte.VISUALIZZA], corpo, h))
    with pytest.raises(ErroreSOAP, match="prescrizione"):
        sbusta(r.corpo, r.stato_http)


def test_rev5_il_code_resta_di_chi_ha_autorizzato(server):
    """Punto 5: A autorizza, il server passa a B, il code di A dava un token a B."""
    client = ClientOAuth2Piemonte(server.url_oauth, GESTIONALE, REDIRECT, trasporto=TrasportoLocale())
    server.utente_oauth2 = TITOLARE
    ra, ritorno = _autorizza(server, client)
    server.utente_oauth2 = SOSTITUTO
    token = client.scambia_codice(client.leggi_callback(ritorno, ra), ra)
    c = leggi_jwt(token.access_token)
    assert c.sub == TITOLARE and c.cf_utente == TITOLARE
    assert server.stato.sessioni[c.id_sessione].utente == TITOLARE


def test_rev6_check_e_revoke_legati_al_gestionale(server, regione):
    """Punto 6: un altro gestionale dello stesso utente verificava e revocava l'Id-Sessione."""
    s, gestione, ids = _mail(server, regione)
    ids.valore = gestione.crea().id_sessione_di_test
    server.gestionali["ALTRO_301"] = {"prescrizione"}
    _, altra_gestione, _ = _mail(server, regione, gestionale=GestionalePiemonte("ALTRO", "301"))
    v = altra_gestione.verifica(ids.valore)
    assert not v.ok and v.stato_token is None
    r = altra_gestione.revoca(ids.valore)
    assert not r.ok
    assert server.stato.sessioni[ids.valore].revocato_alle is None
    assert gestione.verifica(ids.valore).stato_token.valido  # per il suo gestionale resta valido
    assert s.invia(_ricetta()).ok


def test_rev7_revoca_con_jwt_scaduto_401(server):
    """Punto 7: JWT firmato ma scaduto, Id-Sessione valido: la revoca rispondeva 200 (p. 36: 401)."""
    client = ClientOAuth2Piemonte(server.url_oauth, GESTIONALE, REDIRECT, trasporto=TrasportoLocale())
    ra, ritorno = _autorizza(server, client)
    token = client.scambia_codice(client.leggi_callback(ritorno, ra), ra)
    scaduto = server._firma(leggi_jwt(token.access_token).payload | {"exp": 1})
    assert client.revoca_sessione(scaduto, TITOLARE) == 401
    sess = server.stato.sessioni[leggi_jwt(token.access_token).id_sessione]
    assert sess.revocato_alle is None
    assert client.verifica_sessione(scaduto, TITOLARE).stato == 0  # verify: 401 solo se il jwt non è riconosciuto (p. 33)
    assert client.revoca_sessione(token.access_token, TITOLARE) == 200  # gruppo di controllo


class _TrasportoToken:
    """Authorization Server finto in memoria: dà il JWT che gli si chiede e il JWKS della sua chiave."""

    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import rsa

        self.srv = object.__new__(SERVER.ServerPiemonte)  # solo per firmare: niente socket
        self.srv._chiave_jwt = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.srv.kid = "rel-oauth2-key"
        self.token = ""

    def invia(self, richiesta):
        from varco.trasporto import Risposta

        if richiesta.url.endswith("/.well-known/jwks.json"):
            return Risposta(200, json.dumps(self.srv.jwks()).encode(), {}, 0.0)
        dati = {"access_token": self.token, "token_type": "Bearer", "scope": "prescrizione", "expires_in": 7199}
        return Risposta(200, json.dumps(dati).encode(), {}, 0.0)


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def test_rev8_scambia_codice_verifica_la_firma():
    """Punto 8: scambia_codice accettava un JWT con alg «none»; la verifica era una funzione a parte."""
    import hmac

    t = _TrasportoToken()
    client = ClientOAuth2Piemonte("http://127.0.0.1:9/reloauthserver", GESTIONALE, REDIRECT, trasporto=t)
    ra = client.autorizzazione()
    payload = {"sub": TITOLARE, "aud": "VARCO_301", "exp": 4102444800, "scope": "prescrizione",
               "userData": {"cfutente": TITOLARE, "idSessione": UUID_PROVA}}
    testa_hs = _b64({"alg": "HS256", "typ": "JWT"})
    firma_hs = base64.urlsafe_b64encode(hmac.new(b"segreto", f"{testa_hs}.{_b64(payload)}".encode(), hashlib.sha256).digest())
    falsi = {
        "none": f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(payload)}.",
        "HS256": f"{testa_hs}.{_b64(payload)}.{firma_hs.rstrip(b'=').decode()}",
        "firma altrui": t.srv._firma(payload).rsplit(".", 1)[0] + "." + _TrasportoToken().srv._firma(payload).rsplit(".", 1)[1],
        "aud di un altro": t.srv._firma(payload | {"aud": "ALTRO_301"}),
        "exp non numerico": t.srv._firma(payload | {"exp": "1"}),
    }
    for nome, falso in falsi.items():
        t.token = falso
        with pytest.raises(ErroreAutorizzazione, match="jwt_non_valido"):
            client.scambia_codice("code", ra)
    t.token = t.srv._firma(payload)  # gruppo di controllo
    assert client.scambia_codice("code", ra).contenuto.cf_utente == TITOLARE
    assert client.scambia_codice("code", ra, jwks=t.srv.jwks()).scope == ("prescrizione",)


def test_rev8_documentazione_dice_dove_si_verifica_la_firma():
    doc = (RADICE / "docs" / "SAR_PIEMONTE.md").read_text(encoding="utf-8")
    assert "`scambia_codice` verifica la firma" in doc
