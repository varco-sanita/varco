# SPDX-License-Identifier: EUPL-1.2
"""SAR della Regione Umbria (PuntoZero): guardia, JWT, codec JSON, server finto severo, registro.

Nessuna chiamata ai sistemi umbri: tutto gira contro strumenti/umbria_server_finto.py su 127.0.0.1,
con certificati di PROVA generati qui. Fonti: specifiche/umbria (strumenti/scarica_specifiche.py
--gruppi umbria): wiki «Home» e «Prescrittori», OpenAPI sar-open-api-prescrittore.yaml.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

import pytest

from varco import AmbienteBloccato, ConfigurazioneNonValida
from varco.ambienti import (
    HOST_UMBRIA_PRODUZIONE,
    HOST_UMBRIA_TEST,
    e_collaudo_regionale,
    e_collaudo_umbria,
    e_produzione,
    verifica_url_consentito,
)
from varco.errori import ErroreTrasporto, RicettaNonValida
from varco.ricetta import (
    Assistito,
    ClassePriorita,
    CriteriNreUtilizzati,
    LottoNRE,
    Prescrittore,
    Ricetta,
    RicettaUmbria,
    Riga,
    TipoPrescrizione,
)
from varco.ricetta import json_umbria
from varco.trasporto import RegistratoreFile, TrasportoHTTP
from varco.trasporto.http import Risposta
from varco.trasporto.umbria import (
    AdesioneUmbria,
    ApplicativoUmbria,
    CanaleUmbria,
    ErroreServizioUmbria,
    FirmatarioJWTPKCS12,
    InvioIncertoUmbria,
    ServizioUmbria,
    cx_cf,
)

RADICE = Path(__file__).resolve().parents[2]
SPEC = RADICE / "specifiche" / "umbria"
OPENAPI = SPEC / "openapi" / "sar-open-api-prescrittore.yaml"
TITOLARE, SOSTITUTO, ASSISTITO = "PROVAX00X00X000Y", "PROVAX00X00X000Z", "PNIMRA70A01H501P"
APPLICATIVO = ApplicativoUmbria("VARCO", "Varco (bene comune)", "0.1")


def _modulo(nome: str):
    spec = importlib.util.spec_from_file_location(nome, RADICE / "strumenti" / f"{nome}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(nome, mod)
    spec.loader.exec_module(mod)
    return mod


SERVER = _modulo("umbria_server_finto")


@pytest.fixture(scope="module")
def materiale(tmp_path_factory):
    return SERVER.materiale_umbria(tmp_path_factory.mktemp("umbria-di-prova"))


@pytest.fixture
def server(materiale):
    with SERVER.ServerUmbria(materiale) as s:
        yield s


class TrasportoLocale(TrasportoHTTP):
    """Il trasporto vero (guardia, HTTPS, nessun redirect, mTLS), senza attese: il server è su 127.0.0.1."""

    def __init__(self, materiale, con_certificato: bool = True, **kw):
        super().__init__(intervallo_minimo_s=0.5, contesto_tls=materiale.contesto_client(con_certificato), **kw)
        self.limitatore.intervallo = 0.0


def _firmatario(materiale, p12=None, algoritmo="RS256"):
    return FirmatarioJWTPKCS12(str(p12 or materiale.firma_p12), b"pw", algoritmo)


def _canale(server, materiale, cf=TITOLARE, *, trasporto=None, **kw) -> CanaleUmbria:
    return CanaleUmbria(cf, kw.pop("firmatario", None) or _firmatario(materiale), azienda=kw.pop("azienda", "100201"),
                        base_url=server.url, applicativo_di_prova=APPLICATIVO,
                        trasporto=trasporto or TrasportoLocale(materiale), **kw)


def _servizio(server, materiale, cf=TITOLARE, **kw) -> RicettaUmbria:
    return RicettaUmbria(_canale(server, materiale, cf, **kw))


def _farm(nre: str | None, **kw) -> Ricetta:
    base = dict(prescrittore=Prescrittore(TITOLARE, "100", "201", "F"), assistito=Assistito(ASSISTITO),
                tipo=TipoPrescrizione.FARMACEUTICA,
                righe=(Riga(1, codice_gruppo_equivalenza="CJA", descrizione_gruppo_equivalenza="GRUPPO DI PROVA"),),
                nre=nre)
    base.update(kw)
    return Ricetta(**base)


def _spec(nre: str | None, **kw) -> Ricetta:
    base = dict(prescrittore=Prescrittore(TITOLARE, "100", "201", "F"), assistito=Assistito(ASSISTITO),
                tipo=TipoPrescrizione.SPECIALISTICA, classe_priorita=ClassePriorita.PROGRAMMATA,
                codice_diagnosi="4254", descrizione_diagnosi="CONTROLLO",
                righe=(Riga(1, codice="89.7", descrizione="VISITA DI PROVA", codice_catalogo="897", tipo_accesso="0"),),
                nre=nre)
    base.update(kw)
    return Ricetta(**base)


def _lotto(s: RicettaUmbria, tipo="1") -> LottoNRE:
    e = s.richiedi_lotto_nre(tipo)
    assert e.ok, e
    return e.lotto


def _payload(jwt: str) -> tuple[dict, dict]:
    t, p, _ = jwt.split(".")
    dec = lambda x: json.loads(base64.urlsafe_b64decode(x + "=" * (-len(x) % 4)))  # noqa: E731
    return dec(t), dec(p)


# ------------------------------------------------------------------ guardia


@pytest.mark.parametrize("url,produzione,collaudo", [
    (f"https://{HOST_UMBRIA_TEST}/sar/v1/servizi-prescrittore/dem-invio-prescritto", False, True),
    (f"https://{HOST_UMBRIA_TEST.upper()}./sar", False, True),
    (f"https://{HOST_UMBRIA_PRODUZIONE}/sar", True, False),
    ("https://fse.regione.umbria.it/x", True, False),
    ("https://www.puntozeroscarl.it/", True, False),
    ("https://api-salute-test.regione.umbria.it.example.org/", False, False),
    ("https://umbria.it.example/", False, False),
])
def test_host_umbri_riconosciuti(url, produzione, collaudo):
    assert e_produzione(url) is produzione
    assert e_collaudo_umbria(url) is collaudo
    assert e_collaudo_regionale(url) is collaudo


def test_collaudo_umbro_solo_col_flag_e_la_produzione_mai_col_flag_del_collaudo():
    test = f"https://{HOST_UMBRIA_TEST}/sar/v1/x"
    with pytest.raises(AmbienteBloccato, match="Umbria"):
        verifica_url_consentito(test)
    verifica_url_consentito(test, consenti_collaudo_regionale=True)
    with pytest.raises(AmbienteBloccato, match="PRODUZIONE"):
        verifica_url_consentito(f"https://{HOST_UMBRIA_PRODUZIONE}/sar", consenti_collaudo_regionale=True)


def test_canale_verso_la_regione_vuole_un_adesione(materiale):
    with pytest.raises(ConfigurazioneNonValida, match="AdesioneUmbria"):
        CanaleUmbria(TITOLARE, _firmatario(materiale), azienda="100201")
    with pytest.raises(ConfigurazioneNonValida, match="localhost"):
        CanaleUmbria(TITOLARE, _firmatario(materiale), azienda="100201", base_url=f"https://{HOST_UMBRIA_PRODUZIONE}/sar")
    with pytest.raises(ConfigurazioneNonValida, match="applicativo_di_prova"):
        CanaleUmbria(TITOLARE, _firmatario(materiale), azienda="100201", adesione=AdesioneUmbria("ADES-PROVA", APPLICATIVO),
                     applicativo_di_prova=APPLICATIVO)


class _Cattura:
    """Un trasporto proprio col flag del collaudo: conta gli inoltri (non deve inoltrare nulla senza flag)."""

    def __init__(self, flag: bool):
        self.consenti_collaudo_regionale = flag
        self.inoltri = []

    def invia(self, r):
        self.inoltri.append(r.url)
        return Risposta(200, b'{"codEsito":"00","codRegione":"100","codRagLotto":"0A","identificativoLotto":"1","codLotto":"000001"}', {}, 0)


def test_con_adesione_senza_flag_la_guardia_ferma_prima_della_rete(materiale):
    t = _Cattura(flag=False)
    can = CanaleUmbria(TITOLARE, _firmatario(materiale), azienda="100201",
                       adesione=AdesioneUmbria("ADES-PROVA", APPLICATIVO), trasporto=t)
    with pytest.raises(AmbienteBloccato):
        RicettaUmbria(can).richiedi_lotto_nre()
    assert t.inoltri == []
    # gruppo di controllo: con adesione E flag la richiesta parte (verso il trasporto finto, non la rete)
    t2 = _Cattura(flag=True)
    can2 = CanaleUmbria(TITOLARE, _firmatario(materiale), azienda="100201",
                        adesione=AdesioneUmbria("ADES-PROVA", APPLICATIVO), trasporto=t2)
    assert RicettaUmbria(can2).richiedi_lotto_nre().ok
    assert t2.inoltri == [f"https://{HOST_UMBRIA_TEST}/sar/v1/servizi-prescrittore/richiesta-lotto-nre"]


def test_trasporto_vero_blocca_gli_host_umbri_prima_della_rete(monkeypatch, materiale):
    def vietato(*a, **k):
        raise AssertionError("nessuna chiamata di rete doveva partire")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", vietato)
    for host in (HOST_UMBRIA_TEST, HOST_UMBRIA_PRODUZIONE):
        with pytest.raises(AmbienteBloccato):
            TrasportoHTTP().invia(__import__("varco.trasporto.http", fromlist=["Richiesta"]).Richiesta(
                "umbria.prova", f"https://{host}/sar/v1/servizi-prescrittore/richiesta-lotto-nre", b"{}"))


# ------------------------------------------------------------------ JWT


def test_jwt_come_la_wiki(server, materiale):
    can = _canale(server, materiale)
    testa, auth = _payload(can.token_autenticazione())
    assert testa["alg"] == "RS256" and testa["typ"] == "JWT" and len(testa["x5c"]) == 1
    assert auth["iss"] == f"auth:{materiale.cn_firma}" and auth["sub"] == cx_cf(TITOLARE)
    assert auth["sub"] == "PROVAX00X00X000Y^^^&2.16.840.1.113883.2.9.4.3.2&ISO"
    assert auth["aud"] == server.audience and not auth["aud"].endswith("/sar")  # come gli esempi della wiki
    assert auth["exp"] - auth["iat"] == 300 and auth["jti"]
    _, firma = _payload(can.token_firma(ServizioUmbria.INVIO, ASSISTITO))
    assert firma["iss"] == f"integrity:{materiale.cn_firma}"
    assert firma["subject_organization_id"] == "100" and firma["subject_organization"] == "Regione Umbria"
    assert firma["locality"].endswith("^^^^100201") and firma["subject_role"] == "APR"
    assert firma["person_id"] == cx_cf(ASSISTITO) and firma["patient_consent"] is True
    assert firma["purpose_of_use"] == "TREATMENT" and firma["action_id"] == "CREATE"
    assert firma["subject_application_id"] == "VARCO"


@pytest.mark.parametrize("servizio,azione,scopo,assistito", [
    (ServizioUmbria.LOTTO_NRE, "CREATE", None, False),
    (ServizioUmbria.NRE_UTILIZZATI, "READ", None, False),
    (ServizioUmbria.SOSTITUZIONE, "CREATE", None, False),
    (ServizioUmbria.INVIO, "CREATE", "TREATMENT", True),
    (ServizioUmbria.VISUALIZZA, "READ", "TREATMENT", True),
    (ServizioUmbria.ANNULLA, "DELETE", "UPDATE", True),
])
def test_claim_per_servizio_come_le_tabelle_della_wiki(server, materiale, servizio, azione, scopo, assistito):
    _, f = _payload(_canale(server, materiale).token_firma(servizio, ASSISTITO if assistito else None))
    assert f["action_id"] == azione
    assert f.get("purpose_of_use") == scopo and (("purpose_of_use" in f) is (scopo is not None))
    assert ("person_id" in f) is assistito and ("patient_consent" in f) is assistito


def test_firma_del_jwt_verificata_col_certificato_x5c(server, materiale):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    for alg, h in (("RS256", hashes.SHA256()), ("RS384", hashes.SHA384()), ("RS512", hashes.SHA512())):
        token = _canale(server, materiale, firmatario=_firmatario(materiale, algoritmo=alg)).token_autenticazione()
        testa, _ = _payload(token)
        cert = x509.load_der_x509_certificate(base64.b64decode(testa["x5c"][0]))
        t, p, s = token.split(".")
        cert.public_key().verify(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)), f"{t}.{p}".encode(), padding.PKCS1v15(), h)
        assert testa["alg"] == alg
    with pytest.raises(ConfigurazioneNonValida):
        _firmatario(materiale, algoritmo="RS383")  # il refuso della wiki


def test_person_id_obbligatorio_dove_lo_vuole_la_wiki(server, materiale):
    can = _canale(server, materiale)
    with pytest.raises(ConfigurazioneNonValida, match="person_id"):
        can.token_firma(ServizioUmbria.ANNULLA, None)
    s = RicettaUmbria(can)
    with pytest.raises(RicettaNonValida, match="person_id"):
        s.annulla("1000A1000001000")
    assert server.stato.richieste == []


def test_configurazione_del_canale(server, materiale):
    with pytest.raises(ConfigurazioneNonValida, match="locality"):
        _canale(server, materiale, azienda="100999")
    with pytest.raises(ConfigurazioneNonValida, match="subject_role"):
        _canale(server, materiale, ruolo="MMG")
    with pytest.raises(AttributeError):
        _canale(server, materiale).cf_medico = SOSTITUTO


# ------------------------------------------------------------------ codec e controlli locali


def test_payload_json_come_l_openapi_tutto_stringhe_niente_null():
    corpo = json_umbria.richiesta_invio(_farm("1000A1000001000"))
    assert corpo["pinCode"] == "" and corpo["codiceAss"] == ASSISTITO  # in chiaro, non cifrato
    assert corpo["nre"] == "1000A1000001000" and corpo["codRegione"] == "100"
    riga = corpo["elencoDettagliPrescrizioni"]["dettaglioPrescrizione"][0]
    assert riga["quantita"] == "1"

    def foglie(x):
        if isinstance(x, dict):
            for v in x.values():
                yield from foglie(v)
        elif isinstance(x, list):
            for v in x:
                yield from foglie(v)
        else:
            yield x

    assert all(isinstance(v, str) for v in foglie(corpo))
    assert json_umbria.richiesta_lotto(TITOLARE, "1") == {"codRegione": "100", "identificativoLotto": "1", "cfmedico": TITOLARE}


@pytest.mark.parametrize("ricetta,frammento", [
    (_farm(None), "nre obbligatorio"),
    (_farm("0600A1000001000"), "comincia per 100"),
    (_farm("1000A100000100"), "15 caratteri"),
    (_farm("1000A1000001000", prescrittore=Prescrittore(TITOLARE, "060", "201", "F")), "codRegione"),
    (_farm("1000A1000001000", assistito=Assistito("STP0601010000001", tipo_ricetta="ST")), "person_id"),
    (_farm("1000A1000001000", testata2="SMARTCUP=SI;TEL=3330000000;EMAIL=prova@example.org;NOTECUP=x;"), "specialistiche"),
    (_spec("1000A1000001000", testata2="SMARTCUP=SI;TEL=3330000000"), "termina"),
    (_spec("1000A1000001000", testata2="SMARTCUP=SI;TEL=1;EMAIL=a@b.it;NOTECUP=" + "<" * 60 + ";"), "256"),
])
def test_controlli_locali_umbria(ricetta, frammento):
    assert any(frammento in p for p in json_umbria.problemi_umbria(ricetta)), json_umbria.problemi_umbria(ricetta)


def test_controlli_locali_gruppo_di_controllo():
    assert json_umbria.problemi_umbria(_farm("1000A1000001000")) == []
    assert json_umbria.problemi_umbria(_spec("1000A1000001000",
                                             testata2="SMARTCUP=SI;TEL=3330000000;EMAIL=prova@example.org;NOTECUP=;")) == []


def test_lotto_nre_come_la_collection_postman():
    l1000 = LottoNRE("100", "0A", "1", "000001")
    assert l1000.dimensione == 1000 and l1000.nre(0) == "1000A1000001000" and l1000.nre(999) == "1000A1000001999"
    l100 = LottoNRE("100", "0A", "0", "0000002")
    assert l100.dimensione == 100 and l100.nre(7) == "1000A0000000207"
    assert l1000.contiene("1000A1000001042") and not l1000.contiene("1000A1000002042")
    with pytest.raises(ValueError):
        l100.nre(100)


# ------------------------------------------------------------------ giro completo contro il server finto (mTLS + JWT)


def test_e2e_lotto_invio_visualizza_nre_annulla(server, materiale):
    s = _servizio(server, materiale)
    lotto = _lotto(s)
    e = s.invia(_farm(lotto.nre(0)))
    assert e.ok and e.nre == lotto.nre(0) and len(e.codice_autenticazione) == 30
    assert e.pdf_promemoria.startswith(b"%PDF")
    r = server.stato.richieste[-1]
    assert r["cn_tls"] == materiale.cn_firma and r["cf_utente"] == TITOLARE and r["corpo"]["codiceAss"] == ASSISTITO
    v = s.visualizza(e.nre, cf_assistito=ASSISTITO)
    assert v.ok and v.stato_processo == "3" and v.righe[0]["codGruppoEquival"] == "CJA"
    trovate = s.interroga_nre_utilizzati(CriteriNreUtilizzati("100", nre=e.nre))
    assert [x.nre for x in trovate.ricette] == [e.nre]
    assert s.annulla(e.nre, cf_assistito=ASSISTITO).ok
    di_nuovo = s.annulla(e.nre, cf_assistito=ASSISTITO)
    assert not di_nuovo.ok and di_nuovo.errori[0].codice == "1120"


def test_e2e_specialistica_con_smartcup(server, materiale):
    s = _servizio(server, materiale)
    lotto = _lotto(s, "0")
    e = s.invia(_spec(lotto.nre(1), testata2="SMARTCUP=SI;TEL=3330000000;EMAIL=prova@example.org;NOTECUP=PROVA;"))
    assert e.ok, e.errori


def test_e2e_nre_riusato_o_di_un_altro_medico(server, materiale):
    s = _servizio(server, materiale)
    lotto = _lotto(s)
    assert s.invia(_farm(lotto.nre(5))).ok
    riuso = s.invia(_farm(lotto.nre(5)))
    assert not riuso.ok and riuso.errori[0].codice == "1102"
    inventato = s.invia(_farm("1000A1999999000"))
    assert not inventato.ok and inventato.errori[0].codice == "1101"


def test_e2e_sostituto_con_il_suo_canale(server, materiale):
    tit = _servizio(server, materiale)
    lotto = _lotto(tit)
    pr = Prescrittore(TITOLARE, "100", "201", "F", codice_fiscale_sostituto=SOSTITUTO)
    with pytest.raises(RicettaNonValida, match="sostituto"):
        tit.invia(_farm(lotto.nre(0), prescrittore=pr))
    sost = _servizio(server, materiale, SOSTITUTO)
    e = sost.invia(_farm(lotto.nre(1), prescrittore=pr))
    assert e.ok, e.errori
    assert sost.dichiara_sostituzione is not None
    esito = tit.dichiara_sostituzione(SOSTITUTO, dt.date(2026, 11, 1), dt.date(2026, 11, 30),
                                      codice_asl="201", codice_specializzazione="F")
    assert esito.ok and server.stato.sostituzioni[-1]["pwd"] == ""
    with pytest.raises(RicettaNonValida, match="rovesciato"):
        tit.dichiara_sostituzione(SOSTITUTO, dt.date(2026, 11, 30), dt.date(2026, 11, 1),
                                  codice_asl="201", codice_specializzazione="F")


@pytest.mark.parametrize("stato", [502, 504])
@pytest.mark.parametrize("accettata", [True, False], ids=["accettata", "persa"])
def test_e2e_invio_incerto_annulla_e_nuovo_nre(server, materiale, stato, accettata):
    """wiki «Invio prescritto DEMA», IMPORTANT: dopo 502/504 annullare con lo STESSO NRE e rifare
    l'invio con un NRE DIVERSO."""
    s = _servizio(server, materiale)
    lotto = _lotto(s)
    server.guasti["dem-invio-prescritto"] = (stato, accettata)
    with pytest.raises(InvioIncertoUmbria) as ei:
        s.invia(_farm(lotto.nre(0)))
    assert ei.value.nre == lotto.nre(0) and ei.value.stato_http == stato and ei.value.cf_assistito == ASSISTITO
    del server.guasti["dem-invio-prescritto"]
    ann = s.annulla_invio_incerto(ei.value)
    assert ann.ok is accettata  # se il SAC non l'aveva vista, l'annullamento risponde «inesistente»
    assert not s.invia(_farm(lotto.nre(0))).ok  # lo stesso NRE non si riusa
    assert s.invia(_farm(lotto.nre(1))).ok


def test_invio_senza_risposta_e_incerto_anche_lui(materiale):
    class Muto:
        def invia(self, r):
            raise ErroreTrasporto("timeout")

    can = CanaleUmbria(TITOLARE, _firmatario(materiale), azienda="100201", base_url="https://127.0.0.1:9/sar",
                       applicativo_di_prova=APPLICATIVO, trasporto=Muto())
    with pytest.raises(InvioIncertoUmbria):
        RicettaUmbria(can).invia(_farm("1000A1000001000"))
    with pytest.raises(ErroreTrasporto) as ei:
        RicettaUmbria(can).richiedi_lotto_nre()
    assert not isinstance(ei.value, InvioIncertoUmbria)  # incerto vale solo per l'invio


# ------------------------------------------------------------------ il server finto morde (gruppi di controllo)


def _post(server, materiale, servizio: str, corpo, *, auth=None, firma=None, con_certificato=True, tipo="application/json"):
    can = _canale(server, materiale)
    sv = ServizioUmbria(servizio)
    cf_ass = ASSISTITO if json_umbria and servizio in ("dem-invio-prescritto", "dem-visualizza-prescritto",
                                                       "dem-annulla-prescritto") else None
    h = {"Content-Type": tipo,
         "Authorization": f"Bearer {auth if auth is not None else can.token_autenticazione()}",
         "FSE-JWT-Signature": firma if firma is not None else can.token_firma(sv, cf_ass)}
    req = urllib.request.Request(server.url + sv.percorso, data=json.dumps(corpo).encode(), headers=h, method="POST")
    ctx = materiale.contesto_client(con_certificato)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _rifirma(materiale, token: str, **modifiche) -> str:
    """Lo stesso token con alcuni claim cambiati, rifirmato con la chiave di prova (firma valida)."""
    from varco.trasporto.umbria import jwt_firmato

    _, p = _payload(token)
    p.update(modifiche)
    for k, v in list(p.items()):
        if v is None:
            del p[k]
    return jwt_firmato(_firmatario(materiale), p)


def test_server_gruppo_di_controllo_richiesta_buona(server, materiale):
    stato, d = _post(server, materiale, "richiesta-lotto-nre", json_umbria.richiesta_lotto(TITOLARE, "1"))
    assert stato == 200 and d["codEsito"] == "00"


@pytest.mark.parametrize("difetto", ["alg", "x5c", "estranea", "iss", "scaduto", "aud", "sub", "jti", "firma"])
def test_server_rifiuta_token_di_autenticazione_difettosi(server, materiale, difetto):
    can = _canale(server, materiale)
    buono = can.token_autenticazione()
    if difetto == "estranea":
        token = CanaleUmbria(TITOLARE, _firmatario(materiale, materiale.firma_estranea_p12), azienda="100201",
                             base_url=server.url, applicativo_di_prova=APPLICATIVO).token_autenticazione()
    elif difetto == "alg":
        t, p, s = buono.split(".")
        testa, _ = _payload(buono)
        testa["alg"] = "HS256"
        token = ".".join([base64.urlsafe_b64encode(json.dumps(testa).encode()).rstrip(b"=").decode(), p, s])
    elif difetto == "x5c":
        t, p, s = buono.split(".")
        testa, _ = _payload(buono)
        del testa["x5c"]
        token = ".".join([base64.urlsafe_b64encode(json.dumps(testa).encode()).rstrip(b"=").decode(), p, s])
    elif difetto == "iss":
        token = _rifirma(materiale, buono, iss="integrity:" + materiale.cn_firma)
    elif difetto == "scaduto":
        token = _rifirma(materiale, buono, iat=1, exp=2)
    elif difetto == "aud":
        token = _rifirma(materiale, buono, aud=server.url)  # con «/sar»: la descrizione, non l'esempio
    elif difetto == "sub":
        token = _rifirma(materiale, buono, sub=cx_cf(SOSTITUTO))
    elif difetto == "jti":
        _post(server, materiale, "richiesta-lotto-nre", json_umbria.richiesta_lotto(TITOLARE, "1"), auth=buono)
        token = buono  # lo stesso jti una seconda volta
    else:
        t, p, s = buono.split(".")
        token = f"{t}.{p}.{s[:-4]}AAAA"
    stato, d = _post(server, materiale, "richiesta-lotto-nre", json_umbria.richiesta_lotto(TITOLARE, "1"), auth=token)
    assert stato == 401 and d["status"] == 401, d


@pytest.mark.parametrize("modifica,servizio", [
    ({"purpose_of_use": "TREATMENT"}, "richiesta-lotto-nre"),  # «Non inviare»
    ({"person_id": cx_cf(ASSISTITO)}, "dem-nre-utilizzati"),
    ({"action_id": "READ"}, "richiesta-lotto-nre"),
    ({"subject_organization_id": "060"}, "richiesta-lotto-nre"),
    ({"locality": "Azienda USL di Prova"}, "richiesta-lotto-nre"),
    ({"subject_role": "MMG"}, "richiesta-lotto-nre"),
    ({"subject_application_vendor": None}, "richiesta-lotto-nre"),
])
def test_server_rifiuta_claim_applicativi_sbagliati(server, materiale, modifica, servizio):
    can = _canale(server, materiale)
    firma = _rifirma(materiale, can.token_firma(ServizioUmbria(servizio), None), **modifica)
    corpo = (json_umbria.richiesta_lotto(TITOLARE, "1") if servizio == "richiesta-lotto-nre"
             else json_umbria.richiesta_interroga_nre(CriteriNreUtilizzati("100", nre="1000A1000001000"), TITOLARE))
    stato, d = _post(server, materiale, servizio, corpo, firma=firma)
    assert stato == 401, d


def test_server_person_id_diverso_dal_codice_assistito(server, materiale):
    s = _servizio(server, materiale)
    nre = _lotto(s).nre(0)
    can = _canale(server, materiale)
    firma = can.token_firma(ServizioUmbria.INVIO, "RSSMRA80A01H501U")
    stato, d = _post(server, materiale, "dem-invio-prescritto", json_umbria.richiesta_invio(_farm(nre)), firma=firma)
    assert stato == 401 and "person_id" in d["detail"]


@pytest.mark.parametrize("guasto", ["numero", "null", "ignota", "manca", "pincode", "regione", "cifrato"])
def test_server_rifiuta_corpi_fuori_dall_openapi(server, materiale, guasto):
    s = _servizio(server, materiale)
    corpo = json_umbria.richiesta_invio(_farm(_lotto(s).nre(0)))
    if guasto == "numero":
        corpo["elencoDettagliPrescrizioni"]["dettaglioPrescrizione"][0]["quantita"] = 2  # come gli esempi della wiki
    elif guasto == "null":
        corpo["testata1"] = None  # come la collection Postman
    elif guasto == "ignota":
        corpo["campoInventato"] = "x"
    elif guasto == "manca":
        del corpo["tipoVisita"]
    elif guasto == "pincode":
        corpo["pinCode"] = "1234567890"
    elif guasto == "regione":
        corpo["codRegione"] = "060"
    else:
        corpo["codiceAss"] = base64.b64encode(b"x" * 256).decode()
    stato, d = _post(server, materiale, "dem-invio-prescritto", corpo)
    if guasto == "cifrato":
        assert stato == 200 and d["codEsitoInserimento"] == "9999"  # errore «del SAC»: 200 con l'elenco
    else:
        assert stato == 400 and d["title"] == "InvalidRequestContent", d


def test_server_senza_certificato_di_autenticazione_niente_handshake(server, materiale):
    with pytest.raises((urllib.error.URLError, ConnectionError, OSError)):
        _post(server, materiale, "richiesta-lotto-nre", json_umbria.richiesta_lotto(TITOLARE, "1"), con_certificato=False)


def test_server_content_type_e_percorso(server, materiale):
    stato, d = _post(server, materiale, "richiesta-lotto-nre", json_umbria.richiesta_lotto(TITOLARE, "1"), tipo="application/xml")
    assert stato == 415


def test_errore_rfc7807_arriva_al_chiamante(server, materiale):
    class Rompi(TrasportoLocale):
        def invia(self, r):
            corpo = json.loads(r.corpo)
            corpo["campoInventato"] = "x"
            return super().invia(dataclasses.replace(r, corpo=json.dumps(corpo).encode()))

    s = _servizio(server, materiale, trasporto=Rompi(materiale))
    with pytest.raises(ErroreServizioUmbria) as ei:
        s.richiedi_lotto_nre()
    assert ei.value.stato_http == 400 and ei.value.problema["title"] == "InvalidRequestContent"


# ------------------------------------------------------------------ registro


def test_registro_redatto_niente_dati_personali_ne_token(server, materiale, tmp_path):
    t = TrasportoLocale(materiale, registratore=RegistratoreFile(tmp_path))
    s = _servizio(server, materiale, trasporto=t)
    lotto = _lotto(s)
    ricetta = _spec(lotto.nre(0), assistito=Assistito(ASSISTITO, cognome_nome="ROSSI MARIO", indirizzo="VIA DI PROVA 1"),
                    testata2="SMARTCUP=SI;TEL=3331234567;EMAIL=mario.rossi@example.org;NOTECUP=PROVA;")
    e = s.invia(ricetta)
    assert e.ok
    s.visualizza(e.nre, cf_assistito=ASSISTITO)
    testo = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    for chiaro in (ASSISTITO, TITOLARE, e.nre, e.codice_autenticazione, "ROSSI MARIO", "VIA DI PROVA", "3331234567",
                   "mario.rossi@example.org", "CONTROLLO", "eyJ", "promemoria SINTETICO", lotto.prefisso):
        assert chiaro not in testo, chiaro


# ------------------------------------------------------------------ contro l'OpenAPI ufficiale (specifiche scaricate)


richiede_openapi = pytest.mark.skipif(not OPENAPI.exists(), reason="specifiche Umbria non scaricate "
                                      "(strumenti/scarica_specifiche.py --gruppi umbria)")


def _openapi():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))


def _schema(doc, nome):
    """Lo schema OpenAPI 3.1 (JSON Schema 2020-12) con i $ref risolti e, per severità, niente proprietà
    non dichiarate: il kit non deve inventare campi."""
    comp = doc["components"]["schemas"]

    def risolvi(s):
        if isinstance(s, dict):
            if "$ref" in s:
                return risolvi(comp[s["$ref"].rsplit("/", 1)[-1]])
            out = {k: risolvi(v) for k, v in s.items() if k != "xml"}
            if out.get("type") == "object":
                out["additionalProperties"] = False
            return out
        if isinstance(s, list):
            return [risolvi(x) for x in s]
        return s

    return risolvi(comp[nome])


@richiede_openapi
@pytest.mark.parametrize("nome,corpo", [
    ("InvioPrescrittoRichiesta", json_umbria.richiesta_invio(_farm("1000A1000001000"))),
    ("InvioPrescrittoRichiesta", json_umbria.richiesta_invio(_spec("1000A1000001000"))),
    ("VisualizzaPrescrittoRichiesta", json_umbria.richiesta_visualizza("1000A1000001000", TITOLARE)),
    ("AnnullaPrescrittoRichiesta", json_umbria.richiesta_annulla("1000A1000001000", TITOLARE)),
    ("InterrogaNreUtilRichiesta", json_umbria.richiesta_interroga_nre(
        CriteriNreUtilizzati("100", tipo=TipoPrescrizione.SPECIALISTICA, dal=dt.datetime(2026, 10, 1), al=dt.datetime(2026, 10, 2)), TITOLARE)),
    ("LottoRichiestaNRE", json_umbria.richiesta_lotto(TITOLARE, "1")),
    ("DichiarazioneSostituzioneMedicoRichiesta", json_umbria.richiesta_sostituzione(
        TITOLARE, SOSTITUTO, "201", "F", dt.date(2026, 11, 1), dt.date(2026, 11, 30))),
])
def test_richieste_del_kit_valide_per_l_openapi(nome, corpo):
    import jsonschema

    jsonschema.validate(corpo, _schema(_openapi(), nome))


@richiede_openapi
def test_lo_schema_morde_gruppo_di_controllo():
    import jsonschema

    corpo = json_umbria.richiesta_invio(_farm("1000A1000001000"))
    corpo["elencoDettagliPrescrizioni"]["dettaglioPrescrizione"][0]["quantita"] = 2
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(corpo, _schema(_openapi(), "InvioPrescrittoRichiesta"))


@richiede_openapi
def test_lo_schema_del_server_finto_e_quello_dell_openapi():
    """Il server finto trascrive proprietà e required dell'OpenAPI: se la specifica cambia, questo lo dice."""
    comp = _openapi()["components"]["schemas"]
    nomi = {"richiesta-lotto-nre": "LottoRichiestaNRE", "dem-nre-utilizzati": "InterrogaNreUtilRichiesta",
            "sostituzione-medico": "DichiarazioneSostituzioneMedicoRichiesta", "dem-invio-prescritto": "InvioPrescrittoRichiesta",
            "dem-visualizza-prescritto": "VisualizzaPrescrittoRichiesta", "dem-annulla-prescritto": "AnnullaPrescrittoRichiesta"}
    for servizio, nome in nomi.items():
        ammesse, obbligatorie = SERVER.SCHEMI[servizio]
        assert ammesse == set(comp[nome]["properties"]), servizio
        assert obbligatorie == set(comp[nome].get("required", [])), servizio
    riga = comp["DettaglioPrescrizioneType"]
    assert SERVER.RIGA == (set(riga["properties"]), set(riga["required"]))
    percorsi = {p.rsplit("/", 1)[-1] for p in _openapi()["paths"]}
    assert set(nomi) <= percorsi


@richiede_openapi
@pytest.mark.parametrize("file,nome", [
    ("invio_ok.json", "InvioPrescrittoRicevuta"), ("invio_avviso_0001.json", "InvioPrescrittoRicevuta"),
    ("invio_rifiuto_1101.json", "InvioPrescrittoRicevuta"), ("visualizza_ok.json", "VisualizzaPrescrittoRicevuta"),
    ("visualizza_5005.json", "VisualizzaPrescrittoRicevuta"), ("annulla_ok.json", "AnnullaPrescrittoRicevuta"),
    ("nre_utilizzati_ok.json", "InterrogaNreUtilRicevuta"), ("lotto_ok_1000.json", "LottoRicevutaNRE"),
    ("lotto_ok_100.json", "LottoRicevutaNRE"), ("lotto_rifiuto.json", "LottoRicevutaNRE"),
    ("sostituzione_ok.json", "DichiarazioneSostituzioneMedicoRicevuta"),
])
def test_risposte_sintetiche_valide_per_l_openapi(file, nome):
    import jsonschema

    dati = json.loads((RADICE / "conformita" / "risposte" / "umbria" / file).read_text(encoding="utf-8"))
    jsonschema.validate(dati, _schema(_openapi(), nome))


def test_risposte_sintetiche_rigenerabili():
    for nome, dati in SERVER.risposte_sintetiche().items():
        assert (RADICE / "conformita" / "risposte" / "umbria" / nome).read_bytes() == dati, nome
