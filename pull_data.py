"""
Récupère les données collectées par GitHub (branche "data") et prépare l'analyse.

  1. télécharge la branche data (fichiers .gz) ;
  2. décompresse dans data/ : bot.db (live) + archive/bot-AAAA-MM.db ;
  3. construit data/all.db = tout fusionné (c'est ce que lisent stats.py et wallets.py).

Usage : python pull_data.py
Les fichiers de data/ sont une COPIE : ils sont écrasés à chaque appel.
"""
import gzip
import os
import subprocess

from bot import config, storage

REF = "refs/remotes/origin/data"


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def main() -> None:
    print("Téléchargement de la branche data...")
    git("fetch", "--quiet", "origin", f"+data:{REF}")
    files = [f for f in git("ls-tree", "-r", "--name-only", REF).decode().split() if f.endswith(".gz")]

    for name in files:
        out = os.path.join(config.DATA_DIR, name[:-3])
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out + ".tmp", "wb") as f:
            f.write(gzip.decompress(git("show", f"{REF}:{name}")))
        os.replace(out + ".tmp", out)
        print(f"  {out} ({os.path.getsize(out) // 1024} Ko)")

    path = storage.merge_all()
    print(f"Base d'analyse prête : {path} ({os.path.getsize(path) // 1024} Ko)")


if __name__ == "__main__":
    main()
