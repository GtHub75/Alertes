import requests
import json
import os
import sys

# ── Configuration ──────────────────────────────────────────────
SEARCH_URL = (
    "https://trouverunlogement.lescrous.fr/api/v1/studentsHousings/search"
    "?countries=France&campagne=42&city=Paris"
)

COOKIES = {
    "SESSION": os.environ.get("CROUS_SESSION_COOKIE", ""),
}

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
KNOWN_IDS_FILE = "known_ids.json"
LISTING_BASE_URL = "https://trouverunlogement.lescrous.fr/tools/42/search"

# ── Fetch ───────────────────────────────────────────────────────

def fetch_listings():
    """
    Récupère les annonces depuis l'API Crous.
    Lève SessionExpiredError si la session est expirée.
    Lève une exception générique en cas d'autre erreur réseau.
    """
    try:
        response = requests.get(SEARCH_URL, cookies=COOKIES, headers=HEADERS, timeout=15)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Erreur réseau : {e}") from e

    # Redirigé vers la page de login = session expirée
    if response.status_code in (401, 403):
        raise SessionExpiredError("Session expirée (401/403)")

    if "discovery/connect" in response.url or "identification" in response.url.lower():
        raise SessionExpiredError("Redirigé vers la page de connexion")

    if response.status_code != 200:
        raise RuntimeError(f"Réponse inattendue : HTTP {response.status_code}")

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise SessionExpiredError("Réponse non-JSON reçue (probablement page de login HTML)") from e

    # Vérifie que la structure attendue est bien là
    housings = data.get("data", {}).get("studentsHousings")
    if housings is None:
        raise SessionExpiredError("Structure JSON inattendue — session peut-être expirée")

    return housings


class SessionExpiredError(Exception):
    pass


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
    name    = listing.get("label") or listing.get("title") or "Logement sans nom"
    address = listing.get("address") or listing.get("residence") or "Adresse inconnue"
    lid     = listing.get("id", "")
    link    = f"{LISTING_BASE_URL}#{lid}" if lid else LISTING_BASE_URL

    embed = {
        "title": "🏠 Nouveau logement disponible !",
        "color": 0x1D6FA5,
        "fields": [
            {"name": "📋 Nom",     "value": name,                        "inline": False},
            {"name": "📍 Adresse", "value": address,                     "inline": False},
            {"name": "🔗 Lien",    "value": f"[Voir l'annonce]({link})", "inline": False},
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
            "(https://trouverunlogement.lescrous.fr), récupère le nouveau cookie "
            "et mets à jour le secret `CROUS_SESSION_COOKIE` dans GitHub."
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
    #    known_ids  = ce qui était dispo AVANT
    #    current_ids = ce qui est dispo MAINTENANT
    #    → new_ids  = apparu ou REVENU disponible depuis la dernière vérif
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
