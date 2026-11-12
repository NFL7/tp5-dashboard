# TP5 - Dashboard Consommation électrique (déployé sur Render)

Structure :
    csv/               <- les 10 CSV des consommateurs (versionnés sur GitHub)
    init_db.py         <- (re)construit energie.db à partir des CSV
    db.py              <- accès aux données (connexion ouverte/fermée à chaque requête)
    app.py             <- dashboard Dash + Plotly + Folium ; régénère la base au démarrage ; expose server = app.server
    requirements.txt   <- librairies (dont gunicorn)
    gunicorn.conf.py   <- délai de démarrage allongé pour Render (lu automatiquement)
    .gitignore         <- exclut energie.db (jamais versionné)

Lancer en local :
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    python app.py        # http://127.0.0.1:8050

Render :
    Build Command : pip install -r requirements.txt
    Start Command : gunicorn app:server
