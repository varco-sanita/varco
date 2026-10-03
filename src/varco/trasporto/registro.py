# SPDX-License-Identifier: EUPL-1.2
"""Registratore su file di richieste e risposte (per le prove e il debug).

Il registratore è **facoltativo**: `TrasportoHTTP` non registra niente se non gliene
si passa uno. Quando c'è, scrive per ogni chiamata:
    <AAAAMMGG-HHMMSS>_<n>_<servizio>_richiesta.xml
    <AAAAMMGG-HHMMSS>_<n>_<servizio>_risposta.xml
    <AAAAMMGG-HHMMSS>_<n>_<servizio>_meta.json

Tre modalità, decise alla costruzione:

1. **Redatta (default)**. Prima di scrivere, toglie: codici fiscali (anche omocodici,
   ovunque compaiano), pincode, credenziali, NRE e codice di autenticazione, Id-Sessione (UUID),
   JWT e parametri OAuth2 (`code`, `code_verifier`, token) di SIRPED, nomi e
   indirizzi, diagnosi, esenzioni, i campi a testo libero del modello (note, `prescrizione1` e
   `prescrizione2`, descrizioni scritte dal medico, disposizioni regionali, testate) e dei moduli
   regionali, e ogni allegato base64 o PDF (il promemoria riporta il CF in chiaro). Nello
   User-Agent del SAR FVG toglie la coppia `<CF del titolare>/<identificativo della postazione>`.
   Toglie anche i testi liberi scritti dal servizio (`messaggio`; nel SAC `esito`, `tipoErrore`) e,
   nei tag sensibili, gli attributi (`<birthTime value=…/>`). Nel tracciato SAC vale una ALLOWLIST
   (`TAG_SAC_LEGGIBILI`: contenitori, codici, flag, date): ogni altro elemento SAC si redige.
   Un corpo XML si redige sull'albero, dopo che il parser ha decodificato entità numeriche e CDATA
   (anche l'XML scritto come testo dentro un altro); JSON e form sui valori decodificati.
   NEL DUBBIO NON SI SCRIVE: un corpo che il registro non sa leggere (XML troncato o non ben formato,
   UTF-16/32, byte non UTF-8) diventa `[NON SCRITTO: ...]` con lunghezza e impronta, in ogni modalità.
   Al loro posto resta un segnaposto con un
   impronta HMAC a chiave casuale di processo: dentro la stessa esecuzione lo
   stesso valore ha la stessa impronta (si segue un NRE da invio ad annullamento),
   ma la chiave non viene mai scritta, quindi l'impronta non si inverte
   provando tutti i CF possibili. Il resto (codici di esito, codici dei farmaci) resta leggibile.
   Attenzione: un log redatto è *pseudonimizzato*, non anonimo (data, ora e
   farmaco restano). Per il GDPR resta un dato personale: va tenuto in locale,
   con accesso ristretto, e cancellato quando non serve più.

2. **In chiaro solo con dati di test** (`identita_di_test=...`). Serve a raccogliere
   le prove con le utenze pubbliche del kit MEF. Una chiamata si scrive in chiaro
   **solo se** tutte queste condizioni valgono; altrimenti si scrive redatta e il
   motivo finisce nel meta (`in_chiaro_negato`):
     - l'host è un ambiente di test (non produzione, e "test"/"-val" nel nome, o localhost);
     - l'utente dell'header `Authorization: Basic` è tra le identità di test;
     - ogni codice fiscale trovato nella richiesta, nella risposta e dentro gli
       allegati decodificati (anche negli stream compressi dei PDF) è tra le
       identità di test, anche se scritto in minuscolo;
     - nessun dato identificativo senza CF (nome, indirizzo, tessera SASN o TEAM,
       data di nascita: assistiti esteri) è valorizzato;
     - se la richiesta porta il CF dell'assistito CIFRATO (`codiceAss`,
       `cfAssistito`), la risposta deve mostrare un CF di test in più (il promemoria
       riporta il CF decifrato): senza risposta o senza promemoria non si verifica.
   Un dataset reale quindi non finisce mai in chiaro per questa strada.

3. **Tutto in chiaro** (`registra_dati_personali_in_chiaro=True`). Scrive
   richieste e risposte intere, anche con dati personali e sanitari reali, tranne le
   credenziali (sotto). Serve
   solo a chi ha una base giuridica e un motivo per farlo (per esempio un audit), e
   se ne assume la responsabilità. Il flag deve essere proprio `True`; emette un
   `RuntimeWarning` alla creazione e ogni meta lo dichiara.

In TUTTE le modalità, anche le due in chiaro, le credenziali si mascherano:
  - gli header di autenticazione (`Authorization`, `Authorization2F`, cookie, `X-Api-Key` e ogni
    header con «auth», «token», «key», «secret», «password», «session», «jwt», «assertion»...:
    `X-JWT-ASSERTION` del SAR FVG, `X-idSessione` e `X-OAuth2-Authorization` di SIRPED);
  - nell'URL: `utente:password@` e i parametri con nome da credenziale (`password`, `pincode`,
    `token`, `api_key`, `code`, `code_verifier`, ...); l'URL nel meta è sempre redatto;
  - nei messaggi d'errore: le stesse coppie nome=valore, `Basic ...`/`Bearer ...`, JWT, UUID; XML e
    JSON scritti dentro un errore o un header si leggono e si redigono come un corpo, a ogni
    profondità, e un markup che il parser non legge non si scrive (giro 3 di revisione, N2);
  - nel corpo: i tag `password`, `pinCode`, `token`, `idSessione` e simili (anche i loro attributi), le
    chiavi JSON e i campi dei form con nome da credenziale (con TUTTO ciò che contengono: anche
    `{"password":{"value":…}}`), ogni JWT e ogni UUID (Id-Sessione).
Il valore si maschera qualunque forma abbia: nessuna eccezione per `***...` o `[REDATTO:...`.
In chiaro, un corpo senza credenziali si scrive con i byte originali; un corpo illeggibile no (sopra). Cartella e file nascono
con accesso solo per l'utente (0700/0600) e non si sovrascrivono mai.
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import hashlib
import hmac
import html
import itertools
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from glob import escape as glob_escape
import secrets
import warnings
import zlib
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, quote, quote_plus, unquote, unquote_plus, urlsplit

from ..ambienti import DOMINIO_FSE, DOMINIO_SOGEI, e_produzione, host_normalizzati
from .http import Richiesta, Risposta

# --- credenziali: mascherate in OGNI modalità ------------------------------------------
#
# Il nome dice se un valore è una credenziale: header, parametri dell'URL e dei form, chiavi JSON,
# coppie «nome=valore» o «nome: valore» nei messaggi d'errore. La regola guarda la FINE del nome
# (X-Api-Key, client_secret, pinCode, access_token, idSessione...), più «code» del flusso OAuth2.
_VOCABOLARIO_CREDENZIALI = (
    r"password|passwd|pwd|passphrase|pincode|pin|secret|token|api[_-]?key|apikey|key|"
    r"session[_-]?id|sessionid|id[_-]?sessione|idsessione|sessione|session|sid|credentials?|"
    r"authorization|auth|signature|code[_-]?verifier|jwt|assertion|cookie"
)
_NOME_CREDENZIALE = re.compile(rf"(?:{_VOCABOLARIO_CREDENZIALI})$", re.I)
# Nei tag XML la stessa regola, più stretta: niente «key», «auth», «signature» (KeyInfo, ds:Signature
# sono firma e certificati pubblici, non segreti).
_TAG_CREDENZIALE = re.compile(
    r"(?:password|passwd|pwd|passphrase|pincode|pin|secret|token|apikey|api_key|sessionid|session_id|"
    r"idsessione|id_sessione|codeverifier|code_verifier)$", re.I)
# Nei nomi degli header basta che compaia una di queste parole (anche in mezzo: X-Api-Key-Id).
_HEADER_CREDENZIALE = ("auth", "token", "jwt", "assertion", "sessione", "session", "cookie", "secret",
                       "password", "passwd", "credential", "signature", "api-key", "apikey", "api_key", "pincode")


def e_nome_credenziale(nome: str) -> bool:
    n = (nome or "").strip().lower()
    return n == "code" or bool(_NOME_CREDENZIALE.search(n))


def _header_mascherato(nome: str) -> bool:
    n = nome.lower()
    # "jwt"/"assertion": X-JWT-ASSERTION del SAR FVG (ID token dell'utente, modalità federata)
    # "sessione": X-idSessione di SIRPED (Id-Sessione del secondo fattore); X-OAuth2-Authorization ha "auth"
    # X-Api-Key e simili: ogni nome che finisce con una parola da credenziale
    return (n in _MASCHERATI or any(p in n for p in _HEADER_CREDENZIALE) or e_nome_credenziale(n)
            or n.endswith("-pin") or n.endswith("-key"))


_MASCHERATI = {"authorization", "authorization2f", "cookie", "set-cookie", "proxy-authorization"}


# --- modalità -------------------------------------------------------------------------

MODALITA_REDATTA = "redatta"
MODALITA_TEST_IN_CHIARO = "in_chiaro_dati_di_test"
MODALITA_DATI_PERSONALI_IN_CHIARO = "IN_CHIARO_DATI_PERSONALI"

# --- cosa si redige -------------------------------------------------------------------

# Codice fiscale di persona fisica, comprese le varianti per omocodia
# (cifre sostituite con LMNPQRSTUV). Non preceduto né seguito da lettere o cifre.
_OMO = "[0-9LMNPQRSTUV]"
# Anche minuscolo o misto: nei testi liberi un CF può essere scritto a mano.
CF_REGEX = re.compile(
    rf"(?<![A-Za-z0-9])[A-Z]{{6}}{_OMO}{{2}}[A-Z]{_OMO}{{2}}[A-Z]{_OMO}{{3}}[A-Z](?![A-Za-z0-9])", re.I
)
_CF_BYTES = re.compile(CF_REGEX.pattern.encode(), re.I)
# NRE (15 caratteri, es. 1300A4019294833) e codice di autenticazione (30 cifre) anche
# fuori dai loro tag: messaggi, testi d'errore, eccezioni.
_NRE_REGEX = re.compile(r"(?<![A-Za-z0-9])\d{4}[A-Z]\d{10}(?![A-Za-z0-9])")
_CODICE_LUNGO = re.compile(r"(?<!\d)\d{20,}(?!\d)")
# credenziali dentro un URL: https://utente:password@host
_USERINFO = re.compile(r"(?<=://)[^/@\s]+@")
# credenziali in un testo: «password=...», «token: ...», «"api_key": "..."», «Basic ...», «Bearer ...»
# Il valore si maschera SEMPRE, qualunque forma abbia: niente eccezioni per valori che «sembrano già
# mascherati» (`***...`, `[REDATTO:...`), perché quella forma può appartenere alla credenziale vera.
# Il valore va fino al primo separatore; `[`/`]` ne fanno parte (una password può contenerli).
_CREDENZIALE_KV = re.compile(
    rf"(?<![\w.-])([\w.-]*(?:{_VOCABOLARIO_CREDENZIALI})|code)(?![\w-])"
    rf"([\"']?\s*[=:]\s*)"
    rf"(?:\"([^\"\x00]*)\"|'([^'\x00]*)'|((?:Basic|Bearer|Digest|Negotiate|NTLM)\s+[^\s\"'&;,<>)\x00]+|[^\s\"'&;,<>)\x00]+))",
    re.I,
)
_SCHEMA_AUTH = re.compile(r"\b(Basic|Bearer|Digest|Negotiate|NTLM)(\s+)([A-Za-z0-9._~+/=:-]{3,})", re.I)
# Segnaposto prodotti DENTRO una passata di `credenziali`: si mettono da parte come \x00<n>\x00 (un NUL
# nel testo in ingresso diventa U+FFFD) e si rimettono alla fine. Così ogni valore si redige una volta
# sola, e l'idempotenza non dipende mai dalla forma del valore.
_ACCANTONATO = re.compile(r"\x00(\d+)\x00")
_TAG_GENERICO = re.compile(r"(<(?:[\w.-]+:)?([\w.-]+)\b[^>]*(?<!/)>)(.*?)(</(?:[\w.-]+:)?\2\s*>)", re.S)
# un corpo application/x-www-form-urlencoded (il servizio token OAuth2)
_FORM = re.compile(r"[\w.%+*~-]+=[^&\s]*(?:&[\w.%+*~-]+=[^&\s]*)*")

# Tag (nome locale) il cui contenuto si toglie sempre nella modalità redatta.
# Tracciato SAC (XSD del kit MEF) e, per il futuro canale FSE, i nomi del CDA.
TAG_REDATTI: dict[str, str] = {
    # credenziali e segreti
    "pinCode": "pincode",
    "password": "credenziale",
    # identità di medico e assistito
    "cfMedico": "cf",
    "cfMedico1": "cf",
    "cfMedico2": "cf",
    "codiceAss": "cf",
    "cfAssistito": "cf",
    "cognNome": "nome",
    "indirizzo": "indirizzo",
    "numTessSasn": "tessera",
    "socNavigaz": "tessera",
    "numIdentPers": "tessera",
    "numIdentTess": "tessera",
    "dataNascitaEstero": "data_nascita",
    "dataScadTessera": "tessera",
    "istituzCompetente": "tessera",
    "testata1": "testata",
    "testata2": "testata",
    # identificativi della ricetta (con NRE e codice si ritira il farmaco)
    "nre": "nre",
    "codAutenticazione": "codice_autenticazione",
    # dati sanitari espliciti e testi liberi
    "codDiagnosi": "diagnosi",
    "descrizioneDiagnosi": "diagnosi",
    "codEsenzione": "esenzione",
    "patologia": "diagnosi",
    "testoLibero": "testo_libero",
    "descrTestoLiberoNote": "testo_libero",
    "motivazNote": "testo_libero",
    "altro": "testo_libero",
    # testi liberi del modello (Riga.prescrizione1/2, Riga.descrizione quando manca il codice,
    # Assistito.stato_estero): li scrive il medico, ci può finire qualunque cosa
    "prescrizione1": "testo_libero",
    "prescrizione2": "testo_libero",
    "descrProdPrest": "testo_libero",
    "descrGruppoEquival": "testo_libero",  # Riga.descrizione_gruppo_equivalenza: testo dal gestionale
    # testi liberi scritti dal servizio (Comunicazione del SAC, del SIST): ci può finire un nome
    "messaggio": "testo_libero",
    # SOAP Fault (1.1 e 1.2): testo e dettaglio li scrive il servizio, ci può finire un nome o una
    # diagnosi (giro 3, N3). Il faultcode resta leggibile; SOAP 1.2 Reason/Text lo prende «text».
    "faultstring": "testo_libero",
    "detail": "testo_libero",
    "statoEstero": "tessera",
    # allegati
    "pdfPromemoria": "pdf",
    # CDA (FSE 2.0)
    "given": "nome",
    "family": "nome",
    "streetAddressLine": "indirizzo",
    "birthTime": "data_nascita",
    "name": "nome",
    "addr": "indirizzo",
    "city": "indirizzo",
    "postalCode": "indirizzo",
    "county": "indirizzo",
    "telecom": "contatto",
    "text": "testo_libero",  # testo narrativo delle sezioni CDA
    "originalText": "testo_libero",
    # SIST Regione Puglia (CVPService.xsd): anagrafica, identificativi, medici, CDA in chiaro
    "cognome": "nome",
    "nome": "nome",
    "dataNascita": "data_nascita",
    "nomeComuneResidenza": "indirizzo",
    "indirizzoResidenza": "indirizzo",
    "codIdentificativoAssistito": "cf",
    "codFiscale": "cf",
    "codiceFiscale": "cf",
    "codAssistito": "cf",
    "IUP": "nre",
    "iup": "nre",
    "codRicetta": "nre",
    "identificativoRicetta": "nre",
    "identificativoPrescrizione": "nre",
    "codAutenticazioneMedico": "codice_autenticazione",
    "codMedicoPrescrittore": "codice_medico",
    "codMedicoSostituito": "codice_medico",
    "codPrescrittore": "codice_medico",
    "cdaInstance": "cda",
    "annotazione": "testo_libero",
    "dispReg": "testo_libero",
    "nota": "testo_libero",  # SIST: Riga.note e Riga.note_prestazione (xml_sist._prestazione)
    "residenza": "indirizzo",
    "sesso": "sesso",
    # SAR Regione FVG (Insiel): VerificaPosizioneMedicoSostituto
    "cfMedicoTitolare": "cf",
    "cfMedicoSostituto": "cf",
    # SIRPED Regione Piemonte: CreateAuth/CheckToken/RevokeAuth (XSD A2F del Sistema TS)
    "userId": "utente",
    "cfUtente": "cf",
    "token": "id_sessione",
    "idSessione": "id_sessione",
}
# confronto senza maiuscole: «cfutente» del JWT come «cfUtente» dell'XSD A2F
_TAG_REDATTI_MINUSCOLO = {k.lower(): v for k, v in TAG_REDATTI.items()}

# --- tracciato SAC: allowlist ----------------------------------------------------------------------
#
# Negli elementi dei namespace del SAC (`http://<messaggio>.xsd.dem.sanita.finanze.it`, XSD del kit MEF
# in `varco/schemi`) vale una ALLOWLIST: si scrive leggibile solo ciò che sta qui sotto (contenitori,
# codici, flag, date, quantità). Ogni altro elemento SAC si redige: quelli di `TAG_REDATTI` e
# `TAG_SAC_REDATTI` col loro tipo, uno sconosciuto (uno schema nuovo) come «non_classificato».
# Nel dubbio non si scrive. `tests/unit/test_revisione_giro2_sac_guardia_registro.py` fallisce se
# un elemento degli XSD non è classificato.
TAG_SAC_LEGGIBILI = frozenset({
    # radici e contenitori
    "InvioPrescrittoRichiesta", "InvioPrescrittoRicevuta", "VisualizzaPrescrittoRichiesta",
    "VisualizzaPrescrittoRicevuta", "AnnullaPrescrittoRichiesta", "AnnullaPrescrittoRicevuta",
    "InterrogaNreUtilRichiesta", "InterrogaNreUtilRicevuta", "Comunicazione", "DettaglioPrescrizione",
    "ElencoComunicazioni", "ElencoDettagliPrescrizioni", "ElencoErroriRicette", "ElencoNota",
    "ElencoNreUtilRecord", "ErroreRicetta", "Nota", "NreUtilRecord",
    # esiti e codici del servizio
    "codEsito", "codEsitoAnnullamento", "codEsitoInserimento", "codEsitoInterrogaNreUtilizzati",
    "codEsitoVisualizzazione", "codice", "statoProcesso", "provenienza", "lotto", "codLotto",
    "flagPromemoria", "dataInserimento", "progPresc", "progrPresc",
    # codici della prescrizione (prodotti, prestazioni, note AIFA: tabelle pubbliche)
    "codProdPrest", "codGruppoEquival", "codMotivazione", "notaProd", "codCatalogoPrescr", "tipoAccesso",
    "numeroNota", "condErogabilita", "approprPrescrittiva", "tipoAmbulatorio", "quantita", "numsedute",
    "nonSost",
    # medico (codici di censimento, non il CF) e struttura
    "codRegione", "codASLAo", "codStruttura", "codSpecializzazione",
    # flag, tipi, date della ricetta
    "tipoRic", "tipoPrescrizione", "tipoPrescr", "tipoVisita", "classePriorita", "indicazionePrescr",
    "oscuramDati", "ricettaInterna", "nonEsente", "reddito", "dataCompilazione", "dataCompilazioneRicetta",
    "dataCompilazioneRicettaDal", "dataCompilazioneRicettaAl", "provAssistito", "aslAssistito",
})
# Elementi SAC redatti solo in quel namespace (nomi troppo generici per gli altri canali).
TAG_SAC_REDATTI: dict[str, str] = {
    "esito": "testo_libero",  # descrizione dell'errore scritta dal servizio
    "tipoErrore": "testo_libero",
}
_SUFFISSO_NS_SAC = ".xsd.dem.sanita.finanze.it"

# Chiavi JSON con un valore SEMPLICE (stringa, numero) che si toglie nella modalità redatta, oltre a
# TAG_REDATTI. Nel JSON non c'è un namespace che dica di quale servizio è un nome: queste valgono per
# ogni JSON, e solo sui valori semplici (un contenitore con lo stesso nome, come `elencoNota.nota`, si
# legge voce per voce). SAR Umbria (revisione, B2): il testo degli errori lo scrive il servizio e ci può
# finire un nome; dal lotto (codRagLotto + identificativoLotto + codLotto) si ricavano gli NRE del medico.
CHIAVI_JSON_REDATTE: dict[str, str] = {
    "esito": "testo_libero",
    "tipoErrore": "testo_libero",
    "nota": "testo_libero",
    "lotto": "lotto",
    "codLotto": "lotto",
    "codRagLotto": "lotto",
    "identificativoLotto": "lotto",
}
_CHIAVI_JSON_REDATTE_MINUSCOLO = {k.lower(): v for k, v in CHIAVI_JSON_REDATTE.items()}


def _tipo_sac(tag) -> str | None:
    """Per un elemento del namespace SAC: il tipo di redazione, o None se è nella allowlist.
    Per un elemento di un altro namespace: «fuori» (decide la regola generale)."""
    if not isinstance(tag, str) or not tag.startswith("{"):
        return "fuori"
    uri, locale = tag[1:].split("}", 1)
    if not uri.endswith(_SUFFISSO_NS_SAC):
        return "fuori"
    if locale in TAG_REDATTI:
        return TAG_REDATTI[locale]
    if locale in TAG_SAC_REDATTI:
        return TAG_SAC_REDATTI[locale]
    return None if locale in TAG_SAC_LEGGIBILI else "non_classificato"

# almeno 64 caratteri base64 di fila (eventualmente a capo): allegati, cifrati, firme
_BASE64_REGEX = re.compile(r"(?:[A-Za-z0-9+/]{4}[\r\n]*){16,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")
_PDF_GREZZO = re.compile(r"%PDF-.*?(?:%%EOF|\Z)", re.S)
# il SAC scrive il nome del medico nelle comunicazioni: COGNOME_MEDICO=...;NOME_MEDICO=...
_NOME_MEDICO = re.compile(r"((?:COGNOME|NOME)_MEDICO=)[^;<\"]*")
_UTENTE_BASIC = re.compile(r"^\s*Basic\s+(\S+)\s*$", re.I)
# SIRPED: l'Id-Sessione è un UUID e nell'ambiente di test torna in una <comunicazione>; il JWT
# OAuth2 sta nelle risposte JSON del servizio token; code e code_verifier nel corpo della richiesta
# (questi ultimi li prende la regola sui nomi delle credenziali).
_UUID_REGEX = re.compile(r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f-])")
_JWT_REGEX = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")


class Redattore:
    """Toglie i dati personali da un testo. La chiave HMAC vive solo in memoria.

    I corpi XML si redigono sull'ALBERO, non sul testo serializzato: il parser decodifica entità
    numeriche (``&#82;``), CDATA e riferimenti prima che si guardi il contenuto, e il file scritto è
    l'albero redatto. JSON e form si redigono sui valori decodificati. Solo un testo semplice (UTF-8,
    senza markup) passa dalle espressioni regolari; un corpo illeggibile non si scrive.
    """

    def __init__(self) -> None:
        self._chiave = secrets.token_bytes(32)

    def impronta(self, valore: str) -> str:
        return hmac.new(self._chiave, valore.encode("utf-8", "replace"), hashlib.sha256).hexdigest()[:10]

    def segnaposto(self, tipo: str, valore: str) -> str:
        return f"[REDATTO:{tipo}:{self.impronta(valore)}]"

    # ---------------------------------------------------------------- credenziali (sempre)

    def credenziale(self, valore: str) -> str:
        v = valore.strip()
        if _JWT_REGEX.fullmatch(v):
            return self.segnaposto("jwt", v)
        if _UUID_REGEX.fullmatch(v):
            return self.segnaposto("id_sessione", v.lower())
        return self.segnaposto("credenziale", v)

    def credenziali(self, t: str) -> str:
        """Solo le credenziali: vale anche nelle modalità in chiaro.

        Ogni valore si maschera qualunque forma abbia (anche `***...` o `[REDATTO:...`): i segnaposto
        di questa passata si accantonano e non si rileggono, invece di riconoscerli dalla forma."""
        accantonati: list[str] = []

        def metti(segnaposto: str) -> str:
            accantonati.append(segnaposto)
            return f"\x00{len(accantonati) - 1}\x00"

        t = t.replace("\x00", "\ufffd")
        t = _USERINFO.sub(lambda m: metti("***@"), t)
        t = _JWT_REGEX.sub(lambda m: metti(self.segnaposto("jwt", m.group(0))), t)

        def _kv(m: re.Match) -> str:
            for i, apice in ((3, '"'), (4, "'"), (5, "")):
                if m.group(i) is not None:
                    v = m.group(i)
                    if not v.strip() or _ACCANTONATO.fullmatch(v):
                        return m.group(0)
                    return m.group(1) + m.group(2) + apice + metti(self.credenziale(v)) + apice
            return m.group(0)

        t = _CREDENZIALE_KV.sub(_kv, t)
        t = _SCHEMA_AUTH.sub(lambda m: m.group(1) + m.group(2) + metti(self.segnaposto("credenziale", m.group(3))), t)

        def _tag(m: re.Match) -> str:
            if not _TAG_CREDENZIALE.search(m.group(2)) or not m.group(3).strip() or _ACCANTONATO.fullmatch(m.group(3)):
                return m.group(0)
            return m.group(1) + metti(self.credenziale(m.group(3))) + m.group(4)

        t = _TAG_GENERICO.sub(_tag, t)
        t = _UUID_REGEX.sub(lambda m: metti(self.segnaposto("id_sessione", m.group(0).lower())), t)
        return _ACCANTONATO.sub(lambda m: accantonati[int(m.group(1))], t)

    # ---------------------------------------------------------------- testi brevi

    def _dati(self, t: str) -> str:
        t = CF_REGEX.sub(lambda m: self.segnaposto("cf", m.group(0).upper()), t)
        t = _NRE_REGEX.sub(lambda m: self.segnaposto("nre", m.group(0)), t)
        t = _CODICE_LUNGO.sub(lambda m: self.segnaposto("codice", m.group(0)), t)
        return _NOME_MEDICO.sub(lambda m: m.group(1) + self.segnaposto("nome", m.group(0)), t)

    def breve(self, t: str) -> str:
        """Per header ed errori: credenziali, CF, NRE, codici lunghi, nomi (niente regola sul base64,
        che negli URL darebbe falsi positivi). Prima si decodificano entità e percent-encoding."""
        return self._dati(self.credenziali(_decodifica(t)))

    def url(self, u: str) -> str:
        """URL redatto: niente credenziali (utente:password@, parametri), CF e NRE anche codificati."""
        try:
            p = urlsplit(u)
        except ValueError:
            return self.breve(u)
        netloc = p.netloc
        if "@" in netloc:
            netloc = "***@" + netloc.rpartition("@")[2]
        parti = []
        for voce in p.query.split("&") if p.query else ():
            if "=" in voce:
                k, v = voce.split("=", 1)
                parti.append(f"{self.breve(unquote_plus(k))}={self._valore(unquote_plus(k), unquote_plus(v), True)}")
            else:
                parti.append(self.breve(unquote_plus(voce)))
        risultato = f"{p.scheme}://{netloc}{self.breve(unquote(p.path))}" if p.scheme else self.breve(unquote(p.path))
        if p.query:
            risultato += "?" + "&".join(parti)
        if p.fragment:
            risultato += "#" + self.breve(unquote(p.fragment))
        return risultato

    def user_agent(self, v: str) -> str:
        """User-Agent del SAR FVG (Idof-dem-AT-01, par. 3.1): `... <CFTitolare>/<DeviceId>`. Il DeviceId
        è il MAC address della postazione o un numero di licenza: con il CF identifica il medico e
        il suo computer. La coppia si toglie intera; prodotto e sistema operativo restano."""
        if CF_REGEX.search(v) or re.fullmatch(r"\S+/\S+ .+/\S+ \S+/\S+", v.strip()):
            v = re.sub(r"\S+/\S+$", lambda m: self.segnaposto("postazione", m.group(0)), v.strip())
        return self.metadato(v)  # e qualunque altro CF, ovunque sia; XML e JSON annidati (giro 4)

    # ---------------------------------------------------------------- testi lunghi e corpi

    def _testo_libero(self, t: str) -> str:
        """Un testo già estratto (nodo XML, valore JSON, corpo non strutturato), redatto per intero."""
        decodificato = _decodifica(t)
        if decodificato != t and any(c in decodificato for c in "{[\\"):
            # JSON emerso dalla decodifica (percent-encoding, entità): si redige come JSON (giro 4)
            return self._con_json(decodificato, True, self._testo_libero)
        t = self.credenziali(decodificato)  # prima del base64: JWT e token ne verrebbero tagliati
        if _ha_markup(t):  # markup emerso dalla decodifica (entità): nel dubbio non si scrive
            return self.non_scritto(t.encode("utf-8"), "markup nel testo").decode()
        t = _PDF_GREZZO.sub(lambda m: f"[REDATTO:pdf:{len(m.group(0))}B]", t)
        t = _BASE64_REGEX.sub(lambda m: f"[REDATTO:base64:{len(m.group(0))}c]", t)
        return self._dati(t)

    def testo(self, t: str) -> str:
        """Redige un testo intero (per esempio un XML serializzato): come `corpo`."""
        return self.corpo(t.encode("utf-8")).decode("utf-8")

    def corpo(self, b: bytes, *, redigi: bool = True, code_json_credenziale: bool = False) -> bytes:
        """Il corpo da scrivere su disco. `redigi=False` (modalità in chiaro): toglie solo le credenziali,
        e se non ce ne sono restituisce i byte originali, identici.

        `code_json_credenziale`: la chiave JSON `code` è una credenziale (authorization code OAuth2).
        Di norma non lo è (è anche un codice d'errore in molti servizi); il registratore la accende per i
        servizi `piemonte.oauth2.*` (issue #12)."""
        esito = self._corpo(b, redigi, code_json_credenziale)
        return b if esito is None else esito

    def non_scritto(self, b: bytes, motivo: str) -> bytes:
        """Il segnaposto di un corpo che il registro non sa leggere: lunghezza e impronta, niente contenuto.
        L'impronta è HMAC con la chiave di processo, come gli altri segnaposto: non si inverte."""
        impronta = hmac.new(self._chiave, b, hashlib.sha256).hexdigest()[:16]
        return f"[NON SCRITTO: corpo non leggibile ({motivo}), {len(b)} byte, hmac-sha256 {impronta}]".encode()

    def _corpo(self, b: bytes, redigi: bool, code_json_credenziale: bool = False) -> bytes | None:
        """Nel dubbio non si scrive: un corpo si scrive (redatto, o in chiaro con le sole credenziali
        tolte) solo se il registro lo legge davvero (XML ben formato, JSON, form, testo semplice UTF-8).
        Un XML che il parser non legge (troncato, UTF-16, altra codifica) o byte che non sono testo
        diventano un segnaposto, in ogni modalità: nessun ripiego sulle espressioni regolari."""
        xml = _analizza_xml(b)
        if xml is not None:
            try:
                cambiato = self._albero(xml.radice, redigi)
                # Le dichiarazioni di namespace stanno fuori dall'albero e i commenti/le istruzioni di
                # elaborazione il parser li scarta: in chiaro, ad albero invariato, tornavano i byte
                # originali con un `password=` in un xmlns o in un commento (issue #1). Le credenziali si
                # tolgono anche dagli URI dei namespace, e un corpo con commenti o PI si riscrive dall'albero.
                uri_cambiati = any(self._uri_namespace(u) != u
                                   for dichiarate in xml.dichiarazioni.values() for _, u in dichiarate)
                if cambiato or redigi or uri_cambiati or _COMMENTO_O_PI.search(b):
                    return _serializza(xml, uri_namespace=self._uri_namespace)
                return None
            except RecursionError:
                return self.non_scritto(b, "XML annidato oltre il limite")
        motivo = _motivo_illeggibile(b)
        if motivo is not None:
            return self.non_scritto(b, motivo)
        testo = b.decode("utf-8")
        s = testo.strip()
        if s[:1] in ("{", "["):
            try:
                dati = json.loads(s)
            except ValueError:
                return self.non_scritto(b, "JSON non valido")
            nuovi = self._json(dati, redigi, code_credenziale=code_json_credenziale)
            return json.dumps(nuovi, ensure_ascii=False).encode("utf-8") if (redigi or nuovi != dati) else None
        if _FORM.fullmatch(s):
            coppie = parse_qsl(s, keep_blank_values=True)
            nuove = [(k, self._valore(k, v, redigi)) for k, v in coppie]
            if not redigi and nuove == coppie:
                return None
            return "&".join(f"{quote_plus(k)}={quote(v, safe='[]:/@,')}" for k, v in nuove).encode("utf-8")
        nuovo = self._con_json(testo, True, self._testo_libero) if redigi else self._solo_credenziali(testo)
        return None if (not redigi and nuovo == testo) else nuovo.encode("utf-8")

    def _uri_namespace(self, u: str) -> str:
        """L'URI di una dichiarazione di namespace come si scrive su disco: senza credenziali, in ogni
        modalità (issue #1). Un URI ordinario resta identico."""
        return self._solo_credenziali(u) if u else u

    def _valore(self, nome: str, v: str, redigi: bool, contesto: str = "parametro", code_credenziale: bool = False) -> str:
        """Il valore di un parametro (URL, form), di una chiave JSON o di un attributo XML con quel nome.

        Per i parametri vale la regola larga dei nomi (compreso `code` dell'OAuth2); per le chiavi JSON
        la stessa senza `code` (è anche un codice d'errore); per gli attributi quella dei tag."""
        if not v.strip():
            return v
        if contesto == "attributo":
            credenziale = bool(_TAG_CREDENZIALE.search(nome))
        elif contesto == "json":
            credenziale = (code_credenziale or nome.strip().lower() != "code") and e_nome_credenziale(nome)
        else:
            credenziale = e_nome_credenziale(nome) or bool(_TAG_CREDENZIALE.search(nome))
        if credenziale:
            return self.credenziale(v)
        if redigi:
            tipo = _TAG_REDATTI_MINUSCOLO.get(nome.lower())
            if tipo is not None:
                return self.segnaposto(tipo, v.strip())
        return self._nodo(v, redigi)

    def _json(self, x, redigi: bool, nome: str | None = None, *, code_credenziale: bool = False):
        if nome is not None and x is not None and not isinstance(x, bool):
            credenziale = (code_credenziale or nome.strip().lower() != "code") and e_nome_credenziale(nome)
            tipo = _TAG_REDATTI_MINUSCOLO.get(nome.lower()) if redigi else None
            if isinstance(x, (dict, list)) and (credenziale or tipo):
                # la chiave classifica TUTTO il contenuto, a ogni profondità: {"password":{"value":…}}
                # (giro 3, N4). Il contenitore diventa un solo segnaposto.
                intero = json.dumps(x, ensure_ascii=False, sort_keys=True)
                return self.credenziale(intero) if credenziale else self.segnaposto(tipo, intero)
            if credenziale or tipo:
                return self._valore(nome, str(x), redigi, "json", code_credenziale)
            if redigi and not isinstance(x, (dict, list)):
                tipo_json = _CHIAVI_JSON_REDATTE_MINUSCOLO.get(nome.lower())
                testo = str(x).strip()
                # un codice numerico (`"esito": "0000"` di altri servizi) non è testo libero: resta
                if tipo_json is not None and not (tipo_json == "testo_libero" and testo.isdigit() and len(testo) <= 8):
                    return self.segnaposto(tipo_json, testo)
        if isinstance(x, dict):
            return {k: self._json(v, redigi, k, code_credenziale=code_credenziale) for k, v in x.items()}
        if isinstance(x, list):
            return [self._json(v, redigi, nome, code_credenziale=code_credenziale) for v in x]
        if isinstance(x, str):
            return self._nodo(x, redigi)
        return x

    def _nodo(self, t: str, redigi: bool) -> str:
        """Testo di un nodo XML (già decodificato dal parser)."""
        s = t.strip()
        if _ha_markup(s):
            annidato = _analizza_xml(s.encode("utf-8"))
            if annidato is not None:  # XML scritto come testo (escape o CDATA): si redige anche quello
                self._albero(annidato.radice, redigi)
                fuori = _serializza(annidato, prologo=False, uri_namespace=self._uri_namespace).decode("utf-8")
                return t[: len(t) - len(t.lstrip())] + fuori + t[len(t.rstrip()):]
            # markup che il parser non legge: nel dubbio non si scrive
            return self.non_scritto(s.encode("utf-8"), "XML annidato non leggibile").decode()
        if redigi:
            return self._con_json(t, True, self._testo_libero)
        return self._solo_credenziali(t)

    def _solo_credenziali(self, t: str) -> str:
        """In chiaro: si tolgono solo le credenziali, ma guardando il testo come lo leggerebbe chi lo
        decodifica (entità, percent-encoding): `&lt;password&gt;…` o `password%3D…` restavano leggibili
        (giro 4, verifica di N4). Se non c'è niente da togliere, il testo resta identico."""
        dec = _decodifica(t)
        if dec != t and _ha_markup(dec):
            xml = _analizza_xml(dec.strip().encode("utf-8"))
            if xml is None:
                return self.non_scritto(t.encode("utf-8"), "markup codificato non leggibile").decode()
            cambiato = self._albero(xml.radice, False)
            uri_cambiati = any(self._uri_namespace(u) != u for d in xml.dichiarazioni.values() for _, u in d)
            if not (cambiato or uri_cambiati or _COMMENTO_O_PI.search(dec.encode("utf-8"))):
                return t
            return _serializza(xml, prologo=False, uri_namespace=self._uri_namespace).decode("utf-8")
        tolto = self._con_json(dec, False, self.credenziali)
        return t if tolto == dec else tolto

    def _con_json(self, t: str, redigi: bool, resto) -> str:
        """JSON scritto dentro un testo (un valore, un messaggio d'errore): ogni oggetto o lista JSON si
        redige come un corpo JSON, a ogni profondità; il resto del testo passa a `resto`. Un pezzo che
        comincia come un oggetto JSON ({"…) ma non si legge: nel dubbio il testo non si scrive.

        Anche una STRINGA JSON con escape (`"{\\"password\\":…}"`: JSON serializzato due o tre volte) si
        decodifica e si redige come il suo contenuto, poi si riscrive come stringa JSON (giro 4, verifica
        di N2/N4). Una stringa senza escape (`"ciao"`) resta testo."""
        if "{" not in t and "[" not in t and "\\" not in t:
            return resto(t)
        decoder = json.JSONDecoder()
        pezzi: list[str] = []
        testo: list[str] = []
        i = 0
        while i < len(t):
            c = t[i]
            if c in "{[":
                try:
                    dati, fine = decoder.raw_decode(t, i)
                except ValueError:
                    dati, fine = None, None
                if fine is not None and isinstance(dati, (dict, list)) and dati:
                    if testo:
                        pezzi.append(resto("".join(testo)))
                        testo = []
                    pezzi.append(json.dumps(self._json(dati, redigi), ensure_ascii=False))
                    i = fine
                    continue
                if fine is None and re.match(r"""\{\s*\\*["']""", t[i:]):  # anche un dict Python (repr)
                    return self.non_scritto(t.encode("utf-8"), "JSON annidato non leggibile").decode()
            elif c == '"':
                try:
                    dati, fine = decoder.raw_decode(t, i)
                except ValueError:
                    dati, fine = None, None
                if isinstance(dati, str) and "\\" in t[i:fine]:
                    if testo:
                        pezzi.append(resto("".join(testo)))
                        testo = []
                    pezzi.append(json.dumps(self._nodo(dati, redigi), ensure_ascii=False))
                    i = fine
                    continue
            testo.append(c)
            i += 1
        if testo:
            pezzi.append(resto("".join(testo)))
        return "".join(pezzi)

    # ---------------------------------------------------------------- metadati (errori, header)

    def metadato(self, t: str, *, redigi: bool = True) -> str:
        """Un testo breve del meta (messaggio d'errore, valore di un header), redatto per intero:
        XML e JSON annidati si leggono e si redigono come un corpo, a ogni profondità (giro 3, N2);
        il resto come `breve` (o, in chiaro, solo le credenziali). Nel dubbio non si scrive: markup
        che il parser non legge diventa un segnaposto."""
        t = _decodifica(t)
        resto = self.breve if redigi else self.credenziali
        if not _ha_markup(t):
            return self._con_json(t, redigi, resto)
        xml = _analizza_xml(("<varco-testo>" + t + "</varco-testo>").encode("utf-8"))
        if xml is not None:
            try:
                for figlio in xml.radice:
                    self._albero(figlio, redigi)
            except RecursionError:
                return self.non_scritto(t.encode("utf-8"), "XML annidato oltre il limite").decode()
            pezzi = [self._con_json(xml.radice.text or "", redigi, resto)]
            for figlio in xml.radice:
                coda, figlio.tail = figlio.tail, None
                pezzi.append(_serializza(_XML(figlio, xml.dichiarazioni, False), prologo=False,
                                         uri_namespace=self._uri_namespace).decode("utf-8"))
                if coda:
                    pezzi.append(self._con_json(coda, redigi, resto))
            return "".join(pezzi)
        if _solo_pseudo_tag(t):
            return self._con_json(t, redigi, resto)
        return self.non_scritto(t.encode("utf-8"), "XML nel testo non leggibile").decode()

    def errore(self, e: BaseException, *, redigi: bool = True) -> str:
        """Il messaggio d'errore per il meta. I testi che un servizio scrive dentro l'eccezione (il
        faultstring di un SOAP Fault: giro 3, N3) si redigono come testo libero prima di tutto il resto."""
        # Gli argomenti dell'eccezione, non repr(e): repr raddoppia le barre rovesciate e un JSON con
        # «pass\u0077ord» non si leggerebbe più come password (giro 4, verifica di N2).
        # Ogni argomento come TESTO, mai con repr: repr sfugge le barre rovesciate (anche nei bytes) e una
        # chiave «pass\u0077ord» non si riconoscerebbe più (giro 4, verifiche r2 e r3 di N2).
        def da_bytes(b) -> str:
            try:
                return bytes(b).decode("utf-8")
            except UnicodeDecodeError:
                return self.non_scritto(bytes(b), "byte non UTF-8 nell'errore").decode()

        def semplice(o):
            return da_bytes(o) if isinstance(o, (bytes, bytearray, memoryview)) else str(o)

        def come_testo(a) -> str:
            if isinstance(a, str):
                return a
            if isinstance(a, (bytes, bytearray, memoryview)):
                return da_bytes(a)
            if isinstance(a, (dict, list, tuple, set, frozenset)):
                try:  # un contenitore si redige come JSON, a ogni profondità
                    return json.dumps(list(a) if isinstance(a, (set, frozenset)) else a,
                                      ensure_ascii=False, default=semplice)
                except (TypeError, ValueError):
                    return self.non_scritto(repr(a).encode("utf-8"), "contenitore non leggibile").decode()
            return str(a)

        parti = [come_testo(a) for a in e.args] if e.args else [str(e)]
        if redigi:
            for attributo in ("faultstring",):
                v = getattr(e, attributo, None)
                if not isinstance(v, str) or not v.strip():
                    continue
                if not any(v in p for p in parti):  # non si trova negli argomenti: nel dubbio non si scrive
                    return f"{type(e).__name__}: " + self.non_scritto(repr(e).encode("utf-8"), "testo del servizio").decode()
                segnaposto = self.segnaposto("testo_libero", v.strip())
                parti = [p.replace(v, segnaposto) for p in parti]
        return f"{type(e).__name__}: " + " | ".join(self.metadato(p, redigi=redigi) for p in parti)

    def _albero(self, e: ET.Element, redigi: bool) -> bool:
        """Redige l'elemento sul posto. Restituisce True se ha cambiato qualcosa."""
        cambiato = False
        locale = _nome_locale(e.tag)
        tipo = None
        if redigi:
            tipo = _tipo_sac(e.tag)
            if tipo == "fuori":
                tipo = _TAG_REDATTI_MINUSCOLO.get(locale.lower())
        if tipo is None and _TAG_CREDENZIALE.search(locale):
            tipo = "credenziale"
        if tipo is not None:
            # tag sensibile: tutto ciò che porta si toglie, anche negli ATTRIBUTI (<birthTime value=…/>,
            # <telecom value=…/>, <password value=…/>) e nei discendenti
            def via(v: str) -> str:
                # un Id-Sessione (UUID) o un JWT tengono il loro tipo, per seguirli da un file all'altro
                if tipo == "credenziale" or _UUID_REGEX.fullmatch(v.strip()) or _JWT_REGEX.fullmatch(v.strip()):
                    return self.credenziale(v)
                return self.segnaposto(tipo, v.strip())

            for k, v in list(e.attrib.items()):
                if v.strip():
                    e.attrib[k] = via(v)
                    cambiato = True
            contenuto = "".join(e.itertext()).strip()
            if len(e):
                attributi_figli = "".join(v for f in e.iter() if f is not e for v in f.attrib.values())
                contenuto = (contenuto + attributi_figli).strip()
                for figlio in list(e):
                    e.remove(figlio)
                e.text = via(contenuto) if contenuto else None
                cambiato = True
            elif contenuto:
                e.text = via(contenuto)
                cambiato = True
            return cambiato
        for k, v in list(e.attrib.items()):
            nuovo = self._valore(_nome_locale(k), v, redigi, "attributo")
            if nuovo != v:
                e.attrib[k] = nuovo
                cambiato = True
        if e.text:
            nuovo = self._nodo(e.text, redigi)
            cambiato |= nuovo != e.text
            e.text = nuovo
        for figlio in e:
            cambiato |= self._albero(figlio, redigi)
            if figlio.tail:
                nuovo = self._nodo(figlio.tail, redigi)
                cambiato |= nuovo != figlio.tail
                figlio.tail = nuovo
        return cambiato


_BOM_NON_UTF8 = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")
_MARKUP = re.compile(r"<[A-Za-z_?!/]")
_CONTROLLO = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _ha_markup(t: str) -> bool:
    return bool(_MARKUP.search(t))


# «<urlopen error [Errno 61] Connection refused>»: parentesi angolari senza struttura XML (niente
# attributi, niente chiusura). Non nascondono il contenuto di un elemento, a meno che il primo nome
# non sia quello di un dato da redigere.
_PSEUDO_TAG = re.compile(r"<([A-Za-z_][\w.-]*)\s[^<>=/]*>")


def _solo_pseudo_tag(t: str) -> bool:
    for m in _PSEUDO_TAG.finditer(t):
        nome = m.group(1)
        if _TAG_CREDENZIALE.search(nome) or nome.lower() in _TAG_REDATTI_MINUSCOLO:
            return False
    return not _ha_markup(_PSEUDO_TAG.sub("", t))


def _motivo_illeggibile(b: bytes) -> str | None:
    """Perché un corpo che non è XML ben formato non si può scrivere; None se è testo semplice leggibile."""
    if b.startswith(_BOM_NON_UTF8) or b"\x00" in b:
        return "codifica non UTF-8 (UTF-16/32 o byte nulli)"
    try:
        testo = b.decode("utf-8")
    except UnicodeDecodeError:
        return "byte non UTF-8"
    if _CONTROLLO.search(testo):
        return "caratteri di controllo"
    if _ha_markup(testo):
        return "XML non ben formato"
    return None


def _decodifica(t: str) -> str:
    """Entità XML/HTML (anche numeriche) e percent-encoding: ciò che un lettore decodificherebbe."""
    for _ in range(3):
        nuovo = html.unescape(t)
        if "%" in nuovo:
            nuovo = unquote(nuovo)
        if nuovo == t:
            break
        t = nuovo
    return t


# --- XML: analisi e scrittura che conservano i prefissi -------------------------------

_NS_XML = "http://www.w3.org/XML/1998/namespace"


@dataclass
class _XML:
    radice: ET.Element
    dichiarazioni: dict[int, list[tuple[str, str]]]
    prologo: bool


def _nome_locale(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _analizza_xml(b: bytes) -> _XML | None:
    """Albero e dichiarazioni di namespace di ogni elemento, o None se non è XML ben formato."""
    inizio = b.lstrip()
    if inizio.startswith(b"\xef\xbb\xbf"):
        inizio = inizio[3:].lstrip()
    if not inizio.startswith(b"<"):
        return None
    parser = ET.XMLPullParser(events=("start-ns", "start"))
    try:
        parser.feed(b)
        parser.close()
        eventi = list(parser.read_events())
    except (ET.ParseError, ValueError, RecursionError):
        return None
    radice, dichiarazioni, in_attesa = None, {}, []
    for evento, dato in eventi:
        if evento == "start-ns":
            in_attesa.append(dato)
        else:
            if radice is None:
                radice = dato
            if in_attesa:
                dichiarazioni[id(dato)] = in_attesa
                in_attesa = []
    if radice is None:
        return None
    return _XML(radice, dichiarazioni, inizio.startswith(b"<?xml"))


def _escape(t: str, attributo: bool = False) -> str:
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if attributo:
        t = t.replace('"', "&quot;").replace("\n", "&#10;").replace("\r", "&#13;").replace("\t", "&#9;")
    return t


# Commenti e istruzioni di elaborazione dopo il prologo: il parser li scarta, quindi non si redigono.
# Un corpo che ne contiene non si restituisce mai così com'è (issue #1).
_COMMENTO_O_PI = re.compile(rb"<!--|<\?(?!xml[\s?])", re.IGNORECASE)


def _serializza(xml: _XML, prologo: bool | None = None, uri_namespace=lambda u: u) -> bytes:
    out: list[str] = []
    if xml.prologo if prologo is None else prologo:
        out.append("<?xml version='1.0' encoding='utf-8'?>\n")
    nuovi_prefissi = itertools.count()

    def nome(tag: str, scope: dict[str, str], nuove: list[tuple[str, str]], attributo: bool) -> str:
        if not tag.startswith("{"):
            if not attributo and scope.get(""):
                nuove.append(("", ""))  # elemento senza namespace dentro un namespace predefinito
                scope[""] = ""
            return tag
        uri, locale = tag[1:].split("}", 1)
        if uri == _NS_XML:
            return "xml:" + locale
        for p, u in scope.items():
            if u == uri and (p or not attributo):
                return f"{p}:{locale}" if p else locale
        while True:
            p = f"ns{next(nuovi_prefissi)}"
            if p not in scope:
                break
        nuove.append((p, uri))
        scope[p] = uri
        return f"{p}:{locale}"

    def scrivi(e: ET.Element, scope_padre: dict[str, str]) -> None:
        scope = dict(scope_padre)
        nuove = list(xml.dichiarazioni.get(id(e), ()))
        for p, u in nuove:
            scope[p] = u
        tag = nome(e.tag, scope, nuove, False)
        attributi = [(nome(k, scope, nuove, True), v) for k, v in e.attrib.items()]
        out.append("<" + tag)
        for p, u in nuove:
            u = uri_namespace(u)  # solo in uscita: lo scope resta sull'URI vero, per risolvere i prefissi
            out.append(f' xmlns:{p}="{_escape(u, True)}"' if p else f' xmlns="{_escape(u, True)}"')
        for k, v in attributi:
            out.append(f' {k}="{_escape(v, True)}"')
        if e.text or len(e):
            out.append(">")
            if e.text:
                out.append(_escape(e.text))
            for figlio in e:
                scrivi(figlio, scope)
                if figlio.tail:
                    out.append(_escape(figlio.tail))
            out.append(f"</{tag}>")
        else:
            out.append(" />")

    scrivi(xml.radice, {})
    return "".join(out).encode("utf-8")


# --- ricerca dei CF anche dentro gli allegati -----------------------------------------

def _stream_pdf(pdf: bytes) -> Iterable[bytes]:
    yield pdf
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        try:
            yield zlib.decompress(m.group(1))
        except zlib.error:
            yield m.group(1)


def _testi_decodificati(corpo: bytes) -> list[bytes]:
    """Il corpo come lo leggerebbe un parser: testi e attributi dell'albero XML (entità decodificate,
    anche dell'XML scritto come testo), oppure il testo con entità e percent-encoding decodificati."""
    viste = []
    xml = _analizza_xml(corpo)
    if xml is not None:
        pezzi = []
        for e in xml.radice.iter():
            pezzi.extend(v for v in e.attrib.values())
            pezzi.extend(t for t in (e.text, e.tail) if t)
        for p in list(pezzi):
            if p.strip().startswith("<"):  # XML scritto come testo (escape o CDATA)
                pezzi.extend(x.decode("utf-8", "replace") for x in _testi_decodificati(p.strip().encode("utf-8")))
        viste.append("\n".join(pezzi).encode("utf-8"))
    testo = corpo.decode("utf-8", "replace")
    decodificato = _decodifica(testo)
    if decodificato != testo:
        viste.append(decodificato.encode("utf-8"))
    return viste


def codici_fiscali_presenti(corpo: bytes) -> set[str]:
    """CF in chiaro nel corpo (anche scritti con entità XML o percent-encoding), nei blocchi base64
    decodificabili e negli stream dei PDF."""
    trovati: set[str] = set()
    for vista in [corpo, *_testi_decodificati(corpo)]:
        trovati |= _cf_in(vista)
    return trovati


def _cf_in(corpo: bytes) -> set[str]:
    trovati = {m.decode().upper() for m in _CF_BYTES.findall(corpo)}
    testo = corpo.decode("utf-8", "replace")
    blocchi = [corpo] if b"%PDF-" in corpo else []
    for m in _BASE64_REGEX.finditer(testo):
        try:
            blocchi.append(base64.b64decode("".join(m.group(0).split()), validate=True))
        except (binascii.Error, ValueError):
            continue
    for blocco in blocchi:
        parti = _stream_pdf(blocco) if blocco.startswith(b"%PDF-") or b"%PDF-" in blocco[:1024] else [blocco]
        for parte in parti:
            trovati |= {m.decode().upper() for m in _CF_BYTES.findall(parte)}
    return trovati


def _host_di_test(url: str) -> bool:
    if e_produzione(url):
        return False
    # ogni lettura dell'host (come la vedranno urllib e il DNS) deve essere di test
    return all(_un_host_di_test(h) for h in host_normalizzati(url))


def _un_host_di_test(host: str) -> bool:
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    if host == DOMINIO_SOGEI or host.endswith("." + DOMINIO_SOGEI):
        return "test" in host
    if host == DOMINIO_FSE or host.endswith("." + DOMINIO_FSE):
        return "-val." in host
    return False


def _utente_basic(intestazioni: dict[str, str]) -> str | None:
    for k, v in intestazioni.items():
        if k.lower() == "authorization":
            m = _UTENTE_BASIC.match(v or "")
            if not m:
                return None
            try:
                return base64.b64decode(m.group(1), validate=True).decode("utf-8", "replace").partition(":")[0]
            except (binascii.Error, ValueError):
                return None
    return None


# Tag che identificano una persona SENZA un codice fiscale in chiaro (assistiti esteri,
# SASN, nomi, indirizzi): in modalità «dati di test» devono essere vuoti, perché il
# controllo sui CF non li vedrebbe.
TAG_IDENTITA_SENZA_CF = (
    "cognNome", "indirizzo", "numTessSasn", "socNavigaz", "numIdentPers", "numIdentTess",
    "dataNascitaEstero", "dataScadTessera", "istituzCompetente", "given", "family",
    "streetAddressLine", "birthTime",
)
# Tag con il CF dell'assistito CIFRATO: il chiaro si concede solo se la risposta mostra
# un CF di test in più (il promemoria PDF riporta il CF decifrato dal SAC).
TAG_CF_CIFRATO = ("codiceAss", "cfAssistito")


def _tag_pieni(corpo: bytes, nomi: Iterable[str]) -> set[str]:
    nomi = tuple(nomi)
    pieni = set()
    xml = _analizza_xml(corpo)
    if xml is not None:  # sull'albero: entità e CDATA decodificati
        for e in xml.radice.iter():
            locale = _nome_locale(e.tag)
            # il SAC riempie gli indirizzi vuoti con i soli separatori: «|||» conta come vuoto
            if locale in nomi and "".join(e.itertext()).strip(" \t\r\n|"):
                pieni.add(locale)
    testo = corpo.decode("utf-8", "replace")
    for nome in nomi:
        rx = rf"<(?:[\w.-]+:)?{nome}\b[^>]*(?<!/)>(.*?)</(?:[\w.-]+:)?{nome}\s*>"
        if any(m.strip(" \t\r\n|") for m in re.findall(rx, testo, re.S)):
            pieni.add(nome)
    return pieni


def motivo_non_di_test(
    richiesta: Richiesta, risposta: Risposta | None, identita_di_test: frozenset[str]
) -> str | None:
    """None se la chiamata usa SOLO dati di test; altrimenti il motivo (senza dati personali)."""
    if not _host_di_test(richiesta.url):
        return "host non di test"
    utente = _utente_basic(richiesta.intestazioni)
    if utente is None:
        return "utente non verificabile (manca Authorization Basic)"
    if utente not in identita_di_test:
        return "utente non tra le identità di test"
    cf_richiesta = codici_fiscali_presenti(richiesta.corpo)
    cf_risposta = codici_fiscali_presenti(risposta.corpo) if risposta is not None else set()
    estranei = (cf_richiesta | cf_risposta) - identita_di_test
    if estranei:
        return f"{len(estranei)} codici fiscali non di test"
    corpi = richiesta.corpo + (risposta.corpo if risposta is not None else b"")
    senza_cf = _tag_pieni(corpi, TAG_IDENTITA_SENZA_CF)
    if senza_cf:
        return "dati identificativi senza CF verificabile: " + ", ".join(sorted(senza_cf))
    if _tag_pieni(richiesta.corpo, TAG_CF_CIFRATO) and not (cf_risposta - cf_richiesta):
        return "CF dell'assistito cifrato e non verificabile nella risposta"
    return None


# --- registratore ---------------------------------------------------------------------

class RegistratoreFile:
    """Registra su file ogni scambio. Redatto di default: vedi la docstring del modulo."""

    def __init__(
        self,
        cartella: str | Path,
        etichetta: str = "",
        *,
        identita_di_test: Iterable[str] | None = None,
        registra_dati_personali_in_chiaro: bool = False,
    ):
        if not isinstance(registra_dati_personali_in_chiaro, bool):
            raise TypeError("registra_dati_personali_in_chiaro deve essere proprio True o False")
        if registra_dati_personali_in_chiaro and identita_di_test is not None:
            raise ValueError("Scegliere una sola modalità: identita_di_test oppure registra_dati_personali_in_chiaro")
        if registra_dati_personali_in_chiaro is True:
            self.modalita = MODALITA_DATI_PERSONALI_IN_CHIARO
            warnings.warn(
                "RegistratoreFile: registra_dati_personali_in_chiaro=True, richieste e risposte "
                "INTERE (CF, promemoria PDF, dati sanitari) vengono scritte su disco",
                RuntimeWarning,
                stacklevel=2,
            )
        elif identita_di_test is not None:
            self.modalita = MODALITA_TEST_IN_CHIARO
        else:
            self.modalita = MODALITA_REDATTA
        self.identita_di_test = frozenset(identita_di_test or ())
        if self.modalita == MODALITA_TEST_IN_CHIARO and not self.identita_di_test:
            raise ValueError("identita_di_test vuota: nessuna chiamata potrebbe essere in chiaro")
        self.cartella = Path(cartella)
        # accesso ristretto: solo l'utente che registra (su Windows chmod conta poco)
        self.cartella.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.etichetta = etichetta
        self._contatore = itertools.count(1)
        self._redattore = Redattore()
        self.file_scritti: list[Path] = []

    def _scrivi(self, percorso: Path, dati: bytes) -> Path:
        """Crea il file in esclusiva (mai sovrascrivere un'altra registrazione), permessi 0600."""
        base, n = percorso, 1
        while True:
            try:
                fd = os.open(percorso, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
                break
            except FileExistsError:
                n += 1
                percorso = base.with_name(base.name.replace("_", f"_{n:02d}bis_", 1))
        with os.fdopen(fd, "wb") as f:
            f.write(dati)
        self.file_scritti.append(percorso)
        return percorso

    def _decidi(self, richiesta: Richiesta, risposta: Risposta | None) -> tuple[bool, str | None]:
        """(in_chiaro, motivo del rifiuto)."""
        if self.modalita == MODALITA_DATI_PERSONALI_IN_CHIARO:
            return True, None
        if self.modalita == MODALITA_TEST_IN_CHIARO:
            motivo = motivo_non_di_test(richiesta, risposta, self.identita_di_test)
            return motivo is None, motivo
        return False, None

    def __call__(self, richiesta: Richiesta, risposta: Risposta | None, errore: BaseException | None) -> None:
        adesso = _dt.datetime.now().astimezone()
        n = next(self._contatore)
        in_chiaro, negato = self._decidi(richiesta, risposta)
        r = self._redattore
        # in chiaro si tolgono SOLO le credenziali (password, pincode, token, Id-Sessione, JWT): sempre
        oauth2 = richiesta.servizio.startswith("piemonte.oauth2.")  # `code` JSON = authorization code (issue #12)
        red = lambda b: r.corpo(b, redigi=not in_chiaro, code_json_credenziale=oauth2)  # noqa: E731
        # header ed errori: XML e JSON annidati si redigono come i corpi (giro 3, N2)
        red_t = lambda v: r.metadato(v, redigi=not in_chiaro)  # noqa: E731

        nome_servizio = re.sub(r"[^\w-]", "-", richiesta.servizio.replace(".", "-"))
        radice = f"{adesso:%Y%m%d-%H%M%S}_{n:02d}_{nome_servizio}"
        if self.etichetta:
            radice += f"_{self.etichetta}"
        while any(self.cartella.glob(f"{glob_escape(radice)}_*")):
            radice += "-bis"  # un altro registratore ha usato lo stesso secondo e numero
        self._scrivi(self.cartella / f"{radice}_richiesta.xml", red(richiesta.corpo))
        if risposta is not None:
            self._scrivi(self.cartella / f"{radice}_risposta.xml", red(risposta.corpo))
        meta = {
            "data_ora": adesso.isoformat(timespec="seconds"),
            "servizio": richiesta.servizio,
            "modalita": self.modalita if in_chiaro else MODALITA_REDATTA,
            "url": r.url(richiesta.url),  # redatto in ogni modalità
            "intestazioni_richiesta": {
                k: ("***" if _header_mascherato(k)
                    else r.user_agent(v) if k.lower() == "user-agent" and not in_chiaro
                    else red_t(v))
                for k, v in richiesta.intestazioni.items()
            },
            "stato_http": risposta.stato_http if risposta else None,
            "durata_s": round(risposta.durata_s, 3) if risposta else None,
            "intestazioni_risposta": (
                {k: ("***" if _header_mascherato(k) else red_t(v)) for k, v in risposta.intestazioni.items()}
                if risposta
                else None
            ),
            "errore": r.errore(errore, redigi=not in_chiaro) if errore else None,
        }
        if negato:
            meta["in_chiaro_negato"] = negato
        self._scrivi(self.cartella / f"{radice}_meta.json", json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
