"""Kauf-Check: Tacho-Betrug-/Unfall-Checkliste + km-Plausibilitaet (mit Verschleissabgleich).

- CHECKLIST: abhakbare Profi-Pruefpunkte mit Erklaerung (Tacho, Unfall, Technik).
- mileage_plausibility: km/Jahr aus Laufleistung + Erstzulassung + Verdikt.
- wear_status: welche Teile bei DIESEM km-Stand laengst faellig waren (Belege
  verlangen!) bzw. als naechstes anstehen (Budget).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .wear import _occurrences, load_items

CHECKLIST = [
    ("🔢 Tacho / Kilometerstand", [
        ("km in ALLEN Steuergeräten gelesen & identisch?",
         "Kombi, Motor-ECU, Getriebe, ABS/ESP, Gateway, BCM, Airbag, teils Schlüssel speichern km. "
         "Betrüger drehen meist nur das Kombi zurück – Abweichung = Tachobetrug. Tool: Hella Gutmann "
         "mega macs, Bosch KTS, Autel, ODIS/ISTA/Xentry."),
        ("Fehlerspeicher-km plausibel (kein Eintrag über aktuellem Stand)?",
         "Jeder Fehler/DPF-Regeneration hat einen km-Zeitstempel → zeigt den echten km-Verlauf. "
         "Eintrag „bei 210.000 km“ bei angeblich 130.000 = entlarvt."),
        ("HU-Bericht-Kilometer über die Jahre steigend?",
         "TÜV/DEKRA notieren km bei jeder HU. carVertical/Historie zeigt den Verlauf."),
        ("Abnutzung passt zu km (Pedalgummis, Lenkrad, Sitz, Schaltknauf)?",
         "Speckiges Lenkrad/abgefahrene Pedale bei 60.000 km = Verdacht."),
        ("Scheckheft lückenlos + Stempel-km steigend?",
         "Marken-Servicehistorie (VW ServiceNet, BMW, Xentry) je VIN gegenchecken."),
    ]),
    ("💥 Unfall / Vorschäden", [
        ("Lackschichtdicke rundum gemessen (gleichmäßig)?",
         "Schichtdickenmessgerät: dicke/ungleiche Werte = nachlackiert/gespachtelt."),
        ("Spaltmaße gleichmäßig, Schrauben an Kotflügel/Türen/Haube unberührt?",
         "Frische/verdrehte Schrauben = Unfallreparatur."),
        ("Schweißnähte/Unterboden original (keine nachträglichen Nähte)?",
         "Nachträgliche Nähte = Strukturschaden."),
        ("VIN-Historie geprüft (carVertical / AutoDNA)?",
         "Aggregiert Schäden, Auktionen, km aus mehreren Ländern (kostenpflichtig, aber Gold wert)."),
        ("Unfallfreiheit schriftlich im Kaufvertrag zusichern lassen?",
         "Rechtliche Absicherung gegen arglistige Täuschung."),
    ]),
    ("🔧 Technik / Probefahrt", [
        ("Fehlerspeicher komplett ausgelesen (keine aktiven Fehler)?",
         "Vor dem Kauf Diagnose – versteckte Fehler kosten später."),
        ("Kaltstart selbst gemacht (Kettenrasseln, blauer/weißer Rauch)?",
         "Warmer Motor verdeckt Steuerketten-/Öl-/Kopfdichtungsprobleme."),
        ("Modellspezifische Schwachstelle deines Untermodells geprüft?",
         "Siehe Tab „Verschleiß“ – z. B. Tesla Querlenker, BMW N47-Kette, Ford EcoBoost Kühlung."),
        ("E-Auto: Batterie-Gesundheit (SoH) ausgelesen?",
         "State of Health via Diagnose/App – <90 % mindert Reichweite & Wert deutlich."),
        ("Ölzustand, Kühlmittel, Bremsflüssigkeit, Reifen-DOT geprüft?",
         "Verschlepptes Service + altes Gummi = versteckte Folgekosten."),
    ]),
    ("📋 Recht, Papiere & Kosten", [
        ("Gewährleistung geklärt (Privat = keine, Händler = 1 Jahr)?",
         "Privatkauf schließt Gewährleistung aus – Mängel danach sind dein Problem. "
         "Beim Händler bleibt die 1-Jahres-Gewährleistung."),
        ("Schriftlicher Kaufvertrag mit Zusicherungen (ADAC-Muster)?",
         "Unfallfreiheit, km-Stand, bekannte Mängel, Anzahl Vorbesitzer schriftlich – sonst "
         "keine Handhabe bei arglistiger Täuschung."),
        ("Zulassungsbescheinigung Teil I + II (Fahrzeugbrief) vorhanden?",
         "Fehlt Teil II, läuft oft noch ein Kredit (Bank hält den Brief) → du wirst nicht Eigentümer. "
         "Halter im Brief = Verkäufer? Ausweis abgleichen."),
        ("HU/AU gültig – Bericht + Restlaufzeit geprüft?",
         "HU bald fällig = ~120 € + Risiko teurer Nachbesserung, um durchzukommen. "
         "Letzten HU-Bericht zeigen lassen (nennt auch km-Stand!)."),
        ("Alle Schlüssel, Serviceheft, Codekarte & Handbuch dabei?",
         "Nachbestellung von Schlüssel/Codekarte kostet 100–400 €; lückenloses Scheckheft = Wert."),
        ("Ummeldung/Zulassung + ggf. Kurzzeitkennzeichen eingeplant (~60–100 €)?",
         "Einmalkosten für Anmeldung, Kennzeichen, evtl. Überführung – im Budget einrechnen."),
        ("EU-Reimport? Ausstattung/Tacho (mph) & Garantie prüfen.",
         "Reimporte sind günstiger, haben aber teils andere Ausstattung/Serviceintervalle."),
    ]),
]

# Die "goldenen Regeln" – kompakte Faustregeln eines kritischen Profis, als
# Merkkasten fuers Kaufgespraech. (gruppe, [regeln])
GOLDEN_RULES = [
    ("🧠 Mindset", [
        "Kauf den Verkäufer, nicht das Auto – Halter = Verkäufer? Ehrlich mit Mängeln? "
        "Drängt er? Wegbleiben-Können ist deine stärkste Waffe.",
        "Verlieb dich nicht – der nächste Gute kommt. Emotion kostet Tausende.",
        "Zu gut = zu gut: 25–30 % unter Markt ist Mangel oder Betrug, kein Glück.",
    ]),
    ("🔍 Besichtigung", [
        "Motor muss KALT sein – warmgefahren verdeckt Kettenrasseln, Rauch, Öldruck. „Schon warm“? → nochmal kommen.",
        "Fehlerspeicher + km in ALLEN Steuergeräten auslesen (OBD) – entlarvt Tacho-Dreh & versteckte Fehler.",
        "Unter's Auto: Unterboden, Radläufe, Bremsleitungen, Schweißnähte. Frischer Unterbodenschutz = Verdacht.",
        "Papiere zuerst: ZB II (Brief) im Original, HU-Bericht (echter km-Verlauf!), lückenloses Scheckheft.",
        "Probefahrt lang & vielseitig, Radio aus – Autobahn, enge Kurve, Vollbremsung, rückwärts.",
    ]),
    ("💶 Geld & Verhandlung", [
        "Nie Vorkasse/Anzahlung/Treuhand/Ausland – bar bei Übergabe oder Echtzeit-Überweisung vor Ort.",
        "Rechne Folgekosten, nicht Kaufpreis – billig + fälliger Zahnriemen/DSG/DPF = teuer.",
        "Zielpreis vorher festlegen, Mängel als Munition nennen – dann schweigen. Wer zuerst redet, verliert.",
        "Ernster Kandidat? Profi-Check (ADAC/TÜV/Werkstatt, ~100–150 €) = billigste Versicherung.",
    ]),
]

# Profi-Zustandsbewertung: die konkreten "Tells", auf die Mechaniker/Haendler
# achten – Beobachtung -> was sie bedeutet. Die letzten Prozent auf Augenhoehe.
PRO_INSPECTION = [
    ("🛢️ Motor & Öl", [
        ("Öleinfülldeckel + Ölpeilstab: heller Schleim / „Mayonnaise“?",
         "Kühlwasser im Öl → Zylinderkopfdichtung/Kopf. Finger dran: hell-braun-schleimig = Alarm (Motorschaden-Risiko)."),
        ("Kühlwasser-Ausgleichsbehälter: Ölfilm obenauf oder brauner Schlamm?",
         "Öl im Kühlwasser → Kopfdichtung. Kühlmittel sollte klar/sauber sein."),
        ("Abgas beim Gasstoß (KALT): Farbe?",
         "Blau = Öl verbrennt (Ventilschaftdichtungen/Ringe/Turbo). Weiß & bleibend = Kühlwasser (Kopfdichtung). Schwarz = zu fett/Einspritzung."),
        ("Ladeluft-/Turboschlauch abziehen: Ölschlamm drin?",
         "Turbo undicht oder Kurbelgehäuse-Entlüftung zu → teurer Turbo-/Motorschaden im Anmarsch."),
    ]),
    ("⚙️ Getriebe & Antrieb", [
        ("Automatik/DSG bei 10–30 km/h: Ruckeln, Schaltschläge, Gedenksekunde?",
         "DQ200/DQ381-Mechatronik/Kupplung → 1.500–2.500 €. Im Stop-and-go am deutlichsten."),
        ("Handschalter: greift die Kupplung erst ganz oben? Im hohen Gang niedrigtourig Vollgas → Drehzahl steigt ohne Schub?",
         "Kupplung rutscht/verschlissen."),
        ("Klackern beim Lenkeinschlag + Anfahren (enge Kurve)?",
         "Antriebswellen-Gleichlaufgelenke – oft weil die Achsmanschette gerissen ist (Fett raus, Dreck rein)."),
    ]),
    ("🛞 Fahrwerk & Bremsen", [
        ("An jeder Ecke kräftig runterdrücken – wippt es mehrfach nach?",
         "Stoßdämpfer müde. Gesund: 1× zurückfedern, dann steht es."),
        ("Reifen ungleichmäßig abgefahren (innen vs. außen), alle 4 vergleichen?",
         "Spur/Achse verstellt (Unfall?) oder Fahrwerk ausgeschlagen."),
        ("Bremsscheiben-Rand: fühlbarer Grat/Lippe? Beim Bremsen Rubbeln oder Ziehen?",
         "Scheiben verschlissen/verzogen; Ziehen = Sattel klemmt."),
        ("Über Bodenwelle / beim Rangieren: Klappern oder Poltern vorn?",
         "Koppelstangen/Querlenker-Buchsen – oft HU-relevant."),
    ]),
    ("🔩 Karosserie & versteckter Rost", [
        ("Frischer Unterbodenschutz oder frisch gewaschener Motorraum?",
         "Versteckt Rost bzw. Öl-/Wasser-Lecks. Ein gesundes Auto braucht das nicht – Vorsicht statt Freude."),
        ("Radläufe innen, Schweller-Unterkante, Bremsleitungen, Domlager, Reserveradmulde?",
         "Rost-Neststellen. Mulde nass/rostig = undicht oder Heckschaden."),
        ("Spaltmaße gleichmäßig? Schrauben an Haube/Kotflügel/Türen unberührt?",
         "Verdrehte/lackierte Schrauben, ungleiche Spalten = Unfallreparatur."),
        ("Lackschichtdicke rundum messen (Gerät ~20 €)?",
         "Dicke/ungleiche Werte = nachlackiert/gespachtelt → Unfall verschwiegen."),
    ]),
    ("💡 Elektrik & Innenraum", [
        ("Zündung an (Motor aus): gehen ALLE Warnleuchten kurz an – und wieder aus?",
         "Fehlt eine (Airbag/ABS/Motor/DPF) → Birne rausgenommen, um einen Fehler zu verstecken! Muss angehen UND ausgehen."),
        ("Alles einzeln durchklicken: Fenster, Sitzheizung, Spiegel, ZV, Kamera, Assistenz?",
         "Jede Reparatur teuer und beim Privatkauf danach dein Problem."),
        ("Muffig/nass? Teppich unter den Matten & Reserveradmulde fühlen, Gurt ganz rausziehen?",
         "Wasserschaden/Hochwasser → Elektronik-Spätschäden. Nasser Gurt/Schimmelrand = Finger weg."),
    ]),
    ("🆔 Identität (fälschungssicher)", [
        ("FIN an mehreren Stellen vergleichen: Scheibe, Türholm, Motorraum, Brief?",
         "Alle müssen identisch sein. Abweichung = zusammengeschweißt/gestohlen (Cut-and-Shut)."),
        ("Typenschild/Plaketten unversehrt, keine überklebten/ersetzten Nieten?",
         "Manipuliertes Typenschild → andere Identität."),
    ]),
]

# Digitale Pruefung per OBD-Diagnose: was ein Diagnosegeraet verraet, das man
# von aussen nicht sieht. (kategorie, [(was pruefen, was es verraet)])
OBD_CHECKS = [
    ("🔌 Standard-OBD2 (~10–20 € Bluetooth-Adapter + App: Car Scanner, Torque)", [
        ("Fehlerspeicher auslesen (aktive + gespeicherte Codes)",
         "Versteckte Defekte, die (noch) keine Warnleuchte zeigen – vor dem Warmfahren einstecken."),
        ("Readiness-/Bereitschaftsmonitore prüfen",
         "Stehen sie auf „nicht bereit“, wurde der Fehlerspeicher KÜRZLICH gelöscht – meist um Fehler/HU zu vertuschen. Starkes Warnsignal."),
        ("Live-Daten: Kraftstoff-Trims (LTFT), Zündaussetzer, Kühlmitteltemp, Ladedruck",
         "Hoher Langzeit-Trim = Falschluft/Einspritzung; Aussetzer = Zündung/Kompression; Ladedruck daneben = Turbo."),
        ("Freeze-Frame (Umgebungsdaten zum Fehler) ansehen",
         "Enthält oft den km-Stand beim Fehler – direkt mit dem Tacho abgleichen!"),
        ("12V-Spannung (Ruhe ~12,5 V, laufend ~14 V)",
         "Schwache/alte Batterie oder defekte Lichtmaschine."),
    ]),
    ("🛠️ Marken-Diagnose (OBDeleven/VCDS bei VW-Konzern · BimmerLink BMW · Forscan Ford · Carly)", [
        ("ALLE Steuergeräte scannen (ABS, Airbag, Getriebe, Gateway)",
         "Generische Reader sehen nur den Motor – Fehler in ABS/Airbag/Getriebe bleiben sonst verborgen."),
        ("km-Stand in mehreren Steuergeräten vergleichen (Kombi, Motor, Gateway, Schlüssel)",
         "DAS Anti-Tacho-Betrug-Werkzeug: weichen die Werte ab → zurückgedreht."),
        ("Fehlerspeicher-Einträge MIT km-Zeitstempel",
         "Ein Fehler „bei 210.000 km“ auf einem „130.000-km-Auto“ entlarvt den Betrug."),
        ("Adaptionswerte (DSG-Kupplung, Injektoren, Drosselklappe)",
         "Zeigt DSG-Kupplungsverschleiß nahe Grenzwert bzw. verschlissene Komponenten."),
    ]),
    ("🔋 E-Auto / Hybrid – der wichtigste Digital-Check", [
        ("Batterie-Gesundheit (SoH) auslesen (Car Scanner, aviloo, Hersteller-App)",
         "<90 % SoH mindert Reichweite und Wert massiv – der teuerste Posten am E-Auto."),
        ("Zellspannungen / Balancing, Ladezyklen",
         "Ausreißer-Zelle = beginnender Akkudefekt; viele Schnelllade-Zyklen = mehr Alterung."),
        ("12V-Batterie & DC-DC-/Ladewandler (z. B. ICCU bei Hyundai/Kia)",
         "Häufige E-Auto-Panne (ADAC) – teils sicherheitskritisch."),
    ]),
]

# Diagnose-Ergebnisse DEUTEN – wie man einen Profi-Scan (z.B. Hella Gutmann
# mega macs) beim Gebrauchtwagenkauf liest. (schritt, [(beobachtung, deutung)])
DIAGNOSE_INTERPRETATION = [
    ("1️⃣ Gesamtabfrage (alle Steuergeräte) überfliegen", [
        ("Welche Systeme zuerst zählen",
         "Motor, Getriebe, ABS/ESP, Airbag/SRS – Fehler dort sind teuer oder sicherheitsrelevant. Komfort (Radio, PDC, Licht) ist nachrangig."),
        ("Wie viele Fehler normal sind",
         "Ein paar sporadische/historische Einträge sind bei älteren Autos normal. VIELE aktuelle Fehler über mehrere Systeme = vernachlässigt / Problemauto."),
        ("Verdächtig sauber",
         "Blitzsauberer Speicher bei altem Auto + „gerade frisch gemacht“ = oft kurz vorher gelöscht, um Fehler zu verstecken."),
    ]),
    ("2️⃣ Fehlercode-Status verstehen", [
        ("statisch / aktuell",
         "Fehler liegt JETZT an → echtes, aktives Problem. Ernst nehmen."),
        ("sporadisch / historisch",
         "War da, ist gerade weg – Wackelkontakt oder Einmal-Effekt. Häufigkeitszähler beachten."),
        ("Umweltbedingungen / Freeze-Frame zum Code",
         "Zeigt km-Stand & Bedingungen beim Fehler → mit Tacho abgleichen. km über aktuellem Stand = Betrug."),
    ]),
    ("3️⃣ Teuer oder harmlos? (typische Code-Familien)", [
        ("P0300–P0308 Zündaussetzer",
         "Zündung/Kompression – günstig (Kerze/Spule) bis teuer (Motor). Zylinder-Zähler ansehen."),
        ("P0401 / P042x / P24xx – AGR, DPF, Kat",
         "Diesel-Abgas oder Katalysator – oft vierstellig teuer."),
        ("P0234 / P229x – Ladedruck",
         "Turbo/Ladedruck – teuer."),
        ("P07xx – Getriebe",
         "Getriebe/Kupplung – teuer, besonders DSG."),
        ("B-Codes Airbag/SRS (crash-gespeichert)",
         "Sicherheit – crash-Eintrag = das Auto hatte einen Unfall / ausgelösten Airbag."),
        ("einzelne Sensor-/Glühkerzen-Codes",
         "Meist günstig – trotzdem als Verhandlungspunkt notieren."),
    ]),
    ("4️⃣ Der wichtigste Check: km-Abgleich", [
        ("km in mehreren Steuergeräten vergleichen (Kombi, Motor, Gateway)",
         "Der mega macs liest km aus mehreren ECUs. Weichen sie ab → Tacho zurückgedreht."),
        ("Serviceintervall-/Wartungshistorie",
         "Letzter Service-km ist oft hinterlegt – passt er zum aktuellen Tacho?"),
    ]),
    ("5️⃣ Istwerte (Live-Daten) richtig lesen", [
        ("Kraftstoff-Anpassung / Lambda-Regelung (Fuel Trim)",
         "Grob ±10 % ist ok. Stark positiv = Falschluft / zu mager."),
        ("Zündaussetzer-Zähler je Zylinder",
         "Wächst im Leerlauf → Zündung/Einspritzung/Kompression an genau diesem Zylinder."),
        ("Ladedruck Soll vs. Ist",
         "Sollten zusammenlaufen; große Abweichung = Turbo undicht/defekt."),
        ("DPF: Beladung, Regenerationsabstand, Aschemasse",
         "Hohe Aschemasse / sehr häufige Regeneration = DPF am Ende (teuer)."),
        ("E-Auto: SoH & Zellspannungen",
         "SoH <90 % = Reichweite/Wert runter; Ausreißer-Zelle = Akku-Warnung."),
    ]),
    ("💡 So holst du das Meiste aus dem mega macs", [
        ("Integrierte „bekannte Probleme / Reparaturhinweise / SIS“ nutzen",
         "Der Tester zeigt zum Modell/Code oft die bekannten Ursachen und Prüfschritte – erspart Raten."),
        ("Exakten Code + Klartext fotografieren",
         "Dann lässt sich jeder Code gezielt nachschlagen (Kosten, Häufigkeit) statt pauschal zu deuten."),
    ]),
]


# Typische Betrugsmaschen beim Gebrauchtwagenkauf (DE, v.a. Kleinanzeigen/eBay-KA).
# Je Masche: wie du sie erkennst (signal) und wie du dich schuetzt (protect).
SCAM_PATTERNS = [
    ("💸 Preis zu gut, um wahr zu sein",
     "Auto 20–40 % unter Marktwert, top ausgestattet, wenig km. Der Preis ist der Koeder – "
     "das Auto existiert oft gar nicht oder hat verschwiegene Schaeden.",
     "Marktwert vergleichen (dieses Tool: „unter fairem Preis“). >25 % darunter = Alarm. "
     "Niemals wegen des Preises die Vorsicht ausschalten."),
    ("🌍 Verkäufer/Fahrzeug angeblich im Ausland",
     "„Bin beruflich in UK/Irland/Spanien“, Auto stehe im Ausland oder werde „per Spedition geliefert“. "
     "Oft holprig formuliertes Deutsch, Kommunikation nur per E-Mail/WhatsApp.",
     "Kein Kauf ohne persoenliche Besichtigung VOR Ort in Deutschland. Auslandslogistik + Vorkasse = Betrug."),
    ("🏦 Treuhand-/Escrow-Masche",
     "„Sichere Abwicklung über Treuhänder / eBay-Kaufabwicklung / DHL-Treuhand / PayPal-Schutz“ – "
     "mit gefälschten E-Mails im Original-Design. Auto komme nach Zahlung an den „Treuhänder“.",
     "eBay-Kleinanzeigen hat KEINEN Auto-Treuhandservice. Solche Links/Mails IMMER Fake. "
     "Nur Zahlung Zug um Zug bei Uebergabe."),
    ("💳 Anzahlung / Reservierung aus der Ferne",
     "„Andere Interessenten – überweise 10–20 % Anzahlung, dann reserviere ich / liefere ich.“",
     "NIE anzahlen, bevor du Auto + Papiere + Verkäufer persönlich gesehen hast. Weg = weg."),
    ("🚫 Keine Besichtigung/Probefahrt möglich",
     "Auto „schon verpackt/verschifft“, „eingelagert“, „Schlüssel beim Spediteur“ – Ausreden, warum "
     "du es nicht anschauen kannst.",
     "Keine Besichtigung = kein Kauf. Punkt."),
    ("🎁 Zahlung per Gutscheinkarten / Krypto / Western Union / Auslandskonto",
     "Verlangt Steam-/Amazon-Gutscheincodes, Bitcoin, Bargeldtransfer (Western Union/MoneyGram) "
     "oder Ueberweisung auf ein Auslands-IBAN (nicht DE).",
     "Seriös ist NUR: Barzahlung bei Uebergabe oder Echtzeit-Ueberweisung vor Ort. Alles andere = Betrug."),
    ("😢 Emotionale Dringlichkeits-Story",
     "Soldat im Einsatz, Trauerfall/Erbstueck, schnelle Auswanderung, Scheidung – „muss schnell weg, "
     "deshalb billig“. Erzeugt Zeitdruck + Mitleid.",
     "Story ist Teil der Masche. Zeitdruck ignorieren, nüchtern prüfen wie bei jedem Kauf."),
    ("🖼️ Gestohlene oder generische Fotos",
     "Nur Hochglanz-/Prospektbilder, kein Kennzeichen, keine Detail-/Mängelfotos. Gleiches Inserat "
     "taucht mehrfach/mit anderem Preis auf.",
     "Bilder-Rückwärtssuche (Google Lens/TinEye). Echte Detailfotos + Video verlangen (mit Zettel/Datum)."),
    ("🧾 Halter ≠ Verkäufer / keine FIN / fehlende Papiere",
     "Name im Fahrzeugbrief passt nicht zum Verkäufer, keine FIN im Inserat, „Papiere kommen per Post“, "
     "Ummeldung sollst DU übernehmen.",
     "Zulassungsbescheinigung Teil I+II + Ausweis prüfen (Halter=Verkäufer). FIN abgleichen (Tab VIN-Decoder). "
     "carVertical/AutoDNA-Historie ziehen."),
    ("🔢 Tacho-Rückdreh",
     "km wirken zu niedrig fürs Baujahr, Abnutzung (Lenkrad/Pedale/Sitz) passt nicht.",
     "Siehe Checkliste oben: km in ALLEN Steuergeräten + Fehlerspeicher-km + HU-Historie gegenprüfen."),
]


def scam_flags(price: float | None, fair_price: float | None = None,
               mileage: int | None = None, first_reg: str | None = None,
               price_rating: int | None = None, ref=None) -> list[dict]:
    """Automatische Risiko-/Betrugs-Warnungen fuer EIN Angebot (datengetrieben).

    Kombiniert Fair-Preis-Abstand (zu billig!) und km-Plausibilitaet. Gibt
    Liste {level, text}; level: 'danger' | 'warn' | 'info'.
    """
    out: list[dict] = []
    if price and fair_price and fair_price > 0:
        gap = (price - fair_price) / fair_price          # negativ = guenstiger als fair
        fair_eur = f"{fair_price:,.0f} €".replace(",", ".")
        if gap <= -0.35:
            out.append({"level": "danger", "text":
                f"Preis {abs(gap)*100:.0f} % unter statistischem Marktwert (fair ~{fair_eur}). "
                "Extrem günstig heißt fast immer: verschwiegener Unfall/Mangel ODER Betrug "
                "(Vorkasse/Ausland/Treuhand). Nie ohne Besichtigung + Papiere zahlen."})
        elif gap <= -0.20:
            out.append({"level": "warn", "text":
                f"Preis {abs(gap)*100:.0f} % unter Marktwert (fair ~{fair_eur}). Auffällig günstig – "
                "Grund klären (Mangel? Unfall? Lockangebot?). Besichtigung Pflicht, keine Anzahlung aus der Ferne."})
    mp = mileage_plausibility(mileage, first_reg, ref)
    if mp and mp["km_per_year"] < 5000:
        out.append({"level": "warn", "text":
            f"Nur ~{mp['km_per_year']:.0f} km/Jahr – Tacho-Rückdreh oder langer Stillstand möglich. "
            "km in allen Steuergeräten + Fehlerspeicher-km + HU-Historie prüfen."})
    return out


def next_hu(first_reg: str | None, ref=None) -> dict | None:
    """Naechste HU (TÜV) aus der Erstzulassung: erste HU nach 36 Monaten, danach
    alle 24 (planmaessig angenommen). Gibt {due 'YYYY-MM', months_until, level};
    level='warn' wenn in <=4 Monaten faellig. Realen HU-Bericht trotzdem pruefen!"""
    if not first_reg:
        return None
    ref = ref or datetime.now(timezone.utc)
    try:
        y = int(str(first_reg)[:4])
        mo = int(str(first_reg)[5:7]) if len(str(first_reg)) >= 7 else 1
    except (ValueError, TypeError):
        return None
    months_since = (ref.year - y) * 12 + (ref.month - mo)
    if months_since < 36:
        due_m = 36
    else:
        due_m = 36 + 24 * ((months_since - 36 + 23) // 24)   # naechster Termin >= jetzt
    due_year = y + (mo - 1 + due_m) // 12
    due_month = (mo - 1 + due_m) % 12 + 1
    mu = due_m - months_since
    level = "warn" if mu <= 4 else "info"
    return {"due": f"{due_year:04d}-{due_month:02d}", "months_until": mu, "level": level}


def warranty_note(source: str | None) -> str | None:
    """Gewaehrleistungs-Hinweis je Quelle (Privat vs. Haendler)."""
    if source == "kleinanzeigen":
        return ("Meist **Privatverkauf** → KEINE Gewährleistung („gekauft wie gesehen unter "
                "Ausschluss jeder Gewährleistung“). Mängel = dein Risiko. Alle Zusagen "
                "(unfallfrei, km, Mängel) schriftlich in den Kaufvertrag!")
    if source in ("autoscout24", "watch"):
        return ("Meist **Händler** → gesetzliche Gewährleistung (bei Gebraucht oft auf **1 Jahr** "
                "verkürzt). Sachmängel reklamierbar – Kaufvertrag/Rechnung aufheben.")
    return None


def emission_note(drivetrain: str | None, year: int | None) -> tuple[str, str] | None:
    """Umweltzonen-/Fahrverbots-Hinweis, v.a. fuer aeltere Diesel. (level, text)."""
    if (drivetrain or "").lower() != "diesel" or not year:
        return None
    if year < 2015:
        return ("warn", "Diesel vor ~2015 ist oft nur Euro 5 oder älter → **Fahrverbote / "
                "Umweltzonen** in einigen Städten möglich, schlechterer Wiederverkauf. "
                "Euro-Norm (Feld 14.1 im Schein) prüfen!")
    return ("info", "Diesel: Euro-Norm (idealerweise Euro 6d) im Fahrzeugschein prüfen – "
            "relevant für Umweltzonen und Wiederverkauf.")


def zahnriemen_time_status(conn: sqlite3.Connection, model_id: int, variant: str | None,
                           first_reg: str | None, ref=None, years_interval: int = 6) -> dict | None:
    """Zeitbasierter Zahnriemen-Check. Der km-Verschleiss deckt nur die Laufleistung
    ab – ein ALTER Wagen mit wenig km kann den Zahnriemen nach ZEIT ueberfaellig
    haben (reisst -> Motorschaden). Nur fuer Modelle MIT Zahnriemen (nicht Kette).
    Gibt {due, age_years, years_interval, cost, component} oder None."""
    items = load_items(conn, model_id)
    if variant and variant != "alle":
        items = [i for i in items if i["variant"] in (variant, "alle")]
    zr = [i for i in items if "zahnriemen" in (i["component"] or "").lower()]
    if not zr or not first_reg:
        return None
    ref = ref or datetime.now(timezone.utc)
    try:
        y = int(str(first_reg)[:4])
        mo = int(str(first_reg)[5:7]) if len(str(first_reg)) >= 7 else 1
    except (ValueError, TypeError):
        return None
    age_years = (ref.year - y) + (ref.month - mo) / 12.0
    return {"due": age_years >= years_interval, "age_years": age_years,
            "years_interval": years_interval,
            "cost": max(i["cost_eur"] for i in zr), "component": zr[0]["component"]}


def age_service_checks(first_reg: str | None, mileage: int | None,
                       drivetrain: str | None, ref=None) -> list[dict]:
    """Alters-/zeitbasierte Pruefpunkte, die der km-Verschleiss verpasst
    (wie der Zahnriemen). Liste {level, text}; level 'warn'|'info'."""
    mp = mileage_plausibility(mileage, first_reg, ref)
    if not mp:
        return []
    age, kmy, dt = mp["age_years"], mp["km_per_year"], (drivetrain or "").lower()
    out: list[dict] = []
    if age >= 2:
        out.append({"level": "warn" if age >= 4 else "info", "text":
            "🛑 **Bremsflüssigkeit** alle ~2 Jahre wechseln (zieht Wasser → Siedepunkt sinkt → "
            "Bremsversagen bei Belastung). Wechselbeleg da? Sonst ~60–90 € einplanen."})
    if age >= 5:
        out.append({"level": "warn", "text":
            f"🔋 **12V-Starterbatterie** hält nur ~5–6 J – bei {age:.0f} J oft fällig (~120–200 €). "
            "Häufigste Pannenursache (ADAC), unabhängig von der Laufleistung."})
    if age >= 6:
        out.append({"level": "info", "text":
            "🛞 **Reifenalter**: Gummi altert nach ~6–8 J aus (hart/rissig, unsicher) – "
            "DOT-Nummer (Woche/Jahr) prüfen, nicht nur die Profiltiefe."})
    if age >= 3:
        out.append({"level": "info", "text":
            "❄️ **Klimaanlage**: Kältemittel/Service alle ~2–3 J (~80 €). Im Test: kühlt sie zügig?"})
    if kmy < 6000 and age >= 3:
        out.append({"level": "warn", "text":
            f"🐌 **Wenigfahrer** (~{kmy:.0f} km/J): Standschäden prüfen – Bremsscheiben-Korrosion, "
            "alte/verharzte Betriebsstoffe, Reifen-Standplatten, bei Diesel DPF-Zusetzen."})
    if dt == "elektro" and age >= 3:
        out.append({"level": "info", "text":
            "🔌 **E-Auto**: Bremsscheiben rosten oft (Rekuperation → Bremse kaum genutzt); "
            "12V-Batterie & Batterie-Gesundheit (SoH) auslesen lassen."})
    return out


def listing_age_days(first_seen: str | None, ref=None) -> int | None:
    """Tage, die ein Inserat schon online ist (aus first_seen ISO-Timestamp)."""
    if not first_seen:
        return None
    ref = ref or datetime.now(timezone.utc)
    try:
        seen = datetime.fromisoformat(first_seen)
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, (ref - seen).days)


def negotiation_hint(offer_price: float | None, fair_price: float | None = None,
                     days_online: int | None = None) -> dict | None:
    """Verhandlungs-Zielpreis + Spielraum aus Fair-Preis und Inserats-Standzeit.

    Der faire Marktwert ist der Anker; lange Standzeit ('Ladenhüter') gibt
    zusaetzlichen Hebel. Gibt {target, room_eur, room_pct, args} oder None.
    """
    if not offer_price or offer_price <= 0:
        return None
    args: list[str] = []
    if fair_price and fair_price > 0:
        gap = (offer_price - fair_price) / fair_price
        if gap > 0.02:
            anchor = fair_price               # überteuert -> Ziel ist der faire Marktwert
            args.append(f"{gap*100:.0f} % über fairem Marktwert (~{fair_price:,.0f} €)".replace(",", "."))
        else:
            anchor = offer_price * 0.96        # schon markt-/untergerecht -> moderater Handelsabschlag
            if gap < -0.05:
                args.append("bereits unter Marktwert – Spielraum v. a. über Zustand & Standzeit")
    else:
        anchor = offer_price * 0.93           # ohne Fair-Preis: ~7 % Daumenregel
    extra = 0.0                                # Standzeit-Bonus (Käufermarkt)
    if days_online is not None:
        if days_online >= 60:
            extra = 0.05
            args.append(f"seit {days_online} Tagen online – langer Ladenhüter, starker Hebel")
        elif days_online >= 30:
            extra = 0.03
            args.append(f"seit {days_online} Tagen online")
    target = min(round(anchor * (1 - extra)), int(offer_price))
    room = offer_price - target
    return {"target": target, "room_eur": room,
            "room_pct": (room / offer_price * 100) if offer_price else 0.0, "args": args}


def mileage_plausibility(mileage: int | None, first_reg: str | None, ref=None) -> dict | None:
    """km/Jahr + Verdikt aus Laufleistung und Erstzulassung ('YYYY-MM')."""
    if not mileage or not first_reg:
        return None
    ref = ref or datetime.now(timezone.utc)
    try:
        y = int(str(first_reg)[:4])
        m = int(str(first_reg)[5:7]) if len(str(first_reg)) >= 7 else 1
    except ValueError:
        return None
    age = max(0.3, (ref.year - y) + (ref.month - m) / 12.0)
    kmy = mileage / age
    if kmy < 5000:
        verdict, level = "auffällig niedrig – Rückdreh/Standschäden prüfen!", "warn"
    elif kmy < 8000:
        verdict, level = "niedrig (Wenigfahrer möglich – trotzdem prüfen)", "info"
    elif kmy <= 22000:
        verdict, level = "normal", "ok"
    elif kmy <= 32000:
        verdict, level = "hoch (Vielfahrer/Langstrecke)", "info"
    else:
        verdict, level = "sehr hoch – Verschleiß genau prüfen", "warn"
    return {"km_per_year": kmy, "age_years": age, "verdict": verdict, "level": level}


def wear_status(conn: sqlite3.Connection, model_id: int, variant: str | None, mileage: int):
    """(schon_faellig, demnaechst): Teile, die bei diesem km-Stand bereits faellig
    waren (Belege verlangen) bzw. als naechstes anstehen."""
    items = load_items(conn, model_id)
    if variant and variant != "alle":
        items = [i for i in items if i["variant"] in (variant, "alle")]
    done, upcoming = [], []
    for it in items:
        occ = _occurrences(it["at_km"], it["interval_km"], mileage)
        if occ >= 1:
            done.append(it)
        if mileage < it["at_km"]:
            nxt = it["at_km"]
        elif it["interval_km"]:
            nxt = it["at_km"] + occ * it["interval_km"]
        else:
            nxt = None
        if nxt and nxt > mileage:
            upcoming.append({**it, "next_km": nxt})
    return (sorted(done, key=lambda x: -x["cost_eur"]),
            sorted(upcoming, key=lambda x: x["next_km"]))


def due_soon(conn: sqlite3.Connection, model_id: int, variant: str | None,
             mileage: int, horizon_km: int = 15000) -> list[dict]:
    """Teile, die INNERHALB der naechsten `horizon_km` faellig werden.

    Genau die Warnung 'Achtung, in ~X km ist Teil Y faellig'.
    """
    items = load_items(conn, model_id)
    if variant and variant != "alle":
        items = [i for i in items if i["variant"] in (variant, "alle")]
    out = []
    for it in items:
        occ = _occurrences(it["at_km"], it["interval_km"], mileage)
        if mileage < it["at_km"]:
            nxt = it["at_km"]
        elif it["interval_km"]:
            nxt = it["at_km"] + occ * it["interval_km"]
        else:
            nxt = None
        if nxt and 0 < (nxt - mileage) <= horizon_km:
            out.append({**it, "next_km": nxt, "km_until": nxt - mileage})
    return sorted(out, key=lambda x: x["km_until"])


def carvertical_url(vin: str | None = None) -> str:
    base = "https://www.carvertical.com/de"
    return f"{base}/history?vin={vin}" if vin else base
