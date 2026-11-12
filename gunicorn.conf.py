# Lu automatiquement par « gunicorn app:server » (même dossier).
# Le démarrage régénère la base (1 M de relevés) puis calcule les agrégats :
# sur le plan gratuit Render (CPU partagé) cela peut dépasser les 30 s par défaut de gunicorn.
timeout = 600
workers = 1
threads = 2
preload_app = False
