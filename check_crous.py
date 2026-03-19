import requests
import json
import os
import sys

# ── Configuration ──────────────────────────────────────────────
SEARCH_URL = "https://trouverunlogement.lescrous.fr/api/fr/search/42"

SEARCH_BODY = {
    "idTool": 42,
    "need_aggregation": False,
    "page": 1,
    "pageSize": 100,
    "sector": None,
    "occupationModes": [],
    "location": [
        {"lon": 2.224122, "lat": 48.902156},
        {"lon": 2.4697602, "lat": 48.8155755}
    ],
    "residence": None,
    "precision": 6,
    "equipment": [],
    "price": {"max": 10000000},
    "area": {"min": 0},
    "adaptedPmr": False,
    "toolMechanism": "flow"
}

COOKIES = {
    "PHPSESSID": os.environ.get("CROUS_PHPSESSID", ""),
    "qpid": os.environ.get("CROUS_QPID", ""),
}

HEADERS = {
    "Accept": "application/ld+json, application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
}

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
KNOWN_IDS_FILE = "known_ids.json"
LISTING_BASE_URL = "https://trouverunlogement.lescrous.fr/tools/42/search"

# ── Fetch ───────────────────────────────────────────────────────

class SessionExpiredError(Exception):
    pass


def fetch_listings():
    """
    Récupère les annonces depuis l'API Crous.
    Lève SessionExpiredError si la session est expirée.
    Lève RuntimeError en cas d'autre erreur réseau.
    """
    try:
        response = requests.post(
            SEARCH_URL,
            json=SEARCH_BODY,
            cookies=COOKIES,
            headers=HEADERS,
            timeout=15
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Erreur réseau : {e}") from e

    # Session expirée
    if response.status_code in (401, 403):
        raise SessionExpiredError("Session expirée (401/403)")

    if "discovery/connect" in response.url or "identification" in response.url.lower():
        raise SessionExpiredError("Redirigé vers la page de connexion")

    if response.status_code != 200:
        raise RuntimeError(f"Réponse inattendue : HTTP {response.status_code}")

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise SessionExpiredError("Réponse non-JSON (probablement page de login HTML)") from e

    # Vérifie la structure attendue
    items = data.get("results", {}).get("items")
    if items is None:
        raise SessionExpiredError("Structure JSON inattendue — session peut-être expirée")

    return items


# ── Stockage ────────────────────────────────────────────────────

def load_known_ids():
    """Charge les IDs disponibles lors de la DERNIÈRE vérification."""
    try:
        with open(KNOWN_IDS_FILE, "r") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def save_known_ids(ids):
    """Sauvegarde uniquement les IDs ACTUELLEMENT disponibles."""
    with open(KNOWN_IDS_FILE, "w") as f:
        json.dump(list(ids), f)


# ── Notifications Discord ───────────────────────────────────────

def send_discord_new_listing(listing):
    """Envoie une notification pour un nouveau logement disponible."""
    name      = listing.get("label") or "Logement sans nom"
    residence = listing.get("residence", {})
    address   = residence.get("address") or "Adresse inconnue"
    res_label = residence.get("label") or ""
    lid       = listing.get("id", "")
    link      = f"{LISTING_BASE_URL}#{lid}" if lid else LISTING_BASE_URL

    # Loyer en euros (les montants sont en centimes)
    occupation = listing.get("occupationModes", [])
    if occupation:
        rent     = occupation[0].get("rent", {})
        rent_min = rent.get("min", 0) // 100
        rent_max = rent.get("max", 0) // 100
        prix     = f"{rent_min}€" if rent_min == rent_max else f"{rent_min}–{rent_max}€"
    else:
        prix = "Non renseigné"

    embed = {
        "title": "🏠 Nouveau logement disponible !",
        "color": 0x1D6FA5,
        "fields": [
            {"name": "📋 Nom",     "value": f"{name} ({res_label})" if res_label else name, "inline": False},
            {"name": "📍 Adresse", "value": address,                                         "inline": False},
            {"name": "💶 Loyer",   "value": prix,                                            "inline": True},
            {"name": "🔗 Lien",    "value": f"[Voir l'annonce]({link})",                     "inline": False},
        ],
        "footer": {"text": "Mon Logement Crous • Surveillance automatique"},
    }
    _post_to_discord({"embeds": [embed]})
    print(f"  ✅ Notification envoyée : {name}")


def send_discord_session_expired():
    """Envoie une alerte Discord si la session est expirée."""
    embed = {
        "title": "⚠️ Session Crous expirée !",
        "description": (
            "Le cookie de session n'est plus valide.\n"
            "**Action requise :** reconnecte-toi sur [trouverunlogement.lescrous.fr]"
            "(https://trouverunlogement.lescrous.fr), récupère les nouveaux cookies "
            "`PHPSESSID` et `qpid`, et mets à jour les secrets dans GitHub."
        ),
        "color": 0xFF3B30,
        "footer": {"text": "Mon Logement Crous • Surveillance automatique"},
    }
    _post_to_discord({"embeds": [embed]})
    print("  ⚠️  Alerte session expirée envoyée sur Discord.")


def send_discord_error(message):
    """Envoie une alerte Discord en cas d'erreur inattendue."""
    embed = {
        "title": "❌ Erreur du script Crous",
        "description": f"```{message}```",
        "color": 0xFF9500,
        "footer": {"text": "Mon Logement Crous • Surveillance automatique"},
    }
    _post_to_discord({"embeds": [embed]})
    print(f"  ❌ Alerte erreur envoyée sur Discord : {message}")


def _post_to_discord(payload):
    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()


# ── Programme principal ─────────────────────────────────────────

def main():
    print("🔍 Vérification des annonces Crous Paris...")

    # 1. Récupération des annonces
    try:
        listings = fetch_listings()
    except SessionExpiredError as e:
        print(f"  🔒 Session expirée : {e}")
        send_discord_session_expired()
        sys.exit(1)
    except RuntimeError as e:
        print(f"  ❌ Erreur : {e}")
        send_discord_error(str(e))
        sys.exit(1)

    print(f"  {len(listings)} annonce(s) disponible(s) en ce moment.")

    # 2. Comparaison avec la passe précédente
    #    known_ids   = ce qui était dispo AVANT
    #    current_ids = ce qui est dispo MAINTENANT
    #    → new_ids   = apparu ou REVENU disponible depuis la dernière vérif
    current_ids = {str(l["id"]) for l in listings}
    known_ids   = load_known_ids()
    new_ids     = current_ids - known_ids

    if new_ids:
        print(f"  🆕 {len(new_ids)} nouveau(x) logement(s) détecté(s) !")
        for listing in listings:
            if str(listing["id"]) in new_ids:
                send_discord_new_listing(listing)
    else:
        print("  Aucun nouveau logement.")

    # 3. On sauvegarde UNIQUEMENT les IDs actuellement dispos
    #    (pas d'accumulation → les retours sont re-détectés)
    save_known_ids(current_ids)
    print("  État sauvegardé.")


if __name__ == "__main__":
    main()
