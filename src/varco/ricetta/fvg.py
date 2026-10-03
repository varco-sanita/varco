# SPDX-License-Identifier: EUPL-1.2
"""`ServizioRicetta` verso il SAR della Regione Friuli-Venezia Giulia (Insiel): stesso contratto, altro canale.

Corrispondenza con il contratto (Idof-dem-AT-01 dell'11/02/2026, cap. 4):

  invia      -> InvioPrescritto (stesso tracciato del SAC; il SAR lo passa al SAC in modo sincrono)
  visualizza -> VisualizzaPrescritto (NRE e CF del medico, come nel SAC; `cf_assistito` non serve)
  annulla    -> AnnullaPrescritto
  interroga_nre_utilizzati -> InterrogaNreUtil: lo schema c'è, l'endpoint NO (il WSDL punta a
                localhost e il cap. 5 non lo elenca). Il kit lo chiama solo se gli si dà un URL.

In più, un servizio solo regionale: `verifica_sostituto` (GestoreAutorizzazioni,
VerificaPosizioneMedicoSostituto).

Cosa resta al programma del medico (scritto nella specifica, non nel tracciato):
  - il **downgrade in ricetta rossa** (MIR) per la specialistica con i codici 060120-060130
    (par. 2.3.6): `richiede_downgrade_mir(esito)` lo riconosce, il canale MIR non è implementato;
  - il **downgrade automatico** per indisponibilità o oltre la soglia di tempo (par. 2.3.3), con
    l'eventuale annullamento della ricetta accolta in ritardo;
  - la stampa del promemoria e i lotti di NRE (servizio MIR, schema non pubblicato).

Scritto e verificato sulle specifiche: NON collaudato sul sistema regionale.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..cifratura import CifratoreSanitel
from ..errori import ErroreSOAP, RicettaNonValida
from ..trasporto.fvg import CanaleFVG, ServizioFVG
from ..trasporto.sac import RispostaGrezza
from . import xml_fvg
from .modello import (
    Comunicazione,
    CriteriNreUtilizzati,
    Esito,
    EsitoAnnullamento,
    EsitoInterrogazioneNre,
    EsitoInvio,
    EsitoVisualizzazione,
    Messaggio,
    Ricetta,
)


@dataclass
class UltimoScambio:
    servizio: str
    grezza: RispostaGrezza


@dataclass(frozen=True)
class VerificaSostituto:
    """Esito di VerificaPosizioneMedicoSostituto: il sostituto è abilitato sulla posizione del titolare in quell'ASL?"""

    cf_titolare: str | None
    cf_sostituto: str | None
    codice_asl: str | None
    abilitato: bool
    messaggio: str | None = None
    messaggi: tuple[Messaggio, ...] = ()
    comunicazioni: tuple[Comunicazione, ...] = field(default=())


def esito_da_fault_invio(e: ErroreSOAP) -> EsitoInvio | None:
    """Il par. 2.3.6 non dice se i codici di downgrade arrivano come errore della ricevuta o come
    SOAP Fault: se arrivano come Fault, diventano un esito non riuscito riconoscibile. Gli altri
    Fault restano eccezioni (None)."""
    codice = xml_fvg.codice_downgrade_in_testo(e.faultstring) or xml_fvg.codice_downgrade_in_testo(e.faultcode)
    if codice is None:
        return None
    return EsitoInvio(codice="9999", messaggi=(xml_fvg.messaggio_downgrade(codice, e.faultstring),))


def richiede_downgrade_mir(esito: Esito) -> bool:
    """True se il SAR ha risposto con un codice 060120-060130: la ricetta di specialistica va emessa
    come ricetta rossa (MIR) senza avvisi al medico (par. 2.3.6)."""
    return not esito.ok and any(xml_fvg.e_codice_downgrade(m.codice) for m in esito.messaggi)


class RicettaFVG:
    """Implementazione di `ServizioRicetta` sul SAR FVG."""

    def __init__(self, canale: CanaleFVG, cifratore: CifratoreSanitel, *, valida_localmente: bool = True):
        """
        `cifratore`: cifra il CF dell'assistito (`codiceAss`). È OBBLIGATORIO e non ha un valore
        predefinito perché la specifica dice due cose diverse: la tabella del par. 4.2 parla del
        certificato SanitelCF, il par. 4.6 di «un certificato fornito dalla regione Friuli Venezia
        Giulia», consegnato ai fornitori con il progetto Medici in Rete e non pubblicato. Va
        chiesto a Insiel quale usare (docs/SAR_FVG.md, sez. 7). Stesso algoritmo in entrambi i casi:
        RSA PKCS#1 v1.5 e base64, cioè `CifratoreSanitel(certificato)`.
        """
        if cifratore is None:
            raise ValueError("serve il cifratore del CF dell'assistito: certificato da chiedere a Insiel")
        self.canale = canale
        self.cifratore = cifratore
        self.valida_localmente = valida_localmente
        self.ultimo: UltimoScambio | None = None

    # ------------------------------------------------------------------ supporto

    @property
    def _prodotto(self) -> str:
        return self.canale.applicativo.prodotto_cme

    def _chiama(self, servizio: ServizioFVG, corpo):
        el, grezza = self.canale.chiama(servizio, corpo)
        self.ultimo = UltimoScambio(servizio.value, grezza)
        return el

    def _cf(self, cf_medico: str | None) -> str:
        return cf_medico or self.canale.cf_medico

    @staticmethod
    def _nre(nre: str | None) -> str:
        if not nre or len(nre) != 15:
            raise ValueError(f"NRE non valido (servono 15 caratteri): {nre!r}")
        return nre

    def _verifica_medico(self, ricetta: Ricetta) -> None:
        """Invia chi ha la carta: il sostituto se c'è (cfMedico2), altrimenti il titolare (regole SAC
        sui sostituti, richiamate dal par. 4.2; par. 2.2: CF della carta = CF del medico inviante)."""
        atteso = ricetta.prescrittore.codice_fiscale_sostituto or ricetta.prescrittore.codice_fiscale
        if atteso.upper() != self.canale.cf_medico:
            raise RicettaNonValida(
                [f"il medico autenticato ({self.canale.cf_medico}) non è quello che prescrive ({atteso}): "
                 "con il sostituto invia il sostituto, con la sua carta"]
            )

    # ------------------------------------------------------------------ contratto

    def invia(self, ricetta: Ricetta) -> EsitoInvio:
        # versioneCR (par. 3.1, p. 13): «obbligatoriamente» per la specialistica, patch compresa. Sempre,
        # anche senza i controlli locali: è configurazione del software, non dato della ricetta. Fino al
        # 02/10/2026 mancava e il server finto rispondeva 0000 (revisione esterna, punto 2).
        problemi = xml_fvg.problemi_versione_cr(ricetta, self.canale.applicativo.versione_cr)
        if self.valida_localmente:
            problemi = ricetta.problemi() + xml_fvg.problemi_fvg(ricetta) + problemi
        if problemi:
            raise RicettaNonValida(problemi)
        if self.valida_localmente:
            self._verifica_medico(ricetta)
        corpo = xml_fvg.richiesta_invio(ricetta, self.cifratore.cifra, self._prodotto, self.canale.applicativo.versione_cr)
        try:
            return xml_fvg.leggi_ricevuta_invio(self._chiama(ServizioFVG.INVIO, corpo))
        except ErroreSOAP as e:
            esito = esito_da_fault_invio(e)
            if esito is None:
                raise
            return esito

    def visualizza(
        self, nre: str, cf_medico: str | None = None, *, cf_assistito: str | None = None
    ) -> EsitoVisualizzazione:
        # cf_assistito: ignorato, il SAR FVG identifica la ricetta con NRE e CF del medico (par. 4.4)
        corpo = xml_fvg.richiesta_visualizza(self._nre(nre), self._cf(cf_medico), self._prodotto)
        return xml_fvg.leggi_ricevuta_visualizza(self._chiama(ServizioFVG.VISUALIZZA, corpo))

    def annulla(self, nre: str, cf_medico: str | None = None) -> EsitoAnnullamento:
        corpo = xml_fvg.richiesta_annulla(self._nre(nre), self._cf(cf_medico), self._prodotto)
        return xml_fvg.leggi_ricevuta_annulla(self._chiama(ServizioFVG.ANNULLA, corpo))

    def interroga_nre_utilizzati(
        self, criteri: CriteriNreUtilizzati, cf_medico: str | None = None
    ) -> EsitoInterrogazioneNre:
        """Lista degli NRE utilizzati (par. 4.5). L'endpoint non è pubblicato: senza un URL esplicito
        nel canale (`url={ServizioFVG.INTERROGA_NRE: ...}`) il kit si ferma prima di chiamare."""
        self.canale.url_di(ServizioFVG.INTERROGA_NRE)  # ConfigurazioneNonValida se manca, prima di tutto
        if self.valida_localmente:
            # tipoPrescr è facoltativo in InterrogaNreUtilRichiesta.xsd: il vincolo del SAC di test
            # (1153) non vale qui (revisione esterna giro 2, 4-sar-fvg N4)
            problemi = criteri.problemi(tipo_obbligatorio=False)
            if problemi:
                raise RicettaNonValida(problemi)
        try:
            corpo = xml_fvg.richiesta_interroga_nre(criteri, self._cf(cf_medico))
        except ValueError as e:
            raise RicettaNonValida([str(e)]) from e
        return xml_fvg.leggi_ricevuta_interroga_nre(self._chiama(ServizioFVG.INTERROGA_NRE, corpo))

    # ------------------------------------------------------------------ solo FVG

    def verifica_sostituto(self, cf_titolare: str, cf_sostituto: str, codice_asl: str) -> VerificaSostituto:
        """VerificaPosizioneMedicoSostituto (GestoreAutorizzazioni): la posizione del sostituto
        rispetto al titolare in un'ASL. Il documento non descrive il servizio: c'è solo il WSDL."""
        corpo = xml_fvg.richiesta_verifica_sostituto(cf_titolare, cf_sostituto, codice_asl, self._prodotto)
        campi = xml_fvg.leggi_verifica_sostituto(self._chiama(ServizioFVG.GESTORE_AUTORIZZAZIONI, corpo))
        return VerificaSostituto(**campi)
