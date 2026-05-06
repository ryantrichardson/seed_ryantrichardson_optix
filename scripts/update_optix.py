import os

API_KEY = os.environ.get("MASSIVE_API_KEY")

if not API_KEY:
    raise RuntimeError("Missing MASSIVE_API_KEY")

print("Massive API key loaded successfully.")
print("Optix updater placeholder is running.")
