# Plan: Portfolio Lab — chat-gesteuerter US-Aktien-Screener mit Backtest

**Einzeiliges Ziel:** Der Nutzer beschreibt im Chat Auswahlkriterien für ein Aktienportfolio, die App wählt daraufhin aus echten US-Marktdaten konkrete Titel aus, testet sie historisch und stellt sie dem S&P 500 gegenüber — mit Kennzahlen, deren bekannte Verzerrungen entweder korrigiert oder am Bildschirm benannt sind, weil damit Entscheidungen über echtes Geld vorbereitet werden.

> **An den Ausführenden:** Der Auftraggeber ist kein Berufsentwickler, sondern Finanzstudent. Alles, was er selbst tun muss (Schlüssel eintragen, App starten), gehört als Schritt-für-Schritt-Anleitung in die Ausgabe. Kommunikationssprache Deutsch; Code, Bezeichner und Commits Englisch.
>
> **Leitsatz dieses Plans:** Ein Backtest, der zu gute Zahlen zeigt, ist schlimmer als gar keiner, weil daran Geld hängt. Wo eine Verzerrung nicht beseitigt werden kann, wird sie beziffert und angezeigt — niemals weggelassen.

---

## Classification

**Track:** From scratch. Auf dem Rechner existiert kein Python-Projekt; die einzige `.py`-Datei ist ein Hello-World in `Documents/ClaudeCoding` (verified: `find ~ -maxdepth 4 \( -name pyproject.toml -o -name "*.py" \)` ohne `Library`).
**Zurückgestellte Nebenwünsche:** keine. Vorhandenes Uni-Material in `~/Downloads` und die R-Skripte sind auf ausdrücklichen Wunsch **kein** Input (user).

## Interview Ledger

| # | Frage | Ergebnis |
|---|---|---|
| Q1 | Welches Projekt? | Finance-App in Python (user) |
| Q2 | Kursdatenanalyse oder eigene Finanzen? | verworfen — Nutzer nannte stattdessen den eigentlichen Auftrag: Stock-Picker/Portfolio-Builder, Backtest gegen S&P, mehrere Portfolios (user) |
| Q3 | Regelwerk oder Sprachmodell? | Sprachmodell als Bedienoberfläche; die Beispielnachrichten sind reine Filter, iterativ (user) |
| Q4 | Einfacher oder echter Backtest? | beides, einfacher zuerst (user) |
| Q5 | Datenbudget? | kostenlos starten, Datenschicht austauschbar (user) |
| Q6 | Lernwerkzeug oder echtes Geld? | auch echtes Geld (user) |
| Q7 | Plan annehmen oder ändern? | — offen |

7 Fragen von 14. Der Plan lief vor Auslieferung durch drei Runden mit einem unabhängigen Prüfer, der ihn nicht geschrieben hatte: Runde 1 durchgefallen (12 Blocker), Runde 2 durchgefallen (4 verbliebene Defekte, darunter eine falsche Phasenreihenfolge), Runde 3 bestanden. Alle Befunde sind eingearbeitet; die vier Restschwächen, die der Prüfer nicht als behebbar ansah, stehen unter Open Items.

## Goal & Success Criteria

Fertig ist v1, wenn der Nutzer selbst durchführen kann:

1. App mit einem Terminal-Befehl starten, sie öffnet sich im Browser.
2. Seine ursprüngliche Nachricht wörtlich eingeben — `portfolio aus 10 aktien us markt cap to price max 1/1 und price book max 2/1` — und sehen, welche Filter daraus wurden, im Klartext; unverständliche Teile werden ausdrücklich als unverstanden gemeldet, nicht stillschweigend verworfen.
3. `lass cap nicht über USD 2bn sein` als zweite Nachricht senden und sehen, dass die vorherigen Filter erhalten bleiben und einer hinzukommt.
4. Eine Titelliste mit Ticker, Name, Sektor und jedem Filterwert je Titel sehen, dazu die Sortierregel und die Zahl der wegen fehlender oder veralteter Daten ausgeschlossenen Titel.
5. Ein zweites Portfolio anlegen, wechseln, App schließen und neu starten — beide samt Chatverlauf vorhanden.
6. Einen Backtest starten und eine Grafik gegen den S&P 500 **auf gleicher Renditebasis** sehen, darunter Rendite p. a., Schwankungsbreite, Sharpe, maximaler Rückgang, Abweichung zum Index, Umschlagshäufigkeit — alles nach Handelskosten.
7. Am Bildschirm ablesen, welcher Backtest-Typ läuft, welche Verzerrungen korrigiert sind und welche nicht.
8. Sehen, wie oft diese Spezifikation schon getestet wurde, und einen zurückgehaltenen Zeitraum separat prüfen.

## Current State

- Kein Python-Projekt vorhanden außer einem Hello-World (verified: `find`, s. o.).
- `uv` liegt unter `~/.local/bin/uv`, `python3 --version` meldet **3.13.15** (verified: direkt ausgeführt).
- Kein Homebrew, kein CMake (verified: Notizdatei `dev-environment.md`, 2026-09-18).
- Der Nutzer besitzt einen Anthropic-API-Schlüssel aus einem Vorprojekt (verified: Notizdatei `zaoan-app.md`) `[A2]`.
- Geheimnisse legt der Nutzer selbst in `.env.local` ab und will sie nie im Chat sehen (verified: `secrets-handling.md`).
- SEC EDGAR stellt XBRL-Daten der US-Melder ohne Schlüssel bereit, einschließlich eines nächtlich neu gebauten Gesamtpakets `companyfacts.zip` (verified: sec.gov, EDGAR-API-Seite). **Einschränkung:** XBRL wurde stufenweise eingeführt; frühe Jahre und kleine Melder sind lückenhaft. Der tatsächliche Abdeckungsbeginn wird in Phase 1 **gemessen**, nicht angenommen `[A11]`.
- Zeitpunktgenaue, delisting-bereinigte Daten sind kommerziell verfügbar (Sharadar über Nasdaq Data Link, ab 1998, 16.000+ US-Firmen inkl. Delistings) (verified: Websuche). Ein Preis war nicht ermittelbar.
- Zur Größenordnung der Überlebensverzerrung nennen die Anbieter QuantRocket und Bookmap „zehn Jahre zurück fehlen ~75 % der damals gehandelten Titel" und „5–15 Prozentpunkte p. a. Überzeichnung" (verified: diese Quellen sagen das). `[A14: es sind Anbieterangaben, keine geprüfte Wissenschaft — die Richtung ist unstrittig, die Höhe nicht]`

## Scope (v1)

Lokal auf dem Mac laufende Web-Oberfläche: Chat → Filterspezifikation → Screening über einen lokal gespeicherten US-Aktienbestand → Titelliste → Backtest in zwei Stufen → Vergleich gegen den S&P 500, für beliebig viele parallel gespeicherte Portfolios.

## Out of Scope & Parked Items

- **Orderausführung, Broker-Anbindung, Depotabgleich** — ausgeschlossen. Das Werkzeug analysiert, es handelt nicht.
- **Anlageempfehlungen** — die App bewertet keinen Titel als kaufens- oder verkaufenswert. Sie wendet die Kriterien des Nutzers an und zeigt das Ergebnis samt Unsicherheiten.
- **Nicht-US-Märkte** — „us markt" (user). Zweiter Markt wäre eine zusätzliche Quelle, kein Umbau.
- **Zugriff vom iPhone/iPad** — v1 läuft lokal `[A3]`.
- **Steuern, Währungsumrechnung, Depotgebühren** — nicht in der Backtest-Rechnung. Handelskosten dagegen schon.
- **Intraday, Optionen, Leerverkäufe, Hebel** — nur Long auf Tagesschlusskursen. *Stand 25.09.2026: Hebel, Leerverkäufe und Options-Backtests sind als nächster Ausbau vorgemerkt, siehe „Geplant: Hebel und Derivate“ unten; Optionen gibt es bereits als Analyse (OPTIONS-Reiter).*
- **Bezahlte Datenquelle** — Anschluss wird vorbereitet (Phase 10), nicht abonniert (user, Q5).
- **Vollständige Beseitigung der Überlebensverzerrung** — mit kostenlosen Quellen nicht erreichbar. Wird beziffert und angezeigt, nicht behauptet. Beseitigung ist genau das, was die bezahlte Quelle später liefert.

## Approach

Vier Schichten, in dieser Reihenfolge, jede für sich prüfbar.

**1. Datenschicht — lokaler Bestand, austauschbare Quelle.**
Eine Schnittstelle `DataSource` mit: `universe(asof)`, `fundamentals(asof)`, `prices(tickers, von, bis)`. Drei Umsetzungen:
- `FreeSource` — SEC-XBRL für Bilanzdaten, `yfinance` für Kurse.
- `FixtureSource` — winziger fester Datensatz im Repository. **Existiert ab Phase 1** und dient als Beweis, dass die Schnittstelle wirklich austauschbar ist: die Screener-Tests laufen gegen beide Umsetzungen.
- `PaidSource` — Phase 10, gleiche Schnittstelle.

Alles landet in einer lokalen DuckDB-Datei. Abfragen laufen gegen die lokale Datei, nie gegen das Netz — deshalb kann das Screening schnell sein, obwohl die Quellen langsam sind.

**2. Spezifikationsschicht.** `PortfolioSpec` (Filter, Sortierregel, Anzahl, Gewichtung, Umschichtung, Liquiditätsuntergrenzen, Datenfrische) als validiertes Objekt — die einzige Sprache zwischen Chat und Screener.

**3. Sprachmodell als Übersetzer, nicht als Auswähler.** Das Modell erhält die aktuelle Spezifikation und die neue Nachricht und liefert eine geänderte Spezifikation über ein Werkzeug-Schema mit fest definierten Feldern. **Das Schema enthält kein Feld für Ticker** — das Modell kann keine Aktien nennen, auch wenn es wollte. Ein Kriterium ohne passendes Feld muss ausdrücklich zurückgewiesen werden. Damit ist die Titelauswahl vollständig deterministisch und aus den Rohdaten nachrechenbar.

**4. Backtest, zwei klar getrennte Stufen — mit ehrlichen Namen.**
- **Stufe A „Rückblick"** (Phase 5): die heute gescreenten Titel rückwärts geplottet. Kennt bereits die Überlebenden. Keine Strategierendite.
- **Stufe B „Point-in-Time (Fundamentaldaten)"** (Phasen 7–8) — bewusst *nicht* „echter Backtest" genannt: zu jedem Quartalsstichtag wird mit genau den Bilanzdaten gescreent, die an diesem Tag veröffentlicht waren, über das zu diesem Tag gültige Universum. Beseitigt den Blick in die Zukunft. Beseitigt die Überlebensverzerrung nur so weit, wie die kostenlose Kursquelle verschwundene Titel überhaupt führt — Phase 7 **misst**, wie weit das reicht, und die gemessene Quote steht danach am Bildschirm. Was nicht korrigiert werden kann, wird beziffert, nicht behauptet.

Freiheit des Ausführenden: Modulschnitt unterhalb dieser vier Schichten, Benennung, Testorganisation, Layoutdetails.

## Requirements

| # | Anforderung | Abnahme |
|---|---|---|
| R1 | WENN eine Nachricht eintrifft, DANN bleiben alle bisherigen Filter erhalten, außer die Nachricht widerspricht ihnen. | Beide Beispielnachrichten nacheinander → zweite Spezifikation enthält beide Filter. |
| R2 | Alle aktiven Filter, Sortierregel, Titelzahl, Gewichtung, Liquiditäts- und Frischegrenzen stehen im Klartext am Bildschirm. | Sichtprüfung. |
| R3 | WENN ein Kriterium kein Feld der Spezifikation trifft, DANN wird es ausdrücklich gemeldet und nicht übernommen. | Erfundenes Kriterium → Meldung, Spezifikation unverändert. |
| R4 | Ein Screen liefert Ticker, Name, Sektor und jeden aktiven Filterwert je Titel. | Tabelle vollständig. |
| R5 | Zu wenige Treffer → ausdrücklicher Hinweis statt stillschweigend kürzerer Liste. | Zu enger Filter → Hinweis. |
| R6 | Zu viele Treffer → Sortierung nach der angezeigten Regel; Regel per Nachricht änderbar. | Regel wechseln → andere Liste. |
| R7 | Beliebig viele benannte Portfolios samt Chatverlauf überleben einen Neustart. | Zwei anlegen, neu starten, beide da. |
| R8 | Backtests ziehen Handelskosten ab: Gebühr je Transaktion plus Geld-Brief-Spanne, **gestaffelt nach Marktkapitalisierung**, in den Einstellungen änderbar. | Kosten auf 0 → Ergebnis ändert sich nachweisbar; zwei Größenklassen ergeben unterschiedliche Kosten. |
| R9 | Jeder Backtest weist für Portfolio **und** Index aus: Rendite p. a., Schwankungsbreite p. a., Sharpe, maximaler Rückgang (auf Tagesbasis), Abweichung zum Index, Umschlagshäufigkeit. | Formeln gegen analytisch bekannte Testreihen geprüft (Phase 6). |
| R10 | Grafik zeigt Portfolio und Index auf gemeinsamer Basis 100. | Sichtprüfung. |
| R11 | **Portfolio und Vergleichsindex liegen auf derselben Renditebasis.** Beide inklusive Dividenden. | Test: die 10-Jahres-Rendite p. a. der Indexreihe weicht höchstens **25 Basispunkte** von der veröffentlichten S&P-500-Total-Return-Zahl ab (bei Rückfall auf `SPY`: höchstens **60** Basispunkte, weil Fondskosten und Ausschüttungstiming abweichen — die weitere Toleranz ist dann am Bildschirm zu nennen). |
| R12 | Jede Grafik trägt ein Etikett mit Backtest-Typ, korrigierten und **nicht** korrigierten Verzerrungen. | Sichtprüfung beider Typen. |
| R13 | Stufe B verwendet je Stichtag nur Bilanzdaten mit SEC-Meldedatum ≤ Stichtag; bei mehreren Fassungen derselben Periode gilt die **zuerst** eingereichte. | Automatischer Test: Datensatz mit späterem Meldedatum darf nicht auftauchen; nachträgliche Korrektur darf die ursprüngliche nicht verdrängen. |
| R14 | Ein Titel ist zum Stichtag nur wählbar, wenn seine jüngste passende Meldung höchstens **N Monate** alt ist (Standard 6). | Test mit einem Melder, der vor Jahren aufhörte → nicht wählbar. |
| R15 | Ein Titel ist nur wählbar über einem Mindestkurs und einem Mindest-Handelsvolumen (Standard: 5 USD, 1 Mio. USD mittleres Tagesvolumen über 60 Tage), beides änderbar. | Test mit einem Pennystock → ausgeschlossen und gezählt. |
| R16 | Umschichtung: gescreent wird mit Daten bis Handelsschluss Tag *t*, gehandelt wird zur **Eröffnung des nächsten Handelstags**, Kosten werden dabei abgezogen. | Test auf einem festen Datensatz, in dem Schluss *t* und Eröffnung *t+1* verschieden sind: der verbuchte Ausführungskurs ist gleich der Eröffnung *t+1*; ändert man allein den Schlusskurs *t*, bleibt der Ausführungskurs unverändert (ändert sich aber gegebenenfalls die Auswahl). |
| R16b | **Stufe A ist Kaufen-und-Halten**: einmaliger Kauf zu Beginn, Dividenden reinvestiert, keine Umschichtung. Umschlagshäufigkeit damit definitionsgemäß null nach dem Ersterwerb. | Kennzahlentabelle zeigt für Stufe A Umschlag 0; Kosten fallen nur beim Ersterwerb an. |
| R17 | Dividenden werden zum Ex-Tag in dieselbe Position reinvestiert; Bruchstücke sind erlaubt, damit keine unbeabsichtigte Kassenhaltung entsteht. | Test mit einem Dividendenzahler: Endwert entspricht der Rechnung von Hand. |
| R18 | Verschwindet ein Titel während des Backtests, wird eine ausgewiesene Delisting-Rendite angesetzt (Standard −30 %, änderbar), nicht der letzte bekannte Kurs. | Test mit einem simulierten Delisting. |
| R19 | Der Backtest-Bericht nennt, wie viele Titel im Zeitraum das Universum verlassen und für wie viele davon keine Kurse vorliegen. | Zahlen im Bericht. |
| R20 | Der Bericht zeigt Startdatum ±6 Monate und das Ergebnis ohne den stärksten Einzeltitel. | Beide Varianten sichtbar. |
| R21 | Das System zählt und zeigt, wie oft diese Spezifikation schon auf diesen Daten getestet wurde, und hält standardmäßig die letzten 2 Jahre als Prüfzeitraum zurück, der ausdrücklich freigeschaltet werden muss. | Zähler erhöht sich; Prüfzeitraum ist erst nach Freischaltung im Ergebnis. |
| R22 | Jedes gespeicherte Backtest-Ergebnis trägt eine Kennung des Datenbestands (Aufbauzeitpunkt, Zeilenzahlen, Quellenversion); ein erneuter Lauf auf anderem Bestand wird als solcher gekennzeichnet. | Bestand neu aufbauen, Lauf wiederholen → Hinweis erscheint. |
| R23 | Keine Orderausführung, keine Broker-Bibliothek, keine Empfehlungsformulierung in der Oberfläche. | Abhängigkeitsprüfung plus automatische Prüfung der Oberflächentexte gegen eine Liste verbotener Formulierungen („kaufen", „empfehlen", „solltest du" …). |
| R24 | Geheimnisse nur aus `.env.local`, nie in Oberfläche, Protokoll oder Fehlermeldung. | Absichtlicher Fehlerfall → Schlüssel taucht nicht auf. |

## Key Decisions

| Entscheidung | Wahl | Grundlage |
|---|---|---|
| Sprache/Laufzeit | Python 3.13.15 über `uv` | (user) Python; beides direkt verifiziert |
| Oberfläche | Streamlit, lokal im Browser | `[A3]` — Chat, Tabellen, Grafiken, Mehrfachauswahl ohne eigenes Frontend; ein Startbefehl |
| Lokaler Speicher | DuckDB, zwei Dateien | spaltenorientiert, filtert Tausende Titel in Millisekunden `[A4]` |
| Fundamentaldaten | SEC EDGAR `companyfacts.zip`, lokal indiziert | kostenlos, kein Schlüssel, enthält Meldedaten (Pflicht für R13) und auch verschwundene Melder |
| **Kursdaten** | **`yfinance`**, Tagesdaten; gespeichert werden **bereinigter und unbereinigter** Schlusskurs, Volumen, Dividenden, Splits. Ausweichquelle: **Stooq** über `pandas-datareader` | einzige kostenlose Quelle mit brauchbarer US-Abdeckung; inoffiziell, daher nur private Nutzung und hinter der Schnittstelle gekapselt `[A5]` |
| **Preisbasis der Kennzahlen** | Marktkapitalisierung und Kurs-Buchwert werden aus dem **unbereinigten** Kurs × damalige Aktienzahl gerechnet; Renditen aus dem **bereinigten** Kurs | bereinigte Kurse verfälschen historische Bewertungskennzahlen rückwirkend |
| **Vergleichsindex** | S&P 500 **Total Return** (`^SP500TR`); Ausweich: `SPY` mit reinvestierten Dividenden | (user) S&P; Total Return, weil das Portfolio Dividenden enthält — ein Preisindex erzeugte künstliche Mehrrendite `[A15]` |
| Risikoloser Zins für Sharpe | 3-Monats-US-Schatzwechsel von FRED (`DGS3MO`); ersatzweise ein in den Einstellungen gesetzter fester Wert | ohne definierte Größe wäre Sharpe beliebig `[A16]` |
| Sprachmodell | Anthropic API, Schlüssel aus `.env.local`, Werkzeug-Schema erzwingt Struktur | (user besitzt Schlüssel) `[A2]` |
| Rolle des Modells | ausschließlich Übersetzer Nachricht→Spezifikation | Auswahl muss deterministisch und nachrechenbar sein, weil echtes Geld daran hängt (user, Q6) |
| Gewichtung | gleichgewichtet als Standard, per Nachricht änderbar | einfachste nachvollziehbare Regel `[A6]` |
| Umschichtung | quartalsweise | Bilanzdaten erscheinen quartalsweise `[A7]` |
| Sortierregel | nach der strengsten Bewertungskennzahl der Spezifikation, aufsteigend; angezeigt und änderbar | macht R6 sichtbar statt versteckt `[A8]` |
| Universum | US-Stammaktien an NYSE/Nasdaq/AMEX; Fonds, ETFs, Zweitnotierungen ausgeschlossen | „us markt" (user) `[A9]` |
| Handelskosten | 0,05 % Gebühr plus Spanne **gestaffelt**: ≥10 Mrd 0,05 %, 2–10 Mrd 0,15 %, 0,3–2 Mrd 0,40 %, <0,3 Mrd 1,00 %; alles änderbar | die Filter des Nutzers zielen auf kleine Werte, dort ist die Spanne um ein Vielfaches größer `[A10]` |
| Delisting-Rendite | −30 % als Standard, in den Einstellungen änderbar | Konvention aus der Literatur; in dieser Sitzung nicht nachgeprüft `[A17]` |
| Datenfrische | Meldung höchstens 6 Monate alt | verhindert Zombie-Titel `[A18]` |
| Geheimnisse | `.env.local`, nie ausgegeben | (verified: `secrets-handling.md`) |
| Versionsverwaltung | `git init`; `.env.local` und `data/` ausgeschlossen | ein Datenbestand gehört nie ins Repository |

## Data & State Changes

**`data/market.duckdb`** — jederzeit aus den Quellen reproduzierbar, darf gelöscht werden:
- `securities` — Ticker, CIK, Name, Sektor, Börse, **erster und letzter beobachteter Handelstag**, Status.
- `ticker_history` — CIK, Ticker, gültig-ab, gültig-bis. Wird aus den beobachteten Kurszeiträumen und der SEC-Einreichungshistorie aufgebaut, **nicht** aus der heutigen Ticker-Liste. Grund: SECs aktuelle Zuordnungsdatei kennt verschwundene Firmen nicht; wer sie als Universum benutzt, screent die Vergangenheit aus der Sicht der Überlebenden.
- `fundamentals` — CIK, Kennzahl, Wert, Periode, **Meldedatum**, Einreichungsnummer. Auswahlregel: für Periode P am Stichtag t gilt die Fassung mit dem **frühesten** Meldedatum ≤ t.
- `prices` — Ticker, Datum, Schluss bereinigt, Schluss unbereinigt, Volumen, Dividende, Splitfaktor.
- `shares_outstanding` — CIK, Datum, Anzahl, Herkunftsmerkmal. Reihenfolge der XBRL-Merkmale wird in Phase 1 festgelegt und dokumentiert `[A19]`.
- `data_snapshot` — Aufbauzeitpunkt, Zeilenzahlen je Tabelle, Quellenversionen. Wird an jeden Backtest-Lauf gehängt (R22).

**`data/user.duckdb`** — **nicht** reproduzierbar, wird beim Neuaufbau der Marktdaten nicht angefasst:
- `portfolios`, `messages`, `backtest_runs` (inkl. Snapshot-Kennung und Zählerstand für R21).

Migrationen: nummerierte SQL-Dateien, beim Start der Reihe nach angewandt.

## Kennzahlen-Vokabular v1

Dies ist die vollständige Liste der Felder, die `PortfolioSpec` filtern und sortieren kann. Was hier nicht steht, wird nach R3 ausdrücklich zurückgewiesen. Erweiterung ist je ein Eintrag, kein Umbau.

| Feld | Herleitung | Quelle / XBRL-Merkmal `[A20]` |
|---|---|---|
| Marktkapitalisierung | unbereinigter Kurs × Aktienzahl zum Stichtag | `prices` × `shares_outstanding` |
| Aktienzahl | Reihenfolge: Deckblattangabe, sonst ausstehende Stammaktien, sonst gewichteter Durchschnitt verwässert | `dei:EntityCommonStockSharesOutstanding` → `us-gaap:CommonStockSharesOutstanding` → `us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding` |
| Eigenkapital (Buchwert) | wie gemeldet | `us-gaap:StockholdersEquity` → ersatzweise die Fassung einschließlich Minderheitenanteilen |
| Kurs-Buchwert-Verhältnis | Marktkapitalisierung ÷ Eigenkapital; bei Eigenkapital ≤ 0 nicht definiert | abgeleitet |
| Umsatz (4 Quartale) | Summe der letzten vier gemeldeten Quartale | `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax` → `us-gaap:Revenues` |
| Kurs-Umsatz-Verhältnis | Marktkapitalisierung ÷ Umsatz | abgeleitet |
| Operativer Cashflow (4 Quartale) | Summe der letzten vier Quartale | `us-gaap:NetCashProvidedByUsedInOperatingActivities` |
| Kurs-Cashflow-Verhältnis | Marktkapitalisierung ÷ operativer Cashflow | abgeleitet |
| Gewinn (4 Quartale) | Summe der letzten vier Quartale | `us-gaap:NetIncomeLoss` |
| Kurs-Gewinn-Verhältnis | Marktkapitalisierung ÷ Gewinn; bei Verlust nicht definiert | abgeleitet |
| Sektor / Branche | Branchenschlüssel des Melders, im SEC-Wortlaut (389 Werte) | SIC-Code aus der EDGAR-Einreichungsdatei |
| Branchengruppe | 13 Gruppen, aus dem SIC-Code abgeleitet (`data/industry.py`); der SEC-Wortlaut bleibt daneben sichtbar | abgeleitet |
| Börse | Notierungsort | Universumsaufbau |
| Kurs | letzter unbereinigter Schlusskurs | `prices` |
| Mittleres Tagesvolumen (60 Tage, in USD) | Median aus Kurs × Stückvolumen | `prices` |
| Datenalter | Stichtag minus jüngstes passendes Meldedatum | `fundamentals` |

`[A20]`: Die genannten XBRL-Merkmale stammen aus Erfahrungswissen, **nicht** aus einer Prüfung in dieser Sitzung. Umsatz und Cashflow sind je nach Melder unterschiedlich gekennzeichnet. **Phase 1 prüft jedes Merkmal an echten Einreichungen nach und schreibt die tatsächlich gefundene Reihenfolge fest**; ein Feld, dessen Abdeckung unter 80 % des Universums liegt, wird entweder mit einer Ersatzkennzeichnung ergänzt oder mit dem Abdeckungsgrad versehen angezeigt.

## Interfaces, Integrations & Credentials

Nach außen bietet die App nichts an.

| Dienst | Zweck | Zugang | Bekannte Schwäche |
|---|---|---|---|
| SEC EDGAR | Bilanzdaten inkl. Meldedatum | kein Schlüssel; aussagekräftige `User-Agent`-Kennung mit Kontaktadresse verlangt, Abrufrate begrenzt | Gesamtpaket einmal laden statt vieler Einzelabrufe `[A11]` |
| `yfinance` (Yahoo) | Tageskurse, Dividenden, Splits, Indexreihe | kein Schlüssel | inoffiziell, kann ausfallen; **führt verschwundene Titel praktisch nicht** — Kern der verbleibenden Verzerrung `[A5]` |
| FRED | risikoloser Zins | kein Schlüssel für die CSV-Reihe `[A16]` | — |
| Anthropic API | Nachricht → Spezifikation | `${ANTHROPIC_API_KEY}` aus `.env.local` | Ausfall: Filter bleiben von Hand bedienbar |
| *später* Sharadar / Nasdaq Data Link | zeitpunktgenau, delisting-bereinigt | `${NASDAQ_DATA_LINK_API_KEY}` | erst nach ausdrücklicher Entscheidung |

Der Nutzer trägt Schlüssel selbst ein; der Ausführende gibt ihm einen Ein-Zeilen-Befehl zum Öffnen der Datei und fragt den Wert nie ab.

## Edge Cases & Failure Handling

Grundhaltung: **laut scheitern**. Eine klare Fehlermeldung ist besser als eine Grafik auf halben Daten.

| Fall | Verhalten |
|---|---|
| Kein Treffer | „0 Treffer" plus Aufschlüsselung, wie viele Titel jeder Filter **einzeln** übrig ließe |
| Zu wenige Treffer | Liste zeigen, Hinweis (R5) |
| Kennzahl fehlt | Titel ausschließen **und** in „N Titel ausgeschlossen" zählen, aufklappbar |
| Buchwert ≤ 0 | fällt aus buchwertbasierten Filtern, wird gezählt |
| Meldung älter als 6 Monate | nicht wählbar, gezählt (R14) |
| Unter Mindestkurs/-volumen | nicht wählbar, gezählt (R15) |
| Kurslücke im Zeitraum | Titel im betroffenen Quartal übersprungen, im Bericht vermerkt |
| Titel verschwindet | Delisting-Rendite ansetzen (R18), Erlös auf die übrigen verteilen, zählen (R19) |
| Anthropic-Schlüssel fehlt | klare Meldung mit Verweis auf `.env.local`; Filter bleiben von Hand bedienbar |
| Modell liefert ungültiges Schema | bis zu zwei Wiederholungen, dann Meldung, Spezifikation unverändert |
| SEC- oder Kursabruf scheitert | Wiederholung mit wachsender Wartezeit, dann Meldung; vorhandener Bestand bleibt nutzbar mit Angabe seines Standes |
| Bestand älter als 7 Tage | sichtbare Warnung |
| Startdatum vor Datenbeginn | auf den frühesten belastbaren Tag setzen, Hinweis; Begründung nennt den gemessenen XBRL-Abdeckungsbeginn |
| Zeitraum kürzer als eine Umschichtungsperiode | ablehnen |
| Doppelter Portfolioname | ablehnen |

## Risks, Landmines & Adaptations

| Gefunden | Was der Plan dagegen tut |
|---|---|
| **Überlebensverzerrung** — kostenlose Kursquellen führen verschwundene Titel kaum; die Zuordnung Ticker→CIK ist heute-basiert. Trifft „billig, klein" besonders hart. | `ticker_history` aus Einreichungshistorie + Deckblatt-Tickern + beobachteten Kurszeiträumen statt aus der heutigen Liste, **vor** der Backtest-Schleife gebaut (Phase 7). Die Wiederfindungsquote wird an einer aus den eigenen Daten gezogenen Stichprobe **gemessen** und angezeigt — unter 50 % wird ausdrücklich festgehalten, dass das Universum überlebendenbasiert bleibt, statt eine Korrektur zu behaupten. Delisting-Rendite statt letztem Kurs (R18). Lücken im Bericht (R19). Etikett an jeder Grafik (R12). Stufe B heißt bewusst nicht „echter Backtest". Phase 10 bereitet die bereinigte Quelle vor — sie ist die einzige vollständige Lösung. |
| **Blick in die Zukunft bei Bilanzdaten** | Stufe B prüft Meldedatum, abgesichert durch Test (R13); ursprüngliche Fassung schlägt spätere Korrektur |
| **Blick in die Zukunft beim Handel** | gescreent bis Schluss Tag *t*, gehandelt zur Eröffnung Tag *t+1* (R16) |
| **Vergleichsindex ohne Dividenden** — erzeugte glatt ein bis zwei Prozentpunkte Scheinmehrrendite pro Jahr | Total-Return-Reihe vorgeschrieben, Gleichbasigkeit durch Test abgesichert (R11) |
| **Untradebare Kleinstwerte** — genau die Titel, die der Filter des Nutzers anzieht | Mindestkurs und Mindestvolumen als Standard (R15); Kostenmodell nach Größe gestaffelt (R8) |
| **Zombie-Melder** — alter Buchwert gegen gefallenen Kurs sieht am billigsten aus | Frischegrenze von 6 Monaten (R14) |
| **Sprachmodell erfindet Titel oder Kennzahlen** | Schema ohne Ticker-Feld; Zurückweisungspflicht (R3); Auswahl deterministisch |
| **Überanpassung durch wiederholtes Probieren** — die Bedienart der App ist eine Maschine zum Suchen schöner Kurven | Zähler je Spezifikation am Ergebnis sichtbar; zurückgehaltener Prüfzeitraum, der ausdrücklich freigeschaltet werden muss (R21); Empfindlichkeitsprüfung (R20) |
| **Ergebnisse nicht reproduzierbar** — Kursquellen korrigieren bereinigte Kurse rückwirkend | Snapshot-Kennung an jedem Lauf, Warnung bei abweichendem Bestand (R22) |
| **SEC-XBRL ist unordentlich** | Phase 1 prüft an echten Firmen gegen den Geschäftsbericht; Merkmalsreihenfolge für Aktienzahlen wird dokumentiert `[A19]`; Auswahlregel für Korrekturen festgelegt |
| **Kostenlose Kursquelle inoffiziell** | alles lokal gespeichert, App läuft ohne Netz weiter; Quelle gekapselt, Austausch ist Konfiguration; private Nutzung |
| **Erstaufbau dauert lange** | einmaliger Hintergrundvorgang mit Fortschrittsanzeige; Phase 1 misst die Dauer vor dem Oberflächenbau |
| **Restrisiko: keine Anlageberatung** | App wendet Kriterien an und zeigt Ergebnisse samt Unsicherheiten; die Entscheidung trifft der Nutzer. Hinweis in der Oberfläche, Formulierungsprüfung (R23) |

## Assumptions Ledger

| ID | Annahme | Grundlage | Auswirkung bei Irrtum | Prüfung |
|----|---------|-----------|----------------------|---------|
| A2 | Anthropic-Schlüssel vorhanden und gültig | Notiz zum Vorprojekt | Phase 3 blockiert, Rest läuft | Phase 3, Schritt 1 |
| A3 | Streamlit lokal im Browser ist akzeptabel | **unbestätigt** — der Nutzer hat dem nicht widersprochen, aber auch nicht zugestimmt | Oberflächenschicht neu, Datenschicht und Screener bleiben | Abnahme durch den Nutzer am Ende von Phase 4 |
| A4 | DuckDB filtert das Universum ohne spürbare Wartezeit | Bauart einer spaltenorientierten Datenbank | vorberechnete Tabelle je Stichtag | Phase 1 |
| A5 | `yfinance` liefert bereinigte und unbereinigte Tageskurse für das US-Universum sowie die Indexreihe | Websuche 2026 | Stooq; sonst bezahlte Quelle früher | Phase 1, Abnahmekriterium |
| A6 | Gleichgewichtung ist der gewünschte Standard | Nutzer nannte keine | eine Zeile in der Spezifikation | im Chat änderbar |
| A7 | Quartalsweise Umschichtung passt | Bilanzrhythmus | Parameter | Einstellungen |
| A8 | Sortierung nach strengster Bewertungskennzahl | muss bei Trefferüberschuss entschieden werden | andere Liste | angezeigt, im Chat änderbar |
| A9 | Universum = US-Stammaktien ohne Fonds/ETFs/Zweitnotierungen | „us markt" (user) | Filterregel im Universumsaufbau | Phase 1, Stichprobe |
| A10 | Gestaffelte Spannen 0,05–1,00 % | Größenordnungen nach Liquiditätsklasse; in dieser Sitzung nicht nachgeprüft | Zahlen in den Einstellungen | Nutzer setzt eigene Konditionen |
| A11 | SEC-Gesamtpaket ist der richtige Weg und in vertretbarer Zeit ladbar; XBRL-Abdeckung beginnt je nach Meldergröße gestaffelt | sec.gov nennt das Paket; der Staffelbeginn ist **nicht** geprüft | Einzelabrufe, deutlich langsamer; kürzerer Backtest-Zeitraum | Phase 1 **misst** die Abdeckung je Jahr und legt das früheste belastbare Startdatum fest |
| A12 | ~~Oberflächensprache Deutsch~~ → **Englisch** | (user, Q7) — entschieden, keine Annahme mehr | — | — |
| A13 | Ein Portfolio hat höchstens einige Dutzend Titel | Nutzer nannte 10 | Anzeigedetails | — |
| A14 | Größenordnung der Überlebensverzerrung (75 % / 5–15 pp) | **Anbieterangaben**, keine geprüfte Wissenschaft | Richtung bleibt, Höhe könnte kleiner sein | wird als Anbieterangabe zitiert, nicht als Tatsache |
| ~~A15~~ | **ERLEDIGT 21.09.2026:** `^SP500TR` liefert 2766 Tagesbalken 2015–2025, 13,54 % p. a.; `^GSPC` 11,55 %; `SPY` bereinigt 13,43 % (11 bp unter dem Index) | verified: eigener Abruf | — | — |
| A16 | FRED-Zinsreihe ohne Schlüssel beziehbar | nicht geprüft | fester Wert in den Einstellungen | Phase 6 |
| A17 | Delisting-Rendite −30 % | Literaturkonvention, nicht nachgeprüft | Zahl in den Einstellungen; Empfindlichkeit zeigt den Einfluss | Phase 7 |
| A18 | 6 Monate Frischegrenze | Quartalsrhythmus plus Meldefrist | Parameter | Einstellungen |
| A19 | Aktienzahl ist aus XBRL zum Stichtag ableitbar | Deckblattangaben existieren, sind aber uneinheitlich gekennzeichnet | Rückfall auf die letzte gemeldete Zahl mit Kennzeichnung der Unschärfe | Phase 1, Stichprobe gegen Geschäftsberichte |
| A20 | Die im Kennzahlen-Vokabular genannten XBRL-Merkmale sind die richtigen | **Erfahrungswissen, in dieser Sitzung nicht geprüft** | einzelne Kennzahlen fehlen oder sind lückenhaft | Phase 1 (e): Merkmale nachprüfen, Abdeckung je Feld messen, unter 80 % kennzeichnen |
| A21 | Historische Ticker verschwundener Firmen sind aus Deckblattangaben und Einreichungshistorie teilweise rekonstruierbar | Deckblatt-XBRL enthält den Ticker erst in jüngeren Jahren | Universum bleibt überlebendenbasiert; wird dann so beschriftet statt korrigiert behauptet | Phase 7, gemessene Wiederfindungsquote mit 50-%-Entscheidungspunkt |

## Open Items (nichts blockiert den Baubeginn)

* ~~**Handelsfrequenz als eigene Grenze (24.09.2026).**~~ — **ist umgesetzt** (`PortfolioSpec.min_trading_days_60 = 40`, gezählt in `prices.session_coverage`, als eigener Ausschlussgrund gezählt und angezeigt); der Eintrag hier war nur nicht nachgezogen (festgestellt 24.09.2026, Cloud-Sitzung). Die Liquiditätsgrenze prüft die Höhe
  des Umsatzes, nicht seine Regelmäßigkeit. Vorschlag: eine Mindestzahl an Handelstagen im
  Fenster verlangen (etwa 40 der letzten 60 Sitzungen) und die Ausgeschlossenen, wie alle
  anderen Grenzen, getrennt zählen und anzeigen. Ändert die Ergebnisse bestehender
  Screens, deshalb eine Entscheidung des Nutzers und keine stille Korrektur.

**Geklärt in Q7 (user):** „cap to price max 1/1" heißt **price to profit**, also Kurs-Gewinn-Verhältnis ≤ 1. Das Feld ist im Kennzahlen-Vokabular bereits vorhanden; die zugehörigen XBRL-Merkmale lädt Phase 1 mit. **Oberflächensprache ist Englisch.** Hinweis ohne Handlungsbedarf: ein KGV ≤ 1 ist ein extrem enger Filter, der in vielen Quartalen null oder sehr wenige Treffer liefert — die App zeigt dafür die Aufschlüsselung, wie viele Titel jeder Filter **einzeln** übrig ließe, sodass sofort sichtbar ist, welcher Filter beißt.

**Vier Restschwächen, die der unabhängige Prüfer stehen ließ — sie sind nicht behoben, sondern benannt:**

- **Der wahrscheinlichste Ausgang von Phase 7 ist der Unter-50-%-Fall.** Der Ticker steht erst in jüngeren Einreichungen im Deckblatt, und die kostenlose Kursquelle wirft verschwundene Titel heraus. Realistisch läuft Stufe B also auf einem überlebendenbasierten Universum und sagt das auch so. **Phase 7 ist kein Versprechen, dass die Überlebensverzerrung kostenlos verschwindet** — sie misst, wie groß der Rest ist. Vollständig behoben wird sie erst durch die bezahlte Quelle (Phase 10).
- ~~**`[A15]` und `[A16]` sind ungeprüfte Verfügbarkeitsannahmen**~~ — **beide erledigt (24.09.2026).** `[A15]`: `^SP500TR` liegt für 2010–2026 vor, kein Rückfall auf `SPY`, die 25-bp-Toleranz aus R11 gilt und wird eingehalten (gemessen 11 bp). `[A16]`: der risikolose Zins kommt aus `^IRX` (13-Wochen-US-Schatzwechsel) über dieselbe Kursquelle — kein FRED, kein Schlüssel; der feste Wert aus den Einstellungen greift nur, wenn für das Fenster keine Zinsreihe gespeichert ist, und die Oberfläche nennt jeweils, welcher von beiden benutzt wurde.
- **`[A10]` und `[A17]` sind ungeprüfte Zahlen, die das Ergebnis bewegen** — die gestaffelten Spannen und die Delisting-Rendite von −30 %. Bei einem Portfolio aus kleinen Werten hängt das Ergebnis spürbar an beiden. Beide sind änderbar, und Phase 9 zeigt den Einfluss — geprüft wurden sie nicht.
- **Bedeutung von „cap to price max 1/1"** — wörtlich ergäbe das die Aktienzahl. Bis zur Klärung gilt: R3 greift, das Modell meldet den Teil als unverstanden, und die App fragt im Chat nach, statt zu raten. Kandidaten, die die App zur Auswahl anbietet: Kurs-Umsatz-Verhältnis, Kurs-Cashflow-Verhältnis, Marktkapitalisierung ≤ Kurs (unwahrscheinlich). **Eine Antwort darauf ersetzt später einfach einen Eintrag im Filterschema.**
- **Bezahlte Datenquelle ja/nein** — bis dahin alles kostenlos; Phase 10 legt den Anschluss bereit (user, Q5).
- **Weitere Kennzahlen** (Verschuldung, Dividende, Momentum, Gewinnwachstum) — das Schema ist erweiterbar; jede Kennzahl ist ein Eintrag, kein Umbau.
- **iPad-Zugriff** — später möglich, nicht in v1.

## Verification

```bash
uv run pytest -q                       # alle automatischen Tests
uv run python -m plab.checks.data      # Bestand: Zeilen, Stand, Abdeckung je Jahr, Lücken
uv run streamlit run app.py            # App starten
```

Was der Nutzer selbst prüft:

1. App starten, sie öffnet sich im Browser.
2. Wörtlich `portfolio aus 10 aktien us markt cap to price max 1/1 und price book max 2/1` eingeben → verstandene Filter im Klartext, unverstandener Teil ausdrücklich gemeldet.
3. `lass cap nicht über USD 2bn sein` → beide Filter aktiv, Liste ändert sich.
4. Unmögliches Kriterium eingeben → ausdrückliche Zurückweisung.
5. Zweites Portfolio, wechseln, neu starten → beide da.
6. Backtest „Rückblick" → Grafik gegen S&P, Kennzahlen, Warnetikett lesbar.
7. Backtest „Point-in-Time" → andere Zahlen, anderes Etikett, Zahl der verschwundenen Titel genannt.
8. Handelskosten verdoppeln → Ergebnis verschlechtert sich sichtbar.
9. Denselben Backtest ein zweites Mal starten → Zähler steht auf 2.
10. Prüfzeitraum freischalten → separates Ergebnis, als solches gekennzeichnet.

## Nachtrag 21.09.2026 — zwei Änderungen auf Zuruf

**1. Die Beispielkriterien waren nur ein Beispiel (user).** Kurs-Buchwert und Kurs-Gewinn sind nicht das Anlagekonzept, sondern illustrierten die Bedienung. An der Bauweise ändert das nichts — Filter sind Daten, nicht Code. Es verschiebt aber eine Priorität: **die Breite des Kennzahlen-Wortschatzes** wird wichtiger als jede einzelne Kennzahl. Derzeit 13 Felder; Erweiterungskandidaten stehen unter Open Items.

**2. Neu: Live-Tracking von Papierdepots (user).** Ein Schalter friert die aktuelle Auswahl zu den heutigen Kursen ein und verfolgt sie vorwärts gegen den S&P 500 Total Return.

Warum das die *stärkste* Messung im ganzen Werkzeug ist: Ein Backtest muss sich gegen drei Verzerrungen verteidigen — Überlebende, Rückschau, und das Herumprobieren an Filtern, bis die Kurve schön aussieht. Ein ab heute mitlaufendes Depot hat **keine davon**, weil die Auswahl feststeht, bevor das Ergebnis existiert. Es ist per Konstruktion außerhalb der Stichprobe. Der Preis ist Geduld: es sagt erst nach Zeitablauf etwas aus. Der zurückgehaltene Prüfzeitraum aus R21 versucht genau das zu simulieren — hier braucht es keine Simulation.

Modelliert: Einstiegskosten einmalig, Dividenden über den bereinigten Kurs (gleiche Renditebasis wie der Index — der häufigste stille Fehler), eingestellte Titel werden zum letzten Kurs gehalten **und gekennzeichnet**. Nicht modelliert: Steuern, Währung, laufende Gebühren, Umschichtung (Kaufen-und-Halten).

## Abnahme Phase 1 — Ergebnis vom 21.09.2026

**Bestanden.** Gemessen statt angenommen:

| Kriterium | Ergebnis |
|---|---|
| (a) Screen-Laufzeit | **3 ms** über 6.041 Titel (Kriterium: < 1000 ms); Tabellenaufbau 6,6 s |
| (b) Marktkapitalisierung gegen unabhängige Quelle | 30 von 39 Stichproben auf ±10 % gleich, viele exakt; 9 Abweichler durch Aktienklassen/Stichtage, **kein** Skalierungsfehler in der Zufallsstichprobe |
| (c) Kursbasen | bereinigt und unbereinigt getrennt gespeichert, 1.800.787 Zeilen |
| (d) XBRL-Abdeckung | 2009: 457 Melder · 2010: ~1.450 · **2011: ~7.700** → frühestes belastbares Startdatum **2011** |
| (e) Merkmalsabdeckung (zulässiges Universum, 4.862 Titel) | Eigenkapital 92,3 % · Gewinn 90,4 % · Cashflow 90,0 % · **Umsatz 77,8 % → unter der 80-%-Schwelle, wird gekennzeichnet** |
| (f) `FixtureSource` | vorhanden, Screener-Tests laufen gegen beide Quellen |
| (g) Aufbaudauer | SEC-Ingest 91 s · Kursdownload 500 s · Download 1,41 GB in 80 s |

**Drei Datenfehler gefunden und behoben, alle erst auf echten Daten sichtbar:**
1. Aktienzahlen von 0 oder 1 in den SEC-Daten → Marktkapitalisierung von wenigen Dollar → sortierten sich in einem Billigfilter **an die Spitze**. Jetzt Mindestgrenze plus Rückfall auf das nächste Merkmal.
2. Frische wurde je Firma statt **je Kennzahl** geprüft → Warner Music rechnete mit Aktienzahlen von 2019. Jetzt harte Altersgrenze pro Kennzahl (400 Tage).
3. Manche Melder taggen Aktienzahlen **in Tausend** (Suja Life tagte beide Skalen in derselben Einreichung). Wo beide vorliegen, gewinnt die größere; wo nur die skalierte vorliegt, greift eine Umsatz-Plausibilitätsprüfung (eine Firma kann nicht täglich mehr als die Hälfte ihrer Aktien handeln) — betroffene Titel werden **ausgeschlossen und gezählt**, nicht mit einem geratenen Faktor 1000 korrigiert.

**Erster echter Durchlauf der Gesamtkette** (Chat → Filter → Titel): Die Kriterien „KGV ≤ 1, KBV ≤ 2, Marktkapitalisierung ≤ 2 Mrd." liefern **2 Titel**. Die Aufschlüsselung zeigt warum: KGV ≤ 1 allein lässt im gesamten zulässigen Universum nur **4** Titel übrig.

## Stand 24.09.2026 — was steht, was der Plan noch schuldet

Abgehakt oben: **Phase 1** (Abnahme dokumentiert), **Phase 3** (die Abnahmekriterien sind
durch `tests/test_translate.py` einzeln abgedeckt: kein Ticker-Feld, geschlossenes Enum,
zwei aufeinander aufbauende Nachrichten, Zurückweisung erfundener Kriterien, Grenzen
gegen Chat-Zugriff geschützt), **Phase 4** (läuft; die Abnahme selbst und damit `[A3]`
gehört dem Nutzer, sie ist nicht erteilt).

**Phase 2 ist seit dem 24.09.2026 abgeschlossen.** Das offene Kriterium war der Satz, auf
den es in dieser Phase ankommt: *dieselbe* Testreihe gegen `FreeSource` **und**
`FixtureSource`. Zuvor prüfte `tests/test_seam.py` nur die Schnittstelle, `test_screen.py`
lief gegen von Hand gebaute Rahmen, und der Screener sah das Fixture nie von Ende zu Ende,
weil `screening_frame()` allein auf `FreeSource` existierte. Umgesetzt:

* `base.build_screening_frame()` baut den Screening-Rahmen **nur aus den drei Primitiven
  der Schnittstelle** (`universe`, `fundamentals`, `prices`). Jede Quelle, die das
  Protokoll erfüllt, bekommt den Rahmen damit geschenkt; `FixtureSource` nutzt genau ihn.
* `tests/test_screener_contract.py` schreibt die Zusicherungen **einmal** und lässt sie
  über beide Quellen laufen: Spaltenvertrag, abgeleitete Kennzahlen gegen ihre Rohwerte,
  vollständig abgerechnete Ausschlüsse (`Summe der Ausschlüsse == Universum − zulässig`),
  Frischegrenze, 0-Treffer mit Aufschlüsselung, Sortierung, Branchenfilter, Gewichte,
  und dass kein Fakt aus der Zukunft in den Rahmen gerät.
* `FreeSource` behält seine schnelle SQL-Fassung — aber ein Test vergleicht sie über alle
  5.363 Titel mit der Referenz und verlangt Gleichheit auf 1e-9 bei Kurs, Liquidität und
  Datenalter. Die Abkürzung ist damit überwacht statt gehofft.

**Dabei aufgefallen (offen, nicht behoben): die Liquiditätsgrenze misst nicht, wie oft ein
Titel überhaupt handelt.** Der Vergleich der beiden Bauweisen wich anfangs bei genau drei
Titeln ab — BRR, HWH, CSAN. Alle drei haben im letzten Jahr **vier Handelstage**. Die
Grenze nimmt den Median von 60 Bars, gleich wie alt sie sind; ein Titel, der an vier Tagen
je über 1 Mio. USD umsetzt und danach 361 Tage gar nicht, besteht sie. R15 will genau
solche Titel fernhalten, und tut es hier nicht. Ein Vorschlag steht unter Open Items.

**Phasen 5 bis 10 sind unangetastet.** Es gibt keinen Rückblick-Backtest, kein
Kostenmodell, keine Kennzahlentabelle gegen synthetische Reihen, kein `backtest_runs`,
kein historisches Universum (`ticker_history` hat 0 Zeilen), keinen Point-in-Time-Lauf,
keinen Testzähler und keinen freischaltbaren Prüfzeitraum außer der Einstellung
`holdout_years = 2`, die noch nichts bewirkt.

**Gebaut wurde stattdessen in die Breite**, außerhalb der Phasenliste: Research-Tab mit
Einzelfirmenanalyse und Restatement-Befunden, Optionen samt Volatilitätsfläche,
Strategie-Vergleich (Einmalanlage gegen Ratenkauf gegen Dip-Regel), Makro, Rohstoffe,
Kalender, News, Morning-Briefing, Watchlist, Live-Tracking von Papierdepots — und am
24.09. der Branchenfilter samt Kerzenchart. Rund 5.600 Zeilen Code, 194 Tests grün.

Das ist eine Entscheidung mit Preis, und sie steht hier, damit sie sichtbar ist: Der
Leitsatz dieses Plans lautet, dass ein Backtest mit zu guten Zahlen schlimmer ist als
gar keiner. Solange Phasen 5 bis 9 fehlen, hat die App viele Ansichten und **keine
belastbare historische Auswertung** — die Frage „hätte das funktioniert" kann sie nicht
beantworten. Das Live-Tracking (Nachtrag oben) ist der ehrliche Ersatz, aber es braucht
Zeit, die noch nicht vergangen ist.

Zwei kleinere Befunde aus der Durchsicht am 23.09.:

* **Kriterium 1(e) ist halb eingelöst.** Die Abdeckung je Feld wird gemessen und in die
  Tabelle `coverage` geschrieben, aber nirgends gelesen — der einzige Zugriff außerhalb
  von `sec.py`/`schema.py` steht in `scripts/build_fundamentals.py`. Gemessen ist nicht
  gekennzeichnet; am Bildschirm ist nicht zu sehen, dass ein Filter auf Umsatz (67 % des
  Gesamtuniversums) auf dünnerem Eis steht als einer auf Eigenkapital (91 %).
* **`ticker_history` und `shares_outstanding` sind leer.** Für heute folgenlos, aber
  `shares_outstanding` erklärt, warum 426 Titel mangels Aktienzahl ausgeschlossen
  werden, und `ticker_history` ist die Voraussetzung für Phase 7.

## Gegenprüfung Phase 7 + 8 — Ergebnis vom 25.09.2026

Der Plan verlangt vor Phase 8 eine Gegenprüfung durch jemanden, der den Code nicht
geschrieben hat. Sie hat stattgefunden. **Verdikt: Phase 7 bestanden, Phase 8
durchgefallen.** Zehn Befunde, die schwersten drei:

**1. Der Handel wurde zum Eröffnungskurs ausgeführt, aber zum SCHLUSSKURS bemessen.**
Das ist Rückschau, und sie zeigt nach oben. Reproduziert: flacher Markt, ein einziger
Kurssprung von 10 auf 13 am Ausführungstag, niemand handelt dort — der Lauf meldete
**+30 %**. Das Depot wurde bei *jeder* Umschichtung um `Schluss/Eröffnung` neu gehebelt.
Der Prüfer bezifferte es auf **+0,92 Punkte pro Jahr** und 14 % des Endvermögens.
Behoben; das Buch wird jetzt zum Eröffnungskurs bewertet, weil dort das Geld eingesetzt
wird. Der alte R16-Test konnte es nicht sehen: sein Prüffall hatte `Schluss < Eröffnung`,
das einzige Vorzeichen, unter dem der Fehler nach unten zeigt.

**2. Stufe B hatte überhaupt keine Delisting-Rendite.** R18 war nur in Stufe A umgesetzt,
der Plan behauptete es für beide. Ein Titel, dessen Kurse aufhören, wurde zum letzten
Kurs gehalten **und** zum letzten Kurs kostenlos verkauft. Heute folgenlos, weil im
Bestand kein einziger Titel tot ist — und genau deshalb gefährlich: mit der bezahlten
Quelle (Phase 10) käme es sofort zum Tragen. Behoben, von Hand nachgerechnet: halb in
einem flachen Titel, halb in einem verschwindenden → 85,00.

**3. Umschlag und Kosten konnten auf einem echten Handel null sein.** Eine Position ohne
Eröffnungskurs am Umschichtungstag fiel aus der Berechnung, blieb aber im eingesetzten
Kapital — halbes Depot bewegt, Umschlag 0,000000, Kosten 0. Behoben: was nicht bepreist
werden kann, wird gehalten statt verkauft, und der Hinweis dazu steht am Ergebnis.

**Weiter behoben:** das Etikett behauptete eine Kostenstaffelung, die nicht stattfindet
(jetzt: „teuerste Stufe, überschätzt große Firmen absichtlich"); es verschwieg die
Positionsbemessung und das Delisting; ein Screening-Datum ohne sichtbare Meldungen ließ
den Lauf abstürzen (`Series * None`); und drei Tests bestanden aus falschen Gründen —
darunter einer, der eine **Anzahl** prüfte statt der **Namen** und mit vollständig
kaputter Rückschau grün geblieben wäre, und einer, der eine Unterbrechung beim Schreiben
prüfen sollte, ohne je zu unterbrechen.

**Offen, bewusst nicht in derselben Sitzung behoben:**

* **`period_start`-Verschiebung bei Korrekturen** (`pit.py`): Eine Neueinreichung, die den
  Periodenbeginn um Tage verschiebt, landet in einer anderen Partition und überlebt als
  „früheste Fassung ihrer eigenen Periode". Gemessen **32.463** betroffene Gruppen.
  Richtung unbestimmt, aber es verletzt eine absolute Regel (R13). Der naheliegende Fix
  — nur nach `period_end` partitionieren — würde Quartal und Geschäftsjahr mit gleichem
  Periodenende vermischen; es braucht eine Bucket-Bildung nach Periodenlänge. Das ist
  eine überlegte Änderung, keine Abendreparatur.
* ~~**`period_start`-Verschiebung bei Korrekturen**~~ — **behoben am 24.09.2026 (Cloud-
  Sitzung):** `pit._visible` partitioniert jetzt nach `period_end` **und Längenklasse**
  (Jahr 330–400 Tage, Quartal 80–100, sonst gerundete Monate), die früheste Meldung
  gewinnt. Quartal und Neun-Monats-Spanne mit gleichem Ende bleiben getrennt. Dieselbe
  Lücke steckte in `research.py` (Jahres- und Quartalshistorie) und ist dort mit behoben.
  Drei neue Tests in `tests/test_pit.py`; der Quartalsfall schlug vorher fehl (150 statt
  100). `CACHE_VERSION` → `v3`. **Noch nicht gemessen:** die Wirkung auf die drei
  Varianten der Tabelle unten. Das braucht `data/market.duckdb`, die nur lokal liegt:
  `scripts/warm_pit_cache.py` neu laufen lassen, dann die drei Läufe wiederholen.
* ~~**`ticker_history` wird von Stufe B nicht benutzt.**~~ — **behoben am 24.09.2026
  (Cloud-Sitzung):** `FreeSource.universe()` liest jetzt `history.universe_at()`: ein
  Titel gehört zum Stichtag, wenn sein Preisfenster in `ticker_history` ihn abdeckt
  (10 Tage Kulanz); Stammdaten kommen aus `securities`, ein nur dort bekannter
  verschwundener Titel bleibt mit leeren Feldern drin. Titel ganz ohne Kurse bleiben wie
  bisher an jedem Stichtag drin, damit die Ausschluss-Rechnung aufgeht. Die Preisfenster
  werden jetzt bei jedem `scripts/build_prices.py` mit aufgefrischt
  (`history.refresh_price_windows`). Sind sie leer oder älter als der neueste Kurs, fällt
  die Quelle auf `securities` zurück **und der Lauf sagt das im Hinweis**. Vier neue
  Tests in `tests/test_stage_b.py`, alle vier schlugen vorher fehl. Dabei gefunden und
  behoben: ein Stichtag, an dem alle Meldungen älter als 400 Tage sind, ließ den Lauf mit
  `KeyError: 'data_age_days'` abstürzen (schnelle SQL-Fassung in `free.py`; die
  Referenzfassung in `base.py` hatte die Absicherung schon). `CACHE_VERSION` → `v4`.
  **Lokal einmal nötig:** `scripts/build_ticker_history.py` oder `scripts/build_prices.py`
  laufen lassen, damit die Fenster aktuell sind — sonst greift die Rückfallebene.
* **Der Cache-Schlüssel erkennt Korrekturen an Ort und Stelle nicht** (Quelle, jüngster
  Balken, Zeilenzahlen ändern sich dabei nicht). Dokumentiert statt behauptet;
  `CACHE_VERSION` ist jetzt `v2` und die Regel zum Hochzählen steht im Modul.
* ~~**R19 für Stufe B**~~ — **behoben am 24.09.2026:** der Lauf zählt, wie viele Titel
  zwischen zwei Stichtagen das Universum verlassen haben, und nennt die ersten acht.

**Wirkung auf die Zahlen, gleiche Bedingungen vorher/nachher:**

| Variante | vorher | nachher | ggü. Index vorher → nachher |
|---|---|---|---|
| KGV ≤ 5 & KBV ≤ 2, quartalsweise | 4,17 % | **3,53 %** | −10,05 → **−10,68** |
| dasselbe, jährlich | 11,95 % | **11,69 %** | −2,27 → **−2,53** |
| KBV ≤ 1, 20 Titel, jährlich | 15,77 % | **15,22 %** | +1,55 → **+1,01** |

## Dritte Gegenprüfung Phase 7 + 8 — 24.09.2026 (Cloud-Sitzung)

Derselbe Prüfer hat die Fixes der zweiten Runde zu brechen versucht. Alle acht halten.
**Verdikt: Phase 7 bestanden; Phase 8 durchgefallen an genau einem Befund**, der inzwischen
behoben ist:

1. **Blockierend (R13): Die Altersgrenze wurde VOR der Wahl der ersten Einreichung
   angewandt.** War das Original älter als 400 Tage, wurde eine spätere Korrektur
   derselben Periode zur „ersten“ — mit frischem Datum, das einen säumigen Melder obendrein
   als aktuell durchgehen ließ. Genau die notleidenden, billigen Titel eines Value-Screens.
   Jetzt: erste Einreichung über alles Sichtbare, erst danach die Altersgrenze. Test mit
   dem Szenario des Prüfers. `CACHE_VERSION` → `v6`. Häufigkeit ungemessen, Obergrenze:
   `SELECT COUNT(*) FROM (SELECT cik, metric, period_end, MIN(filed) f0, MAX(filed) f1, COUNT(DISTINCT value) nv FROM fundamentals WHERE period_start IS NULL GROUP BY ALL) WHERE date_diff('day', f0, f1) > 400 AND nv > 1`
2. **R19-Zahl „ohne Kurs“ war konstruktionsbedingt immer null.** Ersetzt durch die Zahl, die
   Risiko trägt: Positionen, deren Kurse endeten, während sie gehalten wurden, und die zum
   angenommenen Delisting-Abschlag geschlossen wurden.

**Beim Test zu 2 selbst gefunden, nicht vom Prüfer:** Der Handelskalender von Stufe B wurde
nur aus den Kursen der gehaltenen Titel gebaut. Verschwanden alle gehaltenen Titel, endete
der Kalender mit ihnen — nichts sah delistet aus, der −30-%-Abschlag (R18) griff nie, und
der Lauf hörte still früher auf. Jetzt liefert die Quelle den Marktkalender
(`FreeSource.sessions`). Aufwärtsverzerrung, betraf konzentrierte Depots mit Pleiten.

Vom Prüfer als Verdacht notiert, nicht behoben: auf der bezahlten Quelle bekommt jeder
Abgang pauschal −30 % (Übernahmen mit Aufschlag ebenso wie Pleiten; Sharadar ACTIONS wird
nicht genutzt), und das Etikett sagt dort weiterhin „Überlebende“ — vorsichtig falsch.

**Stand Phase 8:** Nach Einschätzung des Prüfers mit Fix 1 bestanden, vorbehaltlich der nur
lokal möglichen Messungen: 5-Minuten-Grenze (Cache `v6` neu wärmen), Wirkung auf die drei
Vergleichsvarianten, Häufigkeit der R13-Lecks.

## Zweite Gegenprüfung Phase 7 + 8 — 24.09.2026 (Cloud-Sitzung)

Ein unabhängiger Prüfer (eigener Agent, hat den Code nicht geschrieben) hat Phase 7 und 8
nach den Fixes erneut gegen die Abnahmekriterien gelesen und Befunde mit Skripten
nachgestellt. **Verdikt: Phase 7 bestanden, Phase 8 durchgefallen.** Alle Befunde sind
abgearbeitet, jeder mit einem Test, der am alten Code scheitert (sieben) bzw. eine
Mutation erkennt (einer):

1. **Blockierend (R13): Korrektur unter besserem XBRL-Tag verdrängte das Original.**
   `instant_values` und `share_counts` wählten die früheste Meldung *je Tag* und
   bevorzugten danach den besseren Tag — ein 10-K/A mit `StockholdersEquity` schlug das
   10-K mit dem Ersatz-Tag, samt neuer Aktienzahl. Jetzt spricht nur die erste Einreichung
   einer Periode, der Tag-Vorrang wählt nur unter deren Tags. Ausnahme, bewusst: eine
   unplausible Aktienzahl (0, 1) in der ersten Einreichung überlässt die Periode der
   nächsten. **Häufigkeit in den echten Daten ungemessen**, Abfrage dafür:
   `SELECT COUNT(*) FROM (SELECT cik, metric, period_end, MIN(filed) f0, arg_min(filed, tag_rank) fbest FROM fundamentals WHERE period_start IS NULL GROUP BY ALL) WHERE fbest > f0`
2. **Kriterium 3 auf dem `ticker_history`-Pfad war ungetestet** — das Entfernen von
   `valid_from <= asof` blieb grün. Neuer Test mit spät beginnendem Preisfenster.
3. **R19 nur halb:** jetzt beide Zahlen (verlassen / davon ohne Kurs), auch bei null.
4. **Kosten wurden zu 0 % geliehen statt vom Anlagebetrag bezahlt** (Aufwärtsverzerrung,
   bei Buy-and-hold über die ganze Haltedauer). Behoben: 10 → 40 bei 1 % ergibt 396, nicht 399.
5. **`exclude` (R20, „ohne stärksten Titel“) verkleinerte das Depot** statt den Platz an
   den nächsten Titel zu geben. Behoben.
6. **Cache-Schlüssel ignorierte die Universumsbasis** — jetzt Teil des Schlüssels; der
   Rückfall-Hinweis war zudem falsch formuliert, korrigiert.
7. **R16 wörtlich** („nur Schlusskurs am Screening-Tag ändern → Füllung unverändert“) hat
   jetzt einen eigenen Test.
8. Klein: Benchmark wurde rückwärts aus der Zukunft aufgefüllt, wenn der Index später
   beginnt (jetzt: kein Vergleich statt eines erfundenen); Umschichtungstermine rutschten
   nach einem 31. dauerhaft auf den 28. (jetzt vom Start aus gezählt).

Nicht geändert: eine Position, deren Kurse weniger als 10 Tage vor Fensterende enden,
wird zum letzten Kurs bewertet statt mit Delisting-Abschlag (Datenverzug und Delisting
sind dort nicht unterscheidbar). `CACHE_VERSION` → `v5`.

**Was für die Abnahme von Phase 8 noch fehlt:** (a) eine dritte Prüfrunde über diese
Fixes, (b) die Messungen, die nur lokal gehen — 5-Minuten-Grenze mit neu gewärmtem Cache,
Wirkung auf die drei Vergleichsvarianten, Häufigkeit von Befund 1.

## Phase 7 — Ergebnis vom 24.09.2026: die Messung fällt aus, und das ist das Ergebnis

**Abgeschlossen, mit dem Ausgang, den der Prüfer vorhergesagt hatte.** `ticker_history`
ist gebaut — 16.918 Melder aus der SEC-Einreichungsdatei gelesen, Einreichungsfenster und
beobachtete Kurszeiträume je mit Quellenangabe hinterlegt, 13.972 Zeilen. Dann die
vorgeschriebene Messung: 20 CIKs aus dem **eigenen** Bestand, die 2014 noch meldeten und
seit 2019 nichts mehr, gezogen über einen Hash der CIK, damit niemand bequeme Firmen
ausgesucht haben kann.

| Frage | Ergebnis |
|---|---|
| Für wie viele liefert `ticker_history` überhaupt ein Kürzel? | **0 von 20** |
| Für wie viele davon liegen Kurse vor? | **0 von 20** |
| Verdikt | **überlebendenbasiert** (Schwelle: 50 %) |

**Warum null und nicht wenig:** Die SEC-Einreichungsdatei führt unter `tickers` nur die
**heutigen** Kürzel. Für eine verschwundene Firma ist das Feld leer. Die im Plan
vorgesehene Deckblattangabe `dei:TradingSymbol` ist ein **Text**-Fakt und steckt deshalb
nicht im numerischen `companyfacts`-Paket; sie pro Einreichung zu holen hieße
Zehntausende Einzelabrufe. Von 16.918 Meldern haben überhaupt nur 6.586 ein aktuelles
Kürzel — die anderen 10.332 sind aus Sicht der kostenlosen Quelle namenlos.

**Folge, und sie steht jetzt in der Oberfläche statt nur hier:** Stufe B wird auf einem
überlebendenbasierten Universum laufen. Die Seitenleiste und das Etikett an jeder
Backtest-Grafik nennen die gemessenen Quoten im Klartext, statt eine Korrektur zu
behaupten, die nicht stattfindet. Vollständig behoben wird das erst durch die bezahlte
Quelle (Phase 10).

**R18 · Delisting-Rendite ist umgesetzt.** Eine Position, deren Kurse vor dem Ende des
Zeitraums aufhören, hält **nicht** ihren letzten Kurs, sondern nimmt den ausgewiesenen
Verlust (Standard −30 %, Einstellung `[A17]`). Von Hand nachgerechneter Test: halb in
einer Verdopplung, halb in einem verschwindenden Titel → 100 × (0,5 × 2,0 + 0,5 × 0,7) =
**135,00**. Ein zweiter Test zeigt, dass die Annahme das Ergebnis bewegt (−10 % gegen
−100 %), damit sie als das sichtbar bleibt, was sie ist: eine ungeprüfte Zahl.

**R19 · Zählung:** Die Zahl der verschwundenen Titel und die angewandte Annahme stehen als
Hinweis am Ergebnis.

## Phase 6 — Ergebnis vom 24.09.2026

**Bestanden.** Die Formeln liegen in `metrics.py`, damit Portfolio und Index nachweislich
von derselben Funktion gemessen werden — ein Index, der nach einer anderen Regel gerechnet
wird als das Portfolio, das er beurteilen soll, verschiebt jeden Vergleich um die Differenz
der Methoden.

**Jede Formel gegen eine analytisch bekannte Reihe, Toleranz 1 Basispunkt** (22 Tests in
`tests/test_metrics.py`):

| Kennzahl | Prüfreihe | Erwartung |
|---|---|---|
| Rendite p. a. | +1 % monatlich über 5 Jahre (das Beispiel aus dem Plan) | 12,6825 % |
| Rendite p. a. | Verdopplung über genau 2 Jahre | 41,379 % |
| Schwankung p. a. | Renditen wechseln exakt ±1 % | geschlossene Form 0,01·√(n/(n−1))·√252 |
| Schwankung p. a. | konstantes Wachstum | exakt 0 |
| Max. Rückgang | 100 → 70 → Erholung | exakt −30 % |
| Max. Rückgang | Verdopplung, dann Halbierung | **−50 %**, nicht 0 — gemessen vom laufenden Hoch |
| Sharpe | flache Linie | undefiniert statt unendlich |
| Abweichung zum Index | Portfolio = Index | exakt 0 |
| Abweichung zum Index | dauerhafter Vorsprung, gleicher Verlauf | **0** — gleiche Fahrt, nur höher |
| Umschlag | Kaufen-und-Halten | 0 |

**Risikoloser Zins:** `^IRX`, der 13-Wochen-US-Schatzwechsel, gemittelt über das
Backtest-Fenster, bezogen über dieselbe Kursquelle wie alles andere — damit ist `[A16]`
ohne FRED und ohne Schlüssel erledigt. Liegt für das Fenster keine Reihe vor, greift der
Wert aus den Einstellungen, und die Oberfläche schreibt dazu, welcher von beiden benutzt
wurde. Ein Sharpe gegen einen ungenannten Zins ist mit nichts vergleichbar, auch nicht mit
dem eigenen vom Vormonat.

**R23 — Formulierungsprüfung, automatisch.** `wording.py` führt die verbotenen
Formulierungen; ein Test läuft über **alle** Zeichenketten der Oberfläche (per AST, ohne
Docstrings). Er prüft zuerst sich selbst: elf Beispielsätze müssen erkannt, sechs
harmlose (»buy and hold«) durchgelassen werden — ein Wächter, der nichts fängt, besteht
auf jeder Codebasis. Zwei echte Treffer beim ersten Lauf, beide berechtigt entschärft:

* `briefing.py` enthält die Anweisung an das Modell *„never recommend buying or selling"* —
  eine Verbotsformulierung, keine Empfehlung. Die Prüfung erkennt jetzt Verneinungen im
  Umfeld von 40 Zeichen.
* `research.py` führt *„strong buy"* als **Analysten**-Bewertungsklasse. Das ist fremde
  Rede, als Datum ausgewiesen; die Ausnahme steht namentlich in `ATTRIBUTED`, gilt nur bei
  exakter Übereinstimmung, und die Bildunterschrift sagt jetzt ausdrücklich, dass diese
  Einstufungen von Wall-Street-Analysten stammen und nicht von dieser App.

Dazu die Abhängigkeitsprüfung: keine Broker- oder Orderbibliothek in `pyproject.toml`.
Und ein Test hält fest, dass der Hinweis am Seitenfuß (»places no orders, connects to no
broker, recommends no security«) eine **gerenderte** Zeichenkette bleibt — die erste
Fassung dieses Tests war auf den Modul-Docstring hereingefallen, der das Richtige sagt,
aber niemandem.

## Phase 5 — Ergebnis vom 24.09.2026

**Bestanden.** `backtest.py` rechnet Stufe A: einmal kaufen, halten, gegen den S&P 500
Total Return. Was dabei gemessen statt angenommen wurde:

| Kriterium | Ergebnis |
|---|---|
| R11 · gleiche Renditebasis | `^SP500TR` gegen `^GSPC` (derselbe Index **ohne** Dividenden): **+201 bp/Jahr** über 10 Jahre — das ist der Dividendenbeitrag, und er wäre null, wäre die Reihe fälschlich eine Kursreihe. Gegen `SPY`, denselben Index auf umgekehrtem Weg gebaut (gehandelte Kurse plus echte Ausschüttungen): **11 bp/Jahr** Abstand, der Index oben. Der Plan erlaubt 25 bp. |
| R8 · Kosten wirksam und gestaffelt | Kosten auf null → Jahresrendite steigt messbar (im Testlauf 9,8 bp/Jahr); Micro-Cap zahlt mehr als Mega-Cap; **fehlende Marktkapitalisierung zahlt die teuerste Stufe**, nicht die billigste |
| R16b · Stufe A ist Kaufen-und-Halten | Umschlag 0,00 in der Kennzahlentabelle; Kosten nur beim Ersterwerb, per Test auf den Cent nachgerechnet |
| R17 · Dividenden zum Ex-Tag reinvestiert | Test mit einem Zahler, dessen **Kurs** flach liegt und dessen bereinigte Reihe um 4 % steigt → Endwert exakt 104,00 |
| R10 · Grafik auf Basis 100 | Portfolio und Index auf gemeinsamer Basis, identische Datumsachse |
| R12 · Etikett | nennt beide **nicht** korrigierten Verzerrungen (Auswahl mit heutigen Daten, fehlende Delistings) und dass beide nach oben zeigen |
| R22 · Snapshot-Kennung | jeder Lauf landet in `backtest_runs` mit Kennung des Datenbestands; ein früherer Lauf auf anderem Bestand erzeugt eine Warnung |

**Zwei Befunde aus dem Bau, beide behoben:**

* Die Jahresrendite wurde zunächst vom **Kurvenwert nach Kosten** aus annualisiert. Damit
  verschwand das gesamte Kostenmodell aus der Schlagzeilenzahl, während es in der
  Gesamtrendite noch auftauchte — die beiden erzählten verschiedene Geschichten. Jetzt
  ist das eingesetzte Kapital die Bezugsgröße, und ein Test hält das fest.
* Ein Backtest, dessen Startdatum weiter zurückreicht als die gespeicherten Kurse,
  **verkürzte den Zeitraum stillschweigend**. Jetzt steht ein Hinweis am Ergebnis.

**Grenze, die dabei sichtbar wurde:** Der Kursbestand umfasst ein rollendes Fenster von
460 Tagen, echte Zehnjahres-Rückblicke sind damit nicht möglich. Für die Benchmark-Reihen
liegen 2010–2026 vor, für die Einzelaktien nicht. Ein tieferer Kursdownload ist eine
Entscheidung über Zeit und Datenmenge, keine Programmieraufgabe.

## Weltmarkt, Stufe 1 — ausländische Filer der SEC (24.09.2026, Cloud-Sitzung)

Auf Wunsch des Nutzers, als erster Schritt Richtung Weltmarkt. Bis hierhin las
`sec.py` nur Beträge **in USD unter US-GAAP-Tags**: jede Firma, die in Euro, Yen oder
Rupien berichtet — auch US-GAAP-Filer mit 20-F — und jede IFRS-Firma hatte **gar keine**
Fundamentaldaten. Jetzt:

* **Einlesen** (`sec.py`): IFRS-Tags je Kennzahl (nach Konzept gewählt, noch nicht gegen
  das Archiv gemessen — `measure_coverage()` ist die Prüfung), Beträge in jeder
  ISO-Währung; je Filer eine Berichtswährung (die der meisten Fakten), damit
  Convenience-Übersetzungen nicht als zweiter Wert daneben stehen. Neue Spalten
  `fundamentals.currency` und `.form`.
* **Umrechnung zum Stichtag** (`pit.to_usd`): Geldwerte werden zum Kurs des
  Screening-Datums in USD umgerechnet (`{CUR}USD=X`, höchstens 10 Tage alt), nie zu einem
  späteren. Ohne Kurs: keine Beträge, eigener Ausschlussgrund
  „no exchange rate for the reporting currency“. Kurse: `scripts/build_fx.py` einmal,
  danach frischt `build_prices.py` sie täglich auf.
* **ADR-Verhältnis** (`data/listing.py`, Tabelle `listing_ratios`): Filer mit 20-F/40-F
  melden Originalaktien, ihr US-Kurs gilt je ADR (Toyota: 1 ADR = 10 Aktien) — ohne
  Korrektur wäre der Börsenwert zehnfach zu hoch. `scripts/build_listing_ratios.py`
  misst das Verhältnis über Yahoo (SEC-Aktien / Yahoo-Aktien) und übernimmt es nur nahe
  einem üblichen Verhältnis; `--set TICKER=VERHÄLTNIS` setzt es von Hand. Ohne
  gemessenes Verhältnis: eigener Ausschlussgrund „foreign listing: ADR ratio not
  measured“. **Yahoo ist dafür keine Primärquelle** — die Liste der Verhältnisse ≠ 1
  nach dem Lauf ansehen.
* Beide Wege zum Screening-Rahmen wenden das gleich an (Test). Die Bewertungshistorie im
  RESEARCH-Reiter ebenso (Kurs zum Jahresende, Verhältnis); die Tabellen dort zeigen
  Beträge wie gemeldet und nennen die Währung. `CACHE_VERSION` → `v7`.
* **Ungemessen:** wie viele Filer dazukommen, wie viele Verhältnisse sich auflösen. Das
  zeigt der lokale Lauf (unten). Bestehende Daten ohne Währungsangabe gelten als USD —
  bis zum Neuaufbau ändert sich also nichts.

**Lokal ausführen, in dieser Reihenfolge:** `scripts/build_fundamentals.py` (Neuaufbau,
zeigt am Ende die Filer je Währung), `scripts/build_fx.py`,
`scripts/build_listing_ratios.py`, dann `scripts/warm_pit_cache.py`.

## Weltmarkt, Stufe 2 — Markt-Monitor und beliebige Yahoo-Ticker (24.09.2026)

* **MONITOR-Reiter** (`world.py`): Indizes aus Amerika, Europa und Asien-Pazifik (u. a.
  S&P/ASX 200, DAX, Nikkei), Währungspaare (AUD/USD, EUR/USD, …), US-Zinskurve,
  Rohstoffe, Krypto — je Letzter Wert, 1T, 1W, 1M, seit Jahresbeginn, 1J; Zinsen in
  Basispunkten. Jede Veränderung gegen den letzten Schluss **am oder vor** dem
  Fensterbeginn. Darunter ein Vergleich beliebiger Märkte, auf 100 normiert. Tagesdaten:
  `scripts/build_world.py` einmal, danach frischt `build_prices.py` sie täglich auf.
* **Beliebige Yahoo-Ticker** (`BHP.AX`, `SAP.DE`, `7203.T`): RESEARCH zeigt Kurs, Volumen,
  Bollinger, Rendite- und Risikokennzahlen und den Live-Kurs, in der Währung der Börse;
  LAB → Single stock testet Kauf-/Verkaufsregeln darauf. Abgerufen bei Bedarf und nur im
  Speicher gehalten, **nicht** in den Marktbestand geschrieben — der ist das Universum des
  Screeners. Keine Bilanzzahlen (keine SEC-Meldungen), kein Screen, kein Backtest,
  kein Papierdepot (Depots werden aus dem Bestand bewertet; die App sagt das).
* In der laufenden App geprüft (Monitor mit Demo-Reihen; ausländischer Ticker ohne
  Yahoo-Zugang ergibt eine klare Meldung, keinen Absturz). Der echte Yahoo-Abruf ist
  in der Cloud-Sitzung gesperrt und daher nur mit nachgebautem Download getestet.

## Weltmarkt, Stufe 3 — Compustat Global über WRDS (25.09.2026, Cloud-Sitzung)

Entscheidung des Nutzers: WRDS über die Uni statt eigener ESEF/EDINET-Strecken oder
eines bezahlten Anbieters.

* **Download** (`data/wrds.py`, `scripts/build_wrds.py`): SQL direkt gegen den
  WRDS-PostgreSQL-Server (`psycopg2`), nicht über das `wrds`-Paket — das verlangt
  pandas < 2.3. Tabellen `g_company`, `g_security`, `g_secd`, `g_funda`, `g_fundq`,
  `g_exrt_dly`; nur Spalten, die WRDS tatsächlich führt (vorher abgefragt). Ablage als
  Parquet unter `data/cache/wrds/`; abgeschlossene Jahre der Tageskurse werden nicht
  erneut geholt. Standard-Länder: entwickelte Märkte außerhalb Nordamerikas, ab 2010.
* **Übersetzung** ins bestehende Schema, in eine eigene Datei `data/world.duckdb`, die
  zusätzlich den US-Teil des freien Bestands trägt (`copy_us`, von `build_prices.py`
  täglich nachgezogen). Eine Zeile je Firma: die Hauptnotiz im Heimatmarkt
  (`prirow`), nur Stammaktien. Ticker = Compustat-Ticker + Land (`7203.JPN`).
  CIK = 1.000.000.000 + gvkey, damit nie eine SEC-CIK getroffen wird.
* **Alles in USD zum Tageskurs** (Kurse je Pfund aus `g_exrt_dly`, über den Dollar
  gekreuzt; Pence/Cent/Agorot durch 100; `qunit`). Split-bereinigt über `ajexdi`,
  Gesamtrendite über `trfd`. Fundamentaldaten behalten ihre Währung und laufen durch
  `pit.to_usd`, die Kurse dafür stehen als `{CUR}USD=X` im Bestand.
* **Meldedatum geschätzt**, weil Compustat Global es für Jahresabschlüsse nicht führt:
  Periodenende + gesetzliche Frist (4 Monate Jahresbericht in EU/UK/Japan, 3 Monate
  Zwischenbericht) — das späteste Datum, zu dem eine fristgerechte Firma veröffentlicht
  haben muss, also nie vor der Wahrheit. Wo `g_fundq.rdq` existiert, gilt es. Rest-
  Vorgriff: Firmen, die die Frist reißen. Der Build zählt, wie viele Fakten auf einer
  Schätzung beruhen (`fundamentals.fp = 'est'`).
* **Aktienzahl** aus der Tagesdatei (`cshoc`, je Monat der letzte Handelstag, am selben
  Tag bekannt) vor der aus dem Bericht; bei zwei Aktiengattungen nur die Hauptlinie —
  Börsenwert dann zu niedrig.
* **Grenzen, bekannt:** Quartals-Cashflow wird nicht gelesen (in `g_fundq` kumuliert,
  `oancfy`); die Floors ($5 Mindestkurs, $1m Umsatz) sind US-gedacht und schließen
  britische Pennystocks und kleine europäische Werte aus; Research-Bereiche, die SEC-
  Meldungen brauchen (Form 4, 10-K, Filings), sagen bei Compustat-Firmen, dass es sie
  nicht gibt. Der US-Teil bleibt überlebendenbasiert, daher `survivorship_free = False`.
* **Ungetestet gegen den echten Dienst** (aus der Cloud-Sitzung nicht erreichbar): die
  Übersetzung ist mit handgebauten Dateien im Compustat-Global-Layout getestet
  (`tests/wrds_fixture.py`, 17 Tests), der Download gegen einen nachgebauten Server.
  Unsicher sind vor allem Spaltennamen (`qunit`, `rdq`, `gp`), die Einheit von `cshoc`
  (angenommen: Stück, nicht Millionen) und ob `datafmt = 'HIST_STD'` in `g_funda`
  befüllt ist. Die Länderzählung am Ende des ersten Builds zeigt es.

**Nachtrag 25.09.2026 — US-Teil aus CRSP** (`data/crsp.py`), auf Wunsch des Nutzers,
nachdem die erste Anmeldung bei WRDS funktioniert hat. Behebt die Überlebensverzerrung
für die USA, die Phase 7 gemessen hat (0 von 20):

* **Kurse** aus der CRSP-Tagesdatei (Legacy-Format `crsp.dsf`, `dsenames`, `dsedelist`):
  Stammaktien (Share Code 10/11) an NYSE, NYSE American, Nasdaq. Gehandelter Kurs
  `|prc|` (negativ = Geld-Brief-Mitte), Split-bereinigt über `cfacpr`, Gesamtrendite aus
  CRSPs `ret` aufgezinst.
* **Nach dem CRSP-Ende** (Abo-Stand, oft Dezember des Vorjahres) laufen die noch
  gehandelten Linien mit den Tageskursen des freien Bestands weiter, am letzten CRSP-Tag
  auf CRSPs Basis umgerechnet (`extend`, täglich über `build_prices.py`). Ticker einer
  noch gehandelten Linie = der heutige des freien Bestands (META, nicht FB); beendete
  Linien behalten den letzten CRSP-Ticker, bei Kollision mit Permno-Suffix.
* **Schlüssel = SEC-CIK** über die CRSP-Compustat-Verknüpfung (`ccmxpf_lnkhist`) und
  `comp.company.cik`, damit die SEC-Fundamentaldaten mit echtem Meldedatum gelten —
  auch für verschwundene Firmen, die die SEC weiter führt. Ohne CIK: 2 Mrd. + Permno.
  **Compustat NA** nur für Perioden vor der ersten XBRL-Meldung (`prune_compustat`),
  datiert über `rdq`, sonst Frist (10-Q 45, 10-K 90 Tage).
* **Delisting-Rendite**: neue Tabelle `delisting_returns`; Stufe A und B nehmen sie statt
  der angenommenen −30 %, wo CRSP sie hat, und sagen im Bericht, welche Werte echt
  waren. Fehlt sie (häufig bei Delistings wegen schlechter Lage), bleibt die Annahme.
* `WorldSource.survivorship_free` misst: wahr, sobald beendete CRSP-Linien im Bestand
  sind; die Oberfläche sagt dann, wie viele beendete Notierungen enthalten sind.
* **Ungetestet gegen den echten Dienst**; unsicher vor allem: ob das Abo das
  Legacy-Format `dsf` noch führt (CRSP stellt auf das CIZ-Format `dsf_v2` um — dann
  meldet der Build klar, dass `crsp.dsf` fehlt, und dieser Teil muss angepasst werden),
  und wie viele noch gehandelte Linien im freien Bestand keinen Anschluss finden (der
  Build zählt sie).
* **Gemessen am 25.09.2026 (erster echter Lauf):** Der WRDS-Account der FU darf CRSP
  nicht lesen („permission denied for schema crsp_a_stock“) — die Tabellen sind
  sichtbar, die Zeilen nicht. Der Build prüft das jetzt vorab (eine Zeile lesen) und
  fällt mit klarer Meldung auf den freien US-Bestand zurück. Der US-Teil bleibt damit
  vorerst überlebendenbasiert; der Code liegt bereit, falls die Lizenz kommt.
  Compustat Global dagegen ist lesbar: 9.020 Firmen in DEU/JPN, 8,8 Mio. Tageskurse.
  Abweichende Namen dort: Jahresüberschuss `nicon`, Aktien `cshoi`, kein `gp`, im
  Quartal `ibq` statt `niq`, kein `rdq` — alle über Ausweichnamen abgefangen.
* **CRSP im aktuellen Format (CIZ)**: CRSP hat das Legacy-Format mit Dezember 2024
  eingestellt; ein aktuelles Abo hat vermutlich nur `crsp.dsf_v2` und
  `crsp.stksecurityinfohist`. Der Download nimmt CIZ, wenn lesbar, sonst Legacy. Das
  Einlesen übersetzt CIZ in die Legacy-Form: Aktienfilter wie Tidy Finance
  (`sharetype 'NS'`, `securitytype 'EQTY'`, `securitysubtype 'COM'`, US-Firma, NYSE/
  AMEX/Nasdaq); die Split-Basis wird aus der Kursrendite ohne Dividenden (`dlyretx`)
  rückgerechnet, weil CIZ keinen kumulierten Faktor führt; die Delisting-Rendite steht
  in CIZ in einer eigenen Zeile (`dlydelflg = 'Y'`) und wird von dort gelesen. Test:
  dieselben Testdaten in beiden Formaten ergeben denselben Bestand. Spaltennamen aus
  dem `tidyfinance`-Paket (PyPI) abgeglichen, das dieselben WRDS-Abfragen stellt.
* **Zweiter WRDS-Account für CRSP** (`WRDS_CRSP_USERNAME`/`_PASSWORD`), weil die FU
  CRSP nicht lizenziert; der Nutzer beantragt einen USYD-Account.
* **Länderfilter** (`country`, ISO-3 des Handelsplatzes: USA, JPN, DEU, …) im
  Screening-Rahmen beider Wege, als Filterfeld für den Chat („japanische Aktien“ →
  `country == "JPN"`) und als Auswahl „Market“ in der Seitenleiste, sobald der Bestand
  mehr als einen Markt hat. `CACHE_VERSION` → `v9` (Cache neu wärmen).
* **Gefunden beim Test dazu:** `copy_us` löschte beim Übernehmen des US-Bestands die
  Wechselkurse aus Compustat (`JPYUSD=X` …); danach wäre jede Nicht-US-Firma mit „no
  exchange rate“ ausgeschlossen worden. Ersetzt werden jetzt nur die Reihen, die der
  freie Bestand selbst liefert. Test schlug vorher fehl.
* Nebenbei behoben: `research.peers` gab bei fehlender Branche eine Tabelle ohne Spalten
  zurück, der Comps-Bereich stürzte ab.

**Verifikation 25.09.2026 (lokale Sitzung):** Der Build aus dem Zwischenlager
(`--from-dir=data/cache/wrds --us=free`) ist durchgelaufen; `data/world.duckdb` ist
4,9 GB. Gezählt: 10.914 Linien = 5.364 US (aus dem freien Bestand kopiert) + 5.550
Compustat Global + 0 CRSP (FU-Account ohne Lizenz, wie gemessen). Die Compustat-
Abdeckung ist nur für **DEU (863 Firmen, 692 handelnd) und JPN (4.663 / 4.028)**
vollständig — die anderen Standardländer kommen mit einstelligen Restzahlen (GBR 5,
FRA 3, AUT 5, …) aus Firmen mit Doppelnotiz, weil das Zwischenlager nur die
DEU/JPN-Testläufe ab 2020 enthält. Kurse laufen bis 2026-09-25 (die US-Fortschreibung
aus dem freien Bestand greift), die FRED-Serien sind mitkopiert. Rauchtest
`WorldSource.screening_frame(2026-06-30)`: 10.047 Zeilen (USA 5.305, JPN 4.031,
DEU 695), Länderfeld befüllt. Testsuite als Grundlinie: 631 grün, 1 übersprungen.
**Nächster Schritt:** voller Download aller 21 Standardländer ab 2010
(`scripts/build_wrds.py`, Duo-Freigabe nötig); abgeschlossene DEU/JPN-Jahre im
Zwischenlager werden übersprungen, 2010–2019 wird für sie nachgeholt.

**Gemessen am 26.09.2026 (voller Download, lokale Sitzung):** Der erste Lauf brach
nach ~12 Stunden bei Italien 2026 ab („SSL connection has been closed unexpectedly“
— WRDS trennt lange Verbindungen); der Neustart setzte dank des Jahr-für-Jahr-
Zwischenlagers nahtlos an der Bruchstelle auf. Ergebnis (`world.duckdb`, 10,5 GB):
**22.139 Compustat-Global-Firmen** (JPN 5.337, AUS 2.997, GBR 2.724, HKG 1.752,
DEU 1.574, …; dazu Doppelnotizen in KOR, ZAF, TWN u. a. in einstelliger Zahl),
**58,9 Mio. Compustat-Tageskurse** (74,9 Mio. mit US-Anteil), **16,0 Mio. Fakten**,
davon 13,3 Mio. (83 %) auf geschätztem Meldedatum — wie erwartet, Jahresabschlüsse
haben kein echtes in Compustat Global. **Kein einziger Kurs ohne Wechselkurs**
(`price_rows_without_rate = 0`). US-Teil: 5.364 Linien aus dem freien Bestand,
Kurse bis 25.09.2026. Rauchtest `WorldSource.screening_frame(2026-06-30)`:
18.900 Zeilen, alle 21 Länder vertreten, Länderfeld befüllt. Testsuite grün.
**CRSP:** Der USYD-Account (Zugangsdaten in `.env.local` als `WRDS_CRSP_*`)
wartet noch auf die Freischaltung der CRSP-Lizenz; danach
`scripts/build_wrds.py --countries=none` — die Prüfung `crsp.readable()` fängt
eine fehlende Lizenz ab. Bis dahin bleibt der US-Teil überlebendenbasiert
(`survivorship_free = False` für den Welt-Bestand).

**Stufe B auf dem vollen Welt-Bestand, gemessen am 27.09.2026:** Der PIT-Zwischenspeicher
wurde für den Welt-Bestand gewärmt (60 Stichtage neu gerechnet, ~64 s je Rahmen wegen der
25 Mio. Fakten, gesamt 2:39 h; `warm_pit_cache.py` folgt jetzt der gewählten Quelle).
Ein voller Lauf 2011-01-03 bis 2024-09-24 (Holdout-Regel greift, 55 von 63 Stichtagen)
mit einem Value-Screen (KGV ≤ 5, KBV ≤ 2, 30 Titel, quartalsweise) brauchte **7 Sekunden,
55/55 Cache-Treffer** — die 5-Minuten-Grenze aus Phase 8 ist auf dem vollen Bestand
eingehalten; kalt wären es ~55 Minuten. Ergebnis zur Plausibilität: 5,7 % p. a. gegen
13,8 % S&P 500 TR (−8,1 Prozentpunkte) — passt zur dokumentierten Deep-Value-Schwäche
dieses Fensters; die Rechnung schmeichelt nicht.

**Befund 01.10.2026 (lokale Sitzung) — das tägliche Update verwaist den PIT-Cache:**
Der werktägliche Launchdienst (`com.moritzfriedrich.portfoliolab.briefing`, Mo–Fr 10:00)
bringt die Kurse auf den Tagesstand. Der Cache-Schlüssel ist der Fingerabdruck des
Snapshots (Quelle, neueste Kurszeile, Zeilenzahlen, `snapshot()` in `data/free.py`) —
der ändert sich mit **jedem** Tagesupdate, also sind nach jeder Aktualisierung alle 63
Rahmen verwaist und Stufe B läuft kalt. Gemessen: Build-Kennungen `2c79ada07b` (World,
gewärmt 27.09.) und `9592b54c09` (Free, 25.09.) galten nicht mehr; die aktuellen waren
`b9b5f92eb6` / `30e55a1712`. In der Nacht auf den 02.10. wurden beide Caches neu gewärmt
(Free ~8 min, World ~2:40 h) — gültig bis zum nächsten 10-Uhr-Update.
**Offene Entscheidung (Nutzer):** (1) Morgenjob wärmt Free täglich mit (+8 min CPU),
World wöchentlich zusammen mit `build_wrds.py`; (2) World unter der Woche kalt lassen;
(3) Cache-Schlüssel entschärfen (alte Rahmen über Tagesupdates behalten — ändern sich
fast nie, Ausnahme: verspätete Nachmeldung alter Perioden; schwächt eine bewusst
konservative Schutzregel, bräuchte Test und Doku). Empfehlung der Sitzung: 1 + 2.

**Entschieden 02.10.2026 (Nutzer): 1 + 2, umgesetzt (lokale Sitzung).**
* `morning_briefing.py` wärmt nach einem erfolgreichen Kurs-Update den Free-Cache neu
  (`warm_pit_cache.py --source=free --prune`, ~8 min). Nach dem Mailversand, damit das
  Briefing nicht wartet; im `finally`, also auch wenn das Briefing scheitert oder nichts
  verfolgt wird; im Vordergrund, weil launchd beim Jobende die ganze Prozessgruppe
  beendet. Scheitert das Kurs-Update, wird nicht gewärmt — der Fingerabdruck ist dann
  unverändert, die alten Rahmen gelten weiter.
* **World wöchentlich wärmen lohnt nicht:** `build_prices.py` frischt täglich auch die
  US-Hälfte des Welt-Bestands auf (`refresh_world_store`), also ändert sich auch dessen
  Fingerabdruck jeden Werktag. Ein wöchentlicher Lauf (2:40 h) hielte nur bis zum
  nächsten Morgen. World bleibt unter der Woche kalt (Option 2); wer dort viele
  Stufe-B-Läufe plant, wärmt vorher von Hand.
* **Aufräumen:** jeder verwaiste Free-Build wiegt ~39 MB, täglich gewärmt wären das
  ~10 GB/Jahr. `pit_cache.prune()` löscht Builds, deren neueste Datei älter als 7 Tage
  ist (nach Alter, nicht „alles außer dem aktuellen“, weil das Verzeichnis nicht weiß,
  zu welcher Quelle ein Build gehört — ein zwei Tage alter World-Build kann noch gültig
  sein), dazu alte `CACHE_VERSION`-Ordner. Löschen ändert nie ein Ergebnis.
* Tests: `test_pit_cache.py` (Pruning: aktuell und jung bleibt, alt und alte Version
  geht; leerer Cache harmlos), `test_morning_briefing.py` (Befehl, kein Wärmen nach
  gescheitertem Update, Wärmen auch bei gescheitertem Briefing). Alle fünf scheitern
  ohne die Änderung. **Ungetestet:** der echte launchd-Lauf — erster Beleg ist
  `data/briefings/run.log` am nächsten Werktag (Zeile „pit cache warmed …“).

**Nebenbei:** MONITOR hat einen Umschalter Dollar ↔ Gold. Indizes und Aktien in Unzen,
Währungen in Milligramm Gold je Einheit, fremde Indizes erst zum Tageskurs in Dollar;
Renditen, Dollar-Index und Kreuzkurse bleiben (keine Währung). Die dafür nötigen
Dollarkurse (`CAD`, `BRL`, `CHF`, `JPY`, `HKD`, `CNY`, `KRW`, `INR`) speichert
`build_world.py` mit.

## Nachtrag 24.09.2026 (Cloud-Sitzung) — Papierdepots, Einzeltitel-Regeln, Chart

Auf Zuruf des Nutzers, außerhalb der Phasenliste:

* **Kerzen unter der Linie** (RESEARCH): Linie (mit optionalen Bollinger-Bändern) oben,
  dieselben Tage als Kerzen darunter, gemeinsame Zeitachse (`charts.py`).
* **Einzeltitel-Regeln** (LAB, Modus „Single stock“): neue Signale Bollinger-Rückkehr
  und -Ausbruch, RSI (Wilder), Kanal-Ausbruch (Donchian), MACD, dazu ein Trendfilter über
  einer N-Tage-Linie. Jedes Signal nachlaufend, mit Handrechen-Tests und einem
  Rückschau-Test (Zukunft umschreiben ändert kein früheres Signal). Kauf-/Verkaufsmarken
  im Chart, Vergleich gegen Buy-and-Hold desselben Titels. Kombination zweier
  Einstiegssignale ist bewusst nicht ausdrückbar und landet in `unsupported`.
* **Papierdepots** (`paper.py`, Tabellen `paper_books`/`paper_positions`): In SCREEN,
  RESEARCH, STRATEGY und LAB ein abgeschlossener Kasten „Paper money“ — Betrag wählen,
  simuliert kaufen; das Depot ist ab dann eingefroren und wird nur noch bewertet. Drei
  Arten: Halten (Kauf zum letzten Schlusskurs, Einstiegskosten einmal), Regel (LAB-Regel
  live, Füllung zur nächsten Eröffnung, Kosten je Handel, teuerste Stufe ohne
  Marktkapitalisierung), Sparplan (STRATEGY-Plan ab heute; Einmalanlage wird als
  Halte-Depot über ein Jahr Beiträge eröffnet, weil ihr Betrag sonst mit der Zeit
  wüchse). Neuer Reiter **BOOKS** vergleicht alle Depots (Summen, Rendite-auf-Einsatz-
  Kurven, Tabelle, Detail je Depot gegen den S&P 500 TR mit gleichem Geld); die alten
  SCREEN-Trackings erscheinen dort auf 10.000 gerechnet. In der laufenden App geprüft.
  Dabei gefunden und behoben: ein Klick konnte in Streamlit zwei identische Depots
  anlegen — gleiche Depots innerhalb von 30 s gelten jetzt als derselbe Klick.
* **KI-Zusammenfassung** (RESEARCH, Knopf „Write AI summary“, `summary.py`): Claude
  (`claude-opus-5`, mit serverseitigem Fallback bei einer Ablehnung) schreibt aus einem
  Datenblatt der App — Kennzahlen, Jahres- und Quartalszahlen wie zuerst gemeldet,
  Restatements, Kursverhalten, Vergleichsfirmen — eine kurze Einschätzung mit festen
  Abschnitten (Wachstum, Bilanz, Bewertung, Für, Gegen, Vor dem Handeln prüfen). Nur
  diese Zahlen, keine Empfehlung, kein Kursziel. Je Datenstand gespeichert
  (`ai_summaries`), ein zweiter Klick kostet nichts. Claude-Skills zur Aktienbewertung
  gab es nicht; sie wären auch überflüssig, weil die Zahlen aus der App selbst kommen.
  **Ungetestet gegen die echte API** (kein Schlüssel und kein Zugang in der
  Cloud-Sitzung); der Aufruf ist gegen die SDK-Signatur (anthropic 1.7.0) geprüft und
  mit einem nachgebauten Client getestet. Beim ersten echten Klick die Antwort ansehen.
  Nebenbei behoben: ohne gespeicherte Index-/Rohstoffreihen stürzte der RESEARCH-Reiter
  bei jeder Tickereingabe ab (`price_only_symbols` ohne Spalten).
* **Nicht umgesetzt: Papiergeld im OPTIONS-Reiter.** Ein Optionsdepot live zu bewerten
  braucht tägliche Optionspreise, die der Bestand nicht hat; es mit Black-Scholes und
  eingefrorener Volatilität zu bewerten, sähe wie ein Marktwert aus und wäre keiner.

## Nachtrag 24.09.2026 — Branchenfilter und Kerzenchart

**1. Branchenfilter (user).** Die 389 SEC-Bezeichnungen sind präzise und untippbar. Neu
ist `data/industry.py`: eine Tabelle von SIC-Bereichen, erster Treffer gewinnt, die auf
13 Gruppen abbildet (Technology 807 Titel, Health Care 986, Financials 774 …). Ein Test
prüft, dass kein breiter Bereich einen engeren still überschreibt. 1 Titel fällt in
„Other", 143 in „Unclassified". Der SEC-Wortlaut bleibt in `sector` und in der
Ergebnistabelle stehen — die Gruppe ist Bequemlichkeit, die Melderangabe ist das, was
gegen die Einreichung geprüft werden kann. Das Übersetzungsmodell bekommt jetzt **alle**
389 Werte statt der ersten 120 (ein abgeschnittenes Vokabular ist schlimmer als keines:
das Modell kann „fehlt in den Daten" nicht von „abgeschnitten" unterscheiden und erfindet
eine plausible Formulierung, die nichts trifft) und zusätzlich die 13 Gruppen mit dem
`in`-Operator für mehrere auf einmal. Titel ohne Branchenangabe fallen durch jeden
Branchenfilter; ihre Zahl steht jetzt als Hinweis am Ergebnis, statt lautlos zu
verschwinden.

**2. Kerzenchart mit Bollinger-Bändern (user).** Im Research-Tab, umschaltbar, Bänder
standardmäßig aus, Zeitraum 6M/1Y/5Y. Dafür brauchte es die Datenlage: `prices` kannte
nur Eröffnung und Schluss, die Kursquelle liefert Hoch und Tief seit immer und der
Ingest hat sie weggeworfen. Migrationen 010/011 ergänzen `high`/`low`, der Nachlauf am
24.09. füllte 1.636.951 von 1.842.747 Zeilen (88,8 %; die restlichen liegen außerhalb des
460-Tage-Fensters), Plausibilitätsprüfung `high ≥ close ≥ low` ohne Verletzung. Die
Kerzen werden **pro sichtbarem Zeitraum** angeboten: wo kein Tagesbereich vorliegt, gibt
es eine Linie und die Begründung dazu, keine dochtlose Kerze, die wie Daten aussieht.
Die Bänder rechnen mit der Populations-Standardabweichung (Bollingers Definition, nicht
pandas' Vorgabe — auf 20 Tagen ~2,6 % Unterschied in der Breite) auf derselben
unbereinigten Reihe, aus der die Kerzen gezeichnet werden.

**Nebenbefund, der einen Ladefehler verhindert hat:** `open_market_for_rebuild()` hat die
Migrationen übersprungen. Solange nie eine Spalte dazukam, fiel das nicht auf; der erste
Aufbau auf einer Kopie hätte gegen die alte Tabelle geschrieben und wäre gescheitert.

## Geprüft und verworfen: Kronos als Kursprognose (02.10.2026, lokale Sitzung)

**Anlass:** Ein Instagram-Reel („$24,000/year terminal vs. a free model“) stellte ein
freies Prognosemodell vor. Nach den Einblendungen (Open Source, Kerzen, „45 global
exchanges“, Fächer möglicher Pfade) ist es **Kronos** (`shiyu-coder/Kronos`, MIT,
Tsinghua, AAAI 2026; Modelle `NeoQuasar/Kronos-mini|small|base`, `large` nicht frei).
Kommentare tippten auf TimesFM (Google) — unwahrscheinlich: TimesFM ist ein allgemeines
Zeitreihenmodell, nicht auf Börsenkerzen trainiert.

**Test, außerhalb des Projekts (Zwischenordner, nichts eingebaut):** Kronos'
Vortraining endet im **Juni 2024**, also nur Prognosen ab Juli 2024. Kurse aus
`market.duckdb` (Free), Auswahl der Aktien zum 28.06.2024 (Kurs > $5, Median-Umsatz
60 T > $20 Mio., 40 zufällig, Seed 7; nur Überlebende — Free-Bestand). Pfade einzeln
gezogen (`predict_batch` mit `sample_count=1`; `predict` *mittelt* die Pfade intern).
Vergleich gegen „bleibt wie heute“ (Random Walk), „immer aufwärts“, Momentum, und ein
Band aus der 60-Tage-Volatilität (log-normal, √h).

* **Haupttest** (small, 256 T Vorlauf, 20 T Horizont, 20 Pfade, 9 Zeitpunkte
  07/2024–10/2025, 350 Prognosen): Richtung 46 % (immer aufwärts 58 %), MAE 14,4 %
  (Random Walk 7,6 %), RankIC −0,05 (t −0,7), 80-%-Band hält 23 % (Vol-Band 82 %).
* **Gegenprobe** (20 Aktien × 6 Zeitpunkte bzw. 120 künstliche Random Walks, 10 Pfade):

  | Variante | nach Anstieg: Prognose (tats.) | nach Fall | MAE Kronos / RW | 80-%-Band (Vol-Band) |
  |---|---|---|---|---|
  | Zufall, small, 256/20 | −11,2 % (−1,5 %) | +5,1 % (+1,3 %) | 11,7 / 7,1 % | 20 % (79 %) |
  | Zufall, base, 256/20 | −9,2 % (−1,5 %) | +9,9 % (+1,3 %) | 14,3 / 7,1 % | 12 % (79 %) |
  | echt, small, 400/20 | −13,3 % (+1,4 %) | +3,9 % (−4,5 %) | 13,6 / 9,3 % | 34 % (74 %) |
  | echt, small, 400/5 | −8,0 % (+0,7 %) | −0,2 % (−1,9 %) | 7,9 / 4,4 % | 39 % (79 %) |
  | echt, base, 400/20 | −11,1 % (+1,4 %) | +12,8 % (−4,5 %) | 20,2 / 9,3 % | 12 % (74 %) |
  | echt, base, 400/5 | −8,1 % (+0,7 %) | +6,8 % (−1,9 %) | 14,5 / 4,4 % | 21 % (79 %) |

**Befund:** Kronos kehrt jeden jüngsten Trend um und ist viel zu selbstsicher — auch auf
reinen Zufallskursen, wo „im Mittel unverändert“ richtig ist. Es liegt also am Modell,
nicht an unseren Daten; der Aufbau entspricht dem offiziellen Tagesbeispiel
(`examples/prediction_cn_markets_day.py`, 400 T Vorlauf, `amount = Kurs × Volumen`).
Das größere `base` ist schlechter als `small`. Kein Horizont, kein Vorlauf schlägt den
Random Walk. **Entscheidung (Nutzer, 02.10.2026): nicht einbauen.** Wer eine Bandbreite
in RESEARCH will: das Band aus der 60-Tage-Volatilität trifft sein Soll (74–82 % bei
80 %), ohne PyTorch.

**Nicht geprüft:** chinesische/asiatische Börsen und Intraday-Daten, auf denen das Paper
misst; Feinabstimmung (Fine-Tuning) auf US-Daten; Kronos-mini.
**Technik, falls je wieder:** braucht PyTorch (~770 MB Umgebung) und GPU (MPS, M4).
Kein KV-Cache — jeder Schritt rechnet den ganzen Verlauf neu: 800 Pfade auf einmal
brauchten 40–75 min je Zeitpunkt, in Paketen à 100 nur ~4 min für 1.200. `base` mit
400 T Vorlauf und Paketen à 100 belegte **13 GB** und lagerte aus (16-GB-Mac);
Pakete à 20 plus `torch.mps.empty_cache()` hielten es bei ~2,4 GB. Einen Tokenizer
nicht zwischen Geräten teilen (sein Rotary-Cache bleibt auf dem alten Gerät).

## Geplant: Hebel und Derivate (vorgemerkt am 25.09.2026, noch nicht begonnen)

Auf Frage des Nutzers („gibt es die Option für Derivate und Leverage?“) nur in den Plan
aufgenommen — **nichts davon ist gebaut**. Der Nutzer entscheidet, wann und was.

**Ist-Stand:**
* OPTIONS-Reiter: Mehrbein-Positionen aus Calls, Puts und Aktien (long/short, Vorlagen),
  Auszahlung bei Fälligkeit, Wert heute, Greeks, implizite Volatilität (selbst per
  Bisektion aus Black-Scholes), Fläche, erwartete Schwankung, Wahrscheinlichkeit über
  einem Niveau; je Portfolio gespeichert. Nur Live-Kurse von Yahoo, kein Backtest.
* Futures nur als Kursreihen (MONITOR, COMMODITIES), keine Positionen.
* Backtests (Stufe A/B), Papierdepots und LAB: nur long, voll investiert, kein Kredit,
  keine Leerverkäufe.

**Vorgemerkt, in empfohlener Reihenfolge:**

1. **Hebel in Stufe A/B, Papierdepots und LAB** (Vorschlag: zuerst).
   Faktor einstellbar (z. B. 1,0–3,0×). Kreditzins auf den geliehenen Teil = kurzer
   Zins (`^IRX`) + Aufschlag (Einstellung), täglich belastet. Wartungsmarge
   (Einstellung, z. B. 25 %): fällt das Eigenkapital darunter, Zwangsverkauf zur
   nächsten Eröffnung (R16-Logik), Kosten wie jeder Handel (R8); ein Tag unter null =
   Totalverlust, ausgewiesen. Bericht nennt Zinskosten, Anzahl Margin Calls und den
   schlechtesten Stand des Eigenkapitals. Prüfung: Hebel 1,0 = heutiges Ergebnis auf
   den Cent; Hand-Rechnung einer Reihe mit bekanntem Einbruch, die genau einen Margin
   Call auslöst.
2. **Leerverkäufe in LAB** (Einzeltitel-Regeln): Verkaufssignal eröffnet eine
   Short-Position statt nur auszusteigen. Leihgebühr p. a. (Einstellung, schwer
   leihbare Titel sind in Wahrheit teurer — nicht messbar mit freien Daten, daher als
   Annahme ausweisen), Dividenden werden dem Leerverkäufer belastet. Delisting einer
   Short-Position: Gewinn nur bis zum letzten Kurs, keine Annahme zugunsten des Nutzers.
3. **Gehebelte ETFs simulieren** (2×/3× mit täglicher Neugewichtung, Kosten p. a.):
   zeigt den Volatilitätsverlust in Seitwärtsmärkten; Vergleich gegen echte Produkte
   (z. B. `SSO`, `UPRO`) als Plausibilitätsprüfung, wo Yahoo sie führt.
4. **Backtest von Optionsstrategien.** Zwei Wege, klar getrennt ausweisen:
   * *Näherung:* Preise per Black-Scholes aus historischer Volatilität (bzw. VIX für
     den Index) — deutlich als Schätzung etikettiert (R12), ohne Skew und ohne echte
     Geld-Brief-Spannen, beides ist zu nennen.
   * *Echt:* historische Optionsdaten aus **OptionMetrics (IvyDB) über WRDS**, falls FU
     oder USYD es lizenzieren (auf der WRDS-Seite prüfen: blau = lizenziert). Dann
     eigene Datenstrecke wie `data/crsp.py`, Point-in-Time-Regeln unverändert.
5. **Futures-Positionen** (Rohstoffe, Index) mit Initial-/Wartungsmarge und Rollen der
   Kontrakte — nur, wenn 1–4 stehen; fortlaufende Kontraktreihen mit Rollrendite
   brauchen eine eigene Datenquelle, Yahoos `=F`-Reihen sind nur der Frontmonat.

**Regeln, die dafür gelten müssen:** R16 (Ausführung zur nächsten Eröffnung, auch bei
Zwangsverkauf), R8 (Kosten je Handel), R18 (Delisting), R12 (Etikett nennt jede Annahme:
Zinsaufschlag, Leihgebühr, Modellpreise), R23 (keine Empfehlung — Hebel wird gezeigt,
nicht angeraten). Die Oberfläche soll bei Hebel > 1 sichtbar machen, dass Verluste über
den Einsatz hinaus möglich sind.

## Build Phases

- [x] **Phase 1: Datenfundament, und der Beweis dass es trägt**
      **Done when:** (a) eine lokale Abfrage filtert das US-Stammaktienuniversum nach Kurs-Buchwert und Marktkapitalisierung in **unter 1 Sekunde**, Trefferzahl und Laufzeit sind notiert; (b) für 5 von Hand gewählte Firmen stimmen Buchwert, Aktienzahl und daraus gerechnetes Kurs-Buchwert-Verhältnis mit deren letztem Geschäftsbericht überein; (c) für dieselben 5 liegen bereinigte **und** unbereinigte Tageskurse vor; (d) eine Tabelle zeigt die **gemessene** XBRL-Abdeckung je Kalenderjahr, und daraus ist ein frühestes belastbares Backtest-Startdatum festgelegt und im Code hinterlegt; (e) **jedes Feld des Kennzahlen-Vokabulars hat eine nachgeprüfte Merkmalsreihenfolge und einen gemessenen Abdeckungsgrad; Felder unter 80 % Abdeckung sind als solche gekennzeichnet** `[A20]`; (f) `FixtureSource` existiert und liefert dieselbe Schnittstelle; (g) die Dauer des Erstaufbaus ist gemessen und notiert.
      **Steps:**
      - `uv init`, `git init`, `.gitignore` für `.env.local` und `data/`
      - `DataSource`-Schnittstelle definieren, `FixtureSource` mit festem Mini-Datensatz zuerst bauen
      - SEC-Gesamtpaket laden (korrekte `User-Agent`-Kennung), nach DuckDB: Kennzahl, Wert, Periode, Meldedatum, Einreichungsnummer `[A11]`
      - **Jedes Merkmal des Kennzahlen-Vokabulars** an echten Einreichungen nachprüfen, Reihenfolge festschreiben, Abdeckung je Feld messen `[A19]` `[A20]` — auch Umsatz und Cashflow, damit die Klärungskandidaten aus den Open Items später ohne neue Ladeläufe verfügbar sind
      - Universum aufbauen: US-Stammaktien, Fonds/ETFs/Zweitnotierungen ausschließen `[A9]`
      - Kurslader über `yfinance`, bereinigt und unbereinigt speichern; Stooq als Ausweich vorsehen `[A5]`
      - Abdeckung je Jahr messen, Startdatum festlegen
      - Bei Fehlschlag hier anhalten und die Ausweichoption aus `[A4]`/`[A5]`/`[A11]` ziehen, bevor weitergebaut wird
      **Covers:** Datengrundlage für R4, R13; prüft A4, A5, A9, A11, A19, A20

- [x] **Phase 2: Spezifikation und Screener, ohne Sprachmodell**
      **Done when:** eine von Hand geschriebene Spezifikation liefert 10 Titel mit allen Filterwerten; **dieselbe Testreihe läuft grün gegen `FreeSource` und gegen `FixtureSource`** — das ist der eigentliche Beweis, dass die Datenschicht austauschbar ist; zu enger Filter erzeugt den 0-Treffer-Hinweis mit Aufschlüsselung je Filter; Ausschlüsse wegen fehlender Daten, Datenalter und Liquidität werden getrennt gezählt und ausgewiesen.
      **Steps:**
      - `PortfolioSpec`: Filter, Sortierregel, Anzahl, Gewichtung, Umschichtung, Mindestkurs, Mindestvolumen, Frischegrenze
      - Screener: Spezifikation + Stichtag → sortierte Liste mit Begründungswerten
      - Frischegrenze (R14) und Liquiditätsgrenzen (R15) umsetzen
      - Sonderfälle der Tabelle oben umsetzen; Tests zuerst schreiben, sie müssen vorher fehlschlagen
      - Testreihe gegen beide Datenquellen laufen lassen
      **Covers:** R4, R5, R6, R14, R15

- [x] **Phase 3: Sprachmodell als Übersetzer**
      **Done when:** die zwei Beispielnachrichten erzeugen nacheinander die erwartete Spezifikation, die zweite ergänzt die erste; der unverständliche Teil der ersten Nachricht wird ausdrücklich als unverstanden gemeldet; ein erfundenes Kriterium wird zurückgewiesen, Spezifikation bleibt unverändert; das Schema enthält nachweislich kein Feld für Ticker; fehlender Schlüssel erzeugt eine klare Meldung. Geprüft wird in dieser Phase über ein kleines Kommandozeilen-Testprogramm, nicht über die Oberfläche.
      **Steps:**
      - Schlüssel aus `.env.local`; dem Nutzer den Befehl zum Öffnen der Datei geben, Wert nie erfragen `[A2]`
      - Werkzeug-Schema mit fest definierten Feldern, ohne Ticker-Feld
      - Zurückweisungspfad für nicht abbildbare Kriterien; Nachfragepfad für mehrdeutige (siehe Open Items)
      - Kommandozeilen-Testprogramm; Tests mit festen Modellantworten, damit sie ohne Netz laufen
      **Covers:** R1, R3, R24; prüft A2

- [x] **Phase 4: Oberfläche und mehrere Portfolios** — *gebaut und in Benutzung; die förmliche Abnahme und damit `[A3]` steht noch aus*
      **Done when:** der Nutzer startet die App mit einem Befehl, baut per Chat ein Portfolio, sieht alle aktiven Filter im Klartext, legt ein zweites an, wechselt, startet neu — beide samt Chatverlauf da; Alter des Datenbestands sichtbar. **Abnahme durch den Nutzer; hier wird `[A3]` bestätigt oder verworfen.**
      **Steps:**
      - Streamlit: Portfolioliste seitlich, Chat und Ergebnistabelle mittig
      - Speicherung in `data/user.duckdb`, getrennt vom Marktdatenbestand
      - Klartextanzeige der Spezifikation samt Grenzen und Sortierregel
      - Startanleitung für den Nutzer schreiben
      **Covers:** R2, R7

- [x] **Phase 5: Rückblick-Backtest, Kosten, Vergleichsgrafik** — *Kennzahlen-formeln erst in Phase 6 gegen analytische Reihen geprüft (R9)*
      **Done when:** Grafik Portfolio gegen S&P auf Basis 100 mit Kennzahlentabelle; **der Test nach R11 ist grün — die Indexreihe ist eine Total-Return-Reihe und ihre 10-Jahres-Rendite p. a. liegt innerhalb von 25 Basispunkten zur veröffentlichten S&P-500-TR-Zahl (60 bei `SPY`-Rückfall)**; Stufe A ist als Kaufen-und-Halten mit Umschlag 0 ausgewiesen (R16b); Warnetikett an der Grafik; Kosten wirksam und nach Größenklasse gestaffelt; jeder Lauf trägt eine Snapshot-Kennung.
      **Steps:**
      - Gestaffeltes Kostenmodell `[A10]`
      - Rückblick-Rechnung, gleichgewichtet, **Kaufen-und-Halten nach R16b** — einmaliger Kauf, Dividenden reinvestiert, keine Umschichtung; das steht so in der Oberfläche
      - Total-Return-Indexreihe laden `[A15]`, Test nach R11 schreiben
      - Dividendenbehandlung nach R17 samt Test
      - Snapshot-Kennung an `backtest_runs` (R22)
      - Grafik plus Etikett nach R12
      **Covers:** R8, R10, R11, R12, R17, R22

- [x] **Phase 6: Kennzahlen für Entscheidungen über echtes Geld**
      **Done when:** Rendite p. a., Schwankungsbreite, Sharpe, maximaler Rückgang (Tagesbasis), Abweichung zum Index und Umschlagshäufigkeit stehen für Portfolio **und** Index in der Tabelle; **jede Formel ist gegen eine synthetische Reihe geprüft, deren richtiges Ergebnis sich von Hand ausrechnen lässt** (z. B. konstant +1 % monatlich über 5 Jahre → bekannte Rendite p. a., Rückgang null; eine Reihe mit genau einem Einbruch bekannter Tiefe → bekannter maximaler Rückgang), Toleranz 1 Basispunkt; der risikolose Zins ist definiert und seine Quelle im Code benannt; die automatische Formulierungsprüfung der Oberfläche läuft grün.
      **Steps:**
      - Kennzahlenmodul, Tests gegen die synthetischen Reihen zuerst
      - Zinsreihe anbinden `[A16]`, Ersatzwert in den Einstellungen
      - Anzeige Portfolio und Index nebeneinander
      - Liste verbotener Formulierungen plus automatische Prüfung der Oberflächentexte (R23)
      **Covers:** R9, R23
      **Wichtige Phase:** Ergebnis von einem unbeteiligten Prüfer gegen genau diese Abnahmekriterien gegenlesen lassen (Kennzahlenformeln sind die häufigste stille Fehlerquelle), Befunde abarbeiten, bis er ohne Einwand durchgeht.

> **Reihenfolge-Hinweis, der Geld wert ist:** Phase 7 muss **vor** Phase 8 laufen. Ein Point-in-Time-Backtest über ein Universum, das aus den heutigen Überlebenden besteht, liefert ein zu gutes Ergebnis — und keines der Abnahmekriterien von Phase 8 würde das bemerken, weil sie nur Meldedaten und Ausführungskurse prüfen. Deshalb wird zuerst das historische Universum gebaut, dann die Umschichtungsschleife darauf gesetzt. Die unabhängige Gegenprüfung findet erst statt, wenn beide stehen.

- [x] **Phase 7: Historisches Universum und Delisting-Behandlung** — *gemessen, Ergebnis: überlebendenbasiert (0/20)*
      **Done when:** `ticker_history` ist aus der SEC-Einreichungshistorie, den Deckblatt-Tickerangaben der Einreichungen und den beobachteten Kurszeiträumen aufgebaut, **nicht** aus der heutigen Ticker-Liste; **die Wiederfindungsquote ist gemessen**: aus dem eigenen Datenbestand werden 20 CIKs gezogen, die 2014 noch einen Jahresbericht einreichten und seit 2019 nichts mehr — für wie viele davon liefert `ticker_history` einen Ticker, und für wie viele davon liegen Kurse vor? Beide Quoten stehen im Bericht und in der Oberfläche; verschwindende Titel bekommen die ausgewiesene Delisting-Rendite statt des letzten Kurses, ein Test mit simuliertem Delisting ist grün.
      **Steps:**
      - `ticker_history` aufbauen; Deckblattangabe `dei:TradingSymbol` nutzen, wo vorhanden, sonst über die Einreichungsdatei
      - Die 20er-Stichprobe aus den eigenen Daten ziehen (Regel oben, keine von Hand genannten Firmen) und beide Quoten messen
      - **Entscheidungspunkt:** liegt die Kursabdeckung der verschwundenen Titel unter 50 %, wird im Plan und in der Oberfläche festgehalten, dass Stufe B ein **überlebendenbasiertes Universum** benutzt, bis `PaidSource` da ist — und genau das steht dann im Etikett, statt eine Korrektur zu behaupten, die nicht stattfindet
      - Delisting-Rendite als Einstellung `[A17]`, Standard −30 %
      - Zählung und Anzeige nach R19
      **Covers:** R18, R19

- [ ] **Phase 8: Point-in-Time-Backtest (Fundamentaldaten)** — *drei Gegenprüfungen; alle Befunde behoben (siehe „Dritte Gegenprüfung“). Offen nur, was die lokale Datenbank braucht: 5-Minuten-Grenze, Wirkung auf die Zahlen, Häufigkeit der R13-Lecks*
      **Done when:** quartalsweise Umschichtung, die je Stichtag **das Universum aus Phase 7** und ausschließlich die bis dahin gemeldeten Bilanzdaten benutzt; **drei Tests sind grün:** ein Datensatz mit späterem Meldedatum gelangt nicht in die Auswahl; eine nachträgliche Korrektur verdrängt die ursprüngliche Fassung nicht; ein Titel, der zum Stichtag noch nicht notiert war oder schon verschwunden ist, erscheint nicht in der Auswahl. Der Test nach R16 belegt die Ausführung zur Folgetags-Eröffnung. Das Etikett nennt, welche Verzerrungen korrigiert sind und welche nicht — mit der in Phase 7 gemessenen Quote. Ein voller Durchlauf über den gesamten belastbaren Zeitraum braucht **höchstens 5 Minuten**; wird das überschritten, werden Zwischenergebnisse je Stichtag zwischengespeichert, bis es eingehalten ist.
      **Steps:**
      - Stichtagsabfrage über Meldedatum, Auswahlregel „früheste Fassung" (R13)
      - Umschichtungsschleife gegen das historische Universum, Ausführung zur Folgetags-Eröffnung, Kostenabzug (R16)
      - Laufzeit messen, gegen die 5-Minuten-Grenze prüfen
      - Tests nach R13 und R16 zuerst schreiben, sie müssen vorher fehlschlagen
      **Covers:** R13, R16
      **Wichtige Phase:** Ergebnis von einem unbeteiligten Prüfer gegen die Abnahmekriterien von Phase 7 **und** 8 zusammen gegenlesen lassen — hier entscheidet sich, ob die Zahlen etwas wert sind. Befunde abarbeiten, bis er ohne Einwand durchgeht.

- [x] **Phase 9: Empfindlichkeit, Testzähler, zurückgehaltener Zeitraum**
      **Done when:** der Bericht zeigt Startdatum ±6 Monate und das Ergebnis ohne den stärksten Einzeltitel neben dem Hauptergebnis; der Zähler „diese Spezifikation wurde N-mal auf diesen Daten getestet" steht am Ergebnis und erhöht sich nachweislich; die letzten 2 Jahre sind standardmäßig zurückgehalten und erscheinen erst nach ausdrücklicher Freischaltung, die im Lauf vermerkt wird.
      **Steps:**
      - Mehrfachläufe mit verschobenem Start und ohne den stärksten Titel
      - Zähler in `backtest_runs` über einen Fingerabdruck der Spezifikation
      - Zurückgehaltenen Zeitraum umsetzen samt Freischaltung und Vermerk
      - Ein Satz zur Überanpassung im Bericht
      **Covers:** R20, R21

- [x] **Phase 10: Umschalter auf die bezahlte Datenquelle** — *gebaut am 24.09.2026 (Cloud-Sitzung), gegen den echten Dienst ungeprüft*
      **Ergebnis:** `PORTFOLIO_LAB_SOURCE=free|paid` in `.env.local` wählt die Quelle (`Settings.data_source`, `data/sources.py`). `data/paid.py` übersetzt Sharadar TICKERS/SF1/SEP in **dasselbe Schema** in eine eigene Datei `data/paid.duckdb` (`scripts/build_paid.py`); `PaidSource` ist `FreeSource` auf dieser Datei, es gibt also keine zweite Abfragelogik, die abdriften könnte. Nur die Dimensionen ARQ/ARY (as reported) werden geladen; MR* wäre Rückschau (R13). Nur „Domestic Common Stock“. Ohne Schlüssel, ohne gebauten Bestand oder bei unbekannter Wahl erscheint in der App eine Meldung mit dem fehlenden Schritt; in der laufenden App geprüft. Die Screener-Testreihe (`tests/test_screener_contract.py`) läuft unverändert über einen im Sharadar-Layout von Hand gebauten Bestand (`tests/paid_fixture.py`), dazu 12 Tests in `tests/test_paid.py`. Anleitung im README („Switching to the paid data source“). **Offen:** Der Download (`paid.download_table`) ist ungetestet, weil es kein Abo gibt und der Container Nasdaq nicht erreicht; das Tabellenlayout stammt aus der veröffentlichten Beschreibung. Beim ersten echten Lauf die Ausgabe prüfen: die Zahl der Delistings mit Kursen muss in die Tausende gehen.
      **Done when:** eine Einstellung wählt die Datenquelle; ohne Schlüssel erscheint eine klare Meldung statt eines Absturzes; die Screener-Testreihe aus Phase 2 läuft unverändert gegen die gewählte Quelle; eine kurze Anleitung beschreibt, was der Nutzer beim Wechsel tun muss und welche Verzerrung damit wegfällt.
      **Steps:**
      - `PaidSource` gegen dieselbe Schnittstelle
      - Quellenwahl in den Einstellungen, Schlüssel aus `.env.local`
      - Testreihe gegen die neue Quelle laufen lassen
      - Anleitung schreiben
      **Covers:** Austauschbarkeit aus Q5
