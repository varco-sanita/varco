# SPDX-License-Identifier: EUPL-1.2
"""Codec JSON del SAR Umbria: modello del kit <-> payload delle API REST.

La wiki dice che il payload «è identico a quello del SAC, nomi dei campi inclusi»: i campi si ricavano
dalle stesse funzioni del codec SAC (`xml_sac.campi_testata`, `xml_sac._campi_riga`), quindi il modello
dati non cambia. La forma JSON segue l'OpenAPI `sar-open-api-prescrittore.yaml`, che è il contratto
leggibile da una macchina:
  - ogni valore è una STRINGA (`type: string`, anche `quantita` e `nonEsente`); gli esempi della wiki e
    della collection Postman usano numeri e `null` (docs/SAR_UMBRIA.md, sez. 7);
  - un campo senza valore NON si manda (`null` non è `type: string`);
  - le righe stanno in `elencoDettagliPrescrizioni.dettaglioPrescrizione`;
  - la richiesta del lotto usa `codRegione`, `identificativoLotto`, `cfmedico` come l'OpenAPI; la wiki e
    Postman scrivono `CodRegione`, `IdentificativoLotto`, `CFMedico` (sez. 7, da chiarire).
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import re
from dataclasses import dataclass
from typing import Any

from ..errori import RicettaNonValida
from . import xml_sac
from .xml_fvg import e_codice_stp_eni
from .modello import (
    Comunicazione,
    CriteriNreUtilizzati,
    Esito,
    EsitoAnnullamento,
    EsitoInterrogazioneNre,
    EsitoInvio,
    EsitoVisualizzazione,
    Messaggio,
    NotaPrestazione,
    NreUtilizzato,
    Ricetta,
    TipoPrescrizione,
)

CODICE_REGIONE_UMBRIA = "100"
MAX_TESTATA2_SMARTCUP = 256
_SMARTCUP = re.compile(r"SMARTCUP=SI;TEL=[^;]*;EMAIL=[^;]*;NOTECUP=[^;]*;")
_CF = re.compile(r"[A-Z0-9]{16}")


def _senza_vuoti(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


# ------------------------------------------------------------------ controlli locali (solo Umbria)


def problemi_umbria(ricetta: Ricetta) -> list[str]:
    """Ciò che il SAR Umbria chiede oltre al SAC (wiki «Prescrittori»)."""
    p: list[str] = []
    if ricetta.prescrittore.codice_regione != CODICE_REGIONE_UMBRIA:
        p.append(f"codRegione {ricetta.prescrittore.codice_regione!r}: in Umbria è \"100\"")
    nre = ricetta.nre or ""
    if not nre:
        p.append("nre obbligatorio: in Umbria l'NRE lo mette il medico, da un lotto chiesto al SAR (richiesta-lotto-nre)")
    elif len(nre) != 15 or not nre.startswith(CODICE_REGIONE_UMBRIA):
        p.append(f"nre {nre!r}: 15 caratteri, da un lotto della Regione Umbria (comincia per 100)")
    cf = (ricetta.assistito.codice_fiscale or "").upper()
    if (not _CF.fullmatch(cf) or e_codice_stp_eni(cf)
            or (ricetta.assistito.tipo_ricetta or "") in ("ST", "EE", "UE", "NE", "NX")):
        # person_id del JWT di firma: «Obbligatorio», nel tipo CX del CF. Per STP, ENI ed esteri la
        # specifica non dice cosa mettere: il kit non inventa (docs/SAR_UMBRIA.md, sez. 7)
        p.append("serve il codice fiscale dell'assistito (16 caratteri, non STP/ENI né assicurato estero): "
                 "va nel claim person_id del JWT, che la specifica definisce solo per il CF")
    t2 = ricetta.testata2 or ""
    if t2.upper().startswith("SMARTCUP"):
        if ricetta.tipo is not TipoPrescrizione.SPECIALISTICA:
            p.append("SmartCUP (testata2) vale solo per le ricette specialistiche")
        if not _SMARTCUP.fullmatch(t2):
            p.append("testata2 SmartCUP: «SMARTCUP=SI;TEL=...;EMAIL=...;NOTECUP=...;», che termina con «;»")
        if len(_xml_escape(t2)) > MAX_TESTATA2_SMARTCUP:
            p.append(f"testata2 SmartCUP oltre {MAX_TESTATA2_SMARTCUP} caratteri (compresi gli encoding XML)")
    return p


def _xml_escape(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


# ------------------------------------------------------------------ richieste


def richiesta_invio(ricetta: Ricetta) -> dict[str, Any]:
    """InvioPrescrittoRichiesta: pinCode vuoto, CF dell'assistito in chiaro, NRE del lotto del medico."""
    campi = xml_sac.campi_testata(ricetta, "", ricetta.assistito.codice_fiscale)
    corpo = {k: v for k, v in campi.items() if v is not None and (v != "" or k == "pinCode")}
    righe = []
    for r in ricetta.righe:
        valori = xml_sac._campi_riga(r)
        righe.append({k: v for k, v in valori.items() if v not in (None, "")})
    corpo["elencoDettagliPrescrizioni"] = {"dettaglioPrescrizione": righe}
    return corpo


def _nre_cf(nre: str, cf_medico: str) -> dict[str, Any]:
    if not nre or len(nre) != 15:
        raise ValueError(f"NRE non valido (servono 15 caratteri): {nre!r}")
    return {"pinCode": "", "nre": nre, "cfMedico": cf_medico}


def richiesta_visualizza(nre: str, cf_medico: str) -> dict[str, Any]:
    return _nre_cf(nre, cf_medico)


def richiesta_annulla(nre: str, cf_medico: str) -> dict[str, Any]:
    return _nre_cf(nre, cf_medico)


def _data_ora(d: _dt.datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def richiesta_interroga_nre(criteri: CriteriNreUtilizzati, cf_medico: str) -> dict[str, Any]:
    return _senza_vuoti({
        "pinCode": "",
        "codRegione": criteri.codice_regione,
        "nre": criteri.nre,
        "codLotto": criteri.codice_lotto,
        "cfMedico": cf_medico,
        "cfAssistito": criteri.cf_assistito,
        "tipoPrescr": criteri.tipo.value if criteri.tipo else None,
        "dataCompilazioneRicettaDal": _data_ora(criteri.dal) if criteri.dal else None,
        "dataCompilazioneRicettaAl": _data_ora(criteri.al) if criteri.al else None,
    })


def richiesta_lotto(cf_medico: str, identificativo_lotto: str, codice_regione: str = CODICE_REGIONE_UMBRIA) -> dict[str, Any]:
    """LottoRichiestaNRE (MEF par. 4.1): identificativoLotto «1» = 1000 NRE (consigliato ai MMG e PLS),
    «0» = 100 NRE (consigliato agli specialisti)."""
    if identificativo_lotto not in ("0", "1"):
        raise ValueError("identificativoLotto: «0» (100 NRE) o «1» (1000 NRE)")
    return {"codRegione": codice_regione, "identificativoLotto": identificativo_lotto, "cfmedico": cf_medico}


def richiesta_sostituzione(cf_titolare: str, cf_sostituto: str, codice_asl: str, codice_specializzazione: str,
                           dal: _dt.date, al: _dt.date, *, codice_struttura: str | None = None,
                           nota: str | None = None, codice_regione: str = CODICE_REGIONE_UMBRIA) -> dict[str, Any]:
    """DichiarazioneSostituzioneMedicoRichiesta (specifica MEF «pre-autorizzazione sostituzione medico»,
    par. 3.1). L'OpenAPI vuole anche `pwd` (required), che gli esempi non mandano: va vuota, come pinCode."""
    if al < dal:
        raise ValueError("periodo di sostituzione rovesciato")
    if cf_titolare.upper() == cf_sostituto.upper():
        raise ValueError("titolare e sostituto coincidono")
    return _senza_vuoti({
        "pinCode": "", "pwd": "",
        "cfMedicoTitolare": cf_titolare.upper(), "codRegione": codice_regione, "codASLAo": codice_asl,
        "codStruttura": codice_struttura, "codSpecializzazione": codice_specializzazione,
        "cfMedicoSostituto": cf_sostituto.upper(),
        "dataInizioSostituzione": dal.strftime("%Y%m%d"), "dataFineSostituzione": al.strftime("%Y%m%d"),
        "nota": nota,
    })


# ------------------------------------------------------------------ risposte


class RispostaNonConforme(ValueError):
    """La risposta 2xx non rispetta l'OpenAPI (campo obbligatorio assente, tipo sbagliato)."""


# Schemi delle risposte, trascritti da components/schemas dell'OpenAPI del prescrittore
# (sar-open-api-prescrittore.yaml, commit 3cd93d86): per ogni oggetto i campi obbligatori e il tipo di
# ogni campo dichiarato. «s» = string; («o», Nome) = oggetto; («a», Nome) = array di oggetti. Nessuno
# schema dichiara additionalProperties: i campi in più si accettano (e si ignorano). Un test confronta
# questa tabella con l'OpenAPI scaricata (tests/unit/test_revisione_umbria.py).
_S = "s"
_ERRORI_RICETTE = ("o", "ElencoErroriRicetteType")
_COMUNICAZIONI = ("o", "ElencoComunicazioniType")
SCHEMI_RISPOSTE: dict[str, tuple[tuple[str, ...], dict[str, Any]]] = {
    "ComunicazioneType": (("codice", "messaggio"), {"codice": _S, "messaggio": _S}),
    "ElencoComunicazioniType": (("comunicazione",), {"comunicazione": ("a", "ComunicazioneType")}),
    "ErroreRicettaType": (("codEsito",), {"codEsito": _S, "esito": _S, "progPresc": _S, "tipoErrore": _S}),
    "ElencoErroriRicetteType": (("erroreRicetta",), {"erroreRicetta": ("a", "ErroreRicettaType")}),
    "ErroreType": (("codEsito",), {"codEsito": _S, "esito": _S, "tipoErrore": _S}),
    "ElencoErroriType": (("errore",), {"errore": ("a", "ErroreType")}),
    "NotaType": ((), {"progrPresc": _S, "codProdPrest": _S, "tipoAmbulatorio": _S}),
    "ElencoNotaType": (("nota",), {"nota": ("a", "NotaType")}),
    "DettaglioPrescrizioneType": (("quantita",), {k: _S for k in (
        "codProdPrest", "descrProdPrest", "codGruppoEquival", "descrGruppoEquival", "testoLibero",
        "descrTestoLiberoNote", "nonSost", "motivazNote", "codMotivazione", "notaProd", "quantita",
        "prescrizione1", "prescrizione2", "codCatalogoPrescr", "tipoAccesso", "numeroNota", "condErogabilita",
        "approprPrescrittiva", "patologia", "numsedute")}),
    "ElencoDettagliPrescrizioniType": (("dettaglioPrescrizione",),
                                       {"dettaglioPrescrizione": ("a", "DettaglioPrescrizioneType")}),
    "NreUtilRecordType": ((), {k: _S for k in ("nre", "cfMedico", "tipoPrescrizione", "dataCompilazioneRicetta",
                                               "cfAssistito", "provenienza", "lotto", "codAutenticazione")}),
    "ElencoNreUtilRecordType": ((), {"nreUtilRecord": ("a", "NreUtilRecordType")}),
    "InvioPrescrittoRicevuta": (("codEsitoInserimento",), {
        "nre": _S, "codAutenticazione": _S, "dataInserimento": _S, "codEsitoInserimento": _S,
        "elencoErroriRicette": _ERRORI_RICETTE, "elencoComunicazioni": _COMUNICAZIONI,
        "elencoNota": ("o", "ElencoNotaType"), "flagPromemoria": _S, "pdfPromemoria": _S}),
    "VisualizzaPrescrittoRicevuta": (("codEsitoVisualizzazione",), {
        **{k: _S for k in (
            "nre", "cfMedico1", "cfMedico2", "codRegione", "codASLAo", "codStruttura", "codSpecializzazione",
            "testata1", "testata2", "tipoRic", "codiceAss", "cognNome", "indirizzo", "oscuramDati", "numTessSasn",
            "socNavigaz", "tipoPrescrizione", "ricettaInterna", "codEsenzione", "nonEsente", "reddito",
            "codDiagnosi", "descrizioneDiagnosi", "dataCompilazione", "tipoVisita", "dispReg", "provAssistito",
            "aslAssistito", "indicazionePrescr", "altro", "classePriorita", "statoEstero", "istituzCompetente",
            "numIdentPers", "numIdentTess", "dataNascitaEstero", "dataScadTessera", "statoProcesso",
            "codAutenticazione", "dataInserimento", "codEsitoVisualizzazione")},
        "elencoDettagliPrescrizioni": ("o", "ElencoDettagliPrescrizioniType"),
        "elencoErroriRicette": _ERRORI_RICETTE, "elencoComunicazioni": _COMUNICAZIONI,
        "elencoNota": ("o", "ElencoNotaType")}),
    "AnnullaPrescrittoRicevuta": (("codEsitoAnnullamento", "nre"), {
        "nre": _S, "codEsitoAnnullamento": _S, "elencoErroriRicette": _ERRORI_RICETTE,
        "elencoComunicazioni": _COMUNICAZIONI}),
    "InterrogaNreUtilRicevuta": (("codEsitoInterrogaNreUtilizzati",), {
        "codEsitoInterrogaNreUtilizzati": _S, "elencoNreUtilRecord": ("o", "ElencoNreUtilRecordType"),
        "elencoErroriRicette": _ERRORI_RICETTE, "elencoComunicazioni": _COMUNICAZIONI}),
    "LottoRicevutaNRE": ((), {k: _S for k in ("codRegione", "codRagLotto", "identificativoLotto", "codLotto",
                                              "codEsito", "esito", "cfmedico")}),
    "DichiarazioneSostituzioneMedicoRicevuta": (("codEsitoInserimento",), {
        "codEsitoInserimento": _S, "dataInserimento": _S, "elencoErrori": ("o", "ElencoErroriType"),
        "elencoComunicazioni": _COMUNICAZIONI}),
}


def verifica_risposta(d: Any, schema: str, percorso: str = "") -> None:
    """Solleva RispostaNonConforme se `d` non rispetta lo schema dell'OpenAPI: oggetto, campi
    obbligatori presenti, ogni campo dichiarato del suo tipo. Un `null` esplicito non è una stringa
    (l'OpenAPI non dichiara nullable): vale come errore, non come assenza (revisione, B4)."""
    dove = percorso or schema
    if not isinstance(d, dict):
        raise RispostaNonConforme(f"{dove}: atteso un oggetto, arrivato {type(d).__name__}")
    obbligatori, campi = SCHEMI_RISPOSTE[schema]
    for k in obbligatori:
        if k not in d:
            raise RispostaNonConforme(f"{dove}: manca {k!r} (required nell'OpenAPI, {schema})")
    for k, v in d.items():
        tipo = campi.get(k)
        if tipo is None:
            continue
        qui = f"{dove}.{k}"
        if tipo == _S:
            if not isinstance(v, str):
                raise RispostaNonConforme(f"{qui}: atteso una stringa, arrivato {type(v).__name__}")
        elif tipo[0] == "o":
            verifica_risposta(v, tipo[1], qui)
        else:
            if not isinstance(v, list):
                raise RispostaNonConforme(f"{qui}: atteso un array, arrivato {type(v).__name__}")
            for i, x in enumerate(v):
                verifica_risposta(x, tipo[1], f"{qui}[{i}]")


def _stringa(d: dict, chiave: str, *, obbligatoria: bool = False) -> str | None:
    v = d.get(chiave)
    if v is None:
        if obbligatoria:
            raise RispostaNonConforme(f"risposta senza {chiave!r} (required nell'OpenAPI)")
        return None
    if not isinstance(v, str):
        raise RispostaNonConforme(f"{chiave!r}: atteso una stringa, arrivato {type(v).__name__}")
    v = v.strip()
    return v or None


def _elenco(d: dict, contenitore: str, voce: str) -> list[dict]:
    c = d.get(contenitore)
    if c is None:
        return []
    if not isinstance(c, dict) or not isinstance(c.get(voce, []), list):
        raise RispostaNonConforme(f"{contenitore}.{voce}: atteso un oggetto con una lista")
    out = c.get(voce, [])
    if not all(isinstance(x, dict) for x in out):
        raise RispostaNonConforme(f"{contenitore}.{voce}: ogni voce dev'essere un oggetto")
    return out


def _messaggi(d: dict, contenitore: str = "elencoErroriRicette", voce: str = "erroreRicetta") -> tuple[Messaggio, ...]:
    return tuple(
        Messaggio(codice=_stringa(e, "codEsito", obbligatoria=True) or "", testo=_stringa(e, "esito"),
                  progressivo=_stringa(e, "progPresc"), tipo=_stringa(e, "tipoErrore"))
        for e in _elenco(d, contenitore, voce)
    )


def _comunicazioni(d: dict) -> tuple[Comunicazione, ...]:
    return tuple(Comunicazione(_stringa(c, "codice", obbligatoria=True) or "", _stringa(c, "messaggio", obbligatoria=True) or "")
                 for c in _elenco(d, "elencoComunicazioni", "comunicazione"))


def _note(d: dict) -> tuple[NotaPrestazione, ...]:
    return tuple(NotaPrestazione(_stringa(n, "progrPresc"), _stringa(n, "codProdPrest"), _stringa(n, "tipoAmbulatorio"))
                 for n in _elenco(d, "elencoNota", "nota"))


def leggi_ricevuta_invio(d: dict) -> EsitoInvio:
    verifica_risposta(d, "InvioPrescrittoRicevuta")
    pdf = _stringa(d, "pdfPromemoria")
    try:
        promemoria = base64.b64decode(pdf, validate=True) if pdf else None
    except (binascii.Error, ValueError) as e:
        raise RispostaNonConforme("pdfPromemoria non è base64 (format: byte)") from e
    return EsitoInvio(
        codice=_stringa(d, "codEsitoInserimento", obbligatoria=True) or "",
        messaggi=_messaggi(d), comunicazioni=_comunicazioni(d),
        nre=_stringa(d, "nre"), codice_autenticazione=_stringa(d, "codAutenticazione"),
        data_inserimento=_stringa(d, "dataInserimento"), pdf_promemoria=promemoria, note=_note(d),
    )


_NON_TESTATA = {"elencoDettagliPrescrizioni", "elencoErroriRicette", "elencoComunicazioni", "elencoNota",
                "statoProcesso", "codAutenticazione", "dataInserimento", "codEsitoVisualizzazione"}


def leggi_ricevuta_visualizza(d: dict) -> EsitoVisualizzazione:
    verifica_risposta(d, "VisualizzaPrescrittoRicevuta")
    testata = {}
    for k in d:
        if k not in _NON_TESTATA:
            v = _stringa(d, k)
            if v:
                testata[k] = v
    righe = tuple({k: _stringa(r, k) for k in r if _stringa(r, k)}
                  for r in _elenco(d, "elencoDettagliPrescrizioni", "dettaglioPrescrizione"))
    return EsitoVisualizzazione(
        codice=_stringa(d, "codEsitoVisualizzazione", obbligatoria=True) or "",
        messaggi=_messaggi(d), comunicazioni=_comunicazioni(d),
        nre=_stringa(d, "nre"), stato_processo=_stringa(d, "statoProcesso"),
        codice_autenticazione=_stringa(d, "codAutenticazione"), data_inserimento=_stringa(d, "dataInserimento"),
        testata=testata, righe=righe,
    )


def leggi_ricevuta_annulla(d: dict) -> EsitoAnnullamento:
    verifica_risposta(d, "AnnullaPrescrittoRicevuta")
    return EsitoAnnullamento(
        codice=_stringa(d, "codEsitoAnnullamento", obbligatoria=True) or "",
        messaggi=_messaggi(d), comunicazioni=_comunicazioni(d),
        nre=_stringa(d, "nre", obbligatoria=True),
    )


def leggi_ricevuta_interroga_nre(d: dict) -> EsitoInterrogazioneNre:
    verifica_risposta(d, "InterrogaNreUtilRicevuta")
    ricette = tuple(
        NreUtilizzato(nre=_stringa(r, "nre"), cf_medico=_stringa(r, "cfMedico"), tipo=_stringa(r, "tipoPrescrizione"),
                      data_compilazione=_stringa(r, "dataCompilazioneRicetta"), cf_assistito=_stringa(r, "cfAssistito"),
                      provenienza=_stringa(r, "provenienza"), lotto=_stringa(r, "lotto"),
                      codice_autenticazione=_stringa(r, "codAutenticazione"))
        for r in _elenco(d, "elencoNreUtilRecord", "nreUtilRecord")
    )
    return EsitoInterrogazioneNre(
        codice=_stringa(d, "codEsitoInterrogaNreUtilizzati", obbligatoria=True) or "",
        messaggi=_messaggi(d), comunicazioni=_comunicazioni(d), ricette=ricette,
    )


@dataclass(frozen=True)
class LottoNRE:
    """Un lotto di NRE (MEF par. 4.1; costruzione dell'NRE come nella collection Postman ufficiale):
    NRE = codRegione + codRagLotto + identificativoLotto + codLotto + progressivo. Prefisso di 12
    caratteri: 1000 NRE (progressivo 000-999); di 13: 100 NRE (00-99).

    Il kit NON tiene il conto degli NRE usati: un NRE si usa una volta sola, anche dopo un invio
    incerto (502/504), e chi integra deve ricordarselo in modo persistente."""

    codice_regione: str
    codice_raggruppamento: str
    identificativo_lotto: str
    codice_lotto: str

    @property
    def prefisso(self) -> str:
        return self.codice_regione + self.codice_raggruppamento + self.identificativo_lotto + self.codice_lotto

    @property
    def dimensione(self) -> int:
        return {12: 1000, 13: 100}[len(self.prefisso)]

    def nre(self, progressivo: int) -> str:
        if not 0 <= progressivo < self.dimensione:
            raise ValueError(f"progressivo {progressivo}: il lotto ha {self.dimensione} NRE (0-{self.dimensione - 1})")
        cifre = len(str(self.dimensione - 1))
        return self.prefisso + str(progressivo).zfill(cifre)

    def contiene(self, nre: str) -> bool:
        return len(nre) == 15 and nre.startswith(self.prefisso) and nre[len(self.prefisso):].isdigit()


@dataclass(frozen=True)
class EsitoLottoNRE(Esito):
    """LottoRicevutaNRE. Riuscito con CodEsito «00» o «01» (come lo script della collection Postman)."""

    lotto: LottoNRE | None = None
    cf_medico: str | None = None

    @property
    def ok(self) -> bool:
        return self.codice in ("00", "01") and self.lotto is not None


def leggi_ricevuta_lotto(d: dict) -> EsitoLottoNRE:
    verifica_risposta(d, "LottoRicevutaNRE")
    codice = _stringa(d, "codEsito") or ""
    testo = _stringa(d, "esito")
    parti = [_stringa(d, k) for k in ("codRegione", "codRagLotto", "identificativoLotto", "codLotto")]
    lotto = None
    if codice in ("00", "01"):
        if not all(parti):
            raise RispostaNonConforme("lotto riuscito ma incompleto (codRegione, codRagLotto, identificativoLotto, codLotto)")
        lotto = LottoNRE(*parti)  # type: ignore[arg-type]
        if len(lotto.prefisso) not in (12, 13) or not lotto.prefisso[:3].isdigit():
            raise RispostaNonConforme(f"prefisso del lotto di {len(lotto.prefisso)} caratteri: attesi 12 o 13")
    messaggi = (Messaggio(codice or "?", testo, "0", "E"),) if codice not in ("00", "01") else ()
    return EsitoLottoNRE(codice=codice, messaggi=messaggi, lotto=lotto, cf_medico=_stringa(d, "cfmedico"))


def leggi_ricevuta_sostituzione(d: dict) -> Esito:
    verifica_risposta(d, "DichiarazioneSostituzioneMedicoRicevuta")
    return Esito(codice=_stringa(d, "codEsitoInserimento", obbligatoria=True) or "",
                 messaggi=_messaggi(d, "elencoErrori", "errore"), comunicazioni=_comunicazioni(d))


def verifica_problemi(ricetta: Ricetta) -> None:
    problemi = ricetta.problemi() + problemi_umbria(ricetta)
    if problemi:
        raise RicettaNonValida(problemi)
