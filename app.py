import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

app = FastAPI(title="Portail Supply Chain 3D")


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
    """Données d'emplacements entrepôt (remplaçable par une requête SQL Unity Catalog)."""
    racks = []
    for allee in range(4):
        for travee in range(6):
            for niveau in range(3):
                taux = ((allee * 17 + travee * 29 + niveau * 43) % 100) / 100.0
                racks.append(
                    {
                        "id": f"A{allee+1}-T{travee+1}-N{niveau+1}",
                        "allee": allee,
                        "travee": travee,
                        "niveau": niveau,
                        "taux_remplissage": round(taux, 2),
                        "classe_abc": "A" if taux > 0.75 else ("B" if taux > 0.4 else "C"),
                        "quantite": int(taux * 120),
                    }
                )
    return {"racks": racks}


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
  <title>Databricks App - Portail Stocks 3D</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
  <style>
    body { margin: 0; font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; display: flex; flex-direction: column; height: 100vh; }
    header { padding: 12px 20px; background: #1e293b; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; align-items: center; }
    .badge { background: #0284c7; padding: 4px 10px; border-radius: 999px; font-size: 12px; }
    main { display: flex; flex: 1; overflow: hidden; }
    #viewer { flex: 2; position: relative; }
    #hud { position: absolute; top: 14px; left: 14px; background: rgba(15,23,42,0.85); padding: 12px; border-radius: 8px; border: 1px solid #334155; font-size: 13px; }
    #sidebar { flex: 1; max-width: 380px; background: #1e293b; border-left: 1px solid #334155; display: flex; flex-direction: column; padding: 16px; }
    #messages { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; margin-bottom: 12px; }
    .msg { background: #0f172a; padding: 10px; border-radius: 8px; font-size: 13px; line-height: 1.4; }
    form { display: flex; gap: 8px; }
    input { flex: 1; padding: 8px 10px; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: white; }
    button { padding: 8px 14px; border-radius: 6px; border: none; background: #ea580c; color: white; font-weight: 600; cursor: pointer; }
  </style>
</head>
<body>
  <header>
    <strong>Portail Supply Chain &amp; Entrepôt 3D</strong>
    <span id="user-badge" class="badge">Chargement SSO...</span>
  </header>
  <main>
    <div id="viewer">
      <div id="hud">
        <div><strong>Vue 3D Entrepôt</strong> (clique sur un rack)</div>
        <div id="selection" style="margin-top:6px;color:#38bdf8;">Aucun emplacement sélectionné</div>
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
    const camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.1, 1000);
    camera.position.set(10, 8, 12);
    camera.lookAt(3, 1.5, 3);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);

    scene.add(new THREE.AmbientLight(0xffffff, 0.7));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(10, 20, 10);
    scene.add(dirLight);
    scene.add(new THREE.GridHelper(16, 16, 0x334155, 0x1e293b));

    const meshes = [];
    fetch('/api/stocks-3d').then(r => r.json()).then(data => {
      const geo = new THREE.BoxGeometry(0.85, 0.75, 0.85);
      data.racks.forEach(rack => {
        const color = rack.taux_remplissage > 0.75 ? 0xef4444 : (rack.taux_remplissage > 0.4 ? 0xf59e0b : 0x10b981);
        const mat = new THREE.MeshLambertMaterial({ color });
        const cube = new THREE.Mesh(geo, mat);
        cube.position.set(rack.allee * 2.2 - 3, rack.niveau * 0.95 + 0.5, rack.travee * 1.1 - 2.5);
        cube.userData = rack;
        scene.add(cube);
        meshes.push(cube);
      });
    });

    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();
    renderer.domElement.addEventListener('click', (e) => {
      const rect = renderer.domElement.getBoundingClientRect();
      mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(mouse, camera);
      const hits = raycaster.intersectObjects(meshes);
      if (hits.length > 0) {
        const d = hits[0].object.userData;
        document.getElementById('selection').textContent =
          `Emplacement ${d.id} | Remplissage : ${Math.round(d.taux_remplissage * 100)}% | Classe ${d.classe_abc} (${d.quantite} u.)`;
      }
    });

    function animate() {
      requestAnimationFrame(animate);
      scene.rotation.y += 0.0015;
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
