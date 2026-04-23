"""LV template library.

A template is a frozen LV skeleton (group + position structure with
German Lang-/Kurztext) that a user can spawn a real Leistungsverzeichnis
from. System templates ship with the product (``is_system=True``,
``created_by_user_id=None``) and are seeded by migration 012; users can
also save any of their own LVs as a custom template
(``is_system=False``, ``created_by_user_id=<uuid>``).

The positions themselves live in a single JSONB ``template_data``
column rather than being exploded into ``positionen`` rows. Reasons:

* A template has no quantities, no prices, and no per-room calculation
  proof — so it doesn't fit the ``Position`` shape cleanly (``menge``
  and ``einheitspreis`` would always be null, ``berechnungsnachweise``
  would always be empty).
* Copying the template into a new LV is a single JSONB read + a bulk
  insert of ``Position`` rows — no join dance to stitch groups back
  together at instantiation time.
* Editing a template is atomic: replace the JSONB blob, done. No
  cascade cleanup of orphaned rows.

``template_data`` shape (mirrors the DB model so the copy step is a
direct field-to-field mapping):

    {
      "gruppen": [
        {
          "nummer": "01",
          "bezeichnung": "Vorarbeiten",
          "positionen": [
            {
              "positions_nummer": "01.01",
              "kurztext": "Untergrund reinigen und prüfen",
              "langtext": "...",
              "einheit": "m²",
              "kategorie": "vorarbeit"
            }
          ]
        }
      ]
    }

``kategorie`` is an informational hint for the template preview UI
(``wand`` | ``decke`` | ``boden`` | ``vorarbeit`` | ``sonstiges``).
The wall/ceiling auto-sync in ``sync_wall_areas`` keys off the
position's kurztext keywords, not this field — keep the kurztext
Austrian-idiomatic so the routing works.
"""

import uuid
from datetime import datetime

from sqlalchemy import String, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class LVTemplate(Base):
    __tablename__ = "lv_templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    # One of: einfamilienhaus | wohnanlage | buero | sanierung | dachausbau
    # Kept as free-form String(50) so we can add categories without a
    # schema migration — the frontend filter uses a fixed set and
    # ignores unknowns.
    category: Mapped[str] = mapped_column(String(50))
    # One of: malerarbeiten (v17 only ships malerarbeiten templates, but
    # the field is here so Elektro/Sanitär/etc. templates can be added
    # later without a schema change).
    gewerk: Mapped[str] = mapped_column(String(100))
    # System templates are seeded by migration. The API rejects DELETE
    # on ``is_system=True`` regardless of who the caller is.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    # NULL for system templates; set to the creating user's id for
    # user-saved templates. We nullable-FK-set-null on user delete so
    # an orphaned custom template still renders in the library with a
    # generic "Eigene Vorlage" badge instead of 500ing.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    template_data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    created_by: Mapped["User | None"] = relationship()


from app.db.models.user import User  # noqa: E402
