# SPDX-License-Identifier: EUPL-1.2
"""Codec tra il modello `Ricetta` e il tracciato XML del SAC (XSD del kit MEF, v1.2).

Qui, e solo qui, stanno i nomi dei tag e il loro ordine (xs:sequence).
La cifratura di CF assistito e pincode avviene qui, con il cifratore iniettato.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Callable

from .modello import (
    Comunicazione,
    CriteriNreUtilizzati,
    EsitoAnnullamento,
    EsitoInterrogazioneNre,
    EsitoInvio,
    EsitoVisualizzazione,
    Messaggio,
    NotaPrestazione,
    NreUtilizzato,
    Ricetta,
    Riga,
)

NS_INVIO_RICH = "http://invioprescrittorichiesta.xsd.dem.sanita.finanze.it"
NS_INVIO_RIC = "http://invioprescrittoricevuta.xsd.dem.sanita.finanze.it"
NS_VIS_RICH = "http://visualizzaprescrittorichiesta.xsd.dem.sanita.finanze.it"
NS_VIS_RIC = "http://visualizzaprescrittoricevuta.xsd.dem.sanita.finanze.it"
NS_ANN_RICH = "http://annullaprescrittorichiesta.xsd.dem.sanita.finanze.it"
NS_ANN_RIC = "http://annullaprescrittoricevuta.xsd.dem.sanita.finanze.it"
NS_TIPI = "http://tipodati.xsd.dem.sanita.finanze.it"
NS_NRE_RICH = "http://interroganreutilrichiesta.xsd.dem.sanita.finanze.it"
NS_NRE_RIC = "http://interroganreutilricevuta.xsd.dem.sanita.finanze.it"

SOAP_ACTION_INVIO = "http://invioprescritto.wsdl.dem.sanita.finanze.it/InvioPrescritto"
SOAP_ACTION_VISUALIZZA = "http://visualizzaprescritto.wsdl.dem.sanita.finanze.it/VisualizzaPrescritto"
SOAP_ACTION_ANNULLA = "http://annullaprescritto.wsdl.dem.sanita.finanze.it/AnnullaPrescritto"
# Nel WSDL il soapAction dice "interroganreassociati", non "interroganreutilizzati": si usa quello del WSDL.
SOAP_ACTION_INTERROGA_NRE = "http://interroganreassociati.wsdl.dem.sanita.finanze.it/InterrogaNreUtilizzati"

for _p, _ns in (
    ("inv", NS_INVIO_RICH),
    ("vis", NS_VIS_RICH),
    ("ann", NS_ANN_RICH),
    ("int", NS_NRE_RICH),
    ("tip", NS_TIPI),
):
    ET.register_namespace(_p, _ns)

Cifra = Callable[[str], str]

# Ordine dei tag di testata (xs:sequence di InvioPrescrittoRichiesta), escluso il blocco esteri.
_ORDINE_TESTATA = (
    "pinCode", "cfMedico1", "cfMedico2", "codRegione", "codASLAo", "codStruttura",
    "codSpecializzazione", "testata1", "testata2", "nre", "tipoRic", "codiceAss",
    "cognNome", "indirizzo", "oscuramDati", "numTessSasn", "socNavigaz",
    "tipoPrescrizione", "ricettaInterna", "codEsenzione", "nonEsente", "reddito",
    "codDiagnosi", "descrizioneDiagnosi", "dataCompilazione", "tipoVisita", "dispReg",
    "provAssistito", "aslAssistito", "indicazionePrescr", "altro", "classePriorita",
)
_ORDINE_ESTERI = (
    "statoEstero", "istituzCompetente", "numIdentPers", "numIdentTess",
    "dataNascitaEstero", "dataScadTessera",
)
_ORDINE_RIGA = (
    "codProdPrest", "descrProdPrest", "codGruppoEquival", "descrGruppoEquival", "testoLibero",
    "descrTestoLiberoNote", "nonSost", "motivazNote", "codMotivazione", "notaProd", "quantita",
    "prescrizione1", "prescrizione2", "codCatalogoPrescr", "tipoAccesso", "numeroNota",
    "condErogabilita", "approprPrescrittiva", "patologia", "numsedute",
)


def _flag(v: bool) -> str | None:
    return "1" if v else None


def _campi_riga(r: Riga) -> dict[str, str | None]:
    return {
        "codProdPrest": r.codice,
        "descrProdPrest": r.descrizione,
        "codGruppoEquival": r.codice_gruppo_equivalenza,
        "descrGruppoEquival": r.descrizione_gruppo_equivalenza,
        "testoLibero": None,  # da specifica: unico valore ammesso è null
        "descrTestoLiberoNote": r.note_prestazione,
        "nonSost": _flag(r.non_sostituibile),
        "motivazNote": r.note,
        "codMotivazione": r.codice_motivazione_non_sost,
        "notaProd": r.nota_aifa,
        "quantita": str(r.quantita),
        "prescrizione1": r.prescrizione1,
        "prescrizione2": r.prescrizione2,
        "codCatalogoPrescr": r.codice_catalogo,
        "tipoAccesso": r.tipo_accesso,
        "numeroNota": r.numero_nota,
        "condErogabilita": r.condizione_erogabilita,
        "approprPrescrittiva": r.appropriatezza,
        "patologia": r.patologia,
        "numsedute": None if r.num_sedute is None else str(r.num_sedute),
    }


def campi_testata(ricetta: Ricetta, pincode_cifrato: str, cf_assistito_cifrato: str | None) -> dict[str, str | None]:
    pr, a = ricetta.prescrittore, ricetta.assistito
    return {
        "pinCode": pincode_cifrato,
        "cfMedico1": pr.codice_fiscale,
        "cfMedico2": pr.codice_fiscale_sostituto,
        "codRegione": pr.codice_regione,
        "codASLAo": pr.codice_asl,
        "codStruttura": pr.codice_struttura,
        "codSpecializzazione": pr.codice_specializzazione,
        "testata1": ricetta.testata1,
        "testata2": ricetta.testata2,
        "nre": ricetta.nre,
        "tipoRic": a.tipo_ricetta,
        "codiceAss": cf_assistito_cifrato,
        "cognNome": a.cognome_nome,
        "indirizzo": a.indirizzo,
        "oscuramDati": _flag(a.oscura_dati),
        "numTessSasn": a.num_tessera_sasn,
        "socNavigaz": a.societa_navigazione,
        "tipoPrescrizione": ricetta.tipo.value,
        "ricettaInterna": _flag(ricetta.ricetta_interna),
        "codEsenzione": ricetta.codice_esenzione,
        "nonEsente": _flag(ricetta.non_esente),
        "reddito": _flag(ricetta.esente_reddito),
        "codDiagnosi": ricetta.codice_diagnosi,
        "descrizioneDiagnosi": ricetta.descrizione_diagnosi,
        "dataCompilazione": ricetta.data_compilazione.strftime("%Y-%m-%d %H:%M:%S"),
        "tipoVisita": ricetta.tipo_visita.value,
        "dispReg": ricetta.disposizioni_regionali,
        "provAssistito": a.provincia,
        "aslAssistito": a.asl,
        "indicazionePrescr": ricetta.indicazione,
        "altro": ricetta.altro,
        "classePriorita": ricetta.classe_priorita.value if ricetta.classe_priorita else None,
        "statoEstero": a.stato_estero,
        "istituzCompetente": a.istituzione_competente,
        "numIdentPers": a.num_ident_personale,
        "numIdentTess": a.num_ident_tessera,
        "dataNascitaEstero": a.data_nascita_estero,
        "dataScadTessera": a.data_scadenza_tessera,
    }


def _sub(parent: ET.Element, ns: str, tag: str, valore: str | None) -> ET.Element:
    el = ET.SubElement(parent, f"{{{ns}}}{tag}")
    if valore is not None:
        el.text = valore
    return el


def richiesta_invio(ricetta: Ricetta, pincode: str, cifra: Cifra) -> ET.Element:
    """Costruisce <InvioPrescrittoRichiesta>. `pincode` in chiaro, cifrato qui."""
    cf_ass = ricetta.assistito.codice_fiscale
    campi = campi_testata(ricetta, cifra(pincode), cifra(cf_ass) if cf_ass else None)
    radice = ET.Element(f"{{{NS_INVIO_RICH}}}InvioPrescrittoRichiesta")
    for tag in _ORDINE_TESTATA:
        # Tutti i tag di testata presenti (par. 3.4: "tutti i tag ... devono essere presenti"),
        # vuoti quando non valorizzati, come nel progetto SoapUI del kit.
        _sub(radice, NS_INVIO_RICH, tag, campi[tag])
    if any(campi[t] for t in _ORDINE_ESTERI):
        for tag in _ORDINE_ESTERI:
            _sub(radice, NS_INVIO_RICH, tag, campi[tag])
    elenco = ET.SubElement(radice, f"{{{NS_INVIO_RICH}}}ElencoDettagliPrescrizioni")
    for riga in ricetta.righe:
        dett = ET.SubElement(elenco, f"{{{NS_TIPI}}}DettaglioPrescrizione")
        valori = _campi_riga(riga)
        for tag in _ORDINE_RIGA:
            if valori[tag] is not None or tag == "quantita":
                _sub(dett, NS_TIPI, tag, valori[tag])
    return radice


def richiesta_visualizza(nre: str, cf_medico: str, pincode: str, cifra: Cifra) -> ET.Element:
    radice = ET.Element(f"{{{NS_VIS_RICH}}}VisualizzaPrescrittoRichiesta")
    _sub(radice, NS_VIS_RICH, "pinCode", cifra(pincode))
    _sub(radice, NS_VIS_RICH, "nre", nre)
    _sub(radice, NS_VIS_RICH, "cfMedico", cf_medico)
    return radice


def richiesta_annulla(nre: str, cf_medico: str, pincode: str, cifra: Cifra) -> ET.Element:
    radice = ET.Element(f"{{{NS_ANN_RICH}}}AnnullaPrescrittoRichiesta")
    _sub(radice, NS_ANN_RICH, "pinCode", cifra(pincode))
    _sub(radice, NS_ANN_RICH, "nre", nre)
    _sub(radice, NS_ANN_RICH, "cfMedico", cf_medico)
    return radice


def _data_ora(d) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def richiesta_interroga_nre(criteri: CriteriNreUtilizzati, cf_medico: str, pincode: str, cifra: Cifra) -> ET.Element:
    """<InterrogaNreUtilRichiesta>. Ordine da xs:sequence; i facoltativi assenti non si mandano.

    Le date sono "aaaa-mm-gg hh:mm:ss": la specifica scrive "aaaa-mm-gg", ma lo
    schema (dataOraType) vuole esattamente 19 caratteri.
    """
    radice = ET.Element(f"{{{NS_NRE_RICH}}}InterrogaNreUtilRichiesta")
    valori = [
        ("pinCode", cifra(pincode)),
        ("codRegione", criteri.codice_regione),
        ("nre", criteri.nre),
        ("codLotto", criteri.codice_lotto),
        ("cfMedico", cf_medico),
        ("cfAssistito", criteri.cf_assistito),
        ("tipoPrescr", criteri.tipo.value if criteri.tipo else None),
        ("dataCompilazioneRicettaDal", _data_ora(criteri.dal) if criteri.dal else None),
        ("dataCompilazioneRicettaAl", _data_ora(criteri.al) if criteri.al else None),
    ]
    for tag, valore in valori:
        if valore is not None or tag in ("pinCode", "codRegione", "cfMedico"):
            _sub(radice, NS_NRE_RICH, tag, valore)
    return radice


# ----------------------------------------------------------- lettura ----
# In lettura si ignorano i namespace: il SAC usa namespace diversi per ricevuta
# e tipi dati, e un parser tollerante ai prefissi è più robusto nel tempo.


def _locale(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _figlio(el: ET.Element, nome: str) -> ET.Element | None:
    for c in el:
        if _locale(c.tag) == nome:
            return c
    return None


def _testo(el: ET.Element, nome: str) -> str | None:
    c = _figlio(el, nome)
    if c is None or c.text is None:
        return None
    t = c.text.strip()
    return t or None


def _messaggi(el: ET.Element) -> tuple[Messaggio, ...]:
    elenco = _figlio(el, "ElencoErroriRicette")
    if elenco is None:
        return ()
    out = []
    for e in elenco:
        out.append(
            Messaggio(
                codice=_testo(e, "codEsito") or "",
                testo=_testo(e, "esito"),
                progressivo=_testo(e, "progPresc"),
                tipo=_testo(e, "tipoErrore"),
            )
        )
    return tuple(out)


def _comunicazioni(el: ET.Element) -> tuple[Comunicazione, ...]:
    elenco = _figlio(el, "ElencoComunicazioni")
    if elenco is None:
        return ()
    return tuple(Comunicazione(_testo(c, "codice") or "", _testo(c, "messaggio") or "") for c in elenco)


def _verifica_radice(el: ET.Element, atteso: str) -> None:
    if _locale(el.tag) != atteso:
        raise ValueError(f"Risposta inattesa: <{_locale(el.tag)}> invece di <{atteso}>")


def leggi_ricevuta_invio(el: ET.Element) -> EsitoInvio:
    import base64

    _verifica_radice(el, "InvioPrescrittoRicevuta")
    pdf = _testo(el, "pdfPromemoria")
    return EsitoInvio(
        codice=_testo(el, "codEsitoInserimento") or "",
        messaggi=_messaggi(el),
        comunicazioni=_comunicazioni(el),
        nre=_testo(el, "nre"),
        codice_autenticazione=_testo(el, "codAutenticazione"),
        data_inserimento=_testo(el, "dataInserimento"),
        pdf_promemoria=base64.b64decode(pdf) if pdf else None,
        note=_note(el),
    )


def _note(el: ET.Element) -> tuple[NotaPrestazione, ...]:
    """ElencoNota/Nota (TipiDati, notaType): progrPresc, codProdPrest, tipoAmbulatorio."""
    elenco = _figlio(el, "ElencoNota")
    if elenco is None:
        return ()
    return tuple(
        NotaPrestazione(_testo(n, "progrPresc"), _testo(n, "codProdPrest"), _testo(n, "tipoAmbulatorio"))
        for n in elenco
        if _locale(n.tag) == "Nota"
    )


_TAG_NON_TESTATA = {
    "ElencoDettagliPrescrizioni", "ElencoErroriRicette", "ElencoComunicazioni", "ElencoNota",
    "statoProcesso", "codAutenticazione", "dataInserimento", "codEsitoVisualizzazione",
}


def leggi_ricevuta_visualizza(el: ET.Element) -> EsitoVisualizzazione:
    _verifica_radice(el, "VisualizzaPrescrittoRicevuta")
    testata = {
        _locale(c.tag): (c.text or "").strip()
        for c in el
        if _locale(c.tag) not in _TAG_NON_TESTATA and (c.text or "").strip()
    }
    righe = []
    elenco = _figlio(el, "ElencoDettagliPrescrizioni")
    if elenco is not None:
        for d in elenco:
            righe.append({_locale(c.tag): (c.text or "").strip() for c in d if (c.text or "").strip()})
    return EsitoVisualizzazione(
        codice=_testo(el, "codEsitoVisualizzazione") or "",
        messaggi=_messaggi(el),
        comunicazioni=_comunicazioni(el),
        nre=_testo(el, "nre"),
        stato_processo=_testo(el, "statoProcesso"),
        codice_autenticazione=_testo(el, "codAutenticazione"),
        data_inserimento=_testo(el, "dataInserimento"),
        testata=testata,
        righe=tuple(righe),
    )


def leggi_ricevuta_annulla(el: ET.Element) -> EsitoAnnullamento:
    _verifica_radice(el, "AnnullaPrescrittoRicevuta")
    return EsitoAnnullamento(
        codice=_testo(el, "codEsitoAnnullamento") or "",
        messaggi=_messaggi(el),
        comunicazioni=_comunicazioni(el),
        nre=_testo(el, "nre"),
    )


def leggi_ricevuta_interroga_nre(el: ET.Element) -> EsitoInterrogazioneNre:
    _verifica_radice(el, "InterrogaNreUtilRicevuta")
    ricette = []
    elenco = _figlio(el, "ElencoNreUtilRecord")
    if elenco is not None:
        for r in elenco:
            ricette.append(
                NreUtilizzato(
                    nre=_testo(r, "nre"),
                    cf_medico=_testo(r, "cfMedico"),
                    tipo=_testo(r, "tipoPrescrizione"),
                    data_compilazione=_testo(r, "dataCompilazioneRicetta"),
                    cf_assistito=_testo(r, "cfAssistito"),
                    provenienza=_testo(r, "provenienza"),
                    lotto=_testo(r, "lotto"),
                    codice_autenticazione=_testo(r, "codAutenticazione"),
                )
            )
    return EsitoInterrogazioneNre(
        codice=_testo(el, "codEsitoInterrogaNreUtilizzati") or "",
        messaggi=_messaggi(el),
        comunicazioni=_comunicazioni(el),
        ricette=tuple(ricette),
    )
