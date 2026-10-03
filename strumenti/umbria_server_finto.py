# SPDX-License-Identifier: EUPL-1.2
"""SAR Umbria FINTO, su 127.0.0.1, per provare il modulo Umbria senza toccare i sistemi della Regione.

Severo quanto la specifica (github.com/punto-zero/umbria-sar-support: wiki «Home» e «Prescrittori»,
OpenAPI `sar-open-api-prescrittore.yaml`). Su ogni richiesta controlla:

  - HTTPS con mutua autenticazione: senza il certificato di autenticazione (firmato dalla CA di
    prova) l'handshake non si chiude;
  - solo POST, solo i percorsi `/sar/v1/servizi-prescrittore/...` dell'OpenAPI, `Content-Type`
    JSON, corpo = un oggetto JSON (niente NaN, Infinity);
  - i due JWT (wiki «Autenticazione», «Token JWT»), entrambi:
      * header: alg RS256/RS384/RS512, typ «JWT», x5c con un certificato emesso dalla CA di prova,
        firma verificata con quel certificato;
      * iss «auth:<CN>» (Authorization) o «integrity:<CN>» (FSE-JWT-Signature), CN del certificato x5c;
      * iat ed exp interi, exp nel futuro e iat non nel futuro; jti mai visto prima (niente replay);
      * aud = l'host del servizio, senza «/sar», come negli esempi della wiki; sub = CF nel tipo CX (CF^^^&2.16.840.1.113883.2.9.4.3.2&ISO),
        lo stesso nei due token;
    solo nel token di firma: subject_organization_id «100», subject_organization «Regione Umbria»,
    locality tra le quattro aziende, subject_role APR/AAS/OAM, subject_application_* valorizzati,
    action_id e purpose_of_use del servizio, person_id e patient_consent=true dove la tabella del
    servizio li vuole e ASSENTI dove dice «Non inviare»;
  - il corpo contro lo schema dell'OpenAPI, trascritto qui: proprietà ammesse, obbligatorie, ogni
    valore una stringa (`type: string`), le righe in elencoDettagliPrescrizioni.dettaglioPrescrizione;
  - il merito che la wiki aggiunge al SAC: codRegione «100», pinCode vuoto, CF dell'assistito in
    chiaro e uguale al person_id del token, NRE obbligatorio, di un lotto ASSEGNATO a quel medico e
    mai usato; il medico del token è quello che invia (cfMedico2 se c'è, altrimenti cfMedico1);
    SmartCUP in testata2 solo nel formato della wiki e solo sulla specialistica.

Gli errori di forma tornano come RFC 7807 (400, 401, 404, 405, 415); gli errori «del SAC» come
risposta 200 con l'elenco degli errori, come dice la wiki. `guasti` simula i 502/504 dell'invio, anche
DOPO aver accettato la ricetta (il caso per cui la specifica chiede annulla + nuovo NRE).

Tutto il materiale crittografico è generato qui ed è di PROVA: niente a che vedere con i certificati
pubblicati da PuntoZero, che il kit non usa e non redistribuisce.
"""

from __future__ import annotations

import base64
import datetime as _dt
import http.server
import itertools
import json
import math
import re
import ssl
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

PREFISSO = "/sar/v1/servizi-prescrittore/"
OID_CF = "2.16.840.1.113883.2.9.4.3.2"
_CX = re.compile(r"([A-Z0-9]{16})\^\^\^&" + re.escape(OID_CF) + r"&ISO")
_CF = re.compile(r"[A-Z0-9]{16}")
_SMARTCUP = re.compile(r"SMARTCUP=SI;TEL=[^;]*;EMAIL=[^;]*;NOTECUP=[^;]*;")
LOCALITY = {
    "Azienda USL Umbria 1^^^^^&2.16.840.1.113883.2.9.4.1.1&ISO^^^^100201",
    "Azienda USL Umbria 2^^^^^&2.16.840.1.113883.2.9.4.1.1&ISO^^^^100202",
    "Azienda Ospedaliera di Perugia^^^^^&2.16.840.1.113883.2.9.4.1.2&ISO^^^^100901",
    "Azienda Ospedaliera di Terni^^^^^&2.16.840.1.113883.2.9.4.1.2&ISO^^^^100902",
}

# servizio -> (action_id, purpose_of_use o None = «Non inviare», con assistito)
CLAIM = {
    "richiesta-lotto-nre": ("CREATE", None, False),
    "dem-nre-utilizzati": ("READ", None, False),
    "sostituzione-medico": ("CREATE", None, False),
    "dem-invio-prescritto": ("CREATE", "TREATMENT", True),
    "dem-visualizza-prescritto": ("READ", "TREATMENT", True),
    "dem-annulla-prescritto": ("DELETE", "UPDATE", True),
}

# --- schemi dell'OpenAPI (components.schemas), trascritti: proprietà e required ------------------
RIGA = ({"codProdPrest", "descrProdPrest", "codGruppoEquival", "descrGruppoEquival", "testoLibero",
         "descrTestoLiberoNote", "nonSost", "motivazNote", "codMotivazione", "notaProd", "quantita",
         "prescrizione1", "prescrizione2", "codCatalogoPrescr", "tipoAccesso", "numeroNota", "condErogabilita",
         "approprPrescrittiva", "patologia", "numsedute"}, {"quantita"})
SCHEMI = {
    "richiesta-lotto-nre": ({"codRegione", "identificativoLotto", "cfmedico"}, {"codRegione", "identificativoLotto"}),
    "dem-nre-utilizzati": ({"pinCode", "codRegione", "nre", "codLotto", "cfMedico", "cfAssistito", "tipoPrescr",
                            "dataCompilazioneRicettaDal", "dataCompilazioneRicettaAl"}, {"cfMedico", "codRegione", "pinCode"}),
    "sostituzione-medico": ({"pinCode", "pwd", "cfMedicoTitolare", "codRegione", "codASLAo", "codStruttura",
                             "codSpecializzazione", "cfMedicoSostituto", "comunicazioneAsl", "dataInizioSostituzione",
                             "dataFineSostituzione", "nota", "opzioni"},
                            {"cfMedicoSostituto", "cfMedicoTitolare", "codASLAo", "codRegione", "codSpecializzazione",
                             "dataFineSostituzione", "dataInizioSostituzione", "pinCode", "pwd"}),
    "dem-invio-prescritto": ({"pinCode", "cfMedico1", "cfMedico2", "codRegione", "codASLAo", "codStruttura",
                              "codSpecializzazione", "testata1", "testata2", "nre", "tipoRic", "codiceAss", "cognNome",
                              "indirizzo", "oscuramDati", "numTessSasn", "socNavigaz", "tipoPrescrizione",
                              "ricettaInterna", "codEsenzione", "nonEsente", "reddito", "codDiagnosi",
                              "descrizioneDiagnosi", "dataCompilazione", "tipoVisita", "dispReg", "provAssistito",
                              "aslAssistito", "indicazionePrescr", "altro", "classePriorita", "statoEstero",
                              "istituzCompetente", "numIdentPers", "numIdentTess", "dataNascitaEstero",
                              "dataScadTessera", "elencoDettagliPrescrizioni"},
                             {"cfMedico1", "codASLAo", "codRegione", "codSpecializzazione", "dataCompilazione",
                              "elencoDettagliPrescrizioni", "pinCode", "tipoPrescrizione", "tipoVisita"}),
    "dem-visualizza-prescritto": ({"pinCode", "nre", "cfMedico"}, {"cfMedico", "nre", "pinCode"}),
    "dem-annulla-prescritto": ({"pinCode", "nre", "cfMedico"}, {"cfMedico", "nre", "pinCode"}),
}
CONTENITORI = {"elencoDettagliPrescrizioni", "opzioni"}


class Rifiuto(Exception):
    def __init__(self, stato: int, titolo: str, dettaglio: str, tipo: str = "mw/validation-error"):
        self.stato, self.titolo, self.dettaglio, self.tipo = stato, titolo, dettaglio, tipo
        super().__init__(dettaglio)

    def corpo(self) -> bytes:
        return json.dumps({"type": self.tipo, "title": self.titolo, "status": self.stato, "detail": self.dettaglio}).encode()


def _b64url_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _intero(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _non_finito(nome: str):
    raise ValueError(f"costante JSON non ammessa: {nome}")


@dataclass
class RicettaFinta:
    nre: str
    cf_medico_titolare: str
    cf_medico_inviante: str
    cf_assistito: str
    tipo: str
    data: str
    codice_aut: str
    corpo: dict
    annullata: bool = False


@dataclass
class StatoUmbria:
    lotti: dict[str, str] = field(default_factory=dict)  # prefisso del lotto -> CF del medico
    usati: set[str] = field(default_factory=set)
    ricette: dict[str, RicettaFinta] = field(default_factory=dict)
    sostituzioni: list[dict] = field(default_factory=list)
    jti: set[str] = field(default_factory=set)
    richieste: list[dict] = field(default_factory=list)
    contatore: itertools.count = field(default_factory=lambda: itertools.count(1))


class ServerUmbria:
    def __init__(self, materiale: "MaterialeUmbria", *, ritardo_orologio_s: float = 0.0):
        from cryptography import x509

        self.materiale = materiale
        self._ca = x509.load_pem_x509_certificate(materiale.ca.read_bytes())
        self.stato = StatoUmbria()
        self.guasti: dict[str, tuple[int, bool]] = {}  # servizio -> (stato HTTP, ricetta accettata prima del guasto)
        self.ritardo_orologio_s = ritardo_orologio_s
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(str(materiale.certificato_server), str(materiale.chiave_server))
        ctx.load_verify_locations(str(materiale.ca))
        ctx.verify_mode = ssl.CERT_REQUIRED  # mTLS col certificato di autenticazione
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Gestore)
        self._httpd.socket = ctx.wrap_socket(self._httpd.socket, server_side=True)
        self._httpd.finto = self  # type: ignore[attr-defined]
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        """La base URL dei servizi (come «Base URL» della wiki, con «/sar»)."""
        return f"https://127.0.0.1:{self._httpd.server_address[1]}/sar"

    @property
    def audience(self) -> str:
        """Valore atteso di aud: come negli esempi della wiki, l'host senza «/sar»."""
        return f"https://127.0.0.1:{self._httpd.server_address[1]}"

    def __enter__(self) -> "ServerUmbria":
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def adesso(self) -> float:
        return time.time() + self.ritardo_orologio_s

    # ------------------------------------------------------------------ JWT

    def _jwt(self, token: str, prefisso: str) -> dict:
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.x509.oid import NameOID

        nome = "Authorization" if prefisso == "auth" else "FSE-JWT-Signature"
        parti = token.split(".")
        if len(parti) != 3:
            raise Rifiuto(401, "Unauthorized", f"{nome}: JWT malformato")
        try:
            testa = json.loads(_b64url_dec(parti[0]), parse_constant=_non_finito)
            dati = json.loads(_b64url_dec(parti[1]), parse_constant=_non_finito)
            firma = _b64url_dec(parti[2])
        except (ValueError, UnicodeDecodeError) as e:
            raise Rifiuto(401, "Unauthorized", f"{nome}: JWT non leggibile ({e})") from e
        if not isinstance(testa, dict) or not isinstance(dati, dict):
            raise Rifiuto(401, "Unauthorized", f"{nome}: header e payload devono essere oggetti JSON")
        alg = testa.get("alg")
        if alg not in ("RS256", "RS384", "RS512"):
            raise Rifiuto(401, "Unauthorized", f"{nome}: alg {alg!r} non ammesso (RS256, RS384, RS512)")
        if testa.get("typ") != "JWT":
            raise Rifiuto(401, "Unauthorized", f"{nome}: typ deve essere 'JWT'")
        x5c = testa.get("x5c")
        if not (isinstance(x5c, list) and x5c and isinstance(x5c[0], str)):
            raise Rifiuto(401, "Unauthorized", f"{nome}: x5c obbligatorio (certificato di firma, DER base64)")
        try:
            cert = x509.load_der_x509_certificate(base64.b64decode(x5c[0], validate=True))
        except ValueError as e:
            raise Rifiuto(401, "Unauthorized", f"{nome}: x5c non è un certificato") from e
        try:
            cert.verify_directly_issued_by(self._ca)
        except (ValueError, TypeError, InvalidSignature) as e:
            raise Rifiuto(401, "Unauthorized", f"{nome}: certificato di firma non emesso dalla CA") from e
        chiave = cert.public_key()
        if not isinstance(chiave, rsa.RSAPublicKey):
            raise Rifiuto(401, "Unauthorized", f"{nome}: serve una chiave RSA")
        h = {"RS256": hashes.SHA256(), "RS384": hashes.SHA384(), "RS512": hashes.SHA512()}[alg]
        try:
            chiave.verify(firma, f"{parti[0]}.{parti[1]}".encode("ascii"), padding.PKCS1v15(), h)
        except InvalidSignature as e:
            raise Rifiuto(401, "Unauthorized", f"{nome}: firma non valida") from e
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if not cn or dati.get("iss") != f"{prefisso}:{cn[0].value}":
            raise Rifiuto(401, "Unauthorized", f"{nome}: iss deve essere «{prefisso}:» + CN del certificato di firma")
        iat, exp = dati.get("iat"), dati.get("exp")
        if not (_intero(iat) and _intero(exp)):
            raise Rifiuto(401, "Unauthorized", f"{nome}: iat ed exp devono essere interi")
        adesso = self.adesso()
        if exp <= adesso:
            raise Rifiuto(401, "Unauthorized", f"{nome}: token scaduto")
        if iat > adesso + 5 or exp <= iat:
            raise Rifiuto(401, "Unauthorized", f"{nome}: iat nel futuro o exp non successivo a iat")
        jti = dati.get("jti")
        if not isinstance(jti, str) or not jti.strip():
            raise Rifiuto(401, "Unauthorized", f"{nome}: jti obbligatorio")
        with self._lock:
            if (prefisso, jti) in self.stato.jti:
                raise Rifiuto(401, "Unauthorized", f"{nome}: jti già usato")
            self.stato.jti.add((prefisso, jti))  # type: ignore[arg-type]
        if dati.get("aud") != self.audience:
            raise Rifiuto(401, "Unauthorized", f"{nome}: aud deve essere l'host del servizio ({self.audience}), "
                                               "come negli esempi della wiki")
        sub = dati.get("sub")
        if not (isinstance(sub, str) and _CX.fullmatch(sub)):
            raise Rifiuto(401, "Unauthorized", f"{nome}: sub = CF nel tipo CX (CF^^^&{OID_CF}&ISO)")
        return dati

    def _claim_firma(self, servizio: str, firma: dict) -> str | None:
        """Claim applicativi del token di firma. Restituisce il CF di person_id, se c'è."""
        attesi = {"subject_organization_id": "100", "subject_organization": "Regione Umbria"}
        for k, v in attesi.items():
            if firma.get(k) != v:
                raise Rifiuto(401, "Unauthorized", f"FSE-JWT-Signature: {k} deve essere {v!r}")
        if firma.get("locality") not in LOCALITY:
            raise Rifiuto(401, "Unauthorized", "FSE-JWT-Signature: locality non tra le aziende della Regione Umbria")
        if firma.get("subject_role") not in ("APR", "AAS", "OAM"):
            raise Rifiuto(401, "Unauthorized", "FSE-JWT-Signature: subject_role APR, AAS o OAM")
        for k in ("subject_application_id", "subject_application_vendor", "subject_application_version"):
            if not (isinstance(firma.get(k), str) and firma[k].strip()):
                raise Rifiuto(401, "Unauthorized", f"FSE-JWT-Signature: {k} obbligatorio")
        azione, scopo, con_assistito = CLAIM[servizio]
        if firma.get("action_id") != azione:
            raise Rifiuto(401, "Unauthorized", f"FSE-JWT-Signature: action_id {firma.get('action_id')!r}, atteso {azione!r}")
        if scopo is None:
            if "purpose_of_use" in firma:
                raise Rifiuto(401, "Unauthorized", f"FSE-JWT-Signature: purpose_of_use da non inviare per {servizio}")
        elif firma.get("purpose_of_use") != scopo:
            raise Rifiuto(401, "Unauthorized", f"FSE-JWT-Signature: purpose_of_use {firma.get('purpose_of_use')!r}, atteso {scopo!r}")
        if con_assistito:
            pid = firma.get("person_id")
            m = _CX.fullmatch(pid) if isinstance(pid, str) else None
            if m is None:
                raise Rifiuto(401, "Unauthorized", "FSE-JWT-Signature: person_id obbligatorio, CF nel tipo CX")
            if firma.get("patient_consent") is not True:
                raise Rifiuto(401, "Unauthorized", "FSE-JWT-Signature: patient_consent deve essere true")
            return m.group(1)
        for k in ("person_id", "patient_consent"):
            if k in firma:
                raise Rifiuto(401, "Unauthorized", f"FSE-JWT-Signature: {k} da non inviare per {servizio}")
        return None

    # ------------------------------------------------------------------ corpo contro l'OpenAPI

    @staticmethod
    def _schema(servizio: str, corpo) -> None:
        if not isinstance(corpo, dict):
            raise Rifiuto(400, "InvalidRequestContent", "il corpo deve essere un oggetto JSON")
        ammesse, obbligatorie = SCHEMI[servizio]
        ignote = sorted(set(corpo) - ammesse)
        if ignote:
            raise Rifiuto(400, "InvalidRequestContent", f"proprietà non previste dall'OpenAPI: {ignote}")
        mancanti = sorted(obbligatorie - set(corpo))
        if mancanti:
            raise Rifiuto(400, "InvalidRequestContent", f"proprietà obbligatorie mancanti: {mancanti}")
        for k, v in corpo.items():
            if k in CONTENITORI:
                continue
            if not isinstance(v, str):
                raise Rifiuto(400, "InvalidRequestContent", f"{k}: type string, arrivato {type(v).__name__}")
        if servizio == "dem-invio-prescritto":
            elenco = corpo["elencoDettagliPrescrizioni"]
            if not isinstance(elenco, dict) or set(elenco) != {"dettaglioPrescrizione"} \
                    or not isinstance(elenco["dettaglioPrescrizione"], list) or not elenco["dettaglioPrescrizione"]:
                raise Rifiuto(400, "InvalidRequestContent", "elencoDettagliPrescrizioni.dettaglioPrescrizione: lista non vuota")
            for i, r in enumerate(elenco["dettaglioPrescrizione"], start=1):
                if not isinstance(r, dict):
                    raise Rifiuto(400, "InvalidRequestContent", f"riga {i}: oggetto")
                ignote = sorted(set(r) - RIGA[0])
                if ignote or not RIGA[1] <= set(r):
                    raise Rifiuto(400, "InvalidRequestContent", f"riga {i}: proprietà ignote {ignote} o quantita mancante")
                for k, v in r.items():
                    if not isinstance(v, str):
                        raise Rifiuto(400, "InvalidRequestContent", f"riga {i}, {k}: type string, arrivato {type(v).__name__}")

    # ------------------------------------------------------------------ gestione

    def gestisci(self, metodo: str, percorso: str, intestazioni, corpo: bytes, cn_tls: str | None) -> tuple[int, bytes]:
        try:
            return self._gestisci(metodo, percorso, intestazioni, corpo, cn_tls)
        except Rifiuto as r:
            return r.stato, r.corpo()

    def _gestisci(self, metodo, percorso, intestazioni, corpo, cn_tls) -> tuple[int, bytes]:
        servizio = percorso[len(PREFISSO):] if percorso.startswith(PREFISSO) else None
        if servizio not in SCHEMI:
            raise Rifiuto(404, "NotFound", f"percorso sconosciuto: {percorso}", "mw/not-found")
        if metodo != "POST":
            raise Rifiuto(405, "MethodNotAllowed", "solo POST", "mw/method-not-allowed")
        if cn_tls is None:
            raise Rifiuto(401, "Unauthorized", "certificato di autenticazione assente")
        if (intestazioni.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            raise Rifiuto(415, "UnsupportedMediaType", "Content-Type application/json", "mw/unsupported-media-type")
        aut = intestazioni.get("Authorization") or ""
        if not aut.startswith("Bearer "):
            raise Rifiuto(401, "Unauthorized", "Authorization: Bearer <JWT> obbligatorio")
        firma_h = intestazioni.get("FSE-JWT-Signature")
        if not firma_h:
            raise Rifiuto(401, "Unauthorized", "FSE-JWT-Signature obbligatorio")
        auth = self._jwt(aut[7:].strip(), "auth")
        firma = self._jwt(firma_h.strip(), "integrity")
        if auth["sub"] != firma["sub"]:
            raise Rifiuto(401, "Unauthorized", "sub diverso nei due token")
        cf_utente = _CX.fullmatch(auth["sub"]).group(1)  # type: ignore[union-attr]
        cf_persona = self._claim_firma(servizio, firma)
        try:
            dati = json.loads(corpo.decode("utf-8"), parse_constant=_non_finito)
        except (ValueError, UnicodeDecodeError) as e:
            raise Rifiuto(400, "InvalidRequestContent", f"JSON non valido: {e}") from e
        self._schema(servizio, dati)
        with self._lock:
            self.stato.richieste.append({"servizio": servizio, "cf_utente": cf_utente, "cn_tls": cn_tls,
                                         "intestazioni": dict(intestazioni.items()), "corpo": dati})
        if "pinCode" in dati and dati["pinCode"] != "":
            raise Rifiuto(400, "InvalidRequestContent", "pinCode: vuoto nel SAR Umbria (autenticazione con i JWT)")
        if dati.get("codRegione") not in (None, "100"):
            raise Rifiuto(400, "InvalidRequestContent", "codRegione: \"100\" (Regione Umbria)")
        metodo_servizio = getattr(self, "_op_" + servizio.replace("-", "_"))
        stato, risposta = metodo_servizio(dati, cf_utente, cf_persona)
        return stato, json.dumps(risposta, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _errore(chiave: str, codice: str, testo: str, **altro) -> dict:
        return {chiave: "9999", **altro,
                "elencoErroriRicette": {"erroreRicetta": [{"codEsito": codice, "esito": testo, "progPresc": "0", "tipoErrore": "E"}]}}

    def _op_richiesta_lotto_nre(self, d, cf_utente, _):
        if d.get("cfmedico", cf_utente) != cf_utente:
            raise Rifiuto(400, "InvalidRequestContent", "cfmedico diverso dall'utente del token")
        tipo = d["identificativoLotto"]
        if tipo not in ("0", "1"):
            return 200, {"codEsito": "99", "esito": "identificativo lotto non valido"}
        n = next(self.stato.contatore)
        lotto = str(n).zfill(7 if tipo == "0" else 6)
        prefisso = "100" + "0A" + tipo + lotto
        with self._lock:
            self.stato.lotti[prefisso] = cf_utente
        return 200, {"codRegione": "100", "codRagLotto": "0A", "identificativoLotto": tipo, "codLotto": lotto,
                     "codEsito": "00", "esito": "lotto assegnato", "cfmedico": cf_utente}

    def _lotto_di(self, nre: str) -> str | None:
        for prefisso, cf in self.stato.lotti.items():
            if nre.startswith(prefisso) and nre[len(prefisso):].isdigit() and len(nre) == 15:
                return cf
        return None

    def _op_dem_invio_prescritto(self, d, cf_utente, cf_persona):
        inviante = d.get("cfMedico2") or d["cfMedico1"]
        if inviante != cf_utente:
            raise Rifiuto(401, "Unauthorized", f"il medico del token ({cf_utente}) non è quello che invia ({inviante})")
        cf_ass = d.get("codiceAss") or ""
        if not _CF.fullmatch(cf_ass):
            # in chiaro: un CF cifrato (base64 di 344 caratteri) qui non passa
            return 200, self._errore("codEsitoInserimento", "1002", "codice assistito non valido (va in chiaro)")
        if cf_ass != cf_persona:
            raise Rifiuto(401, "Unauthorized", "person_id del token diverso dal codice assistito")
        nre = d.get("nre") or ""
        if not nre:
            return 200, self._errore("codEsitoInserimento", "1100", "NRE obbligatorio nel SAR Umbria")
        titolare_lotto = self._lotto_di(nre)
        if titolare_lotto is None or titolare_lotto not in (d["cfMedico1"], inviante):
            return 200, self._errore("codEsitoInserimento", "1101", "NRE non appartenente a un lotto del medico")
        t2 = d.get("testata2") or ""
        if t2.upper().startswith("SMARTCUP") and (d["tipoPrescrizione"] != "P" or not _SMARTCUP.fullmatch(t2) or len(t2) > 256):
            return 200, self._errore("codEsitoInserimento", "1200", "testata2 SmartCUP non valida")
        with self._lock:
            if nre in self.stato.usati:
                return 200, self._errore("codEsitoInserimento", "1102", "NRE già utilizzato")
            self.stato.usati.add(nre)
            n = len(self.stato.ricette) + 1
            ricetta = RicettaFinta(nre, d["cfMedico1"], inviante, cf_ass, d["tipoPrescrizione"], d["dataCompilazione"],
                                   str(n).zfill(30), d)
            self.stato.ricette[nre] = ricetta
        guasto = self.guasti.get("dem-invio-prescritto")
        if guasto is not None:
            stato, accettata = guasto
            if not accettata:
                with self._lock:
                    del self.stato.ricette[nre]  # il SAC non l'ha vista; l'NRE resta comunque bruciato
            return stato, {"type": "mw/gateway", "title": "BadGateway" if stato == 502 else "GatewayTimeout",
                           "status": stato, "detail": "il SAC non ha risposto in tempo"}
        pdf = b"%PDF-1.4\n% promemoria SINTETICO del server finto\n%%EOF\n"
        return 200, {"nre": nre, "codAutenticazione": ricetta.codice_aut,
                     "dataInserimento": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "codEsitoInserimento": "0000", "flagPromemoria": "1",
                     "pdfPromemoria": base64.b64encode(pdf).decode("ascii")}

    def _ricetta_del_medico(self, d, cf_utente, cf_persona, chiave_esito):
        if d["cfMedico"] != cf_utente:
            raise Rifiuto(401, "Unauthorized", "cfMedico diverso dall'utente del token")
        r = self.stato.ricette.get(d["nre"])
        if r is None or cf_utente not in (r.cf_medico_titolare, r.cf_medico_inviante):
            return None, self._errore(chiave_esito, "5005", "NRE inesistente o di un altro medico", nre=d["nre"])
        if r.cf_assistito != cf_persona:
            raise Rifiuto(401, "Unauthorized", "person_id del token diverso dall'assistito della ricetta")
        return r, None

    def _op_dem_visualizza_prescritto(self, d, cf_utente, cf_persona):
        r, errore = self._ricetta_del_medico(d, cf_utente, cf_persona, "codEsitoVisualizzazione")
        if errore:
            return 200, errore
        out = {k: v for k, v in r.corpo.items() if k not in ("pinCode",)}
        out.update({"statoProcesso": "4" if r.annullata else "3", "codAutenticazione": r.codice_aut,
                    "dataInserimento": r.data, "codEsitoVisualizzazione": "0000"})
        return 200, out

    def _op_dem_annulla_prescritto(self, d, cf_utente, cf_persona):
        r, errore = self._ricetta_del_medico(d, cf_utente, cf_persona, "codEsitoAnnullamento")
        if errore:
            return 200, errore
        if r.annullata:
            return 200, self._errore("codEsitoAnnullamento", "1120", "ricetta già annullata", nre=r.nre)
        r.annullata = True
        return 200, {"nre": r.nre, "codEsitoAnnullamento": "0000"}

    def _op_dem_nre_utilizzati(self, d, cf_utente, _):
        if d["cfMedico"] != cf_utente:
            raise Rifiuto(401, "Unauthorized", "cfMedico diverso dall'utente del token")
        if not d.get("nre") and not (d.get("dataCompilazioneRicettaDal") and d.get("dataCompilazioneRicettaAl")):
            return 200, {"codEsitoInterrogaNreUtilizzati": "9999", "elencoErroriRicette": {"erroreRicetta": [
                {"codEsito": "1150", "esito": "servono NRE o intervallo di date", "tipoErrore": "E"}]}}
        record = []
        for r in self.stato.ricette.values():
            if cf_utente not in (r.cf_medico_titolare, r.cf_medico_inviante):
                continue
            if d.get("nre") and r.nre != d["nre"]:
                continue
            if d.get("tipoPrescr") and r.tipo != d["tipoPrescr"]:
                continue
            if d.get("dataCompilazioneRicettaDal") and not d["dataCompilazioneRicettaDal"] <= r.data <= d["dataCompilazioneRicettaAl"]:
                continue
            record.append({"nre": r.nre, "cfMedico": r.cf_medico_titolare, "tipoPrescrizione": r.tipo,
                           "dataCompilazioneRicetta": r.data, "cfAssistito": r.cf_assistito, "provenienza": "0",
                           "lotto": r.nre[:-3], "codAutenticazione": r.codice_aut})
        return 200, {"codEsitoInterrogaNreUtilizzati": "0000", "elencoNreUtilRecord": {"nreUtilRecord": record}}

    def _op_sostituzione_medico(self, d, cf_utente, _):
        if d["cfMedicoTitolare"] != cf_utente:
            raise Rifiuto(401, "Unauthorized", "la sostituzione la dichiara il titolare (utente del token)")
        if d["pwd"] != "":
            raise Rifiuto(400, "InvalidRequestContent", "pwd: vuota (autenticazione con i JWT)")
        for k in ("dataInizioSostituzione", "dataFineSostituzione"):
            if not re.fullmatch(r"\d{8}", d[k]):
                raise Rifiuto(400, "InvalidRequestContent", f"{k}: aaaammgg")
        if d["dataFineSostituzione"] < d["dataInizioSostituzione"] or d["cfMedicoTitolare"] == d["cfMedicoSostituto"]:
            return 200, {"codEsitoInserimento": "9999", "elencoErrori": {"errore": [
                {"codEsito": "2001", "esito": "periodo o medici non validi", "tipoErrore": "E"}]}}
        self.stato.sostituzioni.append(d)
        return 200, {"codEsitoInserimento": "0000", "dataInserimento": _dt.date.today().strftime("%Y%m%d")}


class _Gestore(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # silenzioso nei test
        pass

    def _cn_tls(self) -> str | None:
        cert = self.connection.getpeercert()  # type: ignore[attr-defined]
        for rdn in (cert or {}).get("subject", ()):
            for k, v in rdn:
                if k == "commonName":
                    return v
        return None

    def _rispondi(self, metodo: str) -> None:
        corpo = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        finto: ServerUmbria = self.server.finto  # type: ignore[attr-defined]
        try:
            stato, risposta = finto.gestisci(metodo, self.path, self.headers, corpo, self._cn_tls())
        except Exception as e:  # il server finto non deve morire
            stato, risposta = 500, json.dumps({"type": "mw/errore", "title": "InternalServerError", "status": 500,
                                               "detail": repr(e)}).encode()
        tipo = "application/json" if 200 <= stato < 300 else "application/problem+json"
        self.send_response(stato)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(risposta)))
        self.end_headers()
        self.wfile.write(risposta)

    def do_POST(self):  # noqa: N802
        self._rispondi("POST")

    def do_GET(self):  # noqa: N802
        self._rispondi("GET")


# ------------------------------------------------------------------ materiale di prova


@dataclass(frozen=True)
class MaterialeUmbria:
    ca: Path
    certificato_server: Path
    chiave_server: Path
    autenticazione: tuple[Path, Path]  # certificato e chiave di AUTENTICAZIONE (mTLS) del «produttore»
    firma_p12: Path  # PKCS#12 del certificato di FIRMA dei JWT (password b"pw")
    firma_estranea_p12: Path  # stesso formato, ma NON emesso dalla CA: il server deve rifiutarlo
    cn_firma: str

    def contesto_client(self, con_certificato: bool = True) -> ssl.SSLContext:
        ctx = ssl.create_default_context(cafile=str(self.ca))
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        if con_certificato:
            ctx.load_cert_chain(str(self.autenticazione[0]), str(self.autenticazione[1]))
        return ctx


def materiale_umbria(cartella: Path, cn_produttore: str = "S1#100#VARCOPROVA") -> MaterialeUmbria:
    """CA, server (127.0.0.1), certificato di autenticazione e di firma del «produttore» (CN come
    nell'esempio della wiki, ma di PROVA), più un certificato di firma estraneo alla CA."""
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    cartella.mkdir(parents=True, exist_ok=True)
    adesso = _dt.datetime.now(_dt.timezone.utc)

    def chiave():
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def nome(cn: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"), x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    def emetti(cn, k, ca_k, ca_nome, *, ca=False, san=None, eku=None):
        b = (x509.CertificateBuilder().subject_name(nome(cn)).issuer_name(ca_nome).public_key(k.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(adesso - _dt.timedelta(days=1))
             .not_valid_after(adesso + _dt.timedelta(days=30))
             .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_k.public_key()), critical=False))
        if san:
            b = b.add_extension(x509.SubjectAlternativeName(san), critical=False)
        if eku:
            b = b.add_extension(x509.ExtendedKeyUsage(eku), critical=False)
        if ca:
            b = b.add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        return b.sign(ca_k, hashes.SHA256())

    def pem(base, cert, k):
        pc, pk = cartella / f"{base}.pem", cartella / f"{base}.key"
        pc.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        pk.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
        return pc, pk

    def p12(base, cert, k):
        p = cartella / f"{base}.p12"
        p.write_bytes(pkcs12.serialize_key_and_certificates(b"prova", k, cert, None,
                                                            serialization.BestAvailableEncryption(b"pw")))
        return p

    ca_k = chiave()
    ca_nome = nome("CA DI PROVA Varco (Umbria finto)")
    ca_c = emetti("CA DI PROVA Varco (Umbria finto)", ca_k, ca_k, ca_nome, ca=True)
    ca_p, _ = pem("ca", ca_c, ca_k)
    srv_k = chiave()
    srv_c = emetti("127.0.0.1", srv_k, ca_k, ca_nome,
                   san=[x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))],
                   eku=[ExtendedKeyUsageOID.SERVER_AUTH])
    srv_p, srv_kp = pem("server", srv_c, srv_k)
    aut_k = chiave()
    aut = pem("produttore-auth", emetti(cn_produttore, aut_k, ca_k, ca_nome, eku=[ExtendedKeyUsageOID.CLIENT_AUTH]), aut_k)
    sig_k = chiave()
    firma = p12("produttore-sign", emetti(cn_produttore, sig_k, ca_k, ca_nome), sig_k)
    est_k = chiave()
    estranea = p12("estraneo-sign", emetti(cn_produttore, est_k, est_k, nome(cn_produttore)), est_k)
    return MaterialeUmbria(ca_p, srv_p, srv_kp, aut, firma, estranea, cn_produttore)


# ------------------------------------------------------------------ risposte sintetiche per la suite


def risposte_sintetiche() -> dict[str, bytes]:
    """Le risposte JSON di conformita/risposte/umbria/ (SINTETICHE: la specifica non ne pubblica di reali,
    a parte gli schemi dell'OpenAPI). Scritte dalla riga di comando: vedi sotto."""
    pdf = base64.b64encode(b"%PDF-1.4\n% promemoria SINTETICO\n%%EOF\n").decode("ascii")
    j = lambda d: (json.dumps(d, ensure_ascii=False, indent=2) + "\n").encode("utf-8")  # noqa: E731
    errore = lambda chiave, codice, testo, **x: j({chiave: "9999", **x, "elencoErroriRicette": {"erroreRicetta": [  # noqa: E731
        {"codEsito": codice, "esito": testo, "progPresc": "0", "tipoErrore": "E"}]}})
    return {
        "lotto_ok_1000.json": j({"codRegione": "100", "codRagLotto": "0A", "identificativoLotto": "1",
                                 "codLotto": "000001", "codEsito": "00", "esito": "lotto assegnato",
                                 "cfmedico": "PROVAX00X00X000Y"}),
        "lotto_ok_100.json": j({"codRegione": "100", "codRagLotto": "0A", "identificativoLotto": "0",
                                "codLotto": "0000002", "codEsito": "00", "esito": "lotto assegnato",
                                "cfmedico": "PROVAX00X00X000Y"}),
        "lotto_rifiuto.json": j({"codEsito": "99", "esito": "identificativo lotto non valido"}),
        "lotto_incompleto.json": j({"codRegione": "100", "codEsito": "00", "esito": "lotto assegnato"}),
        "invio_ok.json": j({"nre": "1000A1000001000", "codAutenticazione": "0" * 29 + "1",
                            "dataInserimento": "2026-10-03 11:00:01", "codEsitoInserimento": "0000",
                            "flagPromemoria": "1", "pdfPromemoria": pdf}),
        "invio_avviso_0001.json": j({"nre": "1000A1000001001", "codAutenticazione": "0" * 29 + "2",
                                     "dataInserimento": "2026-10-03 11:05:01", "codEsitoInserimento": "0001",
                                     "elencoErroriRicette": {"erroreRicetta": [
                                         {"codEsito": "1024", "esito": "avviso di prova", "progPresc": "0", "tipoErrore": "W"}]},
                                     "pdfPromemoria": pdf}),
        "invio_rifiuto_1101.json": errore("codEsitoInserimento", "1101", "NRE non appartenente a un lotto del medico"),
        "invio_quantita_numero.json": j({"codEsitoInserimento": 0, "nre": "1000A1000001002"}),
        "visualizza_ok.json": j({"nre": "1000A1000001000", "cfMedico1": "PROVAX00X00X000Y", "codRegione": "100",
                                 "codASLAo": "201", "codSpecializzazione": "F", "tipoPrescrizione": "F",
                                 "dataCompilazione": "2026-10-03 11:00:00", "tipoVisita": "A",
                                 "elencoDettagliPrescrizioni": {"dettaglioPrescrizione": [
                                     {"codGruppoEquival": "CJA", "descrGruppoEquival": "GRUPPO DI PROVA", "quantita": "1"}]},
                                 "statoProcesso": "3", "codAutenticazione": "0" * 29 + "1",
                                 "dataInserimento": "2026-10-03 11:00:01", "codEsitoVisualizzazione": "0000"}),
        "visualizza_5005.json": errore("codEsitoVisualizzazione", "5005", "NRE inesistente", nre="1000A1000001999"),
        "annulla_ok.json": j({"nre": "1000A1000001000", "codEsitoAnnullamento": "0000"}),
        "annulla_senza_nre.json": j({"codEsitoAnnullamento": "0000"}),
        "nre_utilizzati_ok.json": j({"codEsitoInterrogaNreUtilizzati": "0000", "elencoNreUtilRecord": {"nreUtilRecord": [
            {"nre": "1000A1000001000", "cfMedico": "PROVAX00X00X000Y", "tipoPrescrizione": "F",
             "dataCompilazioneRicetta": "2026-10-03 11:00:00", "provenienza": "0", "lotto": "1000A1000001"}]}}),
        "sostituzione_ok.json": j({"codEsitoInserimento": "0000", "dataInserimento": "20261003"}),
    }


if __name__ == "__main__":
    import sys

    destinazione = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "conformita" / "risposte" / "umbria")
    destinazione.mkdir(parents=True, exist_ok=True)
    for nome_file, dati in risposte_sintetiche().items():
        (destinazione / nome_file).write_bytes(dati)
        print(destinazione / nome_file)
