"""Genera il resoconto di stato di Varco in PDF (uso interno, non parte della libreria)."""
import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

OUT = Path(sys.argv[1])
SIMILI = json.loads(Path(sys.argv[2]).read_text()) if len(sys.argv) > 2 else None

INK = colors.HexColor("#1d2733")
MUTED = colors.HexColor("#5b6773")
ACCENT = colors.HexColor("#1f5f8b")
LINE = colors.HexColor("#d5dbe1")
VERDE = colors.HexColor("#dff0e3")
GIALLO = colors.HexColor("#fdf3d6")
GRIGIO = colors.HexColor("#eef0f2")

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping
_F = "/System/Library/Fonts/Supplemental/"
pdfmetrics.registerFont(TTFont("Arial", _F + "Arial.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Bold", _F + "Arial Bold.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Italic", _F + "Arial Italic.ttf"))
pdfmetrics.registerFont(TTFont("Arial-BoldItalic", _F + "Arial Bold Italic.ttf"))
addMapping("Arial", 0, 0, "Arial"); addMapping("Arial", 1, 0, "Arial-Bold")
addMapping("Arial", 0, 1, "Arial-Italic"); addMapping("Arial", 1, 1, "Arial-BoldItalic")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=ss["Title"], fontName="Arial-Bold", fontSize=22, leading=26,
                    textColor=INK, alignment=TA_LEFT, spaceAfter=4)
SUB = ParagraphStyle("sub", parent=ss["Normal"], fontName="Arial", fontSize=10.5, leading=14, textColor=MUTED, spaceAfter=14)
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Arial-Bold", fontSize=14, leading=18,
                    textColor=ACCENT, spaceBefore=14, spaceAfter=6)
P = ParagraphStyle("p", parent=ss["Normal"], fontName="Arial", fontSize=9.8, leading=13.6, textColor=INK, spaceAfter=6)
LI = ParagraphStyle("li", parent=P, leftIndent=12, bulletIndent=2, spaceAfter=3)
CELL = ParagraphStyle("cell", parent=P, fontSize=8.3, leading=10.4, spaceAfter=0)
CELLB = ParagraphStyle("cellb", parent=CELL, fontName="Arial-Bold")
NOTE = ParagraphStyle("note", parent=P, fontSize=8, leading=10.5, textColor=MUTED)


def bullets(items):
    return [Paragraph(t, LI, bulletText="-") for t in items]


def kpi(numero, etichetta, sfondo):
    t = Table([[Paragraph(f'<font size="17"><b>{numero}</b></font>', CELL)],
               [Paragraph(etichetta, CELL)]], colWidths=[56 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), sfondo),
                           ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


TOT_MMG = 37983
# Regione, MMG 2023 (Agenas Tab. 18), canale, gestore, stato kit, certezza canale, gruppo
REGIONI = [
    ("Abruzzo", 950, "SAC nazionale", "-", "Canale coperto, provato su MEF test", "media", "ok"),
    ("Basilicata", 443, "SAC nazionale", "-", "Canale coperto (MEF), da provare", "media", "ok"),
    ("Calabria", 1263, "SAC nazionale", "-", "Canale coperto (MEF), da provare", "media", "ok"),
    ("Campania", 3396, "SAC (fascia C via Sinfonia)", "-", "Canale coperto (MEF), da provare", "media", "ok"),
    ("Marche", 946, "SAC nazionale", "-", "Canale coperto (MEF), da provare", "media", "ok"),
    ("Molise", 241, "SAC nazionale", "-", "Canale coperto (MEF), da provare", "media", "ok"),
    ("Sardegna", 961, "SAC (anche CNS)", "-", "Canale coperto (MEF), da provare", "media", "ok"),
    ("Sicilia", 3654, "SAC nazionale", "-", "Canale coperto (MEF), da provare", "alta", "ok"),
    ("Valle d'Aosta", 72, "SAC nazionale", "-", "Canale coperto (MEF), da provare", "media", "ok"),
    ("Puglia", 2811, "SAR SIST", "InnovaPuglia", "Modulo scritto, attende collaudo", "alta", "scritto"),
    ("Friuli-Venezia Giulia", 712, "SAR regionale", "Insiel", "Modulo scritto, attende collaudo", "alta", "scritto"),
    ("Piemonte", 2732, "SAR SIRPED", "CSI Piemonte", "Modulo scritto, attende collaudo", "alta", "scritto"),
    ("Toscana", 2814, "SAR via CART", "Regione / CART", "Specifiche solo in parte pubbliche", "alta", "no"),
    ("Lombardia", 5277, "SISS", "ARIA", "Specifiche solo in parte pubbliche", "alta", "no"),
    ("Lazio", 4023, "SAR MeSIR (da 07/2026)", "LAZIOcrea", "Specifiche non trovate", "media", "no"),
    ("Veneto", 2764, "SAR DOGE", "Azienda Zero", "Specifiche non trovate", "alta", "no"),
    ("Emilia-Romagna", 2673, "SAR SOLE", "Lepida", "Specifiche non trovate", "alta", "no"),
    ("Liguria", 994, "SAR regionale", "Liguria Digitale", "Specifiche non trovate", "alta", "no"),
    ("Umbria", 635, "SAR Umbria", "PuntoZero", "Specifiche pubbliche su GitHub: prossimo modulo", "media", "pubbliche"),
    ("P.A. Trento", 330, "SAR", "APSS", "Specifiche non trovate", "alta", "no"),
    ("P.A. Bolzano", 292, "SAP", "SABES", "Specifiche non trovate", "alta", "no"),
]
assert len(REGIONI) == 21 and sum(r[1] for r in REGIONI) == TOT_MMG


def somma(gruppo):
    return sum(r[1] for r in REGIONI if r[6] == gruppo)


def pct(n):
    return f"{n / TOT_MMG * 100:.1f}".replace(".", ",") + "%"


def num(n):
    return f"{n:,}".replace(",", ".")


ok, scritto, pubbliche, no = somma("ok"), somma("scritto"), somma("pubbliche"), somma("no")


def piede(canvas, doc):
    canvas.saveState()
    canvas.setFont("Arial", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Varco - resoconto di stato - 3 ottobre 2026 - bene comune, licenza EUPL-1.2")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"pag. {doc.page}")
    canvas.restoreState()


s = []
s.append(Paragraph("Varco - resoconto di stato", H1))
s.append(Paragraph("Un kit aperto e gratuito che fa parlare qualsiasi software dei medici di famiglia con i servizi "
                   "pubblici (ricetta, Fascicolo). Situazione al 3 ottobre 2026, Regione per Regione.", SUB))

s.append(Table([[kpi(pct(ok), f"dei medici di famiglia in Regioni sul canale nazionale, già provato sul MEF di test<br/>({num(ok)} MMG, 9 Regioni)", VERDE),
                 kpi(pct(ok + scritto), f"con Puglia, Friuli e Piemonte dopo il collaudo regionale<br/>({num(ok + scritto)} MMG, 12 Regioni)", GIALLO),
                 kpi("PSS", "Fascicolo: documento valido per lo standard nazionale; invio al gateway da attivare", GRIGIO)]],
               colWidths=[60 * mm] * 3, style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
s.append(Spacer(1, 10))

s.append(Paragraph("In breve", H2))
s += bullets([
    "<b>Ricetta via MEF (SAC):</b> invio, visualizzazione, annullamento, medico sostituto e interrogazione delle ricette "
    "funzionano contro l'ambiente di test ufficiale del MEF, con risposte vere salvate come prova (primo NRE: 1300A4019294833).",
    "<b>Fascicolo Sanitario Elettronico 2.0:</b> il Profilo Sanitario Sintetico generato dal kit passa il validatore "
    "ufficiale del Ministero, fatto girare in locale. Il collegamento al gateway Sogei richiede i loro certificati di test.",
    "<b>Tre sistemi regionali scritti:</b> Puglia (SIST), Friuli-Venezia Giulia (Insiel), Piemonte (SIRPED). Verificati "
    "su schemi ufficiali e simulatori locali; manca il collaudo, che solo la Regione può aprire.",
    "<b>Un solo modello dati per tutti:</b> tre sistemi regionali diversi sono entrati con un solo campo facoltativo e un parametro facoltativo in più (Puglia); Friuli e Piemonte senza modifiche. "
    "L'idea \"un kit, tanti dialetti\" regge.",
    "<b>Qualità:</b> 1.916 test automatici verdi (1.904 senza Java), tre giri di revisione esterna con le correzioni, "
    "suite di conformità pubblica (143 casi) "
    "usabile da chiunque in qualsiasi linguaggio.",
    "<b>Regole rispettate:</b> mai dati reali, mai ambienti di produzione, nessun materiale riservato di terzi, "
    "nessuna funzione clinica (non è un dispositivo medico). Nulla è ancora pubblicato.",
])

s.append(Paragraph("Servizi nazionali", H2))
naz = [[Paragraph(h, CELLB) for h in ("Servizio", "Chi decide l'accesso", "Stato del kit")],
       [Paragraph("Ricetta via SAC (MEF)", CELL), Paragraph("Nessun collaudo del software: bastano le credenziali del medico e la 2FA", CELL),
        Paragraph("Funziona sul test MEF; produzione da attivare", CELL)],
       [Paragraph("FSE 2.0 (gateway Sogei)", CELL), Paragraph("Accreditamento con criteri pubblici ed elenco pubblico (circa 300 fornitori, anche ditte individuali)", CELL),
        Paragraph("Documento valido; servono i certificati di test Sogei per il gateway", CELL)],
       [Paragraph("Certificati di malattia INPS", CELL), Paragraph("Kit di sviluppo su richiesta a Sogei", CELL),
        Paragraph("Non iniziato: serve il kit", CELL)]]
t = Table(naz, colWidths=[40 * mm, 82 * mm, 54 * mm])
t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, LINE), ("BACKGROUND", (0, 0), (-1, 0), GRIGIO),
                       ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
s.append(t)

s.append(PageBreak())
s.append(Paragraph("Stato per Regione - ricetta dematerializzata", H2))
s.append(Paragraph("Il canale è quello da cui il medico di famiglia di quella Regione deve passare per inviare la ricetta. "
                   "MMG = medici di medicina generale, dati 2023 (Agenas).", P))
righe = [[Paragraph(h, CELLB) for h in ("Regione", "MMG", "Canale", "Gestore", "Stato del kit", "Certezza canale")]]
stili = [("GRID", (0, 0), (-1, -1), 0.4, LINE), ("BACKGROUND", (0, 0), (-1, 0), GRIGIO),
         ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3),
         ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
colore = {"ok": VERDE, "scritto": GIALLO, "pubbliche": GRIGIO, "no": colors.white}
for i, (reg, mmg, can, gest, stato, cert, g) in enumerate(REGIONI, start=1):
    righe.append([Paragraph(f"<b>{reg}</b>", CELL), Paragraph(num(mmg), CELL), Paragraph(can, CELL),
                  Paragraph(gest, CELL), Paragraph(stato, CELL), Paragraph(cert, CELL)])
    stili.append(("BACKGROUND", (0, i), (-1, i), colore[g]))
righe.append([Paragraph("<b>Totale Italia</b>", CELL), Paragraph(f"<b>{num(TOT_MMG)}</b>", CELL), "", "", "", ""])
t = Table(righe, colWidths=[34 * mm, 15 * mm, 36 * mm, 28 * mm, 45 * mm, 18 * mm], repeatRows=1)
t.setStyle(TableStyle(stili))
s.append(t)
s.append(Spacer(1, 6))
s.append(Paragraph(
    f"Verde: canale nazionale coperto dal kit, provato sul MEF di test, produzione da attivare ({num(ok)} MMG, {pct(ok)}). Giallo: modulo scritto, attende il collaudo della Regione "
    f"({num(scritto)} MMG, {pct(scritto)}). Grigio: specifiche pubbliche, modulo da scrivere ({num(pubbliche)} MMG, {pct(pubbliche)}). "
    f"Bianco: specifiche non pubbliche o solo in parte ({num(no)} MMG, {pct(no)}).", NOTE))
s.append(Paragraph(
    "Certezza \"media\": per le Regioni sul SAC le prove sono spesso FAQ pubbliche di gestionali e qualche atto regionale "
    "datato; documenti regionali recenti ci sono solo per Sicilia, Sardegna e Marche. Va confermato Regione per Regione.", NOTE))

s.append(PageBreak())
s.append(Paragraph("Cosa abbiamo imparato sui \"cancelli\"", H2))
s += bullets([
    "<b>Il cancello nazionale è aperto.</b> Ricetta e certificati passano con le credenziali del medico; il Fascicolo ha un "
    "esame con criteri pubblici. Il problema non è lo Stato centrale.",
    "<b>I cancelli opachi sono regionali.</b> 12 Regioni su 21 hanno un sistema proprio: criteri, tempi e ambienti di test "
    "sono quasi sempre non pubblici o accessibili solo dopo un'adesione formale.",
    "<b>Piemonte, \"certificata SIRPED\" = autocertificata.</b> Il fornitore esegue da solo 139 prove e firma l'esito. "
    "Ma non esiste una procedura pubblicata per un software nuovo: la seconda fase vale solo per chi era già certificato.",
    "<b>Piemonte, avviso CSI AP26_003:</b> il CSI paga i produttori delle cartelle \"certificate SIRPED\", definiti "
    "\"infungibili\". Un modulo aperto e un collaudo trasparente riducono questa dipendenza.",
    "<b>Emilia-Romagna:</b> esiste già una cartella pubblica regionale (Cartella SOLE di Lepida, 42% dei MMG nel 2019). "
    "Il modello pubblico funziona, ma non è open source ed è chiuso in una Regione.",
])

s.append(Paragraph("Contributi da regalare alle Regioni", H2))
s.append(Paragraph("Scrivendo i moduli abbiamo trovato difetti nelle specifiche pubbliche. Li consegniamo insieme al codice.", P))
s += bullets([
    "<b>Puglia:</b> certificato di produzione allegato scaduto il 29/04/2026; tre numeri di versione diversi; esempi XML non "
    "validi; esempi con nomi e codici fiscali che sembrano reali (non copiati).",
    "<b>Friuli-Venezia Giulia:</b> 17 punti, tra cui due certificati di cifratura contraddittori, un residuo dell'editor nello "
    "schema, un esempio con codice fiscale valido e MAC address; un documento \"a circolazione limitata\" scaricabile senza login.",
    "<b>Piemonte:</b> 24 punti, tra cui il comando d'esempio PKCE che produce valori non validi circa 3 volte su 4 e un pincode "
    "cifrato che non entra nel campo previsto.",
    "<b>MEF:</b> uno schema XSD ufficiale non valido (il kit lo aggira senza modificarlo). <b>Gateway FSE:</b> i codici di "
    "esenzione non vengono verificati da nessuno.",
])

s.append(Paragraph("Prossimi passi", H2))
# Revisione esterna giro 2 (6-conformita N5): la frase diceva «tutto ciò che si poteva costruire da fonti
# pubbliche è costruito» mentre la tabella delle Regioni dà l'Umbria «specifiche pubbliche: prossimo modulo».
_DA_FONTI_PUBBLICHE = [r[0] for r in REGIONI if r[6] == "pubbliche"]
s.append(Paragraph("Da fonti pubbliche resta da costruire " + (", ".join(_DA_FONTI_PUBBLICHE) or "niente")
                   + (": il modulo, sulle specifiche pubblicate. " if _DA_FONTI_PUBBLICHE else ". ")
                   + "Ogni altro passo esce di casa e richiede il via di Lorenzo.", P))
s += bullets([
    "<b>Decisioni aperte:</b> nome del progetto, organizzazione GitHub dedicata, email pubblica del progetto.",
    "<b>1. Pubblicazione</b> del codice (EUPL-1.2) e prima versione; archivio su Software Heritage.",
    "<b>2. Catalogo Developers Italia</b> come software open source di terzi (publiccode.yml già valido).",
    "<b>3. Sogei:</b> certificati di test FSE e un'utenza di test dedicata (bozza mail pronta).",
    "<b>4. Regioni:</b> proposte pronte per Puglia, Friuli e Piemonte: \"il vostro modulo è già scritto, gratis e aperto: "
    "ci aprite l'ambiente di test per il collaudo?\". Toscana tramite contatto diretto.",
    "<b>5. Un secondo manutentore e una prima PA che lo adotti:</b> sono le condizioni di fiducia per l'adozione.",
])
s.append(Spacer(1, 8))
s.append(Paragraph("Documenti di dettaglio con tutte le fonti: indagine-mmg-opensource/INVENTARIO.md, REGIONI.md, "
                   "GITHUB_SIMILI.md; varco/docs/ (ARCHITETTURA, DONO, SAR_PUGLIA, SAR_FVG, SAR_PIEMONTE, MINACCE, "
                   "NON_DISPOSITIVO_MEDICO). Medici per Regione: Agenas, Il personale del SSN, dati 2023, Tab. 18.", NOTE))

if SIMILI:
    s.append(PageBreak())
    s.append(Paragraph("Progetti simili su GitHub e altrove", H2))
    s.append(Paragraph(SIMILI["sintesi"], P))
    for titolo, chiave in (("Sovrapposizione reale", "a"), ("Parziali o da riusare", "b")):
        voci = SIMILI.get(chiave) or []
        s.append(Paragraph(f"<b>{titolo}</b>" + ("" if voci else ": nessuno"), P))
        if voci:
            righe = [[Paragraph(h, CELLB) for h in ("Progetto", "Cosa fa", "Stato")]]
            for v in voci:
                righe.append([Paragraph(v["nome"], CELL), Paragraph(v["cosa"], CELL), Paragraph(v["stato"], CELL)])
            t = Table(righe, colWidths=[48 * mm, 88 * mm, 40 * mm], repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, LINE), ("BACKGROUND", (0, 0), (-1, 0), GRIGIO),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            s.append(t)
            s.append(Spacer(1, 6))
    if SIMILI.get("unico"):
        s.append(Paragraph("<b>Cosa ha di unico il kit</b>", P))
        s += bullets(SIMILI["unico"])

doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=17 * mm, rightMargin=17 * mm, topMargin=16 * mm,
                        bottomMargin=17 * mm, title="Varco - resoconto di stato", author="Lorenzo Nannicini")
doc.build(s, onFirstPage=piede, onLaterPages=piede)
print(OUT)
