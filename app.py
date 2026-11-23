import asyncio
import json
import math
import os
import random
import time
import urllib.request
from datetime import date, timedelta
from typing import Any, Optional

from databricks.sdk import WorkspaceClient
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

app = FastAPI(title="Portail BI Supply Chain & 3D - Databricks Apps")

# --- Données en cache mémoire (simule un cache rechargé en fin de batch) ---
_CACHE: dict[str, Any] = {}

SITES = ["Hub Atlantique", "Hub Nord", "Hub Rhône", "Hub Sud"]
FLUX_TYPES = ["Réception", "Expédition", "Cross-Dock"]
CLASSES_ABC = ["A", "B", "C"]
FAMILLES = ["Aéronautique", "Industrie", "Énergie", "Maintenance", "Pièces Critiques"]
TRANSPORTEURS = ["Geodis", "XPO Logistics", "DB Schenker", "Kuehne+Nagel", "Heppner"]


def _build_demo_dataset() -> dict[str, Any]:
    """Génère un jeu de données Supply Chain réaliste sur 90 jours."""
    rng = random.Random(42)
    today = date.today()
    operations: list[dict[str, Any]] = []

    site_perf = {
        "Hub Atlantique": {"otd_bias": 0.94, "vol_mult": 1.25},
        "Hub Nord": {"otd_bias": 0.89, "vol_mult": 1.10},
        "Hub Rhône": {"otd_bias": 0.92, "vol_mult": 1.00},
        "Hub Sud": {"otd_bias": 0.86, "vol_mult": 0.85},
    }

    op_id = 10000
    for day_offset in range(89, -1, -1):
        d = today - timedelta(days=day_offset)
        d_str = d.isoformat()
        is_weekend = d.weekday() >= 5
        nb_ops_day = 8 if is_weekend else 22

        for _ in range(nb_ops_day):
            op_id += 1
            site = rng.choices(SITES, weights=[32, 26, 24, 18])[0]
            flux = rng.choices(FLUX_TYPES, weights=[42, 46, 12])[0]
            abc = rng.choices(CLASSES_ABC, weights=[25, 35, 40])[0]
            famille = rng.choice(FAMILLES)
            transporteur = rng.choice(TRANSPORTEURS)

            perf = site_perf[site]
            # Probabilité de retard légèrement plus élevée en classe A et sur certains sites
            p_on_time = perf["otd_bias"] - (0.03 if flux == "Expédition" else 0.0)
            r = rng.random()
            if r < p_on_time:
                statut = "À l'heure"
                retard_h = 0.0
            elif r < p_on_time + 0.065:
                statut = "En retard"
                retard_h = round(rng.uniform(1.0, 6.5), 1)
            else:
                statut = "Critique"
                retard_h = round(rng.uniform(7.0, 28.0), 1)

            palettes = max(1, int(rng.gauss(18 * perf["vol_mult"], 7)))
            cout_unit = round(rng.uniform(11.5, 24.0) + (4.5 if statut == "Critique" else 0.0), 2)

            operations.append(
                {
                    "id": f"CMD-{op_id}",
                    "date": d_str,
                    "days_ago": day_offset,
                    "site": site,
                    "flux": flux,
                    "abc": abc,
                    "famille": famille,
                    "transporteur": transporteur,
                    "statut": statut,
                    "retard_h": retard_h,
                    "palettes": palettes,
                    "cout": round(palettes * cout_unit, 2),
                }
            )

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sites": SITES,
        "flux_types": FLUX_TYPES,
        "classes_abc": CLASSES_ABC,
        "operations": operations,
    }


def _build_demo_stocks_3d() -> dict[str, Any]:
    """Génère 32 000 emplacements (4 sites x 20 allées x 80 travées x 5 niveaux) en tableaux plats."""
    sites_idx: list[int] = []
    allees: list[int] = []
    travees: list[int] = []
    niveaux: list[int] = []
    taux_list: list[int] = []
    quantites: list[int] = []

    for s_idx in range(len(SITES)):
        for a in range(20):
            for t in range(80):
                for n in range(5):
                    # Motif réaliste : niveaux bas plus remplis et zones A plus denses
                    base = ((s_idx * 31 + a * 17 + t * 13 + n * 23) % 85)
                    bonus_niveau = max(0, (4 - n) * 5)
                    taux = min(100, base + bonus_niveau)
                    sites_idx.append(s_idx)
                    allees.append(a)
                    travees.append(t)
                    niveaux.append(n)
                    taux_list.append(taux)
                    quantites.append((taux * 110) // 100)

    return {
        "sites": SITES,
        "count": len(allees),
        "site_idx": sites_idx,
        "allee": allees,
        "travee": travees,
        "niveau": niveaux,
        "taux": taux_list,
        "quantite": quantites,
    }


def _get_dataset() -> dict[str, Any]:
    if "dashboard" not in _CACHE:
        _CACHE["dashboard"] = _build_demo_dataset()
    return _CACHE["dashboard"]


def _get_stocks_3d() -> dict[str, Any]:
    if "stocks_3d" not in _CACHE:
        _CACHE["stocks_3d"] = _build_demo_stocks_3d()
    return _CACHE["stocks_3d"]


@app.get("/api/me")
def get_current_user(request: Request):
    """Récupère l'identité SSO injectée par Databricks Apps."""
    email = request.headers.get("x-forwarded-email", "utilisateur.demo@entreprise.fr")
    has_obo = bool(request.headers.get("x-forwarded-access-token"))
    return {
        "email": email,
        "obo_active": has_obo,
        "genie_configured": bool(os.getenv("GENIE_SPACE_ID")),
        "warehouse_id": os.getenv("DATABRICKS_WAREHOUSE_ID", "mode_demo"),
    }


@app.get("/api/dashboard-data")
def get_dashboard_data():
    """Renvoie les opérations en cache (filtrage croisé instantané côté navigateur)."""
    return _get_dataset()


@app.get("/api/stocks-3d")
def get_stocks_3d_endpoint():
    """Renvoie les emplacements 3D en tableaux plats optimisés pour InstancedMesh."""
    return _get_stocks_3d()


@app.post("/api/cache/refresh")
def refresh_cache():
    """Point d'entrée à appeler en fin de batch Databricks Workflow pour rafraîchir le cache."""
    _CACHE.clear()
    ds = _get_dataset()
    _get_stocks_3d()
    return {"status": "ok", "refreshed_at": ds["generated_at"]}


def _run_genie_query(
    token: str, space_id: str, question: str, conversation_id: Optional[str]
) -> dict[str, Any]:
    """Appelle l'API officielle Databricks AI/BI Genie au nom de l'utilisateur (OBO)."""
    w = WorkspaceClient(
        host=os.environ["DATABRICKS_HOST"],
        token=token,
        auth_type="pat",
    )
    if conversation_id:
        msg = w.genie.create_message_and_wait(
            space_id=space_id, conversation_id=conversation_id, content=question
        )
    else:
        msg = w.genie.start_conversation_and_wait(space_id=space_id, content=question)

    parts: list[str] = []
    generated_sql: Optional[str] = None

    for att in msg.attachments or []:
        if att.text and att.text.content:
            parts.append(att.text.content)
        if att.query:
            generated_sql = att.query.query
            if att.query.description and not parts:
                parts.append(att.query.description)
            res = w.genie.get_message_attachment_query_result(
                space_id=space_id,
                conversation_id=msg.conversation_id,
                message_id=msg.id,
                attachment_id=att.attachment_id,
            )
            stmt = res.statement_response
            if stmt and stmt.result:
                rows = stmt.result.data_array
                if not rows and stmt.result.external_links:
                    url = stmt.result.external_links[0].external_link
                    with urllib.request.urlopen(url, timeout=15) as resp:
                        rows = json.loads(resp.read().decode("utf-8"))
                if rows and stmt.manifest and stmt.manifest.schema:
                    cols = [c.name for c in (stmt.manifest.schema.columns or [])]
                    preview = "\n".join(str(dict(zip(cols, r))) for r in rows[:5])
                    parts.append(f"Aperçu ({len(rows)} lignes) :\n{preview}")

    return {
        "conversation_id": msg.conversation_id,
        "answer": "\n\n".join(parts) or "Requête exécutée sans résultat textuel.",
        "sql": generated_sql,
        "mode": "genie_live",
    }


def _demo_smart_answer(question: str, allowed_sites: list[str]) -> dict[str, Any]:
    """Assistant démo qui calcule de vraies statistiques sur le jeu de données filtré par RLS."""
    q = question.lower()
    ops = [o for o in _get_dataset()["operations"] if o["site"] in allowed_sites]
    total = len(ops)
    if total == 0:
        return {"answer": "Aucune donnée visible sur votre périmètre actuel.", "sql": None, "mode": "demo"}

    if any(k in q for k in ("retard", "critique", "otd", "service")):
        par_site: dict[str, list[dict[str, Any]]] = {}
        for o in ops:
            par_site.setdefault(o["site"], []).append(o)
        lignes = []
        for s, items in sorted(par_site.items()):
            on_time = sum(1 for x in items if x["statut"] == "À l'heure")
            crit = sum(1 for x in items if x["statut"] == "Critique")
            otd = 100.0 * on_time / len(items)
            lignes.append(f"- {s} : OTD {otd:.1f}% ({crit} retards critiques sur {len(items)} ordres)")
        sql = (
            "SELECT site, ROUND(100.0 * AVG(CASE WHEN statut = 'À l\\'heure' THEN 1 ELSE 0 END), 1) AS otd_pct,\n"
            "       SUM(CASE WHEN statut = 'Critique' THEN 1 ELSE 0 END) AS critiques\n"
            "FROM gold.fct_operations_logistique\nGROUP BY site ORDER BY otd_pct ASC;"
        )
        return {
            "answer": "Performance OTD et retards critiques sur votre périmètre :\n" + "\n".join(lignes),
            "sql": sql,
            "mode": "demo",
        }

    if any(k in q for k in ("rack", "stock", "3d", "satur", "rempliss", "emplacement")):
        stocks = _get_stocks_3d()
        allowed_idx = {i for i, name in enumerate(stocks["sites"]) if name in allowed_sites}
        satures = sum(
            1
            for s_i, taux in zip(stocks["site_idx"], stocks["taux"])
            if s_i in allowed_idx and taux >= 80
        )
        total_emp = sum(1 for s_i in stocks["site_idx"] if s_i in allowed_idx)
        pct = (100.0 * satures / total_emp) if total_emp else 0.0
        sql = (
            "SELECT COUNT(*) AS emplacements_satures, ROUND(100.0 * COUNT(*) / total, 1) AS pct\n"
            "FROM gold.dim_stocks_3d\nWHERE taux_remplissage >= 80;"
        )
        return {
            "answer": (
                f"Analyse des emplacements 3D sur votre périmètre ({', '.join(allowed_sites)}) :\n"
                f"- {satures:,} emplacements saturés (>= 80%) sur {total_emp:,} ({pct:.1f}%).\n"
                "Astuce : utilisez l'onglet Entrepôt 3D et le filtre 'Classe A (>75%)' pour les isoler."
            ).replace(",", " "),
            "sql": sql,
            "mode": "demo",
        }

    if any(k in q for k in ("cout", "coût", "budget", "euro", "transporteur")):
        par_trans: dict[str, float] = {}
        for o in ops:
            par_trans[o["transporteur"]] = par_trans.get(o["transporteur"], 0.0) + o["cout"]
        top = sorted(par_trans.items(), key=lambda x: x[1], reverse=True)
        lignes = [f"- {t} : {c:,.0f} €".replace(",", " ") for t, c in top]
        sql = (
            "SELECT transporteur, ROUND(SUM(cout_total), 0) AS cout_eur\n"
            "FROM gold.fct_operations_logistique\nGROUP BY transporteur ORDER BY cout_eur DESC;"
        )
        return {
            "answer": "Répartition des coûts logistiques par transporteur (90 jours) :\n" + "\n".join(lignes),
            "sql": sql,
            "mode": "demo",
        }

    total_pal = sum(o["palettes"] for o in ops)
    on_time_global = 100.0 * sum(1 for o in ops if o["statut"] == "À l'heure") / total
    return {
        "answer": (
            f"Synthèse sur votre périmètre ({len(allowed_sites)} site(s), {total:,} ordres) :\n"
            f"- Volume total : {total_pal:,} palettes\n"
            f"- Taux de service OTD global : {on_time_global:.1f}%\n\n"
            "En mode démo, essayez des questions sur les *retards*, les *coûts transporteurs* ou les *racks saturés* "
            "(ou ajoutez GENIE_SPACE_ID dans app.yaml pour brancher votre vrai espace AI/BI Genie)."
        ).replace(",", " "),
        "sql": "SELECT COUNT(*) AS ordres, SUM(palettes) AS total_palettes FROM gold.fct_operations_logistique;",
        "mode": "demo",
    }


@app.post("/api/genie/ask")
async def ask_genie(payload: dict[str, Any], request: Request):
    question = str(payload.get("question", "")).strip()
    conversation_id = payload.get("conversation_id")
    allowed_sites = payload.get("allowed_sites") or SITES
    space_id = os.getenv("GENIE_SPACE_ID")
    user_token = request.headers.get("x-forwarded-access-token")

    if space_id and user_token:
        return await asyncio.to_thread(
            _run_genie_query, user_token, space_id, question, conversation_id
        )
    return _demo_smart_answer(question, allowed_sites)


@app.get("/", response_class=HTMLResponse)
def index():
    return """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Portail BI Supply Chain — Databricks Apps</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
  <style>
    :root {
      --bg: #0b1120;
      --panel: #111827;
      --panel-2: #1e293b;
      --border: #273449;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --accent-2: #f97316;
      --green: #10b981;
      --amber: #f59e0b;
      --red: #ef4444;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      display: flex;
      height: 100vh;
      overflow: hidden;
    }
    /* Sidebar */
    aside.nav {
      width: 245px;
      background: var(--panel);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      padding: 18px 14px;
      flex-shrink: 0;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 4px 8px 18px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 16px;
    }
    .brand-dot {
      width: 12px; height: 12px; border-radius: 3px;
      background: linear-gradient(135deg, var(--accent-2), var(--accent));
    }
    .brand h1 { font-size: 15px; margin: 0; font-weight: 700; letter-spacing: 0.2px; }
    .brand span { font-size: 11px; color: var(--muted); display: block; }
    .nav-btn {
      width: 100%;
      background: transparent;
      border: 1px solid transparent;
      color: var(--muted);
      padding: 10px 12px;
      border-radius: 8px;
      text-align: left;
      font-size: 13.5px;
      font-weight: 500;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 4px;
      transition: all 0.15s ease;
    }
    .nav-btn:hover { background: rgba(56,189,248,0.08); color: var(--text); }
    .nav-btn.active {
      background: rgba(56,189,248,0.14);
      border-color: rgba(56,189,248,0.35);
      color: var(--accent);
      font-weight: 600;
    }
    .nav-btn.locked { opacity: 0.38; cursor: not-allowed; }
    .role-box {
      margin-top: auto;
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
      font-size: 12px;
    }
    .role-box label { display: block; color: var(--muted); margin-bottom: 6px; font-weight: 600; }
    .role-box select {
      width: 100%;
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 6px 8px;
      font-size: 12px;
    }
    .sso-pill {
      margin-top: 10px;
      font-size: 11px;
      color: var(--muted);
      word-break: break-all;
    }
    /* Main area */
    .workspace {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    /* Top filter bar */
    header.topbar {
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      padding: 12px 20px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
    }
    .filters-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .filter-group {
      display: flex;
      flex-direction: column;
      gap: 3px;
    }
    .filter-group label {
      font-size: 10.5px;
      text-transform: uppercase;
      letter-spacing: 0.4px;
      color: var(--muted);
    }
    .filter-group select, .filter-group input {
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 6px 10px;
      font-size: 12.5px;
      min-width: 125px;
    }
    .actions-bar { display: flex; gap: 8px; align-items: center; }
    .btn {
      background: var(--panel-2);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 7px 12px;
      font-size: 12.5px;
      font-weight: 500;
      cursor: pointer;
    }
    .btn:hover { border-color: var(--accent); color: var(--accent); }
    .btn-primary {
      background: var(--accent-2);
      border-color: var(--accent-2);
      color: #fff;
    }
    .btn-primary:hover { opacity: 0.92; color: #fff; }
    /* Views container */
    main.content {
      flex: 1;
      overflow-y: auto;
      padding: 20px;
    }
    .view { display: none; }
    .view.active { display: block; }
    /* KPI Grid */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(160px, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }
    @media (max-width: 1200px) { .kpi-grid { grid-template-columns: repeat(3, 1fr); } }
    .kpi-card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px 16px;
    }
    .kpi-label { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
    .kpi-value { font-size: 24px; font-weight: 700; letter-spacing: -0.4px; }
    .kpi-sub { font-size: 11.5px; margin-top: 6px; color: var(--muted); }
    .up { color: var(--green); }
    .down { color: var(--red); }
    /* Charts Grid */
    .charts-grid {
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 16px;
      margin-bottom: 16px;
    }
    .charts-grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }
    @media (max-width: 1050px) {
      .charts-grid, .charts-grid-2 { grid-template-columns: 1fr; }
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
    }
    .card-title { font-size: 14px; font-weight: 600; margin: 0; }
    .card-sub { font-size: 11.5px; color: var(--muted); }
    .chart-box { position: relative; height: 260px; }
    /* Table view */
    .table-tools {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }
    .search-input {
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 12px;
      font-size: 13px;
      width: 280px;
    }
    .table-wrap {
      overflow-x: auto;
      border: 1px solid var(--border);
      border-radius: 8px;
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); }
    th {
      background: var(--panel-2);
      color: var(--muted);
      font-weight: 600;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }
    th:hover { color: var(--text); }
    tr:hover td { background: rgba(56,189,248,0.05); }
    .tag {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11.5px;
      font-weight: 600;
    }
    .tag-ok { background: rgba(16,185,129,0.16); color: #34d399; }
    .tag-warn { background: rgba(245,158,11,0.18); color: #fbbf24; }
    .tag-crit { background: rgba(239,68,68,0.18); color: #f87171; }
    .pagination {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 12px;
      font-size: 12.5px;
      color: var(--muted);
    }
    /* 3D view */
    #viewer-card {
      position: relative;
      height: calc(100vh - 150px);
      min-height: 460px;
      background: #090f1d;
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
    }
    #hud-3d {
      position: absolute;
      top: 14px; left: 14px;
      background: rgba(15,23,42,0.9);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
      font-size: 12.5px;
      max-width: 360px;
      z-index: 5;
    }
    .legend-row { display: flex; gap: 10px; margin-top: 8px; font-size: 11.5px; color: var(--muted); }
    .dot { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; }
    /* Genie Drawer */
    .genie-drawer {
      position: fixed;
      top: 0; right: -420px;
      width: 400px;
      max-width: 95vw;
      height: 100vh;
      background: var(--panel);
      border-left: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      z-index: 30;
      transition: right 0.22s ease;
      box-shadow: -8px 0 30px rgba(0,0,0,0.45);
    }
    .genie-drawer.open { right: 0; }
    .genie-header {
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .genie-messages {
      flex: 1;
      overflow-y: auto;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .g-msg {
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 13px;
      line-height: 1.45;
      white-space: pre-wrap;
    }
    .g-msg.user { background: rgba(56,189,248,0.12); border-color: rgba(56,189,248,0.3); }
    .sql-block {
      margin-top: 8px;
      background: #070b14;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px;
      font-family: ui-monospace, monospace;
      font-size: 11px;
      color: #7dd3fc;
      overflow-x: auto;
    }
    .quick-prompts {
      padding: 8px 14px 0;
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .chip {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--muted);
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 11.5px;
      cursor: pointer;
    }
    .chip:hover { color: var(--accent); border-color: var(--accent); }
    .genie-form {
      padding: 12px 14px;
      border-top: 1px solid var(--border);
      display: flex;
      gap: 8px;
    }
    .genie-form input {
      flex: 1;
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 13px;
    }
  </style>
</head>
<body>
  <aside class="nav">
    <div class="brand">
      <div class="brand-dot"></div>
      <div>
        <h1>SupplyChain BI</h1>
        <span>Databricks Apps Portal</span>
      </div>
    </div>

    <button class="nav-btn active" data-view="overview"><span>Synthèse KPIs</span><span>01</span></button>
    <button class="nav-btn" data-view="operations"><span>Opérations &amp; Retards</span><span>02</span></button>
    <button class="nav-btn" data-view="warehouse3d"><span>Entrepôt 3D (Stocks)</span><span>03</span></button>

    <div class="role-box">
      <label for="role-select">Profil d'accès (Démo RLS)</label>
      <select id="role-select">
        <option value="director">Direction (Tous les sites)</option>
        <option value="mgr_ouest">Resp. Ouest (Atlantique + Sud)</option>
        <option value="ops_nord">Opérateur Nord (Hub Nord seul)</option>
      </select>
      <div class="sso-pill" id="sso-info">SSO : chargement...</div>
      <div class="sso-pill" id="cache-info" style="margin-top:4px;">Cache batch : ...</div>
    </div>
  </aside>

  <div class="workspace">
    <header class="topbar">
      <div class="filters-bar">
        <div class="filter-group">
          <label for="f-site">Site / Hub</label>
          <select id="f-site"></select>
        </div>
        <div class="filter-group">
          <label for="f-period">Période</label>
          <select id="f-period">
            <option value="7">7 derniers jours</option>
            <option value="30" selected>30 derniers jours</option>
            <option value="90">90 derniers jours</option>
          </select>
        </div>
        <div class="filter-group">
          <label for="f-flux">Flux</label>
          <select id="f-flux">
            <option value="ALL">Tous les flux</option>
            <option value="Réception">Réception</option>
            <option value="Expédition">Expédition</option>
            <option value="Cross-Dock">Cross-Dock</option>
          </select>
        </div>
        <div class="filter-group">
          <label for="f-abc">Classe ABC</label>
          <select id="f-abc">
            <option value="ALL">Toutes classes</option>
            <option value="A">Classe A</option>
            <option value="B">Classe B</option>
            <option value="C">Classe C</option>
          </select>
        </div>
        <div class="filter-group">
          <label for="f-statut">Statut</label>
          <select id="f-statut">
            <option value="ALL">Tous statuts</option>
            <option value="À l'heure">À l'heure</option>
            <option value="En retard">En retard</option>
            <option value="Critique">Critique</option>
          </select>
        </div>
      </div>

      <div class="actions-bar">
        <button class="btn" id="btn-reset">Réinitialiser</button>
        <button class="btn" id="btn-refresh">Recharger batch</button>
        <button class="btn btn-primary" id="btn-genie">Chat IA Genie</button>
      </div>
    </header>

    <main class="content">
      <!-- VUE 1 : SYNTHESE KPIS -->
      <section id="view-overview" class="view active">
        <div class="kpi-grid">
          <div class="kpi-card">
            <div class="kpi-label">Taux de service (OTD)</div>
            <div class="kpi-value" id="kpi-otd">--%</div>
            <div class="kpi-sub" id="kpi-otd-sub">Objectif cible : 93,0 %</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Ordres traités</div>
            <div class="kpi-value" id="kpi-orders">--</div>
            <div class="kpi-sub" id="kpi-orders-sub">-- palettes manutentionnées</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Retards critiques</div>
            <div class="kpi-value" id="kpi-crit">--</div>
            <div class="kpi-sub" id="kpi-crit-sub">Retard moyen : -- h</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Coût logistique total</div>
            <div class="kpi-value" id="kpi-cost">-- €</div>
            <div class="kpi-sub" id="kpi-cost-sub">-- € / palette</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Taux d'occupation 3D</div>
            <div class="kpi-value" id="kpi-occ">--%</div>
            <div class="kpi-sub" id="kpi-occ-sub">-- emplacements saturés</div>
          </div>
        </div>

        <div class="charts-grid">
          <div class="card">
            <div class="card-header">
              <h2 class="card-title">Évolution journalière des volumes (palettes)</h2>
              <span class="card-sub">Réceptions vs Expéditions vs Cross-Dock</span>
            </div>
            <div class="chart-box"><canvas id="chart-timeline"></canvas></div>
          </div>
          <div class="card">
            <div class="card-header">
              <h2 class="card-title">Répartition des statuts</h2>
              <span class="card-sub">Cliquez pour filtrer</span>
            </div>
            <div class="chart-box"><canvas id="chart-status"></canvas></div>
          </div>
        </div>

        <div class="charts-grid-2">
          <div class="card">
            <div class="card-header">
              <h2 class="card-title">Performance OTD (%) par site</h2>
              <span class="card-sub">Périmètre autorisé uniquement</span>
            </div>
            <div class="chart-box"><canvas id="chart-sites"></canvas></div>
          </div>
          <div class="card">
            <div class="card-header">
              <h2 class="card-title">Coûts par transporteur (€)</h2>
              <span class="card-sub">Agrégation dynamique</span>
            </div>
            <div class="chart-box"><canvas id="chart-carriers"></canvas></div>
          </div>
        </div>
      </section>

      <!-- VUE 2 : OPERATIONS & RETARDS -->
      <section id="view-operations" class="view">
        <div class="card">
          <div class="table-tools">
            <input type="text" id="table-search" class="search-input" placeholder="Rechercher ID, transporteur, famille..." />
            <div style="display:flex;gap:8px;align-items:center;">
              <span id="table-count" style="font-size:12.5px;color:var(--muted);">0 lignes</span>
              <button class="btn" id="btn-csv">Exporter CSV</button>
            </div>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th data-sort="id">Ordre</th>
                  <th data-sort="date">Date</th>
                  <th data-sort="site">Site</th>
                  <th data-sort="flux">Flux</th>
                  <th data-sort="abc">ABC</th>
                  <th data-sort="famille">Famille</th>
                  <th data-sort="transporteur">Transporteur</th>
                  <th data-sort="palettes">Palettes</th>
                  <th data-sort="retard_h">Retard (h)</th>
                  <th data-sort="cout">Coût (€)</th>
                  <th data-sort="statut">Statut</th>
                </tr>
              </thead>
              <tbody id="ops-tbody"></tbody>
            </table>
          </div>
          <div class="pagination">
            <span id="page-info">Page 1 / 1</span>
            <div style="display:flex;gap:6px;">
              <button class="btn" id="prev-page">Précédent</button>
              <button class="btn" id="next-page">Suivant</button>
            </div>
          </div>
        </div>
      </section>

      <!-- VUE 3 : ENTREPOT 3D -->
      <section id="view-warehouse3d" class="view">
        <div id="viewer-card">
          <div id="hud-3d">
            <div><strong>Jumeau 3D des Stocks</strong> <span id="stats-3d" style="color:var(--muted);">(chargement...)</span></div>
            <div style="font-size:11px;color:var(--muted);margin-top:3px;">
              Synchronisé avec les filtres Site &amp; Classe ABC | Clic gauche : pivoter | Molette : zoomer
            </div>
            <div id="selection-3d" style="margin-top:8px;color:var(--accent);">Cliquez sur une alvéole pour voir le détail</div>
            <div class="legend-row">
              <span><i class="dot" style="background:#ef4444;"></i>&gt;75% (A)</span>
              <span><i class="dot" style="background:#f59e0b;"></i>40-75% (B)</span>
              <span><i class="dot" style="background:#10b981;"></i>&lt;40% (C)</span>
            </div>
          </div>
        </div>
      </section>
    </main>
  </div>

  <!-- PANNEAU LATERAL GENIE -->
  <div class="genie-drawer" id="genie-drawer">
    <div class="genie-header">
      <div>
        <strong>Assistant IA Genie</strong>
        <div id="genie-mode-label" style="font-size:11px;color:var(--muted);">Connecté au périmètre RLS actif</div>
      </div>
      <button class="btn" id="close-genie">Fermer</button>
    </div>
    <div class="quick-prompts">
      <button class="chip" data-q="Quel est le taux OTD et le nombre de retards critiques par site ?">OTD par site</button>
      <button class="chip" data-q="Combien de racks sont saturés à plus de 80% ?">Racks saturés</button>
      <button class="chip" data-q="Quels transporteurs coûtent le plus cher ?">Coûts transporteurs</button>
    </div>
    <div class="genie-messages" id="genie-messages">
      <div class="g-msg">Posez une question en langage naturel sur les opérations ou les stocks de votre périmètre.</div>
    </div>
    <form class="genie-form" id="genie-form">
      <input id="genie-input" placeholder="Ex: Quels sites ont le plus de retards ?" required />
      <button class="btn btn-primary" type="submit">Envoyer</button>
    </form>
  </div>

  <script>
    // --- Profils d'accès simulés (RLS + visibilité des onglets) ---
    const ROLES = {
      director: { label: 'Direction', sites: ['Hub Atlantique', 'Hub Nord', 'Hub Rhône', 'Hub Sud'], views: ['overview', 'operations', 'warehouse3d'] },
      mgr_ouest: { label: 'Resp. Ouest', sites: ['Hub Atlantique', 'Hub Sud'], views: ['overview', 'operations', 'warehouse3d'] },
      ops_nord: { label: 'Opérateur Nord', sites: ['Hub Nord'], views: ['operations', 'warehouse3d'] }
    };

    const state = {
      role: 'director',
      activeView: 'overview',
      rawOps: [],
      filteredOps: [],
      stocks3d: null,
      sortKey: 'date',
      sortAsc: false,
      page: 1,
      pageSize: 18,
      conversationId: null
    };

    const charts = {};
    let threeState = { initialized: false, scene: null, camera: null, renderer: null, controls: null, mesh: null, highlight: null, visibleIndices: [] };

    // --- Initialisation ---
    async function bootstrap() {
      const [me, dash, s3d] = await Promise.all([
        fetch('/api/me').then(r => r.json()),
        fetch('/api/dashboard-data').then(r => r.json()),
        fetch('/api/stocks-3d').then(r => r.json())
      ]);

      document.getElementById('sso-info').textContent = `SSO : ${me.email} (${me.obo_active ? 'OBO actif' : 'Démo'})`;
      document.getElementById('cache-info').textContent = `Batch : ${dash.generated_at}`;
      document.getElementById('genie-mode-label').textContent = me.genie_configured
        ? 'Mode AI/BI Genie Live (OBO)'
        : 'Mode Démo analytique (prêt pour Genie)';

      state.rawOps = dash.operations;
      state.stocks3d = s3d;

      applyRolePermissions();
      updateAll();
    }

    function getAllowedSites() {
      return ROLES[state.role].sites;
    }

    function applyRolePermissions() {
      const roleCfg = ROLES[state.role];
      // Restreindre les onglets visibles selon le rôle
      document.querySelectorAll('.nav-btn').forEach(btn => {
        const v = btn.dataset.view;
        const allowed = roleCfg.views.includes(v);
        btn.classList.toggle('locked', !allowed);
        btn.disabled = !allowed;
      });
      if (!roleCfg.views.includes(state.activeView)) {
        switchView(roleCfg.views[0]);
      }

      // Alimenter le filtre Site uniquement avec le périmètre autorisé
      const siteSelect = document.getElementById('f-site');
      const prev = siteSelect.value;
      siteSelect.innerHTML = '';
      if (roleCfg.sites.length > 1) {
        siteSelect.innerHTML += `<option value="ALL">Tous mes sites (${roleCfg.sites.length})</option>`;
      }
      roleCfg.sites.forEach(s => {
        siteSelect.innerHTML += `<option value="${s}">${s}</option>`;
      });
      if (roleCfg.sites.includes(prev)) siteSelect.value = prev;
    }

    function switchView(viewId) {
      state.activeView = viewId;
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.view === viewId));
      document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `view-${viewId}`));

      if (viewId === 'warehouse3d') {
        requestAnimationFrame(() => {
          initThreeIfNeeded();
          resizeThree();
          updateThreeColors();
        });
      }
    }

    function getFilters() {
      return {
        site: document.getElementById('f-site').value,
        period: parseInt(document.getElementById('f-period').value, 10),
        flux: document.getElementById('f-flux').value,
        abc: document.getElementById('f-abc').value,
        statut: document.getElementById('f-statut').value,
        search: document.getElementById('table-search').value.trim().toLowerCase()
      };
    }

    function updateAll() {
      const f = getFilters();
      const allowedSites = getAllowedSites();

      state.filteredOps = state.rawOps.filter(o => {
        if (!allowedSites.includes(o.site)) return false;
        if (f.site !== 'ALL' && o.site !== f.site) return false;
        if (o.days_ago >= f.period) return false;
        if (f.flux !== 'ALL' && o.flux !== f.flux) return false;
        if (f.abc !== 'ALL' && o.abc !== f.abc) return false;
        if (f.statut !== 'ALL' && o.statut !== f.statut) return false;
        return true;
      });

      state.page = 1;
      renderKPIs(f, allowedSites);
      renderCharts(allowedSites);
      renderTable(f.search);
      if (threeState.initialized) updateThreeColors();
    }

    function renderKPIs(f, allowedSites) {
      const ops = state.filteredOps;
      const n = ops.length;
      const onTime = ops.filter(o => o.statut === "À l'heure").length;
      const crits = ops.filter(o => o.statut === 'Critique');
      const totalPal = ops.reduce((acc, o) => acc + o.palettes, 0);
      const totalCost = ops.reduce((acc, o) => acc + o.cout, 0);
      const delayed = ops.filter(o => o.retard_h > 0);
      const avgDelay = delayed.length ? delayed.reduce((a, o) => a + o.retard_h, 0) / delayed.length : 0;
      const otd = n ? (100 * onTime / n) : 0;

      document.getElementById('kpi-otd').textContent = `${otd.toFixed(1)} %`;
      document.getElementById('kpi-otd').className = `kpi-value ${otd >= 92 ? 'up' : 'down'}`;
      document.getElementById('kpi-orders').textContent = n.toLocaleString('fr-FR');
      document.getElementById('kpi-orders-sub').textContent = `${totalPal.toLocaleString('fr-FR')} palettes`;
      document.getElementById('kpi-crit').textContent = crits.length.toLocaleString('fr-FR');
      document.getElementById('kpi-crit-sub').textContent = `Retard moyen : ${avgDelay.toFixed(1)} h`;
      document.getElementById('kpi-cost').textContent = `${Math.round(totalCost).toLocaleString('fr-FR')} €`;
      document.getElementById('kpi-cost-sub').textContent = `${totalPal ? (totalCost / totalPal).toFixed(2) : '0.00'} € / palette`;

      // Occupation 3D synchronisée sur le périmètre site/ABC
      if (state.stocks3d) {
        const s3d = state.stocks3d;
        const targetSites = f.site === 'ALL' ? allowedSites : [f.site];
        const allowedIdx = new Set(s3d.sites.map((name, i) => targetSites.includes(name) ? i : -1).filter(i => i >= 0));
        let sumTaux = 0, cnt = 0, sat = 0;
        for (let i = 0; i < s3d.count; i++) {
          if (!allowedIdx.has(s3d.site_idx[i])) continue;
          const t = s3d.taux[i];
          const cls = t > 75 ? 'A' : (t > 40 ? 'B' : 'C');
          if (f.abc !== 'ALL' && cls !== f.abc) continue;
          sumTaux += t;
          cnt++;
          if (t >= 80) sat++;
        }
        const avgOcc = cnt ? (sumTaux / cnt) : 0;
        document.getElementById('kpi-occ').textContent = `${avgOcc.toFixed(1)} %`;
        document.getElementById('kpi-occ-sub').textContent = `${sat.toLocaleString('fr-FR')} empl. saturés (>=80%)`;
      }
    }

    function upsertChart(id, config) {
      if (charts[id]) charts[id].destroy();
      const ctx = document.getElementById(id).getContext('2d');
      charts[id] = new Chart(ctx, config);
    }

    function renderCharts(allowedSites) {
      const ops = state.filteredOps;

      // 1. Timeline par date et flux
      const byDate = {};
      ops.forEach(o => {
        if (!byDate[o.date]) byDate[o.date] = { Réception: 0, Expédition: 0, 'Cross-Dock': 0 };
        byDate[o.date][o.flux] += o.palettes;
      });
      const dates = Object.keys(byDate).sort();
      upsertChart('chart-timeline', {
        type: 'line',
        data: {
          labels: dates.map(d => d.slice(5)),
          datasets: [
            { label: 'Réception', data: dates.map(d => byDate[d]['Réception']), borderColor: '#38bdf8', backgroundColor: 'rgba(56,189,248,0.12)', fill: true, tension: 0.3 },
            { label: 'Expédition', data: dates.map(d => byDate[d]['Expédition']), borderColor: '#f97316', backgroundColor: 'rgba(249,115,22,0.10)', fill: true, tension: 0.3 },
            { label: 'Cross-Dock', data: dates.map(d => byDate[d]['Cross-Dock']), borderColor: '#a855f7', tension: 0.3 }
          ]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { labels: { color: '#cbd5e1' } } },
          scales: {
            x: { ticks: { color: '#94a3b8', maxTicksLimit: 10 }, grid: { color: 'rgba(148,163,184,0.08)' } },
            y: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.08)' } }
          }
        }
      });

      // 2. Statuts (cliquable pour cross-filter)
      const statLabels = ["À l'heure", 'En retard', 'Critique'];
      const statCounts = statLabels.map(st => ops.filter(o => o.statut === st).length);
      upsertChart('chart-status', {
        type: 'doughnut',
        data: {
          labels: statLabels,
          datasets: [{ data: statCounts, backgroundColor: ['#10b981', '#f59e0b', '#ef4444'], borderWidth: 0 }]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          cutout: '62%',
          onClick: (_, els) => {
            if (!els.length) return;
            const clicked = statLabels[els[0].index];
            const sel = document.getElementById('f-statut');
            sel.value = sel.value === clicked ? 'ALL' : clicked;
            updateAll();
          },
          plugins: { legend: { position: 'bottom', labels: { color: '#cbd5e1' } } }
        }
      });

      // 3. OTD par site
      const siteOtd = allowedSites.map(s => {
        const sub = ops.filter(o => o.site === s);
        if (!sub.length) return 0;
        return +(100 * sub.filter(o => o.statut === "À l'heure").length / sub.length).toFixed(1);
      });
      upsertChart('chart-sites', {
        type: 'bar',
        data: {
          labels: allowedSites,
          datasets: [{ label: 'OTD (%)', data: siteOtd, backgroundColor: siteOtd.map(v => v >= 92 ? '#10b981' : '#f59e0b'), borderRadius: 6 }]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: '#cbd5e1' }, grid: { display: false } },
            y: { min: 70, max: 100, ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.08)' } }
          }
        }
      });

      // 4. Coûts par transporteur
      const carrierMap = {};
      ops.forEach(o => { carrierMap[o.transporteur] = (carrierMap[o.transporteur] || 0) + o.cout; });
      const carriers = Object.entries(carrierMap).sort((a, b) => b[1] - a[1]);
      upsertChart('chart-carriers', {
        type: 'bar',
        data: {
          labels: carriers.map(c => c[0]),
          datasets: [{ label: 'Coût (€)', data: carriers.map(c => Math.round(c[1])), backgroundColor: '#38bdf8', borderRadius: 6 }]
        },
        options: {
          indexAxis: 'y',
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.08)' } },
            y: { ticks: { color: '#cbd5e1' }, grid: { display: false } }
          }
        }
      });
    }

    // --- Tableau filtrable / triable / export CSV ---
    function getTableRows(search) {
      let rows = state.filteredOps;
      if (search) {
        rows = rows.filter(o =>
          o.id.toLowerCase().includes(search) ||
          o.transporteur.toLowerCase().includes(search) ||
          o.famille.toLowerCase().includes(search) ||
          o.site.toLowerCase().includes(search)
        );
      }
      const k = state.sortKey;
      const mult = state.sortAsc ? 1 : -1;
      return [...rows].sort((a, b) => (a[k] > b[k] ? mult : a[k] < b[k] ? -mult : 0));
    }

    function renderTable(search) {
      const rows = getTableRows(search);
      const totalPages = Math.max(1, Math.ceil(rows.length / state.pageSize));
      if (state.page > totalPages) state.page = totalPages;
      const start = (state.page - 1) * state.pageSize;
      const slice = rows.slice(start, start + state.pageSize);

      document.getElementById('table-count').textContent = `${rows.length.toLocaleString('fr-FR')} lignes`;
      document.getElementById('page-info').textContent = `Page ${state.page} / ${totalPages}`;

      const tbody = document.getElementById('ops-tbody');
      tbody.innerHTML = slice.map(o => {
        const cls = o.statut === "À l'heure" ? 'tag-ok' : (o.statut === 'En retard' ? 'tag-warn' : 'tag-crit');
        return `<tr>
          <td><strong>${o.id}</strong></td>
          <td>${o.date}</td>
          <td>${o.site}</td>
          <td>${o.flux}</td>
          <td>${o.abc}</td>
          <td>${o.famille}</td>
          <td>${o.transporteur}</td>
          <td>${o.palettes}</td>
          <td>${o.retard_h > 0 ? o.retard_h + ' h' : '-'}</td>
          <td>${o.cout.toLocaleString('fr-FR')} €</td>
          <td><span class="tag ${cls}">${o.statut}</span></td>
        </tr>`;
      }).join('');
    }

    // --- Scène 3D InstancedMesh ---
    function initThreeIfNeeded() {
      if (threeState.initialized || !state.stocks3d) return;
      const container = document.getElementById('viewer-card');
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.5, 1000);
      camera.position.set(42, 30, 56);

      const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setSize(container.clientWidth, container.clientHeight);
      container.appendChild(renderer.domElement);

      const controls = new THREE.OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;

      scene.add(new THREE.AmbientLight(0xffffff, 0.78));
      const dl = new THREE.DirectionalLight(0xffffff, 0.75);
      dl.position.set(50, 90, 60);
      scene.add(dl);
      scene.add(new THREE.GridHelper(220, 44, 0x334155, 0x1e293b));

      const s3d = state.stocks3d;
      const geo = new THREE.BoxGeometry(0.82, 0.72, 0.82);
      const mat = new THREE.MeshLambertMaterial();
      const mesh = new THREE.InstancedMesh(geo, mat, s3d.count);
      mesh.frustumCulled = false;

      const dummy = new THREE.Object3D();
      for (let i = 0; i < s3d.count; i++) {
        // Dispose les 4 sites en 4 quadrants lisibles
        const qx = (s3d.site_idx[i] % 2 === 0 ? -1 : 1) * 28;
        const qz = (s3d.site_idx[i] < 2 ? -1 : 1) * 48;
        const x = qx + (s3d.allee[i] - 10) * 2.1;
        const y = s3d.niveau[i] * 0.92 + 0.5;
        const z = qz + (s3d.travee[i] - 40) * 1.05;
        dummy.position.set(x, y, z);
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
      }
      mesh.instanceMatrix.needsUpdate = true;
      scene.add(mesh);

      const hl = new THREE.Mesh(
        new THREE.BoxGeometry(0.95, 0.85, 0.95),
        new THREE.MeshBasicMaterial({ color: 0x38bdf8, wireframe: true })
      );
      hl.visible = false;
      scene.add(hl);

      const raycaster = new THREE.Raycaster();
      const mouse = new THREE.Vector2();
      let down = { x: 0, y: 0 };
      renderer.domElement.addEventListener('pointerdown', e => { down = { x: e.clientX, y: e.clientY }; });
      renderer.domElement.addEventListener('pointerup', e => {
        if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return;
        const rect = renderer.domElement.getBoundingClientRect();
        mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(mouse, camera);
        const hits = raycaster.intersectObject(mesh);
        if (hits.length > 0) {
          const i = hits[0].instanceId;
          const siteName = s3d.sites[s3d.site_idx[i]];
          const taux = s3d.taux[i];
          const cls = taux > 75 ? 'A' : (taux > 40 ? 'B' : 'C');
          const m = new THREE.Matrix4();
          mesh.getMatrixAt(i, m);
          hl.position.setFromMatrixPosition(m);
          hl.visible = true;
          document.getElementById('selection-3d').textContent =
            `${siteName} | A${s3d.allee[i]+1}-T${s3d.travee[i]+1}-N${s3d.niveau[i]+1} | ${taux}% (Classe ${cls}, ${s3d.quantite[i]} u.)`;
        }
      });

      threeState = { initialized: true, scene, camera, renderer, controls, mesh, highlight: hl };

      function animate() {
        requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
      }
      animate();
    }

    function updateThreeColors() {
      if (!threeState.initialized || !state.stocks3d) return;
      const f = getFilters();
      const allowedSites = getAllowedSites();
      const targetSites = f.site === 'ALL' ? allowedSites : [f.site];
      const s3d = state.stocks3d;
      const allowedIdx = new Set(s3d.sites.map((name, i) => targetSites.includes(name) ? i : -1).filter(i => i >= 0));

      const c = new THREE.Color();
      let activeCount = 0;
      for (let i = 0; i < s3d.count; i++) {
        const taux = s3d.taux[i];
        const cls = taux > 75 ? 'A' : (taux > 40 ? 'B' : 'C');
        const visible = allowedIdx.has(s3d.site_idx[i]) && (f.abc === 'ALL' || f.abc === cls);
        if (visible) {
          c.setHex(cls === 'A' ? 0xef4444 : (cls === 'B' ? 0xf59e0b : 0x10b981));
          activeCount++;
        } else {
          c.setHex(0x162033);
        }
        threeState.mesh.setColorAt(i, c);
      }
      if (threeState.mesh.instanceColor) threeState.mesh.instanceColor.needsUpdate = true;
      document.getElementById('stats-3d').textContent = `(${activeCount.toLocaleString('fr-FR')} empl. actifs)`;
    }

    function resizeThree() {
      if (!threeState.initialized) return;
      const c = document.getElementById('viewer-card');
      threeState.camera.aspect = c.clientWidth / c.clientHeight;
      threeState.camera.updateProjectionMatrix();
      threeState.renderer.setSize(c.clientWidth, c.clientHeight);
    }

    // --- Événements UI ---
    document.querySelectorAll('.nav-btn').forEach(btn => {
      btn.addEventListener('click', () => { if (!btn.disabled) switchView(btn.dataset.view); });
    });

    document.getElementById('role-select').addEventListener('change', e => {
      state.role = e.target.value;
      applyRolePermissions();
      updateAll();
    });

    ['f-site', 'f-period', 'f-flux', 'f-abc', 'f-statut'].forEach(id => {
      document.getElementById(id).addEventListener('change', updateAll);
    });

    document.getElementById('table-search').addEventListener('input', e => {
      state.page = 1;
      renderTable(e.target.value.trim().toLowerCase());
    });

    document.querySelectorAll('th[data-sort]').forEach(th => {
      th.addEventListener('click', () => {
        const key = th.dataset.sort;
        if (state.sortKey === key) state.sortAsc = !state.sortAsc;
        else { state.sortKey = key; state.sortAsc = true; }
        renderTable(document.getElementById('table-search').value.trim().toLowerCase());
      });
    });

    document.getElementById('prev-page').addEventListener('click', () => {
      if (state.page > 1) { state.page--; renderTable(document.getElementById('table-search').value.trim().toLowerCase()); }
    });
    document.getElementById('next-page').addEventListener('click', () => {
      state.page++; renderTable(document.getElementById('table-search').value.trim().toLowerCase());
    });

    document.getElementById('btn-reset').addEventListener('click', () => {
      document.getElementById('f-site').selectedIndex = 0;
      document.getElementById('f-period').value = '30';
      document.getElementById('f-flux').value = 'ALL';
      document.getElementById('f-abc').value = 'ALL';
      document.getElementById('f-statut').value = 'ALL';
      document.getElementById('table-search').value = '';
      updateAll();
    });

    document.getElementById('btn-refresh').addEventListener('click', async () => {
      const res = await fetch('/api/cache/refresh', { method: 'POST' }).then(r => r.json());
      const dash = await fetch('/api/dashboard-data').then(r => r.json());
      state.rawOps = dash.operations;
      document.getElementById('cache-info').textContent = `Batch : ${res.refreshed_at}`;
      updateAll();
    });

    document.getElementById('btn-csv').addEventListener('click', () => {
      const rows = getTableRows(document.getElementById('table-search').value.trim().toLowerCase());
      const headers = ['id', 'date', 'site', 'flux', 'abc', 'famille', 'transporteur', 'palettes', 'retard_h', 'cout', 'statut'];
      const csv = [headers.join(';'), ...rows.map(r => headers.map(h => r[h]).join(';'))].join('\n');
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = 'operations_supply_chain.csv'; a.click();
      URL.revokeObjectURL(url);
    });

    // --- Drawer Genie ---
    const drawer = document.getElementById('genie-drawer');
    document.getElementById('btn-genie').addEventListener('click', () => drawer.classList.add('open'));
    document.getElementById('close-genie').addEventListener('click', () => drawer.classList.remove('open'));

    async function sendGenieQuestion(q) {
      const box = document.getElementById('genie-messages');
      box.innerHTML += `<div class="g-msg user"><strong>Vous :</strong> ${q}</div>`;
      box.scrollTop = box.scrollHeight;

      const res = await fetch('/api/genie/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, conversation_id: state.conversationId, allowed_sites: getAllowedSites() })
      }).then(r => r.json());

      if (res.conversation_id) state.conversationId = res.conversation_id;
      const sqlHtml = res.sql ? `<div class="sql-block">${res.sql}</div>` : '';
      box.innerHTML += `<div class="g-msg"><strong>Genie :</strong>\n${res.answer}${sqlHtml}</div>`;
      box.scrollTop = box.scrollHeight;
    }

    document.getElementById('genie-form').addEventListener('submit', async e => {
      e.preventDefault();
      const inp = document.getElementById('genie-input');
      const q = inp.value.trim();
      if (!q) return;
      inp.value = '';
      await sendGenieQuestion(q);
    });

    document.querySelectorAll('.chip').forEach(ch => {
      ch.addEventListener('click', () => sendGenieQuestion(ch.dataset.q));
    });

    window.addEventListener('resize', resizeThree);
    bootstrap();
  </script>
</body>
</html>"""
