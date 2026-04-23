"""add lv_templates table + seed 5 system templates for Malerarbeiten

Revision ID: 012
Revises: 011
Create Date: 2026-04-23

Backs the "LV-Vorlagen-Bibliothek" feature (v17). Users can now spawn
a new Leistungsverzeichnis from a pre-built skeleton instead of
starting blank. System templates are owned by the product (``is_system
= TRUE``, ``created_by_user_id = NULL``); user-saved templates belong
to the creating user and are deletable by them.

Why the JSONB column instead of exploding into ``leistungsgruppen`` /
``positionen`` rows: a template has no quantities, no prices, and no
per-room calculation proof, so a dedicated table avoids polluting the
real LV tables with NULL-only skeleton rows. Copying a template into
a concrete LV is a single JSONB read plus a bulk insert of ``positionen``
rows at instantiation time — see ``POST /api/lv/from-template``.

Seeding
-------

Five Malerarbeiten templates covering the most common Austrian
residential/commercial scopes:

    1. Einfamilienhaus Standard (14 positions)
    2. Wohnanlage Grundausstattung (17 positions)
    3. Bürogebäude (14 positions)
    4. Sanierung Altbau (15 positions)
    5. Dachgeschossausbau (13 positions)

Position text is deliberately Austrian-idiomatic ("Untergrund",
"Spachtelung Q3", "Silikat-Dispersion", "Kalkmilch") so the wall-area
auto-sync — which keys off kurztext keywords like "Wandanstrich" /
"Deckenanstrich" — routes the positions correctly when a user applies
a template and then clicks "Wandflächen übernehmen".
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Seed data. Kept inline so the migration is self-contained and can run
# without importing anything from the ``app.*`` namespace — migrations
# outlive code, and pulling seed data from a runtime module would couple
# them to a specific app commit.
# ---------------------------------------------------------------------------


def _gr(nummer: str, bezeichnung: str, positionen: list[dict]) -> dict:
    return {"nummer": nummer, "bezeichnung": bezeichnung, "positionen": positionen}


def _pos(
    nummer: str,
    kurztext: str,
    einheit: str,
    kategorie: str,
    langtext: str,
) -> dict:
    return {
        "positions_nummer": nummer,
        "kurztext": kurztext,
        "langtext": langtext,
        "einheit": einheit,
        "kategorie": kategorie,
    }


TEMPLATE_EINFAMILIENHAUS = {
    "gruppen": [
        _gr("01", "Vorarbeiten", [
            _pos("01.01", "Untergrund reinigen, bürsten, abstauben", "m²", "vorarbeit",
                 "Wand- und Deckenflächen reinigen, lose Teile entfernen, Staub absaugen. "
                 "Inklusive Abdecken des Bodens mit Malervlies."),
            _pos("01.02", "Risse und Fehlstellen spachteln", "m²", "vorarbeit",
                 "Haarrisse und kleine Fehlstellen mit geeigneter Innenspachtelmasse "
                 "füllen, nach Erhärtung schleifen und entstauben."),
            _pos("01.03", "Abdecken und Abkleben Möbel, Böden, Fenster", "psch", "vorarbeit",
                 "Sämtliche nicht zu streichende Flächen (Möbel, Böden, Leuchten, "
                 "Fensterrahmen, Steckdosen) mit Malerfolie und Kreppband abkleben."),
        ]),
        _gr("02", "Wandanstriche", [
            _pos("02.01", "Wandanstrich Dispersion 2-lagig — Wohnräume", "m²", "wand",
                 "Grundierung sowie 2-lagiger Anstrich mit hochdeckender Innendispersion, "
                 "matt, weiß, scheuerbeständig (Klasse 3 nach ÖNORM EN 13300)."),
            _pos("02.02", "Wandanstrich Silikat-Dispersion — Küche & Bad", "m²", "wand",
                 "Atmungsaktiver, schimmelhemmender Anstrich auf Silikatbasis für "
                 "Feuchträume. 1× Grundierung, 2× Deckbeschichtung."),
            _pos("02.03", "Wandanstrich farbig — Akzentwand", "m²", "wand",
                 "Farbton bauseits gewählt (z. B. aus RAL-, NCS- oder Herstellerfarbkarte). "
                 "2-lagiger Auftrag auf vorbereitetem Untergrund."),
        ]),
        _gr("03", "Deckenanstriche", [
            _pos("03.01", "Deckenanstrich Dispersion weiß, 2-lagig", "m²", "decke",
                 "Decke grundieren und 2× mit matter Innendispersion, weiß, überrollen. "
                 "Fugen- und streifenfreie Ausführung."),
            _pos("03.02", "Sichtbare Balkendecke — Lasur auf Holz", "m²", "decke",
                 "Holzbalken reinigen, anschleifen, 2× mit atmungsaktiver Holzlasur "
                 "(farblos oder bauseits gewählter Ton) streichen."),
        ]),
        _gr("04", "Treppenhaus", [
            _pos("04.01", "Wandanstrich Treppenhaus — stoßfest", "m²", "wand",
                 "Strapazierfähige Latex- oder Dispersionsbeschichtung, scheuerbeständig "
                 "Klasse 1, 2-lagig auf vorbereitetem Untergrund."),
            _pos("04.02", "Deckenanstrich Treppenhaus", "m²", "decke",
                 "Deckenfläche im Treppenhaus mit Innendispersion weiß, 2-lagig."),
            _pos("04.03", "Handläufe lackieren, 2-lagig", "m", "sonstiges",
                 "Holz- oder Metallhandlauf anschleifen, grundieren, 2× seidenmatt lackieren. "
                 "Farbton bauseits."),
        ]),
        _gr("05", "Sonstiges", [
            _pos("05.01", "Heizkörperlackierung, hitzebeständig", "Stk", "sonstiges",
                 "Heizkörper vollständig demontiert oder vor Ort: entrosten, grundieren, "
                 "mit hitzebeständigem Heizkörperlack 2× beschichten."),
            _pos("05.02", "Fensterbänke innen — Lack weiß", "m", "sonstiges",
                 "Holz- oder Metallfensterbänke schleifen, grundieren, 2× mit seidenmattem "
                 "Weißlack beschichten."),
            _pos("05.03", "Türen lackieren, beidseitig", "Stk", "sonstiges",
                 "Zimmertüren und Zargen anschleifen, grundieren, beidseitig inkl. Kanten "
                 "2× seidenmatt lackieren, Beschläge demontieren und wieder montieren."),
        ]),
    ]
}


TEMPLATE_WOHNANLAGE = {
    "gruppen": [
        _gr("01", "Vorarbeiten", [
            _pos("01.01", "Vorarbeiten Wohnungen — Untergrund vorbereiten", "m²", "vorarbeit",
                 "Wand- und Deckenflächen in Wohnungen reinigen, Risse spachteln, "
                 "Tiefgrund auftragen. Je Einheit separat abzurechnen."),
            _pos("01.02", "Vorarbeiten Gemeinschaftsbereiche", "m²", "vorarbeit",
                 "Untergrund in Stiegenhaus, Keller, Gemeinschaftsräumen prüfen, lose "
                 "Altanstriche entfernen, spachteln, grundieren."),
            _pos("01.03", "Abdecken und Abkleben", "psch", "vorarbeit",
                 "Böden, Geländer, Beschläge, Türen und Einbauten in allen zu bearbeitenden "
                 "Bereichen schutzweise abdecken."),
        ]),
        _gr("02", "Wohnungen", [
            _pos("02.01", "Wandanstrich Wohnräume Dispersion, 2-lagig", "m²", "wand",
                 "Hochdeckende Innendispersion weiß, matt, scheuerbeständig Klasse 3, "
                 "2-lagig in allen Wohn- und Schlafräumen."),
            _pos("02.02", "Wandanstrich Küche und Bad — Silikat", "m²", "wand",
                 "Schimmelhemmender Silikatanstrich für Küchen und Feuchträume. "
                 "1× Grundierung, 2× Deckbeschichtung."),
            _pos("02.03", "Deckenanstrich Wohnung — Dispersion weiß", "m²", "decke",
                 "Decke in allen Wohnräumen mit matter Innendispersion weiß, 2-lagig."),
        ]),
        _gr("03", "Stiegenhäuser", [
            _pos("03.01", "Wandanstrich Stiegenhaus — strapazierfähig", "m²", "wand",
                 "Scheuerbeständige Latexbeschichtung Klasse 1-2 für stark frequentierte "
                 "Stiegenhauswände, 2-lagig."),
            _pos("03.02", "Deckenanstrich Stiegenhaus", "m²", "decke",
                 "Stiegenhausdecke mit Innendispersion weiß, 2-lagig, fugenfrei."),
            _pos("03.03", "Sockelleisten lackieren", "m", "sonstiges",
                 "Holzsockelleisten im Stiegenhaus anschleifen, grundieren, 2× seidenmatt "
                 "lackieren."),
            _pos("03.04", "Handläufe und Geländer lackieren", "m", "sonstiges",
                 "Metallgeländer und Handläufe entrosten, grundieren, mit Rostschutzlack "
                 "2× beschichten."),
        ]),
        _gr("04", "Keller und Technikräume", [
            _pos("04.01", "Wandanstrich Keller — Silikat gegen Feuchte", "m²", "wand",
                 "Diffusionsoffener Silikatanstrich in Kellerräumen, 2-lagig, auf "
                 "mineralischem Untergrund."),
            _pos("04.02", "Deckenanstrich Keller", "m²", "decke",
                 "Kellerdecke mit atmungsaktiver Dispersion weiß, 2-lagig."),
            _pos("04.03", "Kennzeichnung Fluchtwege", "psch", "sonstiges",
                 "Fluchtweg- und Notausgangskennzeichnungen nach ÖNORM Z 1000 "
                 "auftragen bzw. erneuern."),
        ]),
        _gr("05", "Gemeinschaftsbereiche", [
            _pos("05.01", "Wandanstrich Waschküche — abwaschbar", "m²", "wand",
                 "Abwaschbare, feuchteresistente Dispersionsbeschichtung Klasse 1, "
                 "2-lagig."),
            _pos("05.02", "Wandanstrich Fahrradraum", "m²", "wand",
                 "Stoßfeste Innendispersion, 2-lagig, in Gemeinschafts-Fahrradraum."),
            _pos("05.03", "Postkastenanlage lackieren", "psch", "sonstiges",
                 "Metall-Postkastenanlage entrosten, grundieren, 2× mit Metalllack seidenmatt "
                 "beschichten. Farbton bauseits."),
            _pos("05.04", "Müllraum — Beschichtung hygienisch", "m²", "wand",
                 "Hygienisch abwaschbare Spezialbeschichtung für Müllräume, "
                 "schimmelresistent, 2-lagig."),
        ]),
    ]
}


TEMPLATE_BUERO = {
    "gruppen": [
        _gr("01", "Vorarbeiten", [
            _pos("01.01", "Untergrund reinigen und prüfen", "m²", "vorarbeit",
                 "Sämtliche Wand- und Deckenflächen auf Festigkeit und Haftfähigkeit "
                 "prüfen, reinigen, entstauben."),
            _pos("01.02", "Spachtelung Q2 — Gipskartonwände", "m²", "vorarbeit",
                 "Gipskartonflächen in Oberflächengüte Q2 verspachteln: Fugen und "
                 "Schraubenköpfe zweimalig spachteln, schleifen."),
            _pos("01.03", "Abdeckarbeiten Büromöbel", "psch", "vorarbeit",
                 "Büromöbel, EDV, Bodenbeläge und Einbauten mit Malerfolie abdecken, "
                 "nach Arbeitsabschluss entfernen."),
        ]),
        _gr("02", "Büroräume", [
            _pos("02.01", "Wandanstrich Büro — abwaschbar", "m²", "wand",
                 "Scheuerbeständige Innendispersion Klasse 2, abwaschbar, 2-lagig "
                 "auf vorbereitetem Untergrund. Weiß oder bauseits gewählter Ton."),
            _pos("02.02", "Deckenanstrich Büro — Dispersion weiß", "m²", "decke",
                 "Bürodecke mit matter Innendispersion weiß, 2-lagig, streifenfrei."),
            _pos("02.03", "Farbige Akzentwand — Corporate-Farbton", "m²", "wand",
                 "Akzentwand in Unternehmensfarbe (CI-Vorgabe), deckend 2-lagig "
                 "gestrichen. Farbton gemäß Styleguide."),
        ]),
        _gr("03", "Brandschutz", [
            _pos("03.01", "Brandschutzanstrich Stahlträger — F30", "m²", "sonstiges",
                 "Dämmschichtbildender Brandschutzanstrich auf Stahlkonstruktionen "
                 "für Feuerwiderstandsklasse R30 gemäß ÖNORM EN 13381-8, "
                 "inkl. Deckbeschichtung."),
            _pos("03.02", "Brandschutzanstrich Holzkonstruktion — F30", "m²", "sonstiges",
                 "Brandschutzbeschichtung für tragende Holzbauteile, F30 gemäß "
                 "ÖNORM B 3800, inkl. Deckanstrich."),
        ]),
        _gr("04", "Sanitärbereich", [
            _pos("04.01", "Wandanstrich WC — Silikat, abwaschbar", "m²", "wand",
                 "Abwaschbarer Silikatanstrich für Toilettenbereiche, "
                 "schimmelhemmend, 2-lagig."),
            _pos("04.02", "Deckenanstrich WC", "m²", "decke",
                 "WC-Decke mit feuchteresistenter Dispersion weiß, 2-lagig."),
        ]),
        _gr("05", "Verkehrsflächen", [
            _pos("05.01", "Wandanstrich Flur — stoßfest", "m²", "wand",
                 "Strapazierfähige Latexbeschichtung Klasse 1 für Flur- und "
                 "Gangwände, 2-lagig."),
            _pos("05.02", "Deckenanstrich Flur", "m²", "decke",
                 "Flurdecke mit matter Innendispersion weiß, 2-lagig."),
            _pos("05.03", "Sockelleisten lackieren", "m", "sonstiges",
                 "Holzsockelleisten schleifen, grundieren, 2× seidenmatt lackieren. "
                 "Farbton bauseits."),
            _pos("05.04", "Bodenmarkierungen Tiefgarage/Keller", "m²", "sonstiges",
                 "Bodenmarkierungen (Parkplätze, Fluchtwege, Gefahrenbereiche) mit "
                 "2K-Bodenmarkierungsfarbe, farblich nach ÖNORM."),
        ]),
    ]
}


TEMPLATE_SANIERUNG = {
    "gruppen": [
        _gr("01", "Bestandserhebung und Abbrucharbeiten", [
            _pos("01.01", "Altlackentfernung", "m²", "vorarbeit",
                 "Bestehende Lackbeschichtungen durch Abbeizen, Abschleifen oder "
                 "thermisches Entfernen vollständig abtragen. Fachgerechte Entsorgung "
                 "inklusive."),
            _pos("01.02", "Altanstrich lose abstoßen und entfernen", "m²", "vorarbeit",
                 "Kreidende, blätternde oder hohl liegende Altanstriche durch Abspachteln "
                 "und Bürsten entfernen, bis tragfähiger Untergrund erreicht ist."),
            _pos("01.03", "Tapeten entfernen", "m²", "vorarbeit",
                 "Altetapeten (Papier, Raufaser, Vinyl) mittels Tapetenlöser anweichen "
                 "und vollständig abziehen. Leimreste gründlich entfernen."),
        ]),
        _gr("02", "Untergrundsanierung", [
            _pos("02.01", "Stuckprofile restaurieren und ergänzen", "m", "vorarbeit",
                 "Historische Stuckprofile reinigen, fehlende Abschnitte mit passendem "
                 "Stuckgips bzw. Modelliermasse ergänzen, schleifen, grundieren."),
            _pos("02.02", "Risse kraftschlüssig verschließen", "m", "vorarbeit",
                 "Risse ausstemmen, kraftschlüssig mit Reparaturmörtel bzw. elastischer "
                 "Rissfüllmasse schließen, armieren mit Glasseidenband wo erforderlich."),
            _pos("02.03", "Putzausbesserungen Kalkzementputz", "m²", "vorarbeit",
                 "Fehlstellen im Bestandsputz mit Kalkzementputz ergänzen, an Bestand "
                 "anarbeiten, reiben, schleifen."),
            _pos("02.04", "Tiefengrundierung Altputz", "m²", "vorarbeit",
                 "Saugfähigen, sandenden Altputz mit Tiefengrund durchtränken, "
                 "Festigung und Haftvermittlung für nachfolgenden Anstrich."),
            _pos("02.05", "Kalkmilchanstrich als Ausgleich", "m²", "vorarbeit",
                 "Ausgleichender Kalkmilchanstrich zur Vorbereitung historischer, "
                 "diffusionsoffener Bauteile."),
        ]),
        _gr("03", "Wandanstriche Bestand", [
            _pos("03.01", "Wandanstrich Kalkfarbe — atmungsaktiv", "m²", "wand",
                 "Traditioneller Kalkfarbenanstrich, hoch diffusionsoffen, für "
                 "historische Wandflächen. 2-lagig, feuchte Anwendung."),
            _pos("03.02", "Wandanstrich Silikat — diffusionsoffen", "m²", "wand",
                 "Reiner Silikatanstrich nach ÖNORM, diffusionsoffen und UV-stabil, "
                 "2-lagig auf mineralischem Untergrund."),
        ]),
        _gr("04", "Deckenanstriche", [
            _pos("04.01", "Deckenanstrich Altbau — Kalkfarbe", "m²", "decke",
                 "Altbau-Decke mit traditioneller Kalkfarbe 2-lagig überrollen, "
                 "atmungsaktiv."),
            _pos("04.02", "Stuckdecke reinigen und lasieren", "m²", "decke",
                 "Stuckdecke vorsichtig abbürsten, reinigen, mit pigmentierter "
                 "Kalklasur in abgestimmten Tönen überarbeiten."),
        ]),
        _gr("05", "Holzbauteile", [
            _pos("05.01", "Holzvertäfelung aufarbeiten, lasieren", "m²", "sonstiges",
                 "Holzvertäfelung anschleifen, reinigen, 2× mit atmungsaktiver Holzlasur "
                 "behandeln. Farbton nach Muster."),
            _pos("05.02", "Holzfenster innen — 3-lagig Lackaufbau", "m²", "sonstiges",
                 "Holzfenster innen: Grundierung, Zwischenanstrich, Deckanstrich "
                 "seidenmatt. Beschläge demontieren und wieder montieren."),
            _pos("05.03", "Kassettentüren restaurieren und lackieren", "Stk", "sonstiges",
                 "Historische Kassettentür reinigen, anschleifen, Fehlstellen "
                 "ausbessern, 2× mit passendem Lack beschichten. Beschläge separat."),
        ]),
    ]
}


TEMPLATE_DACHAUSBAU = {
    "gruppen": [
        _gr("01", "Vorarbeiten Gipskarton", [
            _pos("01.01", "Gipskartonflächen spachteln Q3", "m²", "vorarbeit",
                 "Gipskartonflächen in Oberflächengüte Q3: Fugen, Schraubenköpfe und "
                 "Gesamtfläche vollflächig glättspachteln, schleifen."),
            _pos("01.02", "Schleifen und Entstauben", "m²", "vorarbeit",
                 "Gespachtelte Flächen feingeschliffen, abgesaugt, staubfrei für "
                 "nachfolgenden Anstrich vorbereitet."),
            _pos("01.03", "Vorstrich/Haftgrund Gipskarton", "m²", "vorarbeit",
                 "Haftgrundierung auf Gipskartonflächen als Sperr- und Haftschicht "
                 "für nachfolgenden Dispersionsanstrich."),
        ]),
        _gr("02", "Dachschrägen", [
            _pos("02.01", "Dachschräge streichen — Dispersion matt, weiß", "m²", "wand",
                 "Geneigte Dachschrägen mit matter Innendispersion weiß, 2-lagig, "
                 "fugen- und streifenfrei gerollt."),
            _pos("02.02", "Kehlen und Gratüberbrückungen nacharbeiten", "m", "vorarbeit",
                 "Innen- und Außenkanten zwischen Schräge und Wand armieren, spachteln, "
                 "schleifen, vorstreichen."),
        ]),
        _gr("03", "Trockenbauwände", [
            _pos("03.01", "Wandanstrich Trockenbau — Dispersion", "m²", "wand",
                 "Trockenbauwände mit matter Innendispersion weiß, 2-lagig, "
                 "scheuerbeständig Klasse 3."),
            _pos("03.02", "Wandanstrich Installationswand Bad — Silikat", "m²", "wand",
                 "Feuchteresistenter Silikatanstrich auf GKBI-Platten im Badbereich, "
                 "2-lagig, schimmelhemmend."),
        ]),
        _gr("04", "Holzbauteile", [
            _pos("04.01", "Sichtdachstuhl Holzlasur", "m²", "sonstiges",
                 "Sichtbaren Dachstuhl (Sparren, Pfetten) reinigen, 2× mit atmungs"
                 "aktiver Holzlasur behandeln, Farbton bauseits."),
            _pos("04.02", "Sichtbare Dachbalken — Lasur farblos", "m", "sonstiges",
                 "Einzelne sichtbare Holzbalken mit farbloser, UV-beständiger Lasur "
                 "2× behandeln."),
            _pos("04.03", "Holzvertäfelung an Drempeln lasieren", "m²", "sonstiges",
                 "Drempelverkleidungen aus Holz anschleifen, 2× lasieren, Farbton "
                 "auf Dachstuhl abgestimmt."),
        ]),
        _gr("05", "Deckenanstriche und Details", [
            _pos("05.01", "Deckenanstrich DG", "m²", "decke",
                 "Decke im Dachgeschoss mit Innendispersion weiß, 2-lagig."),
            _pos("05.02", "Dachflächenfenster-Leibungen streichen", "Stk", "sonstiges",
                 "Leibungen rund um Dachflächenfenster (z. B. Velux) spachteln, "
                 "schleifen, 2× streichen."),
            _pos("05.03", "Treppenaufgang DG — Wandanstrich stoßfest", "m²", "wand",
                 "Treppenaufgang zum Dachgeschoss mit scheuerbeständiger Dispersion "
                 "Klasse 1, 2-lagig."),
        ]),
    ]
}


SEED_TEMPLATES: list[dict] = [
    {
        "name": "Einfamilienhaus Standard — Malerarbeiten",
        "description": (
            "Typische Malerarbeiten für ein freistehendes Einfamilienhaus: "
            "Vorarbeiten, Wand- und Deckenanstriche in Wohnräumen und "
            "Feuchträumen, Treppenhaus und Details."
        ),
        "category": "einfamilienhaus",
        "template_data": TEMPLATE_EINFAMILIENHAUS,
    },
    {
        "name": "Wohnanlage Grundausstattung — Malerarbeiten",
        "description": (
            "Größerer Scope für Mehrfamilienhaus oder Wohnanlage: Wohnungen, "
            "Stiegenhäuser, Keller, Waschküche, Fahrradraum, Müllraum."
        ),
        "category": "wohnanlage",
        "template_data": TEMPLATE_WOHNANLAGE,
    },
    {
        "name": "Bürogebäude — Malerarbeiten",
        "description": (
            "Malerarbeiten für gewerbliche Büroflächen: abwaschbare "
            "Beschichtungen, Brandschutzanstriche F30, Sanitärbereiche, "
            "Verkehrsflächen und Akzentwände in Corporate-Farben."
        ),
        "category": "buero",
        "template_data": TEMPLATE_BUERO,
    },
    {
        "name": "Sanierung Altbau — Malerarbeiten",
        "description": (
            "Sanierung bestehender Substanz: Altlackentfernung, Stuck"
            "restaurierung, Kalkfarbe und Silikatanstriche, Holzbauteile "
            "aufarbeiten. Vorarbeiten dominant."
        ),
        "category": "sanierung",
        "template_data": TEMPLATE_SANIERUNG,
    },
    {
        "name": "Dachgeschossausbau — Malerarbeiten",
        "description": (
            "Dachausbau mit Dachschrägen, Gipskartonwänden und Trockenbau-"
            "Oberflächen. Inklusive Sichtdachstuhl-Behandlung und "
            "Dachflächenfenster-Leibungen."
        ),
        "category": "dachausbau",
        "template_data": TEMPLATE_DACHAUSBAU,
    },
]


# ---------------------------------------------------------------------------
# Migration body
# ---------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "lv_templates" not in inspector.get_table_names():
        op.create_table(
            "lv_templates",
            sa.Column(
                "id",
                sa.Uuid(),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("category", sa.String(50), nullable=False),
            sa.Column("gewerk", sa.String(100), nullable=False),
            sa.Column(
                "is_system",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "created_by_user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "template_data",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )
        # Index for the "my templates" and "system templates" filters
        # — both queries predicate on is_system. Small win on large
        # user-template sets; harmless on the seeded-only case.
        op.create_index(
            "ix_lv_templates_is_system",
            "lv_templates",
            ["is_system"],
        )
        op.create_index(
            "ix_lv_templates_created_by_user_id",
            "lv_templates",
            ["created_by_user_id"],
        )

    # Seed system templates. Guarded with a count check so a rerun on a
    # DB that already has them (e.g. someone downgraded then upgraded)
    # doesn't duplicate. Identification via ``is_system=TRUE`` + ``name``
    # is sufficient because system names are unique by product design.
    existing = bind.execute(
        sa.text(
            "SELECT name FROM lv_templates WHERE is_system = TRUE"
        )
    ).scalars().all()
    existing_names = set(existing)

    missing = [t for t in SEED_TEMPLATES if t["name"] not in existing_names]
    if missing:
        # Use a lightweight Table reference for bulk_insert so the JSONB
        # dict values are adapted correctly via SQLAlchemy's type system.
        tbl = sa.table(
            "lv_templates",
            sa.column("name", sa.String),
            sa.column("description", sa.Text),
            sa.column("category", sa.String),
            sa.column("gewerk", sa.String),
            sa.column("is_system", sa.Boolean),
            sa.column("created_by_user_id", sa.Uuid),
            sa.column("template_data", postgresql.JSONB),
        )
        op.bulk_insert(
            tbl,
            [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "category": t["category"],
                    "gewerk": "malerarbeiten",
                    "is_system": True,
                    "created_by_user_id": None,
                    "template_data": t["template_data"],
                }
                for t in missing
            ],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "lv_templates" in inspector.get_table_names():
        for idx in (
            "ix_lv_templates_created_by_user_id",
            "ix_lv_templates_is_system",
        ):
            try:
                op.drop_index(idx, table_name="lv_templates")
            except Exception:
                pass
        op.drop_table("lv_templates")
