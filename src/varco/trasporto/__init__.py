# SPDX-License-Identifier: EUPL-1.2
"""Livello di trasporto, separato dal modello dati.

- http:     HTTPS generico (guardia produzione, limite frequenza, registrazione)
- soap:     busta SOAP 1.1
- sac:      canale verso il SAC (Basic auth, Authorization2F, SOAPAction)
- sist:     canale verso il SIST della Regione Puglia (WS-Security X.509, datiOperatore/datiApplicativo)
- fvg:      canale verso il SAR della Regione FVG (mTLS con CRS/CNS o token federati, User-Agent, prodottoCme)
- piemonte: canale verso SIRPED, SAR della Regione Piemonte (RUPAR + Id-Sessione via mail, oppure JWT OAuth2)
- umbria:   canale verso il SAR della Regione Umbria (REST, mTLS + due JWT come il gateway FSE 2.0)
- piemonte_a2f, piemonte_oauth2: Id-Sessione di SIRPED (CreateAuth/CheckToken/RevokeAuth; PKCE, token, JWKS)
- wssecurity: firma X.509 del Timestamp nell'header SOAP
- registro: registrazione su file di richieste/risposte
"""

from .http import Richiesta, Risposta, Trasporto, TrasportoHTTP
from .registro import RegistratoreFile
from .sac import CanaleSAC, RispostaGrezza, sessione_2f_test
from .sist import AdesioneSIST, AmbienteSIST, ApplicativoSIST, CanaleSIST, OperatoreSIST
from .wssecurity import ChiaveOperatore, ChiavePKCS12
from .fvg import AdesioneFVG, ApplicativoFVG, CanaleFVG, ModalitaFVG, PostazioneFVG, ServizioFVG, TokenFVG
from .piemonte import AdesionePiemonte, CanalePiemonte, GestionalePiemonte, ModalitaPiemonte, ServizioPiemonte
from .piemonte_a2f import ServizioIdSessione, UtenteA2F
from .piemonte_oauth2 import ClientOAuth2Piemonte
from .umbria import (AdesioneUmbria, ApplicativoUmbria, CanaleUmbria, FirmatarioJWT, FirmatarioJWTPKCS12,
                     InvioIncertoUmbria, ServizioUmbria)

__all__ = [
    "Richiesta",
    "Risposta",
    "Trasporto",
    "TrasportoHTTP",
    "RegistratoreFile",
    "CanaleSAC",
    "RispostaGrezza",
    "sessione_2f_test",
    "AdesioneSIST",
    "AmbienteSIST",
    "ApplicativoSIST",
    "CanaleSIST",
    "OperatoreSIST",
    "ChiaveOperatore",
    "ChiavePKCS12",
    "AdesioneFVG",
    "ApplicativoFVG",
    "CanaleFVG",
    "ModalitaFVG",
    "PostazioneFVG",
    "ServizioFVG",
    "TokenFVG",
    "AdesionePiemonte",
    "CanalePiemonte",
    "GestionalePiemonte",
    "ModalitaPiemonte",
    "ServizioPiemonte",
    "ServizioIdSessione",
    "UtenteA2F",
    "ClientOAuth2Piemonte",
    "AdesioneUmbria",
    "ApplicativoUmbria",
    "CanaleUmbria",
    "FirmatarioJWT",
    "FirmatarioJWTPKCS12",
    "InvioIncertoUmbria",
    "ServizioUmbria",
]
