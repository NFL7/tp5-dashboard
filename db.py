"""
db.py - Module d'acces aux donnees (SQLite).

Toutes les requetes du dashboard passent par ce module. Une connexion est
ouverte puis fermee a CHAQUE requete (aucune connexion permanente).
La base est (re)construite par : python init_db.py
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Sequence

import pandas as pd

DB_PATH = Path(__file__).resolve().parent / "energie.db"

# Tarif applicable a un releve : meme contrat que le consommateur ET heure du
# releve comprise dans [heure_debut, heure_fin[. Si heure_debut > heure_fin,
# la plage passe minuit (ex. HC 22:00 -> 06:00).
_JOIN_TARIF = """
    JOIN tarifs t
      ON t.id_contrat = c.id_contrat
     AND (
           (t.heure_debut <= t.heure_fin
            AND strftime('%H:%M', r.horodatage) >= t.heure_debut
            AND strftime('%H:%M', r.horodatage) <  t.heure_fin)
        OR (t.heure_debut >  t.heure_fin
            AND (strftime('%H:%M', r.horodatage) >= t.heure_debut
              OR strftime('%H:%M', r.horodatage) <  t.heure_fin))
         )
"""


@contextmanager
def _connexion():
    """Ouvre une connexion et la ferme systematiquement a la sortie du bloc."""
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Base introuvable ({DB_PATH.name}). Lancez d'abord : python init_db.py")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
    finally:
        conn.close()


def _requete(sql: str, params: Sequence = ()) -> pd.DataFrame:
    with _connexion() as conn:
        return pd.read_sql_query(sql, conn, params=list(params))


def _filtre_types(types: Optional[Sequence[str]], alias: str = "c"):
    """Fragment SQL 'AND alias.type IN (...)' + parametres. None = pas de filtre."""
    if types is None:
        return "", []
    types = list(types)
    if not types:
        return " AND 1 = 0", []
    marques = ",".join("?" * len(types))
    return f" AND {alias}.type IN ({marques})", types


# Pas de temps entre deux releves (heures), deduit des donnees une seule fois.
_PAS_H: Optional[float] = None


def get_pas_heures() -> float:
    global _PAS_H
    if _PAS_H is None:
        df = _requete(
            """SELECT horodatage FROM releves
               WHERE id_consommateur = (SELECT MIN(id_consommateur) FROM releves)
               ORDER BY horodatage LIMIT 500""")
        pas = pd.to_datetime(df["horodatage"]).diff().dropna().dt.total_seconds().median()
        _PAS_H = float(pas) / 3600 if pas and pas > 0 else 1.0
    return _PAS_H


# --------------------------------------------------------------------------
# Fonctions de requetage
# --------------------------------------------------------------------------
def get_types() -> List[str]:
    df = _requete("SELECT DISTINCT type FROM consommateurs ORDER BY type")
    return df["type"].tolist()


def get_consommateurs(types: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """Un consommateur par ligne : id, type, coordonnees, contrat."""
    filtre, params = _filtre_types(types)
    return _requete(
        f"""SELECT c.id_consommateur, c.type, c.latitude, c.longitude, c.id_contrat
            FROM consommateurs c WHERE 1 = 1 {filtre}
            ORDER BY c.type, c.id_consommateur""", params)


def get_consommation_moyenne_par_type(types: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """Puissance moyenne (kW) des releves par type de consommateur."""
    filtre, params = _filtre_types(types)
    return _requete(
        f"""SELECT c.type, AVG(r.puissance_kw) AS puissance_moyenne_kw
            FROM releves r JOIN consommateurs c USING (id_consommateur)
            WHERE 1 = 1 {filtre}
            GROUP BY c.type ORDER BY c.type""", params)


def get_profil_horaire(types: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """Puissance moyenne (kW) par heure de la journee et par type."""
    filtre, params = _filtre_types(types)
    return _requete(
        f"""SELECT c.type,
                   CAST(strftime('%H', r.horodatage) AS INTEGER) AS heure,
                   AVG(r.puissance_kw) AS puissance_moyenne_kw
            FROM releves r JOIN consommateurs c USING (id_consommateur)
            WHERE 1 = 1 {filtre}
            GROUP BY c.type, heure ORDER BY c.type, heure""", params)


def get_stats_consommateurs(types: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """
    Par consommateur : energie consommee (kWh) et facture (EUR) sur la periode,
    avec le tarif du contrat du consommateur applique a l'heure de chaque releve.
    """
    filtre, params = _filtre_types(types)
    df = _requete(
        f"""SELECT c.id_consommateur, c.type, c.latitude, c.longitude, c.id_contrat,
                   SUM(r.puissance_kw)              AS somme_kw,
                   SUM(r.puissance_kw * t.prix_kwh) AS somme_kw_prix,
                   AVG(r.puissance_kw)              AS puissance_moyenne_kw,
                   MAX(r.puissance_kw)              AS puissance_max_kw
            FROM releves r
            JOIN consommateurs c USING (id_consommateur)
            {_JOIN_TARIF}
            WHERE 1 = 1 {filtre}
            GROUP BY c.id_consommateur
            ORDER BY c.type, c.id_consommateur""", params)
    pas = get_pas_heures()
    df["conso_kwh"] = df.pop("somme_kw") * pas
    df["facture_eur"] = df.pop("somme_kw_prix") * pas
    return df


def get_repartition_hp_hc(types: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """Energie (kWh) et cout (EUR) par type et par plage tarifaire (HP / HC)."""
    filtre, params = _filtre_types(types)
    df = _requete(
        f"""SELECT c.type, t.plage,
                   SUM(r.puissance_kw)              AS somme_kw,
                   SUM(r.puissance_kw * t.prix_kwh) AS somme_kw_prix
            FROM releves r
            JOIN consommateurs c USING (id_consommateur)
            {_JOIN_TARIF}
            WHERE 1 = 1 {filtre}
            GROUP BY c.type, t.plage ORDER BY c.type, t.plage""", params)
    pas = get_pas_heures()
    df["conso_kwh"] = df.pop("somme_kw") * pas
    df["cout_eur"] = df.pop("somme_kw_prix") * pas
    return df


def get_kpis(types: Optional[Sequence[str]] = None) -> dict:
    """Indicateurs globaux pour les cartes KPI du dashboard."""
    filtre, params = _filtre_types(types)
    base = _requete(
        f"""SELECT COUNT(DISTINCT c.id_consommateur) AS nb_consommateurs,
                   COUNT(r.id_releve)                AS nb_releves,
                   COALESCE(SUM(r.puissance_kw), 0)  AS somme_kw,
                   COALESCE(MAX(r.puissance_kw), 0)  AS pic_kw
            FROM consommateurs c LEFT JOIN releves r USING (id_consommateur)
            WHERE 1 = 1 {filtre}""", params).iloc[0]
    facture = _requete(
        f"""SELECT COALESCE(SUM(r.puissance_kw * t.prix_kwh), 0) AS somme_kw_prix
            FROM releves r JOIN consommateurs c USING (id_consommateur)
            {_JOIN_TARIF}
            WHERE 1 = 1 {filtre}""", params).iloc[0]["somme_kw_prix"]
    pas = get_pas_heures()
    return {
        "nb_consommateurs": int(base["nb_consommateurs"]),
        "nb_releves": int(base["nb_releves"]),
        "energie_kwh": float(base["somme_kw"]) * pas,
        "pic_kw": float(base["pic_kw"]),
        "facture_eur": float(facture) * pas,
    }


def verifier_tarification() -> dict:
    """Controle qualite : chaque releve doit correspondre a exactement une ligne tarifaire."""
    df = _requete(
        f"""SELECT COUNT(*) AS nb_releves_tarifes,
                   (SELECT COUNT(*) FROM releves) AS nb_releves_total
            FROM releves r JOIN consommateurs c USING (id_consommateur)
            {_JOIN_TARIF}""").iloc[0]
    return {
        "releves_total": int(df["nb_releves_total"]),
        "lignes_tarifees": int(df["nb_releves_tarifes"]),
        "coherent": int(df["nb_releves_tarifes"]) == int(df["nb_releves_total"]),
    }

def get_resume_consommateurs() -> pd.DataFrame:
    """Une ligne par consommateur : sommes (kW), tarifs, annee (agregation faite en SQL)."""
    return _requete(
        f"""SELECT c.id_consommateur AS consommateur, c.type AS type_conso,
                   c.latitude AS lat, c.longitude AS lon,
                   COUNT(*)                                                  AS n,
                   SUM(r.puissance_kw)                                       AS somme_kw,
                   SUM(r.puissance_kw * t.prix_kwh)                          AS somme_kw_prix,
                   SUM(CASE WHEN t.plage = 'HP' THEN r.puissance_kw ELSE 0 END) AS somme_kw_hp,
                   SUM(CASE WHEN t.plage = 'HC' THEN r.puissance_kw ELSE 0 END) AS somme_kw_hc,
                   AVG(r.puissance_kw)                                       AS kw_moy,
                   CAST(strftime('%Y', MIN(r.horodatage)) AS INTEGER)        AS annee,
                   MAX(CASE WHEN t.plage = 'HP' THEN t.prix_kwh END)         AS prix_hp,
                   MAX(CASE WHEN t.plage = 'HC' THEN t.prix_kwh END)         AS prix_hc
            FROM releves r JOIN consommateurs c USING (id_consommateur)
            {_JOIN_TARIF}
            GROUP BY c.id_consommateur
            ORDER BY c.id_consommateur""")


def get_slots() -> pd.DataFrame:
    """Puissance moyenne (kW) par consommateur, jour de la semaine (0 = lundi) et heure."""
    df = _requete(
        """SELECT r.id_consommateur AS consommateur, c.type AS type_conso,
                  CAST(strftime('%w', r.horodatage) AS INTEGER) AS dow,
                  CAST(strftime('%H', r.horodatage) AS INTEGER) AS heure,
                  AVG(r.puissance_kw) AS puissance_kw
           FROM releves r JOIN consommateurs c USING (id_consommateur)
           GROUP BY r.id_consommateur, dow, heure""")
    df["jour_semaine"] = (df.pop("dow") + 6) % 7      # SQLite : 0 = dimanche -> 0 = lundi
    return df



if __name__ == "__main__":
    print("Types :", get_types())
    print(get_consommation_moyenne_par_type().round(3).to_string(index=False))
    print("KPIs :", get_kpis())
    print("Tarification :", verifier_tarification())
