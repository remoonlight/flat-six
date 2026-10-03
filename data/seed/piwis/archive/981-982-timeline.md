# 981 / 982 关联规则表的修订时间线

共享规则表可能包含 991 等分支；整表修订日期不是每一个 981/982 固件目标的发布日期。下面保留修订描述原文与表版本，固件发布日期尚未核实。

| 规则来源 | 日期原文 | 日期 ISO（可解析时） | 表版本 | 修订描述原文 |
|---|---|---|---|---|
| flash-rules/VERSTAERKER.xml | 18.07.2011 | 2011-07-18 | 1 | 未记录 |
| flash-rules/AIRBAG_9x1.xml | 18.09.2012 | 2012-09-18 | 1 | In neuen Fredo übernommen |
| DME-flash-rules.xml | 22.10.2012 | 2012-10-22 | 1 | neue China PDK Datensätze, neue 981 Basis MT Datensätze (Getriebeschutz-Standdrehzahl auf 5000U/min korrigiert), neue 991 Kit MT Datensätze (Korrektur Leerlaufreglerbedatung zur Vermeidung von E-Gas-Fehlern bei Kriechbetrieb mit kaltem Motor) |
| GETRIEBE-flash-rules.xml | 30.10.2012 | 2012-10-30 | 1 | 未记录 |
| flash-rules/VERSTAERKER.xml | 30.10.2012 | 2012-10-30 | 2 | Neu Eingepflegt |
| DME-flash-rules.xml | 05.12.2012 | 2012-12-05 | 2 | 981 China Datensätze |
| DME-flash-rules.xml | 02.05.2013 | 2013-05-02 | 3 | Update 981 PAP USA MJ D + TOP Datensätze +TOP S Datensätze |
| GETRIEBE-flash-rules.xml | 21.06.2013 | 2013-06-21 | 2 | Neue Carrera Stände |
| GETRIEBE-flash-rules.xml | 21.06.2013 | 2013-06-21 | 3 | Turbo und Turbo S neu |
| flash-rules/VERSTAERKER.xml | 01.07.2013 | 2013-07-01 | 3 | Update Cayman Bose |
| DME-flash-rules.xml | 24.07.2013 | 2013-07-24 | 4 | NEUE TOP/S Stände Serie |
| GETRIEBE-flash-rules.xml | 24.07.2013 | 2013-07-24 | 4 | Update |
| GETRIEBE-flash-rules.xml | 24.07.2013 | 2013-07-24 | 5 | Update Turbo/Turbo S/GT3 |
| flash-rules/AIRBAG_9x1.xml | 25.07.2013 | 2013-07-25 | 2 | Erweiterung auf 991 TOP und 991 GT3 Achtung: in Dimensions ist eine manuell geänderte Version 2 freigegeben. Grund dafür ist, daß zu dem Zeitpunkt der Export als XML nicht korrekt funktionierte. Inhaltlich stimmt jedoch die in Dimensions freigegebene Version mit dieser überein. |
| GETRIEBE-flash-rules.xml | 19.08.2013 | 2013-08-19 | 6 | Alles auf Index 15 |
| DME-flash-rules.xml | 13.09.2013 | 2013-09-13 | 5 | Neue TOP LEV2 Stände (OBD-Kennung, P-Code, Kurztest) |
| DME-flash-rules.xml | 13.09.2013 | 2013-09-13 | 6 | GT3: USA Erster Seriendatensatz GT3: Rest 1. Korrektur mit Robustheitsmassnahmen verchiedener Diagnosen TOP: Akustik, StartStop und verschiedene Bugfix; zusätzliche China-Datensätze |
| DME-flash-rules.xml | 16.09.2013 | 2013-09-16 | 7 | Gesetzkonforme Hochschaltanzeige, NVLD-Korrektur, verschiedene Bugfixe |
| DME-flash-rules.xml | 22.11.2013 | 2013-11-22 | 8 | Programmstandsänderung wegen Motorakustik bei offenem Verdeck und Zurückrollen bei Start/Stop-Betrieb |
| DME-flash-rules.xml | 28.11.2013 | 2013-11-28 | 9 | Korrektur Öltemperatur-Plausibilitäts-Diagnose |
| flash-rules/AIRBAG_9x1.xml | 14.01.2014 | 2014-01-14 | 3 | Erweiterung auf Targa, d.h. Ergänzung der Targaproduktschlüssel zu den C4-Regeln |
| flash-rules/AIRBAG_9x1.xml | 27.02.2014 | 2014-02-27 | 4 | Erweiterung um neuen HW/SW-Stand für Modelljahr F und folgende |
| DME-flash-rules.xml | 03.03.2014 | 2014-03-03 | 10 | Korrektur Fehlercodes / Fehlerklassen USA |
| DME-flash-rules.xml | 03.03.2014 | 2014-03-03 | 11 | GT3 Datensätze |
| DME-flash-rules.xml | 28.03.2014 | 2014-03-28 | 12 | GT3 Datensätze II |
| GETRIEBE-flash-rules.xml | 28.03.2014 | 2014-03-28 | 7 | TOP / S auf Index 16 |
| GETRIEBE-flash-rules.xml | 28.03.2014 | 2014-03-28 | 8 | Index erhöht/GT3 RS eingefügt |
| DME-flash-rules.xml | 15.04.2014 | 2014-04-15 | 13 | MJF Änderung Sportabgasanlage (nicht rückwärtskompatibel), neue GT3 Datensätze |
| DME-flash-rules.xml | 29.04.2014 | 2014-04-29 | 14 | Korrektur ACC/StartStop - E-Gasfehler |
| DME-flash-rules.xml | 07.10.2014 | 2014-10-07 | 15 | GT3 Nmax-Reduktion bei kaltem Motor, Öldruckadaption über Lebenszeit. KIT Emotionalisierung |
| DME-flash-rules.xml | 13.10.2014 | 2014-10-13 | 16 | Emotionalisierung für GTS und Reintegration 991 KIT |
| DME-flash-rules.xml | 27.10.2014 | 2014-10-27 | 17 | Verbesserung Race-Start-Fahrleistung aller PDK-Varianten |
| GETRIEBE-flash-rules.xml | 27.10.2014 | 2014-10-27 | 10 | Abgrenzung der KIT/GTS Varianten |
| GETRIEBE-flash-rules.xml | 27.10.2014 | 2014-10-27 | 9 | Anpassung Dämpfungskennfeld des virt. Fahrpedals |
| GETRIEBE-flash-rules.xml | 08.12.2014 | 2014-12-08 | 11 | Akustikbeanstandungen |
| DME-flash-rules.xml | 14.01.2015 | 2015-01-14 | 18 | Korrektur Ösltandmessung 981 GTS |
| DME-flash-rules.xml | 14.01.2015 | 2015-01-14 | 19 | Korrektur Ölstandmessung 981/S |
| GETRIEBE-flash-rules.xml | 21.01.2015 | 2015-01-21 | 12 | GT3 / GT3 RS Aktualisiert |
| DME-flash-rules.xml | 28.01.2015 | 2015-01-28 | 20 | erste Seriendatensätze GT4 MJ G |
| GETRIEBE-flash-rules.xml | 28.01.2015 | 2015-01-28 | 13 | 未记录 |
| flash-rules/AIRBAG_9x1.xml | 29.01.2015 | 2015-01-29 | 5 | Erweiterung um GT4 |
| DME-flash-rules.xml | 13.03.2015 | 2015-03-13 | 21 | GT4: Korrektur KAT-Schutz |
| flash-rules/SCHEINWERFER_LED_LINKS_9X1_SW.xml | 20.03.2015 | 2015-03-20 | 1 | 未记录 |
| flash-rules/SCHEINWERFER_LED_RECHTS_9X1_SW.xml | 20.03.2015 | 2015-03-20 | 1 | 未记录 |
| DME-flash-rules.xml | 27.03.2015 | 2015-03-27 | 22 | GT3 RS erste Seriendatensatz |
| DME-flash-rules.xml | 04.05.2015 | 2015-05-04 | 23 | GT3(USA: Cylinder Inbalance-Diagnose hinzu (MJ G-Anforderung) alle: verschiedene Bugfix) GT4(Seriendatensätze 981 GT4 MJ G:  - Korrektur bezüglich Nmax-Begrenzung bei schnellen Schaltvorgängen) |
| flash-rules/VERSTAERKER.xml | 04.05.2015 | 2015-05-04 | 4 | GT3 RS hinzugefügt |
| DME-flash-rules.xml | 18.05.2015 | 2015-05-18 | 24 | erste Seriendatensätze 981S Spyder MJ G |
| DME-flash-rules.xml | 28.07.2015 | 2015-07-28 | 25 | 981S Spyder Vmax |
| GETRIEBE-flash-rules.xml | 17.08.2015 | 2015-08-17 | 14 | Rennstart ohne aktive Bremsregelsysteme 991 GT3/GT3RS |
| DME-flash-rules.xml | 08.09.2015 | 2015-09-08 | 26 | GT4 Korrektur KAT-Schutz im Schubhieb. 991II Erste Seriendatensätze. |
| GETRIEBE-flash-rules.xml | 28.09.2015 | 2015-09-28 | 15 | 991 II SOP Staende und Abgrenzung |
| flash-rules/AIRBAG_9x1.xml | 06.10.2015 | 2015-10-06 | 6 | Erweiterung 981 Spyder |
| GETRIEBE-flash-rules.xml | 17.11.2015 | 2015-11-17 | 16 | GT4 CS aufgenommen |
| DME-flash-rules.xml | 23.11.2015 | 2015-11-23 | 27 | 981 Korrektur Anzeigekonzept |
| DME-flash-rules.xml | 25.11.2015 | 2015-11-25 | 28 | RDW Bugfix |
| DME-flash-rules.xml | 25.11.2015 | 2015-11-25 | 29 | 981 GT4 Clubsport aufgenommen |
| DME-flash-rules.xml | 14.12.2015 | 2015-12-14 | 30 | 991II Turbo eingefügt |
| DME-flash-rules.xml | 19.01.2016 | 2016-01-19 | 31 | Korrektur der Lambdasondendiagnose der USA-Varianten |
| DME-flash-rules.xml | 27.01.2016 | 2016-01-27 | 32 | 982 Serien Stände, neue Spyder Stände, neue GT4 Stände |
| flash-rules/SCHEINWERFER_LINKS.xml | 03.02.2016 | 2016-02-03 | 1 | 未记录 |
| flash-rules/SCHEINWERFER_RECHTS.xml | 03.02.2016 | 2016-02-03 | 1 | 未记录 |
| DME-flash-rules.xml | 02.03.2016 | 2016-03-02 | 33 | lev stände 981/991II Turbo |
| GETRIEBE-flash-rules.xml | 04.03.2016 | 2016-03-04 | 17 | 991II PDK Bugfix |
| flash-rules/AIRBAG_9x1.xml | 14.03.2016 | 2016-03-14 | 7 | GT4 CS Aufgenommen |
| DME-flash-rules.xml | 16.03.2016 | 2016-03-16 | 34 | GT3 991 update |
| flash-rules/SCHEINWERFER_LED_LINKS_9X1_DS.xml | 21.03.2016 | 2016-03-21 | 1 | 未记录 |
| flash-rules/SCHEINWERFER_LED_RECHTS_9X1_DS.xml | 21.03.2016 | 2016-03-21 | 1 | 未记录 |
| flash-rules/AIRBAG_9x1.xml | 23.03.2016 | 2016-03-23 | 8 | Änderungen für 991II / 982 |
| DME-flash-rules.xml | 25.04.2016 | 2016-04-25 | 35 | 981 GT4 Clubsport KW16 Änderungen |
| DME-flash-rules.xml | 26.04.2016 | 2016-04-26 | 36 | KW22 Umfänge |
| GETRIEBE-flash-rules.xml | 26.04.2016 | 2016-04-26 | 18 | KW22 Änderungen |
| flash-rules/VERSTAERKER.xml | 17.05.2016 | 2016-05-17 | 5 | Boxster Spyder eingefügt |
| DME-flash-rules.xml | 23.05.2016 | 2016-05-23 | 37 | 991R eingefügt |
| DME-flash-rules.xml | 04.08.2016 | 2016-08-04 | 38 | KW22/16 MOPF |
| DME-flash-rules.xml | 04.08.2016 | 2016-08-04 | 39 | I-Nummer EU6 TOPS angepasst |
| GETRIEBE-flash-rules.xml | 16.09.2016 | 2016-09-16 | 19 | Clubsport neue SW |
| DME-flash-rules.xml | 29.09.2016 | 2016-09-29 | 40 | 982 MOPF, Einsatz KW43 /2016, MJ H  VR12.5 |
| GETRIEBE-flash-rules.xml | 11.10.2016 | 2016-10-11 | 20 | A329 Stände 991II/982 |
| DME-flash-rules.xml | 18.10.2016 | 2016-10-18 | 41 | GTS eingefügt |
| flash-rules/AIRBAG_9x1.xml | 25.10.2016 | 2016-10-25 | 10 | 未记录 |
| flash-rules/AIRBAG_9x1.xml | 25.10.2016 | 2016-10-25 | 9 | Einführung Index 09 |
| DME-flash-rules.xml | 30.11.2016 | 2016-11-30 | 42 | LEV2 Stände 991II Basis aktuallisiert |
| DME-flash-rules.xml | 06.03.2017 | 2017-03-06 | 43 | GT3 hinzugefügt |
| GETRIEBE-flash-rules.xml | 08.03.2017 | 2017-03-08 | 21 | GT3II hinzugefügt |
| DME-flash-rules.xml | 22.03.2017 | 2017-03-22 | 44 | GT4 CS update |
| DME-flash-rules.xml | 27.03.2017 | 2017-03-27 | 45 | GT4 CS entfernt |
| GETRIEBE-flash-rules.xml | 27.03.2017 | 2017-03-27 | 22 | CS entfernt |
| GETRIEBE-flash-rules.xml | 02.05.2017 | 2017-05-02 | 23 | VR14 Stände eingepflegt |
| DME-flash-rules.xml | 04.05.2017 | 2017-05-04 | 46 | VR13 |
| DME-flash-rules.xml | 13.09.2017 | 2017-09-13 | 47 | hinzufügen TOP S Excl |
| GETRIEBE-flash-rules.xml | 13.09.2017 | 2017-09-13 | 24 | hinzufügen TOP S Excl |
| GETRIEBE-flash-rules.xml | 27.09.2017 | 2017-09-27 | 25 | GT2 RS hinzugefügt |
| DME-flash-rules.xml | 11.10.2017 | 2017-10-11 | 48 | 911II GT2RS hinzugefügt |
| flash-rules/AIRBAG_9x1.xml | 18.10.2017 | 2017-10-18 | 11 | 未记录 |
| flash-rules/AIRBAG_9x1.xml | 18.10.2017 | 2017-10-18 | 12 | 未记录 |
| flash-rules/AIRBAG_9x1.xml | 18.10.2017 | 2017-10-18 | 13 | 991II GT2 RS und 991II GT3 RS für NAR entfernt. Die Datensätze müssen für diese Modelle geändert werden. Der frühzeitige Einsatz der Flashregel verhindert, dass nach Point Of Sales und leicht veraltetem Testerrelease beim Händler falsche Datensätze im Falle einer Reparatur des Airbag-Steuergeräts verwendet werden. |
| DME-flash-rules.xml | 23.11.2017 | 2017-11-23 | 49 | 991II GT3 MT (ohne Touring), GT3 LEV2 PDK hinzugefügt |
| DME-flash-rules.xml | 23.11.2017 | 2017-11-23 | 50 | 982 GTS |
| GETRIEBE-flash-rules.xml | 23.11.2017 | 2017-11-23 | 26 | GT2RS aktualisiert |
| DME-flash-rules.xml | 19.03.2018 | 2018-03-19 | 51 | 991II GT2 China Stand |
| GETRIEBE-flash-rules.xml | 19.03.2018 | 2018-03-19 | 27 | 991.II GT3RS hinzugefügt |
| DME-flash-rules.xml | 13.08.2018 | 2018-08-13 | 52 | 991II GT3 RS hinzugefügt |
| GETRIEBE-flash-rules.xml | 14.08.2018 | 2018-08-14 | 28 | OPF hinzugefügt |
| DME-flash-rules.xml | 27.08.2018 | 2018-08-27 | 53 | 未记录 |
| DME-flash-rules.xml | 27.08.2018 | 2018-08-27 | 54 | GT3 Touring |
| DME-flash-rules.xml | 27.08.2018 | 2018-08-27 | 55 | 未记录 |
| DME-flash-rules.xml | 27.08.2018 | 2018-08-27 | 56 | neue Stände GT3/GT3 RS |
| DME-flash-rules.xml | 10.10.2018 | 2018-10-10 | 57 | 未记录 |
| flash-rules/SCHEINWERFER_LED_LINKS_9X1_DS.xml | 17.12.2018 | 2018-12-17 | 2 | mehrere Fahrzeugmodelltypen hinzugefügt |
| flash-rules/SCHEINWERFER_LED_RECHTS_9X1_DS.xml | 17.12.2018 | 2018-12-17 | 2 | mehrere Fahrzeugmodelltypen hinzugefügt |
| DME-flash-rules.xml | 18.12.2018 | 2018-12-18 | 58 | MJ Abgrenzung 991S hinzugefügt |
| flash-rules/AIRBAG_9x1.xml | 18.12.2018 | 2018-12-18 | 14 | CS1 Produktschlüsseln hinzugefügt GT2 RS und GT3 RS wieder hinzugefügt |
| DME-flash-rules.xml | 01.02.2019 | 2019-02-01 | 59 | 991II GT2 ULEV PR-Nummer geändert, da fehlerhaft |
| DME-flash-rules.xml | 09.04.2019 | 2019-04-09 | 60 | 未记录 |
| DME-flash-rules.xml | 09.04.2019 | 2019-04-09 | 61 | EU6DG Stände 982 |
| DME-flash-rules.xml | 03.05.2019 | 2019-05-03 | 62 | neue Speedster und GT3 RS Stände |
| DME-flash-rules.xml | 23.05.2019 | 2019-05-23 | 63 | 未记录 |
| DME-flash-rules.xml | 23.05.2019 | 2019-05-23 | 64 | 未记录 |
| DME-flash-rules.xml | 23.05.2019 | 2019-05-23 | 65 | 未记录 |
| DME-flash-rules.xml | 23.05.2019 | 2019-05-23 | 65 | 未记录 |
| DME-flash-rules.xml | 23.05.2019 | 2019-05-23 | 66 | 未记录 |
| DME-flash-rules.xml | 05.06.2019 | 2019-06-05 | 67 | 未记录 |
| DME-flash-rules.xml | 08.07.2019 | 2019-07-08 | 68 | 982 GT4 SOP Stände |
| flash-rules/LL_EnginContrModul1UDS.xml | 03.09.2019 | 2019-09-03 | 1 | Initiale Version |
| flash-rules/E5K1G.xml | 18.10.2019 | 2019-10-18 | 1 | 未记录 |
| DME-flash-rules.xml | 13.02.2020 | 2020-02-13 | 69 | 982 C6B Stände |
| GETRIEBE-flash-rules.xml | 13.02.2020 | 2020-02-13 | 29 | 982 C6B Stände |
| flash-rules/LL_EnginContrModul1UDS.xml | 12.03.2020 | 2020-03-12 | 2 | EU6W und USA hinzugefügt |
| flash-rules/AIRBAG_9x1.xml | 20.03.2020 | 2020-03-20 | 15 | KW34/20 |
| GETRIEBE-flash-rules.xml | 29.03.2020 | 2020-03-29 | 30 | 991II GT3 RS EU6DG hinzugefügt |
| flash-rules/LL_EnginContrModul1UDS.xml | 29.03.2020 | 2020-03-29 | 3 | GT3RS/Speedster hinzugefügt |
| DME-flash-rules.xml | 27.04.2020 | 2020-04-27 | 70 | LL_EnginContrModul1UDS in DME.xml |
| DME-flash-rules.xml | 29.07.2020 | 2020-07-29 | 71 | 982 MOPF 34/20 Stände hinzugefügt |
| GETRIEBE-flash-rules.xml | 29.07.2020 | 2020-07-29 | 31 | 982 MOPF 34/20 Stände hinzugefügt |
| DME-flash-rules.xml | 12.10.2020 | 2020-10-12 | 72 | 991II GT3 EU4 PDK angepasst, Aufnahme 172 |
| DME-flash-rules.xml | 03.12.2020 | 2020-12-03 | 73 | GTS 4.0 EU6W PDK |
| GETRIEBE-flash-rules.xml | 03.12.2020 | 2020-12-03 | 32 | 982 GTS 4.0 EU6W PDK |
| DME-flash-rules.xml | 07.12.2020 | 2020-12-07 | 74 | Basis/S ULEV PDK |
| GETRIEBE-flash-rules.xml | 07.12.2020 | 2020-12-07 | 33 | 982 Basis/S ULEV PDK |
| DME-flash-rules.xml | 17.12.2020 | 2020-12-17 | 75 | GTS 4.0 MT Nachlieferung |
| GETRIEBE-flash-rules.xml | 13.01.2021 | 2021-01-13 | 34 | 982 GTS4.0/Spyder/GT4 ULEV PDK |
| DME-flash-rules.xml | 18.03.2021 | 2021-03-18 | 76 | 982 Basis/S PDK/MT MJ L |
| GETRIEBE-flash-rules.xml | 18.03.2021 | 2021-03-18 | 35 | 982 BASIS/S NAR PDK |
| DME-flash-rules.xml | 26.04.2021 | 2021-04-26 | 77 | 982 MOPF25/21 MJ N + SOP 982 Spyder 2.0 CN |
| DME-flash-rules.xml | 28.04.2021 | 2021-04-28 | 78 | 982 Basis/T/S/GTS C6b + Löschung v77 |
| flash-rules/AIRBAG_9x1.xml | 29.04.2021 | 2021-04-29 | 16 | Korrektur 991II Top GT3 NAR Index 11 ohne GT2RS und GT3RS |
| GETRIEBE-flash-rules.xml | 08.06.2021 | 2021-06-08 | 36 | 991 S Doppelte TNR ver-eindeutigt (99161898219) |
| DME-flash-rules.xml | 10.06.2021 | 2021-06-10 | 79 | 982 MOPF 25/21 FGK 10.06.2021 |
| GETRIEBE-flash-rules.xml | 10.06.2021 | 2021-06-10 | 37 | MOPF25/21 - FGK 10.06.2021 |
| DME-flash-rules.xml | 28.06.2021 | 2021-06-28 | 80 | 982 MOPF 25/21 FGK 24.06.2021 |
| DME-flash-rules.xml | 09.07.2021 | 2021-07-09 | 81 | Korrektur MJ-Syntax |
| GETRIEBE-flash-rules.xml | 20.07.2021 | 2021-07-20 | 37 | Aufnahme Alt-Stände 982 |
| DME-flash-rules.xml | 27.09.2021 | 2021-09-27 | 82 | FGK 981I/991I von 15.9.2021 |
| GETRIEBE-flash-rules.xml | 05.10.2021 | 2021-10-05 | 38 | Aufnahme 991I NAR Stände |
| GETRIEBE-flash-rules.xml | 26.10.2021 | 2021-10-26 | 39 | Aufnahme 981 S Stände FGK 25.10. |
| DME-flash-rules.xml | 26.11.2021 | 2021-11-26 | 83 | FGK 23.11 MOPF48/21 |
| DME-flash-rules.xml | 29.11.2021 | 2021-11-29 | 83 | FGK 23.11 MOPF48/21 Nachtrag KD-Stände |
| flash-rules/AIRBAG_9x1.xml | 30.11.2021 | 2021-11-30 | 17 | Aufnahme Produktschlüssel GT2RS CS, 981/982 GT4CS, GT2RS CS bei NAR entfernt |
| GETRIEBE-flash-rules.xml | 01.12.2021 | 2021-12-01 | 40 | Aufnahme 982 MOPF48/21 FGK 23.11. Umsetzung CCB 9732 noch nicht in FGK bestätigt. |
| flash-rules/GATEWAY.xml | 16.12.2021 | 2021-12-16 | 1 | Erstbefüllung 9x1/982 GW |
| DME-flash-rules.xml | 13.01.2022 | 2022-01-13 | 84 | Nachtrag FGK 25.06.2021 |
| GETRIEBE-flash-rules.xml | 01.02.2022 | 2022-02-01 | 41 | Aufnahme 981 Basis NAR/991 S Kit NAR FGK 27.01.2022 |
| GETRIEBE-flash-rules.xml | 10.02.2022 | 2022-02-10 | 42 | 991 S Doppelte TNRp erneut ver-eindeutigt (99161898219 |
| DME-flash-rules.xml | 11.02.2022 | 2022-02-11 | 85 | 982 GT4RS SOP FGK 11.02.2022 |
| DME-flash-rules.xml | 11.02.2022 | 2022-02-11 | 85 | 991 S Kit NAR FGK 27.01.2022 |
| GETRIEBE-flash-rules.xml | 11.02.2022 | 2022-02-11 | 43 | 982 GT4RS SOP FGK 11.02.2022 |
| DME-flash-rules.xml | 19.04.2022 | 2022-04-19 | 86 | 982 GTS 4.0 DME_UDS -> LL_EnginContrModul1UDS |
| DME-flash-rules.xml | 26.04.2022 | 2022-04-26 | 87 | 982 GTS 4.0 DME_UDS -> LL_EnginContrModul1UDS bei allen M1R PR-Nummern |
| GETRIEBE-flash-rules.xml | 03.05.2022 | 2022-05-03 | 75 | 982 GT4RS PDK TNr.: 07-->08; 982S TNr.:09-->17 (nur NAR) FGK 03.06.22 |
| DME-flash-rules.xml | 08.06.2022 | 2022-06-08 | 113 | GT Umfang FGK 07.06.22; Versionierung gemäß Dimensions angepasst |
| DME-flash-rules.xml | 04.07.2022 | 2022-07-04 | 114 | 982 S NAR Umfang FGK DG -> DJ 03.06.22 |
| GETRIEBE-flash-rules.xml | 08.07.2022 | 2022-07-08 | 76 | 982 GT4RS MJ P Ohne NAR/BRA FGK 03.06.22 |
| GETRIEBE-flash-rules.xml | 13.07.2022 | 2022-07-13 | 77 | 982 Index 15(10) aufgrund fehlender FGK durch Index 03 ersetzt; 982 S EU5/ULEV125 und 982 S EU5; 982 S EU5/ULEV125 35613 in 35517 |
| DME-flash-rules.xml | 07.09.2022 | 2022-09-07 | 115 | 982 Umfänge aus FGK vom 25.08.2022 + MOPF 28/22 Stände +  MOPF 28/22 ReInte FGK 07.09.2022 + GT4RS auf MJ P erweitert |
| GETRIEBE-flash-rules.xml | 07.09.2022 | 2022-09-07 | 78 | Anpassung aus FGK vom 25.08.2022 + MOPF 28/22 Stände + MOPF 28/22 ReInte FGK 07.09.2022 |
| DME-flash-rules.xml | 16.09.2022 | 2022-09-16 | 116 | Fehlebehebung GTS 4.0 034.AF: DME_UDS --> LL_EnginContrModul1UDS |
| DME-flash-rules.xml | 20.09.2022 | 2022-09-20 | 117 | Fehlebehebung 982 GT4/Spyder MT ULEV: M-Nummer 7CE hinzugefügt |
| DME-flash-rules.xml | 22.09.2022 | 2022-09-22 | 118 | Fehlebehebung 982 4.0 GTS ULEV: Doppeldeutigkeit |
| flash-rules/AIRBAG_9x1.xml | 23.09.2022 | 2022-09-23 | 29 | 991II GT3RS NAR PTNR entfernt + PSL F83TA bei SpyderRS Regel hinzugefügt |
| flash-rules/AIRBAG_9x1.xml | 26.10.2022 | 2022-10-26 | 30 | PSL F83EA in Spyder- Zeile ergänzt (HW TNR.: 201.11) |
| DME-flash-rules.xml | 09.12.2022 | 2022-12-09 | 119 | Neue GT4RS Stände zu MOPF48/22 |
| GETRIEBE-flash-rules.xml | 22.02.2023 | 2023-02-22 | 79 | Neue Stände GTS4.0 EU6W und EU6AP aus der FGK vom 14.02.2023 |
| GETRIEBE-flash-rules.xml | 05.04.2023 | 2023-04-05 | 80 | Neuer GT3 RS ULEV Stand aus FGK von 29.03 |
| GETRIEBE-flash-rules.xml | 14.04.2023 | 2023-04-14 | 81 | Fehlerbehebung: Entfernen unvollständiger Arbeitsverbünde |
| GETRIEBE-flash-rules.xml | 12.05.2023 | 2023-05-12 | 82 | Neue KD Stände, Nachlieferung unvollständiger Antriebsverbünde |
| DME-flash-rules.xml | 15.05.2023 | 2023-05-15 | 120 | Neue KD Stände Antriebsverbünde |
| GETRIEBE-flash-rules.xml | 11.07.2023 | 2023-07-11 | 83 | Neue 991 Stände |
| DME-flash-rules.xml | 19.07.2023 | 2023-07-19 | 121 | MOPF 25/23 Stände FGK vom 17.05.2023 |
| GETRIEBE-flash-rules.xml | 15.08.2023 | 2023-08-15 | 84 | PR Nummer 708 zur GT3 991 --> 991II Unterscheidung ergänzt |
| GETRIEBE-flash-rules.xml | 16.08.2023 | 2023-08-16 | 85 | Nachtrag MOPF25/23 SpyderRS Stände |
| DME-flash-rules.xml | 18.08.2023 | 2023-08-18 | 122 | PR Nummer 708 zur GT3 991 --> 991II Unterscheidung ergänzt |
| DME-flash-rules.xml | 23.08.2023 | 2023-08-23 | 123 | Fieldfix DME Reintegration MOPF 25/23 FGK 18.08.2023 |
| DME-flash-rules.xml | 28.09.2023 | 2023-09-28 | 124 | Fehlerbehebung Basis ULEV PDK MJ |
| flash-rules/SCHEINWERFER_LED_LINKS_9X1_DS.xml | 25.03.2024 | 2024-03-25 | 3 | Fahrzeugschlüssel F83TA zu P203 hinzugefügt |
| flash-rules/SCHEINWERFER_LED_RECHTS_9X1_DS.xml | 25.03.2024 | 2024-03-25 | 3 | Produktschlüssel F83TA zu P203 hinzugefügt |
