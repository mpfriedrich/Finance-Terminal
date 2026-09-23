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
- **Intraday, Optionen, Leerverkäufe, Hebel** — nur Long auf Tagesschlusskursen.
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
| Sektor / Branche | Branchenschlüssel des Melders | SIC-Code aus der EDGAR-Einreichungsdatei |
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

**Geklärt in Q7 (user):** „cap to price max 1/1" heißt **price to profit**, also Kurs-Gewinn-Verhältnis ≤ 1. Das Feld ist im Kennzahlen-Vokabular bereits vorhanden; die zugehörigen XBRL-Merkmale lädt Phase 1 mit. **Oberflächensprache ist Englisch.** Hinweis ohne Handlungsbedarf: ein KGV ≤ 1 ist ein extrem enger Filter, der in vielen Quartalen null oder sehr wenige Treffer liefert — die App zeigt dafür die Aufschlüsselung, wie viele Titel jeder Filter **einzeln** übrig ließe, sodass sofort sichtbar ist, welcher Filter beißt.

**Vier Restschwächen, die der unabhängige Prüfer stehen ließ — sie sind nicht behoben, sondern benannt:**

- **Der wahrscheinlichste Ausgang von Phase 7 ist der Unter-50-%-Fall.** Der Ticker steht erst in jüngeren Einreichungen im Deckblatt, und die kostenlose Kursquelle wirft verschwundene Titel heraus. Realistisch läuft Stufe B also auf einem überlebendenbasierten Universum und sagt das auch so. **Phase 7 ist kein Versprechen, dass die Überlebensverzerrung kostenlos verschwindet** — sie misst, wie groß der Rest ist. Vollständig behoben wird sie erst durch die bezahlte Quelle (Phase 10).
- **`[A15]` und `[A16]` sind ungeprüfte Verfügbarkeitsannahmen** (Total-Return-Indexreihe über die Kursquelle, Zinsreihe ohne Schlüssel). Beide können beim ersten Lauf auf die Ersatzlösung zwingen; sichtbare Folge wäre die weitere Toleranz von 60 Basispunkten in R11.
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

## Build Phases

- [ ] **Phase 1: Datenfundament, und der Beweis dass es trägt**
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

- [ ] **Phase 2: Spezifikation und Screener, ohne Sprachmodell**
      **Done when:** eine von Hand geschriebene Spezifikation liefert 10 Titel mit allen Filterwerten; **dieselbe Testreihe läuft grün gegen `FreeSource` und gegen `FixtureSource`** — das ist der eigentliche Beweis, dass die Datenschicht austauschbar ist; zu enger Filter erzeugt den 0-Treffer-Hinweis mit Aufschlüsselung je Filter; Ausschlüsse wegen fehlender Daten, Datenalter und Liquidität werden getrennt gezählt und ausgewiesen.
      **Steps:**
      - `PortfolioSpec`: Filter, Sortierregel, Anzahl, Gewichtung, Umschichtung, Mindestkurs, Mindestvolumen, Frischegrenze
      - Screener: Spezifikation + Stichtag → sortierte Liste mit Begründungswerten
      - Frischegrenze (R14) und Liquiditätsgrenzen (R15) umsetzen
      - Sonderfälle der Tabelle oben umsetzen; Tests zuerst schreiben, sie müssen vorher fehlschlagen
      - Testreihe gegen beide Datenquellen laufen lassen
      **Covers:** R4, R5, R6, R14, R15

- [ ] **Phase 3: Sprachmodell als Übersetzer**
      **Done when:** die zwei Beispielnachrichten erzeugen nacheinander die erwartete Spezifikation, die zweite ergänzt die erste; der unverständliche Teil der ersten Nachricht wird ausdrücklich als unverstanden gemeldet; ein erfundenes Kriterium wird zurückgewiesen, Spezifikation bleibt unverändert; das Schema enthält nachweislich kein Feld für Ticker; fehlender Schlüssel erzeugt eine klare Meldung. Geprüft wird in dieser Phase über ein kleines Kommandozeilen-Testprogramm, nicht über die Oberfläche.
      **Steps:**
      - Schlüssel aus `.env.local`; dem Nutzer den Befehl zum Öffnen der Datei geben, Wert nie erfragen `[A2]`
      - Werkzeug-Schema mit fest definierten Feldern, ohne Ticker-Feld
      - Zurückweisungspfad für nicht abbildbare Kriterien; Nachfragepfad für mehrdeutige (siehe Open Items)
      - Kommandozeilen-Testprogramm; Tests mit festen Modellantworten, damit sie ohne Netz laufen
      **Covers:** R1, R3, R24; prüft A2

- [ ] **Phase 4: Oberfläche und mehrere Portfolios**
      **Done when:** der Nutzer startet die App mit einem Befehl, baut per Chat ein Portfolio, sieht alle aktiven Filter im Klartext, legt ein zweites an, wechselt, startet neu — beide samt Chatverlauf da; Alter des Datenbestands sichtbar. **Abnahme durch den Nutzer; hier wird `[A3]` bestätigt oder verworfen.**
      **Steps:**
      - Streamlit: Portfolioliste seitlich, Chat und Ergebnistabelle mittig
      - Speicherung in `data/user.duckdb`, getrennt vom Marktdatenbestand
      - Klartextanzeige der Spezifikation samt Grenzen und Sortierregel
      - Startanleitung für den Nutzer schreiben
      **Covers:** R2, R7

- [ ] **Phase 5: Rückblick-Backtest, Kosten, Vergleichsgrafik**
      **Done when:** Grafik Portfolio gegen S&P auf Basis 100 mit Kennzahlentabelle; **der Test nach R11 ist grün — die Indexreihe ist eine Total-Return-Reihe und ihre 10-Jahres-Rendite p. a. liegt innerhalb von 25 Basispunkten zur veröffentlichten S&P-500-TR-Zahl (60 bei `SPY`-Rückfall)**; Stufe A ist als Kaufen-und-Halten mit Umschlag 0 ausgewiesen (R16b); Warnetikett an der Grafik; Kosten wirksam und nach Größenklasse gestaffelt; jeder Lauf trägt eine Snapshot-Kennung.
      **Steps:**
      - Gestaffeltes Kostenmodell `[A10]`
      - Rückblick-Rechnung, gleichgewichtet, **Kaufen-und-Halten nach R16b** — einmaliger Kauf, Dividenden reinvestiert, keine Umschichtung; das steht so in der Oberfläche
      - Total-Return-Indexreihe laden `[A15]`, Test nach R11 schreiben
      - Dividendenbehandlung nach R17 samt Test
      - Snapshot-Kennung an `backtest_runs` (R22)
      - Grafik plus Etikett nach R12
      **Covers:** R8, R10, R11, R12, R17, R22

- [ ] **Phase 6: Kennzahlen für Entscheidungen über echtes Geld**
      **Done when:** Rendite p. a., Schwankungsbreite, Sharpe, maximaler Rückgang (Tagesbasis), Abweichung zum Index und Umschlagshäufigkeit stehen für Portfolio **und** Index in der Tabelle; **jede Formel ist gegen eine synthetische Reihe geprüft, deren richtiges Ergebnis sich von Hand ausrechnen lässt** (z. B. konstant +1 % monatlich über 5 Jahre → bekannte Rendite p. a., Rückgang null; eine Reihe mit genau einem Einbruch bekannter Tiefe → bekannter maximaler Rückgang), Toleranz 1 Basispunkt; der risikolose Zins ist definiert und seine Quelle im Code benannt; die automatische Formulierungsprüfung der Oberfläche läuft grün.
      **Steps:**
      - Kennzahlenmodul, Tests gegen die synthetischen Reihen zuerst
      - Zinsreihe anbinden `[A16]`, Ersatzwert in den Einstellungen
      - Anzeige Portfolio und Index nebeneinander
      - Liste verbotener Formulierungen plus automatische Prüfung der Oberflächentexte (R23)
      **Covers:** R9, R23
      **Wichtige Phase:** Ergebnis von einem unbeteiligten Prüfer gegen genau diese Abnahmekriterien gegenlesen lassen (Kennzahlenformeln sind die häufigste stille Fehlerquelle), Befunde abarbeiten, bis er ohne Einwand durchgeht.

> **Reihenfolge-Hinweis, der Geld wert ist:** Phase 7 muss **vor** Phase 8 laufen. Ein Point-in-Time-Backtest über ein Universum, das aus den heutigen Überlebenden besteht, liefert ein zu gutes Ergebnis — und keines der Abnahmekriterien von Phase 8 würde das bemerken, weil sie nur Meldedaten und Ausführungskurse prüfen. Deshalb wird zuerst das historische Universum gebaut, dann die Umschichtungsschleife darauf gesetzt. Die unabhängige Gegenprüfung findet erst statt, wenn beide stehen.

- [ ] **Phase 7: Historisches Universum und Delisting-Behandlung**
      **Done when:** `ticker_history` ist aus der SEC-Einreichungshistorie, den Deckblatt-Tickerangaben der Einreichungen und den beobachteten Kurszeiträumen aufgebaut, **nicht** aus der heutigen Ticker-Liste; **die Wiederfindungsquote ist gemessen**: aus dem eigenen Datenbestand werden 20 CIKs gezogen, die 2014 noch einen Jahresbericht einreichten und seit 2019 nichts mehr — für wie viele davon liefert `ticker_history` einen Ticker, und für wie viele davon liegen Kurse vor? Beide Quoten stehen im Bericht und in der Oberfläche; verschwindende Titel bekommen die ausgewiesene Delisting-Rendite statt des letzten Kurses, ein Test mit simuliertem Delisting ist grün.
      **Steps:**
      - `ticker_history` aufbauen; Deckblattangabe `dei:TradingSymbol` nutzen, wo vorhanden, sonst über die Einreichungsdatei
      - Die 20er-Stichprobe aus den eigenen Daten ziehen (Regel oben, keine von Hand genannten Firmen) und beide Quoten messen
      - **Entscheidungspunkt:** liegt die Kursabdeckung der verschwundenen Titel unter 50 %, wird im Plan und in der Oberfläche festgehalten, dass Stufe B ein **überlebendenbasiertes Universum** benutzt, bis `PaidSource` da ist — und genau das steht dann im Etikett, statt eine Korrektur zu behaupten, die nicht stattfindet
      - Delisting-Rendite als Einstellung `[A17]`, Standard −30 %
      - Zählung und Anzeige nach R19
      **Covers:** R18, R19

- [ ] **Phase 8: Point-in-Time-Backtest (Fundamentaldaten)**
      **Done when:** quartalsweise Umschichtung, die je Stichtag **das Universum aus Phase 7** und ausschließlich die bis dahin gemeldeten Bilanzdaten benutzt; **drei Tests sind grün:** ein Datensatz mit späterem Meldedatum gelangt nicht in die Auswahl; eine nachträgliche Korrektur verdrängt die ursprüngliche Fassung nicht; ein Titel, der zum Stichtag noch nicht notiert war oder schon verschwunden ist, erscheint nicht in der Auswahl. Der Test nach R16 belegt die Ausführung zur Folgetags-Eröffnung. Das Etikett nennt, welche Verzerrungen korrigiert sind und welche nicht — mit der in Phase 7 gemessenen Quote. Ein voller Durchlauf über den gesamten belastbaren Zeitraum braucht **höchstens 5 Minuten**; wird das überschritten, werden Zwischenergebnisse je Stichtag zwischengespeichert, bis es eingehalten ist.
      **Steps:**
      - Stichtagsabfrage über Meldedatum, Auswahlregel „früheste Fassung" (R13)
      - Umschichtungsschleife gegen das historische Universum, Ausführung zur Folgetags-Eröffnung, Kostenabzug (R16)
      - Laufzeit messen, gegen die 5-Minuten-Grenze prüfen
      - Tests nach R13 und R16 zuerst schreiben, sie müssen vorher fehlschlagen
      **Covers:** R13, R16
      **Wichtige Phase:** Ergebnis von einem unbeteiligten Prüfer gegen die Abnahmekriterien von Phase 7 **und** 8 zusammen gegenlesen lassen — hier entscheidet sich, ob die Zahlen etwas wert sind. Befunde abarbeiten, bis er ohne Einwand durchgeht.

- [ ] **Phase 9: Empfindlichkeit, Testzähler, zurückgehaltener Zeitraum**
      **Done when:** der Bericht zeigt Startdatum ±6 Monate und das Ergebnis ohne den stärksten Einzeltitel neben dem Hauptergebnis; der Zähler „diese Spezifikation wurde N-mal auf diesen Daten getestet" steht am Ergebnis und erhöht sich nachweislich; die letzten 2 Jahre sind standardmäßig zurückgehalten und erscheinen erst nach ausdrücklicher Freischaltung, die im Lauf vermerkt wird.
      **Steps:**
      - Mehrfachläufe mit verschobenem Start und ohne den stärksten Titel
      - Zähler in `backtest_runs` über einen Fingerabdruck der Spezifikation
      - Zurückgehaltenen Zeitraum umsetzen samt Freischaltung und Vermerk
      - Ein Satz zur Überanpassung im Bericht
      **Covers:** R20, R21

- [ ] **Phase 10: Umschalter auf die bezahlte Datenquelle**
      **Done when:** eine Einstellung wählt die Datenquelle; ohne Schlüssel erscheint eine klare Meldung statt eines Absturzes; die Screener-Testreihe aus Phase 2 läuft unverändert gegen die gewählte Quelle; eine kurze Anleitung beschreibt, was der Nutzer beim Wechsel tun muss und welche Verzerrung damit wegfällt.
      **Steps:**
      - `PaidSource` gegen dieselbe Schnittstelle
      - Quellenwahl in den Einstellungen, Schlüssel aus `.env.local`
      - Testreihe gegen die neue Quelle laufen lassen
      - Anleitung schreiben
      **Covers:** Austauschbarkeit aus Q5
