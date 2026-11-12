"""
init_db.py - (Re)construit energie.db a partir des CSV du projet (dossier ./csv).
Appele automatiquement au demarrage de app.py (necessaire sur Render : disque non persistant).
Usage : python3 init_db.py            (lit ./csv, avec verification complete)
        python3 init_db.py <dossier>
"""
from __future__ import annotations

import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 and __name__ == "__main__" else BASE_DIR / "csv"
DB_PATH = BASE_DIR / "energie.db"

HP_DEBUT, HP_FIN = "06:00", "22:00"   # HP 06h-22h, HC 22h-06h

# Tarifs lus sur Tarifs.png (centimes d'euro / kWh) : (heures pleines, heures creuses)
TARIFS_CENT = {
    "Agriculteur 1": (9.52, 5.28), "Agriculteur 2": (13.03, 9.62),
    "Agriculteur 3": (15.31, 10.08), "Agriculteur 4": (14.17, 10.07),
    "Industriel 1": (23.12, 17.15), "Industriel 2": (33.54, 17.15),
    "Industriel 3": (16.27, 11.08), "Industriel 4": (12.38, 9.25),
    "Site public 1": (14.21, 11.32), "Site public 2": (13.98, 7.67),
}
# Positions FICTIVES (le jeu de donnees ne fournit aucune coordonnee)
COORDS = {
    "Agriculteur 1": (48.5600, 3.3000), "Agriculteur 2": (48.8200, 3.0900),
    "Agriculteur 3": (48.2700, 2.7000), "Agriculteur 4": (48.4300, 2.1600),
    "Industriel 1": (48.5400, 2.6600), "Industriel 2": (48.6300, 2.4300),
    "Industriel 3": (48.9300, 2.2900), "Industriel 4": (48.6100, 2.4800),
    "Site public 1": (48.8566, 2.3522), "Site public 2": (48.8049, 2.1204),
}

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE consommateurs (
    id_consommateur TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    latitude        REAL,
    longitude       REAL,
    id_contrat      TEXT NOT NULL
);

CREATE TABLE releves (
    id_releve       INTEGER PRIMARY KEY,
    id_consommateur TEXT NOT NULL,
    horodatage      TEXT NOT NULL,
    puissance_kw    REAL NOT NULL,
    FOREIGN KEY (id_consommateur) REFERENCES consommateurs (id_consommateur)
);

CREATE TABLE tarifs (
    id_tarif    INTEGER PRIMARY KEY,
    id_contrat  TEXT NOT NULL,
    plage       TEXT NOT NULL CHECK (plage IN ('HP', 'HC')),
    heure_debut TEXT NOT NULL,
    heure_fin   TEXT NOT NULL,
    prix_kwh    REAL NOT NULL
);

CREATE INDEX idx_releves_conso ON releves (id_consommateur);
CREATE INDEX idx_releves_horo  ON releves (horodatage);
CREATE INDEX idx_tarifs_contrat ON tarifs (id_contrat);
"""

# Nom de fichier CSV -> consommateur (ex. Agri1_2024__newSheet.csv, Agri_2_2025__newSheet.csv, SP_1_2022__newSheet.csv)
MOTIF_FICHIER = re.compile(r"^(Agri|Industriel|SP)_?(\d)_\d{4}", re.IGNORECASE)
TYPES_FICHIER = {"agri": "Agriculteur", "industriel": "Industriel", "sp": "Site public"}


def identifier(fichier: Path):
    """Type et nom du consommateur, d'apres le nom du fichier CSV."""
    m = MOTIF_FICHIER.match(unicodedata.normalize("NFC", fichier.stem))
    if not m:
        raise ValueError(f"Consommateur non identifie : {fichier}")
    type_ = TYPES_FICHIER[m.group(1).lower()]
    return type_, f"{type_} {m.group(2)}"


def lire(fichier: Path) -> pd.DataFrame:
    d = pd.read_csv(fichier, encoding="utf-8-sig", usecols=[0, 1], names=["horodatage", "puissance_w"], header=0,
                    dtype={"horodatage": str})
    fmt = "%d/%m/%Y %H:%M:%S" if d["horodatage"].iloc[0].count("/") == 2 else "%Y-%m-%d %H:%M:%S"
    d["horodatage"] = pd.to_datetime(d["horodatage"], format=fmt, errors="coerce")
    d["puissance_kw"] = pd.to_numeric(d["puissance_w"], errors="coerce") / 1000.0   # W -> kW
    return d.dropna(subset=["horodatage", "puissance_kw"])[["horodatage", "puissance_kw"]]


def construire_tables():
    fichiers = [f for f in sorted(DATA_DIR.glob("*.csv")) if not f.name.startswith("._")]
    if not fichiers:
        raise SystemExit(f"[ERREUR] Aucun CSV trouve dans {DATA_DIR}")
    print(f"{len(fichiers)} fichiers CSV dans {DATA_DIR}")

    consos, releves, tarifs = [], [], []
    for f in fichiers:
        type_, nom = identifier(f)
        assert nom in TARIFS_CENT and nom in COORDS, f"{nom} absent de TARIFS_CENT/COORDS"
        contrat = "CT_" + nom.replace(" ", "_")
        lat, lon = COORDS[nom]
        consos.append({"id_consommateur": nom, "type": type_, "latitude": lat,
                       "longitude": lon, "id_contrat": contrat})
        hp, hc = TARIFS_CENT[nom]
        tarifs.append({"id_contrat": contrat, "plage": "HP", "heure_debut": HP_DEBUT,
                       "heure_fin": HP_FIN, "prix_kwh": hp / 100})
        tarifs.append({"id_contrat": contrat, "plage": "HC", "heure_debut": HP_FIN,
                       "heure_fin": HP_DEBUT, "prix_kwh": hc / 100})
        d = lire(f)
        d.insert(0, "id_consommateur", nom)
        releves.append(d)
        print(f"  lu : {nom:14s} {len(d):>7} releves")

    c = pd.DataFrame(consos)
    t = pd.DataFrame(tarifs)
    t.insert(0, "id_tarif", range(1, len(t) + 1))
    r = pd.concat(releves, ignore_index=True).sort_values(
        ["id_consommateur", "horodatage"]).reset_index(drop=True)
    r["id_releve"] = range(1, len(r) + 1)
    r["horodatage"] = r["horodatage"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return c, r[["id_releve", "id_consommateur", "horodatage", "puissance_kw"]], t


def create_and_load(c, r, t) -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA foreign_keys = ON;")
        c.to_sql("consommateurs", conn, if_exists="append", index=False)
        t.to_sql("tarifs", conn, if_exists="append", index=False)
        r.to_sql("releves", conn, if_exists="append", index=False, chunksize=50_000)
        conn.commit()
    finally:
        conn.close()


def verify(c, r) -> bool:
    ok = True
    conn = sqlite3.connect(DB_PATH)
    try:
        for table, df in (("consommateurs", c), ("releves", r)):
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            ok &= n == len(df)
            print(f"  [{'OK ' if n == len(df) else 'KO '}] {table}: {n} lignes en base / {len(df)} source")

        sql = pd.read_sql_query(
            """SELECT c.type, AVG(r.puissance_kw) AS moyenne_kw
               FROM releves r JOIN consommateurs c USING (id_consommateur)
               GROUP BY c.type ORDER BY c.type""", conn)
        ref = (r.merge(c[["id_consommateur", "type"]], on="id_consommateur")
                 .groupby("type", as_index=False)["puissance_kw"].mean()
                 .rename(columns={"puissance_kw": "moyenne_kw"}).sort_values("type"))
        same = (sql["type"].tolist() == ref["type"].tolist()
                and ((sql["moyenne_kw"] - ref["moyenne_kw"]).abs() < 1e-9).all())
        ok &= bool(same)
        print(f"  [{'OK ' if same else 'KO '}] consommation moyenne par type (SQL vs pandas)")
        print(sql.round(3).to_string(index=False))

        sql2 = pd.read_sql_query(
            "SELECT type, COUNT(*) AS n FROM consommateurs GROUP BY type ORDER BY type", conn)
        ref2 = c.groupby("type").size().reset_index(name="n").sort_values("type")
        same2 = sql2.values.tolist() == ref2.values.tolist()
        ok &= bool(same2)
        print(f"  [{'OK ' if same2 else 'KO '}] nombre de consommateurs par type (SQL vs pandas)")
    finally:
        conn.close()
    return ok


def main(verifier: bool = True) -> None:
    """verifier=False au demarrage de l'app (rapide) ; verifier=True quand on lance python init_db.py."""
    c, r, t = construire_tables()
    create_and_load(c, r, t)
    print(f"\nBase creee : {DB_PATH}\n  consommateurs={len(c)}  releves={len(r)}  tarifs={len(t)}\n")
    if not verifier:
        return
    print("Verification du chargement :")
    ok = verify(c, r)
    import db
    tarif = db.verifier_tarification()
    print(f"  [{'OK ' if tarif['coherent'] else 'KO '}] chaque releve a exactement un tarif : {tarif}")
    if not (ok and tarif["coherent"]):
        sys.exit("\n[ERREUR] La verification a echoue.")
    print("\nBase prete. Lancez le dashboard avec : python3 app.py")


if __name__ == "__main__":
    main()