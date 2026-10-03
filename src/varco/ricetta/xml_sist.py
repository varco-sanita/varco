# SPDX-License-Identifier: EUPL-1.2
"""Codec tra il modello `Ricetta` e il tracciato CVP del SIST (Regione Puglia, PDD ASL).

Fonti: `wsdl-pddasl/CVPService.xsd` e la javadoc della componente CVP, nelle Specifiche di
integrazione SIST v4.03.27 (16/09/2026). Qui, e solo qui, stanno i nomi dei tag del SIST e il
loro ordine (xs:sequence). Il modello resta quello del SAC: i nomi del tracciato SAC stanno
in `xml_sac.py`, quelli del SIST qui.

Cosa il modello NON ha e il SIST vuole, e da dove lo prendiamo:
  - codice regionale del medico (codMedicoPrescrittore, codMedicoSostituito): non è un
    dato della ricetta ma del medico in quella regione -> funzione `codice_regionale(cf)`
    fornita dalla configurazione del canale;
  - datiOperatore e datiApplicativo: dal canale (`trasporto/sist.py`);
  - cognome, nome, sesso, data e comune di nascita/residenza: facoltativi per gli assistiti
    in anagrafe regionale; se servono si passano con il `Paziente` del modulo FSE (che
    contiene lo stesso `Assistito`).

Formati: date "GG/MM/AAAA" e "GG/MM/AAAA HH:mm:ss" (javadoc chkPrescrizione); codice della
prestazione specialistica senza punti (javadoc PrestazionePrescritta.codPrestazione); nota
AIFA su tre caratteri con zeri a sinistra (storia revisioni 4.02.06).

I tag facoltativi non valorizzati NON si mandano (per JAX-WS un tag vuoto è "", non null).
"""

from __future__ import annotations

import datetime as _dt
import re
import xml.etree.ElementTree as ET
from typing import Callable

from ..errori import ErroreSOAP, RicettaNonValida
from ..trasporto.sist import DatiChiamata
from .modello import (
    Comunicazione,
    CriteriNreUtilizzati,
    EsitoAnnullamento,
    EsitoInterrogazioneNre,
    EsitoInvioSAR,
    EsitoVisualizzazioneSAR,
    Messaggio,
    NreUtilizzato,
    Ricetta,
    Riga,
    TipoPrescrizione,
)

NS = "www.sist.puglia.it/Schemas/PDD_SIST/SCATEL/"
ET.register_namespace("sist", NS)

# "numero di versione del documento di specifiche di integrazione PDDASL ... Il flusso DM 2011
# è attivo a partire dalla versione 4.00": la prima pagina della v4.03.27 dice "Versione 4.03".
VERSIONE_SPECIFICHE = "4.03"
ESENZIONE_NESSUNA = "NES00"
TIPOLOGIA = {TipoPrescrizione.FARMACEUTICA: "1", TipoPrescrizione.SPECIALISTICA: "3"}  # 2 = ricovero (non nel modello)

CodiceRegionale = Callable[[str], str]


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def _sub(parent: ET.Element, tag: str, valore: str | None, *, anche_vuoto: bool = False) -> ET.Element | None:
    if valore is None and not anche_vuoto:
        return None
    el = ET.SubElement(parent, _q(tag))
    if valore is not None:
        el.text = valore
    return el


def _testata(radice: ET.Element, dati: DatiChiamata) -> None:
    op = ET.SubElement(radice, _q("datiOperatore"))
    _sub(op, "codStruttura", dati.operatore.codice_struttura)
    _sub(op, "codiceFiscale", dati.operatore.codice_fiscale)
    _sub(op, "ruoloIstituzionale", dati.operatore.ruolo)
    app = ET.SubElement(radice, _q("datiApplicativo"))
    # xs:sequence di datiApplicativo: applDigest, created, nome, nonce, produttore, versione
    _sub(app, "applDigest", dati.appl_digest)
    _sub(app, "created", dati.created)
    _sub(app, "nome", dati.nome)
    _sub(app, "nonce", dati.nonce)
    _sub(app, "produttore", dati.produttore)
    _sub(app, "versione", dati.versione)


def data_sist(d: _dt.date) -> str:
    return d.strftime("%d/%m/%Y")


def data_ora_sist(d: _dt.datetime) -> str:
    return d.strftime("%d/%m/%Y %H:%M:%S")


def codice_prestazione_sist(codice: str) -> str:
    """Codice del nomenclatore senza il punto separatore (es. "89.7" -> "897", "93.39.8" -> "93398")."""
    return codice.replace(".", "").strip()


def nota_aifa_sist(nota: str | None) -> str | None:
    """Tre caratteri, allineata a destra con zeri: "1" -> "001" (storia revisioni 4.02.06)."""
    if not nota:
        return None
    n = nota.strip()
    return n.zfill(3) if n.isdigit() else n


def codice_nazionale_asl(codice_regione: str | None, asl: str | None) -> str | None:
    """Codice nazionale dell'ASL: regione (3) + ASL (3), es. "160" + "114" -> "160114"."""
    if codice_regione and asl:
        return codice_regione + asl.zfill(3)
    return None


def flag_ciclica(ricetta: Ricetta) -> str:
    """flagCiclica (obbligatorio): 1 se la ricetta contiene SOLO prestazioni cicliche.

    Il modello non ha il flag: lo ricaviamo dal numero di sedute, che il tracciato SAC
    valorizza solo per le prestazioni cicliche (DPCM 12/01/2017).
    """
    righe = ricetta.righe
    return "1" if righe and all(r.num_sedute for r in righe) else "0"


def _prestazione(parent: ET.Element, r: Riga, tipo: TipoPrescrizione) -> None:
    p = ET.SubElement(parent, _q("prestazione"))
    # xs:sequence: codGruppoEquivalenza, codMotivoNonSost, codPrestazione, nota, notaCUF,
    #              quantitaPrescritta, codCatalogoPrescr, tipoAccesso, numSedute
    if tipo is TipoPrescrizione.FARMACEUTICA:
        _sub(p, "codGruppoEquivalenza", r.codice_gruppo_equivalenza)
        _sub(p, "codMotivoNonSost", r.codice_motivazione_non_sost if r.non_sostituibile else None)
        _sub(p, "codPrestazione", r.codice)
        _sub(p, "nota", r.note)
        _sub(p, "notaCUF", nota_aifa_sist(r.nota_aifa))
        _sub(p, "quantitaPrescritta", str(r.quantita))
    else:
        _sub(p, "codPrestazione", codice_prestazione_sist(r.codice) if r.codice else None)
        _sub(p, "nota", r.note_prestazione)
        _sub(p, "quantitaPrescritta", str(r.quantita))
        _sub(p, "codCatalogoPrescr", r.codice_catalogo)
        _sub(p, "tipoAccesso", r.tipo_accesso)
        _sub(p, "numSedute", None if r.num_sedute is None else str(r.num_sedute))


def assistito_ssn(a) -> bool:
    """Assistito del SSN: niente tipoRic (javadoc chkPrescrizione: "null" = assistiti del SSN
    provvisti di codice fiscale), cioè tipo ricetta "IT" nel CDA (CDA2_Prescrizione p. 15). Gli
    altri (EE, UE, NA, ND, NE, NX, ST: assicurati esteri, SASN, STP) non lo sono."""
    return not a.tipo_ricetta


def problemi_sist(ricetta: Ricetta, codice_regionale: CodiceRegionale | None = None) -> list[str]:
    """Controlli di forma in più che servono al SIST (oltre a `Ricetta.problemi()`)."""
    p: list[str] = []
    a = ricetta.assistito
    if a.codice_fiscale and a.asl and not a.codice_regione:
        # CDA2_Prescrizione: la ASL di residenza è obbligatoria per gli assistiti SSN, come codice nazionale
        p.append("SIST: serve la regione dell'ASL dell'assistito (Assistito.codice_regione) per il codice nazionale")
    if assistito_ssn(a) and not a.asl:
        # CDA2_Prescrizione p. 2: participant ASL "obbligatorio per tutti gli assistiti SSN, mentre
        # risulta facoltativo in tutti gli altri casi (soggetti assicurati da istituzioni estere,
        # assistiti "STP", personale navigante iscritto al SASN)"
        p.append("SIST: serve l'ASL di residenza dell'assistito SSN (CDA2_Prescrizione p. 2, participant ASL)")
    if not ricetta.non_esente and not ricetta.codice_esenzione:
        p.append("SIST: codEsenzione è obbligatorio: un codice di esenzione oppure non_esente (NES00)")
    if ricetta.tipo is TipoPrescrizione.SPECIALISTICA:
        for i, r in enumerate(ricetta.righe, start=1):
            if r.codice and len(codice_prestazione_sist(r.codice)) > 5:
                p.append(f"riga {i}: codice prestazione senza punti oltre 5 caratteri ({r.codice})")
            if r.codice_catalogo and not r.tipo_accesso:
                # 4.03.18: "tipoAccesso obbligatorio" per le prestazioni del catalogo regionale
                p.append(f"riga {i}: con il codice di catalogo regionale serve il tipo di accesso (0 o 1)")
    if codice_regionale is not None:
        for cf in filter(None, (ricetta.prescrittore.codice_fiscale, ricetta.prescrittore.codice_fiscale_sostituto)):
            try:
                codice_regionale(cf)
            except KeyError:
                p.append(f"SIST: manca il codice regionale del medico {cf}")
    return p


def richiesta_chk(
    ricetta: Ricetta,
    dati: DatiChiamata,
    codice_regionale: CodiceRegionale,
    paziente=None,
) -> ET.Element:
    """<chkPrescrizione>. `paziente`: facoltativo, un `varco.fse.modello.Paziente` con lo
    stesso `Assistito` della ricetta, per nome, cognome, sesso, nascita e residenza."""
    if paziente is not None and paziente.assistito != ricetta.assistito:
        raise RicettaNonValida(["il Paziente passato non contiene l'Assistito della ricetta"])
    pr, a = ricetta.prescrittore, ricetta.assistito
    radice = ET.Element(_q("chkPrescrizione"))
    _testata(radice, dati)

    ass = ET.SubElement(radice, _q("assistito"))
    residenza = getattr(paziente, "residenza", None)
    oscura = a.oscura_dati
    _sub(ass, "codIdentificativoAssistito", a.codice_fiscale or a.num_ident_tessera)
    _sub(ass, "codNazionaleAslResidenza", codice_nazionale_asl(a.codice_regione, a.asl))
    if paziente is not None:
        _sub(ass, "cognome", paziente.cognome)
        _sub(ass, "dataNascita", data_sist(paziente.data_nascita) if paziente.data_nascita else None)
        _sub(ass, "nome", paziente.nome)
        _sub(ass, "nomeComuneResidenza", residenza.comune if residenza else None)
        _sub(ass, "sesso", paziente.sesso if paziente.sesso in ("M", "F") else None)
        # con l'oscuramento l'indirizzo "non deve essere avvalorato" (Appendice A, Oscuramento dati)
        _sub(ass, "indirizzoResidenza", None if oscura or residenza is None else residenza.via)
    _sub(ass, "numTessSasn", a.num_tessera_sasn)
    _sub(ass, "socNavigaz", a.societa_navigazione)
    _sub(ass, "statoEstero", a.stato_estero)
    _sub(ass, "istituzCompetente", a.istituzione_competente)
    _sub(ass, "numIdentPers", a.num_ident_personale)
    _sub(ass, "numIdentTess", a.num_ident_tessera)
    _sub(ass, "dataNascitaEstero", a.data_nascita_estero)
    _sub(ass, "dataScadTessera", a.data_scadenza_tessera)

    sostituto = pr.codice_fiscale_sostituto
    _sub(radice, "tipologia", TIPOLOGIA[ricetta.tipo])
    _sub(radice, "dataPrescrizione", data_ora_sist(ricetta.data_compilazione))
    _sub(radice, "iup", ricetta.nre)
    # Prescrive chi ha la CNS: il sostituto, se c'è. Il titolare va in codMedicoSostituito.
    _sub(radice, "codMedicoSostituito", codice_regionale(pr.codice_fiscale) if sostituto else None)
    _sub(radice, "codMedicoPrescrittore", codice_regionale(sostituto or pr.codice_fiscale))
    _sub(radice, "codEsenzione", ricetta.codice_esenzione or (ESENZIONE_NESSUNA if ricetta.non_esente else None))
    elenco = ET.SubElement(radice, _q("elencoPrestazioni"))
    for r in ricetta.righe:
        _prestazione(elenco, r, ricetta.tipo)
    _sub(radice, "flagCiclica", flag_ciclica(ricetta))
    _sub(radice, "versione", VERSIONE_SPECIFICHE)
    _sub(radice, "tipoRic", a.tipo_ricetta)
    _sub(radice, "oscuramDati", "1" if oscura else None)
    _sub(radice, "ricettaInterna", "1" if ricetta.ricetta_interna else None)
    _sub(radice, "tipoVisita", ricetta.tipo_visita.value)
    _sub(radice, "dispReg", ricetta.disposizioni_regionali)
    _sub(radice, "indicazionePrescr", ricetta.indicazione)
    _sub(radice, "classePriorita", ricetta.classe_priorita.value if ricetta.classe_priorita else None)
    _sub(radice, "codDiagnosi", ricetta.codice_diagnosi)
    _sub(radice, "descrizioneDiagnosi", ricetta.descrizione_diagnosi)
    _sub(radice, "testata1", ricetta.testata1)
    return radice


def richiesta_registra(dati: DatiChiamata, prescrizione: str, oscurato: bool, id_pcp: str | None = None) -> ET.Element:
    """<setRegistraPrescrizione>. `prescrizione`: il CDA firmato (p7m) come stringa (base64)."""
    radice = ET.Element(_q("setRegistraPrescrizione"))
    _testata(radice, dati)
    _sub(radice, "prescrizione", prescrizione)
    _sub(radice, "oscurato", "true" if oscurato else "false")
    _sub(radice, "idPCP", id_pcp)
    return radice


def richiesta_annulla(dati: DatiChiamata, nre: str) -> ET.Element:
    radice = ET.Element(_q("setAnnullaPrescrizione"))
    _testata(radice, dati)
    _sub(radice, "identificativoPrescrizione", nre)
    _sub(radice, "versione", VERSIONE_SPECIFICHE)
    return radice


def richiesta_identificata(dati: DatiChiamata, nre: str, cf_assistito: str, tipo_operazione: str | None = None) -> ET.Element:
    """<getPrescrizioneIdentificata>: identificazione "forte", NRE più CF dell'assistito."""
    radice = ET.Element(_q("getPrescrizioneIdentificata"))
    _testata(radice, dati)
    _sub(radice, "codiceFiscale", cf_assistito)
    _sub(radice, "identificativoRicetta", nre)
    _sub(radice, "versione", VERSIONE_SPECIFICHE)
    _sub(radice, "tipoOperazione", tipo_operazione)
    return radice


def richiesta_ricerca(dati: DatiChiamata, criteri: CriteriNreUtilizzati, codice_prescrittore: str) -> ET.Element:
    """<getPrescrizioniIdentificate>: è la cosa più vicina a InterrogaNreUtilizzati del SAC.

    Il SIST cerca per periodo (obbligatorio: errore 000061), prescrittore, assistito e tipo;
    non per NRE puntuale né per lotto.
    """
    if criteri.nre or criteri.codice_lotto:
        raise RicettaNonValida(["SIST: la ricerca per NRE puntuale o per lotto non esiste (getPrescrizioniIdentificate)"])
    if criteri.dal is None or criteri.al is None:
        raise RicettaNonValida(["SIST: serve un periodo di emissione (dal, al): errore 000061"])
    radice = ET.Element(_q("getPrescrizioniIdentificate"))
    _testata(radice, dati)
    _sub(radice, "codFiscale", criteri.cf_assistito)
    _sub(radice, "codPrescrittore", codice_prescrittore)
    _sub(radice, "tipoPrescr", TIPOLOGIA[criteri.tipo] if criteri.tipo else "4")  # 4 = tutti i tipi
    _sub(radice, "dataEmissioneDal", data_sist(criteri.dal))
    _sub(radice, "dataEmissioneAl", data_sist(criteri.al))
    _sub(radice, "versione", VERSIONE_SPECIFICHE)
    return radice


# ------------------------------------------------------------------ lettura delle risposte


def _locale(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _figlio(el: ET.Element | None, nome: str) -> ET.Element | None:
    if el is None:
        return None
    for c in el:
        if _locale(c.tag) == nome:
            return c
    return None


def _testo(el: ET.Element | None, nome: str) -> str | None:
    c = _figlio(el, nome)
    if c is None or c.text is None:
        return None
    t = c.text.strip()
    return t or None


def _ritorno(risposta: ET.Element, attesa: str) -> ET.Element | None:
    if _locale(risposta.tag) != attesa:
        raise ValueError(f"attesa <{attesa}>, arrivata <{_locale(risposta.tag)}>")
    return _figlio(risposta, "return")


def _anomalie(ret: ET.Element | None) -> tuple[Messaggio, ...]:
    elenco = _figlio(ret, "elencoAnomalie")
    if elenco is None:
        return ()
    return tuple(
        Messaggio(codice=_testo(a, "codice") or "", testo=_testo(a, "descrizione"), tipo=_testo(a, "codCriticita"))
        for a in elenco
        if _locale(a.tag) == "anomalia"
    )


def _comunicazioni(ret: ET.Element | None) -> tuple[Comunicazione, ...]:
    elenco = _figlio(ret, "elencoComunicazioni")
    if elenco is None:
        return ()
    return tuple(
        Comunicazione(_testo(c, "codice") or "", _testo(c, "messaggio") or "")
        for c in elenco
        if _locale(c.tag) == "comunicazione"
    )


def _codice_esito(ok: bool, messaggi: tuple[Messaggio, ...]) -> str:
    """Il SIST non ha un codice esito come il SAC: lo ricaviamo, con lo stesso significato."""
    if not ok or any(m.bloccante for m in messaggi):
        return "9999"
    return "0001" if messaggi else "0000"


def leggi_chk(risposta: ET.Element) -> EsitoInvioSAR:
    """chkPrescrizioneResponse: IUP (cioè NRE) e codice di autenticazione, oppure le anomalie.

    Solo IUP senza codAutenticazione = SAC non disponibile: ricetta rossa (javadoc chkPrescrizione).
    """
    ret = _ritorno(risposta, "chkPrescrizioneResponse")
    messaggi = _anomalie(ret)
    nre = _testo(ret, "IUP")
    return EsitoInvioSAR(
        codice=_codice_esito(bool(nre), messaggi),
        messaggi=messaggi,
        comunicazioni=_comunicazioni(ret),
        nre=nre,
        codice_autenticazione=_testo(ret, "codAutenticazione"),
    )


def leggi_registra(risposta: ET.Element) -> bool:
    ret = _ritorno(risposta, "setRegistraPrescrizioneResponse")
    return (_testo(ret, "esito") or "").upper() == "TRUE"


def leggi_annulla(risposta: ET.Element, nre: str | None = None) -> EsitoAnnullamento:
    ret = _ritorno(risposta, "setAnnullaPrescrizioneResponse")
    messaggi = _anomalie(ret)
    ok = (_testo(ret, "esito") or "").upper() == "TRUE"
    return EsitoAnnullamento(codice=_codice_esito(ok, messaggi), messaggi=messaggi, nre=nre)


def _righe_atomiche(prescrizione: ET.Element | None) -> tuple[dict[str, str], ...]:
    righe: list[dict[str, str]] = []
    for blocco, contenitore, voce in (("datiFarm", "farmaci", "farmaco"), ("datiSpec", "prestazioni", "prestazionePrescritta")):
        cont = _figlio(_figlio(prescrizione, blocco), contenitore)
        if cont is None:
            continue
        for v in cont:
            if _locale(v.tag) == voce:
                righe.append({_locale(c.tag): c.text.strip() for c in v if c.text and c.text.strip()})
    return tuple(righe)


def leggi_identificata(risposta: ET.Element, nre: str | None = None) -> EsitoVisualizzazioneSAR:
    """getPrescrizioneIdentificataResponse: CDA in chiaro (prescrizioni pugliesi) o dati atomici."""
    from .cda_sist import righe_dal_cda

    ret = _ritorno(risposta, "getPrescrizioneIdentificataResponse")
    messaggi = _anomalie(ret)
    cda = _testo(ret, "cdaInstance")
    prescr = _figlio(ret, "prescrizione")
    testata = {
        _locale(c.tag): c.text.strip()
        for c in (prescr if prescr is not None else ())
        if len(c) == 0 and c.text and c.text.strip()
    }
    righe = _righe_atomiche(prescr)
    if not righe and cda:
        righe = righe_dal_cda(cda)
    osc = _testo(ret, "oscurato")
    return EsitoVisualizzazioneSAR(
        codice=_codice_esito(True, messaggi),
        messaggi=messaggi,
        nre=testata.get("IUP") or testata.get("codRicetta") or nre,
        stato_processo=_testo(ret, "statoRicetta"),
        codice_autenticazione=testata.get("codAutenticazioneMedico"),
        testata=testata,
        righe=righe,
        cda=cda,
        oscurato=None if osc is None else booleano_xs(osc),
        stato_sar=testata.get("statoPrescrizione"),
    )


def booleano_xs(valore: str) -> bool:
    """xs:boolean (XML Schema Part 2, par. 3.2.2): "true", "false", "1", "0". CVPService.xsd
    tipizza così `oscurato`. Un altro valore non si indovina: ValueError."""
    v = valore.strip()
    if v in ("true", "1"):
        return True
    if v in ("false", "0"):
        return False
    raise ValueError(f"valore xs:boolean non valido: {valore!r}")


def leggi_ricerca(risposta: ET.Element) -> EsitoInterrogazioneNre:
    ret = _ritorno(risposta, "getPrescrizioniIdentificateResponse")
    elenco = _figlio(ret, "elenco")
    ricette = tuple(
        NreUtilizzato(
            nre=_testo(p, "IUP") or _testo(p, "codRicetta"),
            cf_assistito=_testo(p, "codAssistito"),
            data_compilazione=_testo(p, "dataEmissione"),
        )
        for p in (elenco if elenco is not None else ())
        if _locale(p.tag) == "prescrizione"
    )
    return EsitoInterrogazioneNre(codice="0000", ricette=ricette)


_CODICE_FAULT = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def codice_fault_sist(errore: ErroreSOAP) -> str | None:
    """Il codice a 6 cifre di un SoapFaultException del SIST (es. 000004 "Prescrizione non
    identificata"), cercato nel faultstring e poi nel dettaglio del Fault."""
    testi = [errore.faultstring or ""]
    try:
        radice = ET.fromstring(errore.corpo or b"")
        fault = next((e for e in radice.iter() if _locale(e.tag) == "Fault"), None)
        if fault is not None:  # solo dentro il Fault: l'header WS-Security è pieno di cifre (base64)
            testi.append(" ".join(fault.itertext()))
    except ET.ParseError:
        pass
    for testo in testi:
        m = _CODICE_FAULT.search(testo)
        if m:
            return m.group(1)
    return None
