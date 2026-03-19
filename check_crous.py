import requests
import json
import os
import sys

# ── Configuration ──────────────────────────────────────────────
SEARCH_URL = "https://trouverunlogement.lescrous.fr/api/fr/search/42"

SEARCH_BODY = {
    "idTool": 42,
    "need_aggregation": True,
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
    "HAPROXYID": os.environ.get("CROUS_HAPROXYID", ""),
}

HEADERS = {
    "Accept": "application/ld+json, application/json",
    "Content-Type": "application/json",
    "Origin": "https://trouverunlogement.lescrous.fr",
    "Referer": "https://trouverunlogement.lescrous.fr/tools/42/search?bounds=2.224122_48.902156_2.4697602_48.8155755&locationName=Paris",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.3.1 Safari/605.1.15",
}

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
KNOWN_IDS_FILE = "known_ids.json"
ERROR_STATE_FILE = "error_state.json"
LISTING_BASE_URL = "https://trouverunlogement.lescrous.fr/tools/42/search"


# ── Fetch ───────────────────────────────────────────────────────

class SessionExpiredError(Exception):
    pass


def fetch_listings():
    try:
        response = requests.post(
            SEARCH_URL,
            json=SEARCH_BODY,
            cookies=COOKIES,
            headers=HEADERS,
            timeout=15
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Erreur reseau : {e}") from e

    if response.status_code in (401, 403):
        raise SessionExpiredError("Session expiree (401/403)")

    if "discovery/connect" in response.url or "identification" in response.url.lower():
        raise SessionExpiredError("Redirige vers la page de connexion")

    if response.status_code != 200:
        raise RuntimeError(f"Reponse inattendue : HTTP {response.status_code}")

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise SessionExpiredError("Reponse non-JSON (probablement page de login HTML)") from e

    items = data.get("results", {}).get("items")
    if items is None:
        raise SessionExpiredError("Structure JSON inattendue - session peut-etre expiree")

    return items


# ── Gestion état d'erreur ───────────────────────────────────────

def load_error_state():
    try:
        with open(ERROR_STATE_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"in_error": False, "error_type": None}


def save_error_state(state):
    with open(ERROR_STATE_FILE, "w") as f:
        json.dump(state, f)


# ── Stockage logements ──────────────────────────────────────────

def load_known_ids():
    try:
        with open(KNOWN_IDS_FILE, "r") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def save_known_ids(ids):
    with open(KNOWN_IDS_FILE, "w") as f:
        json.dump(list(ids), f)


# ── Notifications Discord ───────────────────────────────────────

def send_discord_new_listing(listing):
    name      = listing.get("label") or "Logement sans nom"
    residence = listing.get("residence", {})
    address   = residence.get("address") or "Adresse inconnue"
    res_label = residence.get("label") or ""
    lid       = listing.get("id", "")
    link      = f"{LISTING_BASE_URL}#{lid}" if lid else LISTING_BASE_URL

    occupation = listing.get("occupationModes", [])
    if occupation:
        rent     = occupation[0].get("rent", {})
        rent_min = rent.get("min", 0) // 100
        rent_max = rent.get("max", 0) // 100
        prix     = f"{rent_min}e" if rent_min == rent_max else f"{rent_min}-{rent_max}e"
    else:
        prix = "Non renseigne"

    embed = {
        "title": "Nouveau logement disponible !",
        "color": 0x1D6FA5,
        "fields": [
            {"name": "Nom",     "value": f"{name} ({res_label})" if res_label else name, "inline": False},
            {"name": "Adresse", "value": address,                                         "inline": False},
            {"name": "Loyer",   "value": prix,                                            "inline": True},
            {"name": "Lien",    "value": f"[Voir l'annonce]({link})",                     "inline": False},
        ],
        "footer": {"text": "Mon Logement Crous - Surveillance automatique"},
    }
    _post_to_discord({"embeds": [embed]})
    print(f"  Notification envoyee : {name}")


def send_discord_session_expired():
    embed = {
        "title": "Session Crous expiree !",
        "description": (
            "Le cookie de session n'est plus valide.\n"
            "**Action requise :** reconnecte-toi sur [trouverunlogement.lescrous.fr]"
            "(https://trouverunlogement.lescrous.fr), recupere les nouveaux cookies "
            "`PHPSESSID` et `qpid`, et mets a jour les secrets dans GitHub."
        ),
        "color": 0xFF3B30,
        "footer": {"text": "Mon Logement Crous - Surveillance automatique"},
    }
    _post_to_discord({"embeds": [embed]})
    print("  Alerte session expiree envoyee sur Discord.")


def send_discord_error(message):
    embed = {
        "title": "Erreur du script Crous",
        "description": f"```{message}```",
        "color": 0xFF9500,
        "footer": {"text": "Mon Logement Crous - Surveillance automatique"},
    }
    _post_to_discord({"embeds": [embed]})
    print(f"  Alerte erreur envoyee sur Discord : {message}")


def send_discord_recovered(error_type):
    if error_type == "session":
        title = "Session Crous retablie !"
        description = "La connexion au site Crous fonctionne a nouveau normalement."
    else:
        title = "Site Crous de retour !"
        description = "Le site fonctionne a nouveau normalement. La surveillance reprend."

    embed = {
        "title": title,
        "description": description,
        "color": 0x2ECC71,
        "footer": {"text": "Mon Logement Crous - Surveillance automatique"},
    }
    _post_to_discord({"embeds": [embed]})
    print("  Notification de retour a la normale envoyee sur Discord.")


def _post_to_discord(payload):
    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()


# ── Programme principal ─────────────────────────────────────────

def main():
    print("Verification des annonces Crous Paris...")

    error_state = load_error_state()

    # 1. Recuperation des annonces
    try:
        listings = fetch_listings()

    except SessionExpiredError as e:
        print(f"  Session expiree : {e}")
        if not error_state["in_error"]:
            send_discord_session_expired()
            save_error_state({"in_error": True, "error_type": "session"})
        else:
            print("  Alerte deja envoyee, silence jusqu'au retour.")
        sys.exit(1)

    except RuntimeError as e:
        print(f"  Erreur : {e}")
        if not error_state["in_error"]:
            send_discord_error(str(e))
            save_error_state({"in_error": True, "error_type": "error"})
        else:
            print("  Alerte deja envoyee, silence jusqu'au retour.")
        sys.exit(1)

    # 2. Si on etait en erreur, notifier le retour a la normale
    if error_state["in_error"]:
        send_discord_recovered(error_state["error_type"])
        save_error_state({"in_error": False, "error_type": None})

    print(f"  {len(listings)} annonce(s) disponible(s) en ce moment.")

    # 3. Comparaison avec la passe precedente
    current_ids = {str(l["id"]) for l in listings}
    known_ids   = load_known_ids()
    new_ids     = current_ids - known_ids

    if new_ids:
        print(f"  {len(new_ids)} nouveau(x) logement(s) detecte(s) !")
        for listing in listings:
            if str(listing["id"]) in new_ids:
                send_discord_new_listing(listing)
    else:
        print("  Aucun nouveau logement.")

    # 4. Sauvegarde
    save_known_ids(current_ids)
    print("  Etat sauvegarde.")


if __name__ == "__main__":
    main()
