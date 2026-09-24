# Kosteneffizienter ETS-Import mit konsistenten logischen KNX-Geräten

- Status: Architekturgrundsatz im Gespräch bestätigt; schriftliche Spezifikation zur Prüfung.
- Datum: 2026-09-25.
- Verantwortlicher Bereich: KNX Suggestions in Home Assistant Core; KNX Frontend für Darstellung und Startoptionen.
- Ausgangspunkt: [KNX project suggestions](2026-09-24-knx-project-suggestions-design.md).
- Beschluss: AI klärt unsichere Zugehörigkeit und Bedeutung. Lokaler Code verantwortet Geräteidentität, Konfiguration, Abgleich und Validierung.

## Ziel und Geltungsbereich

Der ETS-Import soll weniger API-Kosten und Wartezeit verursachen, ohne die Qualität oder Vollständigkeit der vorgeschlagenen Home-Assistant-Konfiguration zu verschlechtern. Ein logischer Verbraucher erhält ein konsistentes Home-Assistant-Device mit seinen Haupt- und Zusatzfunktionen. Die Zahl erzeugter Entities allein ist kein Qualitätsmaß.

Das System muss sowohl gepflegte ETS-Funktionen als auch Projekte unterstützen, deren Zusammenhänge überwiegend aus Namen, Gruppenadresshierarchien und Kommunikationsobjekten hervorgehen. Diese gemischte Datenqualität ist eine Entwurfsannahme; die Verteilung in realen Nutzerprojekten ist noch nicht gemessen. Sie darf nicht durch eine Pflicht zu gepflegten ETS-Funktionen ersetzt werden.

Diese Spezifikation ersetzt die bisherigen Entscheidungen zur Analyseaufteilung, Kontextaufbereitung und AI-Konfigurationserzeugung. Labs, expliziter Start, Review, Benutzerkorrekturen, sichere Anwendung, YAML-Schreibschutz und Wiederaufnahme bleiben bestehen. Das Dokument ist kein Implementierungsplan und autorisiert noch keine Produktänderungen.

## Vergleich der Architekturvarianten

| Kriterium | A: bisherigen Ablauf verschlanken | B: lokale Verarbeitung mit gezielter AI | C: hierarchische AI-Pipeline |
| --- | --- | --- | --- |
| Aufgabenverteilung | AI erzeugt weiter fertige Konfiguration | lokal gruppieren; AI klärt Semantik; lokal konfigurieren | AI erkennt Geräte; weitere AI-Stufen konfigurieren |
| Token und Kosten | mittel; Konfigurationswissen wiederholt sich | voraussichtlich niedrig bei Regeln und Wiederverwendung | zusätzliche Planungs- und Übergabetokens |
| Aufrufe | einer pro Paket plus Wiederholungen | null für sichere/cachebare Fälle; sonst Pakete offener Kandidaten | Erkennungsstufe plus Konfigurationspakete |
| Latenz | geringer Umbau; Verbesserung durch Scheduling | kurze lokale Pfade; AI nur bei Bedarf | zusätzliche sequenzielle Abhängigkeiten |
| Kontextgröße | mittel | kleine vollständige Kandidaten mit nötigen Nachbarn | lokaler Kontext plus übergreifende Zusammenfassungen |
| Gerätezuordnung | von Paketgrenzen abhängig | explizite Evidenz; Kandidatengrenzen korrigierbar | frühe Erkennungsfehler können fortwirken |
| Wiederverwendung | meist vollständige Pakete | einzelne Analysen und Rollenbindungen | Zwischenergebnisse mehrerer Stufen |
| Implementierung | niedrig | mittel | hoch |
| Skalierung | begrenzt verbessert | gut mit Indizes und inkrementeller Analyse | möglich, mit höherem Orchestrierungsaufwand |

Variante B ist gewählt. A ist als Übergang brauchbar. Eine zweite AI-Stufe aus C bleibt eine gezielte Ausnahme für ungeklärte Fälle. Es gibt keine obligatorische AI-Kette für jedes Gerät und keinen verpflichtenden separaten Klassifikationsaufruf.

## Befunde zum vorhandenen Draft

Untersucht wurden der lokale Core-Stand `f50d011d875`, die Suggestions-Module, ihre Tests, der Frontend-Startdialog und die AI-Task-/OpenAI-Anbindung. Die folgenden Größen stammen aus lokaler Promptaufbereitung ohne Provideraufruf und ohne vorhandene HA-Entities oder HA-Raumlisten im Kontext.

| Test-Fixture | GAs | Pakete | Prompt-Bytes gesamt | Anteil Konfigurationsvertrag |
| --- | ---: | ---: | ---: | ---: |
| Core `tests/components/knx/fixtures/project.json` | 7 | 1 | 28.286 | 68,3 % |
| xknxproject `testprojekt-ets6-functions.json` | 2 | 1 | 21.959 | 88,0 % |
| xknxproject `smart_linking.json` | 35 | 4 | 117.986 | 65,5 % |

`_configuration_contract()` enthält 17 Plattformen und misst 19.324 Bytes. Der gesamte Vertrag wird jedem Aufruf beigefügt. Sein Python-Cache spart keine übertragenen Tokens. Die derzeitige Näherung Bytes/4 entspricht dafür etwa 4.831 Input-Tokens pro Aufruf; das sind keine gemessenen Abrechnungstokens.

`build_parts()` bewahrt ETS-Funktionen innerhalb von Räumen, teilt nicht zugeordnete Evidenz jedoch in Pakete von höchstens zwölf Einzeladressen/Objekten/Restgeräten auf. Die Grenze entspricht keinem logischen Gerät. `_part_context()` ergänzt keine systematische Nachbarschaft weiterer relevanter Objekte desselben Kanals. Der normale Teilkontext enthält keine `group_ranges`.

`generation.py` lässt die AI vollständige Storage-Konfiguration als JSON-String in strukturierter Ausgabe erzeugen. Die Geräte-IDs hängen von Paket und AI-Endpoint-ID ab; `_append_result()` hängt Teilresultate an. Die Überlappungsprüfung vereinigt keine komplementären Teilgeräte über Paketgrenzen hinweg.

Die Verarbeitung wartet zwischen Paketen mindestens 30 Sekunden, bei größeren Prompts länger. Bei 100 Paketen entstehen mindestens 49,5 Minuten Zwischenpausen. Wiederholtes Hashen des gesamten Projekts sowie wiederholte Vollsuchen und Serialisierungen sind zusätzliche lokale Arbeit.

Checkpointing vermeidet beim Resume Wiederholung abgeschlossener Pakete desselben Laufs. Ein Analyse-Cache über neue Läufe hinweg fehlt. Die AI-Task-Rückgabe enthält keine generischen Abrechnungsdetails; der lokale OpenAI-Adapter zeichnet Input-/Output-Tokens im Trace auf, aber in den untersuchten Stellen keine aufgeschlüsselten Cache-Tokens.

## Fachliche Invarianten

1. Ein logischer Verbraucher ist nicht gleich einer Gruppenadresse, einer Individualadresse oder einem Raum.
2. Ein physischer Mehrkanalaktor darf mehreren logischen Geräten zugeordnet sein. Ein logisches Gerät darf Evidenz mehrerer physischer Geräte verwenden.
3. Gemeinsame Zentraladressen, Taster und geteilte Statussignale begründen keine automatische Geräteverschmelzung.
4. Der Einbauort eines Aktors bestimmt nicht den funktionalen Raum seiner Verbraucher.
5. DPT, Kommunikationsflags und Namen sind Evidenz. Keines dieser Merkmale allein beweist eine semantische Rolle oder Gerätezugehörigkeit.
6. Relevante Zusatzfunktionen dürfen nicht stillschweigend verschwinden. Unklar oder nicht unterstützt ist ein sichtbares Ergebnis.
7. Paketgrenzen und AI-Modellwahl dürfen keine Geräteidentität definieren.
8. Benutzerkorrekturen werden durch neue Analyse, Cache-Treffer oder Wiederaufnahme nicht überschrieben.

## Verarbeitungsfluss und Datenverträge

```text
ETS-Projektsnapshot
  -> normalisierter Beziehungsindex
  -> Gerätekandidaten mit relevanter Nachbarschaft
  -> lokale Regeln / gespeicherte Analyse
  -> AI nur für offene semantische Entscheidungen
  -> validierte Gerätezugehörigkeit und Rollenbindungen
  -> deterministische Entity-Konfiguration
  -> Abgleich mit aktuellem HA-Bestand
  -> fachliche und technische Validierung
  -> bestehender Review- und Apply-Ablauf
```

Die folgenden Datenverträge sind logische Grenzen, keine Vorgabe für neue Dienste, Klassenhierarchien oder je eine Datei pro Schritt.

| Ergebnis | Mindestinhalt | Verantwortlichkeit |
| --- | --- | --- |
| Beziehungsindex | Referenzen, GA-Objekt-Verknüpfungen, Kanal/Modul, Funktionen, Räume, GA-Pfade | lokal; aus einem unveränderlichen Projektsnapshot |
| Kandidat | vorläufige ID, Anker, Mitglieder, Nachbarevidenz, Widersprüche, Scope | lokal; noch keine endgültige Geräteidentität |
| Semantische Analyse | Zuordnung, Rollenbindungen, Evidenzreferenzen, offene Punkte, Abdeckungsstatus | Regeln, gespeichertes Ergebnis oder AI; immer lokal validiert |
| Endpoint | persistente logische ID, bestätigte Evidenz, Zuordnungsrevision | lokal; unabhängig von Batch und Modell |
| Entity-Vorschlag | Plattform, validierte Konfiguration, Endpoint, Provenienz | lokaler Compiler oder begrenzter Spezialfallpfad |
| Review-Vorschlag | Create/Update, aktuelle HA-Ziele, Konflikte, Benutzeränderungen | bestehender Suggestions-Ablauf |

### Normalisieren und Beziehungen indexieren

Das ETS-Projekt wird einmal pro Snapshot indexiert. Normale Dictionaries und Mengen reichen zunächst. Indizes verbinden GA mit Kommunikationsobjekten, Objekte mit Kanal/Modul und physischem Gerät, Funktionen mit GAs und Räumen sowie GAs mit ihren Hierarchiepfaden. Vollständige Originaldaten bleiben lokal verfügbar.

Mehrfachdarstellungen werden aufgelöst, aber bedeutungstragende Objekttexte, Richtungs-/Flaginformationen und Verknüpfungen bleiben erhalten. Objekte ohne GA erzeugen keine adressierbare HA-Funktion, können aber relevante Nachbarevidenz liefern. Sie werden nicht allein wegen fehlender GA aus der Evidenzbasis gelöscht.

Der ausgewählte Scope bestimmt, für welche Endpoints Vorschläge entstehen dürfen. Notwendige Evidenz außerhalb des Scopes darf als solche gekennzeichnet beigefügt werden; sie erweitert nicht automatisch die Menge anzulegender Geräte. Eine unsichere Scope-Zuordnung bleibt ungeklärt.

### Kandidaten und Grenzen

Explizite ETS-Funktionen bilden bevorzugte Anker. Kanal-/Modulzugehörigkeit, funktionale Blöcke, GA-Hierarchie und Namen liefern weitere Kandidaten. Widersprüchliche Anker bleiben sichtbar. Die Methode verwendet keine uneingeschränkte transitive Vereinigung aller verbundenen Knoten: Eine Zentraladresse darf kein ganzes Gebäude zu einem Cluster verbinden.

Ein Kandidat enthält einen Kern sowie relevante Nachbarobjekte und Hinweise auf konkurrierende Kandidaten. Die Nachbarschaft folgt typisierten Beziehungen, nicht pauschal allen Objekten eines Aktors. Bei großen oder mehrdeutigen Kanälen werden geteilte Funktionen explizit dargestellt und konkurrierende Abgrenzungen zur Klärung mitgegeben.

AI darf eine Zuordnung bestätigen, Kandidaten zusammenführen/aufteilen oder fehlenden Kontext benennen. Diese Vorschläge sind referenzgebunden und werden lokal geprüft. Ein Context-Request wird ausschließlich aus vorhandenen Projektindizes bedient; es gibt keine offene Recherche oder freie Toolausführung.

Kandidaten mit noch offener gemeinsamer Gerätezugehörigkeit werden gemeinsam geklärt oder als zusammenhängender offener Fall gehalten. Bevor endgültige Device-Vorschläge veröffentlicht werden, prüft der Backend-Abgleich bestätigte Anker und geteilte Referenzen auch über Paketgrenzen hinweg. Unabhängige, bereits bestätigte Geräte können währenddessen wie bisher im Review erscheinen.

Passt ein unteilbarer Kandidat nicht in das Budget, wird er nicht abgeschnitten. Zunächst werden redundante Darstellungen reduziert. Reicht das nicht, wird der Fall als zu groß zur gezielten Klärung/Review ausgewiesen. Technische Unterabfragen müssen dieselbe lokale Endpoint-Zuordnung verwenden und dürfen keine unabhängigen Geräte erzeugen.

### Regeln und AI-Aufgabe

Der erste deterministische Pfad nutzt explizite, unterstützte ETS-Funktionstypen und Rollen wie `SwitchOnOff`/`InfoOnOff`, sofern DPTs und Verbindungen dazu passen und keine konkurrierende Zuordnung besteht. Unbekannte Rollen bleiben im Restpfad. Regeln müssen ihre verwendete Evidenz benennen; DPT- oder Namensheuristiken allein werden nicht als sichere Entscheidung behandelt.

Die AI bearbeitet nur offene Zugehörigkeit und Rollen. Geräteerkennung und Rollenklärung erfolgen normalerweise gemeinsam. Die strukturierte Ausgabe enthält:

- referenzierte Kandidaten und vorgeschlagene Mitgliedschaft;
- Rollenbindungen an vorhandene GA-/Objektreferenzen;
- funktionalen Raumbezug mit Evidenz, falls erschließbar;
- geteilte Beziehungen, Widersprüche und fehlenden Kontext;
- einen Abdeckungsstatus für jede relevante Eingabe.

Die zulässigen Status sind zugeordnet, geteilt, nicht unterstützt, nicht relevant mit Grund oder ungeklärt. Der Backend-Abgleich ergänzt fehlende Einträge als ungeklärt; eine unvollständige Antwort gilt nicht als vollständig analysiertes Gerät. Selbstberichtete Confidence ist keine Freigabebedingung.

Feste Instructions beschreiben diese Aufgabe und Invarianten. Variable Kandidaten und offene Fragen folgen danach. ETS-Texte sind untrusted Daten, keine Anweisungen. Die AI erfindet keine Referenzen, Konfigurationsschlüssel oder Steuerwerte. Lange freie Begründungen werden nur für Konflikte benötigt.

### Endpoint-Identität und Zusammenführung

Eine kanonische lokale Endpoint-ID wird nach bestätigter Zuordnung vergeben. Bei einer eindeutigen ETS-Funktion wird sie aus einer projektlokalen Namespace-ID und der Funktionsreferenz abgeleitet. Für erschlossene Geräte wird eine ID persistiert und mit bestätigten Ankern und einer Zuordnungsrevision verknüpft. Die ID wird nicht aus dem gesamten jeweils aktuellen GA-Satz berechnet.

Eine neue Zusatzfunktion ändert die Identität nicht, sofern die Anker eindeutig fortbestehen. Bei fehlenden/ersetzten Ankern, Split oder Merge wird keine bestehende HA-Zuordnung geraten: Der Fall benötigt Review. Namensgleichheit allein genügt nicht. Kollisionen widersprüchlicher Anker werden vor der Konfigurationserzeugung ausgewiesen.

Endpoint-Identität, lauflokale Vorschlags-ID und bestehende HA-Device-ID bleiben getrennt. Der Abgleich persistiert die bestätigte Beziehung zum HA-Gerät. Bestehende Drafts ohne diesen Herkunftsnachweis bleiben reviewbar; ihre Geräte werden nicht rückwirkend automatisch verschmolzen. Eine vom Benutzer vorgenommene Entity-Verschiebung bleibt eine explizite Zielkorrektur und wird nicht ungeprüft zur globalen ETS-Regel.

### Konfiguration und HA-Abgleich

Bestätigte Rollenbindungen werden anhand kleiner unterstützter Abbildungen in die bestehenden KNX-Konfigurationsstrukturen übersetzt. Die autoritativen Entity-Store-Schemas und Validatoren bleiben maßgeblich. Es entsteht kein zweites vollständiges HA-Schemasystem.

Zunächst wird der Compiler auf tatsächlich abgesicherte Rollen-/Plattformkombinationen begrenzt. Für andere bislang unterstützte Fälle darf die AI mit einem gezielten Plattformvertrag Konfiguration vorschlagen. Auch dieser Pfad arbeitet innerhalb einer bestätigten oder sichtbar ungeklärten Gerätezuordnung und verwendet die vorhandene Validierung. Nicht unterstützte Spezialfunktionen verschwinden nicht aus der Abdeckungsanzeige.

Create/Update, HA-Raumziele und Überschneidungen werden nach der ETS-Analyse gegen den aktuellen Bestand entschieden. Im Update-Modus werden bestehende Einstellungen erhalten; vorgeschlagene Änderungen sind explizite Unterschiede, kein ungeprüftes Ersetzen durch generierte Defaults. Ist der Abgleich selbst mehrdeutig, wird er zur Review-Frage. YAML bleibt schreibgeschützt.

Beispiel Deckenlampe: Schalten, Rückmeldung und unterstützte Helligkeitsrollen bilden eine `light`-Entity. Kanalspezifische Sperre oder Diagnose können zusätzliche Entities desselben logischen Geräts ergeben. Globale Aktordiagnose wird getrennt dem physischen Aktor zugeordnet. Eine relative Dimmfunktion ohne passende Plattformabbildung bleibt als nicht unterstützt sichtbar; sie wird nicht als absolute Helligkeit umgedeutet.

## Kontextauswahl

| Daten | Behandlung |
| --- | --- |
| DPT, Funktionstyp/-rolle, Objekttexte, Flags | für relevante Objekte erhalten; Widersprüche sichtbar machen |
| Kanal, Modul, funktionale Blöcke | zur Abgrenzung und Wiederverwendung erhalten |
| GA-Hierarchie und funktionaler Raumpfad | relevant ergänzen; Einbauort getrennt halten |
| Namen, Beschreibungen, Kommentare | bedeutungstragende Inhalte erhalten; keine pauschale Abschneidung |
| Hersteller/Applikation/Hardware | einmal je relevantem Gerät/Muster, nur bei Interpretationsbedarf |
| Projektversionen, GUID, Download-/Ladestatus, Seriennummer | lokal für Verwaltung/Provenienz; normalerweise kein AI-Kontext |
| Referenzlisten | einmal kompakt darstellen; Kurzreferenzen serverseitig auf Originale abbilden |
| HA-Floors/Areas | lokal abgleichen; nur relevante Mehrdeutigkeiten separat behandeln |
| HA-Entities | kein Pflichtkontext der ETS-Semantik; nur nötige Daten im Abgleich/Spezialfallpfad |
| Plattformverträge und Beispiele | im normalen semantischen Pfad weglassen; Spezialfall auf relevante Plattform begrenzen |

## Wiederverwendung und Cache-Gültigkeit

Berechnungs-Cache, semantischer Ergebnis-Cache und Provider-Prompt-Cache sind getrennt. Nur ein wiederverwendetes semantisches Ergebnis vermeidet den gesamten AI-Aufruf samt Ausgabe.

Der lokale Ergebnis-Cache ist auf die KNX-Integration und Projektidentität begrenzt. Ein Eintrag enthält die validierte semantische Analyse, Provenienz und Abhängigkeiten. Der Schlüssel umfasst den kanonischen relevanten Kontext einschließlich Nachbarevidenz und Scope-Grenzen, Analyse-/Regel-/Promptversion sowie das tatsächlich konfigurierte Modellprofil. Die Entity-ID des Providers allein genügt nicht, da sich seine Konfiguration ändern kann.

Änderungen am relevanten Kontext machen den Eintrag ungültig. Die Kandidatenbildung wird bei einem neuen Snapshot erneut ausgeführt, damit auch neu hinzugekommene Nachbarn berücksichtigt werden, die vorher noch nicht in einer Abhängigkeitsliste standen. Änderungen an einer unabhängigen Stelle des Projekts sollen unveränderte Kandidaten nicht entwerten.

Die semantische Analyse enthält keine veralteten HA-Zielentscheidungen. Aktueller HA-Bestand, Compiler-/Schema-Version und Benutzerkorrekturen werden bei jeder Wiederverwendung erneut berücksichtigt. Falls ein Spezialfall-AI-Ergebnis HA-Konfiguration voraussetzt, gehört dieser konkrete Kontext zusätzlich zu dessen Cache-Abhängigkeiten.

Gültige Teilentscheidungen können erhalten bleiben, während offene Punkte erneut betrachtet werden. Ein unvollständiges Ergebnis darf keinen vollständigen Cache-Treffer vortäuschen. Technische Providerfehler werden nicht als fachliches negatives Ergebnis gecacht. Gleichartige Herstellerkanäle werden vorerst nicht projektübergreifend automatisch übertragen; eine spätere Musterwiederverwendung benötigt abgesicherte Struktur-/Applikationsgleichheit.

Cache-Einträge nutzen versionierte lokale Speicherung. Pro Kandidat und Analyseprofil wird nur die aktuelle wiederverwendbare Revision vorgehalten; ersetzte Revisionen werden entfernt. Nicht mehr benötigte Cache-Einträge dürfen unabhängig von gespeicherten Review-Läufen verworfen werden. Verlust des Caches darf keine Review-Daten oder angewendete Konfiguration verlieren und löst ohne expliziten Analyse-/Resume-Auftrag keine AI-Aufrufe aus.

## AI Task, Provider-Caching und Modelle

Home Assistant AI Task bleibt der Aufrufweg. Ein günstiges Standardprofil bearbeitet offene Semantik; optional wird ein stärkeres Profil für schwierige Fälle gewählt. Der Start zeigt die verwendeten AI-Task-Entities. Es erfolgt kein stiller Wechsel zu einem nicht ausgewählten Anbieter. Ein eigener KNX-API-Client oder eine externe Batch-Integration ist für diesen Entwurf nicht nötig.

Die lokale öffentliche `async_generate_data`-Schnittstelle bietet Instructions und strukturierte Ausgabe, aber keine direkte Systemprompt-, Modell-, Cache- oder Conversation-Fortsetzungsoption. Modellprofile werden über konfigurierte AI-Task-Entities gewählt. Für die einfache Voreinstellung reicht eine Entity; ein zweites Modell ist optional.

Der lokale OpenAI-Adapter setzt bereits einen Cache-Key und modellabhängige Retention-Parameter. Der gemeinsame Chat-Log-Aufbau fügt jedoch die Uhrzeit sekundengenau vor dem Tasktext ein. Ändert sie sich, ist der folgende Tasktext kein identisches wiederverwendbares Präfix. Deshalb wird Provider-Caching nicht als garantierte Einsparung eingeplant. Eine Korrektur dieses gemeinsamen Verhaltens ist ein separat abzugrenzender AI-Task-Beitrag, keine Voraussetzung dieser KNX-Architektur.

Stabile Instructions und Schemas werden konsistent angeordnet. Sie werden nicht künstlich verlängert, um Cache-Schwellen zu erreichen. Provider-Cache-Treffer und gegebenenfalls Cache-Schreibkosten werden nur dann ausgewiesen, wenn entsprechende Abrechnungsdaten verfügbar sind.

Konkrete Modelle werden anhand des KNX-Vergleichskorpus gewählt, nicht allein nach Tokenpreis. Ein Nano-Klassifikator vor jedem Aufruf ist nicht vorgesehen. Kleineres Modell, Ausgabeumfang und Reasoning müssen gemeinsam einschließlich Nachfragen und Fehlversuchen bewertet werden. Unnötige Websuche und Code-Ausführung gehören nicht zum Importprofil.

## Batching, Budgets und Wiederaufnahme

Mehrere vollständige Kandidaten teilen sich einen Aufruf. Die Auswahl berücksichtigt das Inputbudget und eine konservative erwartete Ausgabegröße anhand der Rollenbindungen. Keine feste GA-Anzahl definiert mehr ein Gerät oder ein Paket. Größenwerte aus Bytes bleiben ausdrücklich Schätzungen, solange kein passender Tokenzähler oder Providerwert verfügbar ist.

Der erste Scheduler arbeitet sequenziell, jedoch ohne die allgemeine 30-Sekunden-Pause. Ein für das gewählte Providerprofil bekanntes Request-/Tokenlimit wird durch Pacing eingehalten; ohne bekanntes Limit wird mit einem laufenden Aufruf und reaktivem Backoff gearbeitet. `Retry-After` bleibt maßgeblich. Die vorhandene Obergrenze von drei Rate-Limit-Wiederholungen bleibt erhalten; erschöpfte Quoten sind kein Anlass für automatische Endloswiederholungen. Begrenzte Parallelität ist erst nach Messung erforderlich.

Pro ungelöstem Kandidaten ist neben der ersten Analyse höchstens eine zusätzliche gezielte Klärung mit erweitertem Kontext oder stärkerem ausgewähltem Profil vorgesehen. Danach bleibt der Fall reviewpflichtig. Validierte Ergebnisse anderer Kandidaten werden dabei nicht erneut generiert. Zusätzliche Versuche benötigen einen expliziten neuen Auftrag. Transport-/Rate-Limit-Retries sind von diesen semantischen Klärungsversuchen getrennt und werden in der Kostenbilanz mitgezählt.

Outputlimits werden für das konkrete Paket berücksichtigt. Ein unbekanntes Providerlimit ist kein Beweis für Unfähigkeit und darf nicht pauschal alle solche AI Tasks sperren. Konservative Pakete und sichtbare Abbruch-/Unvollständigkeitsbehandlung ersetzen die starre Pflicht, dass jeder Provider mindestens 8.192 Tokens als Property exponiert. Ein Outputlimit ist kein vorhergesagter Verbrauch.

Checkpointing bleibt lokal und dauerhaft. Fertige Kandidatenanalysen und Vorschläge werden wiederverwendet. Bei Abbruch werden keine neuen Aufrufe gestartet; laufende Ergebnisse werden nach bestehender Stop-Semantik behandelt. Ein Prozessabsturz zwischen Providerantwort und lokalem Checkpoint kann ohne Provider-Idempotenz weiterhin zu einem erneuten bezahlten Aufruf führen; genau-einmal-Abrechnung wird nicht versprochen.

## Validierung und Fehlerbehandlung

Referenzen müssen existieren und zum bereitgestellten Kontext passen. Eine GA darf nur für kompatible Rollen verwendet werden; gemeinsame Nutzung muss ausdrücklich als solche zulässig sein. Kandidatenübergreifende Verschmelzungen benötigen Evidenz und dürfen keine widersprüchlichen Funktionsanker verdecken. Steuerwerte, Polarität und Payloads werden nicht aus vagen Namen geraten.

Die technische Validierung nutzt weiterhin KNX-Schemas, DPT-/Payloadprüfungen und Eigentumsgrenzen. Eine strukturell gültige Antwort ist noch kein semantisch korrektes Ergebnis. Fehlende Funktionen und widersprüchliche Beziehungen führen zu sichtbaren offenen Punkten, nicht zu einer vollständigen Erfolgsmarkierung.

Schema-/Compileränderungen erzwingen erneute Konfigurationsvalidierung. Änderungen an Projekt, HA-Bestand oder Zielgeräten werden vor Apply erneut geprüft. Benutzeränderungen und bereits erfolgreiche Apply-Aktionen behalten ihren Vorrang. Die bestehende nicht-transaktionale, nachvollziehbare Wiederaufnahme bleibt erhalten.

## Qualitäts- und Kostenvergleich

Der unveränderte Draft ist die Baseline. Alle Varianten werden auf demselben fachlich annotierten Korpus bei denselben Modi und Scopes verglichen. Reale Projekte benötigen fachlich bestätigte erwartete Gerätegrenzen, Rollen und sinnvolle Ausnahmen. Die drei kleinen Fixtures belegen Overhead, ersetzen diesen Korpus aber nicht.

Verglichen werden Gerätezuordnung einschließlich falscher Merges/Splits, Vollständigkeit unterstützter Funktionen, Rollen-/DPT-Korrektheit, gültige Konfigurationen, manuelle Korrekturen, ungeklärte Fälle und tatsächliche Abdeckung. Ein System, das schwierige Funktionen weglässt oder mehr Arbeit an den Nutzer abgibt, darf dadurch keine scheinbare Qualitätsverbesserung erzielen.

Die Freigabe verlangt keine Regression gegenüber der geprüften Baseline bei Zuordnung, Vollständigkeit und Korrekturaufwand. Falsche Steueradressen oder gefährliche Rollenverwechslungen sind unabhängig vom Gesamtscore Ausschlussgründe. Bekannte Baseline-Fehler dienen nicht als zulässige Zielqualität. Stochastische Ergebnisse werden mit mehreren Durchläufen bewertet; Ergebnisse verschiedener Datenqualitätsklassen werden getrennt berichtet.

Die Kostenbilanz erfasst alle Aufrufe einschließlich Fehlversuchen, Klärungen, tatsächlichem Input/Output und, soweit verfügbar, Reasoning-, Cache-Lese-/Schreibanteilen. Fehlende Abrechnungswerte werden als unbekannt statt als null ausgewiesen. Zusätzlich werden lokale Vorbereitung, Providerdauer, Wartezeit und gesamte Laufzeit gemessen. Erst daraus werden Einsparungen abgeleitet; ein Prozentversprechen ist nicht Teil dieses Entwurfs.

Pflichtszenarien für die spätere Umsetzung:

- Vollständige Lampe mit Schalten, Status, Helligkeit, Sperre und Diagnose über mehrere GA-Bereiche.
- Mehrere Verbraucher an einem Aktor; gemeinsame Zentraladresse verschmilzt sie nicht.
- Kanalspezifische und globale Aktordiagnose werden unterschiedlich zugeordnet.
- Fehlende ETS-Funktionen, generische GA-Namen und aussagekräftige Hierarchiepfade.
- Widersprüchliche DPT-/Namens-/Flag-Evidenz sowie unadressierte Nachbarobjekte.
- Unvollständige und abgeschnittene AI-Antworten werden nicht als vollständige Abdeckung akzeptiert.
- Unterschiedliche Paketgrößen ändern bestätigte Endpoint-Identitäten nicht.
- Unveränderte Wiederholung benötigt für gültige Cache-Treffer keinen AI-Aufruf.
- Neue relevante Nachbarevidenz invalidiert eine Analyse; unabhängige Änderungen tun dies nicht.
- Ergänzte Diagnosefunktion erhält die bestehende Endpoint-ID; mehrdeutige Splits/Merges benötigen Review.
- Scope-übergreifende Evidenz erzeugt keine unbestellten Geräte außerhalb des ausgewählten Scopes.
- HA-Raumänderung, Benutzerkorrektur, YAML-Overlap und bestehendes Update-Ziel bleiben korrekt.
- Stop, Restart und Resume behalten bestätigte Arbeit und lösen keine stillen Folgeaufrufe aus.

## Umsetzungsschnitt und ausgeschlossene Erweiterungen

Der erste zusammenhängende Umsetzungsschnitt liegt in KNX Core: Index, Kandidaten, semantische Ausgabe, lokale Abbildung unterstützter Rollen, Identität und Ergebniswiederverwendung. Vorhandene Suggestions-Modelle, Validatoren, Review- und Apply-Pfade werden soweit tragfähig weiterverwendet. Frontend-Änderungen betreffen offene Funktionen, Herkunft der Entscheidung, aktualisierte Schätzung und optionales Eskalationsprofil.

Parseränderungen sind nur bei nachgewiesen fehlenden Quelldaten nötig. Die unabhängige Home-Assistant-Frontend-Kopie und der KNX-Frontend-Submodule-Stand werden nicht berührt. Gemeinsame AI-Task-Verbesserungen zu Zeitkontext oder Usage sind getrennt zu planen und zu prüfen.

Nicht Bestandteil sind Graphdatenbank, Vektorindex, Fine-Tuning, autonome Agentenschleifen, obligatorische Mehrmodellketten, projektübergreifender semantischer Ähnlichkeitscache, direkte Provider-Batch-API und automatische Übernahme von Herstellerprofilen. Diese Erweiterungen brauchen erst einen gemessenen Nutzen.

## Quellen und nächster Prüfschritt

Die Codebefunde beziehen sich auf den oben genannten lokalen Draft und insbesondere `suggestions/context.py`, `generation.py`, `matching.py`, `store.py`, `apply.py`, `ai_task/task.py`, `ai_task/entity.py`, `conversation/chat_log.py` und `openai_conversation/entity.py` in Home Assistant Core sowie die xknxproject-Modelle und Test-Fixtures.

- [Home Assistant AI Task entity](https://developers.home-assistant.io/docs/core/entity/ai-task/): dokumentierte Aufgaben- und Ausgabeschnittstelle.
- [OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching): Präfixgleichheit, modellabhängige Cache-Bedingungen und Abrechnung.
- [OpenAI Batch API](https://developers.openai.com/api/docs/guides/batch): separate asynchrone Verarbeitung; nicht der hier gewählte Standardpfad.

Nach Prüfung und Zustimmung zu diesem schriftlichen Entwurf folgt ein Implementierungsplan. Ausführungsart und Produktänderungen werden erst im Anschluss an dessen Prüfung festgelegt.
