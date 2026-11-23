import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

app = FastAPI(title="Portail Supply Chain 3D - Haute Capacité")


@app.get("/api/me")
def get_current_user(request: Request):
    """Récupère l'identité SSO injectée par Databricks Apps."""
    email = request.headers.get("x-forwarded-email", "utilisateur.demo@entreprise.fr")
    has_obo_token = bool(request.headers.get("x-forwarded-access-token"))
    return {
        "email": email,
        "obo_active": has_obo_token,
        "warehouse_id": os.getenv("DATABRICKS_WAREHOUSE_ID", "non_configure"),
    }


@app.get("/api/stocks-3d")
def get_stocks_3d():
    """Génère 100 000 emplacements (50 allées x 250 travées x 8 niveaux) en tableaux plats."""
    nb_allees = 50
    nb_travees = 250
    nb_niveaux = 8

    allees = []
    travees = []
    niveaux = []
    taux_list = []
    quantites = []

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
        "count": len(allees),
        "allee": allees,
        "travee": travees,
        "niveau": niveaux,
        "taux": taux_list,
        "quantite": quantites,
    }


@app.post("/api/genie/ask")
async def ask_genie_demo(payload: dict, request: Request):
    """Point d'entrée prêt pour brancher w.genie.start_conversation_and_wait()."""
    question = payload.get("question", "")
    user = request.headers.get("x-forwarded-email", "Utilisateur")
    space_id = os.getenv("GENIE_SPACE_ID")
    if not space_id:
        return {
            "answer": (
                f"[{user}] Mode démo actif : configure la variable GENIE_SPACE_ID "
                f"dans app.yaml pour exécuter « {question} » sur tes tables Unity Catalog."
            )
        }
    return {"answer": f"Requête transmise à l'espace Genie {space_id} : {question}"}


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
    #hud { position: absolute; top: 14px; left: 14px; background: rgba(15,23,42,0.88); padding: 12px 14px; border-radius: 8px; border: 1px solid #334155; font-size: 13px; max-width: 360px; }
    .filters { display: flex; gap: 6px; margin-top: 10px; }
    .filter-btn { background: #334155; color: #f8fafc; border: 1px solid #475569; padding: 4px 9px; border-radius: 5px; font-size: 12px; cursor: pointer; }
    .filter-btn.active { background: #0284c7; border-color: #38bdf8; }
    #sidebar { flex: 1; max-width: 380px; background: #1e293b; border-left: 1px solid #334155; display: flex; flex-direction: column; padding: 16px; }
    #messages { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; margin-bottom: 12px; }
    .msg { background: #0f172a; padding: 10px; border-radius: 8px; font-size: 13px; line-height: 1.4; }
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

    // Curseur visuel autour de l'emplacement sélectionné
    const highlightGeo = new THREE.BoxGeometry(0.96, 0.86, 0.96);
    const highlightMat = new THREE.MeshBasicMaterial({ color: 0x38bdf8, wireframe: true });
    const highlightMesh = new THREE.Mesh(highlightGeo, highlightMat);
    highlightMesh.visible = false;
    scene.add(highlightMesh);

    let stockData = null;
    let instancedMesh = null;
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
      const x = (stockData.allee[i] - 25) * 2.2;
      const y = stockData.niveau[i] * 0.95 + 0.5;
      const z = (stockData.travee[i] - 125) * 1.1;
      outVec.set(x, y, z);
    }

    fetch('/api/stocks-3d').then(r => r.json()).then(data => {
      stockData = data;
      const count = data.count;
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

      document.getElementById('stats').textContent = `(${count.toLocaleString('fr-FR')} emplacements actifs)`;
    });

    // Filtrage instantané par classe ABC (coloration sélective sans recréer la géométrie)
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

    // Détection de clic (distingue un vrai clic d'une rotation de caméra)
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
        body: JSON.stringify({ question: q })
      });
      const data = await res.json();
      box.innerHTML += `<div class="msg"><strong>Genie :</strong> ${data.answer}</div>`;
      box.scrollTop = box.scrollHeight;
    });
  </script>
</body>
</html>"""
