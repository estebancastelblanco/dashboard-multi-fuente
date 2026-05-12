"""Registry de experimentos — cada uno es una card en el selector home."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Experiment:
    slug: str
    title: str
    start_date: str            # YYYY-MM-DD
    end_date: str | None       # None = en curso
    design_doc_url: str | None
    results_doc_url: str | None
    page: str                  # Ruta a la pagina .py en pages/
    description: str = ""
    attachments: list[str] = field(default_factory=list)  # rutas relativas a docs embebidos
    # Etapas del funnel que NO tenemos en vivo (delivery/open stats de WA).
    # Los valores live se computan en la página.
    funnel_baseline: dict = field(default_factory=dict)


REGISTRY: list[Experiment] = [
    Experiment(
        slug="fakedoor-habicapital",
        title="FakeDoor Habicapital",
        start_date="2026-04-20",
        end_date=None,
        design_doc_url="https://docs.google.com/document/d/1kjJMrth-iyedRHG2_Og3bfKcvcPPpWmRvF_tqxqN64E/edit",
        results_doc_url="https://docs.google.com/document/d/1SN9dHza6_qKDLvyLiSK4LvJagLGzTMdcoACPlZ8uNYc/edit",
        page="pages/1_FakeDoor_Habicapital.py",
        description=(
            "Crédito de Libre Inversión con Garantía Hipotecaria al 20% EA "
            "sobre la base de descartes de Habi Sellers. A/B AH=84m vs BH=120m."
        ),
        attachments=["experiments/fakedoor_habicapital/funnel.md"],
        funnel_baseline={
            # Constante de delivery WA (no hay API live de Infobip — promedio historico)
            "wa_delivery_ratio": 0.77,
        },
    ),
    Experiment(
        slug="demo-multi-fuente",
        title="Demo · multi-fuente",
        start_date="2026-05-12",
        end_date=None,
        design_doc_url=None,
        results_doc_url=None,
        page="pages/2_Demo_multi_fuente.py",
        description=(
            "Dashboard de demostración: 3 tablas en vivo (BigQuery, HubSpot, "
            "Google Sheets) de máximo 10×4 cada una. Sirve como prueba de "
            "conectividad."
        ),
        attachments=[],
    ),
]
