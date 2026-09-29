from datetime import date
from io import StringIO

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Mirakl Product Status Checker",
    layout="wide",
    page_icon="./icons/logo_color.png"
)
t1, t2 = st.columns([0.1, 1.5])
with t1:
    st.image("./icons/logo_color.png", width=70)
with t2:
    st.header(":color[**MIRAKL** \u22EE]{foreground=rgb(39,99,255)} :orange[\u00FEroduct \u0161tatus \u010Dhecker]")

st.space("small")

# --------------------------
# Session state
# --------------------------
DEFAULTS = {
    "status_choice": "ALL",
    "date_since": None,
    "date_to": None,
    "skus_raw": "",
    "shop_id": "",
    "api_key": "",
    "sales_channel": None,
}

for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)


def reset_filters():
    st.session_state["status_choice"] = DEFAULTS["status_choice"]
    st.session_state["status_choice_label"] = DEFAULTS["status_choice"]
    st.session_state["date_since"] = DEFAULTS["date_since"]
    st.session_state["date_to"] = DEFAULTS["date_to"]
    st.session_state["skus_raw"] = DEFAULTS["skus_raw"]


# --------------------------
# Helpers
# --------------------------
def parse_identifiers(raw: str) -> list[str]:
    """Split by newline/comma/semicolon/space, deduplicate while preserving order."""
    if not raw:
        return []
    for s in [",", ";", "\n", " "]:
        raw = raw.replace(s, "|")
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def build_params(
        status: str,
        since: date | None,
        to: date | None,
        skus: list[str],
        shop_id: str,
):
    params = []
    if since:
        params.append(("updated_since", f"{since}T00:00:00Z"))
    if to:
        params.append(("updated_to", f"{to}T23:59:59Z"))
    if status != "ALL":
        params.append(("status", status))
    if shop_id:
        params.append(("shop_id", shop_id))
    for sku in skus:
        params.append(("provider_unique_identifier", sku))
    return params


def _ean(product: dict) -> str | None:
    return next(
        (d.get("value") for d in product.get("unique_identifiers", []) if d.get("code") == "EAN"),
        None,
    )


def normalize_row(product: dict) -> dict:
    errors = product.get("errors", []) or []
    warnings = product.get("warnings", []) or []

    error_msgs = "; ".join(
        f"[{e.get('code', '')}] {e.get('message', '')}" for e in errors
    )

    # Collect ALL rejection details — previous code kept only the last one
    rejection_labels, rejection_codes = [], []
    for err in errors:
        rd = err.get("rejection_details") or {}
        if rd.get("reason_label"):
            rejection_labels.append(rd["reason_label"])
        if rd.get("reason_code"):
            rejection_codes.append(rd["reason_code"])

    warning_msgs = "; ".join(
        f"[{w.get('code', '')}] {w.get('message', '')}" for w in warnings
    )

    return {
        "SKU": product.get("provider_unique_identifier"),
        "EAN": _ean(product),
        "Status": product.get("status"),
        "Erreurs": len(errors),
        "Commentaire opérateur": " | ".join(rejection_labels) or None,
        "Code rejet": " | ".join(dict.fromkeys(rejection_codes)) or None,
        "Messages erreurs": error_msgs or None,
        "Warnings": len(warnings),
        "Messages warnings": warning_msgs or None,
    }


def explode_errors(items: list) -> pd.DataFrame:
    """One row per error per product."""
    rows = []
    for product in items:
        sku = product.get("provider_unique_identifier")
        ean = _ean(product)
        status = product.get("status")
        for err in (product.get("errors", []) or []):
            rd = err.get("rejection_details") or {}
            rows.append({
                "SKU": sku,
                "EAN": ean,
                "Status": status,
                "Code erreur": err.get("code"),
                "Message erreur": err.get("message"),
                "Canaux": ", ".join(err.get("channels", [])) if err.get("channels") else None,
                "Code rejet": rd.get("reason_code"),
                "Commentaire opérateur": rd.get("reason_label"),
                "Message rejet": rd.get("message"),
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def explode_warnings(items: list) -> pd.DataFrame:
    """One row per warning per product."""
    rows = []
    for product in items:
        sku = product.get("provider_unique_identifier")
        ean = _ean(product)
        status = product.get("status")
        for w in (product.get("warnings", []) or []):
            rows.append({
                "SKU": sku,
                "EAN": ean,
                "Status": status,
                "Code warning": w.get("code"),
                "Message warning": w.get("message"),
                "Attribut": w.get("attribute_code"),
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# --------------------------
# UI — Identification + Filtres
# --------------------------

st.html("<style>.st-key-identification_container { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); } </style>")

with st.container(border=True, key="identification_container"):
    st.subheader("🪪 Identification")
    st.space("xxsmall")

    urls = {
        "https://maxedanl-prod.mirakl.net": "Maxeda BE & NL",
        "https://marketplace.bricodepot.es": "Brico Dépôt ES & PT",
        "https://marketplace.castorama.fr": "Castorama",
        "https://culturafr-prod.mirakl.net": "Cultura",
        "https://marketplace.empik.com": "Empik",
        "https://kiabi.mirakl.net": "Kiabi",
        "https://marketplace.kingfisher.com": "Kingfisher",
        "https://mirakl-web.groupe-rueducommerce.fr": "Rue du Commerce",
        "https://showroomprive.mirakl.net": "Showroom Privé",
        "https://marketplace.worten.pt": "Worten",
    }

    with st.form("mirakl_form", border=False):
        ic1, ic2, ic3 = st.columns(3)
        with ic1:
            sales_channel = st.selectbox(
                "Sélectionnez un canal de vente",
                options=list(urls.keys()),
                index=(list(urls.keys()).index(st.session_state["sales_channel"])
                       if st.session_state["sales_channel"] in urls else 0),
                format_func=lambda u: urls[u],
                key="sales_channel",
                placeholder="Choisir un canal",
            )
        with ic2:
            st.text_input(
                "Shop ID du vendeur",
                value=st.session_state["shop_id"],
                key="shop_id",
                placeholder="Ex: 12345",
            )
        with ic3:
            st.text_input(
                "Clé API du vendeur",
                value=st.session_state["api_key"],
                key="api_key",
                type="password",
                placeholder="********-****-****-****-********",
            )

        st.divider()
        st.subheader("⚙️ Filtres")
        st.space("xxsmall")

        f1, f2, f3 = st.columns([0.8, 1, 1.2])

        with f1:
            st.caption("Statut")
            st.session_state["status_choice"] = st.radio(
                " ",
                ["ALL", "LIVE", "NOT_LIVE"],
                horizontal=True,
                index=["ALL", "LIVE", "NOT_LIVE"].index(st.session_state["status_choice"]),
                key="status_choice_label",
            )
            st.session_state["status_choice"] = st.session_state["status_choice_label"]

        with f2:
            st.caption("Période de mise à jour (optionnel)")
            st.date_input("Depuis", value=st.session_state["date_since"], key="date_since", format="YYYY-MM-DD")
            st.date_input("Avant", value=st.session_state["date_to"], key="date_to", format="YYYY-MM-DD")

        with f3:
            st.caption("SKUs (optionnel)")
            st.text_area(
                " ",
                value=st.session_state["skus_raw"],
                key="skus_raw",
                placeholder="Un par ligne ou séparés par , ; espace",
                height=123,
            )

        st.space("xsmall")

        a1, a2, a3 = st.columns([2, 1, 1])
        with a2:
            submitted = st.form_submit_button(
                label="Valider",
                width="stretch",
                type="primary",
                icon=":material/check:"
            )
        with a3:
            st.form_submit_button(
                label="Réinitialiser",
                width="stretch",
                on_click=reset_filters,
                icon=":material/refresh:"
            )

# --------------------------
# Appel API
# --------------------------
if submitted:
    missing = []
    if not st.session_state["sales_channel"]:
        missing.append("canal de vente")
    if not st.session_state["api_key"]:
        missing.append("clé API")
    if missing:
        st.error(f"Veuillez renseigner : {', '.join(missing)}.")
        st.stop()

    skus = parse_identifiers(st.session_state["skus_raw"])
    url = f"{st.session_state['sales_channel']}/api/mcm/products/sources/status/export"
    headers = {"Authorization": st.session_state["api_key"]}
    params = build_params(
        status=st.session_state["status_choice"],
        since=st.session_state["date_since"] if isinstance(st.session_state["date_since"], date) else None,
        to=st.session_state["date_to"] if isinstance(st.session_state["date_to"], date) else None,
        skus=skus,
        shop_id=st.session_state["shop_id"].strip(),
    )

    st.space("small")

    st.html("<style>.st-key-results_container { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); } </style>")

    with st.container(border=True, key="results_container"):
        st.subheader("🔎 Résultats")
        st.space("xxsmall")
        with st.spinner("Requête en cours…"):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=60)
            except requests.exceptions.RequestException as exc:
                st.error(f"Erreur réseau : {exc}")
                st.stop()

        if not resp.ok:
            st.error(f"Erreur API {resp.status_code} — {resp.reason}\n\n{resp.text[:1000]}")
            st.stop()

        try:
            data = resp.json()
        except ValueError:
            txt = resp.text
            try:
                df_csv = pd.read_csv(StringIO(txt))
                st.info("Réponse interprétée comme CSV (pas JSON).")
                st.dataframe(df_csv, width="stretch", hide_index=True)
                st.download_button(
                    "Télécharger CSV",
                    data=txt.encode("utf-8"),
                    file_name="mirakl_products_export.csv",
                    mime="text/csv",
                    width="stretch",
                )
                st.stop()
            except Exception:
                st.error("Réponse non JSON et non CSV lisible.")
                st.text(txt[:1500])
                st.stop()

        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            items = data["data"]
        elif isinstance(data, list):
            items = data
        else:
            st.warning("Format JSON inattendu. Affichage brut ci-dessous.")
            st.json(data)
            st.stop()

        df = pd.DataFrame([normalize_row(p) for p in items])

        if df.empty:
            st.info("Aucun résultat avec ces filtres.")
            st.stop()

        df = df.sort_values(["Erreurs", "Warnings", "Status"], ascending=[False, False, True])

        # Métriques synthétiques
        n_total = len(df)
        n_not_live = int((df["Status"] == "NOT_LIVE").sum())
        n_live = int((df["Status"] == "LIVE").sum())
        n_rejected = int(df["Commentaire opérateur"].notna().sum())

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("*Produits*", n_total)
        m2.metric("*Publiés*", f":green[{n_live}]")
        m3.metric("*Non publiés*", f":red[{n_not_live}]")
        m4.metric("*Avec commentaires de l'opérateur*", f":orange[{n_rejected}]")

        st.divider()

        total_errors = int(df["Erreurs"].sum())
        total_warnings = int(df["Warnings"].sum())

        tab_summary, tab_errors, tab_warnings = st.tabs([
            "Résumé",
            f"Erreurs ({total_errors})",
            f"Warnings ({total_warnings})",
        ])

        # --- Résumé ---
        with tab_summary:
            max_err = max(int(df["Erreurs"].max()), 1)
            max_warn = max(int(df["Warnings"].max()), 1)
            st.dataframe(
                df,
                width="stretch",
                hide_index=True,
                column_config={
                    "Erreurs": st.column_config.ProgressColumn(
                        "Erreurs", min_value=0, max_value=max_err, format="%d"
                    ),
                    "Commentaire opérateur": st.column_config.TextColumn(
                        "Commentaire opérateur", width="large"
                    ),
                    "Messages erreurs": st.column_config.TextColumn(
                        "Messages erreurs", width="large"
                    ),
                    "Warnings": st.column_config.ProgressColumn(
                        "Warnings", min_value=0, max_value=max_warn, format="%d"
                    ),
                    "Messages warnings": st.column_config.TextColumn(
                        "Messages warnings", width="large"
                    ),
                },
            )

            st.space("xsmall")

            st.download_button(
                "Télécharger le résumé (CSV)",
                data=df.to_csv(index=False).encode("utf-8"),
                file_name="mirakl_resume.csv",
                mime="text/csv",
                type="primary",
                icon=":material/download:"
            )

        # --- Détail Erreurs ---
        with tab_errors:
            df_errors = explode_errors(items)
            if df_errors.empty:
                st.info("Aucune erreur détectée.")
            else:
                st.caption(
                    f"{len(df_errors)} ligne(s) d'erreur — "
                    f"{df_errors['SKU'].nunique()} produit(s) concerné(s)"
                )
                st.dataframe(
                    df_errors,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Message erreur": st.column_config.TextColumn(
                            "Message erreur", width="large"
                        ),
                        "Commentaire opérateur": st.column_config.TextColumn(
                            "Commentaire opérateur", width="large"
                        ),
                        "Message rejet": st.column_config.TextColumn(
                            "Message rejet", width="large"
                        ),
                    },
                )

                st.space("xsmall")

                st.download_button(
                    "Télécharger les erreurs (CSV)",
                    data=df_errors.to_csv(index=False).encode("utf-8"),
                    file_name="mirakl_erreurs.csv",
                    mime="text/csv",
                    type="primary",
                    icon=":material/download:"
                )

        # --- Détail Warnings ---
        with tab_warnings:
            df_warnings = explode_warnings(items)
            if df_warnings.empty:
                st.info("Aucun warning détecté.")
            else:
                st.caption(
                    f"{len(df_warnings)} ligne(s) de warning — "
                    f"{df_warnings['SKU'].nunique()} produit(s) concerné(s)"
                )
                st.dataframe(
                    df_warnings,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Message warning": st.column_config.TextColumn(
                            "Message warning", width="large"
                        ),
                    },
                )

                st.space("xsmall")

                st.download_button(
                    "Télécharger les warnings (CSV)",
                    data=df_warnings.to_csv(index=False).encode("utf-8"),
                    file_name="mirakl_warnings.csv",
                    mime="text/csv",
                    type="primary",
                    icon=":material/download:"
                )
