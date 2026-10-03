# SPDX-License-Identifier: EUPL-1.2
"""Codec tra il modello `Ricetta` e il tracciato del SAR FVG (Insiel, `wsdl_prescritto.zip`).

Il SAR FVG usa i tracciati del SAC «senza variazioni» (Idof-dem-AT-01, par. 2.2). Le differenze,
tutte dagli XSD pubblicati, sono:

  - namespace propri: `http://<messaggio>.xsd.dem.sanita.fvg.it-v1.0` (la lista degli NRE
    utilizzati usa namespace senza versione: `...sanita.fvg.it`);
  - attributo obbligatorio `tip:prodottoCme` sulla radice di invio, visualizzazione e
    annullamento (e verifica del sostituto); attributo `tip:versioneCR` su
    `ElencoDettagliPrescrizioni` (par. 3.1: da indicare per la specialistica);
  - `pinCode` resta un elemento obbligatorio ma «non utilizzato»: si manda vuoto;
  - `DettaglioPrescrizione` NON ha `numsedute` (lo schema FVG è precedente a quel campo);
  - tipi più stretti: `tipoRic` 2 caratteri, `indicazionePrescr`, `altro`, `classePriorita`
    1 carattere, `nre` 15. Un tag vuoto non è valido: i facoltativi si mandano solo se valorizzati
    (nel SAC il kit li manda tutti, anche vuoti, come il progetto SoapUI del MEF);
  - servizio regionale in più: `VerificaPosizioneMedicoSostituto` (GestoreAutorizzazioni).

La lettura delle ricevute è quella del SAC (`xml_sac.leggi_*`): confronta i nomi locali dei tag,
quindi i namespace FVG non cambiano niente.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Callable

from ..errori import RicettaNonValida
from . import xml_sac
from .modello import CriteriNreUtilizzati, Messaggio, Ricetta, TipoPrescrizione

_V = "-v1.0"
NS_INVIO_RICH = f"http://invioprescrittorichiesta.xsd.dem.sanita.fvg.it{_V}"
NS_INVIO_RIC = f"http://invioprescrittoricevuta.xsd.dem.sanita.fvg.it{_V}"
NS_VIS_RICH = f"http://visualizzaprescrittorichiesta.xsd.dem.sanita.fvg.it{_V}"
NS_VIS_RIC = f"http://visualizzaprescrittoricevuta.xsd.dem.sanita.fvg.it{_V}"
NS_ANN_RICH = f"http://annullaprescrittorichiesta.xsd.dem.sanita.fvg.it{_V}"
NS_ANN_RIC = f"http://annullaprescrittoricevuta.xsd.dem.sanita.fvg.it{_V}"
NS_SOST_RICH = f"http://verificaposizionemedicosostitutorichiesta.xsd.dem.sanita.fvg.it{_V}"
NS_SOST_RIC = f"http://verificaposizionemedicosostitutoricevuta.xsd.dem.sanita.fvg.it{_V}"
NS_TIPI = f"http://tipodati.xsd.dem.sanita.fvg.it{_V}"
# lista degli NRE utilizzati: namespace SENZA versione (InterrogaNreUtilRichiesta.xsd dello zip)
NS_NRE_RICH = "http://interroganreutilrichiesta.xsd.dem.sanita.fvg.it"
NS_NRE_RIC = "http://interroganreutilricevuta.xsd.dem.sanita.fvg.it"

# Prefissi come nell'esempio del par. 3.1 (inv, tip). ElementTree tiene una sola tabella di prefissi
# per processo: per i namespace FVG si usano prefissi diversi da quelli del SAC.
for _p, _ns in (
    ("finv", NS_INVIO_RICH),
    ("fvis", NS_VIS_RICH),
    ("fann", NS_ANN_RICH),
    ("fsost", NS_SOST_RICH),
    ("fint", NS_NRE_RICH),
    ("ftip", NS_TIPI),
):
    ET.register_namespace(_p, _ns)

Cifra = Callable[[str], str]

# Codici di errore per cui il software deve scalare in ricetta rossa (MIR) senza disturbare il medico
# (par. 2.3.6, solo specialistica). Sono di 6 cifre: dove arrivino non è detto (sez. 7 di SAR_FVG.md).
DOWNGRADE_MIR = range(60120, 60131)

# Campi del modello che il tracciato FVG non ha.
_ASSENTI_IN_FVG = ("numsedute",)
# Campi di riga riservati alla specialistica (p. 21): (attributo di Riga, tag del tracciato)
_SOLO_SPECIALISTICA = (("codice_catalogo", "codCatalogoPrescr"), ("tipo_accesso", "tipoAccesso"),
                       ("numero_nota", "numeroNota"))
_STP_ENI = re.compile(r"(STP|ENI)[0-9]{13}")


def problemi_fvg(ricetta: Ricetta) -> list[str]:
    """Controlli locali di forma che valgono solo nel SAR FVG (oltre a `Ricetta.problemi()`)."""
    p: list[str] = []
    for i, r in enumerate(ricetta.righe, start=1):
        if r.num_sedute is not None:
            p.append(f"riga {i}: il tracciato del SAR FVG non ha il numero di sedute (numsedute)")
        if (r.prescrizione1 or "").strip().upper().startswith("TV"):
            # par. 4.2.2: in FVG la televisita si esprime col codice di catalogo regionale, «TV» non è ammesso
            p.append(f"riga {i}: in FVG la televisita si indica col codice di catalogo regionale, non con prescrizione1=TV")
        if not (r.codice_catalogo or "").strip() and ricetta.tipo is TipoPrescrizione.SPECIALISTICA:
            # "" vale assente: il codec non manda tag vuoti, e la riga partirebbe senza catalogo
            p.append(f"riga {i}: specialistica senza codice di catalogo regionale (codCatalogoPrescr, par. 4.2, p. 21)")
        if ricetta.tipo is TipoPrescrizione.FARMACEUTICA:
            # p. 21: codCatalogoPrescr e tipoAccesso «da utilizzarsi unicamente per prescrizioni
            # specialistiche», numeroNota «unicamente per le prescrizioni specialistiche trattate dal
            # DM 9 dic 2015» (revisione esterna giro 2, 4-sar-fvg N2: lo XSD non lo vede)
            for campo, tag in _SOLO_SPECIALISTICA:
                if (getattr(r, campo) or "").strip():
                    p.append(f"riga {i}: {tag} vale solo per la specialistica, non per la farmaceutica (p. 21)")
    if ricetta.prescrittore.codice_regione != "060":
        # p. 16: codRegione «Codice Regione del medico prescrittore "060"» (revisione esterna giro 2, N5)
        p.append(f"codRegione {ricetta.prescrittore.codice_regione!r}: nel SAR FVG è \"060\" (p. 16)")
    a = ricetta.assistito
    if a.codice_fiscale and len(a.codice_fiscale) > 16:
        p.append("codice assistito: al massimo 16 caratteri")
    if (a.codice_fiscale or "").upper().startswith(("STP", "ENI")) and not _STP_ENI.fullmatch(a.codice_fiscale.upper()):
        # p. 18 ammette «Codice Fiscale/STP/ENI/altro» ma non descrive la forma dello STP/ENI: qui è
        # quella che il server finto già controllava, prefisso + 13 cifre (16 caratteri come il CF).
        # Un codice troncato al prefisso passava come «altro» (revisione esterna giro 2, 4-sar-fvg N3).
        p.append(f"codice {a.codice_fiscale!r}: un codice STP/ENI è il prefisso seguito da 13 cifre")
    if (a.codice_fiscale or "").upper().startswith("STP") and a.tipo_ricetta != "ST":
        # p. 18: «Il Codice assistito deve essere coerente con quanto indicato nel campo Tipo Ricetta»
        p.append("codice STP: serve il tipo ricetta ST (stranieri in temporaneo soggiorno, p. 18)")
    return p


# [0-9] e non \d: \d ammette le cifre Unicode («١.٤.٤»), che il server rifiuta (revisione esterna giro 2, 4-sar-fvg N6)
_VERSIONE_CR = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def problemi_versione_cr(ricetta: Ricetta, versione_cr: str | None) -> list[str]:
    """Par. 3.1, p. 13: la versione del catalogo regionale va indicata «obbligatoriamente» nelle
    ricette di specialistica, «comprensivo della terza cifra che identifica la patch»."""
    if ricetta.tipo is not TipoPrescrizione.SPECIALISTICA:
        return []
    v = (versione_cr or "").strip()
    if not v:
        return ["specialistica senza versioneCR: serve ApplicativoFVG.versione_cr (par. 3.1, p. 13)"]
    if not _VERSIONE_CR.fullmatch(v):
        return [f"versioneCR {v!r}: servono tre numeri, patch compresa, es. 1.4.4 (par. 3.1, p. 13)"]
    return []


def _sub(parent: ET.Element, ns: str, tag: str, valore: str | None) -> ET.Element:
    el = ET.SubElement(parent, f"{{{ns}}}{tag}")
    if valore is not None:
        el.text = valore
    return el


def _attributo_prodotto(radice: ET.Element, prodotto_cme: str) -> None:
    if not (prodotto_cme or "").strip():
        raise ValueError("prodottoCme obbligatorio (attributo td:prodottoCme, use=required)")
    radice.set(f"{{{NS_TIPI}}}prodottoCme", prodotto_cme)


def richiesta_invio(ricetta: Ricetta, cifra: Cifra, prodotto_cme: str, versione_cr: str | None = None) -> ET.Element:
    """<InvioPrescrittoRichiesta> del SAR FVG. `cifra`: cifratura del CF dell'assistito (par. 4.6).

    Specialistica con una riga senza catalogo (anche ""): RicettaNonValida, sempre. Lo XSD lo lascia
    facoltativo, la specifica no, e un "" spariva in silenzio (revisione esterna 02/10/2026).
    `versione_cr` la impone `RicettaFVG.invia` (`problemi_versione_cr`); qui, se c'è, si scrive.
    """
    if ricetta.tipo is TipoPrescrizione.SPECIALISTICA:
        problemi = [p for p in problemi_fvg(ricetta) if "codCatalogoPrescr" in p]
        if problemi:
            raise RicettaNonValida(problemi)
    cf_ass = ricetta.assistito.codice_fiscale
    campi = xml_sac.campi_testata(ricetta, "", cifra(cf_ass) if cf_ass else None)
    campi = {k: (None if v == "" else v) for k, v in campi.items()}  # vuoto = assente: un tag vuoto non valida
    campi["pinCode"] = None  # «non utilizzato»: elemento obbligatorio, vuoto
    radice = ET.Element(f"{{{NS_INVIO_RICH}}}InvioPrescrittoRichiesta")
    _attributo_prodotto(radice, prodotto_cme)
    obbligatori = {"pinCode", "cfMedico1", "codRegione", "codASLAo", "codSpecializzazione",
                   "tipoPrescrizione", "dataCompilazione", "tipoVisita"}
    for tag in xml_sac._ORDINE_TESTATA + xml_sac._ORDINE_ESTERI:
        if tag in obbligatori or campi[tag] is not None:
            _sub(radice, NS_INVIO_RICH, tag, campi[tag])
    elenco = ET.SubElement(radice, f"{{{NS_INVIO_RICH}}}ElencoDettagliPrescrizioni")
    if (versione_cr or "").strip() and ricetta.tipo is TipoPrescrizione.SPECIALISTICA:  # par. 3.1: «nel caso di ricette di specialistica»
        elenco.set(f"{{{NS_TIPI}}}versioneCR", versione_cr.strip())  # attributo globale di TipiDati: qualificato
    for riga in ricetta.righe:
        dett = ET.SubElement(elenco, f"{{{NS_TIPI}}}DettaglioPrescrizione")
        valori = xml_sac._campi_riga(riga)
        for tag in xml_sac._ORDINE_RIGA:
            if tag in _ASSENTI_IN_FVG:
                continue
            if valori[tag] not in (None, "") or tag == "quantita":
                _sub(dett, NS_TIPI, tag, valori[tag])
    return radice


def _nre_cf(radice_tag: str, ns: str, nre: str, cf_medico: str, prodotto_cme: str) -> ET.Element:
    radice = ET.Element(f"{{{ns}}}{radice_tag}")
    _attributo_prodotto(radice, prodotto_cme)
    _sub(radice, ns, "pinCode", None)
    _sub(radice, ns, "nre", nre)
    _sub(radice, ns, "cfMedico", cf_medico)
    return radice


def richiesta_visualizza(nre: str, cf_medico: str, prodotto_cme: str) -> ET.Element:
    return _nre_cf("VisualizzaPrescrittoRichiesta", NS_VIS_RICH, nre, cf_medico, prodotto_cme)


def richiesta_annulla(nre: str, cf_medico: str, prodotto_cme: str) -> ET.Element:
    return _nre_cf("AnnullaPrescrittoRichiesta", NS_ANN_RICH, nre, cf_medico, prodotto_cme)


def richiesta_verifica_sostituto(cf_titolare: str, cf_sostituto: str, codice_asl: str, prodotto_cme: str) -> ET.Element:
    """<VerificaPosizioneMedicoSostitutoRichiesta> (GestoreAutorizzazioni, servizio solo regionale)."""
    radice = ET.Element(f"{{{NS_SOST_RICH}}}VerificaPosizioneMedicoSostitutoRichiesta")
    _attributo_prodotto(radice, prodotto_cme)
    _sub(radice, NS_SOST_RICH, "pinCode", None)
    _sub(radice, NS_SOST_RICH, "cfMedicoTitolare", cf_titolare)
    _sub(radice, NS_SOST_RICH, "cfMedicoSostituto", cf_sostituto)
    _sub(radice, NS_SOST_RICH, "codASLAo", codice_asl)
    return radice


def codice_lotto_fvg(codice_lotto: str) -> str:
    """Dal lotto in forma SAC (regione 3 + raggruppamento 2 + tipo lotto 1 + codice 6-7, cioè l'NRE
    senza il progressivo) al `codLotto` FVG, che lo schema vuole di sole cifre, al massimo 7
    (`codLottoType`, pattern [0-9]{1,7}). La composizione dell'NRE è quella del par. 4.2.
    DA CONFERMARE con Insiel: la specifica non dice che cosa contenga `codLotto` nella ricerca."""
    codice = codice_lotto[6:]
    if not (codice.isdigit() and 1 <= len(codice) <= 7):
        raise ValueError(f"codice lotto non riconducibile al codLotto FVG (cifre, max 7): {codice_lotto!r}")
    return codice


def richiesta_interroga_nre(criteri: CriteriNreUtilizzati, cf_medico: str) -> ET.Element:
    """<InterrogaNreUtilRichiesta> del SAR FVG: stesso ordine del SAC, namespace senza versione,
    niente attributo prodottoCme (lo schema non lo prevede), pinCode vuoto, CF dell'assistito in chiaro."""
    radice = ET.Element(f"{{{NS_NRE_RICH}}}InterrogaNreUtilRichiesta")
    valori = [
        ("pinCode", None),
        ("codRegione", criteri.codice_regione),
        ("nre", criteri.nre),
        ("codLotto", codice_lotto_fvg(criteri.codice_lotto) if criteri.codice_lotto else None),
        ("cfMedico", cf_medico),
        ("cfAssistito", criteri.cf_assistito),
        ("tipoPrescr", criteri.tipo.value if criteri.tipo else None),
        ("dataCompilazioneRicettaDal", xml_sac._data_ora(criteri.dal) if criteri.dal else None),
        ("dataCompilazioneRicettaAl", xml_sac._data_ora(criteri.al) if criteri.al else None),
    ]
    for tag, valore in valori:
        if valore is not None or tag in ("pinCode", "codRegione", "cfMedico"):
            _sub(radice, NS_NRE_RICH, tag, valore)
    return radice


# ----------------------------------------------------------- lettura ----

leggi_ricevuta_invio = xml_sac.leggi_ricevuta_invio
leggi_ricevuta_visualizza = xml_sac.leggi_ricevuta_visualizza
leggi_ricevuta_annulla = xml_sac.leggi_ricevuta_annulla
leggi_ricevuta_interroga_nre = xml_sac.leggi_ricevuta_interroga_nre


def leggi_verifica_sostituto(el: ET.Element) -> dict:
    """Campi di <VerificaPosizioneMedicoSostitutoRicevuta>: CF, ASL, isAbilitato, message, errori."""
    xml_sac._verifica_radice(el, "VerificaPosizioneMedicoSostitutoRicevuta")
    abilitato = (xml_sac._testo(el, "isAbilitato") or "").lower()
    # Qui il contenitore degli errori si chiama ElencoErrori (XSD della ricevuta), non ElencoErroriRicette
    elenco = xml_sac._figlio(el, "ElencoErrori")
    messaggi = tuple(
        Messaggio(codice=xml_sac._testo(e, "codEsito") or "", testo=xml_sac._testo(e, "esito"),
                  progressivo=xml_sac._testo(e, "progPresc"), tipo=xml_sac._testo(e, "tipoErrore"))
        for e in (elenco if elenco is not None else ())
    )
    if abilitato not in ("true", "false", "1", "0"):
        if messaggi:  # un errore applicativo senza isAbilitato: non abilitato, con il motivo
            abilitato = "false"
        else:
            raise ValueError(f"isAbilitato non booleano: {abilitato!r}")
    return {
        "cf_titolare": xml_sac._testo(el, "cfMedicoTitolare"),
        "cf_sostituto": xml_sac._testo(el, "cfMedicoSostituto"),
        "codice_asl": xml_sac._testo(el, "codASLAo"),
        "abilitato": abilitato in ("true", "1"),
        "messaggio": xml_sac._testo(el, "message"),
        "messaggi": messaggi,
        "comunicazioni": xml_sac._comunicazioni(el),
    }


def e_codice_downgrade(codice: str | None) -> bool:
    """True per i codici 060120-060130 (par. 2.3.6)."""
    c = (codice or "").strip()
    return c.isdigit() and len(c) == 6 and int(c) in DOWNGRADE_MIR


def codice_downgrade_in_testo(testo: str | None) -> str | None:
    """Un codice 060120-060130 scritto come numero a sé in un testo (es. il faultstring di un SOAP Fault)."""
    import re

    for m in re.finditer(r"(?<!\d)(\d{6})(?!\d)", testo or ""):
        if e_codice_downgrade(m.group(1)):
            return m.group(1)
    return None


def messaggio_downgrade(codice: str, testo: str | None) -> Messaggio:
    return Messaggio(codice=codice, testo=testo, progressivo="0", tipo="E")
