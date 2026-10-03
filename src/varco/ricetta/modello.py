# SPDX-License-Identifier: EUPL-1.2
"""Modello dati della ricetta dematerializzata, indipendente dal canale.

I nomi sono in italiano e descrittivi; la corrispondenza con i tag del tracciato
SAC sta tutta in `xml_sac.py`. Il modello NON contiene logica clinica: nessun
controllo di interazioni, dosaggi o appropriatezza. I controlli locali sono solo
strutturali (campi obbligatori, valori ammessi dal tracciato), per evitare
chiamate destinate a sicuro rifiuto.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum

from ..errori import RicettaNonValida


class TipoPrescrizione(str, Enum):
    FARMACEUTICA = "F"
    SPECIALISTICA = "P"


class TipoVisita(str, Enum):
    AMBULATORIALE = "A"
    DOMICILIARE = "D"


class ClassePriorita(str, Enum):
    URGENTE = "U"  # entro 72 ore
    BREVE = "B"  # entro 10 giorni
    DIFFERIBILE = "D"  # entro 30/60 giorni
    PROGRAMMATA = "P"  # senza priorità


TIPI_RICETTA_AMMESSI = {"EE", "UE", "NA", "ND", "NE", "NX", "ST"}


def ora_italiana() -> _dt.datetime:
    """Data/ora di Roma senza fuso (il tracciato vuole "aaaa-mm-gg HH:MM:SS" locale).

    Se il database dei fusi non c'è (es. Windows senza il pacchetto tzdata) si usa l'ora locale.
    """
    try:
        from zoneinfo import ZoneInfo

        adesso = _dt.datetime.now(ZoneInfo("Europe/Rome")).replace(tzinfo=None)
    except Exception:
        adesso = _dt.datetime.now()
    return adesso.replace(microsecond=0)


@dataclass(frozen=True)
class Prescrittore:
    """Il medico titolare della posizione (cfMedico1 e dati di censimento regionale)."""

    codice_fiscale: str
    codice_regione: str  # 3 cifre, es. "130" Abruzzo
    codice_asl: str
    codice_specializzazione: str  # 1 carattere, es. "F" medicina generale
    codice_struttura: str | None = None  # solo se la regione lo ha censito
    codice_fiscale_sostituto: str | None = None  # cfMedico2, solo in sostituzione


@dataclass(frozen=True)
class Assistito:
    codice_fiscale: str | None = None  # in chiaro: viene cifrato solo al momento dell'invio
    cognome_nome: str | None = None
    indirizzo: str | None = None
    provincia: str | None = None
    asl: str | None = None
    # Regione dell'ASL di residenza (3 cifre, es. "130" Abruzzo). Il SAC non la chiede; serve ai SAR
    # che vogliono il codice NAZIONALE dell'ASL (regione + ASL).
    codice_regione: str | None = None
    oscura_dati: bool = False
    tipo_ricetta: str | None = None  # EE, UE, NA, ND, NE, NX, ST (assistiti particolari)
    # assicurati da istituzioni estere
    stato_estero: str | None = None
    istituzione_competente: str | None = None
    num_ident_personale: str | None = None
    num_ident_tessera: str | None = None
    data_nascita_estero: str | None = None
    data_scadenza_tessera: str | None = None
    # SASN
    num_tessera_sasn: str | None = None
    societa_navigazione: str | None = None


@dataclass(frozen=True)
class Riga:
    """Una prescrizione (farmaco o prestazione) dentro la ricetta."""

    quantita: int
    codice: str | None = None  # AIC o codice prestazione (codProdPrest)
    descrizione: str | None = None  # descrProdPrest
    codice_gruppo_equivalenza: str | None = None  # principio attivo (AIFA)
    descrizione_gruppo_equivalenza: str | None = None
    non_sostituibile: bool = False
    codice_motivazione_non_sost: str | None = None
    note: str | None = None  # motivazNote (farmaceutica)
    note_prestazione: str | None = None  # descrTestoLiberoNote (specialistica)
    nota_aifa: str | None = None  # notaProd
    codice_catalogo: str | None = None  # codCatalogoPrescr (specialistica)
    tipo_accesso: str | None = None  # "1" primo accesso, "0" altro
    numero_nota: str | None = None
    condizione_erogabilita: str | None = None
    appropriatezza: str | None = None
    patologia: str | None = None
    num_sedute: int | None = None
    prescrizione1: str | None = None
    prescrizione2: str | None = None


@dataclass(frozen=True)
class Ricetta:
    prescrittore: Prescrittore
    assistito: Assistito
    tipo: TipoPrescrizione
    righe: tuple[Riga, ...]
    tipo_visita: TipoVisita = TipoVisita.AMBULATORIALE
    data_compilazione: _dt.datetime = field(default_factory=ora_italiana)
    non_esente: bool = True
    codice_esenzione: str | None = None
    esente_reddito: bool = False
    codice_diagnosi: str | None = None
    descrizione_diagnosi: str | None = None
    classe_priorita: ClassePriorita | None = None
    indicazione: str | None = None  # "S" suggerita, "H" ricovero
    altro: str | None = None
    ricetta_interna: bool = False
    disposizioni_regionali: str | None = None
    testata1: str | None = None
    testata2: str | None = None
    nre: str | None = None  # se assente, lo assegna il SAC

    def __post_init__(self):
        if not isinstance(self.righe, tuple):
            object.__setattr__(self, "righe", tuple(self.righe))

    def problemi(self) -> list[str]:
        """Controlli strutturali dal tracciato (par. 4.2.1). Lista vuota = ok."""
        p: list[str] = []
        pr = self.prescrittore
        if not pr.codice_fiscale:
            p.append("codice fiscale del prescrittore mancante")
        if not (len(pr.codice_regione) == 3 and pr.codice_regione.isdigit()):
            p.append("codice regione: servono 3 cifre")
        if not pr.codice_asl:
            p.append("codice ASL del prescrittore mancante")
        if len(pr.codice_specializzazione or "") != 1:
            p.append("codice specializzazione: serve 1 carattere")
        if not self.righe:
            p.append("almeno una riga di prescrizione")
        a = self.assistito
        if a.tipo_ricetta and a.tipo_ricetta not in TIPI_RICETTA_AMMESSI:
            p.append(f"tipo ricetta non ammesso: {a.tipo_ricetta}")
        if not a.codice_fiscale and not a.tipo_ricetta:
            p.append("serve il codice assistito oppure un tipo ricetta per assistiti particolari")
        if bool(a.provincia) != bool(a.asl):
            p.append("provincia e ASL dell'assistito vanno compilate insieme")
        if a.codice_regione is not None and not (len(a.codice_regione) == 3 and a.codice_regione.isdigit()):
            p.append("codice regione dell'assistito: servono 3 cifre")
        if self.nre is not None and len(self.nre) != 15:
            p.append("NRE: 15 caratteri")
        if self.indicazione not in (None, "S", "H"):
            p.append("indicazione ammessa: S o H")
        if self.altro not in (None, "A"):
            p.append("campo altro ammesso: A")
        if self.tipo is TipoPrescrizione.SPECIALISTICA:
            if not (self.codice_diagnosi or self.descrizione_diagnosi):
                p.append("specialistica: serve codice o descrizione della diagnosi")
        for i, r in enumerate(self.righe, start=1):
            if not (0 <= r.quantita <= 9):
                p.append(f"riga {i}: quantità tra 0 e 9 (una cifra, da tracciato)")
            if r.num_sedute is not None and not (0 <= r.num_sedute <= 9):
                p.append(f"riga {i}: numero sedute di una cifra")
            if self.tipo is TipoPrescrizione.SPECIALISTICA:
                if not r.codice:
                    p.append(f"riga {i}: specialistica senza codice prestazione")
                if not r.descrizione:
                    p.append(f"riga {i}: specialistica senza descrizione")
                if r.non_sostituibile:
                    p.append(f"riga {i}: non sostituibilità solo per farmaci")
            else:
                ha_prodotto = bool(r.codice and r.descrizione)
                ha_principio = bool(r.codice_gruppo_equivalenza and r.descrizione_gruppo_equivalenza)
                if not (ha_prodotto or ha_principio):
                    p.append(f"riga {i}: farmaco senza (codice+descrizione) né (gruppo equivalenza+descrizione)")
                if r.non_sostituibile and not r.codice_motivazione_non_sost:
                    p.append(f"riga {i}: non sostituibile senza codice motivazione")
            if r.tipo_accesso not in (None, "0", "1"):
                p.append(f"riga {i}: tipo accesso ammesso 0 o 1")
        return p

    def valida(self) -> None:
        problemi = self.problemi()
        if problemi:
            raise RicettaNonValida(problemi)


# ---------------------------------------------------------------- esiti ----


@dataclass(frozen=True)
class Messaggio:
    """Un elemento di ElencoErroriRicette."""

    codice: str
    testo: str | None = None
    progressivo: str | None = None  # "0" = tutta la ricetta, ">0" = riga n
    tipo: str | None = None  # testo grezzo del SAC (vedi `gravita`)

    @property
    def gravita(self) -> str | None:
        """"E" bloccante, "W" avviso, None se non indicata.

        La specifica (par. 4.2.1) dice E/W, ma l'ambiente di test risponde
        "Bloccante" (verificato il 30/09/2026): si accettano entrambe le forme.
        """
        t = (self.tipo or "").strip().lower()
        if not t:
            return None
        if t in ("e", "bloccante", "errore") or t.startswith("blocc"):
            return "E"
        if t in ("w", "warning", "avviso", "non bloccante") or "non blocc" in t or t.startswith("avvis"):
            return "W"
        return "E"  # prudenza: una gravità sconosciuta si tratta come bloccante

    @property
    def bloccante(self) -> bool:
        return self.gravita == "E"


@dataclass(frozen=True)
class Comunicazione:
    codice: str
    messaggio: str


@dataclass(frozen=True)
class Esito:
    """Parte comune a tutti gli esiti del SAC."""

    codice: str  # 0000 ok, 0001 ok con avvisi, 9999 rifiutato
    messaggi: tuple[Messaggio, ...] = ()
    comunicazioni: tuple[Comunicazione, ...] = ()

    @property
    def ok(self) -> bool:
        return self.codice in ("0000", "0001")

    @property
    def errori(self) -> tuple[Messaggio, ...]:
        return tuple(m for m in self.messaggi if m.codice != "0000" and m.gravita != "W")

    @property
    def avvisi(self) -> tuple[Messaggio, ...]:
        return tuple(m for m in self.messaggi if m.gravita == "W")

    def comunicazione(self, codice: str) -> str | None:
        for c in self.comunicazioni:
            if c.codice == codice:
                return c.messaggio
        return None


@dataclass(frozen=True)
class NotaPrestazione:
    """Una voce di ElencoNota della ricevuta d'invio (DM 9/12/2015, «decreto Lorenzin»): per una
    prestazione con numero nota, la tipologia di ambulatorio dove erogarla. Non la dichiara il
    medico: la restituisce il SAC (specifica SAR FVG Idof-dem-AT-01, p. 22; TipiDati, notaType)."""

    progressivo: str | None = None  # progrPresc: la riga a cui si riferisce, nell'ordine d'invio
    codice_prestazione: str | None = None  # codProdPrest
    tipo_ambulatorio: str | None = None  # tipoAmbulatorio


@dataclass(frozen=True)
class EsitoInvio(Esito):
    nre: str | None = None
    codice_autenticazione: str | None = None
    data_inserimento: str | None = None
    pdf_promemoria: bytes | None = field(default=None, repr=False)
    note: tuple[NotaPrestazione, ...] = ()  # ElencoNota: dove erogare (revisione esterna 02/10/2026)

    @property
    def cognome_medico(self) -> str | None:
        v = self.comunicazione("0199")
        return v.split("=", 1)[1] if v and "=" in v else v

    @property
    def nome_medico(self) -> str | None:
        v = self.comunicazione("0198")
        return v.split("=", 1)[1] if v and "=" in v else v


@dataclass(frozen=True)
class EsitoVisualizzazione(Esito):
    nre: str | None = None
    stato_processo: str | None = None
    codice_autenticazione: str | None = None
    data_inserimento: str | None = None
    testata: dict[str, str] = field(default_factory=dict)  # tag SAC -> valore
    righe: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class EsitoAnnullamento(Esito):
    nre: str | None = None


@dataclass(frozen=True)
class CriteriNreUtilizzati:
    """Criteri di InterrogaNreUtilRichiesta (par. 4.2.4 della specifica).

    Due modi alternativi: un NRE puntuale, oppure un intervallo di date di
    compilazione (obbligatorio) più, a scelta, lotto, CF assistito e tipo.
    """

    codice_regione: str
    nre: str | None = None
    codice_lotto: str | None = None  # NRE senza il progressivo (12-13 caratteri)
    cf_assistito: str | None = None  # in chiaro: lo schema lo tipizza come CF (16 caratteri)
    tipo: TipoPrescrizione | None = None
    dal: _dt.datetime | None = None
    al: _dt.datetime | None = None

    def problemi(self, *, tipo_obbligatorio: bool = True) -> list[str]:
        """`tipo_obbligatorio`: il SAC di test esige il tipo (errore 1153); il SAR FVG no, il suo XSD
        lo lascia facoltativo (revisione esterna giro 2, 4-sar-fvg N4) e passa False."""
        p: list[str] = []
        if not (len(self.codice_regione) == 3 and self.codice_regione.isdigit()):
            p.append("codice regione: servono 3 cifre")
        if self.nre is not None and len(self.nre) != 15:
            p.append("NRE: 15 caratteri")
        if self.nre is None and (self.dal is None or self.al is None):
            p.append("senza NRE puntuale servono entrambe le date (dal, al)")
        if tipo_obbligatorio and self.nre is None and self.tipo is None:
            # La specifica (par. 4.2.4) dà tipoPrescr come facoltativo, ma il SAC di test risponde
            # 9999/1153 "Il tipo prescrizione è un campo obbligatorio" (verificato il 30/09/2026).
            p.append("senza NRE puntuale serve anche il tipo di prescrizione (il SAC lo esige: errore 1153)")
        if self.dal and self.al and self.dal > self.al:
            p.append("intervallo di date rovesciato (dal > al)")
        if self.codice_lotto is not None and not (12 <= len(self.codice_lotto) <= 13):
            p.append("codice lotto: da 12 a 13 caratteri")
        if self.cf_assistito is not None and len(self.cf_assistito) != 16:
            p.append("CF assistito: 16 caratteri")
        return p


@dataclass(frozen=True)
class NreUtilizzato:
    """Un elemento di ElencoNreUtilRecord."""

    nre: str | None = None
    cf_medico: str | None = None
    tipo: str | None = None
    data_compilazione: str | None = None
    cf_assistito: str | None = None
    provenienza: str | None = None  # "0" web service, "1" applicazione web (specifica)
    lotto: str | None = None
    codice_autenticazione: str | None = None


@dataclass(frozen=True)
class EsitoInterrogazioneNre(Esito):
    ricette: tuple[NreUtilizzato, ...] = ()


STATI_PROCESSO = {
    "1": "lotto richiesto senza associazione medico",
    "2": "lotto richiesto con associazione medico",
    "3": "ricetta da erogare",
    "4": "ricetta annullata dal prescrittore",
    "5": "ricetta in corso di erogazione",
    "6": "ricetta sospesa",
    "7": "singola prescrizione erogata",
    "8": "ricetta erogata",
    "9": "annullamento ricetta erogata",
    "10": "ricetta scaduta (non erogata)",
}
