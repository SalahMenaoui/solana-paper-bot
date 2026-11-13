"""
Statistiques rapides sur les données collectées.
Usage : python stats.py
"""
import statistics
import time

from bot import db
from bot.analysis import token_outcomes

# Tranches de performance (multiplicateur du prix par rapport à t0).
BUCKETS = [
    ("≤ -90 %   (x0,1)", 0.0, 0.1),
    ("-90/-50 % (x0,1-0,5)", 0.1, 0.5),
    ("-50/0 %   (x0,5-1)", 0.5, 1.0),
    ("0/+100 %  (x1-2)", 1.0, 2.0),
    ("x2 à x5", 2.0, 5.0),
    ("x5 à x10", 5.0, 10.0),
    ("≥ x10", 10.0, float("inf")),
]


def print_distribution(title: str, values: list[float]) -> None:
    print(f"\n{title} (n = {len(values)})")
    if not values:
        print("  pas encore de données")
        return
    for label, low, high in BUCKETS:
        count = sum(1 for v in values if low <= v < high)
        pct = 100 * count / len(values)
        bar = "█" * round(pct / 2)
        print(f"  {label:<22} {count:>5}  {pct:5.1f} %  {bar}")
    print(f"  médiane : x{statistics.median(values):.2f}")


def main() -> None:
    conn = db.connect()
    now = int(time.time())

    total = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
    print(f"Tokens collectés : {total}")
    for row in conn.execute("SELECT source, COUNT(*) AS n FROM tokens GROUP BY source ORDER BY n DESC"):
        print(f"  - {row['source']:<14} {row['n']}")

    latency = [
        (r["discovered_at"] - r["pair_created_at"]) / 60
        for r in conn.execute("SELECT discovered_at, pair_created_at FROM tokens WHERE pair_created_at IS NOT NULL")
    ]
    if latency:
        print(f"Âge médian de la paire à la découverte : {statistics.median(latency):.0f} min")

    runs = conn.execute(
        "SELECT COUNT(*), SUM(errors), MAX(db_size_kb) FROM runs WHERE started_at > ?", (now - 86400,)
    ).fetchone()
    print(f"Runs sur 24 h : {runs[0]} | erreurs : {runs[1] or 0} | taille base : {runs[2] or 0} Ko")

    outcomes = token_outcomes(conn, now)
    print(f"\nTokens avec résultat à 24 h connu : {len(outcomes)}")
    if outcomes:
        rugs = sum(1 for o in outcomes.values() if o["rug_24h"])
        print(f"% de rugs à 24 h : {100 * rugs / len(outcomes):.1f} %")

    finals = [o["final_24h"] for o in outcomes.values() if o["final_24h"] is not None]
    peaks = [o["peak_24h"] for o in outcomes.values()]
    print_distribution("Performance à 24 h (prix 24 h / prix t0)", finals)
    print_distribution("Meilleur prix mesuré dans les 24 h (prix max / prix t0)", peaks)
    print("\nNote : le 'meilleur prix' est échantillonné toutes les 30 min à 2 h ; le vrai pic peut être plus haut.")
    conn.close()


if __name__ == "__main__":
    main()
