import numpy as np
import pandas as pd
import pytest

from dlr.verify.forecast import apply_margins, fit_margins, margin_report


def synthetic_pairs(n_per_lead: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for lead in range(1, 49):
        truth = rng.uniform(1800, 2600, n_per_lead)
        # relative error growing with lead, unbiased
        forecast = truth * (1 + rng.normal(0, 0.01 + 0.002 * lead, n_per_lead))
        rows.append(
            pd.DataFrame(
                {
                    "lead_h": lead,
                    "line_dlr": forecast,
                    "dlr_a": truth,
                    "static_a": 2134.0,
                    "aar_a": 2600.0,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def test_margins_grow_with_lead_and_meet_the_confidence_target() -> None:
    train, test = synthetic_pairs(seed=1), synthetic_pairs(seed=2)
    margins = fit_margins(train, confidence=0.98)
    assert margins.loc[48] > margins.loc[7] > 0
    secured = apply_margins(test, margins)
    exceed = (secured["secure_a"] > secured["dlr_a"]).mean()
    assert exceed == pytest.approx(0.02, abs=0.01)


def test_margin_report_scores_only_leads_from_seven_hours() -> None:
    df = apply_margins(synthetic_pairs(n_per_lead=10), pd.Series(0.1, index=range(1, 49)))
    report = margin_report(df)
    assert report["hours_scored"] == 10 * 42
    scored = df[df["lead_h"] >= 7]
    expected = float((scored["static_a"] > scored["dlr_a"]).mean())
    assert report["static_exceeds_truth_share"] == pytest.approx(expected, abs=1e-4)
