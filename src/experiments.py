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
    external_links: list[tuple[str, str]] = field(default_factory=list)  # (label, url)
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
        external_links=[
            ("HubSpot Workflow · flow 1805564502",
             "https://app.hubspot.com/workflows/6215805/platform/flow/1805564502/edit"),
            ("HubSpot Workflow · flow 1798965900",
             "https://app.hubspot.com/workflows/6215805/platform/flow/1798965900/edit"),
        ],
        funnel_baseline={
            # Constante de delivery WA (no hay API live de Infobip — promedio historico)
            "wa_delivery_ratio": 0.77,
        },
    ),
    Experiment(
        slug="preoferta-temprana",
        title="Pre-Oferta",
        start_date="2026-05-07",
        end_date=None,
        design_doc_url="https://docs.google.com/document/d/1HBqhNVAopMoAyn0aN3TiOsNReG8aKfLPkgcinuFmPJM/edit",
        results_doc_url=None,
        page="pages/2_PreOferta.py",
        description=(
            "EXP-003 · Pre-Oferta Temprana (MX). Probar si revelar una pre-oferta "
            "vía WhatsApp antes de la asignación mejora la CVR de asignado → cierre. "
            "Split 95/5 sobre el flujo de seller en México."
        ),
        attachments=[],
        funnel_baseline={
            "wa_delivery_ratio": 0.80,
            "landing_sheet_id": "1_EMQesd_n67wSqReYaTdJtSd3uvZsb7GXPRD6LyrJN4",
        },
    ),
]
