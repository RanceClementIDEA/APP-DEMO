import asyncio
import json
import os
import re
import time
import urllib.request
from typing import Any, Optional

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import Disposition, Format, StatementState
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

app = FastAPI(title="Portail Supply Chain 3D - Haute Capacité")

_STOCKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CACHE_TTL_SECONDS = 3600
_TABLE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$")


def _generate_demo_stocks() -> dict[str, Any]:
    """Génère 100 000 emplacements simulés (50 allées x 250 travées x 8 niveaux)."""
    nb_allees = 50
    nb_travees = 250
    nb_niveaux = 8

    allees: list[int] = []
    travees: list[int] = []
    niveaux: list[int] = []
    taux_list: list[int] = []
    quantites: list[int] = []

    for a in range(nb_allees):
        for t in range(nb_travees):
            for n in range(nb_niveaux):
                taux = (a * 17 + t * 29 + n * 43) % 101
                allees.append(a)
                travees.append(t)
                niveaux.append(n)
                taux_list.append(taux)
                quantites.append((taux * 120) // 100)

    return {
        "source": "demo",
        "count": len(allees),
        "allee": allees,
        "travee": travees,
        "niveau": niveaux,
        "taux": taux_list,
        "quantite": quantites,
    }


def _fetch_stocks_from_uc(user_token: str, warehouse_id: str, table_name: str) -> dict[str, Any]:
    """Interroge Unity Catalog au nom de l'utilisateur connecté (On-Behalf-Of)."""
    if not _TABLE_NAME_RE.match(table_name):
        raise ValueError(f"Nom de table Unity Catalog invalide : {table_name}")

    w = WorkspaceClient(
        host=os.environ["DATABRICKS_HOST"],
        token=user_token,
        auth_type="pat",
    )

    sql = f"""
        SELECT
            CAST(allee AS INT) AS allee,
            CAST(travee AS INT) AS travee,
            CAST(niveau AS INT) AS niveau,
            CAST(ROUND(taux_remplissage) AS INT) AS taux,
            CAST(quantite AS INT) AS quantite
        FROM {table_name}
        LIMIT 100000
    """

    stmt = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=sql,
        wait_timeout="50s",
        disposition=Disposition.INLINE,
        format=Format.JSON_ARRAY,
    )

    while stmt.status and stmt.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(1.0)
        stmt = w.statement_execution.get_statement(stmt.statement_id)

    if not stmt.status or stmt.status.state != StatementState.SUCCEEDED:
        err = stmt.status.error.message if (stmt.status and stmt.status.error) else "Erreur inconnue"
        raise RuntimeError(f"Échec requête SQL Unity Catalog : {err}")

    rows: list[list[Any]] = []
    if stmt.result and stmt.result.data_array:
        rows.extend(stmt.result.data_array)

    next_chunk = stmt.result.next_chunk_index if stmt.result else None
    while next_chunk is not None:
        chunk = w.statement_execution.get_statement_result_chunk_n(
            statement_id=stmt.statement_id,
            chunk_index=next_chunk,
        )
        if chunk.data_array:
            rows.extend(chunk.data_array)
        next_chunk = chunk.next_chunk_index

    return {
        "source": "unity_catalog",
        "count": len(rows),
        "allee": [int(r[0] or 0) for r in rows],
        "travee": [int(r[1] or 0) for r in rows],
        "niveau": [int(r[2] or 0) for r in rows],
        "taux": [int(r[3] or 0) for r in rows],
        "quantite": [int(r[4] or 0) for r in rows],
    }


@app.get("/api/me")
def get_current_user(request: Request):
    """Récupère l'identité SSO injectée par Databricks Apps."""
    email = request.headers.get("x-forwarded-email", "utilisateur.demo@entreprise.fr")
    has_obo_token = bool(request.headers.get("x-forwarded-access-token"))
    return {
        "email": email,
        "obo_active": has_obo_token,
        "warehouse_id": os.getenv("DATABRICKS_WAREHOUSE_ID", "non_configure"),
        "table_name": os.getenv("STOCKS_TABLE_NAME", "mode_demo"),
    }


@app.get("/api/stocks-3d")
async def get_stocks_3d(request: Request, force_refresh: bool = False):
    """Lit Unity Catalog si configuré, sinon bascule automatiquement sur les 100 000 emplacements de démo."""
    user_token = request.headers.get("x-forwarded-access-token")
    user_email = request.headers.get("x-forwarded-email", "anonymous")
    warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID")
    table_name = os.getenv("STOCKS_TABLE_NAME")

    if not table_name or not warehouse_id or not user_token:
        return await asyncio.to_thread(_generate_demo_stocks)

    now = time.time()
    cached = _STOCKS_CACHE.get(user_email)
    if not force_refresh and cached and (now - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    try:
        payload = await asyncio.to_thread(
            _fetch_stocks_from_uc, user_token, warehouse_id, table_name
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _STOCKS_CACHE[user_email] = (now, payload)
    return payload


@app.post("/api/cache/refresh")
def invalidate_stocks_cache():
    """Vide le cache mémoire à la fin du Databricks Workflow batch."""
    _STOCKS_CACHE.clear()
    return {"status": "ok", "message": "Cache vidé."}


def _run_genie_query(
    token: str, space_id: str, question: str, conversation_id: Optional[str]
) -> dict[str, Any]:
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
        msg = w.genie.start_conversation_and_wait(
            space_id=space_id, content=question
        )

    answer_parts: list[str] = []
    for att in msg.attachments or []:
        if att.text and att.text.content:
            answer_parts.append(att.text.content)

        if att.query:
            if att.query.description and not answer_parts:
                answer_parts.append(att.query.description)

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
                    apercu = "\n".join(str(dict(zip(cols, r))) for r in rows[:5])
                    answer_parts.append(f"Aperçu ({len(rows)} lignes) :\n{apercu}")

    return {
        "conversation_id": msg.conversation_id,
        "answer": "\n\n".join(answer_parts) or "Requête exécutée sans résultat textuel.",
    }


@app.post("/api/genie/ask")
async def ask_genie(payload: dict, request: Request):
    question = payload.get("question", "").strip()
    conversation_id = payload.get("conversation_id")
    user = request.headers.get("x-forwarded-email", "Utilisateur")
    space_id = os.getenv("GENIE_SPACE_ID")
    user_token = request.headers.get("x-forwarded-access-token")

    if not space_id:
        return {
            "answer": (
                f"[{user}] Mode démo actif : ajoute GENIE_SPACE_ID dans app.yaml "
                f"pour exécuter « {question} » sur tes tables Unity Catalog."
            )
        }
    if not user_token:
        return {"answer": "Jeton OBO manquant : active User Authorization dans les paramètres de l'app."}

    return await asyncio.to_thread(
        _run_genie_query, user_token, space_id, question, conversation_id
    )


@app.get("/", response_class=HTMLResponse)
def index():
    return """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <title>Databricks App - Portail Stocks 3D (100 000 emplacements)</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
  <style>
    body { margin: 0; font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; display: flex; flex-direction: column; height: 100vh; }
    header { padding: 12px 20px; background: #1e293b; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; align-items: center; }
    .badge { background: #0284c7; padding: 4px 10px; border-radius: 999px; font-size: 12px; }
    main { display: flex; flex: 1; overflow: hidden; }
    #viewer { flex: 2; position: relative; }
    #hud { position: absolute; top: 14px; left: 14px; background: rgba(15,23,42,0.88); padding: 12px 14px; border-radius: 8px; border: 1px solid #334155; font-size: 13px; max-width: 380px; }
    .filters { display: flex; gap: 6px; margin-top: 10px; }
    .filter-btn { background: #334155; color: #f8fafc; border: 1px solid #475569; padding: 4px 9px; border-radius: 5px; font-size: 12px; cursor: pointer; }
    .filter-btn.active { background: #0284c7; border-color: #38bdf8; }
    #sidebar { flex: 1; max-width: 380px; background: #1e293b; border-left: 1px solid #334155; display: flex; flex-direction: column; padding: 16px; }
    #messages { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; margin-bottom: 12px; }
    .msg { background: #0f172a; padding: 10px; border-radius: 8px; font-size: 13px; line-height: 1.4; white-space: pre-wrap; }
    form { display: flex; gap: 8px; }
    input { flex: 1; padding: 8px 10px; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: white; }
    button[type="submit"] { padding: 8px 14px; border-radius: 6px; border: none; background: #ea580c; color: white; font-weight: 600; cursor: pointer; }
  </style>
</head>
<body>
  <header>
    <strong>Portail Supply Chain &amp; Entrepôt 3D (100 000 emplacements)</strong>
    <span id="user-badge" class="badge">Chargement SSO...</span>
  </header>
  <main>
    <div id="viewer">
      <div id="hud">
        <div><strong>Vue 3D Entrepôt</strong> <span id="stats" style="color:#94a3b8;">(chargement...)</span></div>
        <div style="font-size:11px;color:#94a3b8;margin-top:2px;">Clic gauche : pivoter | Molette : zoomer | Clic droit : déplacer</div>
        <div id="selection" style="margin-top:8px;color:#38bdf8;">Clique sur un emplacement pour inspecter</div>
        <div class="filters">
          <button class="filter-btn active" data-filter="ALL">Tous</button>
          <button class="filter-btn" data-filter="A">Classe A (&gt;75%)</button>
          <button class="filter-btn" data-filter="B">Classe B (40-75%)</button>
          <button class="filter-btn" data-filter="C">Classe C (&lt;40%)</button>
        </div>
      </div>
    </div>
    <aside id="sidebar">
      <h3 style="margin-top:0;">Assistant IA Genie</h3>
      <div id="messages">
        <div class="msg">Pose une question sur tes stocks (ex. <em>Quels racks sont saturés à plus de 80 % ?</em>).</div>
      </div>
      <form id="chat-form">
        <input id="question" placeholder="Demander à Genie..." required />
        <button type="submit">Envoyer</button>
      </form>
    </aside>
  </main>

  <script>
    fetch('/api/me').then(r => r.json()).then(u => {
      document.getElementById('user-badge').textContent = `${u.email} | OBO: ${u.obo_active ? 'Actif' : 'Démo'}`;
    });

    const container = document.getElementById('viewer');
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.5, 1000);
    camera.position.set(55, 38, 75);

    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);

    const controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.set(0, 3, 0);

    scene.add(new THREE.AmbientLight(0xffffff, 0.75));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.75);
    dirLight.position.set(60, 120, 80);
    scene.add(dirLight);
    scene.add(new THREE.GridHelper(300, 60, 0x334155, 0x1e293b));

    const highlightGeo = new THREE.BoxGeometry(0.96, 0.86, 0.96);
    const highlightMat = new THREE.MeshBasicMaterial({ color: 0x38bdf8, wireframe: true });
    const highlightMesh = new THREE.Mesh(highlightGeo, highlightMat);
    highlightMesh.visible = false;
    scene.add(highlightMesh);

    let stockData = null;
    let instancedMesh = null;
    let centerAllee = 25;
    let centerTravee = 125;
    const dummy = new THREE.Object3D();
    const color = new THREE.Color();

    const COLOR_A = 0xef4444;
    const COLOR_B = 0xf59e0b;
    const COLOR_C = 0x10b981;
    const COLOR_DIM = 0x1e293b;

    function getClasseABC(taux) {
      if (taux > 75) return 'A';
      if (taux > 40) return 'B';
      return 'C';
    }

    function getPosition(i, outVec) {
      const x = (stockData.allee[i] - centerAllee) * 2.2;
      const y = stockData.niveau[i] * 0.95 + 0.5;
      const z = (stockData.travee[i] - centerTravee) * 1.1;
      outVec.set(x, y, z);
    }

    fetch('/api/stocks-3d').then(r => r.json()).then(data => {
      stockData = data;
      const count = data.count;
      centerAllee = data.allee.reduce((m, v) => (v > m ? v : m), 0) / 2;
      centerTravee = data.travee.reduce((m, v) => (v > m ? v : m), 0) / 2;

      const geo = new THREE.BoxGeometry(0.85, 0.75, 0.85);
      const mat = new THREE.MeshLambertMaterial();

      instancedMesh = new THREE.InstancedMesh(geo, mat, count);
      instancedMesh.frustumCulled = false;

      for (let i = 0; i < count; i++) {
        getPosition(i, dummy.position);
        dummy.updateMatrix();
        instancedMesh.setMatrixAt(i, dummy.matrix);

        const taux = data.taux[i];
        color.setHex(taux > 75 ? COLOR_A : (taux > 40 ? COLOR_B : COLOR_C));
        instancedMesh.setColorAt(i, color);
      }

      instancedMesh.instanceMatrix.needsUpdate = true;
      if (instancedMesh.instanceColor) instancedMesh.instanceColor.needsUpdate = true;
      scene.add(instancedMesh);

      document.getElementById('stats').textContent = `(${count.toLocaleString('fr-FR')} emplacements - ${data.source})`;
    });

    document.querySelectorAll('.filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        if (!instancedMesh || !stockData) return;
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const filter = btn.dataset.filter;

        for (let i = 0; i < stockData.count; i++) {
          const taux = stockData.taux[i];
          const classe = getClasseABC(taux);
          if (filter === 'ALL' || classe === filter) {
            color.setHex(classe === 'A' ? COLOR_A : (classe === 'B' ? COLOR_B : COLOR_C));
          } else {
            color.setHex(COLOR_DIM);
          }
          instancedMesh.setColorAt(i, color);
        }
        if (instancedMesh.instanceColor) instancedMesh.instanceColor.needsUpdate = true;
      });
    });

    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();
    let downPos = { x: 0, y: 0 };

    renderer.domElement.addEventListener('pointerdown', (e) => {
      downPos = { x: e.clientX, y: e.clientY };
    });

    renderer.domElement.addEventListener('pointerup', (e) => {
      const dist = Math.hypot(e.clientX - downPos.x, e.clientY - downPos.y);
      if (dist > 5 || !instancedMesh || !stockData) return;

      const rect = renderer.domElement.getBoundingClientRect();
      mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(mouse, camera);

      const hits = raycaster.intersectObject(instancedMesh);
      if (hits.length > 0) {
        const idx = hits[0].instanceId;
        const a = stockData.allee[idx] + 1;
        const t = stockData.travee[idx] + 1;
        const n = stockData.niveau[idx] + 1;
        const taux = stockData.taux[idx];
        const qte = stockData.quantite[idx];
        const classe = getClasseABC(taux);

        getPosition(idx, highlightMesh.position);
        highlightMesh.visible = true;

        document.getElementById('selection').textContent =
          `Emplacement A${a}-T${t}-N${n} | Remplissage : ${taux}% | Classe ${classe} (${qte} u.)`;
      }
    });

    window.addEventListener('resize', () => {
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    });

    function animate() {
      requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    }
    animate();

    let conversationId = null;
    document.getElementById('chat-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const input = document.getElementById('question');
      const q = input.value;
      input.value = '';
      const box = document.getElementById('messages');
      box.innerHTML += `<div class="msg"><strong>Vous :</strong> ${q}</div>`;
      const res = await fetch('/api/genie/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, conversation_id: conversationId })
      });
      const data = await res.json();
      if (data.conversation_id) conversationId = data.conversation_id;
      box.innerHTML += `<div class="msg"><strong>Genie :</strong> ${data.answer}</div>`;
      box.scrollTop = box.scrollHeight;
    });
  </script>
</body>
</html>"""
