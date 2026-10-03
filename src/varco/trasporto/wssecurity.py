# SPDX-License-Identifier: EUPL-1.2
"""WS-Security per la PDD ASL del SIST (Regione Puglia): firma X.509 del Timestamp.

Cosa chiede il SIST (Specifiche di integrazione v4.03.27, par. 5.1.1, e policy nei WSDL
`wsdl-pddasl/*.wsdl`: AsymmetricBinding, AlgorithmSuite Basic128, IncludeTimestamp,
InitiatorToken X509 "AlwaysToRecipient", WssX509V3Token10):

- nell'header SOAP un `wsse:Security` con `wsu:Timestamp` (Created, Expires in UTC,
  formato AAAA-MM-GGThh:mm:ssZ), un `wsse:BinarySecurityToken` col certificato X.509 del
  chiamante in base64 (DER) e una `ds:Signature`;
- la firma deve coprire **almeno** il Timestamp; canonicalizzazione exclusive C14N,
  digest SHA-1, firma RSA-SHA1 (è quello che dice la policy Basic128 e che mostrano gli
  esempi ufficiali: SHA-1 è vecchio, ma lo decide il server, non noi);
- il certificato è quello della CNS dell'operatore (la PDD ASL lo cerca nel suo truststore).

Come lo facciamo senza librerie XML-DSig: il Timestamp e il SignedInfo si scrivono
**già in forma canonica** (exclusive C14N 1.0, senza InclusiveNamespaces): un solo
namespace visibilmente usato e dichiarato sull'elemento stesso, attributi in ordine,
nessuno spazio, elementi vuoti come coppia apertura/chiusura. I byte che firmiamo sono
quindi esattamente quelli che il server ricava canonicalizzando il nodo ricevuto. I test
lo verificano con un'implementazione indipendente (libxml2 via lxml, exclusive=True).

La chiave privata non passa da qui: `ChiaveOperatore` è un protocollo. In esercizio la
firma la fa la smart card (PKCS#11) o il dispositivo del medico; `ChiavePKCS12` serve
alle prove con un certificato di test.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Protocol
from xml.sax.saxutils import escape

NS_WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
NS_WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
NS_DS = "http://www.w3.org/2000/09/xmldsig#"
NS_SOAPENV = "http://schemas.xmlsoap.org/soap/envelope/"  # lo stesso di soap.NS_SOAPENV
EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
RSA_SHA1 = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
DIGEST_SHA1 = "http://www.w3.org/2000/09/xmldsig#sha1"
VALUE_X509V3 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-x509-token-profile-1.0#X509v3"
ENCODING_BASE64 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"

VALIDITA_DEFAULT_S = 300  # come l'esempio Java della specifica: Expires = Created + 5 minuti


class ChiaveOperatore(Protocol):
    """Il certificato e la chiave dell'operatore (in esercizio: la CNS del medico)."""

    def certificato_der(self) -> bytes: ...

    def firma_rsa_sha1(self, dati: bytes) -> bytes:
        """Firma PKCS#1 v1.5 con SHA-1 di `dati` (i byte canonici del SignedInfo)."""
        ...


@dataclass
class ChiavePKCS12:
    """Certificato e chiave da un file .p12. Per le prove con certificati di test, o per chi
    ha davvero la propria chiave in un file (meglio una smart card)."""

    percorso: str
    password: bytes | None = field(default=None, repr=False)

    def _carica(self):
        from cryptography.hazmat.primitives.serialization import pkcs12

        with open(self.percorso, "rb") as f:
            chiave, cert, _ = pkcs12.load_key_and_certificates(f.read(), self.password)
        if chiave is None or cert is None:
            raise ValueError(f"{self.percorso}: servono chiave privata e certificato")
        return chiave, cert

    def certificato_der(self) -> bytes:
        from cryptography.hazmat.primitives.serialization import Encoding

        return self._carica()[1].public_bytes(Encoding.DER)

    def firma_rsa_sha1(self, dati: bytes) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        return self._carica()[0].sign(dati, padding.PKCS1v15(), hashes.SHA1())  # noqa: S303 - imposto dalla policy


def _utc(t: _dt.datetime) -> str:
    return t.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp_canonico(id_timestamp: str, creato: _dt.datetime, scade: _dt.datetime) -> str:
    """`wsu:Timestamp` già in exclusive C14N."""
    return (
        f'<wsu:Timestamp xmlns:wsu="{NS_WSU}" wsu:Id="{id_timestamp}">'
        f"<wsu:Created>{_utc(creato)}</wsu:Created>"
        f"<wsu:Expires>{_utc(scade)}</wsu:Expires>"
        "</wsu:Timestamp>"
    )


def signed_info_canonico(riferimento: str, digest_b64: str) -> str:
    """`ds:SignedInfo` già in exclusive C14N (un solo Reference, al Timestamp)."""
    return (
        f'<ds:SignedInfo xmlns:ds="{NS_DS}">'
        f'<ds:CanonicalizationMethod Algorithm="{EXC_C14N}"></ds:CanonicalizationMethod>'
        f'<ds:SignatureMethod Algorithm="{RSA_SHA1}"></ds:SignatureMethod>'
        f'<ds:Reference URI="#{riferimento}">'
        f'<ds:Transforms><ds:Transform Algorithm="{EXC_C14N}"></ds:Transform></ds:Transforms>'
        f'<ds:DigestMethod Algorithm="{DIGEST_SHA1}"></ds:DigestMethod>'
        f"<ds:DigestValue>{digest_b64}</ds:DigestValue>"
        "</ds:Reference>"
        "</ds:SignedInfo>"
    )


def intestazione_security(
    chiave: ChiaveOperatore,
    *,
    prefisso_soap: str = "soapenv",
    adesso: _dt.datetime | None = None,
    validita_s: int = VALIDITA_DEFAULT_S,
) -> str:
    """L'elemento `wsse:Security` da mettere nell'header SOAP (testo XML).

    `prefisso_soap` è il prefisso con cui la busta dichiara il namespace SOAP 1.1
    (serve per `mustUnderstand`).
    """
    adesso = adesso or _dt.datetime.now(_dt.timezone.utc)
    if adesso.tzinfo is None:
        raise ValueError("adesso: serve un datetime con fuso orario")
    id_ts = f"TS-{uuid.uuid4()}"
    id_cert = f"X509-{uuid.uuid4()}"
    ts = timestamp_canonico(id_ts, adesso, adesso + _dt.timedelta(seconds=validita_s))
    digest = base64.b64encode(hashlib.sha1(ts.encode("utf-8")).digest()).decode()  # noqa: S324 - policy Basic128
    signed_info = signed_info_canonico(id_ts, digest)
    firma = base64.b64encode(chiave.firma_rsa_sha1(signed_info.encode("utf-8"))).decode()
    cert = base64.b64encode(chiave.certificato_der()).decode()
    return (
        f'<wsse:Security xmlns:wsse="{NS_WSSE}" xmlns:wsu="{NS_WSU}" {prefisso_soap}:mustUnderstand="1">'
        f"{ts}"
        f'<wsse:BinarySecurityToken EncodingType="{ENCODING_BASE64}" ValueType="{VALUE_X509V3}" '
        f'wsu:Id="{id_cert}">{cert}</wsse:BinarySecurityToken>'
        f'<ds:Signature xmlns:ds="{NS_DS}">'
        f"{signed_info}"
        f"<ds:SignatureValue>{firma}</ds:SignatureValue>"
        "<ds:KeyInfo><wsse:SecurityTokenReference>"
        f'<wsse:Reference URI="#{escape(id_cert)}" ValueType="{VALUE_X509V3}"></wsse:Reference>'
        "</wsse:SecurityTokenReference></ds:KeyInfo>"
        "</ds:Signature>"
        "</wsse:Security>"
    )


# ------------------------------------------------------------------ verifica (prove, server finto)


@dataclass(frozen=True)
class EsitoVerificaWSS:
    valida: bool
    motivo: str | None
    certificato_der: bytes | None = field(default=None, repr=False)
    creato: str | None = None
    scade: str | None = None


def verifica_security(busta: bytes, *, adesso: _dt.datetime | None = None) -> EsitoVerificaWSS:
    """Verifica la firma WS-Security di una busta SOAP come farebbe il server.

    Usa lxml (exclusive C14N di libxml2, indipendente dal modo in cui la busta è stata
    scritta) e `cryptography`. Controlla: digest del Timestamp, firma del SignedInfo col
    certificato del BinarySecurityToken, finestra Created/Expires letta dall'unico Timestamp
    di Security, che deve essere quello firmato. NON controlla la catena
    del certificato (quella la fa il truststore della PDD ASL). Serve alle prove e al
    server finto; non è un componente di sicurezza del client.
    """
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from lxml import etree

    doc = etree.fromstring(busta)
    ns = {"wsse": NS_WSSE, "wsu": NS_WSU, "ds": NS_DS, "soapenv": NS_SOAPENV}
    # SIST §5.1.1: il Timestamp firmato sta nell'HEADER SOAP. Un solo wsse:Security, figlio diretto di
    # soapenv:Header; altrove (Body compreso) non se ne accettano (issue #5: Security spostato nel Body).
    if doc.tag != f"{{{NS_SOAPENV}}}Envelope":
        return EsitoVerificaWSS(False, "la busta non è un soapenv:Envelope")
    nell_header = doc.findall("soapenv:Header/wsse:Security", ns)
    ovunque = doc.findall(".//wsse:Security", ns)
    if len(nell_header) != 1:
        return EsitoVerificaWSS(False, f"serve esattamente un wsse:Security in soapenv:Header (trovati {len(nell_header)})")
    if len(ovunque) != 1:
        return EsitoVerificaWSS(False, "wsse:Security fuori da soapenv:Header")
    sec = nell_header[0]
    firma = sec.find("ds:Signature", ns)
    token = sec.find("wsse:BinarySecurityToken", ns)
    if firma is None or token is None:
        return EsitoVerificaWSS(False, "mancano Signature o BinarySecurityToken")
    signed_info = firma.find("ds:SignedInfo", ns)
    if signed_info.find("ds:CanonicalizationMethod", ns).get("Algorithm") != EXC_C14N:
        return EsitoVerificaWSS(False, "canonicalizzazione non exclusive C14N")
    if signed_info.find("ds:SignatureMethod", ns).get("Algorithm") != RSA_SHA1:
        return EsitoVerificaWSS(False, "metodo di firma non RSA-SHA1")
    rif = firma.find(".//wsse:Reference", ns)
    if rif is None or rif.get("URI") != "#" + (token.get(f"{{{NS_WSU}}}Id") or ""):
        return EsitoVerificaWSS(False, "KeyInfo non punta al BinarySecurityToken")
    per_id = {e.get(f"{{{NS_WSU}}}Id"): e for e in doc.iter() if e.get(f"{{{NS_WSU}}}Id")}
    firmati = []
    firmati_id: set[str] = set()
    for ref in signed_info.findall("ds:Reference", ns):
        bersaglio = per_id.get((ref.get("URI") or "").lstrip("#"))
        if bersaglio is None:
            return EsitoVerificaWSS(False, f"riferimento {ref.get('URI')} non trovato")
        canonico = etree.tostring(bersaglio, method="c14n", exclusive=True, with_comments=False)
        calcolato = base64.b64encode(hashlib.sha1(canonico).digest()).decode()  # noqa: S324
        if calcolato != (ref.findtext("ds:DigestValue", namespaces=ns) or "").strip():
            return EsitoVerificaWSS(False, f"digest diverso per {ref.get('URI')}")
        firmati.append(etree.QName(bersaglio).localname)
        firmati_id.add(bersaglio.get(f"{{{NS_WSU}}}Id"))
    # Specifiche di integrazione SIST v4.03.27, par. 5.1.1 "WS-Security sulla PDD-ASL", voce
    # Timestamp: "è obbligatoria la firma del tag Timestamp, presente nell'header del messaggio
    # SOAP". La finestra temporale si legge quindi SOLO dal Timestamp firmato, figlio di Security,
    # e uno solo: un secondo Timestamp fresco non firmato messo davanti non deve far passare una
    # firma scaduta (revisione esterna giro 2, 3-sar-puglia N2).
    timestamp = sec.findall("wsu:Timestamp", ns)
    if len(timestamp) != 1:
        return EsitoVerificaWSS(False, f"attesi un solo wsu:Timestamp in Security, trovati {len(timestamp)}")
    ts = timestamp[0]
    if ts.get(f"{{{NS_WSU}}}Id") not in firmati_id or per_id.get(ts.get(f"{{{NS_WSU}}}Id")) is not ts:
        return EsitoVerificaWSS(False, "il Timestamp non è firmato")
    cert_der = base64.b64decode("".join((token.text or "").split()))
    cert = x509.load_der_x509_certificate(cert_der)
    si_c14n = etree.tostring(signed_info, method="c14n", exclusive=True, with_comments=False)
    valore = base64.b64decode("".join((firma.findtext("ds:SignatureValue", namespaces=ns) or "").split()))
    try:
        cert.public_key().verify(valore, si_c14n, padding.PKCS1v15(), hashes.SHA1())  # noqa: S303
    except InvalidSignature:
        return EsitoVerificaWSS(False, "firma non valida", cert_der)
    creato = ts.findtext("wsu:Created", namespaces=ns)
    scade = ts.findtext("wsu:Expires", namespaces=ns)
    if adesso is not None:
        c = _dt.datetime.strptime(creato, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
        s = _dt.datetime.strptime(scade, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
        if not (c - _dt.timedelta(minutes=5) <= adesso <= s):
            return EsitoVerificaWSS(False, "Timestamp scaduto o nel futuro", cert_der, creato, scade)
    return EsitoVerificaWSS(True, None, cert_der, creato, scade)
