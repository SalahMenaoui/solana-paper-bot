"""Tests de la logique de suivi. Lancer avec : python -m pytest"""
from bot import tracker

H = 3600
T0 = 1_000_000  # heure de découverte fictive


def test_aucun_checkpoint_avant_1h():
    due, missed = tracker.due_checkpoints(T0, T0 + 30 * 60, set())
    assert due == [] and missed == []


def test_checkpoint_1h_du_avec_retard_tolere():
    due, missed = tracker.due_checkpoints(T0, T0 + int(1.7 * H), set())
    assert [label for label, _ in due] == ["1h"]
    assert missed == []


def test_checkpoint_1h_manque_si_trop_tard():
    due, missed = tracker.due_checkpoints(T0, T0 + int(2.5 * H), set())
    assert [label for label, _ in missed] == ["1h"]


def test_checkpoint_deja_fait_ignore():
    due, _ = tracker.due_checkpoints(T0, T0 + int(1.2 * H), {"1h"})
    assert due == []


def test_run_tres_en_retard_6h_et_1h():
    # À 6h30 : le 1h est manqué (fenêtre 1h-2h), le 6h est dû.
    due, missed = tracker.due_checkpoints(T0, T0 + int(6.5 * H), set())
    assert [l for l, _ in due] == ["6h"]
    assert [l for l, _ in missed] == ["1h"]


def test_mesure_dense_les_premieres_heures():
    assert tracker.wants_dense_measure(T0, T0 + 2 * H, T0 + 2 * H - 30 * 60, "active")
    assert not tracker.wants_dense_measure(T0, T0 + 2 * H, T0 + 2 * H - 10 * 60, "active")


def test_pas_de_mesure_dense_pour_un_rug():
    assert not tracker.wants_dense_measure(T0, T0 + 2 * H, T0, "rug")


def test_rug_par_chute_de_liquidite():
    assert tracker.rug_reason(900, 20_000, 0) == "liquidity_drop"
    assert tracker.rug_reason(15_000, 20_000, 0) is None


def test_rug_par_seuil_absolu():
    # 5 000 $ -> 800 $ : sous 1 000 $ alors que t0 >= 5 000 $
    assert tracker.rug_reason(800, 5_000, 0) == "liquidity_low"
    # petit token à 1 500 $ -> 900 $ : pas un rug (le seuil absolu ne s'applique pas)
    assert tracker.rug_reason(900, 1_500, 0) is None


def test_token_mort_sans_liquidite_affichee():
    # bonding curve pump.fun : pas de liquidité, prix -97 %, volume quasi nul
    assert tracker.rug_reason(None, None, 0, price=0.03, price_t0=1.0, volume_h1=20) == "dead_no_volume"
    # prix -97 % mais encore du volume : pas "mort"
    assert tracker.rug_reason(None, None, 0, price=0.03, price_t0=1.0, volume_h1=5_000) is None


def test_rug_si_paire_disparait_deux_fois():
    assert tracker.rug_reason(None, 10_000, 1) is None
    assert tracker.rug_reason(None, 10_000, 2) == "pair_missing"
