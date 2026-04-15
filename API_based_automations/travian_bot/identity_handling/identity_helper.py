import json
import os

def get_identity_path():
    """Find identity.json in the most common project locations."""
    current_dir = os.path.dirname(__file__)
    module_root = os.path.abspath(os.path.join(current_dir, ".."))
    workspace_root = os.path.abspath(os.path.join(module_root, "..", ".."))

    candidates = [
        os.path.join(os.getcwd(), "database", "identity.json"),
        os.path.join(module_root, "database", "identity.json"),
        os.path.join(workspace_root, "database", "identity.json"),
    ]

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    return candidates[0]

def load_identity_data():
    """Load and return raw identity JSON data."""
    identity_path = get_identity_path()
    if not os.path.exists(identity_path):
        raise FileNotFoundError(
            "❌ identity.json not found. Run launcher option '10) Identity & Villages' "
            "and choose 'Set up new identity' first."
        )
    with open(identity_path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_villages_from_identity():
    """Load all villages from the identity card located in database/."""
    identity = load_identity_data()

    servers = identity["travian_identity"]["servers"]
    if not servers:
        raise Exception("❌ No servers found in identity!")

    villages = servers[0]["villages"]
    if not villages:
        raise Exception("❌ No villages found for the server!")

    return villages

def choose_village_to_scan(villages):
    """Prompt user to pick a village to center the scan around."""
    print("\n🏡 Available villages to scan from:")
    for idx, village in enumerate(villages):
        print(f"{idx}: {village['village_name']} ({village['x']},{village['y']})")

    while True:
        try:
            choice = int(input("\n✏️ Enter the number of the village to scan around: ").strip())
            if 0 <= choice < len(villages):
                selected = villages[choice]
                print(f"\n✅ Selected village: {selected['village_name']} at ({selected['x']},{selected['y']})")
                return selected["x"], selected["y"]
            else:
                print("❌ Invalid selection, please try again.")
        except ValueError:
            print("❌ Please enter a valid number.")
