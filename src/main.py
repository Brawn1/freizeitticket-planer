"""Freizeitticket-Bilanz – hat sich das Ticket gelohnt?

Jeder Besuch wird mit dem Preis erfasst, den du OHNE Ticket gezahlt hättest,
und der Summe dem Ticketpreis gegenübergestellt. Daten liegen in SQLite.

Start:   pip install flet flet-charts   ->   flet run   (im Projektordner)
Android: flet build apk
Getestet mit Flet 1.0.x
"""

import json
import math
import os
import sqlite3
import tempfile
from datetime import date, datetime
from pathlib import Path

import flet as ft
import flet_charts as fch

# In gepackten Flet-Apps (Android/iOS) setzt Flet diese Variable,
# am Desktop landet die DB im Home-Verzeichnis.
DATA_DIR = Path(os.getenv("FLET_APP_STORAGE_DATA") or Path.home() / ".freizeitticket")
DB_FILE = DATA_DIR / "freizeitticket.db"
ALT_JSON = DATA_DIR / "daten.json"  # aus der ersten Version, wird einmalig übernommen

GRUEN = "#1F4E3D"
GLETSCHER = "#7FB7BE"
FELS = "#5C6670"
GOLD = "#C9A227"

# Themenabhängige Farben (passen sich an Hell/Dunkel an)
TEXT_LEISE = ft.Colors.ON_SURFACE_VARIANT
AKZENT = ft.Colors.PRIMARY

DARSTELLUNG = {  # Modus -> (ThemeMode, Icon, Bezeichnung, nächster Modus)
    "system": (ft.ThemeMode.SYSTEM, ft.Icons.BRIGHTNESS_AUTO, "wie System", "hell"),
    "hell": (ft.ThemeMode.LIGHT, ft.Icons.LIGHT_MODE, "hell", "dunkel"),
    "dunkel": (ft.ThemeMode.DARK, ft.Icons.DARK_MODE, "dunkel", "system"),
}

WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

MONATE = ["Jän", "Feb", "Mär", "Apr", "Mai", "Jun",
          "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

# (Name, Icon, Bedingung(anzahl, summe, ticketpreis))
MEILENSTEINE = [
    ("Erster Besuch", ft.Icons.FLAG, lambda n, s, t: n >= 1),
    ("Ein Viertel", ft.Icons.PIE_CHART_OUTLINE, lambda n, s, t: s >= t * 0.25),
    ("Halbzeit", ft.Icons.HOURGLASS_BOTTOM, lambda n, s, t: s >= t * 0.5),
    ("Ticket drin", ft.Icons.EMOJI_EVENTS, lambda n, s, t: s >= t),
    ("Doppelt gelohnt", ft.Icons.WORKSPACE_PREMIUM, lambda n, s, t: s >= t * 2),
    ("10 Besuche", ft.Icons.DIRECTIONS_WALK, lambda n, s, t: n >= 10),
    ("25 Besuche", ft.Icons.HIKING, lambda n, s, t: n >= 25),
    ("50 Besuche", ft.Icons.LANDSCAPE, lambda n, s, t: n >= 50),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS einstellungen (
    schluessel TEXT PRIMARY KEY,
    wert       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS besuche (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    datum      TEXT    NOT NULL,          -- ISO: JJJJ-MM-TT
    ort        TEXT    NOT NULL,
    preis_cent INTEGER NOT NULL CHECK (preis_cent >= 0)
);
CREATE INDEX IF NOT EXISTS idx_besuche_datum ON besuche(datum);
"""


# ---------------------------------------------------------------- Datenbank
class Datenbank:
    def __init__(self, pfad: Path):
        pfad.parent.mkdir(parents=True, exist_ok=True)
        self.pfad = pfad
        self.con = sqlite3.connect(pfad, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self._json_uebernehmen()

    def _json_uebernehmen(self):
        """Einmalige Migration aus der JSON-Version."""
        if not ALT_JSON.exists() or self.con.execute("SELECT COUNT(*) FROM besuche").fetchone()[0]:
            return
        try:
            alt = json.loads(ALT_JSON.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        with self.con:
            self.set_ticketpreis(alt.get("ticketpreis", 750))
            for b in alt.get("besuche", []):
                self.besuch_anlegen(b["datum"], b["ort"], b["preis"])
        ALT_JSON.rename(ALT_JSON.with_suffix(".json.migriert"))

    # Einstellungen
    def ticketpreis(self) -> float:
        row = self.con.execute(
            "SELECT wert FROM einstellungen WHERE schluessel = 'ticketpreis'").fetchone()
        return float(row["wert"]) if row else 750.0

    def set_ticketpreis(self, wert: float):
        with self.con:
            self.con.execute(
                "INSERT INTO einstellungen VALUES ('ticketpreis', ?) "
                "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert", (str(wert),))

    def einstellung(self, schluessel: str, standard: str) -> str:
        row = self.con.execute(
            "SELECT wert FROM einstellungen WHERE schluessel = ?", (schluessel,)).fetchone()
        return row["wert"] if row else standard

    def set_einstellung(self, schluessel: str, wert: str):
        with self.con:
            self.con.execute(
                "INSERT INTO einstellungen VALUES (?, ?) "
                "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert", (schluessel, wert))

    # Besuche
    def besuch_anlegen(self, datum_iso: str, ort: str, preis: float):
        with self.con:
            self.con.execute("INSERT INTO besuche (datum, ort, preis_cent) VALUES (?, ?, ?)",
                             (datum_iso, ort, round(preis * 100)))

    def besuch_loeschen(self, besuch_id: int):
        with self.con:
            self.con.execute("DELETE FROM besuche WHERE id = ?", (besuch_id,))

    def besuche(self):
        return self.con.execute(
            "SELECT id, datum, ort, preis_cent / 100.0 AS preis FROM besuche "
            "ORDER BY datum DESC, id DESC").fetchall()

    def bilanz(self):
        r = self.con.execute(
            "SELECT COUNT(*) AS anzahl, COALESCE(SUM(preis_cent), 0) / 100.0 AS summe "
            "FROM besuche").fetchone()
        return r["anzahl"], r["summe"]

    def letzte_preise_pro_ort(self):
        return self.con.execute(
            "SELECT ort, preis_cent / 100.0 AS preis FROM besuche b "
            "WHERE id = (SELECT MAX(id) FROM besuche WHERE ort = b.ort) ORDER BY ort").fetchall()

    def monats_summen(self):
        return self.con.execute(
            "SELECT substr(datum, 1, 7) AS monat, SUM(preis_cent) / 100.0 AS summe "
            "FROM besuche GROUP BY monat ORDER BY monat").fetchall()

    # Backup / Wiederherstellung
    def backup_bytes(self) -> bytes:
        """Konsistente Kopie der laufenden DB über die SQLite-Backup-API."""
        with tempfile.TemporaryDirectory() as tmp:
            ziel_pfad = Path(tmp) / "backup.db"
            ziel = sqlite3.connect(ziel_pfad)
            self.con.backup(ziel)
            ziel.close()
            return ziel_pfad.read_bytes()

    def wiederherstellen(self, daten: bytes):
        """Prüft die Datei und überschreibt dann die aktuelle DB damit."""
        with tempfile.TemporaryDirectory() as tmp:
            quelle_pfad = Path(tmp) / "restore.db"
            quelle_pfad.write_bytes(daten)
            try:
                quelle = sqlite3.connect(quelle_pfad)
                if quelle.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Datei ist beschädigt.")
                tabellen = {r[0] for r in quelle.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")}
                if not {"besuche", "einstellungen"} <= tabellen:
                    raise ValueError("Das ist kein Freizeitticket-Backup.")
            except sqlite3.DatabaseError:
                raise ValueError("Das ist keine SQLite-Datei.")
            quelle.backup(self.con)
            quelle.close()


# ---------------------------------------------------------------- Helfer
def euro(betrag: float) -> str:
    s = f"{betrag:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {s}"


def erreichte_meilensteine(db) -> set:
    anzahl, summe = db.bilanz()
    ticket = db.ticketpreis()
    return {name for name, _, ok in MEILENSTEINE if ok(anzahl, summe, ticket)}


def monatsliste(von: str, bis: str) -> list[str]:
    """Alle Monate JJJJ-MM von 'von' bis 'bis', höchstens die letzten 12."""
    j, m = map(int, von.split("-"))
    bj, bm = map(int, bis.split("-"))
    monate = []
    while (j, m) <= (bj, bm):
        monate.append(f"{j:04d}-{m:02d}")
        j, m = (j + 1, 1) if m == 12 else (j, m + 1)
    return monate[-12:]


def datum_anzeige(tag: date) -> str:
    return f"{WOCHENTAGE[tag.weekday()]}, {tag:%d.%m.%Y}"


def zahl(text: str) -> float:
    return float(text.strip().replace("€", "").replace(",", "."))


# ---------------------------------------------------------------- UI
def main(page: ft.Page):
    page.title = "Freizeitticket-Bilanz"
    page.theme = ft.Theme(color_scheme_seed=GRUEN)
    page.dark_theme = ft.Theme(color_scheme_seed=GRUEN)
    # Deutsche Texte im Kalender (Monatsnamen, Wochentage, Buttons)
    page.locale_configuration = ft.LocaleConfiguration(
        supported_locales=[ft.Locale("de", "AT"), ft.Locale("de")],
        current_locale=ft.Locale("de", "AT"),
    )
    page.padding = 16
    page.scroll = ft.ScrollMode.AUTO

    db = Datenbank(DB_FILE)
    modus = db.einstellung("darstellung", "system")
    if modus not in DARSTELLUNG:
        modus = "system"
    page.theme_mode = DARSTELLUNG[modus][0]
    picker = ft.FilePicker()
    share = ft.Share()

    def meldung(text: str):
        page.show_dialog(ft.SnackBar(ft.Text(text)))

    # Bilanz-Kopf
    gesamt_text = ft.Text(size=40, weight=ft.FontWeight.BOLD, color=AKZENT)
    status_text = ft.Text(size=16)
    fortschritt = ft.ProgressBar(value=0, bar_height=12, color=AKZENT,
                                 bgcolor=ft.Colors.with_opacity(0.35, GLETSCHER),
                                 border_radius=6)
    statistik = ft.Text(size=13, color=TEXT_LEISE)
    ticket_feld = ft.TextField(label="Ticketpreis", value=f"{db.ticketpreis():.0f}",
                               suffix="€", keyboard_type=ft.KeyboardType.NUMBER,
                               width=140, dense=True)

    # Eingabe
    ort_feld = ft.TextField(label="Wo warst du?", hint_text="z. B. Patscherkofelbahn", expand=True)
    preis_feld = ft.TextField(label="Normalpreis", suffix="€",
                              keyboard_type=ft.KeyboardType.NUMBER, width=130)
    gewaehlter_tag = date.today()

    def kalender_oeffnen(e):
        kalender.value = datetime.combine(gewaehlter_tag, datetime.min.time())
        kalender.last_date = datetime.combine(date.today(), datetime.min.time())
        page.show_dialog(kalender)

    def tag_gewaehlt(e):
        nonlocal gewaehlter_tag
        wert = kalender.value
        if wert is None:
            return
        # Flet liefert je nach Plattform UTC oder lokale Zeit -> auf lokales Datum bringen
        gewaehlter_tag = wert.astimezone().date() if wert.tzinfo else wert.date()
        datum_feld.value = datum_anzeige(gewaehlter_tag)
        page.update()

    kalender = ft.DatePicker(
        first_date=datetime(date.today().year - 2, 1, 1),
        last_date=datetime.combine(date.today(), datetime.min.time()),
        locale=ft.Locale("de", "AT"),
        help_text="Wann warst du dort?",
        cancel_text="Abbrechen",
        confirm_text="Übernehmen",
        on_change=tag_gewaehlt,
    )

    datum_feld = ft.TextField(
        label="Datum", value=datum_anzeige(gewaehlter_tag), width=170,
        read_only=True, suffix_icon=ft.Icons.CALENDAR_MONTH, on_click=kalender_oeffnen,
    )

    diagramm = fch.BarChart(
        height=230,
        interactive=True,
        animation=ft.Animation(600, ft.AnimationCurve.EASE_OUT),
        horizontal_grid_lines=fch.ChartGridLines(color=ft.Colors.with_opacity(0.15, ft.Colors.ON_SURFACE), width=1),
        bottom_axis=fch.ChartAxis(label_size=28),
        left_axis=fch.ChartAxis(label_size=56),
        tooltip=fch.BarChartTooltip(bgcolor=ft.Colors.with_opacity(0.9, GRUEN)),
    )
    diagramm_hinweis = ft.Text(size=12, color=TEXT_LEISE)
    meilenstein_zeile = ft.Row(wrap=True, spacing=6, run_spacing=6)

    liste = ft.Column(spacing=0)
    verlauf_kopf = ft.Text(size=13, color=TEXT_LEISE)
    orte_zeile = ft.Row(wrap=True, spacing=6, run_spacing=6)

    def aktualisieren():
        ticket = db.ticketpreis()
        anzahl, summe = db.bilanz()

        gesamt_text.value = euro(summe)
        fortschritt.value = min(summe / ticket, 1) if ticket > 0 else 0
        if summe >= ticket:
            status_text.value = f"Gelohnt – {euro(summe - ticket)} gespart."
            status_text.color = AKZENT
        else:
            status_text.value = f"Noch {euro(ticket - summe)} bis das Ticket drin ist."
            status_text.color = TEXT_LEISE
        if 0 < anzahl and summe < ticket:
            noch = math.ceil((ticket - summe) / (summe / anzahl))
            status_text.value += f" Bei deinem Schnitt noch etwa {noch} Besuche."
        statistik.value = (
            f"{anzahl} Besuche · im Schnitt {euro(summe / anzahl)} pro Besuch · "
            f"Ticket kostet dich {euro(ticket / anzahl)} pro Besuch"
            if anzahl else "Noch keine Besuche erfasst."
        )

        diagramm_bauen(ticket)

        erreicht = erreichte_meilensteine(db)
        meilenstein_zeile.controls = [
            ft.Chip(
                label=ft.Text(name, color=AKZENT if name in erreicht else TEXT_LEISE),
                leading=ft.Icon(icon, color=GOLD if name in erreicht else ft.Colors.with_opacity(0.4, TEXT_LEISE)),
                bgcolor=ft.Colors.with_opacity(0.18, GOLD) if name in erreicht else None,
                tooltip="Erreicht" if name in erreicht else "Noch offen",
            )
            for name, icon, _ in MEILENSTEINE
        ]

        orte_zeile.controls = [
            ft.Chip(label=ft.Text(r["ort"]),
                    on_click=lambda e, o=r["ort"], p=r["preis"]: vorbelegen(o, p))
            for r in db.letzte_preise_pro_ort()
        ]

        verlauf_bauen()
        page.update()

    def verlauf_bauen():
        """Alle Besuche, neueste zuerst, nach Monat gruppiert mit Monatssumme."""
        besuche = db.besuche()
        verlauf_kopf.value = (f"{len(besuche)} Besuche insgesamt" if besuche
                              else "Noch keine Besuche – erfasse deinen ersten in der Übersicht.")
        monats_summe = {r["monat"]: r["summe"] for r in db.monats_summen()}
        liste.controls = []
        aktueller_monat = None
        for b in besuche:
            monat = b["datum"][:7]
            if monat != aktueller_monat:
                aktueller_monat = monat
                liste.controls.append(ft.Container(
                    content=ft.Row([
                        ft.Text(f"{MONATE[int(monat[5:]) - 1]} {monat[:4]}",
                                size=15, weight=ft.FontWeight.W_600, color=AKZENT),
                        ft.Text(euro(monats_summe.get(monat, 0)), color=TEXT_LEISE),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    padding=ft.Padding(16, 18, 16, 4),
                ))
            tag = datetime.strptime(b["datum"], "%Y-%m-%d").date()
            liste.controls.append(ft.ListTile(
                title=ft.Text(b["ort"]),
                subtitle=ft.Text(datum_anzeige(tag)),
                trailing=ft.Row(
                    [ft.Text(euro(b["preis"]), weight=ft.FontWeight.W_500),
                     ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, tooltip="Besuch löschen",
                                   on_click=lambda e, b=b, tag=tag: loeschen_fragen(b, tag))],
                    tight=True),
            ))

    def diagramm_bauen(ticket):
        """Kumulierte Ersparnis pro Monat; der helle Hintergrundbalken ist das Ziel."""
        summen = {r["monat"]: r["summe"] for r in db.monats_summen()}
        if not summen:
            diagramm.groups = []
            diagramm.visible = False
            diagramm_hinweis.value = "Sobald du Besuche erfasst, wächst hier dein Diagramm."
            return
        diagramm.visible = True
        heute = date.today().strftime("%Y-%m")
        monate = monatsliste(min(summen), max(max(summen), heute))

        kumuliert, laufend = [], sum(v for k, v in summen.items() if k < monate[0])
        for m in monate:
            laufend += summen.get(m, 0)
            kumuliert.append(laufend)

        max_y = max(ticket, kumuliert[-1]) * 1.1 or 1
        diagramm.max_y = max_y
        diagramm.min_y = 0
        diagramm.groups = [
            fch.BarChartGroup(x=i, rods=[fch.BarChartRod(
                from_y=0, to_y=wert, width=16, border_radius=4,
                color=GOLD if wert >= ticket else AKZENT,
                bg_from_y=0, bg_to_y=ticket, bgcolor=ft.Colors.with_opacity(0.25, GLETSCHER),
                tooltip=f"{MONATE[int(m[5:]) - 1]} {m[:4]}\n+{euro(summen.get(m, 0))}\ngesamt {euro(wert)}",
            )])
            for i, (m, wert) in enumerate(zip(monate, kumuliert))
        ]
        diagramm.bottom_axis.labels = [
            fch.ChartAxisLabel(value=i, label=ft.Text(MONATE[int(m[5:]) - 1], size=11))
            for i, m in enumerate(monate)
        ]
        diagramm.left_axis.labels = [
            fch.ChartAxisLabel(value=0, label=ft.Text("0", size=11)),
            fch.ChartAxisLabel(value=ticket, label=ft.Text(f"{ticket:.0f} €", size=11,
                                                          weight=ft.FontWeight.BOLD)),
        ]
        diagramm_hinweis.value = ("Gold = Ticket hat sich gelohnt. "
                                  "Der helle Balken zeigt das Ziel, tippe für Details.")

    def vorbelegen(ort, preis):
        ort_feld.value = ort
        preis_feld.value = f"{preis:.2f}".replace(".", ",")
        page.update()

    def hinzufuegen(e):
        nonlocal gewaehlter_tag
        for f in (ort_feld, preis_feld):
            f.error = None
        ok = True
        if not ort_feld.value.strip():
            ort_feld.error = "Ort fehlt"
            ok = False
        try:
            preis = zahl(preis_feld.value)
            if preis < 0:
                raise ValueError
        except ValueError:
            preis_feld.error = "Betrag z. B. 24,50"
            ok = False
        if not ok:
            page.update()
            return
        vorher = erreichte_meilensteine(db)
        db.besuch_anlegen(gewaehlter_tag.isoformat(), ort_feld.value.strip(), preis)
        ort_feld.value = preis_feld.value = ""
        gewaehlter_tag = date.today()
        datum_feld.value = datum_anzeige(gewaehlter_tag)
        aktualisieren()
        neu = [n for n, _, _ in MEILENSTEINE if n in erreichte_meilensteine(db) - vorher]
        if neu:
            meldung("Meilenstein erreicht: " + ", ".join(neu) + " 🏆")

    def loeschen_fragen(b, tag):
        def ja(e):
            page.pop_dialog()
            db.besuch_loeschen(b["id"])
            aktualisieren()
            meldung("Besuch gelöscht.")

        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Besuch löschen?"),
            content=ft.Text(f"{b['ort']} am {datum_anzeige(tag)} ({euro(b['preis'])})"),
            actions=[ft.TextButton("Abbrechen", on_click=lambda e: page.pop_dialog()),
                     ft.FilledButton("Löschen", on_click=ja)],
        ))

    def ticket_geaendert(e):
        try:
            db.set_ticketpreis(zahl(ticket_feld.value))
            ticket_feld.error = None
            aktualisieren()
        except ValueError:
            ticket_feld.error = "Ungültig"
            page.update()

    # ---- Backup
    def backup_name():
        return f"freizeitticket-{datetime.now():%Y-%m-%d_%H%M}.db"

    async def backup_teilen(e):
        # Android: im Teilen-Menü "In Drive speichern" wählen
        await share.share_files(
            [ft.ShareFile.from_bytes(db.backup_bytes(), name=backup_name(),
                                     mime_type="application/octet-stream")],
            subject="Freizeitticket-Backup")

    async def backup_speichern(e):
        # Android: im Speichern-Dialog links "Drive" als Ort wählen
        pfad = await picker.save_file(dialog_title="Backup speichern",
                                      file_name=backup_name(),
                                      src_bytes=db.backup_bytes())
        if pfad:
            meldung("Backup gespeichert.")

    async def backup_laden(e):
        dateien = await picker.pick_files(dialog_title="Backup auswählen", with_data=True)
        if not dateien:
            return
        f = dateien[0]
        inhalt = f.bytes or (Path(f.path).read_bytes() if f.path else None)
        if not inhalt:
            meldung("Datei konnte nicht gelesen werden.")
            return

        def bestaetigt(e):
            page.pop_dialog()
            try:
                db.wiederherstellen(inhalt)
            except ValueError as fehler:
                meldung(str(fehler))
                return
            ticket_feld.value = f"{db.ticketpreis():.0f}"
            aktualisieren()
            meldung("Backup wiederhergestellt.")

        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Backup wiederherstellen?"),
            content=ft.Text(f"Alle aktuellen Besuche werden durch „{f.name}“ ersetzt."),
            actions=[ft.TextButton("Abbrechen", on_click=lambda e: page.pop_dialog()),
                     ft.FilledButton("Ersetzen", on_click=bestaetigt)],
        ))

    ticket_feld.on_blur = ticket_geaendert
    ticket_feld.on_submit = ticket_geaendert
    preis_feld.on_submit = hinzufuegen

    def darstellung_knopf():
        _, icon, name, _ = DARSTELLUNG[modus]
        return icon, f"Darstellung: {name} (tippen zum Wechseln)"

    def darstellung_wechseln(e):
        nonlocal modus
        modus = DARSTELLUNG[modus][3]
        db.set_einstellung("darstellung", modus)
        page.theme_mode = DARSTELLUNG[modus][0]
        darstellung_btn.icon, darstellung_btn.tooltip = darstellung_knopf()
        meldung(f"Darstellung: {DARSTELLUNG[modus][2]}")
        page.update()

    icon, tip = darstellung_knopf()
    darstellung_btn = ft.IconButton(icon=icon, tooltip=tip, on_click=darstellung_wechseln)

    page.appbar = ft.AppBar(
        title=ft.Text("Freizeitticket"),
        actions=[darstellung_btn, ft.PopupMenuButton(items=[
            ft.PopupMenuItem(content="Backup teilen (z. B. Drive)", icon=ft.Icons.SHARE,
                             on_click=backup_teilen),
            ft.PopupMenuItem(content="Backup speichern unter …", icon=ft.Icons.SAVE_ALT,
                             on_click=backup_speichern),
            ft.PopupMenuItem(content="Backup wiederherstellen", icon=ft.Icons.RESTORE,
                             on_click=backup_laden),
        ])],
    )

    uebersicht = ft.Column([
        ft.Container(
            content=ft.Column([
                ft.Row([ft.Text("Ersparter Eintritt", size=14, color=TEXT_LEISE), ticket_feld],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                gesamt_text, fortschritt, status_text, statistik,
            ], spacing=8),
            padding=20, border_radius=16, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        ),
        ft.Text("Deine Saison", size=18, weight=ft.FontWeight.W_600),
        diagramm,
        diagramm_hinweis,
        meilenstein_zeile,
        ft.Divider(),
        ft.Text("Besuch erfassen", size=18, weight=ft.FontWeight.W_600),
        orte_zeile,
        ft.Row([ort_feld]),
        ft.Row([preis_feld, datum_feld,
                ft.FilledButton("Besuch speichern", icon=ft.Icons.ADD, on_click=hinzufuegen)],
               wrap=True),
    ], spacing=10)

    verlauf = ft.Column([
        ft.Text("Verlauf", size=22, weight=ft.FontWeight.W_600),
        verlauf_kopf,
        liste,
    ], spacing=4, visible=False)

    def seite_wechseln(e):
        index = page.navigation_bar.selected_index
        uebersicht.visible = index == 0
        verlauf.visible = index == 1
        page.update()

    page.navigation_bar = ft.NavigationBar(
        selected_index=0,
        on_change=seite_wechseln,
        destinations=[
            ft.NavigationBarDestination(icon=ft.Icons.INSIGHTS_OUTLINED,
                                        selected_icon=ft.Icons.INSIGHTS, label="Übersicht"),
            ft.NavigationBarDestination(icon=ft.Icons.HISTORY, label="Verlauf"),
        ],
    )

    page.add(uebersicht, verlauf)
    aktualisieren()


if __name__ == "__main__":
    ft.run(main)
