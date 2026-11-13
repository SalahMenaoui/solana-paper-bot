"""
Analyse des wallets (niveau 1) : quels wallets apparaissent souvent parmi les gros
holders, à la découverte, de tokens qui ont ensuite bien performé ?

Limites (importantes) :
  - on voit qui DÉTIENT le token à t0, pas son prix d'achat ni s'il a vendu ;
  - un wallet "gagnant" peut être un insider, un bot ou le wallet d'un créateur ;
  - avec peu d'apparitions, un bon score peut être de la chance.
Le but est d'en faire un SIGNAL pour la phase 2, pas une liste de comptes à copier aveuglément.

Usage : python wallets.py [nb_min_apparitions]   (défaut : 3)
"""
import statistics
import sys
from collections import defaultdict

from bot.analysis import open_analysis_db, token_outcomes

GOOD_PEAK = 3.0   # un token "gagnant" = a atteint au moins x3 dans les 24 h


def main() -> None:
    min_appearances = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    conn = open_analysis_db()
    outcomes = token_outcomes(conn)

    # wallet -> liste des résultats des tokens où il était gros holder à t0
    per_wallet: dict[str, list[dict]] = defaultdict(list)
    insider_flags: dict[str, int] = defaultdict(int)
    for row in conn.execute("SELECT wallet, token_address, is_insider FROM holder_appearances WHERE is_creator = 0"):
        outcome = outcomes.get(row["token_address"])
        if outcome:
            per_wallet[row["wallet"]].append(outcome)
            insider_flags[row["wallet"]] += row["is_insider"]

    ranking = []
    for wallet, results in per_wallet.items():
        n = len(results)
        if n < min_appearances:
            continue
        winners = sum(1 for r in results if r["peak_24h"] >= GOOD_PEAK)
        rugs = sum(1 for r in results if r["rug_24h"])
        finals = [r["final_24h"] for r in results if r["final_24h"] is not None]
        ranking.append({
            "wallet": wallet,
            "n": n,
            "win_rate": winners / n,
            "rug_rate": rugs / n,
            "median_final": statistics.median(finals) if finals else None,
            "insider": insider_flags[wallet],
        })

    # Taux de base : sert de référence pour savoir si un wallet fait mieux que le hasard.
    if outcomes:
        base = sum(1 for o in outcomes.values() if o["peak_24h"] >= GOOD_PEAK) / len(outcomes)
        print(f"Référence : {100 * base:.1f} % de tous les tokens atteignent x{GOOD_PEAK:g} dans les 24 h "
              f"({len(outcomes)} tokens avec résultat)")

    ranking.sort(key=lambda r: (r["win_rate"], r["n"]), reverse=True)
    print(f"\nWallets avec au moins {min_appearances} apparitions : {len(ranking)}\n")
    print(f"{'wallet':<46} {'n':>4} {'≥x3':>6} {'rug':>6} {'méd.24h':>8} {'insider':>8}")
    for r in ranking[:25]:
        median = f"x{r['median_final']:.2f}" if r["median_final"] is not None else "-"
        print(f"{r['wallet']:<46} {r['n']:>4} {100 * r['win_rate']:>5.0f}% {100 * r['rug_rate']:>5.0f}% "
              f"{median:>8} {r['insider']:>8}")
    conn.close()


if __name__ == "__main__":
    main()
