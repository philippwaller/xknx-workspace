# KNX-Vorschläge: Umsetzung und Prüfung

Die freigegebene Gestaltung ist auf `codex/knx-suggestions-ux` im separaten Frontend-Worktree `knx-frontend/.worktrees/suggestions-ux` umgesetzt. Das [Konzept](2026-09-25-knx-suggestions-ux-design.md) beschreibt Informationsarchitektur, Zustände und Interaktionen.

## Ergebnis

Kompakte Bereichs- und Gerätezeilen mit aufklappbaren Entitäten; Suche, Filter und stabile Auswahl; direkte Dialoge für Konfiguration, Zuordnung und Gerät/Bereich; Jobs im Kopfmenü mit eigenem Fortschrittsdialog; aktiver Job als kompakter Banner; Anwendungsvorschau mit bestätigungspflichtigen Aktionen und getrenntem Ergebnisprotokoll. Verwendet bestehende HA-Komponenten, Theme-Variablen und vorhandene Dienste. Keine neuen Abhängigkeiten oder Backend-Verträge.

## Automatische Prüfung

- 32 Testdateien, 372 Tests bestanden. Die gesamte Frontend-Suite läuft im isolierten Checkout, mit Ausschluss verschachtelter Worktrees.
- ESLint ohne Fehler; Prettier vollständig bestanden; `git diff --check` sauber.
- TypeScript: ausschließlich der bereits im ursprünglichen Checkout reproduzierte Fehler `HuiViewBackgroundEditor._config` im gepinnten HA-Frontend (Zeile 168). Der Gesamtcheck ist deshalb nicht grün.
- Lit-Analyse: 0 Fehler, 35 bereits im Ausgangsstand vorhandene Warnungen.
- Die installierten Prüfwerkzeuge wurden direkt aufgerufen: Yarns Installationsstatus verweist auf den ursprünglichen Checkout. Gleiche Programme und Konfiguration, keine Neuinstallation. Node benötigt für die Tests `--localstorage-file`.

## Review und Korrekturen

Ein unabhängiger Review wurde durchgeführt. Die wichtigen Befunde wurden in einem Korrekturdurchlauf mit reproduzierenden Regressionstests behoben: unfertige Bereichsentwürfe beim Speichern des übergeordneten Dialogs; Verlust fremder Konfigurationsänderungen beim Übernehmen einer neueren Revision; Absturz bei zwischenzeitlich entfernter Entität.

Zwei zunächst kleinere Befunde wurden wegen ihrer Wirkung auf den Prüfablauf höher gewichtet und behoben: Ein Auswahlklick darf wartende neue Ergebnisse nicht ungefragt in die Liste aufnehmen; der Entitätsstatus muss auch mobil sichtbar bleiben.

Bekannte kleine Einschränkung: Verschwindet eine komplette Bereichsgruppe durch einen Filter und wird später wieder angezeigt, öffnet sie wieder im Standardzustand. Der Aufklappzustand einzelner Geräte bleibt erhalten.

Beim visuellen Prüfen korrigiert: dauerhaft erreichbare Auswahlleiste, lesbare Jobtitel statt Scope-IDs, begrenzte Adresszusammenfassung und Entitätsnamen aus der verbindlichen Anwendungsvorschau statt interner Vorschlags-IDs.

## Ausführungsentscheidungen

Das Frontend ist ein eigenständiges Repository. Der Worktree wurde deshalb dort angelegt; der native App-Worktree hätte das Workspace-Root verwendet. Gepinntes HA-Frontend lokal kopiert und bestehende Abhängigkeiten wiederverwendet; kein Submodul-Upgrade. Liste und Editorumzug wurden gemeinsam umgesetzt, damit direkte Aktionen jederzeit funktionierende Dialoge öffnen. Portale erhalten bestätigte Schreib-Callbacks vom View.

Das Protokoll zeigt vorhandene bestätigte Aktionen, Fehler und begrenzte Ergebnisse. Ereigniszeitpunkte und vollständige Phasenhistorie sind im heutigen Backend nicht vorhanden und werden nicht erfunden.

Keine echten Jobs gestartet, gestoppt oder verworfen; keine Vorschläge angewendet. Live-Prüfungen beschränken sich auf Anzeige, Auf-/Zuklappen, Dialoge und serverseitige Vorschau. Laufende Generierung, Verbindungsabbrüche, Konflikte und Teilfehler sind durch Komponententests geprüft, nicht durch einen destruktiven Live-Durchlauf.

Responsive Prüfung: 390×844 (dunkel), 768×1024 und 1280×720 (hell), native Vollbilddialoge mobil und HA-Dialoge auf Desktop. Adresssuche öffnet den passenden Entitätstreffer und weist ausgewählte Entitäten außerhalb des Filters aus. Escape schließt den Editor und gibt den Fokus zurück. Kein vollständiger Screenreader-, Kontrastmessungs- oder 200%-Zoom-Audit.

Eine zusätzliche Browser-Regressionsprüfung zeigte die falsche anfängliche Anzeige nativer Auswahlfelder. Explizite Auswahl an den Optionen korrigiert die bestehende Geräte-, Bereichs- und Etagenzuordnung sowie Kategorie und Filter; zwei Tests reproduzierten den Fehler vor der Korrektur.

Abschluss: Produktionsbuild erfolgreich (letzter Build `entrypoint.38ad0763274181ef.js`). Bestehende Gerätezuordnung zusätzlich im Browser nach dem finalen Build korrekt verifiziert. Temporär ersetzte generierte Laufzeitdateien vollständig aus dem Originalbackup wiederhergestellt und mit `diff -qr` identisch geprüft; temporäre Browser-Overrides zurückgesetzt. Original-Quellcodediff bytegleich zum gesicherten Ausgangsstand. Die neue Oberfläche liegt im separaten Worktree und ist noch nicht dauerhaft in den laufenden Checkout integriert.

Frontend-Commits: `742a5f5`, `efaa2ce`, `2313e51`. Die sechs bereits vorher veränderten Dateien bleiben mit ihren ursprünglichen 72 Ergänzungen und 2 Löschungen uncommitted; sie wurden nicht in diese Commits aufgenommen.
