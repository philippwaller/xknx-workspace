# KNX-Vorschläge: Geräte im Mittelpunkt

Am 25. September 2026 vom Nutzer mit „freigegeben“ genehmigtes UX-Konzept. Grundlage ist der in dieser Sitzung vorgestellte schriftliche und klickbare Entwurf. Die Umsetzung folgt nach Prüfung des Implementierungsplans.

## Ziel und Ausgangspunkt

Nutzer sollen gefundene Gerätevorschläge überblicken, ihre Entitäten prüfen, Zuordnungen korrigieren und eine bewusste Auswahl in Home Assistant anwenden können. Laufende AI-Jobs bleiben sichtbar, beanspruchen aber wenig Platz. Gestaltung, Sprache und Bedienung folgen dem Home-Assistant-Frontend.

Die aktuelle Seite wurde im Browser und im Quellcode geprüft: abgeschlossene Jobs stehen groß über den Ergebnissen; Geräte und Entitäten zeigen zahlreiche Formulare, Herkunftsangaben und JSON dauerhaft. Beim Aufklappen einer Bereichsgruppe entsteht eine sehr lange Seite. Der bestehende Datenstand enthält auch viele Vorschläge ohne Home-Assistant-Bereich.

Annahmen: Desktop ist der wichtigste Arbeitsbereich für das Prüfen größerer Projekte; Mobil bleibt vollständig bedienbar. Die vorhandene Generierung und Prüfung vor dem Anwenden bleiben erhalten. Namen und Zuordnungen sind bearbeitbare Vorschläge, keine automatisch bestätigten Fakten.

## Gestaltungsrichtungen

| Ansatz | Stärke | Nachteil | Entscheidung |
| --- | --- | --- | --- |
| Gruppierte Liste mit aufklappbaren Geräten | Hohe Dichte, direkter Vergleich, gleicher Ablauf auf Mobilgeräten | Braucht eine klare Begrenzung der sichtbaren Detailtiefe | Empfohlen |
| Liste mit festem Detailbereich rechts | Gut für dauerhaftes Bearbeiten eines einzelnen Objekts | Weniger Platz für Namen; eigener mobiler Ablauf; Kontextwechsel beim Vergleich | Keine zusätzliche Seitenleiste in der ersten Version |
| Geräte als Kartenraster | Visuell leicht zu gliedern | Verschiedene Kartenhöhen, schlechte Vergleichbarkeit, viel Leerraum | Für diese Verwaltungsaufgabe ungeeignet |

## Informationsarchitektur

1. Bestehende Home-Assistant-Unterseiten-Kopfzeile mit Titel „Vorschläge“, Zurück-Navigation und „Jobs“.
2. Kompakte Ergebniszusammenfassung mit Anzahl der Geräte- und Entitätsvorschläge, aktuellem Job-Kontext und „Neuer AI-Job“.
3. Nur bei aktiven oder handlungsbedürftigen Vorgängen ein schmaler Infobanner.
4. Suche, Bereichsfilter, Zustandsfilter und Sortierung.
5. Nach Home-Assistant-Bereichen gruppierte Gerätezeilen.
6. Am unteren Rand eine Auswahlleiste mit „Auswahl prüfen“.

Die Hauptliste zeigt den ausgewählten Job. Beim ersten Besuch wird ein aktiver Job bevorzugt, sonst der jüngste noch nicht vollständig angewendete Entwurf. Die Auswahl springt danach nicht durch Hintergrundupdates. Das Job-Menü öffnet zunächst den jeweiligen Detaildialog; „Vorschläge ansehen“ wechselt ausdrücklich den Arbeitskontext. Der aktuelle Kontext bleibt neben der Ergebnisanzahl sichtbar.

Diese Entscheidung ersetzt die bisherige gemeinsame Darstellung mehrerer Läufe in denselben Bereichsgruppen. Sie vermeidet unklare Herkunft, doppelte Vorschläge und eine globale Anwenden-Aktion über mehrere unabhängige Revisionen. Eine kombinierte Vorschlagsliste über alle Jobs ist kein Teil der ersten Version. Die unten beschriebene Jobübersicht zeigt hingegen die vorhandenen Jobs als Navigation.

## Geräte, Entitäten und Aufklappen

Bereichsgruppen starten geöffnet, Geräte geschlossen. So sind die Geräte sofort sichtbar. Bereiche und Geräte lassen sich unabhängig auf- und zuklappen; mehrere Geräte dürfen gleichzeitig offen sein. „Alle einklappen“ bezieht sich ausdrücklich auf Bereichsgruppen, nicht auf einen vollständigen Baum bis in technische Details. Individuelle Öffnungszustände bleiben während der Sitzung und bei Liveupdates erhalten.

Eine Gerätezeile zeigt Auswahl, passendes Icon, Gerätename, eine kurze zweite Zeile mit Modell beziehungsweise KNX-Bezug, Anzahl zugeordneter Entitäten, einen relevanten Status und direkte Bearbeitung. Gerätenamen bleiben auch bei langen Modellbezeichnungen lesbar; auf schmalen Ansichten dürfen sie umbrechen. Die Auswahlfläche und der Aufklappbereich sind getrennte Bedienelemente.

Ein aufgeklapptes Gerät zeigt seine Entitäten als ruhige, eingerückte Zeilen: Auswahl, Icon, Name, Entitätstyp, wenige wichtige Gruppenadressen, geplante Aktion und „Konfigurieren“. „Gerätezuordnung ändern“ ist direkt erreichbar. Lange Adresslisten werden zusammengefasst und lassen sich vollständig öffnen. Technische Herkunft wird einmal in „Quelle und KNX-Details“ zusammengefasst. JSON ist ausschließlich eine optionale technische Ansicht.

Ein Gerätevorschlag ist ein logisches Ziel für Home-Assistant-Entitäten. Er ist nicht zwingend identisch mit einem physischen KNX-Gerät. Verschiedene Funktionen desselben Aktors dürfen daher nicht nur wegen derselben Individualadresse zusammengelegt werden.

## Schnell bearbeiten

**Gerät bearbeiten:** Ein Dialog enthält Name, Bereich und bei Bedarf die zugehörige Etage. Bestehenden Bereich auswählen und neuen Bereich vorschlagen sind Optionen desselben Ablaufs. Neue Bereiche und Etagen bleiben Vorschläge bis zum Anwenden. Bei einem bereits vorhandenen Zielgerät werden dessen Zuordnungen angezeigt; gesperrte Änderungen bekommen eine verständliche Begründung.

**Entität konfigurieren:** Der vorhandene schemaabhängige KNX-Editor wird im Standarddialog wiederverwendet. Name und häufige Felder stehen oben; erweiterte Einstellungen und technische Konfiguration darunter zugeklappt. Speichern und Abbrechen sind feste Dialogaktionen. „Speichern“ ändert den Entwurf; erst „Anwenden“ ändert Home Assistant.

**Gerätezuordnung ändern:** Ein direkter Dialog bietet vorgeschlagene Geräte des aktuellen Jobs und vorhandene Geräte derselben KNX-Integration. Er erklärt, dass die gewählte Entität verschoben wird. Es gibt keine zwei permanent nebeneinander sichtbaren Auswahlfelder pro Entität.

Ein Speichern zeigt den Status lokal am betroffenen Objekt. Bei einem Fehler bleibt die Eingabe erhalten. Beim Schließen eines Editors mit ungespeicherten Änderungen wird nachgefragt. Bei einer geänderten Backend-Revision gibt es keine stille Überschreibung: Eingaben behalten, aktuellen Stand laden und erneut prüfen. Bereits abgeschlossene Aktionen und ihre gesperrten Abhängigkeiten bleiben unveränderlich.

## Suchen, filtern und auswählen

Die Suche umfasst Geräte- und Entitätsnamen, Entitätstypen, KNX-Individualadressen und Gruppenadressen. Trifft die Suche eine Entität, wird ihr Gerät sichtbar aufgeklappt; Suchende müssen den Treffer nicht erst suchen. Nach dem Leeren der Suche gilt wieder der vorherige Aufklappzustand.

Filter: Bereich; „Neu“, „Aktualisieren“, „Ohne Bereich“, „Ausgewählt“ und „Angewendet“, soweit diese Zustände aus den Daten ableitbar sind. „Prüfung erforderlich“ erscheint nur bei tatsächlichen Validierungsproblemen. Ohne Bereich ist nicht automatisch ein Fehler. Technisch unstrukturierte Jobfehler werden nicht heuristisch einem Gerät zugeschrieben.

Standardsortierung: Bereich und darin Gerätename. Ein stabiler Fallback trennt gleiche Namen. Bei laufender Generierung werden neue Ergebnisse angekündigt, ohne die gerade bearbeitete Liste umzusortieren oder den Fokus zu versetzen. „Neue Vorschläge anzeigen“ übernimmt neue Einträge in die sortierte Ansicht; Status bereits sichtbarer Einträge aktualisiert sich direkt.

Auswahl erfolgt pro Entität oder pro Gerät; die Gerätecheckbox hat bei Teilmengen einen Zwischenzustand. Optional ist eine ausdrücklich benannte Aktion „Sichtbare Vorschläge auswählen“ möglich. Filter verändern eine vorhandene Auswahl nicht. Die Auswahlleiste nennt insgesamt ausgewählte Entitäten und gegebenenfalls die Anzahl außerhalb des aktuellen Filters. „Ausgewählt“ bedeutet niemals „inhaltlich geprüft“.

## Jobs: Menü, Infobanner und Detaildialog

**Menü:** „Jobs“ steht dauerhaft in der Kopfzeile. Im aktiven Zustand „Jobs · 1 aktiv“ mit Aktivitätsindikator. Ohne aktiven Job ein neutrales Symbol und die Anzahl gespeicherter Jobs. Menüeinträge zeigen lesbaren Umfang, Startzeit, Status und bei Bedarf einen Hinweiszähler; technische IDs sind verborgen. Aktive Jobs zuerst, dann die jüngsten gespeicherten Jobs. Maximal fünf Einträge plus „Alle Jobs anzeigen“; größere Historien stehen in einem scrollbaren Dialog. Jobs werden nicht automatisch gelöscht.

**Infobanner:** Beispielsweise „Vorschläge werden erstellt · 6 von 12 Teilen abgeschlossen“, darunter bei Bedarf „KI verarbeitet Vorgarten“. Rechts „Job öffnen“, links ein kleiner Ladeindikator. Bei mehreren aktiven Jobs eine zusammengefasste Meldung, die zur Jobübersicht führt. Nach Abschluss einmalig eine knappe Ergebnismeldung; kein dauerhaftes großes Erfolgsfeld. Ein unterbrochener oder fehlgeschlagener Job mit Handlungsbedarf bleibt als kompakter Hinweis sichtbar.

**Detaildialog:** Standardbreite auf Desktop, voller Bildschirm auf Mobil. Kopf mit lesbarem Jobnamen, Status und Startzeit. Darunter Fortschritt, aktuelle Phase, Ergebniszahlen, Hinweise und Ergebnisprotokoll. Quelle, vollständige ID und weitere Diagnosedaten stehen unter „Technische Details“. Schließen beendet den Hintergrundjob nicht.

Generierung und Anwenden werden getrennt dargestellt. „6 von 12 Teilen“ ist realer Mengenfortschritt, keine Aussage über die verbleibende Laufzeit. Ohne bekannte Gesamtmenge läuft ein unbestimmter Indikator. Während eines langen AI-Aufrufs bleibt der bestätigte Gesamtfortschritt stehen und die aktuelle Aktivität wird weiter angezeigt. Warten auf Kapazität und Wiederholungsversuche erhalten explizite Texte. Es gibt keine erfundene Restzeit oder künstlich steigende Prozentzahl.

Die Phasen Vorbereiten, Generieren, Validieren und Speichern beziehen sich auf die aktuelle Verarbeitungseinheit. Sie sind keine einmalige globale Treppe, wenn diese Schritte je Teil erneut durchlaufen werden.

**Ergebniszahlen:** Während der Generierung „7 Gerätevorschläge, 10 Entitätsvorschläge“. Nach Anwendung getrennt „7 Geräte erstellt, 8 Entitäten erstellt, 2 Entitäten aktualisiert“, soweit tatsächlich bestätigt. Geräte, Bereiche und Entitäten werden getrennt gezählt. Die Zahl abgeschlossener Aktionen ist nicht die Zahl erstellter Geräte.

**Protokoll:** Zunächst eine kompakte Zusammenfassung bestätigter Aktionen mit Status, lesbarem Ziel und Fehlergrund; Details aufklappbar. Standardmäßig Fehler und die letzten wenigen Ergebnisse; ein Filter zeigt alle Ergebnisse. Kein JSON-Strom und kein seitenlanges Terminal. Das heutige Modell hat keine Ereigniszeitpunkte pro Aktion und keine vollständige Phasenhistorie. Daher keine ausgedachte Zeitleiste. Ein dauerhaftes, zeitgestempeltes Ablaufprotokoll benötigt einen separaten Backend-Vertrag mit begrenzter Aufbewahrung und strukturierten Ereignissen.

## Job-Zustände und Aktionen

| Zustand | Sichtbarer Text | Angebotene Aktionen |
| --- | --- | --- |
| Generierung läuft | Vorschläge werden erstellt | Öffnen, stoppen; gespeicherte Vorschläge prüfen, soweit bisher erlaubt |
| Kapazitäts- oder Wiederholungswartezeit | Warten auf KI / Neuer Versuch … | Öffnen, stoppen; bestätigten Fortschritt behalten |
| Stoppen läuft | Wird gestoppt | Öffnen; keine erneuten widersprüchlichen Befehle |
| Generierung beendet | Bereit zum Prüfen / Mit Hinweisen beendet | Vorschläge ansehen, Auswahl prüfen |
| Gestoppt oder unterbrochen | Gestoppt / Unterbrochen | Gespeicherte Ergebnisse ansehen; fortsetzen nur bei vorhandener Backend-Unterstützung und erfüllten Bedingungen |
| Anwendung läuft | Änderungen werden angewendet | Fortschritt öffnen; Bearbeitung für diesen Job gesperrt |
| Teilweise angewendet | Teilweise angewendet | Erfolge und Fehler anzeigen; verbleibende Aktionen erneut prüfen und anwenden |
| Vollständig angewendet | Angewendet | Ergebnisse und vorhandene Ressourcen öffnen |

Die exakten erlaubten Aktionen werden aus den bestehenden Statusregeln übernommen, nicht allein aus diesem beschreibenden UI-Text abgeleitet. Fortsetzen der Generierung ist etwas anderes als erneutes Anwenden verbleibender Aktionen.

„Entwurf verwerfen“ liegt im Überlaufmenü eines inaktiven Jobs. Ein Bestätigungsdialog nennt genau den Umfang: Gespeicherter Entwurf wird verworfen, bereits erstellte Home-Assistant-Ressourcen bleiben bestehen. Stoppen und Verwerfen sind verschiedene Aktionen.

## Anwenden: bewusster Abschluss

Die feste Auswahlleiste zeigt eine primäre Aktion „Auswahl prüfen“. Während eines aktiven Jobs ist Anwenden gemäß den bestehenden Regeln gesperrt; ein Text erklärt den Grund. Der Nutzer kann den Job stoppen und die gespeicherten Ergebnisse prüfen.

Die vorhandene Backend-Vorschau bleibt maßgeblich. Der Dialog zeigt oben Anzahl neuer Geräte, neuer Entitäten, Aktualisierungen und gegebenenfalls neuer Bereiche beziehungsweise Etagen. Darunter nur relevante Konflikte und nach Gerät gruppierte Änderungen. Aktualisierungen zeigen zunächst die tatsächlich veränderten Felder mit Vorher/Nachher; technische Vollansichten bleiben aufklappbar. Auch notwendige Abhängigkeiten einer ausgewählten Entität erscheinen in der Vorschau.

„Anwenden“ bestätigt die geprüfte Revision. Bei zwischenzeitlicher Änderung wird die Vorschau erneuert und nochmals geprüft. Nach Start ist der Jobstatus über Banner und Jobdialog erreichbar. Bei Teilfehlern bleiben bestätigte Erfolge sichtbar und unveränderlich; erneut ausgeführt werden nur die nach Backend-Regeln noch offenen Aktionen. Es gibt kein pauschales „Rückgängig“, das bereits erzeugte Ressourcen verlässlich zurückrollen würde.

## Visuelle Regeln und Home-Assistant-Konformität

Eine ruhige Oberfläche mit einheitlichen Listenflächen, feinen Trennlinien und abgestuften Textfarben. Keine große Hero-Fläche, keine Kennzahlenkarten über der Geräteliste, keine dekorativen Verläufe. Der primäre Akzent führt zur nächsten sinnvollen Aktion; Statusfarben bedeuten Zustand und werden immer von Text oder Icon begleitet.

Vorhandene Home-Assistant-Schrift und Typografietokens verwenden. Geräte- und Entitynamen bleiben normaler lesbarer Fließtext; technische Metadaten sind sekundär. Als Entwurfswerte etwa 64–72 px für zweizeilige Geräte und 48–56 px für Entitäten; die endgültige Höhe folgt Inhalt und HA-Komponenten. Abstände verwenden die HA-Skala in 4-px-Schritten. Kleine Abstände dürfen nicht zu kleinen Trefferflächen führen.

Im Produkt werden `hass-subpage`, `ha-dropdown`, `ha-dialog` mit `DialogMixin`, `ha-dialog-footer`, `ha-alert`, `ha-button`, `ha-checkbox`, `ha-expansion-panel`, `ha-progress-bar` und `ha-spinner` sowie die vorhandenen KNX-Formulare und HA-Picker eingesetzt. Farben und Abstände kommen aus den Theme-Variablen und `--ha-space-*`. Keine neue Komponentenbibliothek und keine neue Legacy-`mwc-*`-Verwendung.

Die eigenständige HTML-Vorschau bildet Gestaltung und Interaktionen mit Beispieldaten nach. Sie ist kein tatsächlicher Render der HA-Web-Components und keine Freigabe für pixelgenaue Übernahme ihrer CSS-Werte. Konkrete Abstände und Komponentenverhalten werden später im gepinnten HA-Frontend geprüft.

Auf Mobil werden Metadaten umgebrochen und Dialoge bildschirmfüllend. Suche bleibt oben, weitere Filter können in einen Filterdialog. Die Auswahlleiste verdeckt keine Ergebnisse oder fokussierten Elemente. Alle Hauptfunktionen bleiben ohne Hover verfügbar. Lange Namen, Browserzoom und RTL dürfen kein horizontales Scrollen der Seite verursachen.

## Zugänglichkeit, Fehler und leere Zustände

- Getrennte, beschriftete Checkboxen, Aufklappbuttons und Bearbeitungsaktionen; `aria-expanded` und sichtbarer Tastaturfokus.
- Standard-Dialogfokus, Escape und Rückkehr zum auslösenden Element. Bei Liveupdates bleibt der Fokus stabil.
- Text und zentrale Bedienelemente mit WCAG-AA-Kontrast; Informationen nicht nur über Farbe vermitteln.
- Fortschritt sparsam und höflich ankündigen, nicht jedes Prozent oder jede Entity. Reduzierte Bewegung respektieren.
- Keine Vorschläge: kurze Erklärung mit „AI-Job starten“. Fehlendes ETS-Projekt beziehungsweise fehlende AI-Task-Entität: konkrete Voraussetzung mit passendem Einstieg.
- Keine Suchtreffer: Suchbegriff und Filter bleiben erhalten, „Filter zurücksetzen“ ist erreichbar.
- Verbindungsabbruch: letzten bestätigten Stand zeigen und als veraltet kennzeichnen. Änderungen bis zur erfolgreichen Synchronisierung blockieren; Reconnect lädt aktuellen Zustand.
- Nicht mehr vorhandener Job, geändertes Projekt und Revisionskonflikt: spezifische Meldung und erneutes Laden, keine stille Fortsetzung mit altem Stand.
- Mehrere Fehler: Zusammenfassung mit Anzahl, vollständige Details bei Bedarf. Ohne strukturierte Fehlerdaten keine erfundene Zuordnung zu Geräten oder Schweregraden.

## Umsetzungsschnitt und Abnahme

Die bestehende Service-Schicht mit Snapshot, Subscription, Review-Patches und Backend-Vorschau bleibt der Datenweg. Die Seite hält den ausgewählten Job sowie Such-, Filter- und Aufklappzustände. Geräte- und Bereichskomponenten bleiben zuständig für die Ergebnisdarstellung. Jobdetails werden aus der großen View in einen gezielten Dialog ausgelagert. Editoren und Anwendevorschau werden wiederverwendet und visuell verdichtet. Keine neue globale Zustandsverwaltung.

Fast das gesamte Konzept ist mit dem vorhandenen Datenvertrag als Frontend-Änderung möglich. Ausnahmen sind ein dauerhaftes Ereignisprotokoll und präzise objektbezogene Fehlerklassifizierung. Diese werden separat geplant, falls gewünscht; UI und Backend werden unabhängig geliefert.

Abnahmekriterien:

1. Die normale erste Bildschirmansicht zeigt Gerätevorschläge und keinen aufgeklappten abgeschlossenen Job.
2. Entitäten sind mit einem Klick sichtbar; Konfiguration mit einem weiteren. Gerät bearbeiten ist direkt in der geschlossenen Zeile erreichbar.
3. Der aktuelle Job und der Geltungsbereich der Auswahl sind jederzeit eindeutig.
4. Menü → Jobdialog sowie Infobanner → derselbe Jobdialog funktionieren für alle vorhandenen Zustände.
5. Generierungsfortschritt, erstellte Ressourcen und verbleibende Aktionen werden korrekt getrennt.
6. Suche und Liveupdates verlieren weder Auswahl noch ungespeicherte Eingaben oder den Fokus.
7. Prüfung bei 390 px, 768 px und 1280 px, hell/dunkel und erhöhtem Zoom; vollständiger Ablauf per Tastatur.
8. Gezielt testen: Teilselektion, versteckte Auswahl, Revisionskonflikt, unterbrochener Job, teilweise angewendeter Job und doppelte Befehle. Bestehende Tests für erlaubte Lifecycle-Aktionen bleiben maßgeblich.
9. Visuelle Prüfung mit langen Modellnamen, fehlenden Bereichen, mehreren Jobs und großen Ergebnislisten; geschlossene Geräte laden keine vollständigen Formularbäume.

## Quellen

- Offizielle Design-Dokumentation: https://developers.home-assistant.io/docs/frontend/design/
- Komponenten-Galerie: https://design.home-assistant.io/
- Lokaler, gepinnter HA-Frontend-Leitfaden: `knx-frontend/homeassistant-frontend/AGENTS.md` und Skills für Styling, Komponenten und Benennung.
- Bestehende Vorschlagsansicht, Geräte-/Bereichskomponenten, Start-/Anwendedialoge, Service und Typen unter `knx-frontend/src/features/suggestions`, `src/services/suggestions.service.ts`, `src/types/suggestions.ts`.
- Backend-Datenmodell: `home-assistant-core/homeassistant/components/knx/suggestions/model.py`.

Nächster Schritt: den zugehörigen Implementierungsplan prüfen und die Ausführungsmethode wählen. Die Designfreigabe ist erteilt; mit Produktänderungen beginnt die nächste Phase.
