# Risposte SIST: SINTETICHE

Le risposte nella cartella superiore (`conformita/risposte/*.xml`) sono state registrate davvero
dall'ambiente di test del MEF. **Queste no.**

Le Specifiche di integrazione SIST v4.03.27 (Regione Puglia, InnovaPuglia) danno esempi di
richiesta ma nessuna risposta del componente CVP. Queste buste le abbiamo scritte noi con
`strumenti/genera_risposte_sist.py`, seguendo lo schema ufficiale `wsdl-pddasl/CVPService.xsd`,
la javadoc dei servizi e l'header di sicurezza dell'esempio di risposta al par. 5.1.4.
`tests/unit/test_sist.py` controlla che il Body di ognuna validi contro `CVPService.xsd`
(quando le specifiche sono scaricate).

Cosa NON sappiamo e queste risposte quindi non dimostrano:

- il testo esatto dei messaggi e dei faultstring del SIST;
- il formato reale di `codAutenticazione` (qui 30 caratteri numerici, come nel SAC);
- come il SIST mette il CDA in `cdaInstance` (qui come testo XML con escape);
- se `elencoComunicazioni` porta davvero i codici 0198/0199 come il SAC.

Identità usate: solo quelle pubbliche di test del kit MEF. La firma nell'header è finta e non
verificabile (il client non verifica la firma delle risposte: lo dice `docs/SAR_PUGLIA.md`).

Quando ci sarà un collaudo vero, le risposte registrate andranno in una cartella a parte,
accanto a queste.
