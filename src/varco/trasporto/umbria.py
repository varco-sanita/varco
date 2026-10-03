# SPDX-License-Identifier: EUPL-1.2
"""Canale verso il SAR della Regione Umbria (PuntoZero S.c.a r.l.).

Fonte: github.com/punto-zero/umbria-sar-support (wiki «Home» e «Prescrittori», OpenAPI
`sar-open-api-prescrittore.yaml`, collection Postman), al commit fissato in
`strumenti/fonti_specifiche.json`. Le specifiche NON stanno nel repository: si scaricano con
`strumenti/scarica_specifiche.py --gruppi umbria`.

Il SAR Umbria «replica i servizi del SAC» come API REST (POST, JSON o XML) con lo stesso payload del
SAC, nomi dei campi compresi. Cosa cambia sta qui, nel canale:

  - autenticazione come il gateway FSE 2.0 (wiki, «Autenticazione»): mutua autenticazione TLS con il
    certificato di AUTENTICAZIONE del software (sta nel `ssl.SSLContext` del trasporto,
    `TrasportoHTTP(contesto_tls=...)`), più due JWT firmati con il certificato di FIRMA:
      * `Authorization: Bearer <JWT>`: iss «auth:<CN del certificato di firma>», sub = CF dell'utente
        nel formato CX di HL7 v2.5, aud = base URL del servizio, iat, exp, jti;
      * `FSE-JWT-Signature: <JWT>`: iss «integrity:<CN>», gli stessi claim più quelli applicativi
        (subject_organization_id «100», subject_organization «Regione Umbria», locality, subject_role,
        person_id, patient_consent, purpose_of_use, action_id, subject_application_*), che cambiano
        da servizio a servizio (wiki «Prescrittori», tabelle «Valorizzazione dei custom claims»);
    header dei JWT: alg RS256/RS384/RS512, typ «JWT», x5c col certificato di firma (DER, base64);
  - pinCode vuoto, CF dell'assistito IN CHIARO (wiki, «Invio prescritto DEMA»);
  - l'NRE lo mette il medico, da un lotto chiesto al SAR (`richiesta-lotto-nre`);
  - errori HTTP 4xx/5xx con un corpo RFC 7807 (Problem Details); gli errori applicativi del SAC
    restano risposte 2xx con l'elenco degli errori, come nel SAC;
  - dopo un 502 o un 504 sull'invio non si sa se il SAC ha accettato la ricetta: la specifica chiede
    di annullare con lo STESSO NRE e di rifare l'invio con un NRE DIVERSO (`InvioIncertoUmbria`).

Il canale si rifiuta di partire verso un host non locale senza un'`AdesioneUmbria`: l'ambiente di test
è un sistema reale della Regione, e i certificati di test pubblici non sono un'adesione.

Scritto e verificato sulle specifiche: NON collaudato sul sistema regionale.
"""

from __future__ import annotations

import base64
import json
import math
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse

from ..ambienti import HOST_UMBRIA_TEST
from ..errori import ConfigurazioneNonValida, ErroreTrasporto
from .http import Richiesta, Trasporto, TrasportoHTTP, consegna
from .sac import RispostaGrezza

# wiki «Base URL»: TEST https://api-salute-test.regione.umbria.it/sar (la produzione la blocca la guardia)
BASE_URL_TEST = f"https://{HOST_UMBRIA_TEST}/sar"

OID_CF = "2.16.840.1.113883.2.9.4.3.2"  # tipo CX HL7 v2.5: CF^^^&OID&ISO (wiki, claim sub e person_id)
ALGORITMI_JWT = ("RS256", "RS384", "RS512")  # la wiki scrive «RS383»: refuso (docs/SAR_UMBRIA.md, sez. 7)
DURATA_JWT_S = 300


class ServizioUmbria(str, Enum):
    LOTTO_NRE = "richiesta-lotto-nre"
    NRE_UTILIZZATI = "dem-nre-utilizzati"
    SOSTITUZIONE = "sostituzione-medico"
    INVIO = "dem-invio-prescritto"
    VISUALIZZA = "dem-visualizza-prescritto"
    ANNULLA = "dem-annulla-prescritto"

    @property
    def percorso(self) -> str:
        return f"/v1/servizi-prescrittore/{self.value}"


# Claim applicativi per servizio (wiki «Prescrittori»): (action_id, purpose_of_use, con l'assistito).
# «Non inviare» = assente. Per l'annullamento la tabella del servizio dice purpose_of_use UPDATE, la
# tabella generale di «Home» dice «valorizzare fisso a TREATMENT»: il kit segue quella del servizio,
# più specifica (docs/SAR_UMBRIA.md, sez. 7).
CLAIM_PER_SERVIZIO: Mapping[ServizioUmbria, tuple[str, str | None, bool]] = MappingProxyType({
    ServizioUmbria.LOTTO_NRE: ("CREATE", None, False),
    ServizioUmbria.NRE_UTILIZZATI: ("READ", None, False),
    ServizioUmbria.SOSTITUZIONE: ("CREATE", None, False),
    ServizioUmbria.INVIO: ("CREATE", "TREATMENT", True),
    ServizioUmbria.VISUALIZZA: ("READ", "TREATMENT", True),
    ServizioUmbria.ANNULLA: ("DELETE", "UPDATE", True),
})

# claim `locality` (wiki «Home»): l'azienda sanitaria dell'utente, formato XON di HL7 v2.5
LOCALITY_UMBRIA: Mapping[str, str] = MappingProxyType({
    "100201": "Azienda USL Umbria 1^^^^^&2.16.840.1.113883.2.9.4.1.1&ISO^^^^100201",
    "100202": "Azienda USL Umbria 2^^^^^&2.16.840.1.113883.2.9.4.1.1&ISO^^^^100202",
    "100901": "Azienda Ospedaliera di Perugia^^^^^&2.16.840.1.113883.2.9.4.1.2&ISO^^^^100901",
    "100902": "Azienda Ospedaliera di Terni^^^^^&2.16.840.1.113883.2.9.4.1.2&ISO^^^^100902",
})
RUOLI_UMBRIA = ("APR", "AAS", "OAM")  # APR MMG e PLS, AAS altri medici, OAM operatori amministrativi


def cx_cf(cf: str) -> str:
    """Il CF nel tipo CX di HL7 v2.5, come nei claim `sub` e `person_id`."""
    return f"{cf.upper()}^^^&{OID_CF}&ISO"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class ApplicativoUmbria:
    """Il software, nei claim subject_application_id / _vendor / _version (wiki «Home»)."""

    id: str
    fornitore: str
    versione: str

    def __post_init__(self):
        for nome in ("id", "fornitore", "versione"):
            if not (getattr(self, nome) or "").strip():
                raise ConfigurazioneNonValida(f"ApplicativoUmbria: {nome} obbligatorio")


@dataclass(frozen=True)
class AdesioneUmbria:
    """Prova che chi integra ha concordato l'accesso al SAR con PuntoZero / Regione Umbria: senza, il canale
    non parte verso la Regione. `riferimento`: protocollo o identificativo dell'accordo."""

    riferimento: str
    applicativo: ApplicativoUmbria

    def __post_init__(self):
        if not (self.riferimento or "").strip():
            raise ConfigurazioneNonValida("AdesioneUmbria: serve il riferimento dell'adesione")


class FirmatarioJWT(Protocol):
    """Chi firma i due JWT con il certificato di FIRMA del software (la chiave non passa dal kit, se
    chi integra usa un HSM: basta che implementi questo protocollo)."""

    algoritmo: str

    def certificato_der(self) -> bytes: ...

    def firma(self, dati: bytes) -> bytes: ...


class FirmatarioJWTPKCS12:
    """Firmatario su un file PKCS#12 (.pfx/.p12) con chiave RSA."""

    def __init__(self, percorso: str, password: bytes | None, algoritmo: str = "RS256"):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import pkcs12

        if algoritmo not in ALGORITMI_JWT:
            raise ConfigurazioneNonValida(f"algoritmo JWT {algoritmo!r}: ammessi {ALGORITMI_JWT}")
        with open(percorso, "rb") as f:
            chiave, cert, _ = pkcs12.load_key_and_certificates(f.read(), password)
        if not isinstance(chiave, rsa.RSAPrivateKey) or cert is None:
            raise ConfigurazioneNonValida("il PKCS#12 di firma deve contenere una chiave RSA e il suo certificato")
        self._chiave, self._cert, self.algoritmo = chiave, cert, algoritmo

    def certificato_der(self) -> bytes:
        from cryptography.hazmat.primitives import serialization

        return self._cert.public_bytes(serialization.Encoding.DER)

    def firma(self, dati: bytes) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        h = {"RS256": hashes.SHA256(), "RS384": hashes.SHA384(), "RS512": hashes.SHA512()}[self.algoritmo]
        return self._chiave.sign(dati, padding.PKCS1v15(), h)


def common_name(der: bytes) -> str:
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    nomi = x509.load_der_x509_certificate(der).subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not nomi:
        raise ConfigurazioneNonValida("il certificato di firma non ha un Common Name: serve per il claim iss")
    return str(nomi[0].value)


def jwt_firmato(firmatario: FirmatarioJWT, payload: dict[str, Any]) -> str:
    """JWS compatto: header {alg, typ, x5c}, payload JSON, firma RSA PKCS#1 v1.5."""
    if firmatario.algoritmo not in ALGORITMI_JWT:
        raise ConfigurazioneNonValida(f"algoritmo JWT {firmatario.algoritmo!r}: ammessi {ALGORITMI_JWT}")
    testa = {"alg": firmatario.algoritmo, "typ": "JWT",
             "x5c": [base64.b64encode(firmatario.certificato_der()).decode("ascii")]}
    parti = [_b64url(json.dumps(testa, separators=(",", ":")).encode()),
             _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode())]
    firma = firmatario.firma(".".join(parti).encode("ascii"))
    return ".".join(parti + [_b64url(firma)])


class InvioIncertoUmbria(ErroreTrasporto):
    """L'invio è finito con 502, 504 o senza risposta: non si sa se il SAC ha accettato la ricetta.

    La specifica (wiki «Invio prescritto DEMA», IMPORTANT) chiede di annullare con lo STESSO NRE e di
    rifare l'invio con un NRE DIVERSO. `RicettaUmbria.annulla_invio_incerto(errore)` fa la prima metà;
    il nuovo NRE lo sceglie chi integra, dal suo lotto."""

    def __init__(self, messaggio: str, nre: str, cf_assistito: str | None, cf_medico: str,
                 stato_http: int | None = None, corpo: bytes | None = None):
        self.nre = nre
        self.cf_assistito = cf_assistito
        self.cf_medico = cf_medico
        super().__init__(messaggio, stato_http, corpo)


class ErroreServizioUmbria(ErroreTrasporto):
    """Risposta HTTP 4xx/5xx del SAR: il corpo RFC 7807 (type, title, status, detail), se c'è."""

    def __init__(self, stato_http: int, problema: dict[str, Any] | None, corpo: bytes | None):
        self.problema = problema or {}
        titolo = self.problema.get("title") or self.problema.get("type") or "errore"
        super().__init__(f"SAR Umbria: HTTP {stato_http} {titolo}", stato_http, corpo)


def _locale(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


@dataclass(frozen=True)
class RispostaUmbria:
    stato_http: int
    dati: dict[str, Any]
    grezza: RispostaGrezza


class CanaleUmbria:
    """Canale REST verso il SAR Umbria. `cf_medico`: l'utente dei due JWT (claim sub): il medico che
    invia, cioè il titolare, oppure il sostituto quando prescrive lui."""

    def __init__(
        self,
        cf_medico: str,
        firmatario: FirmatarioJWT,
        *,
        azienda: str,
        ruolo: str = "APR",
        adesione: AdesioneUmbria | None = None,
        base_url: str | None = None,
        applicativo_di_prova: ApplicativoUmbria | None = None,
        trasporto: Trasporto | None = None,
        orologio: Callable[[], float] | None = None,
        durata_jwt_s: int = DURATA_JWT_S,
    ):
        """
        `azienda`: codice dell'azienda sanitaria dell'utente per il claim `locality` (100201 USL Umbria 1,
        100202 USL Umbria 2, 100901 AO Perugia, 100902 AO Terni).
        `ruolo`: claim `subject_role` (APR per MMG e PLS).
        `base_url`: solo verso localhost (server finto); verso la Regione si usa l'host di test della wiki,
        con l'adesione e il flag del collaudo regionale sul trasporto.
        """
        cf = (cf_medico or "").strip().upper()
        if len(cf) != 16:
            raise ConfigurazioneNonValida(f"CF del medico non valido: {cf_medico!r}")
        self._cf_medico = cf
        if azienda not in LOCALITY_UMBRIA:
            raise ConfigurazioneNonValida(f"azienda {azienda!r}: ammesse {sorted(LOCALITY_UMBRIA)} (claim locality)")
        if ruolo not in RUOLI_UMBRIA:
            raise ConfigurazioneNonValida(f"ruolo {ruolo!r}: ammessi {RUOLI_UMBRIA} (claim subject_role)")
        if not 0 < durata_jwt_s <= 3600:
            raise ConfigurazioneNonValida("durata dei JWT tra 1 e 3600 secondi")
        self.locality = LOCALITY_UMBRIA[azienda]
        self.ruolo = ruolo
        self.firmatario = firmatario
        self.trasporto = trasporto or TrasportoHTTP()
        self._orologio = orologio or time.time
        self.durata_jwt_s = durata_jwt_s
        if base_url is not None:
            if not _locale(base_url):
                raise ConfigurazioneNonValida("base_url vale solo verso localhost: verso la Regione si usa l'host "
                                              "di test pubblicato, con un'AdesioneUmbria")
            base = base_url.rstrip("/")
        else:
            base = BASE_URL_TEST
        self._base = base
        if not _locale(base):
            if adesione is None:
                raise ConfigurazioneNonValida(
                    "Verso il SAR Umbria serve un'AdesioneUmbria: l'ambiente di test è un sistema della Regione")
            if applicativo_di_prova is not None:
                raise ConfigurazioneNonValida("applicativo_di_prova vale solo verso localhost")
            applicativo = adesione.applicativo
        else:
            applicativo = adesione.applicativo if adesione else applicativo_di_prova
            if applicativo is None:
                raise ConfigurazioneNonValida("serve un'AdesioneUmbria o, verso localhost, un applicativo_di_prova")
        self.adesione = adesione
        self.applicativo = applicativo
        self._cn = common_name(firmatario.certificato_der())  # subito: senza CN non c'è iss

    @property
    def cf_medico(self) -> str:
        """Sola lettura: per un altro medico (o il sostituto) serve un altro canale."""
        return self._cf_medico

    @property
    def base_url(self) -> str:
        return self._base

    @property
    def audience(self) -> str:
        """Claim aud. La tabella della wiki dice «la base URL del servizio», che nella sezione «Base URL» è
        https://api-salute-test.regione.umbria.it/sar; gli esempi decodificati dei due token portano
        https://api-salute-test.regione.umbria.it, senza «/sar». Il kit segue gli esempi: schema, host e
        porta (docs/SAR_UMBRIA.md, sez. 7, da confermare)."""
        u = urlparse(self._base)
        return f"{u.scheme}://{u.netloc}"

    def url_di(self, servizio: ServizioUmbria) -> str:
        u = self._base + servizio.percorso
        if not _locale(u) and (self.adesione is None or self.applicativo is not self.adesione.applicativo):
            raise ConfigurazioneNonValida(f"{u}: verso il SAR Umbria serve un'AdesioneUmbria, decisa alla costruzione")
        return u

    # ------------------------------------------------------------------ JWT

    def _comuni(self, prefisso: str) -> dict[str, Any]:
        adesso = self._orologio()
        if not (isinstance(adesso, (int, float)) and math.isfinite(adesso)):
            raise ConfigurazioneNonValida("orologio non valido")
        iat = int(adesso)
        return {"iss": f"{prefisso}:{self._cn}", "sub": cx_cf(self._cf_medico), "iat": iat,
                "exp": iat + self.durata_jwt_s, "jti": str(uuid.uuid4()), "aud": self.audience}

    def token_autenticazione(self) -> str:
        return jwt_firmato(self.firmatario, self._comuni("auth"))

    def token_firma(self, servizio: ServizioUmbria, cf_assistito: str | None) -> str:
        azione, scopo, con_assistito = CLAIM_PER_SERVIZIO[servizio]
        p = self._comuni("integrity")
        p.update({"subject_organization_id": "100", "subject_organization": "Regione Umbria",
                  "locality": self.locality, "subject_role": self.ruolo})
        if con_assistito:
            cf = (cf_assistito or "").strip().upper()
            if len(cf) != 16:
                # person_id è «Obbligatorio» per i servizi con l'assistito, nel tipo CX del CF: per gli
                # assistiti senza CF (STP, ENI, esteri) la specifica non dice cosa mettere (sez. 7)
                raise ConfigurazioneNonValida(
                    f"{servizio.value}: serve il codice fiscale dell'assistito per il claim person_id")
            p["person_id"] = cx_cf(cf)
            p["patient_consent"] = True
        if scopo is not None:
            p["purpose_of_use"] = scopo
        p["action_id"] = azione
        p.update({"subject_application_id": self.applicativo.id,
                  "subject_application_vendor": self.applicativo.fornitore,
                  "subject_application_version": self.applicativo.versione})
        return jwt_firmato(self.firmatario, p)

    # ------------------------------------------------------------------ chiamata

    def chiama(self, servizio: ServizioUmbria, corpo: dict[str, Any], *, cf_assistito: str | None = None) -> RispostaUmbria:
        url = self.url_di(servizio)
        richiesta = Richiesta(
            servizio=f"umbria.{servizio.value}",
            url=url,
            corpo=json.dumps(corpo, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            intestazioni={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token_autenticazione()}",
                "FSE-JWT-Signature": self.token_firma(servizio, cf_assistito),
            },
        )
        try:
            risposta = consegna(self.trasporto, richiesta)  # guardia anche con un trasporto proprio
        except ErroreTrasporto as e:
            if servizio is ServizioUmbria.INVIO and not isinstance(e, InvioIncertoUmbria):
                raise InvioIncertoUmbria(f"invio senza risposta ({e}): esito sconosciuto", corpo.get("nre") or "",
                                         cf_assistito, self._cf_medico) from e
            raise
        grezza = RispostaGrezza(richiesta.corpo, risposta.corpo, risposta.stato_http, risposta.durata_s)
        stato = risposta.stato_http
        if servizio is ServizioUmbria.INVIO and stato in (502, 504):
            raise InvioIncertoUmbria(f"HTTP {stato}: non si sa se il SAC ha accettato la ricetta", corpo.get("nre") or "",
                                     cf_assistito, self._cf_medico, stato, risposta.corpo)
        dati = _json_o_none(risposta.corpo)
        if not 200 <= stato < 300:
            raise ErroreServizioUmbria(stato, dati if isinstance(dati, dict) else None, risposta.corpo)
        if not isinstance(dati, dict):
            raise ErroreTrasporto(f"SAR Umbria: risposta {stato} senza un oggetto JSON", stato, risposta.corpo)
        return RispostaUmbria(stato, dati, grezza)


def _json_o_none(b: bytes):
    try:
        return json.loads(b.decode("utf-8"), parse_constant=_rifiuta_costante)
    except (ValueError, UnicodeDecodeError):
        return None


def _rifiuta_costante(nome: str):
    raise ValueError(f"costante JSON non standard: {nome}")

