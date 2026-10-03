# SPDX-License-Identifier: EUPL-1.2
"""Server SIST FINTO, in locale, per provare il client del kit senza chiamare la Regione Puglia.

Non è il SIST e non ne imita la logica sanitaria: risponde con buste costruite da noi sullo
schema ufficiale (`wsdl-pddasl/CVPService.xsd`, Specifiche SIST v4.03.27), perché le
specifiche pubblicano esempi di RICHIESTA ma nessuna risposta del componente CVP.

Cosa controlla davvero su ogni richiesta, come farebbe la PDD ASL:
  - SOAP 1.1 e SOAPAction coerente con l'operazione nel Body;
  - firma WS-Security del Timestamp (exclusive C14N di libxml2, RSA-SHA1, certificato del
    BinarySecurityToken) e finestra temporale;
  - il CF del certificato è quello di datiOperatore (altrimenti Fault 000231);
  - applDigest = base64(SHA-1(nonce + created + codice applicativo)) (altrimenti 000220);
  - il Body valida contro CVPService.xsd, se gli si passa il percorso dello schema;
  - setRegistraPrescrizione: base64 -> p7m CAdES, firma verificata, firmatario = operatore
    (000271), CDA valido contro lo schema CDA R2, id del CDA = NRE dato da chkPrescrizione,
    autore = firmatario (000272), data della firma uguale a quella del CDA (000303) e ora
    successiva (000304); `oscurato` (xs:boolean) e `idPCP` conservati e restituiti.
  - Il CDA contro CDA2_Prescrizione (Fault 000218 "Il CDA non è valido"), con gli OID scritti
    qui dalla specifica e NON presi dal kit (revisione esterna 02/10/2026: client e server
    condividevano lo stesso errore): id e setId secondo IUP o NRE (p. 6 e 19), participant
    ASL per gli assistiti SSN (p. 2), moodCode "PRMS" delle prestazioni (p. 195).
  - getPrescrizioniIdentificate: filtri di periodo, prescrittore, tipologia, assistito e
    stato (javadoc CVP, getPrescrizioniIdentificate); senza un periodo Fault 000061.

Regole di merito finte, solo per esercitare i percorsi d'errore del client (codici dalla
specifica, Appendice C e javadoc): codEsenzione mancante -> anomalia 0007 (C); codice
farmaco "000000000" -> 0048 (C); nota AIFA "999" -> 0053 (C, ma con un avviso 0051 W);
assistito "SAC_GIU" nel CF -> solo IUP senza codice di autenticazione (ricetta rossa): uno IUP
vero, di 13 caratteri (Nota Tecnica IUP, par. 2.2.1), non un NRE.

Uso dai test: `with ServerSIST(...) as s: s.url`. Solo libreria standard, lxml e cryptography
(e asn1crypto, che arriva con pyHanko).
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import http.server
import itertools
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

NS = "www.sist.puglia.it/Schemas/PDD_SIST/SCATEL/"
NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
AZIONE = "urn:sist:pddsasl:bindings:1.0:CVPPortType#"

# Header di risposta con la STRUTTURA dell'esempio ufficiale (par. 5.1.4: Timestamp e firma con
# KeyIdentifier del certificato del server). I valori sono finti e la firma non è verificabile:
# il client non verifica la firma delle risposte (lo dice docs/SAR_PUGLIA.md).
HEADER_RISPOSTA = (
    '<S:Header><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
    'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" S:mustUnderstand="1">'
    '<wsu:Timestamp wsu:Id="_3"><wsu:Created>{creato}</wsu:Created><wsu:Expires>{scade}</wsu:Expires></wsu:Timestamp>'
    '<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#" Id="_1"><ds:SignedInfo>'
    '<ds:CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>'
    '<ds:SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"/>'
    '<ds:Reference URI="#_3"><ds:DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"/>'
    "<ds:DigestValue>RklOVE8tU0VSVkVSLUZJTlRP</ds:DigestValue></ds:Reference></ds:SignedInfo>"
    "<ds:SignatureValue>RklOVE8tU0VSVkVSLUZJTlRPLU5PTi1WRVJJRklDQUJJTEU=</ds:SignatureValue>"
    "<ds:KeyInfo><wsse:SecurityTokenReference><wsse:KeyIdentifier "
    'ValueType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-x509-token-profile-1.0#X509SubjectKeyIdentifier">'
    "RklOVE8=</wsse:KeyIdentifier></wsse:SecurityTokenReference></ds:KeyInfo></ds:Signature></wsse:Security></S:Header>"
)


def busta_risposta(corpo: str, *, adesso: _dt.datetime | None = None) -> bytes:
    adesso = adesso or _dt.datetime.now(_dt.timezone.utc)
    f = "%Y-%m-%dT%H:%M:%SZ"
    head = HEADER_RISPOSTA.format(creato=adesso.strftime(f), scade=(adesso + _dt.timedelta(minutes=5)).strftime(f))
    return (f'<?xml version="1.0" encoding="UTF-8"?><S:Envelope xmlns:S="{NS_SOAP}">{head}'
            f"<S:Body>{corpo}</S:Body></S:Envelope>").encode("utf-8")


def fault(codice: str, testo: str) -> bytes:
    return busta_risposta(
        f"<S:Fault><faultcode>S:Server</faultcode><faultstring>{codice}: {escape(testo)}</faultstring>"
        f'<detail><SoapFaultException xmlns="{NS}"><codice>{codice}</codice>'
        f"<messaggio>{escape(testo)}</messaggio></SoapFaultException></detail></S:Fault>"
    )


def _x(tag: str, valore: str | None) -> str:
    return "" if valore is None else f"<SANITA:{tag}>{escape(valore)}</SANITA:{tag}>"


def risposta(operazione: str, interno: str) -> bytes:
    return busta_risposta(f'<SANITA:{operazione}Response xmlns:SANITA="{NS}"><SANITA:return>{interno}</SANITA:return>'
                          f"</SANITA:{operazione}Response>")


def anomalie(elenco: list[tuple[str, str, str]]) -> str:
    if not elenco:
        return ""
    voci = "".join(
        f"<SANITA:anomalia>{_x('codCriticita', c)}{_x('codice', k)}{_x('descrizione', d)}</SANITA:anomalia>"
        for k, d, c in elenco
    )
    return f"<SANITA:elencoAnomalie>{voci}</SANITA:elencoAnomalie>"


# OID dalla specifica CDA2_Prescrizione R12, scritti qui di proposito (non importati dal kit)
OID_IUP, OID_NRE = "2.16.840.1.113883.2.9.4.3.6", "2.16.840.1.113883.2.9.4.3.8"  # p. 6
OID_SETID_MEF = "2.16.840.1.113883.2.9.2.4.3.20"  # p. 19: NRE o codice di autenticazione
OID_ASL = "2.16.840.1.113883.2.9.4.1.1"
OID_CLASSE_RICETTA, OID_TIPO_RICETTA = "2.16.840.1.113883.2.9.6.1.45", "2.16.840.1.113883.2.9.6.1.47"
OID_LOINC, LOINC_SPECIALISTICA = "2.16.840.1.113883.6.1", "57832-8"
ALFABETO_31 = "0123456789ABCDEFGHILMNXPQRSTUVZ"  # Nota Tecnica IUP, par. 2.2.1


def iup_online(codice_prescrittore: str, progressivo: int) -> str:
    """IUP_OnLine (Nota Tecnica IUP, par. 2.2.1): codice prescrittore e progressivo in base 31
    su 6 simboli ciascuno, più il carattere di controllo = somma dei valori mod 31."""

    def b31(n: int) -> str:
        cifre = ""
        for _ in range(6):
            n, r = divmod(n, 31)
            cifre = ALFABETO_31[r] + cifre
        return cifre

    q = b31(int(codice_prescrittore)) + b31(progressivo)
    return q + ALFABETO_31[sum(ALFABETO_31.index(c) for c in q) % 31]


def booleano_xs(v: str | None) -> bool | None:
    """xs:boolean: true, false, 1, 0 (XML Schema Part 2, 3.2.2). Altro: ValueError."""
    if v is None:
        return None
    if v in ("true", "1"):
        return True
    if v in ("false", "0"):
        return False
    raise ValueError(v)


@dataclass
class PrescrizioneFinta:
    nre: str  # NRE o IUP: il campo IUP della risposta porta l'uno o l'altro
    cf_assistito: str
    operatore: str
    tipologia: str
    data: str
    cda: bytes | None = None
    stato_sist: str = "1"  # 1 prescritta, 0 annullata
    stato_sac: str = "3"  # 3 da erogare, 4 annullata
    codice_prescrittore: str | None = None  # codMedicoPrescrittore di chkPrescrizione
    codice_autenticazione: str | None = None
    iup: bool = False  # True: identificativo regionale (SAC non disponibile)
    oscurato: bool = False
    id_pcp: str | None = None
    # Data di erogazione: il server finto non eroga, quindi resta None (mai erogata) salvo che un
    # test la imposti. Serve ai filtri dataErogazioneDal/Al di getPrescrizioniIdentificate.
    data_erogazione: _dt.date | None = None


@dataclass
class StatoServer:
    codice_applicativo: str
    xsd: object | None = None
    schema_cda: object | None = None
    prescrizioni: dict[str, PrescrizioneFinta] = field(default_factory=dict)
    richieste: list[tuple[str, bytes]] = field(default_factory=list)
    contatore: itertools.count = field(default_factory=lambda: itertools.count(1))
    # forma di xs:boolean nelle risposte: "true"/"false" oppure "1"/"0" (entrambe valide nello schema)
    booleani_numerici: bool = False


def _locale(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _t(el: ET.Element | None, *percorso: str) -> str | None:
    for nome in percorso:
        if el is None:
            return None
        el = next((c for c in el if _locale(c.tag) == nome), None)
    if el is None or el.text is None or not el.text.strip():
        return None
    return el.text.strip()


def verifica_cades(p7m: bytes) -> tuple[bytes, str | None, _dt.datetime]:
    """(contenuto, CF del firmatario, signing-time). Solleva ValueError se la firma non torna o
    se manca l'ora della firma: senza, 000303 e 000304 non si possono controllare."""
    from asn1crypto import cms
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    ci = cms.ContentInfo.load(p7m)
    if ci["content_type"].native != "signed_data":
        raise ValueError("non è una busta CMS SignedData")
    sd = ci["content"]
    contenuto = sd["encap_content_info"]["content"].native
    if contenuto is None:
        raise ValueError("firma staccata: il p7m deve contenere il CDA")
    si = sd["signer_infos"][0]
    attributi = {a["type"].native: a["values"][0] for a in si["signed_attrs"]}
    if attributi["message_digest"].native != hashlib.sha256(contenuto).digest():
        raise ValueError("message digest diverso dal contenuto")
    if "signing_certificate_v2" not in attributi:
        raise ValueError("manca signing-certificate-v2 (CAdES-BES)")
    if "signing_time" not in attributi:
        raise ValueError("manca l'attributo signing-time")
    firma_il = attributi["signing_time"].native
    cert_der = sd["certificates"][0].chosen.dump()
    cert = x509.load_der_x509_certificate(cert_der)
    firmati = b"\x31" + si["signed_attrs"].dump()[1:]  # gli attributi firmati si firmano come SET OF
    cert.public_key().verify(si["signature"].native, firmati, padding.PKCS1v15(), hashes.SHA256())
    import re

    cf = re.search(r"[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]",
                   cert.subject.rfc4514_string().upper())
    return contenuto, (cf.group(0) if cf else None), firma_il


def _ora_roma(d: _dt.datetime) -> _dt.datetime:
    """Ora di Roma senza fuso, come effectiveTime del CDA."""
    try:
        from zoneinfo import ZoneInfo

        return d.astimezone(ZoneInfo("Europe/Rome")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001 - senza tzdata: ora locale
        return d.astimezone().replace(tzinfo=None)


def _difetto_tempi(doc, firma_il: _dt.datetime) -> tuple[str, str] | None:
    """javadoc CVP, setRegistraPrescrizione: 000303 firma di un altro giorno, 000304 firma prima
    della creazione. effectiveTime è "AAAAMMGGhhmmss" ora italiana; il confronto è al secondo."""
    v = doc.find("{urn:hl7-org:v3}effectiveTime").get("value") or ""
    try:
        creato = _dt.datetime.strptime(v[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return "000218", f"Il CDA non è valido: effectiveTime {v!r}"
    firma = _ora_roma(firma_il).replace(microsecond=0)
    if firma.date() != creato.date():
        return "000303", "La data di firma del cda deve essere uguale alla data di creazione dello stesso."
    if firma < creato:
        return "000304", "L'orario di firma del cda deve essere successivo all'orario di creazione dello stesso."
    return None


def _difetto_cda(doc, presc: PrescrizioneFinta) -> str | None:
    """I vincoli di CDA2_Prescrizione che lo schema CDA generico non vede."""
    h = {"h": "urn:hl7-org:v3"}
    ident = doc.find("h:id", h)
    atteso_id = (OID_IUP, "Regione Puglia") if presc.iup else (OID_NRE, "MEF")
    if (ident.get("root"), ident.get("assigningAuthorityName")) != atteso_id:
        return f"id {ident.get('root')}/{ident.get('assigningAuthorityName')}, atteso {atteso_id[0]}/{atteso_id[1]} (p. 6)"
    if presc.codice_autenticazione:
        atteso_set = (OID_SETID_MEF, presc.codice_autenticazione, "MEF")
    elif presc.iup:
        atteso_set = (OID_IUP, presc.nre, "Regione Puglia")
    else:
        atteso_set = (OID_SETID_MEF, presc.nre, "MEF")
    s = doc.find("h:setId", h)
    if s is None or (s.get("root"), s.get("extension"), s.get("assigningAuthorityName")) != atteso_set:
        return "setId non corrisponde all'identificativo della prescrizione (p. 19)"
    tipo = doc.find(f"h:code/h:translation[@codeSystem='{OID_CLASSE_RICETTA}']/h:qualifier/"
                    f"h:value[@codeSystem='{OID_TIPO_RICETTA}']", h)
    if tipo is None:
        return "manca il tipo ricetta (qualifierTipoAss_IT, p. 11)"
    asl = doc.find(f"h:participant/h:associatedEntity[@classCode='GUAR']/h:scopingOrganization/h:id[@root='{OID_ASL}']", h)
    if tipo.get("code") == "IT" and asl is None:
        return "manca l'ASL di residenza, obbligatoria per gli assistiti SSN (p. 2)"
    if doc.find("h:code", h).get("code") == LOINC_SPECIALISTICA:
        for obs in doc.findall("h:component/h:structuredBody/h:component/h:section/h:entry/h:observation", h):
            if obs.get("moodCode") != "PRMS":
                return f"prestazione con moodCode {obs.get('moodCode')!r}, atteso 'PRMS' (p. 195)"
    return None


class _Gestore(http.server.BaseHTTPRequestHandler):
    stato: StatoServer

    def log_message(self, *a):  # silenzioso nei test
        pass

    def _rispondi(self, corpo: bytes, codice: int = 200) -> None:
        self.send_response(codice)
        self.send_header("Content-Type", "text/xml;charset=UTF-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_POST(self):  # noqa: N802
        dati = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.stato.richieste.append((self.headers.get("SOAPAction", ""), dati))
        try:
            corpo, codice = self._gestisci(dati)
        except Exception as e:  # il server finto non deve morire
            corpo, codice = fault("100000", f"Si è verificato un errore nel sistema: {e!r}"), 500
        self._rispondi(corpo, codice)

    def _gestisci(self, dati: bytes) -> tuple[bytes, int]:
        from varco.trasporto.sist import appl_digest, cf_del_certificato
        from varco.trasporto.wssecurity import verifica_security
        from lxml import etree

        wss = verifica_security(dati, adesso=_dt.datetime.now(_dt.timezone.utc))
        if not wss.valida:
            return fault("000265", f"Richiesta non valida: WS-Security ({wss.motivo})"), 500
        radice = ET.fromstring(dati)
        body = radice.find(f"{{{NS_SOAP}}}Body")
        op_el = body[0]
        op = _locale(op_el.tag)
        if op_el.tag != f"{{{NS}}}{op}":
            return fault("000239", "Contenuto applicativo non valido (namespace)"), 500
        if self.headers.get("SOAPAction", "").strip('"') != AZIONE + op:
            return fault("000240", "Il contenuto applicativo non corrisponde all'azione."), 500
        if self.stato.xsd is not None:
            el = etree.fromstring(ET.tostring(op_el))
            if not self.stato.xsd.validate(el):
                return fault("000239", f"Contenuto applicativo non valido: {self.stato.xsd.error_log.last_error}"), 500
        operatore = _t(op_el, "datiOperatore", "codiceFiscale")
        if cf_del_certificato(wss.certificato_der) != operatore:
            return fault("000231", "Il codice fiscale della smart card non corrisponde a quello dell'operatore."), 500
        nonce, creato = _t(op_el, "datiApplicativo", "nonce"), _t(op_el, "datiApplicativo", "created")
        if _t(op_el, "datiApplicativo", "applDigest") != appl_digest(nonce or "", creato or "", self.stato.codice_applicativo):
            return fault("000220", "L'applicativo non è autorizzato ad utilizzare questi servizi."), 500
        metodo = getattr(self, "_op_" + op, None)
        if metodo is None:
            return fault("000242", "Servizio non riconosciuto."), 500
        return metodo(op_el, operatore)

    # ------------------------------------------------------------------ operazioni

    def _op_chkPrescrizione(self, el, operatore):  # noqa: N802
        cf = _t(el, "assistito", "codIdentificativoAssistito") or ""
        elenco: list[tuple[str, str, str]] = []
        if not _t(el, "codEsenzione"):
            elenco.append(("0007", "Esenzione non specificata", "C"))
        prestazioni = next((c for c in el if _locale(c.tag) == "elencoPrestazioni"), None)
        for p in prestazioni if prestazioni is not None else ():
            if _t(p, "codPrestazione") == "000000000":
                elenco.append(("0048", "Codice farmaco inesistente", "C"))
            if _t(p, "notaCUF") == "999":
                elenco.append(("0051", "Nota AIFA non specificata per farmaco di fascia \"C\"", "W"))
                elenco.append(("0053", "Nota AIFA non valida per il farmaco", "C"))
        if any(c == "C" for _, _, c in elenco):
            return risposta("chkPrescrizione", anomalie(elenco)), 200
        n = next(self.stato.contatore)
        prescrittore = _t(el, "codMedicoPrescrittore") or "0"
        presc = PrescrizioneFinta("", cf, operatore, _t(el, "tipologia") or "", _t(el, "dataPrescrizione") or "",
                                  codice_prescrittore=prescrittore)
        if "SAC_GIU" in cf:  # SAC non disponibile: lo IUP regionale, niente NRE né codice di autenticazione
            presc.nre, presc.iup = iup_online(prescrittore, n), True
            self.stato.prescrizioni[presc.nre] = presc
            return risposta("chkPrescrizione", anomalie(elenco) + _x("IUP", presc.nre)), 200
        presc.nre = nre = f"1600A{n:010d}"
        codice = f"{_dt.datetime.now():%d%m%Y%H%M%S}{n:016d}"
        presc.codice_autenticazione = codice
        self.stato.prescrizioni[nre] = presc
        comunicazioni = ("<SANITA:elencoComunicazioni><SANITA:comunicazione><SANITA:codice>0199</SANITA:codice>"
                         "<SANITA:messaggio>COGNOME_MEDICO=PRO</SANITA:messaggio></SANITA:comunicazione>"
                         "<SANITA:comunicazione><SANITA:codice>0198</SANITA:codice>"
                         "<SANITA:messaggio>NOME_MEDICO=VA</SANITA:messaggio></SANITA:comunicazione></SANITA:elencoComunicazioni>")
        return risposta("chkPrescrizione", anomalie(elenco) + _x("IUP", nre) + _x("codAutenticazione", codice) + comunicazioni), 200

    def _op_setRegistraPrescrizione(self, el, operatore):  # noqa: N802
        from lxml import etree

        try:
            p7m = base64.b64decode(_t(el, "prescrizione") or "", validate=True)
            cda, firmatario, firma_il = verifica_cades(p7m)
        except Exception as e:
            return fault("000219", f"Errore nel processo di firma del documento o nella definizione del firmatario: {e}"), 500
        if firmatario != operatore:
            return fault("000271", "Il firmatario del documento è diverso dall'operatore che ha richiesto la registrazione."), 500
        doc = etree.fromstring(cda)
        if self.stato.schema_cda is not None and not self.stato.schema_cda.validate(doc):
            return fault("900000", f"Il documento CDA non è conforme allo schema: {self.stato.schema_cda.error_log.last_error}"), 500
        h = {"h": "urn:hl7-org:v3"}
        nre = doc.find("h:id", h).get("extension")
        autore = doc.find("h:author/h:assignedAuthor/h:id", h).get("extension")
        if autore != firmatario:
            return fault("000272", "L'autore del documento è diverso dal firmatario."), 500
        presc = self.stato.prescrizioni.get(nre)
        if presc is None:
            return fault("000004", "Non ci sono elementi corrispondenti ai criteri di ricerca inseriti."), 500
        difetto = _difetto_cda(doc, presc)
        if difetto:
            return fault("000218", f"Il CDA non è valido: {difetto}"), 500
        tempi = _difetto_tempi(doc, firma_il)
        if tempi:
            return fault(*tempi), 500
        try:
            oscurato = booleano_xs(_t(el, "oscurato"))
        except ValueError:
            return fault("000005", "Dato non valido (oscurato)"), 500
        presc.cda = cda
        presc.oscurato, presc.id_pcp = bool(oscurato), _t(el, "idPCP")
        return risposta("setRegistraPrescrizione", _x("esito", "TRUE")), 200

    def _op_setAnnullaPrescrizione(self, el, operatore):  # noqa: N802
        presc = self.stato.prescrizioni.get(_t(el, "identificativoPrescrizione") or "")
        if presc is None:
            return fault("000004", "Non ci sono elementi corrispondenti ai criteri di ricerca inseriti."), 500
        if presc.operatore != operatore:
            return fault("000280", "Non è possibile annullare la prescrizione emessa da un altro medico."), 500
        if presc.stato_sist != "1":
            return fault("000279", "Non è possibile annullare la prescrizione perché non è in stato di Prescritta."), 500
        presc.stato_sist, presc.stato_sac = "0", "4"
        return risposta("setAnnullaPrescrizione", _x("esito", "TRUE")), 200

    def _op_getPrescrizioneIdentificata(self, el, operatore):  # noqa: N802
        presc = self.stato.prescrizioni.get(_t(el, "identificativoRicetta") or "")
        if presc is None or presc.cf_assistito != _t(el, "codiceFiscale"):
            return fault("000004", "Prescrizione non identificata"), 500
        interno = ""
        if presc.cda is not None:
            interno += _x("cdaInstance", presc.cda.decode("utf-8"))
        interno += (f"<SANITA:prescrizione>{_x('IUP', presc.nre)}{_x('statoPrescrizione', presc.stato_sist)}"
                    f"{_x('tipoPrescrizione', presc.tipologia)}</SANITA:prescrizione>")
        vero, falso = ("1", "0") if self.stato.booleani_numerici else ("true", "false")
        interno += _x("statoRicetta", presc.stato_sac) + _x("oscurato", vero if presc.oscurato else falso)
        return risposta("getPrescrizioneIdentificata", interno), 200

    def _op_getPrescrizioniIdentificate(self, el, operatore):  # noqa: N802
        """Filtri della javadoc CVP: codFiscale, codPrescrittore, statoPrescr, tipoPrescr (4 = tutti),
        dataEmissioneDal/Al e dataErogazioneDal/Al (GG/MM/AAAA, estremi inclusi). Senza periodo: 000061.

        javadoc cvp/ws/CVP.html, getPrescrizioniIdentificate, parametri dataErogazioneDal/Al:
        "Seleziona tutte le prescrizioni con data di di erogazione uguale o successiva [precedente]
        alla data specificata". Una prescrizione mai erogata non ha data di erogazione: con un
        filtro di erogazione NON si seleziona (revisione esterna giro 2, 3-sar-puglia N3; prima
        le date di erogazione si leggevano e si ignoravano)."""

        def data(nome: str) -> _dt.date | None:
            v = _t(el, nome)
            return None if v is None else _dt.datetime.strptime(v, "%d/%m/%Y").date()

        try:
            dal, al = data("dataEmissioneDal"), data("dataEmissioneAl")
            erog = (data("dataErogazioneDal"), data("dataErogazioneAl"))
        except ValueError:
            return fault("000005", "Dato non valido"), 500
        if dal is None and al is None and erog == (None, None):
            return fault("000061", "Valorizzare almeno un periodo di ricerca"), 500
        cf, prescrittore = _t(el, "codFiscale"), _t(el, "codPrescrittore")
        stato, tipo = _t(el, "statoPrescr"), _t(el, "tipoPrescr")

        def passa(p: PrescrizioneFinta) -> bool:
            emessa = _dt.datetime.strptime(p.data[:10], "%d/%m/%Y").date()
            return (p.operatore == operatore
                    and (cf is None or p.cf_assistito == cf)
                    and (prescrittore is None or p.codice_prescrittore == prescrittore)
                    and (stato is None or p.stato_sist == stato)
                    and (tipo in (None, "4") or p.tipologia == tipo)
                    and (dal is None or emessa >= dal) and (al is None or emessa <= al)
                    and (erog == (None, None) or (
                        p.data_erogazione is not None
                        and (erog[0] is None or p.data_erogazione >= erog[0])
                        and (erog[1] is None or p.data_erogazione <= erog[1]))))

        voci = "".join(
            f"<SANITA:prescrizione>{_x('codAssistito', p.cf_assistito)}{_x('codRicetta', p.nre)}"
            f"{_x('dataEmissione', p.data[:10])}{_x('IUP', p.nre)}</SANITA:prescrizione>"
            for p in self.stato.prescrizioni.values()
            if passa(p)
        )
        return risposta("getPrescrizioniIdentificate", f"<SANITA:elenco>{voci}</SANITA:elenco>"), 200


class ServerSIST:
    """Server finto su 127.0.0.1, porta libera. `xsd_cvp`: percorso di CVPService.xsd (facoltativo)."""

    def __init__(self, codice_applicativo: str, *, xsd_cvp: Path | None = None, valida_cda: bool = True):
        from lxml import etree

        xsd = etree.XMLSchema(etree.parse(str(xsd_cvp))) if xsd_cvp else None
        schema_cda = None
        if valida_cda:
            from varco.fse.validazione import _schema_cda

            schema_cda = _schema_cda()
        self.stato = StatoServer(codice_applicativo, xsd, schema_cda)
        gestore = type("Gestore", (_Gestore,), {"stato": self.stato})
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), gestore)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/aslba_test"

    def __enter__(self) -> "ServerSIST":
        self._thread.start()
        return self

    def __exit__(self, *a) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
