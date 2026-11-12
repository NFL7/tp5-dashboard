# -*- coding: utf-8 -*-
"""TP4 - Dashboard Dash alimente par la base SQLite (via db.py).
Meme rendu que le TP3 : un filtre transversal pilote les graphiques et la carte."""
import math
import os
from datetime import datetime
from html import escape

import folium
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, ctx, dcc, html
from folium.plugins import MarkerCluster
from plotly.subplots import make_subplots

# --- TP5 : la base SQLite est régénérée à chaque démarrage, à partir des CSV (disque Render non persistant)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import init_db
init_db.main(verifier=False)

import db


# ============================================================
# PARAMÈTRES ET PALETTE
# ============================================================
PAS_MIN = 5  # pas des relevés (minutes)
HEURE_DEBUT_HP, HEURE_FIN_HP = 6, 22   # heures pleines : 6 h <= heure < 22 h ; heures creuses : le reste

TYPES = ["Agriculteur", "Industriel", "Site public"]
COULEURS = {"Agriculteur": "#E09F3E", "Industriel": "#2F6DB5", "Site public": "#8E5BA8"}
JOURS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]

# Noms de communes (affichage seulement) ; les coordonnées viennent de la base.
# Positions FICTIVES : le jeu de données ne fournit aucune coordonnée.
COMMUNES = {
    "Agriculteur 1": "Provins", "Agriculteur 2": "Coulommiers",
    "Agriculteur 3": "Nemours", "Agriculteur 4": "Étampes",
    "Industriel 1": "Melun", "Industriel 2": "Évry-Courcouronnes",
    "Industriel 3": "Gennevilliers", "Industriel 4": "Corbeil-Essonnes",
    "Site public 1": "Paris", "Site public 2": "Versailles",
}

CLUSTER = True        # regroupement des marqueurs voisins (MarkerCluster)
ECART_PRIX = 0.25     # profil atypique : prix moyen à plus de 25 % de la médiane du portefeuille
LABELS = ["Faible", "Moyenne", "Élevée"]
COULEURS_TRANCHE = {"Faible": "#2E7D32", "Moyenne": "#EF6C00", "Élevée": "#C62828"}
P_HAUT = "Facture haute vs consommation"
P_BAS = "Facture basse vs consommation"
MSG_VIDE = "Cochez au moins un type de consommateur"

# Thème du dashboard : bleu nuit du réseau électrique + jaune « courant »
FOND, TEXTE, GRIS = "#EAF0F8", "#142033", "#5A6B85"
NAVY, NAVY2, ACCENT = "#0B1F4B", "#1B4DA1", "#F2A900"


def fr(x, d=0):
    """Nombre au format français : 1 234,5"""
    if pd.isna(x):
        return "n.d."
    return f"{x:,.{d}f}".replace(",", " ").replace(".", ",")


# ============================================================
# DONNEES : lues dans la base SQLite via db.py (plus aucun Excel ici)
# ============================================================
def charger(force=False):
    """Renvoie (res, moy_slot) a partir des requetes SQL de db.py."""
    d = db.get_resume_consommateurs().set_index("consommateur")
    pas_h = PAS_MIN / 60                                               # kW x h = kWh

    res = pd.DataFrame(index=d.index)
    res["type_conso"] = d["type_conso"]
    res["n"] = d["n"]
    res["kwh_obs"] = d["somme_kw"] * pas_h
    res["cout_obs"] = d["somme_kw_prix"] * pas_h
    res["kw_moy"] = d["kw_moy"]
    res["annee"] = d["annee"].astype(int)
    res["kwh_hp_obs"] = d["somme_kw_hp"] * pas_h
    res["kwh_hc_obs"] = d["somme_kw_hc"] * pas_h

    # Couverture de l'année (1,0 = année complète) : ramène un site incomplet à une année pleine
    jours_annee = res["annee"].map(lambda a: pd.Timestamp(int(a), 12, 31).dayofyear)
    res["couverture"] = res["n"] / (jours_annee * 24 * 60 / PAS_MIN)
    res["conso"] = res["kwh_obs"] / res["couverture"]                  # kWh / an
    res["facture"] = res["cout_obs"] / res["couverture"]               # € / an
    res["conso_hp"] = res["kwh_hp_obs"] / res["couverture"]
    res["conso_hc"] = res["kwh_hc_obs"] / res["couverture"]
    res["part_hc"] = res["kwh_hc_obs"] / res["kwh_obs"]
    res["tarif_moyen"] = res["cout_obs"] / res["kwh_obs"]              # €/kWh réellement payé
    res["tarif_hp"] = d["prix_hp"] * 100                               # €/kWh -> c€/kWh
    res["tarif_hc"] = d["prix_hc"] * 100
    jours_obs = res["n"] * PAS_MIN / 1440                              # durée réellement observée
    res["kwh_hp_j"] = res["kwh_hp_obs"] / jours_obs                    # kWh/jour
    res["kwh_hc_j"] = res["kwh_hc_obs"] / jours_obs
    res["prix_rel"] = res["tarif_moyen"] * 100 / res["tarif_hp"] * 100  # 100 = tout payé au tarif HP
    res["eco_pct"] = 100 - res["prix_rel"]
    res["lat"], res["lon"] = d["lat"], d["lon"]
    res["commune"] = [COMMUNES.get(c, "") for c in res.index]

    # Profil moyen heure x jour de la semaine, en indice (100 = puissance moyenne du consommateur)
    slot = db.get_slots()
    slot["indice"] = (slot["puissance_kw"] / slot["consommateur"].map(res["kw_moy"]) * 100
                      ).replace([np.inf, -np.inf], 0).fillna(0)
    moy_slot = slot.groupby(["type_conso", "jour_semaine", "heure"])[["indice", "puissance_kw"]].mean()
    return res, moy_slot


def terciles(s):
    q1, q2 = s.quantile([1 / 3, 2 / 3])
    if q1 < q2:
        t = pd.cut(s, [-np.inf, q1, q2, np.inf], labels=LABELS)   # Faible <= q1 < Moyenne <= q2 < Élevée
    else:
        t = pd.qcut(s.rank(method="first"), 3, labels=LABELS)
    return t.astype(str), q1, q2


def classer(tableau):
    tableau = tableau.copy()
    tableau["tranche_facture"], f_q1, f_q2 = terciles(tableau["facture"])
    tableau["tranche_conso"], _, _ = terciles(tableau["conso"])
    prix_median = tableau["tarif_moyen"].median()
    seuil_haut, seuil_bas = prix_median * (1 + ECART_PRIX), prix_median * (1 - ECART_PRIX)
    tableau["profil"] = "Standard"
    tableau.loc[tableau["tarif_moyen"] >= seuil_haut, "profil"] = P_HAUT
    tableau.loc[tableau["tarif_moyen"] <= seuil_bas, "profil"] = P_BAS
    return tableau, {"f_q1": f_q1, "f_q2": f_q2, "prix_median": prix_median}


res, moy_slot, SEUILS, MAJ = None, None, None, None   # tables globales, remplies par recharger()


def recharger(force=False):
    """(Re)charge les données depuis la base et recalcule tranches et profils.
    Appelée au démarrage et par le bouton « Rafraîchir »."""
    global res, moy_slot, SEUILS, MAJ
    brut, moy_slot = charger(force)
    res, SEUILS = classer(brut)
    MAJ = datetime.now()


recharger()
print(f"\n{len(res)} consommateurs prêts ; années couvertes : {sorted(int(a) for a in res['annee'].unique())}")
print(f"Seuils de facture : {fr(SEUILS['f_q1'])} € et {fr(SEUILS['f_q2'])} € ; prix moyen médian : {fr(SEUILS['prix_median'], 3)} €/kWh")


def selection(types):
    return res[res["type_conso"].isin(types)]


def mise_en_page(fig, titre, haut_sup=0, gauche=60):
    """Style commun : fond transparent, police compacte.
    Le titre-message (une ou deux lignes séparées par <br>) est posé en annotations alignées à gauche."""
    lignes_titre = titre.split("<br>") if titre else []
    haut = (10 + 17 * len(lignes_titre) + haut_sup) if titre else 10
    fig.update_layout(template="plotly_white", autosize=True, font=dict(size=11, color=TEXTE),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", title=None,
                      margin=dict(t=haut, l=gauche, r=16, b=44))
    fig.update_xaxes(automargin=False)
    fig.update_yaxes(automargin=False)
    for i, texte in enumerate(lignes_titre):
        fig.add_annotation(text=texte, xref="paper", yref="paper", x=0, y=1, xanchor="left", yanchor="bottom",
                           xshift=-gauche, yshift=haut - 4 - 17 * (i + 1), showarrow=False, align="left",
                           font=dict(size=12.5, color=TEXTE))
    return fig


def figure_vide(message):
    fig = go.Figure()
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.add_annotation(x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False, text=message,
                       font=dict(size=15, color=GRIS))
    return mise_en_page(fig, "")


# ============================================================
# SCATTER PLOT : HC et prix payé ; lien entre HP et HC
# ============================================================
def fig_scatter(types):
    s_all = selection(types)
    if s_all.empty:
        return figure_vide(MSG_VIDE)
    n = len(s_all)
    visibles = s_all[(s_all["kwh_hp_j"] > 0) & (s_all["kwh_hc_j"] > 0)]   # l'échelle log ne peut pas afficher 0
    if visibles.empty:
        return figure_vide("Aucune consommation à afficher pour la sélection")
    fig = go.Figure()
    for t in types:
        s = visibles[visibles["type_conso"] == t]
        pos = [("top center", "bottom center")[i % 2] for i in range(len(s))]
        fig.add_trace(go.Scatter(
            x=s["kwh_hp_j"], y=s["kwh_hc_j"], mode="markers+text", name=t,
            text=[c.replace(t + " ", "") for c in s.index], textposition=pos,
            marker=dict(color=COULEURS[t], size=12 + 2 * s["eco_pct"].fillna(0).clip(lower=0),
                        opacity=0.88, line=dict(color="white", width=1)),
            customdata=np.column_stack([s.index, s["part_hc"] * 100, s["tarif_moyen"] * 100, s["prix_rel"], s["eco_pct"]]),
            hovertemplate=("<b>%{customdata[0]}</b><br>HP : %{x:,.0f} kWh/j · HC : %{y:,.0f} kWh/j"
                           "<br>Part HC : %{customdata[1]:.1f} %<br>Prix moyen payé : %{customdata[2]:.2f} c€/kWh"
                           "<br>Prix relatif au tarif HP : %{customdata[3]:.0f} (économie %{customdata[4]:.0f} %)<extra></extra>")))
    fig.update_xaxes(title="Heures pleines (kWh/j, log)", type="log")
    fig.update_yaxes(title="Heures creuses (kWh/j, log)", type="log")
    fig.update_layout(legend=dict(orientation="v", x=0.99, xanchor="right", y=0.02, yanchor="bottom", font=dict(size=10),
                                  bgcolor="rgba(255,255,255,.75)"))

    if n >= 5:
        rho_prix = s_all["part_hc"].rank().corr(s_all["prix_rel"].rank())   # Spearman = Pearson sur les rangs
        rho_hphc = s_all["kwh_hp_j"].rank().corr(s_all["kwh_hc_j"].rank())
        rho_prix = 0.0 if pd.isna(rho_prix) else rho_prix
        rho_hphc = 0.0 if pd.isna(rho_hphc) else rho_hphc
        if rho_prix <= -0.5:
            phrase = f"Plus d'heures creuses, prix moyen plus bas (ρ = {rho_prix:.2f})"
        elif rho_prix >= 0.5:
            phrase = f"Plus d'heures creuses, mais pas de prix plus bas (ρ = {rho_prix:.2f})"
        else:
            phrase = f"Lien faible entre part d'heures creuses et prix payé (ρ = {rho_prix:.2f})"
        lien = "forte" if abs(rho_hphc) >= 0.7 else "modérée" if abs(rho_hphc) >= 0.4 else "faible"
        titre = f"{phrase}<br>HP et HC : corrélation {lien} (ρ = {rho_hphc:.2f}), Spearman sur {n} sites"
    else:
        titre = f"{n} consommateurs : trop peu pour une corrélation, on compare les niveaux HP et HC"
    if len(visibles) < n:
        titre += f"<br>{n - len(visibles)} site(s) à 0 kWh : non affiché(s) (échelle log)"
    return mise_en_page(fig, titre)


# ============================================================
# Facture annuelle par consommateur
# ============================================================
def fig_facture(types):
    s_all = selection(types)
    if s_all.empty:
        return figure_vide(MSG_VIDE)
    s = s_all.sort_values("facture")
    fig = go.Figure(go.Bar(
        x=s["facture"] / 1000, y=s.index, orientation="h",
        marker=dict(color=[COULEURS[t] for t in s["type_conso"]], line=dict(color="white", width=1)),
        text=[f"{fr(v / 1000, 1)} k€" for v in s["facture"]], textposition="outside", cliponaxis=False,
        customdata=np.column_stack([s["conso"] / 1000, s["tarif_moyen"] * 100, s["part_hc"] * 100]),
        hovertemplate=("<b>%{y}</b><br>Facture : %{x:,.1f} k€/an<br>Consommation : %{customdata[0]:,.0f} MWh/an"
                       "<br>Prix moyen : %{customdata[1]:.2f} c€/kWh<br>Part HC : %{customdata[2]:.0f} %<extra></extra>")))
    total = s["facture"].sum()
    top = s.index[-1]
    titre = (f"{top} pèse {s['facture'].iloc[-1] / total:.0%} de la facture de la sélection" if total > 0
             else "Aucune facture pour la sélection (consommation nulle)")
    fig.update_xaxes(title="Facture annuelle (k€)", range=[0, s["facture"].max() / 1000 * 1.3])
    fig.update_yaxes(title="")
    return mise_en_page(fig, titre, gauche=100)


# ============================================================
# HEATMAP : moments de la journée et de la semaine selon le type
# ============================================================
def fig_heatmap(types):
    if not types:
        return figure_vide(MSG_VIDE)
    fig = make_subplots(rows=1, cols=len(types), shared_yaxes=True, subplot_titles=types, horizontal_spacing=0.025)
    pics = {}
    for i, t in enumerate(types, start=1):
        z = moy_slot.loc[t, "indice"].unstack("heure").reindex(index=range(7), columns=range(24))
        kw = moy_slot.loc[t, "puissance_kw"].unstack("heure").reindex(index=range(7), columns=range(24))
        if np.isnan(z.values).all() or np.nanmax(z.values) <= 0:     # type sans aucune consommation
            pics[t] = None
        else:
            j, hh = np.unravel_index(np.nanargmax(z.values), z.shape)
            pics[t] = (JOURS[j], int(hh))
        fig.add_trace(go.Heatmap(
            z=z.values, x=list(range(24)), y=JOURS, coloraxis="coloraxis",
            text=np.char.add(np.round(kw.values, 1).astype(str), " kW"),
            hovertemplate="%{y} %{x}h<br>Indice : %{z:.0f}<br>Puissance moyenne : %{text}<extra>" + t + "</extra>"),
            row=1, col=i)
        fig.update_xaxes(title="Heure", tickmode="linear", dtick=3, row=1, col=i)
    fig.update_yaxes(autorange="reversed", categoryorder="array", categoryarray=JOURS)   # lundi en haut
    fig.add_vline(x=5.5, line_dash="dot", line_color="#333", row="all", col="all")        # limites HC / HP
    fig.add_vline(x=21.5, line_dash="dot", line_color="#333", row="all", col="all")
    for a, t in zip(fig.layout.annotations, types):
        a.update(font=dict(color=COULEURS[t], size=13))
    fig.update_layout(coloraxis=dict(colorscale="YlOrRd", cmax=200,
                                     colorbar=dict(title=dict(text="Indice", font=dict(size=10)), thickness=10, len=0.9,
                                                   tickfont=dict(size=9))))
    hc_types = [t for t in types if pics[t] and not (HEURE_DEBUT_HP <= pics[t][1] < HEURE_FIN_HP)]
    sans_conso = [t for t in types if pics[t] is None]
    detail = " · ".join(f"{t} {pics[t][0]} {pics[t][1]}h" if pics[t] else f"{t} : aucune consommation" for t in types)
    debut = (f"Pic en heures creuses pour : {', '.join(hc_types)} - " if hc_types
             else "Pics : " if sans_conso else "Pic en heures pleines pour tous : ")
    titre = debut + detail
    return mise_en_page(fig, titre, haut_sup=22)


# ============================================================
# INDICATEURS (cartes en haut du dashboard)
# ============================================================
def carte_kpi(libelle, valeur, detail="", couleur=ACCENT):
    return html.Div(
        style={"background": "white", "borderRadius": "10px", "padding": "7px 14px", "borderLeft": f"5px solid {couleur}",
               "boxShadow": "0 1px 4px rgba(11,31,75,.10)", "minWidth": "0"},
        children=[
            html.Div(libelle, style={"fontSize": "10.5px", "color": GRIS, "textTransform": "uppercase", "letterSpacing": "0.06em",
                                     "fontWeight": "600"}),
            html.Div(valeur, style={"fontSize": "22px", "fontWeight": "800", "color": NAVY, "lineHeight": "1.15"}),
            html.Div(detail, style={"fontSize": "11px", "color": GRIS, "whiteSpace": "nowrap", "overflow": "hidden",
                                    "textOverflow": "ellipsis"}),
        ])


PLURIEL = {"Agriculteur": "agriculteurs", "Industriel": "industriels", "Site public": "sites publics"}


def indicateurs(types):
    s = selection(types)
    if s.empty:
        return [carte_kpi(lib, "-", "aucun type sélectionné") for lib in
                ("Consommateurs", "Consommation annuelle", "Facture annuelle", "Prix moyen payé")]
    conso, facture = s["conso"].sum(), s["facture"].sum()
    conso_txt = f"{fr(conso / 1e6, 2)} GWh" if conso >= 1e6 else f"{fr(conso / 1e3)} MWh"
    part_hc = s["conso_hc"].sum() / conso if conso > 0 else 0
    prix_txt = f"{fr(facture / conso, 3)} €/kWh" if conso > 0 else "n.d."
    detail_prix = f"{part_hc:.0%} de l'énergie en heures creuses" if conso > 0 else "aucune consommation"
    return [
        carte_kpi("Consommateurs", str(len(s)), " · ".join(f"{(s['type_conso'] == t).sum()} {PLURIEL[t] if (s['type_conso'] == t).sum() > 1 else t.lower()}" for t in types), NAVY2),
        carte_kpi("Consommation annuelle", conso_txt, "chaque site sur son année de relevé", ACCENT),
        carte_kpi("Facture annuelle", f"{fr(facture / 1e3)} k€", "grille HP / HC, hors abonnement et taxes", "#2E7D32"),
        carte_kpi("Prix moyen payé", prix_txt, detail_prix, "#8E5BA8"),
    ]


# ============================================================
# CARTE FOLIUM
# ============================================================
def ligne(libelle, valeur):
    return (f'<tr><td style="padding:3px 8px;color:#555;">{libelle}</td>'
            f'<td style="padding:3px 8px;text-align:right;"><b>{valeur}</b></td></tr>')


def contraste(hex_color):
    """Couleur de texte lisible (#222 ou #fff) sur un fond donné."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#222222" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "#ffffff"


def popup_html(r):
    c = COULEURS[r.type_conso]
    corps = "".join([
        ligne("Consommateur", escape(r.consommateur)),
        ligne("Position (fictive)", escape(r.commune)),
        ligne("Consommation annuelle", f"{fr(r.conso)} kWh"),
        ligne("Facture annuelle", f"{fr(r.facture)} €"),
        ligne("Prix moyen", f"{fr(r.tarif_moyen, 3)} €/kWh"),
        ligne("Part en heures creuses", "n.d." if pd.isna(r.part_hc) else f"{r.part_hc:.0%}"),
        ligne("Tarif HP / HC", f"{fr(r.tarif_hp, 2)} / {fr(r.tarif_hc, 2)} c€/kWh"),
        ligne("Tranche de facture", r.tranche_facture),
    ])
    return folium.Popup(
        f'<div style="font-family:Arial,sans-serif;font-size:13px;min-width:250px;">'
        f'<div style="background:{c};color:{contraste(c)};padding:6px 8px;font-weight:600;'
        f'border-radius:3px 3px 0 0;">{escape(r.type_conso)}</div>'
        f'<table style="width:100%;border-collapse:collapse;">{corps}</table></div>', max_width=320)


def tooltip_html(r):
    return folium.Tooltip(f"<b>{escape(r.consommateur)}</b> ({escape(r.type_conso)})<br>"
                          f"{fr(r.conso)} kWh/an · {fr(r.facture)} €/an", sticky=True)


def js_cluster(fond, bordure, texte):
    return """
    function(cluster) {
        var n = cluster.getChildCount();
        var d = n < 10 ? 32 : (n < 100 ? 40 : 48);
        var h = '<div style="width:'+d+'px;height:'+d+'px;line-height:'+(d-6)+'px;border-radius:50%;'
              + 'text-align:center;font:600 12px Arial,sans-serif;background:FOND;color:TEXTE;'
              + 'border:3px solid BORDURE;box-shadow:0 1px 4px rgba(0,0,0,.35);">'+n+'</div>';
        return L.divIcon({html: h, className: 'cluster-perso', iconSize: L.point(d, d)});
    }""".replace("FOND", fond).replace("TEXTE", texte).replace("BORDURE", bordure)


def conteneur(groupe, fond, bordure, texte):
    if CLUSTER:
        return MarkerCluster(icon_create_function=js_cluster(fond, bordure, texte)).add_to(groupe)
    return groupe


def pastille(c):
    return (f'<span style="display:inline-block;width:12px;height:12px;border-radius:50%;'
            f'background:{c};border:1px solid #333;margin-right:7px;vertical-align:middle;"></span>')


def anneau(c, dash=False):
    style = "dashed" if dash else "solid"
    return (f'<span style="display:inline-block;width:12px;height:12px;border-radius:50%;'
            f'border:3px {style} {c};margin-right:7px;vertical-align:middle;box-sizing:border-box;"></span>')


def construire_carte(types):
    """Carte Folium limitée aux types sélectionnés. Renvoie le HTML complet (pour html.Iframe srcDoc)."""
    sel = selection(types).reset_index()

    # --- carte de base, centrée sur la sélection
    if len(sel):
        lat_min, lat_max, lon_min, lon_max = sel["lat"].min(), sel["lat"].max(), sel["lon"].min(), sel["lon"].max()
        centre = [(lat_min + lat_max) / 2, (lon_min + lon_max) / 2]
        etendue = max(lat_max - lat_min, lon_max - lon_min)
        zoom = 12 if etendue < 1e-6 else int(np.clip(math.floor(math.log2(1406 / etendue)), 2, 16))
    else:
        centre, zoom, etendue = [48.7, 2.6], 9, 0
    m = folium.Map(location=centre, zoom_start=zoom, tiles=None, control_scale=True, prefer_canvas=True)
    # Fond OpenStreetMap : gratuit, sans clé API (les fonds CARTO exigent maintenant une clé)
    folium.TileLayer("OpenStreetMap", name="Fond OpenStreetMap").add_to(m)
    if len(sel) and etendue >= 1e-6:
        m.fit_bounds([[lat_min, lon_min], [lat_max, lon_max]], padding=(40, 40))

    # --- points pleins par type (couches affichées par défaut)
    for t in types:
        sous = sel[sel["type_conso"] == t]
        c = COULEURS[t]
        fg = folium.FeatureGroup(name=f"Type : {t} ({len(sous)})", show=True)
        cible = conteneur(fg, c, "#ffffff", contraste(c))
        for r in sous.itertuples(index=False):
            folium.CircleMarker(location=[r.lat, r.lon], radius=8, color="#333333", weight=1, fill=True,
                                fill_color=c, fill_opacity=0.9, tooltip=tooltip_html(r), popup=popup_html(r)).add_to(cible)
        fg.add_to(m)

    # --- anneaux par tranche de facture (couches masquées par défaut)
    for tr in LABELS:
        sous = sel[sel["tranche_facture"] == tr]
        if sous.empty:
            continue
        c = COULEURS_TRANCHE[tr]
        fg = folium.FeatureGroup(name=f"Facture : {tr} ({len(sous)})", show=False)
        cible = conteneur(fg, "#ffffff", c, "#222222")
        for r in sous.itertuples(index=False):
            folium.CircleMarker(location=[r.lat, r.lon], radius=12, color=c, weight=4, fill=False,
                                tooltip=tooltip_html(r), popup=popup_html(r)).add_to(cible)
        fg.add_to(m)

    # --- anneaux pointillés des profils atypiques (couches masquées par défaut)
    for nom in (P_HAUT, P_BAS):
        sous = sel[sel["profil"] == nom]
        if sous.empty:
            continue
        fg = folium.FeatureGroup(name=f"Atypique : {nom} ({len(sous)})", show=False)
        cible = conteneur(fg, "#222222", "#222222", "#ffffff")
        for r in sous.itertuples(index=False):
            folium.CircleMarker(location=[r.lat, r.lon], radius=17, color="#111111", weight=3, dash_array="4 4",
                                fill=False, tooltip=tooltip_html(r), popup=popup_html(r)).add_to(cible)
        fg.add_to(m)
    folium.LayerControl(collapsed=True, position="topright").add_to(m)

    # --- légende repliée par défaut
    lignes_types = "".join(
        f'<div style="margin:3px 0;">{pastille(COULEURS[t])}{escape(t)} '
        f'<span style="color:#777;">({(sel["type_conso"] == t).sum()})</span></div>' for t in types)
    seuils_txt = {"Faible": f"≤ {fr(SEUILS['f_q1'])} €", "Moyenne": f"{fr(SEUILS['f_q1'])} à {fr(SEUILS['f_q2'])} €",
                  "Élevée": f"> {fr(SEUILS['f_q2'])} €"}
    lignes_tranches = "".join(
        f'<div style="margin:3px 0;">{anneau(COULEURS_TRANCHE[tr])}{tr} '
        f'<span style="color:#777;">({seuils_txt[tr]})</span></div>' for tr in LABELS)
    legende = f"""
<details style="position:fixed;bottom:22px;left:10px;z-index:9999;background:rgba(255,255,255,.96);
     padding:6px 12px;border:1px solid #bbb;border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.25);
     font-family:Arial,sans-serif;font-size:12px;line-height:1.3;max-width:270px;">
  <summary style="font-weight:700;font-size:13px;cursor:pointer;">Légende</summary>
  <div style="font-weight:600;margin-top:6px;">Type de consommateur (point)</div>
  {lignes_types}
  <div style="font-weight:600;margin-top:8px;">Facture annuelle (anneau)</div>
  {lignes_tranches}
  <div style="color:#777;font-size:11px;">Seuils calculés sur les 10 consommateurs, fixes quel que soit le filtre.</div>
  <div style="font-weight:600;margin-top:8px;">Profil atypique (anneau pointillé)</div>
  <div style="margin:3px 0;">{anneau("#111111", dash=True)}Prix moyen à plus de {ECART_PRIX:.0%} de la médiane
  <span style="color:#777;">({fr(SEUILS["prix_median"], 3)} €/kWh)</span></div>
  <div style="color:#444;margin-top:8px;">Bulle numérotée : plusieurs consommateurs proches, zoomer pour les séparer.
  Survol : résumé ; clic : fiche détaillée.</div>
  <div style="color:#8a3b00;margin-top:6px;">Positions fictives : le jeu de données ne fournit pas de coordonnées.</div>
</details>"""
    m.get_root().html.add_child(folium.Element(legende))
    return m.get_root().render()


# ============================================================
# MISE EN PAGE DASH
# ============================================================
STYLE_BLOC = {"background": "white", "borderRadius": "12px", "padding": "8px 12px 6px", "display": "flex",
              "flexDirection": "column", "minHeight": "0", "minWidth": "0", "overflow": "hidden",
              "boxShadow": "0 2px 8px rgba(11,31,75,.10)", "borderTop": f"3px solid {ACCENT}"}
CONFIG_GRAPH = {"displaylogo": False, "responsive": True}
PLEIN = {"position": "absolute", "top": "0", "left": "0", "width": "100%", "height": "100%"}
STYLE_BOUTON = {"background": "rgba(255,255,255,.12)", "color": "white", "border": "1px solid rgba(255,255,255,.45)",
                "borderRadius": "8px", "padding": "6px 11px", "fontWeight": "600", "fontSize": "12px", "cursor": "pointer",
                "whiteSpace": "nowrap"}


def bloc(titre, indice, enfant, **style):
    """Carte blanche : titre + indication de lecture, puis le graphique qui remplit tout l'espace restant."""
    return html.Div(style={**STYLE_BLOC, **style}, children=[
        html.Div(style={"display": "flex", "justifyContent": "space-between", "alignItems": "baseline", "gap": "10px"}, children=[
            html.H3(titre, style={"margin": "0", "fontSize": "14px", "color": NAVY}),
            html.Span(indice, style={"fontSize": "11px", "color": GRIS, "textAlign": "right"}),
        ]),
        html.Div(style={"position": "relative", "flex": "1", "minHeight": "0"}, children=[enfant]),
    ])


effectifs = res["type_conso"].value_counts()
app = Dash(__name__, title="Consommation électrique - dashboard")
server = app.server   # TP5 : exposé pour gunicorn (Render exécute « gunicorn app:server »)
app.index_string = """<!DOCTYPE html>
<html>
  <head>
    {%metas%}
    <title>{%title%}</title>
    {%favicon%}
    {%css%}
    <style>html, body { margin: 0; height: 100%; }</style>
  </head>
  <body>
    {%app_entry%}
    <footer>{%config%}{%scripts%}{%renderer%}</footer>
  </body>
</html>"""

app.layout = html.Div(
    style={"fontFamily": "Segoe UI, Arial, sans-serif", "background": FOND, "color": TEXTE, "height": "100vh", "minHeight": "760px",
           "display": "flex", "flexDirection": "column", "boxSizing": "border-box", "padding": "0 14px 10px"},
    children=[
        # --- bandeau : titre, filtre transversal, boutons
        html.Div(
            style={"background": f"linear-gradient(100deg, {NAVY} 0%, {NAVY2} 100%)", "color": "white", "borderRadius": "0 0 14px 14px",
                   "padding": "8px 18px", "display": "flex", "alignItems": "center", "justifyContent": "space-between",
                   "gap": "12px", "flexWrap": "wrap", "borderBottom": f"3px solid {ACCENT}"},
            children=[
                html.Div(style={"display": "flex", "alignItems": "center", "gap": "12px"}, children=[
                    html.Div("⚡", style={"fontSize": "28px", "color": ACCENT}),
                    html.Div([
                        html.H1("Consommation électrique : usages, tarification et territoire",
                                style={"margin": "0", "fontSize": "16.5px", "fontWeight": "700", "whiteSpace": "nowrap"}),
                        html.Div(f"{len(res)} consommateurs · relevés toutes les {PAS_MIN} min · tarif heures pleines / creuses",
                                 style={"fontSize": "11.5px", "opacity": "0.8"}),
                    ]),
                ]),
                html.Div(style={"display": "flex", "alignItems": "center", "gap": "10px"}, children=[
                    html.Label("Type de consommateur", htmlFor="filtre-type",
                               style={"fontWeight": "700", "fontSize": "11px", "textTransform": "uppercase", "letterSpacing": "0.05em",
                                      "whiteSpace": "nowrap"}),
                    dcc.Checklist(
                        id="filtre-type",
                        options=[{"label": f" {t} ({effectifs[t]})", "value": t} for t in TYPES],
                        value=TYPES,
                        style={"display": "flex", "gap": "8px", "flexWrap": "nowrap"},
                        labelStyle={"display": "inline-block", "cursor": "pointer", "background": "rgba(255,255,255,.14)",
                                    "padding": "5px 12px", "borderRadius": "16px", "fontSize": "12.5px", "whiteSpace": "nowrap"},
                        inputStyle={"marginRight": "5px", "accentColor": ACCENT}),
                ]),
                html.Div(style={"display": "flex", "alignItems": "center", "gap": "10px"}, children=[
                    dcc.Loading(type="dot", color=ACCENT, children=html.Span(id="maj", style={"fontSize": "11px", "opacity": "0.85", "whiteSpace": "nowrap"})),
                    html.Button("↻ Rafraîchir", id="btn-refresh", n_clicks=0, style=STYLE_BOUTON),
                    html.Button("Réinitialiser", id="btn-reset", n_clicks=0, style=STYLE_BOUTON),
                ]),
            ]),

        # --- indicateurs
        html.Div(id="kpis", style={"display": "grid", "gridTemplateColumns": "repeat(4, minmax(0, 1fr))", "gap": "10px",
                                   "padding": "10px 0 0"}),

        # --- grille principale : carte à gauche sur deux lignes, trois graphiques à droite
        html.Div(
            style={"flex": "1", "minHeight": "0", "display": "grid", "gap": "10px", "paddingTop": "10px",
                   "gridTemplateColumns": "1.2fr 1fr 1fr", "gridTemplateRows": "minmax(0, 1fr) minmax(0, 1fr)"},
            children=[
                bloc("Territoire : où sont les consommateurs ?", "clic sur un point : fiche détaillée",
                     html.Iframe(id="carte", srcDoc="", style={**PLEIN, "border": "none", "borderRadius": "8px"}),
                     gridRow="1 / span 2"),
                bloc("Heures creuses et prix payé", "taille du point = économie vs tarif HP",
                     dcc.Graph(id="graph-scatter", config=CONFIG_GRAPH, style=PLEIN)),
                bloc("Facture annuelle par consommateur", "couleur = type de consommateur",
                     dcc.Graph(id="graph-facture", config=CONFIG_GRAPH, style=PLEIN)),
                bloc("Quand consomme-t-on ?", "indice 100 = puissance moyenne du site · pointillés : limites HC / HP",
                     dcc.Graph(id="graph-heatmap", config=CONFIG_GRAPH, style=PLEIN), gridColumn="2 / span 2"),
            ]),

        html.Div("Relevés de 2022 à 2025 : montants comparés tels quels (site incomplet ramené à une année). Factures sans abonnement "
                 "ni taxes. Positions sur la carte fictives (aucune coordonnée dans les données).",
                 style={"color": GRIS, "fontSize": "10.5px", "paddingTop": "6px"}),
    ])


def calculer_vues(types):
    """Tout ce qui dépend du filtre, à partir de la liste des types cochés."""
    types = [t for t in TYPES if t in (types or [])]
    return indicateurs(types), construire_carte(types), fig_scatter(types), fig_facture(types), fig_heatmap(types)


@app.callback(
    Output("kpis", "children"),
    Output("carte", "srcDoc"),
    Output("graph-scatter", "figure"),
    Output("graph-facture", "figure"),
    Output("graph-heatmap", "figure"),
    Output("maj", "children"),
    Input("filtre-type", "value"),
    Input("btn-refresh", "n_clicks"),
)
def mettre_a_jour(types, _clics_refresh):
    """Un seul callback : le filtre ET le bouton « Rafraîchir » redessinent tout le dashboard.
    Le bouton relance en plus les requêtes SQL avant de recalculer les vues."""
    if ctx.triggered_id == "btn-refresh":
        recharger(force=True)
    return (*calculer_vues(types), f"Mis à jour à {MAJ:%H:%M:%S}")


@app.callback(Output("filtre-type", "value"), Input("btn-reset", "n_clicks"), prevent_initial_call=True)
def reinitialiser(_clics):
    """Bouton « Réinitialiser » : recoche les trois types (ce qui relance le callback principal)."""
    return TYPES


if __name__ == "__main__":
    app.run(debug=True)