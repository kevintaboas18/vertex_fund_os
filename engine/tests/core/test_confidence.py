"""Tests for the confidence engine module.

Sources of truth:
- Cerebro/shared/CONFIDENCE_ENGINE.md: confidence formula and labels
- Task 5 brief: interfaces and test code
"""

from wbj.core.confidence import confidence, confidence_label, total_confidence


def test_weighted_formula():
    """Test the confidence formula: 0.30*c + 0.25*sq + 0.20*f + 0.15*cons + 0.10*mf."""
    assert confidence(100, 100, 100, 100, 100) == 100
    assert abs(confidence(80, 60, 100, 40, 50) -
               (0.30*80 + 0.25*60 + 0.20*100 + 0.15*40 + 0.10*50)) < 1e-9


def test_labels():
    """Test confidence_label boundary conditions: >=85 High, >=70 Medium, >=50 Low, else Insufficient."""
    assert confidence_label(85) == "High"
    assert confidence_label(70) == "Medium"
    assert confidence_label(50) == "Low"
    assert confidence_label(49.9) == "Insufficient"


def test_total_confidence_points_weighted():
    """Test total_confidence uses CATEGORY_WEIGHTS correctly.

    Formula: Σ(weight_pts * conf)/100
    Weights: business=20, financial=15, market=20, technical=20, risk=15, valuation=10
    """
    conf = dict(business=90, financial=80, market=70, technical=60, risk=50, valuation=40)
    expected = (20*90 + 15*80 + 20*70 + 20*60 + 15*50 + 10*40) / 100
    assert abs(total_confidence(conf) - expected) < 1e-9
